"""Cached query helpers for the merchant dashboard.

Every query that takes filters routes them through `_filters_key()`
(which makes filter state hashable for Streamlit) and `_filter_sql()`
helpers (which translate filter state into SQL WHERE clauses).

Filter semantics:
  - **date_start / date_end** — applied via `DATE(t.txn_ts) BETWEEN ...`
  - **stores** — list of `store_id` values; applied via `t.store_id IN (...)`
  - **categories** — list of `category` values; when non-empty, queries
    route through the `tenant_transaction_items × tenant_products` join
    and filter on `p.category IN (...)`. KPI revenue and avg-transaction
    switch from transaction-level totals to category-restricted line-
    item sums in that case.

The lake view-builders in `src.lake` handle the cross-merchant queries
for placeholder responses.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import duckdb
import pandas as pd
import streamlit as st

from src.lake.lake_sql import LAKE_K_FLOOR

ROOT = Path(__file__).resolve().parents[2]
DATA_RAW = ROOT / "data" / "raw"
DATA_LAKE_ITEMS = ROOT / "data" / "lake" / "items"

# k-anonymity floor for peer-card suppression — single source of truth
# (mirrors the agent lake query floor). datamodel-v2: 50.
_K = LAKE_K_FLOOR

PANEL_START = date(2026, 3, 1)
PANEL_END   = date(2026, 5, 29)

# datamodel-v2 6-merchant panel — grocery KRG/ACM/WDX + QSR TBL/BKG/CFA.
# (TJ Maxx + off-price dropped.) These dicts back the viewer selector +
# has_same_segment_peers(); all six now have 2 same-segment peers.
MERCHANT_NAME = {
    "KRG": "Kroger", "ACM": "Acme", "WDX": "Winn-Dixie",
    "TBL": "Taco Bell", "BKG": "Burger King", "CFA": "Chick-fil-A",
}
MERCHANT_SEGMENT = {
    "KRG": "grocery", "ACM": "grocery", "WDX": "grocery",
    "TBL": "qsr", "BKG": "qsr", "CFA": "qsr",
}
MERCHANT_COLOR = {
    "KRG": "#0F4C81", "ACM": "#3A6FA5", "WDX": "#6F8FB8",
    "TBL": "#C0563F", "BKG": "#D9822B", "CFA": "#B03A48",
}


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

# Wave 4: the dashboard reads the v4 Parquet via DuckDB (the same engine
# the agents use), not the retired v3 SQLite. Tenant tables are exposed
# as ALIASING VIEWS that bridge the v3 query vocabulary (merchant_id,
# txn_total, customer_id, txn_date) onto the v4 Parquet column names
# (banner_code, subtotal, card_id/customer_token, txn_ts). Views are
# metadata-only (read_parquet is lazy) so creating them per connection
# is cheap.
_TENANT_VIEWS: dict[str, str] = {
    "tenant_transactions": f"""
        SELECT txn_id,
               banner_code        AS merchant_id,
               store_id,
               txn_ts,
               CAST(txn_ts AS DATE) AS txn_date,
               subtotal           AS txn_total,
               discount_total,
               n_lines,
               customer_token     AS customer_id,
               tender, network, entry_mode, wallet_at_tap,
               wallet_provider, segment
        FROM read_parquet('{DATA_RAW / "transactions.parquet"}')
    """,
    "tenant_transaction_items": f"""
        SELECT txn_id, line_id, sku,
               qty, unit_price, discount, line_total, promo_id
        FROM read_parquet('{DATA_RAW / "transaction_items.parquet"}')
    """,
    "tenant_stores": f"""
        SELECT store_id, banner_code, merchant_id, neighborhood,
               metro_region, latitude, longitude, open_date, zone_id
        FROM read_parquet('{DATA_RAW / "stores.parquet"}')
    """,
    "tenant_products": f"""
        SELECT sku,
               product_name       AS name,
               banner_code, merchant_id,
               -- Own-data default: `category`/`subcategory` are the MERCHANT's own
               -- shelf labels (what this banner actually calls things). Any peer
               -- comparison must instead group on the functional_* columns below,
               -- which are what the lake publishes.
               merchant_category    AS category,
               merchant_subcategory AS subcategory,
               merchant_department, merchant_category, merchant_subcategory,
               functional_department, functional_category, functional_subcategory,
               shelf_price        AS base_price,
               private_label, segment
        FROM read_parquet('{DATA_RAW / "products.parquet"}')
    """,
    "tenant_customers": f"""
        SELECT card_id            AS customer_id,
               affluence, loyalty_type, home_zone, network,
               primary_banner, tender, wallet_enrolled, wallet_provider
        FROM read_parquet('{DATA_RAW / "customers.parquet"}')
    """,
}


def _conn() -> duckdb.DuckDBPyConnection:
    """In-memory DuckDB connection with the tenant aliasing views
    registered. Cheap to recreate per query (views are lazy over
    Parquet); query-result caching is handled by the @st.cache_data
    wrappers (viewer-keyed)."""
    con = duckdb.connect(database=":memory:")
    for name, sql in _TENANT_VIEWS.items():
        con.execute(f"CREATE VIEW {name} AS {sql}")
    return con


def _register_lake_views(con: duckdb.DuckDBPyConnection, viewer: str) -> bool:
    """Register the viewer's Wave 3.5 line-item peer lake as DuckDB views
    ``lake_transactions`` / ``lake_stores`` (own rows already excluded at
    build time; peers labeled ``peer_relationship``). Returns False when
    the viewer has no materialized lake. Called by the peer-aware cards
    just before they query peers."""
    vdir = DATA_LAKE_ITEMS / viewer
    if not (vdir / "lake_transactions.parquet").exists():
        return False
    con.execute(
        "CREATE OR REPLACE VIEW lake_transactions AS "
        f"SELECT * FROM read_parquet('{vdir / 'lake_transactions.parquet'}')"
    )
    con.execute(
        "CREATE OR REPLACE VIEW lake_stores AS "
        f"SELECT * FROM read_parquet('{vdir / 'lake_stores.parquet'}')"
    )
    return True


# ---------------------------------------------------------------------------
# Filter helpers
# ---------------------------------------------------------------------------

def _filters_key(filters: dict) -> tuple:
    """Hashable key for @st.cache_data. Order-stable."""
    return (
        filters.get("date_start").isoformat() if filters.get("date_start") else "",
        filters.get("date_end").isoformat()   if filters.get("date_end") else "",
        tuple(sorted(filters.get("stores", []))),
        tuple(sorted(filters.get("categories", []))),
    )


def _resolve_dates(filters: dict | None) -> tuple[date, date]:
    """Resolve the filter's date range to a concrete ``(start, end)``
    pair, defaulting to the full panel range. Used by helpers with
    a fixed analytical window so they can narrow the window to the
    user's selection while preserving default behavior."""
    f = filters or {}
    start = f.get("date_start") or PANEL_START
    end   = f.get("date_end")   or PANEL_END
    return start, end


def _complete_weeks(
    con: duckdb.DuckDBPyConnection,
    merchant_id: str,
    extra_where: str,
    extra_params: list,
    filters: dict | None,
) -> list[str]:
    """Monday-keyed ``'YYYY-MM-DD'`` strings for every COMPLETE Mon–Sun week
    that sits entirely inside the filter's ``[date_start, date_end]`` window
    (the Monday is on/after ``date_start`` and the Sunday (Mon+6) is on/before
    ``date_end``), for this merchant + stores filter, ascending.

    This is the single source of truth for "which weeks count" across every
    recent-vs-baseline card AND the KPI strip, so they never disagree on week
    membership (e.g. the strip counting a partial edge week the SKU/store
    cards drop). The default full-panel window yields the 12 complete weeks
    Mar 2 … May 18.
    """
    date_start, date_end = _resolve_dates(filters)
    rows = con.execute(
        f"""
        SELECT DISTINCT strftime(date_trunc('week', t.txn_ts), '%Y-%m-%d') AS week
        FROM tenant_transactions t
        WHERE t.merchant_id = ?{extra_where}
        ORDER BY week
        """,
        (merchant_id, *extra_params),
    ).fetchall()
    return [
        r[0] for r in rows
        if date.fromisoformat(r[0]) >= date_start
        and date.fromisoformat(r[0]) + timedelta(days=6) <= date_end
    ]


def _recent_baseline_weeks(
    con: duckdb.DuckDBPyConnection,
    merchant_id: str,
    extra_where: str,
    extra_params: list,
    filters: dict | None,
    n_baseline: int = 4,
) -> tuple[str | None, list[str]]:
    """Derive ``(recent_week, baseline_weeks)`` as Monday-keyed
    ``'YYYY-MM-DD'`` strings from the filter's date window, so the
    "this week vs opening baseline" delta cards move with the top
    date-range picker instead of a hard-coded anchor.

      * ``recent_week``    = the last COMPLETE Mon–Sun week whose Sunday
        falls on/before ``date_end`` (a trailing partial week is excluded).
      * ``baseline_weeks`` = the first up-to-``n_baseline`` complete weeks
        (on/after ``date_start``) before the recent week — the "opening
        baseline" convention these cards use.

    Built on ``_complete_weeks`` (the shared week-membership rule). The
    default full-panel window reproduces the historical anchors exactly
    (recent = 2026-05-18, baseline = Mar 2 / 9 / 16 / 23). Returns
    ``(None, [])`` when the window holds no complete week.
    """
    full = _complete_weeks(con, merchant_id, extra_where, extra_params, filters)
    if not full:
        return None, []
    recent = full[-1]
    baseline = [w for w in full if w < recent][:n_baseline]
    return recent, baseline


# Filter scope rule: filters["stores"] narrows OWN merchant data only.
# Peer data is segment-aggregate and not viewer-constrainable.
def _txn_where(filters: dict) -> tuple[str, list]:
    """WHERE-clause fragments that apply to `tenant_transactions t`
    (no products join). Returns `(clause_text, params)`. Always begins
    with `t.merchant_id = ?` so the caller doesn't have to repeat it.
    """
    where, params = [], []
    if filters.get("date_start"):
        where.append("DATE(t.txn_ts) >= ?")
        params.append(filters["date_start"].isoformat())
    if filters.get("date_end"):
        where.append("DATE(t.txn_ts) <= ?")
        params.append(filters["date_end"].isoformat())
    if filters.get("stores"):
        ph = ",".join("?" for _ in filters["stores"])
        where.append(f"t.store_id IN ({ph})")
        params.extend(filters["stores"])
    return (" AND ".join(where), params) if where else ("", [])


def _category_where(filters: dict) -> tuple[str, list]:
    """Category WHERE fragment for queries that JOIN tenant_products p."""
    cats = filters.get("categories") or []
    if not cats:
        return "", []
    ph = ",".join("?" for _ in cats)
    return f"p.category IN ({ph})", list(cats)


def _has_category_filter(filters: dict) -> bool:
    return bool(filters.get("categories"))


def _own_filters_sql(filters: dict | None) -> tuple[str, list]:
    """Returns ``(extra_where, extra_params)`` for direct injection
    after an existing ``WHERE t.merchant_id = ?`` clause. Applies the
    date range and (when non-empty) the stores filter against the
    OWN merchant's transactions. Returns ``("", [])`` for default
    filters. The clause is pre-prefixed with `` AND `` so callers
    can use ``f"WHERE t.merchant_id = ?{extra_where}"`` directly.

    Empty ``stores`` list = no constraint (per spec). Category filter
    requires a products join and is NOT applied here — callers that
    need category filtering use ``_category_where`` directly.
    """
    f = filters or {}
    parts: list[str] = []
    params: list = []
    if f.get("date_start"):
        parts.append("DATE(t.txn_ts) >= ?")
        params.append(f["date_start"].isoformat())
    if f.get("date_end"):
        parts.append("DATE(t.txn_ts) <= ?")
        params.append(f["date_end"].isoformat())
    if f.get("stores"):
        ph = ",".join("?" for _ in f["stores"])
        parts.append(f"t.store_id IN ({ph})")
        params.extend(f["stores"])
    if not parts:
        return "", []
    return " AND " + " AND ".join(parts), params


# ---------------------------------------------------------------------------
# Merchant metadata
# ---------------------------------------------------------------------------

@st.cache_data(ttl=3600)
def stores_for(merchant_id: str) -> pd.DataFrame:
    """All of a merchant's stores with neighborhood + lat/lng + 90-day
    txn count + 90-day revenue. Cached for the session."""
    sql = """
    SELECT s.store_id, s.neighborhood, s.metro_region,
           s.latitude, s.longitude,
           COUNT(t.txn_id) AS n_txns_90d,
           COALESCE(SUM(t.txn_total), 0) AS revenue_90d
    FROM tenant_stores s
    LEFT JOIN tenant_transactions t
      ON t.store_id = s.store_id AND t.merchant_id = s.merchant_id
    WHERE s.merchant_id = ?
    GROUP BY s.store_id, s.neighborhood, s.metro_region, s.latitude, s.longitude
    ORDER BY s.store_id
    """
    with _conn() as c:
        return c.execute(sql, [merchant_id]).df()


# Phase 4.4e removed ``kpi_block`` — replaced by ``kpi_strip`` (Phase
# 4.4a). The v2.5 ``render_kpi_row`` shim now delegates directly to
# ``render_kpi_strip``.


def _unpack_filters_key(key: tuple) -> dict:
    """Reverse of `_filters_key()`. Lets the cached function reconstruct
    a filters dict without forcing the caller to pass the dict and the
    tuple separately (Streamlit can't hash dicts)."""
    date_start_s, date_end_s, stores, categories = key
    out: dict = {}
    if date_start_s:
        out["date_start"] = date.fromisoformat(date_start_s)
    if date_end_s:
        out["date_end"] = date.fromisoformat(date_end_s)
    out["stores"] = list(stores)
    out["categories"] = list(categories)
    return out


# Phase 4.4e removed ``daily_volume`` and ``top_skus`` — the v2.5
# ``render_insights_panel`` that called them is gone (Card 2.1 +
# Card 4.2 cover the same ground).


# Phase 4.4d removed ``category_mix`` (replaced by ``category_share_own``)
# and ``store_performance`` (replaced by ``store_anomalies`` /
# ``store_anomalies_own_only``).


# Phase 4.4e removed ``customer_engagement`` — Section 5
# (``render_customers_section``) replaces it card-by-card.


# Phase 4.4d removed payment-intelligence helpers (``payment_method_mix``,
# ``card_network_mix``, ``entry_mode_trend``, ``wallet_adoption``). The
# payment-intelligence section is NOT IN V3 per docs/archive/V3_PHASE4_AUDIT.md.


# ---------------------------------------------------------------------------
# Time Patterns — hour × day-of-week heatmap
# ---------------------------------------------------------------------------

@st.cache_data(ttl=3600)
def hour_dow_heatmap(merchant_id: str, filters_key: tuple) -> pd.DataFrame:
    """24×7 grid of transaction counts. Returns a DataFrame indexed by
    day_of_week (0=Sun … 6=Sat per SQLite strftime('%w', …)) with
    columns 0..23 (hour). Missing cells filled with 0."""
    filters = _unpack_filters_key(filters_key)
    where, params = _txn_where(filters)
    has_cat = _has_category_filter(filters)
    cat_where, cat_params = _category_where(filters)
    if has_cat:
        sql = """
        SELECT CAST(strftime('%w', t.txn_ts) AS INTEGER) AS dow,
               EXTRACT(hour FROM t.txn_ts) AS hr,
               COUNT(DISTINCT t.txn_id) AS n
        FROM tenant_transaction_items i
        JOIN tenant_transactions t ON t.txn_id = i.txn_id
        JOIN tenant_products p     ON p.sku    = i.sku
        WHERE t.merchant_id = ?
        """
        q_params = [merchant_id]
    else:
        sql = """SELECT CAST(strftime('%w', t.txn_ts) AS INTEGER) AS dow,
                        EXTRACT(hour FROM t.txn_ts) AS hr,
                        COUNT(*) AS n
                 FROM tenant_transactions t WHERE t.merchant_id = ?"""
        q_params = [merchant_id]
    if where:
        sql += f" AND {where}"
        q_params.extend(params)
    if has_cat:
        sql += f" AND {cat_where}"
        q_params.extend(cat_params)
    sql += " GROUP BY dow, hr"
    with _conn() as c:
        long = c.execute(sql, q_params).df()
    # Pivot to a 7×24 grid; fill missing cells with 0.
    if long.empty:
        return pd.DataFrame(0, index=range(7), columns=range(24))
    pivot = (long.pivot_table(index="dow", columns="hr",
                               values="n", aggfunc="sum")
                 .fillna(0).astype(int))
    pivot = pivot.reindex(index=range(7), columns=range(24), fill_value=0)
    return pivot


# ---------------------------------------------------------------------------
# Recent-vs-baseline anchor (shared by the SKU + store cards)
# ---------------------------------------------------------------------------
#
# Recent-vs-baseline convention (SKU + store cards): recent = last full
# Mon-Sun week inside the filter window; baseline = mean weekly total over
# the first up-to-four full weeks of that window. Both are derived per-query
# by ``_recent_baseline_weeks`` so the cards follow the top date-range
# picker; the default full-panel window reproduces the historical anchors
# (recent = May 18-24, baseline = Mar 2 / 9 / 16 / 23). Deviation =
# (recent / baseline - 1) * 100; the SKU and store cards flag magnitudes
# >= 15 pp. Week keys use strftime(date_trunc('week', ts), '%Y-%m-%d')
# (Monday-keyed, date-only).

_A_DEVIATION_THRESHOLD = 15.0          # pp; magnitude required to flag


# ---------------------------------------------------------------------------
# Phase 4.4 — Section 4 helpers (Catalog, Card 4.2 SKU performance)
# ---------------------------------------------------------------------------

def sku_performance(merchant_id: str, filters: dict | None = None) -> dict:
    """Per-SKU recent-week line count + revenue with two deviations vs the
    baseline mean: ``deviation_pct`` (absolute recent/baseline growth) and
    ``trend_dev_pct`` (that growth relative to the merchant's HOUSE growth —
    i.e. share-of-basket gain/loss). Also returns ``house_growth_pct``.

    The promotion card splits on ``trend_dev_pct`` (share view) so it stays
    meaningful for growth-mode merchants; absolute deviation alone would
    flag nothing when the whole store is up. Returns all SKUs (no top-N cap)
    so the renderer can sort + slice per view without re-querying.
    """
    return _sku_performance_cached(merchant_id, _filters_key(filters or {}))


@st.cache_data(ttl=3600)
def _sku_performance_cached(merchant_id: str, key: tuple) -> dict:
    filters = _unpack_filters_key(key)
    extra_where, extra_params = _own_filters_sql(filters)

    with _conn() as c:
        recent_week, baseline_weeks = _recent_baseline_weeks(
            c, merchant_id, extra_where, extra_params, filters,
        )
        if recent_week is None or not baseline_weeks:
            return {"rows": []}
        bl_ph = ",".join("?" for _ in baseline_weeks)
        rows = c.execute(
            f"""
            WITH weekly AS (
                SELECT p.sku, p.name AS sku_name, p.category,
                       strftime(date_trunc('week', t.txn_ts), '%Y-%m-%d') AS week,
                       COUNT(*) AS n_lines,
                       SUM(i.line_total) AS revenue
                FROM tenant_transaction_items i
                JOIN tenant_products p     ON p.sku    = i.sku
                JOIN tenant_transactions t ON t.txn_id = i.txn_id
                WHERE t.merchant_id = ?{extra_where}
                GROUP BY p.sku, p.name, p.category, week
            )
            SELECT sku, sku_name, category,
                   SUM(CASE WHEN week = ? THEN n_lines ELSE 0 END) AS recent_lines,
                   SUM(CASE WHEN week = ? THEN revenue ELSE 0 END) AS recent_revenue,
                   SUM(CASE WHEN week IN ({bl_ph}) THEN n_lines ELSE 0 END)
                        * 1.0 / ? AS baseline_lines
            FROM weekly
            GROUP BY sku, sku_name, category
            """,
            (
                merchant_id, *extra_params,
                recent_week, recent_week,
                *baseline_weeks, len(baseline_weeks),
            ),
        ).fetchall()

    tmp: list[tuple] = []
    tot_recent = 0.0
    tot_base = 0.0
    for sku, name, cat, recent_lines, recent_rev, baseline_lines in rows:
        baseline_f = float(baseline_lines or 0)
        recent_f   = int(recent_lines or 0)
        rev_f      = float(recent_rev or 0)
        if baseline_f <= 0 and recent_f <= 0:
            continue
        if baseline_f > 0:
            tot_recent += recent_f
            tot_base   += baseline_f
        tmp.append((sku, name, cat, baseline_f, recent_f, rev_f))

    # House growth = the merchant's own recent-vs-baseline line growth,
    # over every SKU with a baseline. Each SKU's TREND-ADJUSTED deviation
    # (``trend_dev_pct``) measures whether it gained or lost share of the
    # basket relative to this house trend. That's the signal the promotion
    # card splits on: in a growth-mode merchant (where every item is up in
    # absolute terms, e.g. QSR into May), the absolute deviation flags
    # nothing, but the share view still separates the laggards (growing
    # slower than the store) from the leaders. ``deviation_pct`` (absolute)
    # is kept for context.
    house = (tot_recent / tot_base) if tot_base > 0 else 1.0

    out: list[dict] = []
    for sku, name, cat, baseline_f, recent_f, rev_f in tmp:
        if baseline_f <= 0:
            dev = None
            trend_dev = None
        else:
            item_growth = recent_f / baseline_f
            dev = round((item_growth - 1) * 100, 1)
            trend_dev = (round((item_growth / house - 1) * 100, 1)
                         if house > 0 else None)
        out.append({
            "sku":            sku,
            "sku_name":       name,
            "category":       cat,
            "baseline_lines": round(baseline_f, 1),
            "recent_lines":   recent_f,
            "recent_revenue": round(rev_f, 2),
            "deviation_pct":  dev,
            "trend_dev_pct":  trend_dev,
        })
    return {"rows": out, "house_growth_pct": round((house - 1) * 100, 1)}


# ---------------------------------------------------------------------------
# Phase 4.4 — Section 2 helpers (Performance over time, Cards 2.1-2.3)
# ---------------------------------------------------------------------------


# Friendly day-of-week labels for the hour × DOW heatmap. SQLite's
# strftime('%w', ...) returns 0=Sunday..6=Saturday; we re-order to
# Mon-Sun for display so the work-week reads left-to-right naturally.
_HOUR_DOW_DOW_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_HOUR_DOW_SQLITE_ORDER = [1, 2, 3, 4, 5, 6, 0]  # Mon..Sun


def hour_dow_heatmap_card(merchant_id: str, filters: dict | None = None) -> dict:
    """Hour × day-of-week traffic heatmap with peak/slow analysis.
    Wraps the existing ``hour_dow_heatmap`` panel-totals DataFrame in
    the shape Pattern 3 own-only-sequential expects, plus takeaway
    metadata (peak cell, slow cell, weekday-vs-weekend ratio).
    """
    pivot = hour_dow_heatmap(merchant_id, _filters_key(filters or {}))
    # Re-order rows Mon..Sun and label them; columns stay 0..23.
    # k=50 contract: cells with 0 < count < 5 are suppressed (set to
    # None); cells with count == 0 stay as 0 (no activity, not
    # privacy-sensitive). The footnote surfaces only when the
    # 1-to-4-count band actually has entries.
    cells: list[list[int | None]] = []
    suppressed = 0
    for dow_sqlite in _HOUR_DOW_SQLITE_ORDER:
        row = pivot.loc[dow_sqlite] if dow_sqlite in pivot.index else None
        if row is None:
            cells.append([0] * 24)
        else:
            row_out: list[int | None] = []
            for h in range(24):
                v = int(row[h])
                if 0 < v < _K:      # k-anon floor (LAKE_K_FLOOR)
                    row_out.append(None)
                    suppressed += 1
                else:
                    row_out.append(v)
            cells.append(row_out)

    # Peak / slow over all cells (ignore zeros and suppressed cells
    # for slow — empty cells aren't "slow", they're "closed";
    # suppressed cells aren't comparable). For grocers and QSR
    # banners alike there are typically zero-traffic overnight hours; calling
    # "Tuesday 3am" the slowest is uninformative.
    peak = (0, 0, 0)  # (value, dow_idx, hr)
    slow = (None, 0, 0)
    for i in range(7):
        for h in range(24):
            v = cells[i][h]
            if v is None:
                continue
            if v > peak[0]:
                peak = (v, i, h)
            if v > 0 and (slow[0] is None or v < slow[0]):
                slow = (v, i, h)
    peak_dow, peak_hr, peak_val = (
        _HOUR_DOW_DOW_NAMES[peak[1]], peak[2], peak[0]
    )
    if slow[0] is None:
        slow_dow, slow_hr, slow_val = ("—", 0, 0)
    else:
        slow_dow, slow_hr, slow_val = (
            _HOUR_DOW_DOW_NAMES[slow[1]], slow[2], slow[0]
        )

    # Weekday (Mon-Fri) vs weekend (Sat-Sun) total volume. Sum only
    # non-None cells (suppressed cells contribute 1-4 each — a few
    # below the 5 floor — but excluding them keeps the comparison
    # honest at the k=50 boundary).
    def _row_sum(row: list[int | None]) -> int:
        return sum(v for v in row if v is not None)
    weekday_total = sum(_row_sum(cells[i]) for i in range(0, 5))
    weekend_total = sum(_row_sum(cells[i]) for i in range(5, 7))
    if weekend_total > 0 and weekday_total > 0:
        # Per-day average, since weekday has 5 days and weekend has 2.
        wd_avg = weekday_total / 5
        we_avg = weekend_total / 2
        if wd_avg >= we_avg:
            wd_we_higher = "weekday"
            wd_we_ratio  = round((wd_avg / we_avg - 1) * 100, 0)
        else:
            wd_we_higher = "weekend"
            wd_we_ratio  = round((we_avg / wd_avg - 1) * 100, 0)
    else:
        wd_we_higher = "weekday"
        wd_we_ratio  = 0

    return {
        "rows":      _HOUR_DOW_DOW_NAMES,
        "cols":      [f"{h:02d}" for h in range(24)],
        "cells":     cells,
        "peak_dow":  peak_dow,
        "peak_hr":   peak_hr,
        "peak_val":  peak_val,
        "slow_dow":  slow_dow,
        "slow_hr":   slow_hr,
        "slow_val":  slow_val,
        "wd_we_higher": wd_we_higher,
        "wd_we_ratio":  int(wd_we_ratio),
        "suppressed_count": suppressed,
    }


# ---------------------------------------------------------------------------
# Phase 4.4 — KPI strip data (Section 1: Performance pulse)
# ---------------------------------------------------------------------------
#
# Each KPI card surfaces a value, a delta vs prior 4-week average, and
# a 12-week sparkline. Anchored to the same Mon-Sun week binning as
# A1/A2/A3 so the "this week" / "vs prior 4 weeks" semantics stay
# consistent across the dashboard.

# The KPI strip enumerates its own weeks from the filter window (default
# fast-path = the fixed 12-week list ending 2026-05-18); the SKU / store
# delta cards derive theirs via ``_recent_baseline_weeks``. Both follow the
# top date-range picker and agree on Mon-Sun week binning.


def kpi_strip(merchant_id: str, filters: dict | None = None) -> dict:
    """KPI-strip data — Sales volume / Transactions / Avg basket, each
    for the most recent COMPLETE week with a delta vs the prior-4-
    complete-week average ("vs last month avg") and a trailing weekly
    sparkline.

    Filter semantics (Phase 4.5 — Decision 2 re-anchor):
      * "Recent week" = last full week ≤ filters["date_end"] (the
        trailing partial week is excluded by the Monday-keyed
        ``date_trunc('week', …)`` enumeration).
      * "Baseline" = up to 4 prior complete weeks ending one week
        before the recent week.
      * Sparkline = weeks within the filter window.
      * If the filter window is < 14 days, deltas are suppressed
        (returned as ``None`` so the UI can render "—" instead of
        a misleading 0 %).
      * Stores filter applies to all OWN queries.
      * No filter set = the existing hardcoded 12-week behavior
        ending 2026-05-18.

    Returns::

        {
            "sales":        {"value": float,
                             "delta_pct": float | None,
                             "sparkline": list[float]},
            "transactions": {...},
            "avg_basket":   {...},
        }
    """
    return _kpi_strip_cached(merchant_id, _filters_key(filters or {}))


@st.cache_data(ttl=3600)
def _kpi_strip_cached(merchant_id: str, key: tuple) -> dict:
    filters = _unpack_filters_key(key)
    f = filters
    date_start, date_end = _resolve_dates(f)
    extra_where, extra_params = _own_filters_sql(f)

    # The "default" path (full panel, no stores filter): use the
    # canonical 12-week list ending 2026-05-18 so existing callers
    # who never set a filter see byte-identical numbers.
    is_default = (
        date_start == PANEL_START and date_end == PANEL_END
        and not f.get("stores")
    )
    if is_default:
        weeks = [
            "2026-03-02", "2026-03-09", "2026-03-16", "2026-03-23",
            "2026-03-30", "2026-04-06", "2026-04-13", "2026-04-20",
            "2026-04-27", "2026-05-04", "2026-05-11", "2026-05-18",
        ]
    else:
        # Re-anchor: enumerate the COMPLETE Mon–Sun weeks inside the filter
        # window via the shared ``_complete_weeks`` rule — the same
        # membership the SKU/store recent-vs-baseline cards use, so the strip
        # and those cards never disagree on which weeks count (no partial
        # edge week here that they would drop). The stores filter is honored
        # through ``extra_where`` so weeks with zero matching txns don't show.
        with _conn() as c:
            weeks = _complete_weeks(c, merchant_id, extra_where, extra_params, f)

    if not weeks:
        # Edge case: no data in the filter window. Return zeros so
        # the strip renders without crashing; deltas suppressed.
        return {
            "sales":            {"value": 0.0, "delta_pct": None, "sparkline": []},
            "transactions":     {"value": 0,   "delta_pct": None, "sparkline": []},
            "avg_basket":       {"value": 0.0, "delta_pct": None, "sparkline": []},
        }

    week_lo, week_hi = weeks[0], weeks[-1]

    # Suppress deltas if the filter window is < 14 days — there isn't
    # enough room for "recent vs prior 4-week" to be meaningful.
    suppress_deltas = (date_end - date_start).days < 14

    with _conn() as c:
        # Per-week aggregates for the sparkline.
        rows = c.execute(
            f"""
            SELECT strftime(date_trunc('week', t.txn_ts), '%Y-%m-%d') AS week,
                   SUM(t.txn_total)         AS revenue,
                   COUNT(DISTINCT t.txn_id) AS n_txns
            FROM tenant_transactions t
            WHERE t.merchant_id = ?{extra_where}
              AND strftime(date_trunc('week', t.txn_ts), '%Y-%m-%d')
                  BETWEEN ? AND ?
            GROUP BY week
            """,
            (merchant_id, *extra_params, week_lo, week_hi),
        ).fetchall()

    rev_by_wk: dict[str, float] = {w: float(r) for w, r, _ in rows}
    txn_by_wk: dict[str, int] = {w: int(t) for w, _, t in rows}

    def _series(d: dict) -> list[float]:
        return [float(d.get(w, 0)) for w in weeks]

    rev_series  = _series(rev_by_wk)
    txn_series  = _series(txn_by_wk)
    basket_series = [
        (rev_series[i] / txn_series[i]) if txn_series[i] > 0 else 0.0
        for i in range(len(weeks))
    ]

    def _delta(recent_val: float, prior_vals: list[float]) -> float | None:
        if suppress_deltas:
            return None
        prior_mean = sum(prior_vals) / len(prior_vals) if prior_vals else 0.0
        if prior_mean <= 0:
            return None if suppress_deltas else 0.0
        return round((recent_val / prior_mean - 1) * 100, 1)

    # Prior = up to 4 weeks ending one week before "recent" (the "last
    # month" window), so the 1-week buffer matches the original logic.
    # With fewer than 5 weeks of data available, prior_vals shrinks
    # naturally.
    prior_idx = slice(max(0, len(weeks) - 5), len(weeks) - 1)
    rev_recent  = rev_series[-1];    rev_delta = _delta(rev_recent, rev_series[prior_idx])
    txn_recent  = txn_series[-1];    txn_delta = _delta(txn_recent, txn_series[prior_idx])
    bas_recent  = basket_series[-1]; bas_delta = _delta(bas_recent, basket_series[prior_idx])

    return {
        "sales": {
            "value":     rev_recent,
            "delta_pct": rev_delta,
            "sparkline": rev_series,
        },
        "transactions": {
            "value":     int(txn_recent),
            "delta_pct": txn_delta,
            "sparkline": txn_series,
        },
        "avg_basket": {
            "value":     bas_recent,
            "delta_pct": bas_delta,
            "sparkline": basket_series,
        },
    }


# ---------------------------------------------------------------------------
# Phase 4.3 — own-only question data (fallback path)
# ---------------------------------------------------------------------------
#
# The own-only variants exist as a fallback for any viewer with no
# same-segment peers. (datamodel-v2: all six banners now have 2 peers, so
# these are rarely hit — kept for a future single-member segment.)
# Recent-vs-baseline questions share the baseline convention: recent =
# last full Mon-Sun week in the filter window, baseline = first up-to-4
# full weeks of that window (``_recent_baseline_weeks``; default panel =
# recent May 18-24, baseline Mar 2-23). The 15% deviation floor is reused
# for store / SKU / category anomaly flags.


# ---------------------------------------------------------------------------
# T-A1 / R-A1 — Per-store recent-vs-baseline (no peer column)
# ---------------------------------------------------------------------------

def store_anomalies_own_only(merchant_id: str, filters: dict | None = None) -> dict:
    """Per-store recent-week SALES ($) vs the opening-weeks baseline mean,
    with the % deviation. Recent week and baseline weeks are derived from
    the filter window (see ``_recent_baseline_weeks``), so the card follows
    the top date-range picker. Own-data only — no peer column. Backs the
    dashboard's "Store performance distribution" card.
    """
    return _store_anomalies_own_only_cached(merchant_id, _filters_key(filters or {}))


@st.cache_data(ttl=3600)
def _store_anomalies_own_only_cached(merchant_id: str, key: tuple) -> dict:
    filters = _unpack_filters_key(key)
    f = filters
    store_extra_where = ""
    store_extra_params: list = []
    txn_extra_where = ""
    txn_extra_params: list = []
    if f.get("stores"):
        ph = ",".join("?" for _ in f["stores"])
        store_extra_where = f" AND s.store_id IN ({ph})"
        store_extra_params = list(f["stores"])
        txn_extra_where = f" AND t.store_id IN ({ph})"
        txn_extra_params = list(f["stores"])

    with _conn() as c:
        recent_week, baseline_weeks = _recent_baseline_weeks(
            c, merchant_id, txn_extra_where, txn_extra_params, f,
        )
        if recent_week is None or not baseline_weeks:
            return {"rows": [], "n_flagged": 0, "n_under": 0,
                    "n_over": 0, "top": None}
        bl_ph = ",".join("?" for _ in baseline_weeks)
        own_rows = c.execute(
            f"""
            WITH weekly AS (
                SELECT t.store_id,
                       strftime(date_trunc('week', t.txn_ts), '%Y-%m-%d') AS week,
                       SUM(t.txn_total) AS sales
                FROM tenant_transactions t
                WHERE t.merchant_id = ?{txn_extra_where}
                GROUP BY t.store_id, week
            )
            SELECT s.store_id, s.neighborhood,
                   SUM(CASE WHEN w.week = ?
                            THEN w.sales ELSE 0 END) AS recent,
                   SUM(CASE WHEN w.week IN ({bl_ph})
                            THEN w.sales ELSE 0 END) * 1.0 / ?
                        AS baseline
            FROM tenant_stores s
            LEFT JOIN weekly w ON w.store_id = s.store_id
            WHERE s.merchant_id = ?{store_extra_where}
            GROUP BY s.store_id, s.neighborhood
            """,
            (
                merchant_id, *txn_extra_params,
                recent_week,
                *baseline_weeks, len(baseline_weeks),
                merchant_id, *store_extra_params,
            ),
        ).fetchall()

    rows: list[dict] = []
    for store_id, nb, recent, baseline in own_rows:
        recent_f   = float(recent or 0)
        baseline_f = float(baseline or 0)
        if baseline_f <= 0:
            continue
        dev = round((recent_f / baseline_f - 1) * 100, 1)
        rows.append({
            "store_id":      store_id,
            "neighborhood":  nb,
            "baseline":      round(baseline_f, 1),
            "recent":        int(recent_f),
            "deviation_pct": dev,
            "flag":          abs(dev) >= _A_DEVIATION_THRESHOLD,
        })

    rows.sort(key=lambda r: abs(r["deviation_pct"]), reverse=True)
    flagged = [r for r in rows if r["flag"]]
    n_under = sum(1 for r in flagged if r["deviation_pct"] < 0)
    n_over  = sum(1 for r in flagged if r["deviation_pct"] > 0)
    top = rows[0] if rows else None

    return {
        "rows":      rows,
        "n_flagged": len(flagged),
        "n_under":   n_under,
        "n_over":    n_over,
        "top":       top,
    }


# ---------------------------------------------------------------------------
# Department mix with category drill (own-data)
# ---------------------------------------------------------------------------

def department_mix_own(
    merchant_id: str, top_n: int = 12, filters: dict | None = None,
) -> dict:
    """Share of own sales by the merchant's own DEPARTMENT, with a
    per-department CATEGORY breakdown for the drill-down. Department is the
    right top-level grain for a merchant's mix: at category grain a large
    department that is split across many categories (e.g. Meat & Seafood →
    Beef / Poultry / Pork / Seafood) is understated relative to a single
    undivided category (e.g. Cleaning). Departments are few (~10), so
    ``top_n`` normally surfaces them all; any beyond ``top_n`` roll into
    "Other". Uses MERCHANT labels (``merchant_department`` at the top level,
    ``merchant_category`` in the drill) — the shelf taxonomy this banner
    actually merchandises by."""
    return _department_mix_own_cached(merchant_id, top_n, _filters_key(filters or {}))


@st.cache_data(ttl=3600)
def _department_mix_own_cached(merchant_id: str, top_n: int, key: tuple) -> dict:
    filters = _unpack_filters_key(key)
    extra_where, extra_params = _own_filters_sql(filters)

    with _conn() as c:
        dept_rows = c.execute(
            f"""
            SELECT p.merchant_department AS dept, SUM(i.line_total) AS rev
            FROM tenant_transaction_items i
            JOIN tenant_products p     ON p.sku    = i.sku
            JOIN tenant_transactions t ON t.txn_id = i.txn_id
            WHERE t.merchant_id = ?{extra_where}
            GROUP BY p.merchant_department
            ORDER BY rev DESC
            """,
            (merchant_id, *extra_params),
        ).fetchall()
        sub_rows = c.execute(
            f"""
            SELECT p.merchant_department AS dept, p.category, SUM(i.line_total) AS rev
            FROM tenant_transaction_items i
            JOIN tenant_products p     ON p.sku    = i.sku
            JOIN tenant_transactions t ON t.txn_id = i.txn_id
            WHERE t.merchant_id = ?{extra_where}
            GROUP BY p.merchant_department, p.category
            """,
            (merchant_id, *extra_params),
        ).fetchall()

    if not dept_rows:
        return {"labels": [], "values": [], "top3_names": [],
                "top3_pct": 0.0, "subcats": {}}

    total = sum(float(r[1]) for r in dept_rows) or 1.0
    top = dept_rows[:top_n]
    rest = dept_rows[top_n:]
    labels = [r[0] for r in top]
    values = [round(float(r[1]) / total * 100, 1) for r in top]
    if rest:
        other_rev = sum(float(r[1]) for r in rest)
        labels.append("Other")
        values.append(round(other_rev / total * 100, 1))

    top3 = top[:3]
    top3_names = [r[0] for r in top3]
    top3_pct = round(sum(float(r[1]) for r in top3) / total * 100, 1)

    # Category breakdown per DISPLAYED department, as a share of that
    # department's own sales (so each expander sums to ~100%).
    displayed = {r[0] for r in top}
    dept_total = {r[0]: float(r[1]) for r in dept_rows}
    sub_by_dept: dict[str, list[tuple[str, float]]] = {}
    for dept, cat, rev in sub_rows:
        if dept not in displayed:
            continue
        sub_by_dept.setdefault(dept, []).append((cat, float(rev)))

    subcats: dict[str, dict] = {}
    for dept, subs in sub_by_dept.items():
        subs.sort(key=lambda x: x[1], reverse=True)
        dtot = dept_total.get(dept) or sum(s[1] for s in subs) or 1.0
        subcats[dept] = {
            "labels": [s[0] for s in subs],
            "values": [round(s[1] / dtot * 100, 1) for s in subs],
        }

    return {
        "labels":     labels,
        "values":     values,
        "top3_names": top3_names,
        "top3_pct":   top3_pct,
        "subcats":    subcats,
    }


# ---------------------------------------------------------------------------
# Payment mix (own-data) — entry mode / card network / mobile wallet
# ---------------------------------------------------------------------------

# Raw payment codes → display labels (the Parquet stores lower-case codes).
_ENTRY_MODE_LABEL = {
    "contactless": "Contactless", "chip": "Chip",
    "swipe": "Swipe", "manual": "Manual",
}
_ENTRY_MODE_ORDER = ["contactless", "chip", "swipe", "manual"]
_NETWORK_LABEL = {
    "visa": "Visa", "mc": "Mastercard", "amex": "Amex", "discover": "Discover",
}
_NETWORK_ORDER = ["visa", "mc", "amex", "discover"]
_WALLET_LABEL = {
    "apple": "Apple Pay", "google": "Google Pay", "samsung": "Samsung Pay",
}
_WALLET_ORDER = ["apple", "google", "samsung"]


def payment_mix(merchant_id: str, filters: dict | None = None) -> dict:
    """Own-transaction payment breakdowns: entry mode, card network, and
    mobile-wallet adoption by provider. Shares are % of the merchant's
    own transactions in the window; labels are display-cased."""
    return _payment_mix_cached(merchant_id, _filters_key(filters or {}))


@st.cache_data(ttl=3600)
def _payment_mix_cached(merchant_id: str, key: tuple) -> dict:
    filters = _unpack_filters_key(key)
    extra_where, extra_params = _own_filters_sql(filters)

    with _conn() as c:
        total = c.execute(
            f"SELECT COUNT(*) FROM tenant_transactions t "
            f"WHERE t.merchant_id = ?{extra_where}",
            (merchant_id, *extra_params),
        ).fetchone()[0]
        entry = dict(c.execute(
            f"SELECT entry_mode, COUNT(*) FROM tenant_transactions t "
            f"WHERE t.merchant_id = ?{extra_where} GROUP BY entry_mode",
            (merchant_id, *extra_params),
        ).fetchall())
        net = dict(c.execute(
            f"SELECT network, COUNT(*) FROM tenant_transactions t "
            f"WHERE t.merchant_id = ?{extra_where} GROUP BY network",
            (merchant_id, *extra_params),
        ).fetchall())
        wallet = dict(c.execute(
            f"SELECT wallet_provider, COUNT(*) FROM tenant_transactions t "
            f"WHERE t.merchant_id = ? AND t.wallet_at_tap{extra_where} "
            f"GROUP BY wallet_provider",
            (merchant_id, *extra_params),
        ).fetchall())

    empty = {"labels": [], "values": []}
    if not total:
        return {
            "total_txns": 0,
            "entry_mode": dict(empty), "network": dict(empty),
            "wallet": {"labels": [], "values": [], "adoption_pct": 0.0},
            "contactless_pct": 0.0, "top_entry_label": "—", "top_entry_pct": 0.0,
        }

    def _pct(n: float) -> float:
        return round(n / total * 100, 1)

    def _build(counts: dict, order: list, label_map: dict) -> dict:
        labels, values = [], []
        for code in order:
            if counts.get(code):
                labels.append(label_map[code])
                values.append(_pct(counts[code]))
        # Surface any unexpected code rather than silently dropping it.
        for code, n in counts.items():
            if code not in order and n:
                labels.append(str(code).title())
                values.append(_pct(n))
        return {"labels": labels, "values": values}

    wallet_txns = sum(wallet.values())
    wallet_d = _build(wallet, _WALLET_ORDER, _WALLET_LABEL)
    wallet_d["adoption_pct"] = _pct(wallet_txns)

    top_code = max(entry, key=entry.get) if entry else None
    return {
        "total_txns":       total,
        "entry_mode":       _build(entry, _ENTRY_MODE_ORDER, _ENTRY_MODE_LABEL),
        "network":          _build(net, _NETWORK_ORDER, _NETWORK_LABEL),
        "wallet":           wallet_d,
        "contactless_pct":  _pct(entry.get("contactless", 0)),
        "top_entry_label":  _ENTRY_MODE_LABEL.get(top_code, str(top_code).title())
                            if top_code else "—",
        "top_entry_pct":    _pct(entry[top_code]) if top_code else 0.0,
    }


# ---------------------------------------------------------------------------
# Compatibility shims for placeholders.py and earlier views (no-op now)
# ---------------------------------------------------------------------------

def has_same_segment_peers(merchant_id: str) -> bool:
    """Retained for any caller that still imports it."""
    own_seg = MERCHANT_SEGMENT[merchant_id]
    other_segs = [s for m, s in MERCHANT_SEGMENT.items() if m != merchant_id]
    return own_seg in other_segs
