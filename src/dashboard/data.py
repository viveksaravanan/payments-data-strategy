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

from datetime import date
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
               functional_category    AS category,
               functional_subcategory AS subcategory,
               merchant_department, merchant_category, merchant_subcategory,
               functional_department,
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
# Phase 4.1 — Question-specific data queries
# ---------------------------------------------------------------------------
#
# Each suggested question that anchors on a dashboard chart pattern has a
# dedicated data helper here. The helper returns a dict shaped for the
# pattern's render function (see chart_patterns.py).
#
# A1 (University City decline) anchors on Pattern 1: weekly transaction
# trajectory for the merchant's UC stores plus same-segment peer UC stores,
# normalized to a 4-week baseline. (datamodel-v2: all six banners now have
# 2 same-segment peers — the peer overlay is driven by peer_relationship in
# the lake, not a per-segment carve-out.)

# The 90-day panel spans Sun Mar 1 2026 → Fri May 29 2026.
# `strftime(date_trunc('week', ts), '%Y-%m-%d')` bins each timestamp to the
# Monday that starts its containing Mon-Sun week, as a date-only 'YYYY-MM-DD'
# string. (NB: date_trunc on a TIMESTAMP returns a TIMESTAMP, so a bare
# CAST(... AS VARCHAR) leaks a ' 00:00:00' suffix and never matches the
# Monday-keyed string constants — strftime is the fix.) (the convention v2.5 reports
# already use). The first such week (Feb 23) and the last (May 25) are
# partial; we filter to the 12 fully-covered weeks Mar 2 → May 18 so the
# trough detector doesn't pick a partial-week artifact.
_A1_FULL_WEEK_FIRST = "2026-03-02"
_A1_FULL_WEEK_LAST  = "2026-05-18"

# Baseline window for A1 normalization: first 4 full weeks (Mar 2 → Mar
# 29). Trough search excludes baseline weeks so the merchant's "before"
# index is anchored at 100.
_A1_BASELINE_WEEKS  = 4

# Magnitude threshold (percentage drop from baseline) used to label a
# series as "declined" for market-vs-store-specific classification.
# Set at the noise floor below which week-to-week wobble can produce
# false positives; declines beyond this are visually unambiguous on
# the Pattern 1 chart and match the V3_VISION worked-example narrative.
_A1_DECLINE_PCT     = 15.0






# ---------------------------------------------------------------------------
# P2 — Staple vs non-food tier pricing comparison (Pattern 2 two-panel)
# ---------------------------------------------------------------------------
#
# Pricing tiers follow the catalog overlays from Phase 1.6:
#   tight (staples):   BAKERY, BEVERAGES, DAIRY, FROZEN, MEAT, PANTRY,
#                      PRODUCE, SNACKS
#   loose (non-food):  BABY, HOUSEHOLD, PERSONAL, PET
#
# Each panel shows the per-category percentage gap between own mean
# unit price and each peer's mean unit price. Weighted aggregates (by
# own category revenue) feed the takeaway sentence.

_P2_TIGHT_CATEGORIES = [
    "BAKERY", "BEVERAGES", "DAIRY", "FROZEN",
    "MEAT", "PANTRY", "PRODUCE", "SNACKS",
]
_P2_LOOSE_CATEGORIES = ["BABY", "HOUSEHOLD", "PERSONAL", "PET"]

# Width (in pp) below which we read staple-vs-non-food gaps as the
# same. Above this, we label the strategy "asymmetric" and report
# which tier is softer.
_P2_TIER_SYMMETRY_PP = 2.0






# ---------------------------------------------------------------------------
# D3 — Basket-mix fingerprint vs peer average (Pattern 2 diverging)
# ---------------------------------------------------------------------------





# ---------------------------------------------------------------------------
# P1 — Category × peer pricing heatmap (Pattern 3 cross-merchant diverging)
# ---------------------------------------------------------------------------

# How many categories to surface on the heatmap. The top 10 by own
# revenue captures the merchant's main basket categories without
# pushing the heatmap height beyond what the chat panel can show.
_P1_TOP_N_CATEGORIES = 10

# Minimum peer line-count per cell. Below this, the cell is suppressed
# per Phase 1.5 k=50 (the lake materialization already filters at the
# row level; we re-enforce at the aggregate cell level to match the
# documented anchor-chart contract from V3_AUDIT.md §1.2).
_P1_MIN_PEER_LINES = 5

# Magnitude threshold below which we treat a gap as parity rather
# than describing it as "above" or "below" in the takeaway.
_P1_PARITY_THRESHOLD = 0.5






# ---------------------------------------------------------------------------
# P3 — Volume × pricing-gap scatter (Pattern 4)
# ---------------------------------------------------------------------------

# Magnitude below which we treat a category's gap as parity with peers
# rather than calling it "priced above" in the takeaway.
_P3_ABOVE_PARITY_PCT = 0.5






# ---------------------------------------------------------------------------
# D4 — Own share vs peer-mean share scatter (Pattern 4)
# ---------------------------------------------------------------------------





# ---------------------------------------------------------------------------
# D7 — Per-peer revenue gap decomposition (Pattern 5 waterfall × 2)
# ---------------------------------------------------------------------------
#
# Decomposes own-vs-each-peer revenue gap into driver contributions via
# the panel-level identity ``R = S × (N/S) × B × P`` where
#
#     S   = store count               (the strategic / slow lever)
#     N/S = transactions per store    (operational / store-level)
#     B   = items per transaction     (operational / basket-size)
#     P   = $ per item                (operational / pricing)
#
# The decomposition is per-peer (peer_a and peer_b separately) rather
# than against the simple peer-mean. Peer-mean averaged out the basket-
# size calibration signal (Phase 1.6 Pass 2 set KRG=1.00, ACM=0.90,
# WDX=1.20 — peer-mean from KRG's seat = (0.90+1.20)/2 = 1.05, barely
# different from 1.00), making basket appear small even though it is
# the strongest single calibrated knob in the data. The per-peer
# slices restore the signal.
#
# Stores is a separate driver because the panel's store counts differ
# (KRG=30, ACM=25, WDX=20). Without isolating it, the structural
# store-count gap leaks into Traffic and masks the per-store operational
# story. With Stores carved out, the remaining three drivers compare
# per-store operating performance.
#
# Log decomposition is exact at the panel level: ``log(R_own/R_peer) =
# log(S_own/S_peer) + log((N/S)_own/(N/S)_peer) + log(B_own/B_peer) +
# log(P_own/P_peer)``. Each driver's pp contribution is
# ``100 × log(driver_ratio)``. Mix and Residual are 0-valued
# placeholders — sub-decomposing the Ticket factor into category-mix
# vs. within-category-price effects is deferred to Phase 4.3.
#
# Peer transaction count is recovered from the lake by filtering to
# ``line_id = 1`` rows (the generator assigns line_id starting at 1
# for the first line of every transaction; counting first-line rows
# per peer gives the exact peer txn count without needing
# transaction-level lake exposure). See ``chart_patterns.md`` for the
# general implementation note.

# Pp window inside which two per-store drivers are described as a
# joint pair in the takeaway rather than singling out the marginal
# winner. 2.0 calibrated against the KRG↔WDX pair where Traffic/store
# and Basket sit within 1.2pp of each other on a meaningful gap.
_D7_PER_STORE_TIE_PP = 2.0






# ---------------------------------------------------------------------------
# T1 / T2 / T4 — Trade-area question data (Pattern 6 maps)
# ---------------------------------------------------------------------------
#
# T1: per-neighborhood own-vs-baseline performance ratio + peer-co-decline
#     signal. Diverging color over neighborhood polygons; brand-color
#     store markers overlaid.
# T2: per-neighborhood customer-home density (from
#     ``tenant_customers.home_zip5``, restricted to customers with at
#     least one transaction at this merchant). Sequential color over
#     polygons; own store markers overlaid.
# T4: per-neighborhood expansion-opportunity score = customer-activity
#     numerator / (own_store_count + 1). Peer store markers overlaid
#     for competitive context.
#
# ZIP-to-neighborhood mapping is duplicated from ``src/generate/
# parameters.py`` rather than imported across the dashboard ↔ generate
# boundary; the mapping is static and small.

_ZIP_TO_NEIGHBORHOOD: dict[str, str] = {
    "28202": "Uptown / Center City",
    "28203": "Dilworth",
    "28205": "Plaza Midwood",
    "28206": "NoDa",
    "28210": "SouthPark",
    "28211": "SouthPark",
    "28213": "University City",
    "28223": "University City",
    "28277": "Ballantyne",
    "28104": "Matthews",
    "28105": "Matthews",
    "28078": "Huntersville",
    "28134": "Pineville",
    "28025": "Concord",
    "28027": "Concord",
    "28115": "Mooresville",
    "28117": "Mooresville",
}

# Magnitude (pp) below which a neighborhood is treated as on-baseline
# in T1's takeaway phrasing rather than called out as under- or
# over-performing. Adaptive logic in `_render_t1` uses this floor.
_T1_BASELINE_NOISE_PP = 5.0


def neighborhood_performance(merchant_id: str, filters: dict | None = None) -> dict:
    """Per-neighborhood own-vs-baseline transaction-rate ratio, plus
    a peer-co-decline signal from the same-segment peers in the lake.
    Data shape for Pattern 6 ``diverging`` mode (T1).

    Returns a dict::

        {
            "neighborhoods": list of dicts with name, own_delta_pct,
                             peer_delta_pct, peer_signal, n_stores,
                             n_txns, peer_n_stores, vmin, vmax;
            "weakest":       the most-under-performing neighborhood
                             (or None when all are on baseline);
            "strongest":     the most-over-performing neighborhood;
            "own_baseline":  own panel txns-per-store baseline.
        }
    """
    return _neighborhood_performance_cached(merchant_id, _filters_key(filters or {}))


@st.cache_data(ttl=3600)
def _neighborhood_performance_cached(merchant_id: str, key: tuple) -> dict:
    filters = _unpack_filters_key(key)
    extra_where, extra_params = _own_filters_sql(filters)

    own_stores_df = stores_for(merchant_id)

    with _conn() as c:
        # Own per-neighborhood, TEMPORAL: recent week (_A_RECENT_WEEK_START)
        # vs the 4-week March baseline (_A_BASELINE_WEEK_*), mirroring the
        # store-anomaly card so the map and the store table tell the SAME
        # story. (A full-window cross-sectional per-store average diluted the
        # A1 decline out of existence and even inverted the weakest-neighborhood
        # ranking.) own_n_txns stays the full-window volume = neighborhood size.
        own_rows = c.execute(
            f"""
            SELECT s.neighborhood,
                   COUNT(DISTINCT s.store_id) AS n_stores,
                   COUNT(DISTINCT t.txn_id)   AS total_txns,
                   COUNT(DISTINCT CASE
                       WHEN strftime(date_trunc('week', t.txn_ts), '%Y-%m-%d') = ?
                       THEN t.txn_id END)      AS recent_txns,
                   COUNT(DISTINCT CASE
                       WHEN strftime(date_trunc('week', t.txn_ts), '%Y-%m-%d')
                            BETWEEN ? AND ?
                       THEN t.txn_id END)      AS baseline_txns
            FROM tenant_stores s
            LEFT JOIN tenant_transactions t ON t.store_id = s.store_id{extra_where}
            WHERE s.merchant_id = ?
            GROUP BY s.neighborhood
            """,
            (
                _A_RECENT_WEEK_START,
                _A_BASELINE_WEEK_START, _A_BASELINE_WEEK_END,
                *extra_params, merchant_id,
            ),
        ).fetchall()

        # Peer per-neighborhood, TEMPORAL: same recent-vs-baseline window via
        # the shared helper the store table uses (per-store, k=50 floor). Peer
        # store counts (non-temporal) are kept for the peer_n_stores field.
        peer_recent_base = _peer_neighborhood_recent_vs_baseline(c, merchant_id)
        peer_store_rows = []
        if _register_lake_views(c, merchant_id):
            peer_store_rows = c.execute(
                """
                SELECT neighborhood, COUNT(*) AS n_stores
                FROM lake_stores
                WHERE peer_relationship = 'peer'
                GROUP BY neighborhood
                """,
            ).fetchall()

    peer_store_by_nb = {n: int(s) for n, s in peer_store_rows}

    # own_baseline = merchant-wide mean weekly txns-per-store over the March
    # baseline window (kept for the return contract / reference).
    _tot_stores = sum(int(r[1]) for r in own_rows)
    _tot_baseline_wkly = sum(int(r[4] or 0) for r in own_rows) / 4.0
    own_baseline = (_tot_baseline_wkly / _tot_stores) if _tot_stores else 0.0

    neighborhoods = []
    for nb, n_stores, total_txns, recent_txns, baseline_txns in own_rows:
        n_stores = int(n_stores)
        baseline_wkly = int(baseline_txns or 0) / 4.0
        if n_stores == 0 or baseline_wkly <= 0:
            own_delta = None
        else:
            own_delta = round((int(recent_txns or 0) / baseline_wkly - 1) * 100, 1)

        peer = peer_recent_base.get(nb)
        peer_s = peer_store_by_nb.get(nb, 0)
        if not peer or peer[1] <= 0:
            peer_delta = None
        else:
            peer_recent_ps, peer_baseline_ps = peer
            peer_delta = round((peer_recent_ps / peer_baseline_ps - 1) * 100, 1)

        if own_delta is None:
            peer_signal = "limited own footprint"
        elif peer_delta is None:
            peer_signal = "limited peer footprint"
        elif own_delta < -_T1_BASELINE_NOISE_PP and peer_delta < -_T1_BASELINE_NOISE_PP:
            peer_signal = "market-wide"
        elif own_delta < -_T1_BASELINE_NOISE_PP:
            peer_signal = "operational"
        elif own_delta > _T1_BASELINE_NOISE_PP and peer_delta > _T1_BASELINE_NOISE_PP:
            peer_signal = "market-wide (positive)"
        elif own_delta > _T1_BASELINE_NOISE_PP:
            peer_signal = "operational (positive)"
        else:
            peer_signal = "on baseline"

        neighborhoods.append({
            "name":           nb,
            "own_delta_pct":  own_delta,
            "peer_delta_pct": peer_delta,
            "own_n_stores":   n_stores,
            "own_n_txns":     int(total_txns or 0),
            "peer_n_stores":  peer_s,
            "peer_signal":    peer_signal,
        })

    ranked = [n for n in neighborhoods if n["own_delta_pct"] is not None]
    ranked.sort(key=lambda n: n["own_delta_pct"])
    weakest   = ranked[0]  if ranked else None
    strongest = ranked[-1] if ranked else None

    # Build own store markers for overlay (one per row of stores_for).
    own_markers = [
        {
            "lat": float(r.latitude),
            "lon": float(r.longitude),
            "tooltip": (
                f"<b>{r.store_id}</b><br>{r.neighborhood}<br>"
                f"{int(r.n_txns_90d):,} txns"
            ),
        }
        for r in own_stores_df.itertuples()
    ]

    return {
        "neighborhoods": neighborhoods,
        "weakest":       weakest,
        "strongest":     strongest,
        "own_baseline":  round(own_baseline, 1),
        "own_markers":   own_markers,
    }


def customer_home_density(merchant_id: str, filters: dict | None = None) -> dict:
    """Per-neighborhood customer-home counts restricted to this
    merchant's actual customer base, plus an under-served flag for
    neighborhoods with customers but no own store. Data for T2.

    Returns::

        {
            "neighborhoods": list of dicts with
                name, n_customers, own_n_stores, is_underserved;
            "underserved":   list of under-served names (no own store);
            "pct_underserved": share of merchant's customers living in
                               neighborhoods without a same-merchant
                               store;
            "densest_underserved": name + count of the highest-customer
                                   under-served neighborhood (or None);
            "own_markers": list[{lat, lon, tooltip}] for the overlay.
        }
    """
    return _customer_home_density_cached(merchant_id, _filters_key(filters or {}))


@st.cache_data(ttl=3600)
def _customer_home_density_cached(merchant_id: str, key: tuple) -> dict:
    filters = _unpack_filters_key(key)
    # Stores filter narrows the customer set to "customers who shopped
    # at the selected stores" — date filter narrows to that window.
    # Wave 4 redesign — "where customers shop". The v4 data has no home
    # address (no ZIP; the old home_zip5→neighborhood rollup is gone), so
    # we count distinct customers by the neighborhood of the stores they
    # transact at. The v3 "under-served neighborhood" sub-insight needed a
    # home location and is intentionally dropped (a customer counts in the
    # neighborhood they shop in, which already has an own store).
    f = filters
    extra_where = ""
    extra_params: list = []
    if f.get("date_start"):
        extra_where += " AND DATE(t.txn_ts) >= ?"
        extra_params.append(f["date_start"].isoformat())
    if f.get("date_end"):
        extra_where += " AND DATE(t.txn_ts) <= ?"
        extra_params.append(f["date_end"].isoformat())
    if f.get("stores"):
        ph = ",".join("?" for _ in f["stores"])
        extra_where += f" AND t.store_id IN ({ph})"
        extra_params.extend(f["stores"])

    with _conn() as c:
        rows = c.execute(
            f"""
            SELECT s.neighborhood, COUNT(DISTINCT t.customer_id) AS n
            FROM tenant_transactions t
            JOIN tenant_stores s ON s.store_id = t.store_id
            WHERE t.merchant_id = ?{extra_where}
            GROUP BY s.neighborhood
            """,
            (merchant_id, *extra_params),
        ).fetchall()
        own_store_rows = c.execute(
            """
            SELECT neighborhood, COUNT(*) AS n
            FROM tenant_stores
            WHERE merchant_id = ?
            GROUP BY neighborhood
            """,
            (merchant_id,),
        ).fetchall()

    counts: dict[str, int] = {nb: int(n) for nb, n in rows if nb}
    own_store_by_nb = {n: int(s) for n, s in own_store_rows}

    total = sum(counts.values()) or 1
    out: list[dict] = []
    for nb, n in counts.items():
        out.append({
            "name":         nb,
            "n_customers":  n,
            "own_n_stores": own_store_by_nb.get(nb, 0),
            "is_underserved": False,
        })
    out.sort(key=lambda r: r["n_customers"], reverse=True)

    # Under-served insight dropped (no home geography in v4).
    pct_underserved = 0.0
    underserved: list[dict] = []
    densest = None

    own_stores_df = stores_for(merchant_id)
    own_markers = [
        {
            "lat": float(r.latitude),
            "lon": float(r.longitude),
            "tooltip": f"<b>{r.store_id}</b><br>{r.neighborhood}",
        }
        for r in own_stores_df.itertuples()
    ]

    from . import chart_patterns as CP
    return {
        "neighborhoods":        out,
        "underserved":          [r["name"] for r in underserved],
        "pct_underserved":      pct_underserved,
        "densest_underserved":  densest,
        "own_markers":          own_markers,
        "footnote":             CP.CUSTOMER_COVERAGE_FOOTNOTE,
    }






# ---------------------------------------------------------------------------
# A2 / A3 — Recent-vs-baseline anomaly tables (Pattern 9)
# ---------------------------------------------------------------------------
#
# Recent = last full Mon-Sun week in the panel (Mon May 18 → Sun May 24,
# 2026). Baseline = mean weekly count over the FIRST four full weeks
# (Mar 2, 9, 16, 23) — same anchor A1's trajectory chart uses, so the
# UC decline calibrated in Phase 1.6 reads consistently across A1, A2,
# A3, and T1. A purely-trailing baseline would put the UC trough
# *inside* the baseline window and make recovering stores read positive
# — the cross-question story would lose its through-line. Deviation =
# (recent / baseline - 1) × 100; flagged when
# ``abs(deviation) >= 15``. A2 operates per own store with a peer-
# neighborhood corroboration column; A3 operates per category with an
# aggregate peer-category column.
#
# Recent-week and baseline-window bounds match the A1 trajectory chart's
# week binning (``strftime(date_trunc('week', t.txn_ts), '%Y-%m-%d')`` rounds each
# timestamp to its containing Mon-Sun week's Monday).

_A_RECENT_WEEK_START   = "2026-05-18"
_A_BASELINE_WEEK_START = "2026-03-02"  # first of the four baseline weeks
_A_BASELINE_WEEK_END   = "2026-03-23"  # last  of the four baseline weeks (inclusive)
_A_DEVIATION_THRESHOLD = 15.0          # pp; magnitude required to flag


def _peer_neighborhood_recent_vs_baseline(
    conn,
    merchant_id: str,
) -> dict[str, tuple[float, float]]:
    """Return ``{neighborhood: (peer_recent_per_store, peer_baseline_per_store_per_week)}``
    for the same-segment peers visible from this viewer's lake.
    Used to compute A2's peer-neighborhood ratio column.

    Uses ``WHERE line_id = 1`` so peer counts are true transactions
    rather than line items (see chart_patterns.md "Implementation
    gotchas for lake queries").
    """
    if not _register_lake_views(conn, merchant_id):
        return {}

    # Wave 4: aggregate same-segment peers (peer_relationship='peer';
    # no per-competitor identity). Count transactions via
    # COUNT(DISTINCT lake_txn_id); k=50 floor on each published week×nbhd cell.
    rows = conn.execute(
        """
        WITH peer_weekly AS (
            SELECT ls.neighborhood,
                   strftime(date_trunc('week', lt.txn_date), '%Y-%m-%d') AS week,
                   COUNT(DISTINCT lt.lake_txn_id) AS n_txns
            FROM lake_transactions lt
            JOIN lake_stores ls ON ls.lake_store_id = lt.lake_store_id
            WHERE lt.peer_relationship = 'peer'
              AND strftime(date_trunc('week', lt.txn_date), '%Y-%m-%d')
                  BETWEEN ? AND ?
            GROUP BY ls.neighborhood, week
            HAVING COUNT(DISTINCT lt.lake_txn_id) >= 50  -- k-anon floor (LAKE_K_FLOOR)
        ),
        peer_store_counts AS (
            SELECT neighborhood, COUNT(*) AS n_stores
            FROM lake_stores
            WHERE peer_relationship = 'peer'
            GROUP BY neighborhood
        )
        SELECT pw.neighborhood,
               SUM(CASE WHEN pw.week = ? THEN pw.n_txns ELSE 0 END) AS recent,
               SUM(CASE WHEN pw.week BETWEEN ? AND ? THEN pw.n_txns ELSE 0 END)
                    * 1.0 / 4                                       AS baseline,
               psc.n_stores
        FROM peer_weekly pw
        JOIN peer_store_counts psc ON psc.neighborhood = pw.neighborhood
        GROUP BY pw.neighborhood, psc.n_stores
        """,
        (
            _A_BASELINE_WEEK_START, _A_RECENT_WEEK_START,
            _A_RECENT_WEEK_START,
            _A_BASELINE_WEEK_START, _A_BASELINE_WEEK_END,
        ),
    ).fetchall()

    out: dict[str, tuple[float, float]] = {}
    for nb, recent, baseline, n_stores in rows:
        n = int(n_stores) if n_stores else 0
        if n <= 0:
            continue
        out[nb] = (float(recent) / n, float(baseline) / n)
    return out


def store_anomalies(merchant_id: str, filters: dict | None = None) -> dict:
    """Per-store recent-vs-baseline traffic deviation, with a peer-
    neighborhood corroboration column. Data shape for Pattern 9 (A2).

    Returns::

        {
            "rows": list of dicts with
                store_id, neighborhood, baseline, recent, deviation_pct,
                peer_deviation_pct (or None), flag (bool);
            "n_flagged":              total stores ≥15% off baseline;
            "n_under":                of those, stores below baseline;
            "n_over":                 of those, stores above baseline;
            "n_co_flagged":           flagged stores whose peer-
                                      neighborhood is also ≥15% off
                                      baseline same direction;
            "top":                    row with largest |deviation|;
            "peer_signal_for_top":    short text "peers also down X%" /
                                      "peers flat" / "limited peer footprint".
        }
    """
    return _store_anomalies_cached(merchant_id, _filters_key(filters or {}))


@st.cache_data(ttl=3600)
def _store_anomalies_cached(merchant_id: str, key: tuple) -> dict:
    filters = _unpack_filters_key(key)
    # Stores filter applies to OWN-side: the user's selection restricts
    # which stores appear in the recent/baseline rollup. Date filter is
    # NOT additionally applied here because recent/baseline use fixed
    # week anchors (_A_RECENT_WEEK_START / _A_BASELINE_WEEK_*); the
    # caller's date_range narrows context for other helpers but the
    # anomaly comparison stays week-anchored. Peer overlay unchanged.
    f = filters
    store_extra_where = ""
    store_extra_params: list = []
    if f.get("stores"):
        ph = ",".join("?" for _ in f["stores"])
        store_extra_where = f" AND s.store_id IN ({ph})"
        store_extra_params = list(f["stores"])
    txn_extra_where = ""
    txn_extra_params: list = []
    if f.get("stores"):
        ph = ",".join("?" for _ in f["stores"])
        txn_extra_where = f" AND t.store_id IN ({ph})"
        txn_extra_params = list(f["stores"])

    with _conn() as c:
        own_rows = c.execute(
            f"""
            WITH weekly AS (
                SELECT t.store_id,
                       strftime(date_trunc('week', t.txn_ts), '%Y-%m-%d') AS week,
                       COUNT(DISTINCT t.txn_id) AS n_txns
                FROM tenant_transactions t
                WHERE t.merchant_id = ?{txn_extra_where}
                  AND strftime(date_trunc('week', t.txn_ts), '%Y-%m-%d')
                      BETWEEN ? AND ?
                GROUP BY t.store_id, week
            )
            SELECT s.store_id, s.neighborhood,
                   SUM(CASE WHEN w.week = ?
                            THEN w.n_txns ELSE 0 END) AS recent,
                   SUM(CASE WHEN w.week BETWEEN ? AND ?
                            THEN w.n_txns ELSE 0 END) * 1.0 / 4
                        AS baseline
            FROM tenant_stores s
            LEFT JOIN weekly w ON w.store_id = s.store_id
            WHERE s.merchant_id = ?{store_extra_where}
            GROUP BY s.store_id, s.neighborhood
            """,
            (
                merchant_id, *txn_extra_params,
                _A_BASELINE_WEEK_START, _A_RECENT_WEEK_START,
                _A_RECENT_WEEK_START,
                _A_BASELINE_WEEK_START, _A_BASELINE_WEEK_END,
                merchant_id, *store_extra_params,
            ),
        ).fetchall()
        peer_by_nb = _peer_neighborhood_recent_vs_baseline(c, merchant_id)

    rows: list[dict] = []
    for store_id, nb, recent, baseline in own_rows:
        recent_f   = float(recent or 0)
        baseline_f = float(baseline or 0)
        if baseline_f <= 0:
            continue  # need a baseline to compute deviation
        dev = round((recent_f / baseline_f - 1) * 100, 1)

        peer = peer_by_nb.get(nb)
        if peer is not None and peer[1] > 0:
            peer_dev = round((peer[0] / peer[1] - 1) * 100, 1)
        else:
            peer_dev = None

        rows.append({
            "store_id":           store_id,
            "neighborhood":       nb,
            "baseline":           round(baseline_f, 1),
            "recent":             int(recent_f),
            "deviation_pct":      dev,
            "peer_deviation_pct": peer_dev,
            "flag":               abs(dev) >= _A_DEVIATION_THRESHOLD,
        })

    # Sort by absolute deviation descending so the most-flagged rows
    # land at the top by default.
    rows.sort(key=lambda r: abs(r["deviation_pct"]), reverse=True)

    flagged = [r for r in rows if r["flag"]]
    n_under = sum(1 for r in flagged if r["deviation_pct"] < 0)
    n_over  = sum(1 for r in flagged if r["deviation_pct"] > 0)

    # Co-flagged = own and peer-neighborhood both ≥ threshold same direction.
    def _co(r: dict) -> bool:
        if r["peer_deviation_pct"] is None:
            return False
        if abs(r["peer_deviation_pct"]) < _A_DEVIATION_THRESHOLD:
            return False
        return (r["deviation_pct"] < 0) == (r["peer_deviation_pct"] < 0)
    n_co_flagged = sum(1 for r in flagged if _co(r))

    top = rows[0] if rows else None
    if top is None:
        peer_signal_for_top = "—"
    elif top["peer_deviation_pct"] is None:
        peer_signal_for_top = "limited peer footprint"
    elif _co(top):
        peer_signal_for_top = (
            f"peer-neighborhood stores also off by "
            f"{top['peer_deviation_pct']:+.1f}%"
        )
    else:
        peer_signal_for_top = (
            f"peer-neighborhood stores roughly flat "
            f"({top['peer_deviation_pct']:+.1f}%)"
        )

    return {
        "rows":                rows,
        "n_flagged":           len(flagged),
        "n_under":             n_under,
        "n_over":              n_over,
        "n_co_flagged":        n_co_flagged,
        "top":                 top,
        "peer_signal_for_top": peer_signal_for_top,
    }


def category_anomalies(merchant_id: str, filters: dict | None = None) -> dict:
    """Per-category recent-vs-baseline line-volume deviation with an
    optional peer-category corroboration column. Pattern 9 (A3).

    "Volume" = count of line items in the recent week vs the 4-week
    baseline mean — directly comparable across own and peer because
    both sides are line-level in the materialized tables.

    Returns::

        {
            "rows":           list per category with own/peer
                              deviation_pct and flag bool;
            "n_flagged":      categories at or beyond ±15%;
            "top":            row with largest |own_deviation_pct|;
            "top_direction":  "spike" / "drop" / "neutral";
            "peer_signal_for_top": short text.
        }
    """
    return _category_anomalies_cached(merchant_id, _filters_key(filters or {}))


@st.cache_data(ttl=3600)
def _category_anomalies_cached(merchant_id: str, key: tuple) -> dict:
    filters = _unpack_filters_key(key)
    extra_where, extra_params = _own_filters_sql(filters)

    with _conn() as c:
        own_rows = c.execute(
            f"""
            WITH weekly AS (
                SELECT p.category,
                       strftime(date_trunc('week', t.txn_ts), '%Y-%m-%d') AS week,
                       COUNT(*) AS n_lines
                FROM tenant_transaction_items i
                JOIN tenant_products p     ON p.sku    = i.sku
                JOIN tenant_transactions t ON t.txn_id = i.txn_id
                WHERE t.merchant_id = ?{extra_where}
                  AND strftime(date_trunc('week', t.txn_ts), '%Y-%m-%d')
                      BETWEEN ? AND ?
                GROUP BY p.category, week
            )
            SELECT category,
                   SUM(CASE WHEN week = ? THEN n_lines ELSE 0 END) AS recent,
                   SUM(CASE WHEN week BETWEEN ? AND ? THEN n_lines ELSE 0 END)
                        * 1.0 / 4 AS baseline
            FROM weekly
            GROUP BY category
            """,
            (
                merchant_id, *extra_params,
                _A_BASELINE_WEEK_START, _A_RECENT_WEEK_START,
                _A_RECENT_WEEK_START,
                _A_BASELINE_WEEK_START, _A_BASELINE_WEEK_END,
            ),
        ).fetchall()

        peer_rows = []
        if _register_lake_views(c, merchant_id):
            peer_rows = c.execute(
                """
                WITH weekly AS (
                    SELECT category,
                           strftime(date_trunc('week', txn_date), '%Y-%m-%d') AS week,
                           COUNT(*) AS n_lines
                    FROM lake_transactions
                    WHERE peer_relationship = 'peer'
                      AND strftime(date_trunc('week', txn_date), '%Y-%m-%d')
                          BETWEEN ? AND ?
                    GROUP BY category, week
                    HAVING COUNT(*) >= 50  -- k-anon floor (LAKE_K_FLOOR)
                )
                SELECT category,
                       SUM(CASE WHEN week = ? THEN n_lines ELSE 0 END) AS recent,
                       SUM(CASE WHEN week BETWEEN ? AND ? THEN n_lines ELSE 0 END)
                            * 1.0 / 4 AS baseline
                FROM weekly
                GROUP BY category
                """,
                (
                    _A_BASELINE_WEEK_START, _A_RECENT_WEEK_START,
                    _A_RECENT_WEEK_START,
                    _A_BASELINE_WEEK_START, _A_BASELINE_WEEK_END,
                ),
            ).fetchall()

    peer_by_cat = {
        cat: (float(rec), float(base))
        for cat, rec, base in peer_rows if base and float(base) > 0
    }

    rows: list[dict] = []
    for cat, recent, baseline in own_rows:
        recent_f   = float(recent or 0)
        baseline_f = float(baseline or 0)
        if baseline_f <= 0:
            continue
        dev = round((recent_f / baseline_f - 1) * 100, 1)
        pr  = peer_by_cat.get(cat)
        peer_dev = round((pr[0] / pr[1] - 1) * 100, 1) if pr else None
        rows.append({
            "category":           cat,
            "baseline":           round(baseline_f, 1),
            "recent":             int(recent_f),
            "deviation_pct":      dev,
            "peer_deviation_pct": peer_dev,
            "flag":               abs(dev) >= _A_DEVIATION_THRESHOLD,
        })

    rows.sort(key=lambda r: abs(r["deviation_pct"]), reverse=True)
    flagged = [r for r in rows if r["flag"]]
    top = rows[0] if rows else None

    if top is None:
        direction = "neutral"
        peer_signal_for_top = "—"
    else:
        if top["deviation_pct"] > _A_DEVIATION_THRESHOLD:
            direction = "spike"
        elif top["deviation_pct"] < -_A_DEVIATION_THRESHOLD:
            direction = "drop"
        else:
            direction = "neutral"
        if top["peer_deviation_pct"] is None:
            peer_signal_for_top = "no peer category data"
        elif abs(top["peer_deviation_pct"]) >= _A_DEVIATION_THRESHOLD and \
             (top["deviation_pct"] < 0) == (top["peer_deviation_pct"] < 0):
            peer_signal_for_top = (
                f"peers see the same direction "
                f"({top['peer_deviation_pct']:+.1f}%) — market-wide"
            )
        else:
            peer_signal_for_top = (
                f"peers roughly flat ({top['peer_deviation_pct']:+.1f}%) "
                "— store-specific"
            )

    return {
        "rows":                rows,
        "n_flagged":           len(flagged),
        "top":                 top,
        "top_direction":       direction,
        "peer_signal_for_top": peer_signal_for_top,
    }


# ---------------------------------------------------------------------------
# Phase 4.4 — Section 5 helpers (Customers, Cards 5.1 + 5.2)
# ---------------------------------------------------------------------------
#
# Card 5.3 (Customer home geography) reuses ``customer_home_density``
# from 4.2e — the helper already returns the right shape plus the
# customer-coverage footnote constant. No new helper needed there.

_RECENT_WEEK = "2026-05-18"
_PRIOR_4W_STARTS = ["2026-04-20", "2026-04-27", "2026-05-04", "2026-05-11"]


def _new_pct_for_week(
    c, merchant_id: str, week_start: str, filters: dict | None = None,
) -> float:
    """Helper: % of that week's distinct customers whose first txn
    with this merchant was that same week. Called by
    ``new_vs_returning`` for the recent week and each of the four
    baseline weeks to compute the new-share trend."""
    f = filters or {}
    extra_where = ""
    extra_params: list = []
    if f.get("date_start"):
        extra_where += " AND DATE(txn_ts) >= ?"
        extra_params.append(f["date_start"].isoformat())
    if f.get("date_end"):
        extra_where += " AND DATE(txn_ts) <= ?"
        extra_params.append(f["date_end"].isoformat())
    if f.get("stores"):
        ph = ",".join("?" for _ in f["stores"])
        extra_where += f" AND store_id IN ({ph})"
        extra_params.extend(f["stores"])

    row = c.execute(
        f"""
        WITH this_week AS (
            SELECT DISTINCT customer_id
            FROM tenant_transactions
            WHERE merchant_id = ?{extra_where}
              AND strftime(date_trunc('week', txn_ts), '%Y-%m-%d') = ?
        ),
        first_week AS (
            SELECT customer_id,
                   MIN(strftime(date_trunc('week', txn_ts), '%Y-%m-%d')) AS first_wk
            FROM tenant_transactions
            WHERE merchant_id = ?{extra_where}
            GROUP BY customer_id
        )
        SELECT
            SUM(CASE WHEN fw.first_wk =  ? THEN 1 ELSE 0 END) AS new_count,
            SUM(CASE WHEN fw.first_wk <  ? THEN 1 ELSE 0 END) AS returning_count
        FROM this_week tw
        JOIN first_week fw ON fw.customer_id = tw.customer_id
        """,
        (merchant_id, *extra_params, week_start,
         merchant_id, *extra_params, week_start, week_start),
    ).fetchone()
    new_count = int(row[0] or 0)
    returning_count = int(row[1] or 0)
    total = new_count + returning_count
    return (new_count / total * 100) if total else 0.0


def new_vs_returning(
    merchant_id: str,
    *,
    week_start: str = _RECENT_WEEK,
    filters: dict | None = None,
) -> dict:
    """Card 5.1 data — classify this-week's distinct customers as new
    (first txn was this week) or returning (prior txn exists earlier
    in the panel).

    Returns counts, percentages, and a ``new_pct_delta_pp`` showing
    how the new-customer share moved vs the prior 4-week mean
    new-share.
    """
    return _new_vs_returning_cached(merchant_id, week_start, _filters_key(filters or {}))


@st.cache_data(ttl=3600)
def _new_vs_returning_cached(merchant_id: str, week_start: str, key: tuple) -> dict:
    filters = _unpack_filters_key(key)
    # ``tenant_transactions`` queries here use unaliased ``txn_ts`` /
    # ``store_id``; the standard ``_own_filters_sql`` returns
    # ``t.``-prefixed fragments, so build the WHERE manually.
    f = filters
    extra_where = ""
    extra_params: list = []
    if f.get("date_start"):
        extra_where += " AND DATE(txn_ts) >= ?"
        extra_params.append(f["date_start"].isoformat())
    if f.get("date_end"):
        extra_where += " AND DATE(txn_ts) <= ?"
        extra_params.append(f["date_end"].isoformat())
    if f.get("stores"):
        ph = ",".join("?" for _ in f["stores"])
        extra_where += f" AND store_id IN ({ph})"
        extra_params.extend(f["stores"])

    with _conn() as c:
        row = c.execute(
            f"""
            WITH this_week AS (
                SELECT DISTINCT customer_id
                FROM tenant_transactions
                WHERE merchant_id = ?{extra_where}
                  AND strftime(date_trunc('week', txn_ts), '%Y-%m-%d') = ?
            ),
            first_week AS (
                SELECT customer_id,
                       MIN(strftime(date_trunc('week', txn_ts), '%Y-%m-%d')) AS first_wk
                FROM tenant_transactions
                WHERE merchant_id = ?{extra_where}
                GROUP BY customer_id
            )
            SELECT
                SUM(CASE WHEN fw.first_wk =  ? THEN 1 ELSE 0 END) AS new_count,
                SUM(CASE WHEN fw.first_wk <  ? THEN 1 ELSE 0 END) AS returning_count
            FROM this_week tw
            JOIN first_week fw ON fw.customer_id = tw.customer_id
            """,
            (merchant_id, *extra_params, week_start,
             merchant_id, *extra_params, week_start, week_start),
        ).fetchone()
        new_count       = int(row[0] or 0)
        returning_count = int(row[1] or 0)
        total           = new_count + returning_count

        # Prior 4-week mean new-share for the trend delta.
        prior_new_pcts = [
            _new_pct_for_week(c, merchant_id, wk, filters=filters)
            for wk in _PRIOR_4W_STARTS
        ]

    new_pct       = (new_count       / total * 100) if total else 0.0
    returning_pct = (returning_count / total * 100) if total else 0.0
    prior_new_mean = (
        sum(prior_new_pcts) / len(prior_new_pcts) if prior_new_pcts else 0.0
    )
    new_pct_delta_pp = round(new_pct - prior_new_mean, 1)

    return {
        "new_count":         new_count,
        "returning_count":   returning_count,
        "total_count":       total,
        "new_pct":           round(new_pct, 1),
        "returning_pct":     round(returning_pct, 1),
        "new_pct_delta_pp":  new_pct_delta_pp,
        "week_start":        week_start,
    }


def transactions_per_customer(merchant_id: str, filters: dict | None = None) -> dict:
    """Card 5.2 data — distribution of customers across visit-count
    buckets over the 90-day window plus per-bucket revenue share so
    the takeaway can name the top cohort's customer % and revenue %.

    Buckets: ``1``, ``2-3``, ``4-6``, ``7-10``, ``11+`` visits.
    """
    return _transactions_per_customer_cached(merchant_id, _filters_key(filters or {}))


@st.cache_data(ttl=3600)
def _transactions_per_customer_cached(merchant_id: str, key: tuple) -> dict:
    filters = _unpack_filters_key(key)
    extra_where, extra_params = _own_filters_sql(filters)

    with _conn() as c:
        rows = c.execute(
            f"""
            WITH per_cust AS (
                SELECT t.customer_id,
                       COUNT(DISTINCT t.txn_id) AS n,
                       SUM(t.txn_total)         AS rev
                FROM tenant_transactions t
                WHERE t.merchant_id = ?{extra_where}
                GROUP BY t.customer_id
            )
            SELECT
                CASE
                    WHEN n = 1 THEN '1'
                    WHEN n BETWEEN 2 AND 3 THEN '2-3'
                    WHEN n BETWEEN 4 AND 6 THEN '4-6'
                    WHEN n BETWEEN 7 AND 10 THEN '7-10'
                    ELSE '11+'
                END AS bucket,
                COUNT(*) AS n_customers,
                SUM(rev) AS rev,
                MIN(n)   AS sort_key
            FROM per_cust
            GROUP BY bucket
            ORDER BY sort_key
            """,
            (merchant_id, *extra_params),
        ).fetchall()

    if not rows:
        return {
            "labels": [], "values": [], "top_cohort": "—",
            "top_cohort_cust_pct": 0.0, "top_cohort_rev_pct": 0.0,
            "n_one_visit": 0,
        }

    labels = [r[0] for r in rows]
    counts = [int(r[1]) for r in rows]
    revs   = [float(r[2] or 0) for r in rows]
    total_cust = sum(counts) or 1
    total_rev  = sum(revs)   or 1.0

    # Top cohort = the 11+ bucket if present, otherwise the highest
    # bucket available. The design's takeaway template is anchored to
    # "top cohort", so naming it consistently across viewers matters.
    top_idx = labels.index("11+") if "11+" in labels else len(labels) - 1
    top_cust_pct = round(counts[top_idx] / total_cust * 100, 1)
    top_rev_pct  = round(revs[top_idx]   / total_rev  * 100, 1)
    n_one_visit  = counts[labels.index("1")] if "1" in labels else 0

    return {
        "labels":   labels,
        "values":   counts,
        "x_label":  "Customers",
        "value_format": ",.0f",
        "top_cohort":          labels[top_idx],
        "top_cohort_cust_pct": top_cust_pct,
        "top_cohort_rev_pct":  top_rev_pct,
        "n_one_visit":         n_one_visit,
    }


# ---------------------------------------------------------------------------
# Phase 4.4 — Section 4 helpers (Catalog, Card 4.2 SKU performance)
# ---------------------------------------------------------------------------

def sku_performance(merchant_id: str, filters: dict | None = None) -> dict:
    """Per-SKU recent-week line count + revenue plus deviation vs the
    first-4w baseline mean. Used by Card 4.2's top/bottom toggle —
    the same row set sorted two different ways (top by recent
    revenue, bottom by deviation_pct ascending).

    Returns all SKUs (no top-N cap) so the renderer can sort + slice
    per view without re-querying.
    """
    return _sku_performance_cached(merchant_id, _filters_key(filters or {}))


@st.cache_data(ttl=3600)
def _sku_performance_cached(merchant_id: str, key: tuple) -> dict:
    filters = _unpack_filters_key(key)
    extra_where, extra_params = _own_filters_sql(filters)

    with _conn() as c:
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
                  AND strftime(date_trunc('week', t.txn_ts), '%Y-%m-%d')
                      BETWEEN ? AND ?
                GROUP BY p.sku, p.name, p.category, week
            )
            SELECT sku, sku_name, category,
                   SUM(CASE WHEN week = ? THEN n_lines ELSE 0 END) AS recent_lines,
                   SUM(CASE WHEN week = ? THEN revenue ELSE 0 END) AS recent_revenue,
                   SUM(CASE WHEN week BETWEEN ? AND ? THEN n_lines ELSE 0 END)
                        * 1.0 / 4 AS baseline_lines
            FROM weekly
            GROUP BY sku, sku_name, category
            """,
            (
                merchant_id, *extra_params,
                _A_BASELINE_WEEK_START, _A_RECENT_WEEK_START,
                _A_RECENT_WEEK_START,
                _A_RECENT_WEEK_START,
                _A_BASELINE_WEEK_START, _A_BASELINE_WEEK_END,
            ),
        ).fetchall()

    out: list[dict] = []
    for sku, name, cat, recent_lines, recent_rev, baseline_lines in rows:
        baseline_f = float(baseline_lines or 0)
        recent_f   = int(recent_lines or 0)
        rev_f      = float(recent_rev or 0)
        if baseline_f <= 0 and recent_f <= 0:
            continue
        if baseline_f <= 0:
            dev = None
        else:
            dev = round((recent_f / baseline_f - 1) * 100, 1)
        out.append({
            "sku":            sku,
            "sku_name":       name,
            "category":       cat,
            "baseline_lines": round(baseline_f, 1),
            "recent_lines":   recent_f,
            "recent_revenue": round(rev_f, 2),
            "deviation_pct":  dev,
        })
    return {"rows": out}


# ---------------------------------------------------------------------------
# Phase 4.4 — Section 2 helpers (Performance over time, Cards 2.1-2.3)
# ---------------------------------------------------------------------------

def performance_trajectory(merchant_id: str, filters: dict | None = None) -> dict:
    """Weekly revenue, transactions, and avg-basket trajectory over
    the 12-week full-weeks window. Shared by Card 2.1 (revenue
    trajectory) and Card 2.2 (transaction trajectory).

    Returns::

        {
            "weeks":          12 ISO date strings (week-starts),
            "revenue":        list of weekly revenue ($),
            "transactions":   list of weekly txn counts,
            "basket":         list of weekly avg ticket ($),
            "baseline_band":  list of weekly trailing-4w-mean revenue
                              (visual reference band for Card 2.1);
            "revenue_30d_pct":   % change over the last 30 days,
            "txn_30d_pct":       % change over the last 30 days,
            "basket_30d_pct":    % change over the last 30 days,
            "revenue_trend_shape": "stable" | "accelerating" |
                                   "decelerating",
            "txn_growth_driver":   "more trips" | "bigger baskets" |
                                   "both" | "neither, mixed",
        }
    """
    return _performance_trajectory_cached(merchant_id, _filters_key(filters or {}))


@st.cache_data(ttl=3600)
def _performance_trajectory_cached(merchant_id: str, key: tuple) -> dict:
    filters = _unpack_filters_key(key)
    extra_where, extra_params = _own_filters_sql(filters)

    weeks = [
        "2026-03-02", "2026-03-09", "2026-03-16", "2026-03-23",
        "2026-03-30", "2026-04-06", "2026-04-13", "2026-04-20",
        "2026-04-27", "2026-05-04", "2026-05-11", "2026-05-18",
    ]
    week_lo, week_hi = weeks[0], weeks[-1]

    with _conn() as c:
        rows = c.execute(
            f"""
            SELECT strftime(date_trunc('week', t.txn_ts), '%Y-%m-%d') AS week,
                   SUM(t.txn_total)         AS revenue,
                   COUNT(DISTINCT t.txn_id) AS n_txns
            FROM tenant_transactions t
            WHERE t.merchant_id = ?{extra_where}
              AND strftime(date_trunc('week', t.txn_ts), '%Y-%m-%d') BETWEEN ? AND ?
            GROUP BY week
            """,
            (merchant_id, *extra_params, week_lo, week_hi),
        ).fetchall()

    rev_by_wk = {w: float(r) for w, r, _ in rows}
    txn_by_wk = {w: int(n)   for w, _, n in rows}
    revenue      = [rev_by_wk.get(w, 0.0) for w in weeks]
    transactions = [txn_by_wk.get(w, 0)   for w in weeks]
    basket = [
        (revenue[i] / transactions[i]) if transactions[i] > 0 else 0.0
        for i in range(len(weeks))
    ]

    # Trailing 4-week mean baseline (rolling) for the revenue band.
    baseline_band: list[float] = []
    for i in range(len(weeks)):
        lo = max(0, i - 3)
        window = revenue[lo: i + 1]
        baseline_band.append(sum(window) / len(window) if window else 0.0)

    # 30-day delta = last 4 weeks mean vs prior 4 weeks mean.
    def _delta_pct(series: list[float]) -> float:
        if len(series) < 8:
            return 0.0
        recent = sum(series[-4:]) / 4
        prior  = sum(series[-8:-4]) / 4
        return round((recent / prior - 1) * 100, 1) if prior > 0 else 0.0

    rev_30d_pct = _delta_pct(revenue)
    txn_30d_pct = _delta_pct(transactions)
    bas_30d_pct = _delta_pct(basket)

    # Trend shape: compare the last-2-weeks mean revenue to the
    # 8-weeks-prior mean (weeks -10..-2). The wider prior window damps
    # noise from any single below-trend week, and the 2-week recent
    # window catches genuine acceleration without being whipsawed by
    # a single week's bounce. 4.4b's tighter 1w-vs-2w window
    # produced "accelerating" across every viewer — the calibrated
    # data has gentle overall growth and noise dominated the
    # single-point rate-of-change calculation.
    if len(revenue) >= 10:
        recent_mean = sum(revenue[-2:]) / 2
        prior_mean  = sum(revenue[-10:-2]) / 8 if revenue[-10:-2] else 0
        if prior_mean <= 0:
            trend_shape = "stable"
        else:
            growth = recent_mean / prior_mean - 1
            # 8 % deadband — calibrated to the synthetic data's
            # overall ~6-10 % growth band. Below 8 % growth reads as
            # tracking the panel-wide trend; above signals genuine
            # acceleration. A tighter 5 % deadband classified all
            # five viewers as accelerating in the current data;
            # 8 % differentiates per-viewer.
            if abs(growth) < 0.08:
                trend_shape = "stable"
            elif growth > 0:
                trend_shape = "accelerating"
            else:
                trend_shape = "decelerating"
    else:
        trend_shape = "stable"

    # Growth-driver classification for Card 2.2's combined takeaway.
    # 5pp noise floor — below that, treat as flat for the purposes of
    # naming a driver.
    noise = 5.0
    txn_up    = txn_30d_pct >  noise
    txn_down  = txn_30d_pct < -noise
    bas_up    = bas_30d_pct >  noise
    bas_down  = bas_30d_pct < -noise
    if txn_up and bas_up:
        driver = "both"
    elif txn_up and not bas_up:
        driver = "more trips"
    elif bas_up and not txn_up:
        driver = "bigger baskets"
    elif txn_down or bas_down:
        driver = "neither, mixed"
    else:
        driver = "stable on both"

    return {
        "weeks":          weeks,
        "revenue":        [round(v, 2) for v in revenue],
        "transactions":   transactions,
        "basket":         [round(b, 2) for b in basket],
        "baseline_band":  [round(v, 2) for v in baseline_band],
        "revenue_30d_pct": rev_30d_pct,
        "txn_30d_pct":    txn_30d_pct,
        "basket_30d_pct": bas_30d_pct,
        "revenue_trend_shape": trend_shape,
        "txn_growth_driver":   driver,
    }


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
    # suppressed cells aren't comparable). For grocers and TBL/TJX
    # alike there are typically zero-traffic overnight hours; calling
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

# Last full Mon-Sun week + first-of-baseline reuse the
# ``_A_RECENT_WEEK_START`` / ``_A_BASELINE_WEEK_*`` constants defined
# further down for the A2/A3 helpers. Defining them here would
# duplicate; declare lazily inside the function body via module
# constant lookup.


def kpi_strip(merchant_id: str, filters: dict | None = None) -> dict:
    """Five-card KPI strip data — Revenue / Transactions / Avg basket /
    Unique customers / Anomaly count, each with a delta vs the prior
    4-week average and a 12-week trailing sparkline.

    Filter semantics (Phase 4.5 — Decision 2 re-anchor):
      * "Recent week" = last full week ≤ filters["date_end"].
      * "Baseline" = up to 4 prior weeks within
        [date_start, date_end - 7 days].
      * Sparkline = weeks within the filter window.
      * If the filter window is < 14 days, deltas are suppressed
        (returned as ``None`` so the UI can render "—" instead of
        a misleading 0 %).
      * Stores filter applies to all OWN queries.
      * No filter set = the existing hardcoded 12-week behavior
        ending 2026-05-18.

    Returns::

        {
            "revenue":          {"value": float,
                                 "delta_pct": float | None,
                                 "sparkline": list[float]},
            "transactions":     {...},
            "avg_basket":       {...},
            "unique_customers": {...},
            "anomaly":          {...},
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
        # Re-anchor: enumerate weeks (Monday-keyed) with data inside
        # the filter window. Auto-detects available weeks rather than
        # building from calendar math — also picks up the stores
        # filter so weeks with zero matching txns don't show up.
        with _conn() as c:
            week_rows = c.execute(
                f"""
                SELECT DISTINCT strftime(date_trunc('week', t.txn_ts), '%Y-%m-%d') AS week
                FROM tenant_transactions t
                WHERE t.merchant_id = ?{extra_where}
                ORDER BY week
                """,
                (merchant_id, *extra_params),
            ).fetchall()
        weeks = [r[0] for r in week_rows]

    if not weeks:
        # Edge case: no data in the filter window. Return zeros so
        # the strip renders without crashing; deltas suppressed.
        return {
            "revenue":          {"value": 0.0, "delta_pct": None, "sparkline": []},
            "transactions":     {"value": 0,   "delta_pct": None, "sparkline": []},
            "avg_basket":       {"value": 0.0, "delta_pct": None, "sparkline": []},
            "unique_customers": {"value": 0,   "delta_pct": None, "sparkline": []},
            "anomaly": {
                "concerning": 0, "notable": 0, "total": 0,
                "n_stores_concerning": 0, "n_stores_notable": 0,
                "n_categories_concerning": 0, "n_categories_notable": 0,
                "trailing_concerning": [], "trailing_notable": [],
                "trailing_total": [],
            },
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
                   SUM(t.txn_total)              AS revenue,
                   COUNT(DISTINCT t.txn_id)      AS n_txns,
                   COUNT(DISTINCT t.customer_id) AS n_customers
            FROM tenant_transactions t
            WHERE t.merchant_id = ?{extra_where}
              AND strftime(date_trunc('week', t.txn_ts), '%Y-%m-%d')
                  BETWEEN ? AND ?
            GROUP BY week
            """,
            (merchant_id, *extra_params, week_lo, week_hi),
        ).fetchall()

    rev_by_wk: dict[str, float] = {w: float(r) for w, r, _, _ in rows}
    txn_by_wk: dict[str, int] = {w: int(t) for w, _, t, _ in rows}
    cust_by_wk: dict[str, int] = {w: int(c) for w, _, _, c in rows}

    def _series(d: dict) -> list[float]:
        return [float(d.get(w, 0)) for w in weeks]

    rev_series  = _series(rev_by_wk)
    txn_series  = _series(txn_by_wk)
    cust_series = _series(cust_by_wk)
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

    # Prior = up to 4 weeks ending one week before "recent", so the
    # 1-week buffer matches the original logic. With fewer than 5
    # weeks of data available, prior_vals shrinks naturally.
    prior_idx = slice(max(0, len(weeks) - 5), len(weeks) - 1)
    rev_recent  = rev_series[-1];  rev_delta  = _delta(rev_recent,  rev_series[prior_idx])
    txn_recent  = txn_series[-1];  txn_delta  = _delta(txn_recent,  txn_series[prior_idx])
    cust_recent = cust_series[-1]; cust_delta = _delta(cust_recent, cust_series[prior_idx])
    bas_recent  = basket_series[-1]; bas_delta = _delta(bas_recent,  basket_series[prior_idx])

    # Anomaly counts split by direction. Concerning = ratio < 0.85
    # (below baseline by ≥ 15 %); notable = ratio > 1.15 (above
    # baseline). The card flags "alert" only when there's a
    # concerning item — growth-mode merchants whose stores all run
    # above baseline (TBL, TJX in the current synthetic data) read
    # as "all clear" instead of red.
    anomaly_counts = _anomaly_counts_series(merchant_id, weeks, filters=f)
    anomaly_breakdown = _anomaly_count_breakdown(merchant_id, filters=f)

    return {
        "revenue": {
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
        "unique_customers": {
            "value":     int(cust_recent),
            "delta_pct": cust_delta,
            "sparkline": cust_series,
        },
        "anomaly": {
            "concerning":   anomaly_counts["concerning"][-1],
            "notable":      anomaly_counts["notable"][-1],
            "total":        anomaly_counts["total"][-1],
            "n_stores_concerning":     anomaly_breakdown["n_stores_concerning"],
            "n_stores_notable":        anomaly_breakdown["n_stores_notable"],
            "n_categories_concerning": anomaly_breakdown["n_categories_concerning"],
            "n_categories_notable":    anomaly_breakdown["n_categories_notable"],
            "trailing_concerning": anomaly_counts["concerning"],
            "trailing_notable":    anomaly_counts["notable"],
            "trailing_total":      anomaly_counts["total"],
        },
    }


def _anomaly_counts_series(
    merchant_id: str,
    weeks: list[str],
    filters: dict | None = None,
) -> dict[str, list[int]]:
    """Per-week anomaly counts split by direction. Returns three
    aligned lists::

        {
            "total":      [...],  # ratio off baseline by >= 15%
            "concerning": [...],  # ratio < 0.85 (below baseline)
            "notable":    [...],  # ratio > 1.15 (above baseline)
        }

    Counts stores + categories pooled. Direction-aware so the KPI
    card can flag "alert" only when there are below-baseline items
    rather than coloring growth-mode merchants red.
    """
    extra_where, extra_params = _own_filters_sql(filters)
    week_lo, week_hi = weeks[0], weeks[-1]
    # Baseline = first 4 weeks of the available window so the
    # ratio-vs-baseline measure stays anchored within the filter.
    baseline_lo, baseline_hi = weeks[0], weeks[min(3, len(weeks) - 1)]

    with _conn() as c:
        store_rows = c.execute(
            f"""
            SELECT t.store_id,
                   strftime(date_trunc('week', t.txn_ts), '%Y-%m-%d') AS week,
                   COUNT(DISTINCT t.txn_id) AS n_txns
            FROM tenant_transactions t
            WHERE t.merchant_id = ?{extra_where}
              AND strftime(date_trunc('week', t.txn_ts), '%Y-%m-%d') BETWEEN ? AND ?
            GROUP BY t.store_id, week
            """,
            (merchant_id, *extra_params, week_lo, week_hi),
        ).fetchall()
        cat_rows = c.execute(
            f"""
            SELECT p.category,
                   strftime(date_trunc('week', t.txn_ts), '%Y-%m-%d') AS week,
                   COUNT(*) AS n_lines
            FROM tenant_transaction_items i
            JOIN tenant_products p     ON p.sku    = i.sku
            JOIN tenant_transactions t ON t.txn_id = i.txn_id
            WHERE t.merchant_id = ?{extra_where}
              AND strftime(date_trunc('week', t.txn_ts), '%Y-%m-%d') BETWEEN ? AND ?
            GROUP BY p.category, week
            """,
            (merchant_id, *extra_params, week_lo, week_hi),
        ).fetchall()

    store_baselines: dict[str, list[int]] = {}
    store_by_wk: dict[str, dict[str, int]] = {}
    for sid, wk, n in store_rows:
        store_by_wk.setdefault(sid, {})[wk] = int(n)
        if baseline_lo <= wk <= baseline_hi:
            store_baselines.setdefault(sid, []).append(int(n))

    cat_baselines: dict[str, list[int]] = {}
    cat_by_wk: dict[str, dict[str, int]] = {}
    for cat, wk, n in cat_rows:
        cat_by_wk.setdefault(cat, {})[wk] = int(n)
        if baseline_lo <= wk <= baseline_hi:
            cat_baselines.setdefault(cat, []).append(int(n))

    total_s:      list[int] = []
    concerning_s: list[int] = []
    notable_s:    list[int] = []
    for wk in weeks:
        n_conc = 0
        n_note = 0
        for sid, base_list in store_baselines.items():
            base = sum(base_list) / 4 if base_list else 0
            if base <= 0:
                continue
            ratio = (store_by_wk.get(sid, {}).get(wk, 0)) / base
            if ratio <= 0.85:
                n_conc += 1
            elif ratio >= 1.15:
                n_note += 1
        for cat, base_list in cat_baselines.items():
            base = sum(base_list) / 4 if base_list else 0
            if base <= 0:
                continue
            ratio = (cat_by_wk.get(cat, {}).get(wk, 0)) / base
            if ratio <= 0.85:
                n_conc += 1
            elif ratio >= 1.15:
                n_note += 1
        concerning_s.append(n_conc)
        notable_s.append(n_note)
        total_s.append(n_conc + n_note)
    return {
        "total":      total_s,
        "concerning": concerning_s,
        "notable":    notable_s,
    }


def _anomaly_count_breakdown(merchant_id: str, filters: dict | None = None) -> dict:
    """Split this week's anomaly count into store-side and category-
    side contributions, each further split by direction — used by
    the KPI hint subtitle and the alert/clear flag decision."""
    s = store_anomalies_own_only(merchant_id, filters=filters)
    cat = category_anomalies(merchant_id, filters=filters)
    return {
        "n_stores":              s["n_flagged"],
        "n_stores_concerning":   s["n_under"],
        "n_stores_notable":      s["n_over"],
        "n_categories":          cat["n_flagged"],
        # category_anomalies doesn't pre-split direction; compute here.
        "n_categories_concerning": sum(
            1 for r in cat["rows"]
            if r["flag"] and r["deviation_pct"] is not None
            and r["deviation_pct"] < 0
        ),
        "n_categories_notable":   sum(
            1 for r in cat["rows"]
            if r["flag"] and r["deviation_pct"] is not None
            and r["deviation_pct"] > 0
        ),
    }


# ---------------------------------------------------------------------------
# Phase 4.3 — own-only question data (fallback path)
# ---------------------------------------------------------------------------
#
# The own-only variants exist as a fallback for any viewer with no
# same-segment peers. (datamodel-v2: all six banners now have 2 peers, so
# these are rarely hit — kept for a future single-member segment.)
# Recent-vs-baseline questions share the A2/A3 baseline
# convention: recent = last full Mon-Sun week (May 18 – 24), baseline
# = first 4 weeks of the panel (Mar 2 – 23). The 15% deviation floor
# is reused for store / SKU / category anomaly flags.

# Dayparts for QSR (TBL). Hour buckets are 00-23 from
# ``SUBSTR(txn_ts, 12, 2)``. The boundaries match common QSR daypart
# definitions: late-night / breakfast / lunch / afternoon / dinner.
# Kept to five for chart readability in the 35 % chat panel.
_QSR_DAYPARTS: list[tuple[str, list[int]]] = [
    ("Late night", [0, 1, 2, 3, 4]),
    ("Breakfast",  [5, 6, 7, 8, 9, 10]),
    ("Lunch",      [11, 12, 13, 14]),
    ("Afternoon",  [15, 16, 17]),
    ("Dinner",     [18, 19, 20, 21, 22, 23]),
]
_QSR_HOUR_TO_DAYPART: dict[int, str] = {
    h: dp for dp, hours in _QSR_DAYPARTS for h in hours
}
_QSR_DAYPART_ORDER = [dp for dp, _ in _QSR_DAYPARTS]


# ---------------------------------------------------------------------------
# T-A1 / R-A1 — Per-store recent-vs-baseline (no peer column)
# ---------------------------------------------------------------------------

def store_anomalies_own_only(merchant_id: str, filters: dict | None = None) -> dict:
    """Per-store deviation table for merchants without same-segment
    peers. Same first-4w baseline as A2; no peer-neighborhood column.
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
        own_rows = c.execute(
            f"""
            WITH weekly AS (
                SELECT t.store_id,
                       strftime(date_trunc('week', t.txn_ts), '%Y-%m-%d') AS week,
                       COUNT(DISTINCT t.txn_id) AS n_txns
                FROM tenant_transactions t
                WHERE t.merchant_id = ?{txn_extra_where}
                  AND strftime(date_trunc('week', t.txn_ts), '%Y-%m-%d')
                      BETWEEN ? AND ?
                GROUP BY t.store_id, week
            )
            SELECT s.store_id, s.neighborhood,
                   SUM(CASE WHEN w.week = ?
                            THEN w.n_txns ELSE 0 END) AS recent,
                   SUM(CASE WHEN w.week BETWEEN ? AND ?
                            THEN w.n_txns ELSE 0 END) * 1.0 / 4
                        AS baseline
            FROM tenant_stores s
            LEFT JOIN weekly w ON w.store_id = s.store_id
            WHERE s.merchant_id = ?{store_extra_where}
            GROUP BY s.store_id, s.neighborhood
            """,
            (
                merchant_id, *txn_extra_params,
                _A_BASELINE_WEEK_START, _A_RECENT_WEEK_START,
                _A_RECENT_WEEK_START,
                _A_BASELINE_WEEK_START, _A_BASELINE_WEEK_END,
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
# T-A2 — Per-SKU anomaly table
# ---------------------------------------------------------------------------





# ---------------------------------------------------------------------------
# T-A3 — Day-of-week × daypart heatmap (ratios)
# ---------------------------------------------------------------------------





# ---------------------------------------------------------------------------
# T-D1 / R-D1 — Category share bars
# ---------------------------------------------------------------------------

def category_share_own(
    merchant_id: str, top_n: int = 8, filters: dict | None = None,
) -> dict:
    """Per-category share of own revenue. Top ``top_n`` categories;
    smaller categories rolled into "Other". Pattern 2 own-only-bars.
    """
    return _category_share_own_cached(merchant_id, top_n, _filters_key(filters or {}))


@st.cache_data(ttl=3600)
def _category_share_own_cached(merchant_id: str, top_n: int, key: tuple) -> dict:
    filters = _unpack_filters_key(key)
    extra_where, extra_params = _own_filters_sql(filters)

    with _conn() as c:
        rows = c.execute(
            f"""
            SELECT p.category, SUM(i.line_total) AS rev
            FROM tenant_transaction_items i
            JOIN tenant_products p     ON p.sku    = i.sku
            JOIN tenant_transactions t ON t.txn_id = i.txn_id
            WHERE t.merchant_id = ?{extra_where}
            GROUP BY p.category
            ORDER BY rev DESC
            """,
            (merchant_id, *extra_params),
        ).fetchall()

    if not rows:
        return {"labels": [], "values": [], "top3_names": [], "top3_pct": 0.0,
                "x_label": "Share of revenue (%)"}

    total = sum(float(r[1]) for r in rows) or 1.0
    top = rows[:top_n]
    rest = rows[top_n:]
    labels = [r[0] for r in top]
    values = [round(float(r[1]) / total * 100, 1) for r in top]
    if rest:
        other_rev = sum(float(r[1]) for r in rest)
        labels.append("Other")
        values.append(round(other_rev / total * 100, 1))

    top3 = top[:3]
    top3_names = [r[0] for r in top3]
    top3_pct = round(sum(float(r[1]) for r in top3) / total * 100, 1)

    return {
        "labels":     labels,
        "values":     values,
        "x_label":    "Share of revenue (%)",
        "value_format": ".1f",
        "top3_names": top3_names,
        "top3_pct":   top3_pct,
    }


# ---------------------------------------------------------------------------
# Compatibility shims for placeholders.py and earlier views (no-op now)
# ---------------------------------------------------------------------------

def has_same_segment_peers(merchant_id: str) -> bool:
    """Retained for any caller that still imports it."""
    own_seg = MERCHANT_SEGMENT[merchant_id]
    other_segs = [s for m, s in MERCHANT_SEGMENT.items() if m != merchant_id]
    return own_seg in other_segs
