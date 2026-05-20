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

import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from src.lake import get_lake_stores, get_lake_transactions  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "payments.db"

PANEL_START = date(2026, 3, 1)
PANEL_END   = date(2026, 5, 29)

MERCHANT_NAME = {
    "KRG": "Kroger", "ACM": "Acme", "WDX": "Winn-Dixie",
    "TBL": "Taco Bell", "TJX": "TJ Maxx",
}
MERCHANT_SEGMENT = {
    "KRG": "grocery", "ACM": "grocery", "WDX": "grocery",
    "TBL": "qsr", "TJX": "off_price_retail",
}
MERCHANT_COLOR = {
    "KRG": "#0F4C81", "ACM": "#3A6FA5", "WDX": "#6F8FB8",
    "TBL": "#C0563F", "TJX": "#5B7B58",
}


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    c.execute("PRAGMA foreign_keys = ON")
    return c


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
        return pd.read_sql_query(sql, c, params=(merchant_id,))


@st.cache_data(ttl=3600)
def categories_for(merchant_id: str) -> list[str]:
    sql = """
    SELECT DISTINCT category FROM tenant_products
    WHERE merchant_id = ? ORDER BY category
    """
    with _conn() as c:
        df = pd.read_sql_query(sql, c, params=(merchant_id,))
    return df["category"].tolist()


# ---------------------------------------------------------------------------
# KPI block — switches between transaction-level and line-item-level
# queries depending on whether a category filter is active.
# ---------------------------------------------------------------------------

@st.cache_data(ttl=3600)
def kpi_block(merchant_id: str, filters_key: tuple) -> dict:  # noqa: ARG001
    """4 KPI totals + 30-day-vs-prior-30-day deltas, scoped to the
    current filters. The 30-day windows are anchored to PANEL_END
    regardless of the date filter (so the "vs prior 30d" arrow has
    a fixed meaning); filters still apply to both windows."""
    filters = _unpack_filters_key(filters_key)
    last_end, last_start   = PANEL_END, date(2026, 4, 30)
    prior_end, prior_start = date(2026, 4, 29), date(2026, 3, 31)

    has_cat = _has_category_filter(filters)
    txn_where, txn_params = _txn_where(filters)
    cat_where, cat_params = _category_where(filters)

    def _totals(start: date | None, end: date | None) -> tuple[float, int, int]:
        """Returns (revenue, transactions, customers) for the slice
        bounded by (start, end). If start/end are None, the slice is
        the full filter (i.e. the panel totals)."""
        if has_cat:
            # Line-item path: revenue = sum(line_total), transactions
            # = distinct txn_id with at least one matching item,
            # customers = distinct customer_id of those transactions.
            base_sql = """
            SELECT COALESCE(SUM(i.line_total), 0) AS revenue,
                   COUNT(DISTINCT t.txn_id)       AS txns,
                   COUNT(DISTINCT t.customer_id)  AS customers
            FROM tenant_transaction_items i
            JOIN tenant_transactions t ON t.txn_id = i.txn_id
            JOIN tenant_products p     ON p.sku    = i.sku
            WHERE t.merchant_id = ?
            """
            params: list = [merchant_id]
            if txn_where:
                base_sql += f" AND {txn_where}"
                params.extend(txn_params)
            base_sql += f" AND {cat_where}"
            params.extend(cat_params)
        else:
            # Transaction path: revenue = sum(txn_total), transactions
            # = distinct txn_id, customers = distinct customer_id.
            base_sql = """
            SELECT COALESCE(SUM(t.txn_total), 0) AS revenue,
                   COUNT(DISTINCT t.txn_id)     AS txns,
                   COUNT(DISTINCT t.customer_id) AS customers
            FROM tenant_transactions t
            WHERE t.merchant_id = ?
            """
            params = [merchant_id]
            if txn_where:
                base_sql += f" AND {txn_where}"
                params.extend(txn_params)
        if start and end:
            base_sql += " AND DATE(t.txn_ts) BETWEEN ? AND ?"
            params.extend([start.isoformat(), end.isoformat()])
        with _conn() as c:
            return c.execute(base_sql, params).fetchone()

    rev_all, txns_all, cust_all = _totals(None, None)
    rev_last,  txns_last,  cust_last  = _totals(last_start, last_end)
    rev_prior, txns_prior, cust_prior = _totals(prior_start, prior_end)

    avg_all   = rev_all  / txns_all  if txns_all  else 0.0
    avg_last  = rev_last / txns_last if txns_last else 0.0
    avg_prior = rev_prior / txns_prior if txns_prior else 0.0

    def _delta(a: float, b: float) -> float | None:
        return (a - b) / b if b else None

    return {
        "revenue":           rev_all,
        "transactions":      txns_all,
        "avg_transaction":   avg_all,
        "active_customers":  cust_all,
        "revenue_delta":          _delta(rev_last, rev_prior),
        "transactions_delta":     _delta(txns_last, txns_prior),
        "avg_transaction_delta":  _delta(avg_last, avg_prior),
        "active_customers_delta": _delta(cust_last, cust_prior),
    }


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


# ---------------------------------------------------------------------------
# Sparkline + top SKUs
# ---------------------------------------------------------------------------

@st.cache_data(ttl=3600)
def daily_volume(merchant_id: str, filters_key: tuple) -> pd.DataFrame:
    """Daily transaction counts, filtered. Uses the same line-item path
    when categories are filtered so the count reflects only days where
    a matching item was sold."""
    filters = _unpack_filters_key(filters_key)
    txn_where, txn_params = _txn_where(filters)
    cat_where, cat_params = _category_where(filters)
    if _has_category_filter(filters):
        sql = """
        SELECT DATE(t.txn_ts) AS day, COUNT(DISTINCT t.txn_id) AS n
        FROM tenant_transaction_items i
        JOIN tenant_transactions t ON t.txn_id = i.txn_id
        JOIN tenant_products p     ON p.sku    = i.sku
        WHERE t.merchant_id = ?
        """
        params = [merchant_id]
        if txn_where:
            sql += f" AND {txn_where}"
            params.extend(txn_params)
        sql += f" AND {cat_where} GROUP BY DATE(t.txn_ts) ORDER BY day"
        params.extend(cat_params)
    else:
        sql = """
        SELECT DATE(t.txn_ts) AS day, COUNT(*) AS n
        FROM tenant_transactions t
        WHERE t.merchant_id = ?
        """
        params = [merchant_id]
        if txn_where:
            sql += f" AND {txn_where}"
            params.extend(txn_params)
        sql += " GROUP BY DATE(t.txn_ts) ORDER BY day"
    with _conn() as c:
        return pd.read_sql_query(sql, c, params=params)


@st.cache_data(ttl=3600)
def top_skus(merchant_id: str, filters_key: tuple, n: int = 5) -> pd.DataFrame:
    """Top-N SKUs by revenue. Always uses the line-item path; category
    filter (if any) restricts to those categories."""
    filters = _unpack_filters_key(filters_key)
    txn_where, txn_params = _txn_where(filters)
    cat_where, cat_params = _category_where(filters)
    sql = """
    SELECT p.name, ROUND(SUM(i.line_total), 2) AS revenue
    FROM tenant_transaction_items i
    JOIN tenant_transactions t ON t.txn_id = i.txn_id
    JOIN tenant_products p     ON p.sku    = i.sku
    WHERE t.merchant_id = ?
    """
    params = [merchant_id]
    if txn_where:
        sql += f" AND {txn_where}"
        params.extend(txn_params)
    if cat_where:
        sql += f" AND {cat_where}"
        params.extend(cat_params)
    sql += " GROUP BY p.name ORDER BY revenue DESC LIMIT ?"
    params.append(n)
    with _conn() as c:
        return pd.read_sql_query(sql, c, params=params)


# ---------------------------------------------------------------------------
# Category mix + store performance
# ---------------------------------------------------------------------------

@st.cache_data(ttl=3600)
def category_mix(merchant_id: str, filters_key: tuple) -> pd.DataFrame:
    """Revenue by category. Category filter (if any) narrows the donut
    to the selected categories — natural behavior since the user has
    asked to focus on those."""
    filters = _unpack_filters_key(filters_key)
    txn_where, txn_params = _txn_where(filters)
    cat_where, cat_params = _category_where(filters)
    sql = """
    SELECT p.category, ROUND(SUM(i.line_total), 2) AS revenue
    FROM tenant_transaction_items i
    JOIN tenant_transactions t ON t.txn_id = i.txn_id
    JOIN tenant_products p     ON p.sku    = i.sku
    WHERE t.merchant_id = ?
    """
    params = [merchant_id]
    if txn_where:
        sql += f" AND {txn_where}"
        params.extend(txn_params)
    if cat_where:
        sql += f" AND {cat_where}"
        params.extend(cat_params)
    sql += " GROUP BY p.category ORDER BY revenue DESC"
    with _conn() as c:
        return pd.read_sql_query(sql, c, params=params)


@st.cache_data(ttl=3600)
def store_performance(merchant_id: str, filters_key: tuple) -> pd.DataFrame:
    """Per-store transaction counts. Category filter switches to line-
    item path so the count reflects only matching items."""
    filters = _unpack_filters_key(filters_key)
    txn_where, txn_params = _txn_where(filters)
    cat_where, cat_params = _category_where(filters)
    if _has_category_filter(filters):
        sql = """
        SELECT s.store_id, s.neighborhood,
               COUNT(DISTINCT t.txn_id) AS n_txns
        FROM tenant_stores s
        LEFT JOIN tenant_transactions t
          ON t.store_id = s.store_id AND t.merchant_id = s.merchant_id
        LEFT JOIN tenant_transaction_items i ON i.txn_id = t.txn_id
        LEFT JOIN tenant_products p           ON p.sku   = i.sku AND p.merchant_id = s.merchant_id
        WHERE s.merchant_id = ?
        """
        params = [merchant_id]
        if txn_where:
            sql += f" AND ({txn_where} OR t.txn_id IS NULL)"
            params.extend(txn_params)
        sql += f" AND ({cat_where} OR t.txn_id IS NULL)"
        params.extend(cat_params)
        sql += " GROUP BY s.store_id, s.neighborhood ORDER BY n_txns DESC"
    else:
        sql = """
        SELECT s.store_id, s.neighborhood,
               COUNT(t.txn_id) AS n_txns
        FROM tenant_stores s
        LEFT JOIN tenant_transactions t
          ON t.store_id = s.store_id AND t.merchant_id = s.merchant_id
        WHERE s.merchant_id = ?
        """
        params = [merchant_id]
        if txn_where:
            sql += f" AND ({txn_where} OR t.txn_id IS NULL)"
            params.extend(txn_params)
        sql += " GROUP BY s.store_id, s.neighborhood ORDER BY n_txns DESC"
    with _conn() as c:
        return pd.read_sql_query(sql, c, params=params)


# ---------------------------------------------------------------------------
# Customer Engagement — observable metrics only (no synthetic constructs)
#
# Replaces the prior "Customer Insights" expander, which surfaced
# `grocer_affinity_type` and `behavioral_segment` (constructs from the
# v2.5 generator, not fields a real merchant's POS / analytics platform
# would expose). The four blocks below are derivable from any merchant's
# transaction stream.
# ---------------------------------------------------------------------------

# Anchor for the "last 30 days" recency window — fixed to PANEL_END so
# the metric has a stable meaning regardless of the date filter.
_LAST_30_START = date(2026, 4, 30)


@st.cache_data(ttl=3600)
def customer_engagement(merchant_id: str, filters_key: tuple) -> dict:
    """Four observable customer-engagement blocks:
      - txn_freq:           distribution of transaction count per customer
      - recency:            active in last 30d vs lapsed 30d+
      - revenue_concentration: top-decile share of revenue
      - top_promos:         promo redemption rate (% of customers)
    """
    filters = _unpack_filters_key(filters_key)
    txn_where, txn_params = _txn_where(filters)
    cat_where, cat_params = _category_where(filters)
    has_cat = _has_category_filter(filters)

    # Choose the base relation: tenant_transactions if no category filter;
    # otherwise the item-joined view so the engagement metrics align with
    # the rest of the dashboard's filter semantics.
    if has_cat:
        base_join = (
            "FROM tenant_transaction_items i "
            "JOIN tenant_transactions t ON t.txn_id = i.txn_id "
            "JOIN tenant_products p     ON p.sku    = i.sku"
        )
    else:
        base_join = "FROM tenant_transactions t"

    where_clauses = ["t.merchant_id = ?"]
    base_params: list = [merchant_id]
    if txn_where:
        where_clauses.append(txn_where)
        base_params.extend(txn_params)
    if cat_where:
        where_clauses.append(cat_where)
        base_params.extend(cat_params)
    base_where = " AND ".join(where_clauses)

    out: dict = {}
    with _conn() as c:
        # --- Block 1: transactions-per-customer distribution -------------
        sql = f"""
        WITH per_cust AS (
            SELECT t.customer_id, COUNT(DISTINCT t.txn_id) AS n
            {base_join} WHERE {base_where}
            GROUP BY t.customer_id
        )
        SELECT
            CASE
                WHEN n = 1 THEN '1'
                WHEN n BETWEEN 2 AND 3 THEN '2–3'
                WHEN n BETWEEN 4 AND 6 THEN '4–6'
                WHEN n BETWEEN 7 AND 10 THEN '7–10'
                ELSE '11+'
            END AS bucket,
            COUNT(*) AS n_customers
        FROM per_cust
        GROUP BY bucket
        ORDER BY MIN(n)
        """
        out["txn_freq"] = pd.read_sql_query(sql, c, params=base_params)

        # --- Block 2: recency (active in last 30 days vs lapsed) ---------
        recency_sql = f"""
        SELECT
            COUNT(DISTINCT CASE WHEN DATE(t.txn_ts) >= ? THEN t.customer_id END) AS active_30d,
            COUNT(DISTINCT t.customer_id) AS total_customers
        {base_join} WHERE {base_where}
        """
        active, total = c.execute(
            recency_sql, [_LAST_30_START.isoformat(), *base_params],
        ).fetchone()
        active = int(active or 0)
        total  = int(total  or 0)
        lapsed = max(0, total - active)
        out["recency"] = {
            "active_30d":      active,
            "lapsed_30d":      lapsed,
            "total_customers": total,
            "active_pct":      (100.0 * active / total) if total else 0.0,
        }

        # --- Block 3: revenue concentration (top-decile share) -----------
        rev_value = "i.line_total" if has_cat else "t.txn_total"
        sql = f"""
        WITH cust AS (
            SELECT t.customer_id, SUM({rev_value}) AS rev
            {base_join} WHERE {base_where}
            GROUP BY t.customer_id
        ),
        ranked AS (
            SELECT customer_id, rev,
                   ROW_NUMBER() OVER (ORDER BY rev DESC) AS rn,
                   COUNT(*) OVER ()  AS total_n,
                   SUM(rev) OVER ()  AS total_rev
            FROM cust
        )
        SELECT
            ROUND(100.0 * SUM(CASE WHEN rn <= total_n * 0.10 THEN rev ELSE 0 END) / NULLIF(total_rev,0), 1) AS pct_top10,
            ROUND(100.0 * SUM(CASE WHEN rn <= total_n * 0.20 THEN rev ELSE 0 END) / NULLIF(total_rev,0), 1) AS pct_top20,
            ROUND(100.0 * SUM(CASE WHEN rn <= total_n * 0.50 THEN rev ELSE 0 END) / NULLIF(total_rev,0), 1) AS pct_top50
        FROM ranked
        """
        row = c.execute(sql, base_params).fetchone() or (0, 0, 0)
        out["revenue_concentration"] = {
            "pct_top10": row[0] or 0.0,
            "pct_top20": row[1] or 0.0,
            "pct_top50": row[2] or 0.0,
        }

        # --- Block 4: top promos by redemption rate ----------------------
        # "Redemption rate" = distinct customers who redeemed the promo /
        # distinct customers active in the filter window. Two queries
        # (denominator + per-promo numerator) are run separately and
        # joined in pandas — avoids the bind-count brittleness of nested
        # CTEs with repeated parameter sets.
        denom_sql = f"""
            SELECT COUNT(DISTINCT t.customer_id) AS n
            FROM tenant_transactions t WHERE t.merchant_id = ?
        """
        denom_params: list = [merchant_id]
        if txn_where:
            denom_sql += f" AND {txn_where}"
            denom_params.extend(txn_params)
        denominator = c.execute(denom_sql, denom_params).fetchone()[0] or 0

        promo_sql = """
        SELECT pr.promo_name,
               COUNT(DISTINCT t.customer_id) AS unique_redeemers
        FROM tenant_transaction_items i
        JOIN tenant_transactions t ON t.txn_id = i.txn_id
        JOIN tenant_promotions pr  ON pr.promo_id = i.promo_id AND pr.sku = i.sku
        WHERE t.merchant_id = ?
        """
        promo_params: list = [merchant_id]
        if txn_where:
            promo_sql += f" AND {txn_where}"
            promo_params.extend(txn_params)
        promo_sql += """
            GROUP BY pr.promo_name
            ORDER BY unique_redeemers DESC
            LIMIT 5
        """
        promos = pd.read_sql_query(promo_sql, c, params=promo_params)
        if denominator and not promos.empty:
            promos["redemption_rate_pct"] = (
                100.0 * promos["unique_redeemers"] / denominator
            ).round(1)
        else:
            promos["redemption_rate_pct"] = 0.0
        out["top_promos"] = promos

    return out


# ---------------------------------------------------------------------------
# Payment Intelligence — Verifone-uniquely-rich payment fields
# ---------------------------------------------------------------------------

@st.cache_data(ttl=3600)
def payment_method_mix(merchant_id: str, filters_key: tuple) -> pd.DataFrame:
    """Share of transactions by payment_type (credit / debit)."""
    filters = _unpack_filters_key(filters_key)
    where, params = _txn_where(filters)
    has_cat = _has_category_filter(filters)
    cat_where, cat_params = _category_where(filters)
    if has_cat:
        sql = """
        SELECT t.payment_type AS label, COUNT(DISTINCT t.txn_id) AS n
        FROM tenant_transaction_items i
        JOIN tenant_transactions t ON t.txn_id = i.txn_id
        JOIN tenant_products p     ON p.sku    = i.sku
        WHERE t.merchant_id = ?
        """
        q_params = [merchant_id]
    else:
        sql = "SELECT payment_type AS label, COUNT(*) AS n FROM tenant_transactions t WHERE t.merchant_id = ?"
        q_params = [merchant_id]
    if where:
        sql += f" AND {where}"
        q_params.extend(params)
    if has_cat:
        sql += f" AND {cat_where}"
        q_params.extend(cat_params)
    sql += " GROUP BY label ORDER BY n DESC"
    with _conn() as c:
        return pd.read_sql_query(sql, c, params=q_params)


@st.cache_data(ttl=3600)
def card_network_mix(merchant_id: str, filters_key: tuple) -> pd.DataFrame:
    """Share of transactions by card_network (visa / mc / amex / discover)."""
    filters = _unpack_filters_key(filters_key)
    where, params = _txn_where(filters)
    has_cat = _has_category_filter(filters)
    cat_where, cat_params = _category_where(filters)
    if has_cat:
        sql = """
        SELECT t.card_network AS label, COUNT(DISTINCT t.txn_id) AS n
        FROM tenant_transaction_items i
        JOIN tenant_transactions t ON t.txn_id = i.txn_id
        JOIN tenant_products p     ON p.sku    = i.sku
        WHERE t.merchant_id = ? AND t.card_network IS NOT NULL
        """
        q_params = [merchant_id]
    else:
        sql = """SELECT card_network AS label, COUNT(*) AS n
                 FROM tenant_transactions t
                 WHERE t.merchant_id = ? AND t.card_network IS NOT NULL"""
        q_params = [merchant_id]
    if where:
        sql += f" AND {where}"
        q_params.extend(params)
    if has_cat:
        sql += f" AND {cat_where}"
        q_params.extend(cat_params)
    sql += " GROUP BY label ORDER BY n DESC"
    with _conn() as c:
        return pd.read_sql_query(sql, c, params=q_params)


@st.cache_data(ttl=3600)
def entry_mode_trend(merchant_id: str, filters_key: tuple) -> pd.DataFrame:
    """Per-day count of transactions by entry_mode. Pivoted wide for a
    stacked-area chart (one column per mode)."""
    filters = _unpack_filters_key(filters_key)
    where, params = _txn_where(filters)
    has_cat = _has_category_filter(filters)
    cat_where, cat_params = _category_where(filters)
    if has_cat:
        sql = """
        SELECT DATE(t.txn_ts) AS day, t.entry_mode AS entry_mode, COUNT(DISTINCT t.txn_id) AS n
        FROM tenant_transaction_items i
        JOIN tenant_transactions t ON t.txn_id = i.txn_id
        JOIN tenant_products p     ON p.sku    = i.sku
        WHERE t.merchant_id = ?
        """
        q_params = [merchant_id]
    else:
        sql = """SELECT DATE(txn_ts) AS day, entry_mode, COUNT(*) AS n
                 FROM tenant_transactions t WHERE t.merchant_id = ?"""
        q_params = [merchant_id]
    if where:
        sql += f" AND {where}"
        q_params.extend(params)
    if has_cat:
        sql += f" AND {cat_where}"
        q_params.extend(cat_params)
    sql += " GROUP BY day, entry_mode ORDER BY day"
    with _conn() as c:
        long = pd.read_sql_query(sql, c, params=q_params)
    if long.empty:
        return long
    wide = (long.pivot_table(index="day", columns="entry_mode", values="n",
                              aggfunc="sum")
                .fillna(0).astype(int).reset_index())
    wide["day"] = pd.to_datetime(wide["day"])
    return wide


@st.cache_data(ttl=3600)
def wallet_adoption(merchant_id: str, filters_key: tuple) -> dict:
    """Mobile-wallet adoption among contactless transactions.

    Returns:
      - contactless_total:   distinct txn count where entry_mode='contactless'
      - wallet_total:        of those, the count with wallet_type != NULL
      - wallet_breakdown:    {apple, google, samsung} counts
      - wallet_pct:          wallet_total / contactless_total × 100
    """
    filters = _unpack_filters_key(filters_key)
    where, params = _txn_where(filters)
    has_cat = _has_category_filter(filters)
    cat_where, cat_params = _category_where(filters)
    if has_cat:
        base = """
        FROM tenant_transaction_items i
        JOIN tenant_transactions t ON t.txn_id = i.txn_id
        JOIN tenant_products p     ON p.sku    = i.sku
        WHERE t.merchant_id = ? AND t.entry_mode = 'contactless'
        """
    else:
        base = "FROM tenant_transactions t WHERE t.merchant_id = ? AND t.entry_mode = 'contactless'"
    q_params: list = [merchant_id]
    if where:
        base += f" AND {where}"
        q_params.extend(params)
    if has_cat:
        base += f" AND {cat_where}"
        q_params.extend(cat_params)
    with _conn() as c:
        # Use COUNT(DISTINCT t.txn_id) consistently in both code paths so
        # the wallet_total / contactless_total ratio is meaningful when
        # categories are filtered (one transaction can have many items).
        total = c.execute(
            f"SELECT COUNT(DISTINCT t.txn_id) {base}", q_params,
        ).fetchone()[0] or 0
        with_wallet = c.execute(
            f"SELECT COUNT(DISTINCT t.txn_id) {base} AND t.wallet_type IS NOT NULL",
            q_params,
        ).fetchone()[0] or 0
        breakdown_df = pd.read_sql_query(
            f"""SELECT t.wallet_type AS wallet, COUNT(DISTINCT t.txn_id) AS n
                {base} AND t.wallet_type IS NOT NULL
                GROUP BY t.wallet_type ORDER BY n DESC""",
            c, params=q_params,
        )
    return {
        "contactless_total": int(total),
        "wallet_total":      int(with_wallet),
        "wallet_pct":        (100.0 * with_wallet / total) if total else 0.0,
        "wallet_breakdown":  breakdown_df,
    }


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
               CAST(SUBSTR(t.txn_ts, 12, 2) AS INTEGER) AS hr,
               COUNT(DISTINCT t.txn_id) AS n
        FROM tenant_transaction_items i
        JOIN tenant_transactions t ON t.txn_id = i.txn_id
        JOIN tenant_products p     ON p.sku    = i.sku
        WHERE t.merchant_id = ?
        """
        q_params = [merchant_id]
    else:
        sql = """SELECT CAST(strftime('%w', t.txn_ts) AS INTEGER) AS dow,
                        CAST(SUBSTR(t.txn_ts, 12, 2) AS INTEGER) AS hr,
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
        long = pd.read_sql_query(sql, c, params=q_params)
    # Pivot to a 7×24 grid; fill missing cells with 0.
    if long.empty:
        return pd.DataFrame(0, index=range(7), columns=range(24))
    pivot = (long.pivot_table(index="dow", columns="hr",
                               values="n", aggfunc="sum")
                 .fillna(0).astype(int))
    pivot = pivot.reindex(index=range(7), columns=range(24), fill_value=0)
    return pivot


# ---------------------------------------------------------------------------
# Lake helpers (still used by placeholder handlers; kept for back-compat)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=3600)
def lake_txns_filtered(merchant_id: str, sql_filter: str | None) -> pd.DataFrame:
    """Cached wrapper around `get_lake_transactions`. Used by placeholder
    handlers for cross-merchant pricing comparisons."""
    return get_lake_transactions(merchant_id, sql_filter=sql_filter)


# ---------------------------------------------------------------------------
# Phase 4.1 — Question-specific data queries
# ---------------------------------------------------------------------------
#
# Each suggested question that anchors on a dashboard chart pattern has a
# dedicated data helper here. The helper returns a dict shaped for the
# pattern's render function (see chart_patterns.py).
#
# A1 (University City decline) anchors on Pattern 1: weekly transaction
# trajectory for the merchant's UC stores plus peer_a / peer_b UC stores,
# normalized to a 4-week baseline. Grocer viewers only — TBL and TJX
# have no same-segment peer with UC presence.

# The 90-day panel spans Sun Mar 1 2026 → Fri May 29 2026. SQLite's
# `DATE(ts, 'weekday 0', '-6 days')` bins each timestamp to the Monday
# that starts its containing Mon-Sun week (the convention v2.5 reports
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
_A1_DECLINE_PCT     = 20.0


@st.cache_data(ttl=3600)
def uc_decline_trajectory(merchant_id: str) -> dict:
    """Weekly transaction trajectory for the merchant's University City
    stores plus same-segment peers (peer_a, peer_b), normalized to a
    4-week baseline. Used by A1.

    Returns dict with keys:
        weeks:              list of week-starting (Monday) ISO date strings.
        own:                list of normalized values (baseline = 100);
                            ``None`` for weeks with no data.
        peer_a, peer_b:     same shape; ``None`` when peer has no UC
                            footprint or weekly cell falls below k=5.
        trough_week:        human-formatted week of the own merchant's
                            trough ("Apr 27") — empty if no trough found.
        own_pct_drop:       integer percentage drop from baseline at the
                            own trough.
        peer_a_pct_drop,
        peer_b_pct_drop:    integer drop for each peer at the same
                            trough window (±1 week).
        market_signal:      ``"market-wide"`` / ``"store-specific"`` /
                            ``"mixed"`` — see Phase 4.1 A1 spec.
        has_peers:          True if at least one peer series has data;
                            False (the pattern helper should drop the
                            peer overlays) when both are empty.

    The query is k=5 suppressed on peer cells per Phase 1.5: weeks
    with fewer than 5 peer transactions in UC return ``None`` for that
    peer slot in that week.
    """
    from datetime import date

    lake_t = f"lake_transactions_{merchant_id}"
    lake_s = f"lake_stores_{merchant_id}"

    with _conn() as c:
        own_rows = c.execute(
            """
            SELECT DATE(t.txn_ts, 'weekday 0', '-6 days') AS week,
                   COUNT(DISTINCT t.txn_id) AS n
            FROM tenant_transactions t
            JOIN tenant_stores s ON s.store_id = t.store_id
            WHERE t.merchant_id = ?
              AND s.neighborhood = 'University City'
              AND DATE(t.txn_ts, 'weekday 0', '-6 days') BETWEEN ? AND ?
            GROUP BY week
            ORDER BY week
            """,
            (merchant_id, _A1_FULL_WEEK_FIRST, _A1_FULL_WEEK_LAST),
        ).fetchall()

        peer_rows = c.execute(
            f"""
            SELECT t.peer_id,
                   DATE(t.txn_date, 'weekday 0', '-6 days') AS week,
                   COUNT(DISTINCT t.lake_txn_id) AS n
            FROM {lake_t} t
            JOIN {lake_s} s ON s.lake_store_id = t.lake_store_id
            WHERE s.neighborhood = 'University City'
              AND t.peer_segment = 'grocery'
              AND t.peer_id IN ('peer_a', 'peer_b')
              AND DATE(t.txn_date, 'weekday 0', '-6 days') BETWEEN ? AND ?
            GROUP BY t.peer_id, week
            HAVING COUNT(DISTINCT t.lake_txn_id) >= 5
            ORDER BY t.peer_id, week
            """,
            (_A1_FULL_WEEK_FIRST, _A1_FULL_WEEK_LAST),
        ).fetchall()

    # Build week ordering from the union of every series' weeks so we
    # can hand the chart helper aligned lists.
    own_by_week    = {w: int(n) for w, n in own_rows}
    peer_a_by_week = {w: int(n) for pid, w, n in peer_rows if pid == "peer_a"}
    peer_b_by_week = {w: int(n) for pid, w, n in peer_rows if pid == "peer_b"}
    all_weeks      = sorted(
        set(own_by_week) | set(peer_a_by_week) | set(peer_b_by_week)
    )

    empty = {
        "weeks": [], "own": [], "peer_a": [], "peer_b": [],
        "trough_week": "", "own_pct_drop": 0,
        "peer_a_pct_drop": 0, "peer_b_pct_drop": 0,
        "market_signal": "store-specific",
        "has_peers": False,
    }
    if not all_weeks:
        return empty

    def _series(by_week: dict) -> list[int | None]:
        return [by_week.get(w) for w in all_weeks]

    def _baseline(seq: list) -> float | None:
        head = [v for v in seq[:_A1_BASELINE_WEEKS] if v is not None]
        return (sum(head) / len(head)) if head else None

    def _normalize(seq: list, base: float | None) -> list:
        if base is None or base == 0:
            return [None] * len(seq)
        return [round(v / base * 100, 1) if v is not None else None for v in seq]

    own_seq    = _series(own_by_week)
    peer_a_seq = _series(peer_a_by_week)
    peer_b_seq = _series(peer_b_by_week)

    own_norm    = _normalize(own_seq, _baseline(own_seq))
    peer_a_norm = _normalize(peer_a_seq, _baseline(peer_a_seq))
    peer_b_norm = _normalize(peer_b_seq, _baseline(peer_b_seq))

    # Trough: argmin of own's normalized series from week _A1_BASELINE_WEEKS
    # onward (so the merchant's baseline isn't also its trough).
    trough_idx: int | None = None
    trough_val: float | None = None
    for i in range(_A1_BASELINE_WEEKS, len(own_norm)):
        v = own_norm[i]
        if v is None:
            continue
        if trough_val is None or v < trough_val:
            trough_idx = i
            trough_val = v

    if trough_idx is None or trough_val is None:
        return {**empty, "weeks": all_weeks,
                "own": own_norm, "peer_a": peer_a_norm, "peer_b": peer_b_norm,
                "has_peers": any(v is not None for v in peer_a_norm + peer_b_norm)}

    trough_week_iso = all_weeks[trough_idx]
    # Format Monday week-start as e.g. "Apr 27" — display convention
    # the V3_VISION worked example uses.
    trough_week = date.fromisoformat(trough_week_iso).strftime("%b %-d")
    own_pct_drop = max(0, int(round(100 - trough_val)))

    def _peer_drop_around_trough(seq: list) -> int:
        # Look at the same week ± 1 to absorb 1-week phase offsets between
        # own's trough and a peer's minimum (peers may co-decline a week
        # earlier or later).
        idxs = [trough_idx - 1, trough_idx, trough_idx + 1]
        vals = [seq[i] for i in idxs if 0 <= i < len(seq) and seq[i] is not None]
        if not vals:
            return 0
        return max(0, int(round(100 - min(vals))))

    peer_a_pct_drop = _peer_drop_around_trough(peer_a_norm)
    peer_b_pct_drop = _peer_drop_around_trough(peer_b_norm)

    # Market signal: both peers + own all decline ≥20% → market-wide.
    # Own declines and zero peers do → store-specific. Anything else → mixed.
    peers_with_decline = sum(
        1 for d in (peer_a_pct_drop, peer_b_pct_drop) if d >= _A1_DECLINE_PCT
    )
    own_declined = own_pct_drop >= _A1_DECLINE_PCT
    if own_declined and peers_with_decline == 2:
        market_signal = "market-wide"
    elif own_declined and peers_with_decline == 0:
        market_signal = "store-specific"
    else:
        market_signal = "mixed"

    return {
        "weeks":             all_weeks,
        "own":               own_norm,
        "peer_a":            peer_a_norm,
        "peer_b":            peer_b_norm,
        "trough_week":       trough_week,
        "own_pct_drop":      own_pct_drop,
        "peer_a_pct_drop":   peer_a_pct_drop,
        "peer_b_pct_drop":   peer_b_pct_drop,
        "market_signal":     market_signal,
        "has_peers":         any(v is not None for v in peer_a_norm + peer_b_norm),
    }


# ---------------------------------------------------------------------------
# Compatibility shims for placeholders.py and earlier views (no-op now)
# ---------------------------------------------------------------------------

def has_same_segment_peers(merchant_id: str) -> bool:
    """Retained for any caller that still imports it."""
    own_seg = MERCHANT_SEGMENT[merchant_id]
    other_segs = [s for m, s in MERCHANT_SEGMENT.items() if m != merchant_id]
    return own_seg in other_segs
