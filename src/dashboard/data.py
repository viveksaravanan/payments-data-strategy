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
# Set at the noise floor below which week-to-week wobble can produce
# false positives; declines beyond this are visually unambiguous on
# the Pattern 1 chart and match the V3_VISION worked-example narrative.
_A1_DECLINE_PCT     = 15.0


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


@st.cache_data(ttl=3600)
def staple_vs_nonfood_pricing(merchant_id: str) -> dict:
    """Per-tier × per-peer pricing comparison for P2 (Pattern 2 two-panel).

    Returns a dict shaped for ``render_cross_merchant_comparison(mode='two_panel')``:

        panel_a_title:  "Staple categories"
        panel_a_data:   {categories: [...], peer_a_gaps: [...], peer_b_gaps: [...]}
        panel_b_title:  "Non-food categories"
        panel_b_data:   same shape
        staple_pct:     weighted-mean own-vs-peer_a gap on staple tier
                        (float, signed percentage)
        nonfood_pct:    weighted-mean own-vs-peer_a gap on non-food tier
        tier_signal:    "symmetric" | "asymmetric (softer on staples)" |
                        "asymmetric (softer on non-food)"

    Per-cell gap = ``(own_price - peer_price) / peer_price * 100``.
    Cells with peer line count < 5 (k-anon, Phase 1.5) are returned
    as ``None`` so the chart helper omits the bar.
    """
    lake_t = f"lake_transactions_{merchant_id}"
    all_tiers = _P2_TIGHT_CATEGORIES + _P2_LOOSE_CATEGORIES
    placeholders = ",".join("?" * len(all_tiers))

    with _conn() as c:
        own_rows = c.execute(
            f"""
            SELECT p.category,
                   AVG(i.unit_price)    AS mean_price,
                   SUM(i.line_total)    AS revenue
            FROM tenant_transaction_items i
            JOIN tenant_products p     ON p.sku    = i.sku
            JOIN tenant_transactions t ON t.txn_id = i.txn_id
            WHERE t.merchant_id = ?
              AND p.category IN ({placeholders})
            GROUP BY p.category
            """,
            (merchant_id, *all_tiers),
        ).fetchall()

        peer_rows = c.execute(
            f"""
            SELECT peer_id, category,
                   AVG(unit_price)              AS mean_price,
                   COUNT(DISTINCT lake_txn_id)  AS n_txns
            FROM {lake_t}
            WHERE peer_segment = 'grocery'
              AND peer_id IN ('peer_a', 'peer_b')
              AND category IN ({placeholders})
            GROUP BY peer_id, category
            HAVING n_txns >= 5
            """,
            tuple(all_tiers),
        ).fetchall()

    own_by_cat = {cat: {"price": float(p), "rev": float(r)} for cat, p, r in own_rows}
    peer_a_by_cat = {cat: float(p) for pid, cat, p, _ in peer_rows if pid == "peer_a"}
    peer_b_by_cat = {cat: float(p) for pid, cat, p, _ in peer_rows if pid == "peer_b"}

    def _gap(own_p: float, peer_p: float | None) -> float | None:
        if peer_p is None or peer_p == 0:
            return None
        return round((own_p - peer_p) / peer_p * 100, 1)

    def _panel(tier_cats: list[str]) -> dict:
        cats: list[str] = []
        peer_a_gaps: list[float | None] = []
        peer_b_gaps: list[float | None] = []
        for cat in tier_cats:
            if cat not in own_by_cat:
                continue
            cats.append(cat)
            own_p = own_by_cat[cat]["price"]
            peer_a_gaps.append(_gap(own_p, peer_a_by_cat.get(cat)))
            peer_b_gaps.append(_gap(own_p, peer_b_by_cat.get(cat)))
        return {
            "categories":  cats,
            "peer_a_gaps": peer_a_gaps,
            "peer_b_gaps": peer_b_gaps,
        }

    panel_a = _panel(_P2_TIGHT_CATEGORIES)
    panel_b = _panel(_P2_LOOSE_CATEGORIES)

    def _weighted_mean(panel: dict, gaps_key: str) -> float:
        """Revenue-weighted mean of the per-category gaps."""
        num = 0.0
        den = 0.0
        for cat, gap in zip(panel["categories"], panel[gaps_key]):
            if gap is None:
                continue
            w = own_by_cat[cat]["rev"]
            num += gap * w
            den += w
        return round(num / den, 1) if den > 0 else 0.0

    staple_pct  = _weighted_mean(panel_a, "peer_a_gaps")
    nonfood_pct = _weighted_mean(panel_b, "peer_a_gaps")

    diff = nonfood_pct - staple_pct
    if abs(diff) < _P2_TIER_SYMMETRY_PP:
        tier_signal = "symmetric"
    elif diff > 0:
        # Non-food gap is more positive → own is relatively more
        # expensive on non-food → pricing is softer (closer to peer)
        # on staples.
        tier_signal = "asymmetric (softer on staples)"
    else:
        tier_signal = "asymmetric (softer on non-food)"

    return {
        "panel_a_title": "Staple categories",
        "panel_a_data":  panel_a,
        "panel_b_title": "Non-food categories",
        "panel_b_data":  panel_b,
        "staple_pct":    staple_pct,
        "nonfood_pct":   nonfood_pct,
        "tier_signal":   tier_signal,
    }


# ---------------------------------------------------------------------------
# D3 — Basket-mix fingerprint vs peer average (Pattern 2 diverging)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=3600)
def basket_mix_vs_peers(merchant_id: str) -> dict:
    """Per-category own-share vs peer-mean-share, surfaced as a
    diverging bar chart for D3 (Pattern 2 diverging mode).

    For each category:
        own_share       = own category revenue / own total revenue
        peer_mean_share = mean across peer_a, peer_b of that peer's
                          (category revenue / total revenue)
        delta_pp        = own_share - peer_mean_share, in pp

    Returns categories sorted by ``delta_pp`` descending so the chart
    reads "most over-indexed" at top, "most under-indexed" at bottom.
    """
    lake_t = f"lake_transactions_{merchant_id}"

    with _conn() as c:
        own_rows = c.execute(
            """
            SELECT p.category, SUM(i.line_total) AS rev
            FROM tenant_transaction_items i
            JOIN tenant_products p     ON p.sku    = i.sku
            JOIN tenant_transactions t ON t.txn_id = i.txn_id
            WHERE t.merchant_id = ?
            GROUP BY p.category
            """,
            (merchant_id,),
        ).fetchall()

        peer_rows = c.execute(
            f"""
            SELECT peer_id, category, SUM(line_total) AS rev
            FROM {lake_t}
            WHERE peer_segment = 'grocery'
              AND peer_id IN ('peer_a', 'peer_b')
            GROUP BY peer_id, category
            """
        ).fetchall()

    own_total = sum(float(r) for _, r in own_rows) or 1.0
    own_share = {cat: float(r) / own_total * 100 for cat, r in own_rows}

    # Each peer's per-category share is computed against THAT peer's
    # own total revenue (so categories sum to 100% per peer) — then we
    # average the two peer shares.
    peer_totals: dict[str, float] = {}
    peer_cat_rev: dict[tuple[str, str], float] = {}
    for pid, cat, rev in peer_rows:
        rev_f = float(rev)
        peer_totals[pid] = peer_totals.get(pid, 0.0) + rev_f
        peer_cat_rev[(pid, cat)] = rev_f
    peer_share_by_cat: dict[str, float] = {}
    for cat in own_share:
        shares = []
        for pid, total in peer_totals.items():
            if total <= 0:
                continue
            cat_rev = peer_cat_rev.get((pid, cat), 0.0)
            shares.append(cat_rev / total * 100)
        peer_share_by_cat[cat] = (sum(shares) / len(shares)) if shares else 0.0

    deltas = sorted(
        [(cat, round(own_share[cat] - peer_share_by_cat[cat], 1)) for cat in own_share],
        key=lambda r: r[1], reverse=True,
    )
    categories = [c for c, _ in deltas]
    delta_vals = [d for _, d in deltas]

    if not categories:
        return {
            "categories": [], "deltas": [],
            "top_category": "—", "top_pp": 0.0,
            "bottom_category": "—", "bottom_pp": 0.0,
        }

    top_category, top_pp     = deltas[0]
    bottom_category, bottom_pp = deltas[-1]

    return {
        "categories":      categories,
        "deltas":          delta_vals,
        "top_category":    top_category,
        "top_pp":          top_pp,
        "bottom_category": bottom_category,
        "bottom_pp":       bottom_pp,
    }


# ---------------------------------------------------------------------------
# P1 — Category × peer pricing heatmap (Pattern 3 cross-merchant diverging)
# ---------------------------------------------------------------------------

# How many categories to surface on the heatmap. The top 10 by own
# revenue captures the merchant's main basket categories without
# pushing the heatmap height beyond what the chat panel can show.
_P1_TOP_N_CATEGORIES = 10

# Minimum peer line-count per cell. Below this, the cell is suppressed
# per Phase 1.5 k=5 (the lake materialization already filters at the
# row level; we re-enforce at the aggregate cell level to match the
# documented anchor-chart contract from V3_AUDIT.md §1.2).
_P1_MIN_PEER_LINES = 5

# Magnitude threshold below which we treat a gap as parity rather
# than describing it as "above" or "below" in the takeaway.
_P1_PARITY_THRESHOLD = 0.5


@st.cache_data(ttl=3600)
def category_peer_pricing_gaps(merchant_id: str) -> dict:
    """Per-category × per-peer pricing gap matrix for P1
    (Pattern 3 cross_merchant_diverging).

    Rows: top-``_P1_TOP_N_CATEGORIES`` categories by own revenue.
    Cols: ``["peer_a", "peer_b"]`` — the two same-segment grocer
          peers.

    Each cell = ``(own_mean_unit_price - peer_mean_unit_price) /
    peer_mean_unit_price * 100``. Cells where the peer line count
    falls below ``_P1_MIN_PEER_LINES`` are returned as ``None``;
    the chart helper renders these as transparent with an em-dash
    label.

    Returns a dict shaped for ``render_heatmap`` plus the metadata
    the renderer uses to format its takeaway:

        rows, cols, cells:    heatmap inputs
        max_above:            tuple(value, category, peer_label) for
                              the widest positive gap (own > peer)
        max_below:            same shape for the widest negative gap
        n_suppressed:         count of cells suppressed for k<5
    """
    lake_t = f"lake_transactions_{merchant_id}"

    with _conn() as c:
        own_rows = c.execute(
            """
            SELECT p.category,
                   AVG(i.unit_price) AS mean_price,
                   SUM(i.line_total) AS revenue
            FROM tenant_transaction_items i
            JOIN tenant_products p     ON p.sku    = i.sku
            JOIN tenant_transactions t ON t.txn_id = i.txn_id
            WHERE t.merchant_id = ?
            GROUP BY p.category
            """,
            (merchant_id,),
        ).fetchall()

        peer_rows = c.execute(
            f"""
            SELECT peer_id, category,
                   AVG(unit_price)  AS mean_price,
                   COUNT(*)         AS n_lines
            FROM {lake_t}
            WHERE peer_segment = 'grocery'
              AND peer_id IN ('peer_a', 'peer_b')
            GROUP BY peer_id, category
            """,
        ).fetchall()

    if not own_rows:
        return {
            "rows": [], "cols": ["peer_a", "peer_b"], "cells": [],
            "max_above": None, "max_below": None, "n_suppressed": 0,
        }

    # Top-N categories by own revenue.
    own_top = sorted(own_rows, key=lambda r: float(r[2]), reverse=True)
    own_top = own_top[:_P1_TOP_N_CATEGORIES]
    own_by_cat = {r[0]: float(r[1]) for r in own_top}
    rows = [r[0] for r in own_top]

    peer_price = {(pid, cat): float(p) for pid, cat, p, _ in peer_rows}
    peer_n     = {(pid, cat): int(n)   for pid, cat, _, n in peer_rows}

    cols = ["peer_a", "peer_b"]
    cells: list[list[float | None]] = []
    n_suppressed = 0
    for cat in rows:
        own_p = own_by_cat[cat]
        row = []
        for pid in cols:
            n = peer_n.get((pid, cat), 0)
            pp = peer_price.get((pid, cat))
            if pp is None or n < _P1_MIN_PEER_LINES or pp == 0:
                row.append(None)
                if pp is None:
                    n_suppressed += 0  # peer doesn't carry this category
                else:
                    n_suppressed += 1
            else:
                row.append(round((own_p - pp) / pp * 100, 1))
        cells.append(row)

    # Flatten to find the widest positive and widest negative gaps for
    # the takeaway. Ignore suppressed cells.
    flat: list[tuple[float, str, str]] = []
    for i, cat in enumerate(rows):
        for j, pid in enumerate(cols):
            v = cells[i][j]
            if v is not None:
                flat.append((v, cat, pid))

    if not flat:
        max_above = max_below = None
    else:
        max_above = max(flat, key=lambda r: r[0])
        max_below = min(flat, key=lambda r: r[0])

    return {
        "rows":         rows,
        "cols":         cols,
        "cells":        cells,
        "max_above":    max_above,
        "max_below":    max_below,
        "n_suppressed": n_suppressed,
    }


# ---------------------------------------------------------------------------
# P3 — Volume × pricing-gap scatter (Pattern 4)
# ---------------------------------------------------------------------------

# Magnitude below which we treat a category's gap as parity with peers
# rather than calling it "priced above" in the takeaway.
_P3_ABOVE_PARITY_PCT = 0.5


@st.cache_data(ttl=3600)
def category_pricing_leverage(merchant_id: str) -> dict:
    """Per-category x = pricing gap vs peer-average, y = own line
    count, size = own revenue. Data shape for Pattern 4 (P3).

    Peer-average is the simple mean of peer_a's and peer_b's
    per-category mean unit price. Categories where both peers fall
    below the k=5 floor are skipped entirely (no useful comparison).
    """
    lake_t = f"lake_transactions_{merchant_id}"

    with _conn() as c:
        own_rows = c.execute(
            """
            SELECT p.category,
                   AVG(i.unit_price) AS mean_price,
                   COUNT(*)          AS n_lines,
                   SUM(i.line_total) AS revenue
            FROM tenant_transaction_items i
            JOIN tenant_products p     ON p.sku    = i.sku
            JOIN tenant_transactions t ON t.txn_id = i.txn_id
            WHERE t.merchant_id = ?
            GROUP BY p.category
            """,
            (merchant_id,),
        ).fetchall()

        peer_rows = c.execute(
            f"""
            SELECT peer_id, category,
                   AVG(unit_price) AS mean_price,
                   COUNT(*)        AS n_lines
            FROM {lake_t}
            WHERE peer_segment = 'grocery'
              AND peer_id IN ('peer_a', 'peer_b')
            GROUP BY peer_id, category
            HAVING n_lines >= 5
            """,
        ).fetchall()

    # Per-category peer-mean price (mean across whichever peers have
    # data; suppressed cells from k=5 don't enter the mean).
    peer_prices: dict[str, list[float]] = {}
    for _pid, cat, price, _n in peer_rows:
        peer_prices.setdefault(cat, []).append(float(price))
    peer_mean: dict[str, float] = {
        cat: (sum(ps) / len(ps)) for cat, ps in peer_prices.items()
    }

    points: list[dict] = []
    for cat, own_p, n_lines, revenue in own_rows:
        pm = peer_mean.get(cat)
        if pm is None or pm == 0:
            continue
        gap = round((float(own_p) - pm) / pm * 100, 1)
        points.append({
            "label": cat,
            "x":     gap,
            "y":     int(n_lines),
            "size":  float(revenue),
        })
    # Sort by size descending so the largest bubbles draw first and
    # smaller bubbles sit on top of them in the visual stack.
    points.sort(key=lambda p: p["size"], reverse=True)

    # Takeaway metadata: categories priced above peer-mean, sorted by
    # the magnitude of their pricing gap; plus the highest-volume
    # priced-above category (the merchant's lever with the biggest
    # dollar impact).
    above_peer = [p for p in points if p["x"] > _P3_ABOVE_PARITY_PCT]
    above_peer.sort(key=lambda p: p["x"], reverse=True)
    above_peer_names = [p["label"] for p in above_peer[:3]]

    if above_peer:
        top_volume_above = max(above_peer, key=lambda p: p["size"])
        top_volume_category = top_volume_above["label"]
    elif points:
        top_volume_category = max(points, key=lambda p: p["size"])["label"]
    else:
        top_volume_category = "—"

    return {
        "points":              points,
        "x_label":             "Price gap vs peer-avg (%)",
        "y_label":             "Line count (volume)",
        "x_zero_line":         True,
        "y_baseline":          None,
        "show_45_degree_line": False,
        "above_peer_names":    above_peer_names,
        "top_volume_category": top_volume_category,
    }


# ---------------------------------------------------------------------------
# D4 — Own share vs peer-mean share scatter (Pattern 4)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=3600)
def category_share_vs_peer_share(merchant_id: str) -> dict:
    """Per-category x = own share of own revenue, y = peer-mean share
    of peer revenue, size = own absolute revenue. Pattern 4 (D4) with
    a 45° parity line.

    Points off the parity line are the merchant's distinct mix
    positioning. Below the line: own under-indexes (peers carry more
    of that category). Above the line: own over-indexes.
    """
    lake_t = f"lake_transactions_{merchant_id}"

    with _conn() as c:
        own_rows = c.execute(
            """
            SELECT p.category, SUM(i.line_total) AS rev
            FROM tenant_transaction_items i
            JOIN tenant_products p     ON p.sku    = i.sku
            JOIN tenant_transactions t ON t.txn_id = i.txn_id
            WHERE t.merchant_id = ?
            GROUP BY p.category
            """,
            (merchant_id,),
        ).fetchall()
        peer_rows = c.execute(
            f"""
            SELECT peer_id, category, SUM(line_total) AS rev
            FROM {lake_t}
            WHERE peer_segment = 'grocery'
              AND peer_id IN ('peer_a', 'peer_b')
            GROUP BY peer_id, category
            """,
        ).fetchall()

    own_total = sum(float(r) for _, r in own_rows) or 1.0
    own_share = {cat: float(rev) / own_total * 100 for cat, rev in own_rows}
    own_rev   = {cat: float(rev) for cat, rev in own_rows}

    peer_totals: dict[str, float] = {}
    peer_cat_rev: dict[tuple[str, str], float] = {}
    for pid, cat, rev in peer_rows:
        peer_totals[pid] = peer_totals.get(pid, 0.0) + float(rev)
        peer_cat_rev[(pid, cat)] = float(rev)
    peer_share_by_cat: dict[str, float] = {}
    for cat in own_share:
        shares = []
        for pid, total in peer_totals.items():
            if total > 0:
                shares.append(peer_cat_rev.get((pid, cat), 0.0) / total * 100)
        peer_share_by_cat[cat] = (sum(shares) / len(shares)) if shares else 0.0

    points: list[dict] = []
    deltas: list[tuple[str, float]] = []
    for cat, share in own_share.items():
        peer_s = peer_share_by_cat.get(cat, 0.0)
        points.append({
            "label": cat,
            "x":     round(share,  2),
            "y":     round(peer_s, 2),
            "size":  own_rev[cat],
        })
        deltas.append((cat, share - peer_s))
    points.sort(key=lambda p: p["size"], reverse=True)
    deltas.sort(key=lambda r: r[1], reverse=True)

    if deltas:
        over_category,  over_pp  = deltas[0]
        under_category, under_pp = deltas[-1]
    else:
        over_category = under_category = "—"
        over_pp = under_pp = 0.0

    return {
        "points":              points,
        "x_label":             "Your share of revenue (%)",
        "y_label":             "Peer-mean share of revenue (%)",
        "x_zero_line":         False,
        "y_baseline":          None,
        "show_45_degree_line": True,
        "over_category":       over_category,
        "over_pp":             round(over_pp,  1),
        "under_category":      under_category,
        "under_pp":            round(under_pp, 1),
    }


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


@st.cache_data(ttl=3600)
def revenue_gap_decomposition(merchant_id: str) -> dict:
    """Per-peer decomposition of own-vs-peer revenue gap into Stores /
    Traffic-per-store / Basket / Ticket / Mix / Residual driver bars.

    Returns a dict::

        {
            "per_peer": {
                "peer_a": <decomp dict shaped for render_waterfall>,
                "peer_b": <decomp dict shaped for render_waterfall>,
            },
            "has_peers": bool,
        }

    where each per-peer decomp carries the chart inputs plus takeaway
    metadata (``stores_pp``, ``dominant_per_store``, ``dominant_pp``,
    ``tied_with`` — the per-store driver names within ``_D7_PER_STORE_TIE_PP``
    of the leader).

    Empty (``has_peers: False``) for merchants without same-segment
    peers (TBL, TJX).
    """
    import math

    if not has_same_segment_peers(merchant_id):
        return {"per_peer": {}, "has_peers": False}

    lake_t = f"lake_transactions_{merchant_id}"
    lake_s = f"lake_stores_{merchant_id}"

    with _conn() as c:
        own = c.execute(
            """
            SELECT COUNT(DISTINCT t.txn_id) AS n_txns,
                   SUM(i.qty)               AS total_items,
                   SUM(i.line_total)        AS total_revenue,
                   (SELECT COUNT(*) FROM tenant_stores
                     WHERE merchant_id = ?) AS n_stores
            FROM tenant_transactions t
            JOIN tenant_transaction_items i ON i.txn_id = t.txn_id
            WHERE t.merchant_id = ?
            """,
            (merchant_id, merchant_id),
        ).fetchone()

        # ``line_id = 1`` filter recovers true peer transaction counts
        # from the per-line lake table. See chart_patterns.md "lake
        # query gotchas" for the rationale.
        peer_n_rows = c.execute(
            f"""
            SELECT peer_id, COUNT(*) AS n_txns
            FROM {lake_t}
            WHERE peer_segment = 'grocery'
              AND peer_id IN ('peer_a', 'peer_b')
              AND line_id = 1
            GROUP BY peer_id
            """,
        ).fetchall()

        peer_ir_rows = c.execute(
            f"""
            SELECT peer_id,
                   SUM(qty)        AS total_items,
                   SUM(line_total) AS total_revenue
            FROM {lake_t}
            WHERE peer_segment = 'grocery'
              AND peer_id IN ('peer_a', 'peer_b')
            GROUP BY peer_id
            """,
        ).fetchall()

        peer_s_rows = c.execute(
            f"""
            SELECT peer_id, COUNT(*) AS n_stores
            FROM {lake_s}
            WHERE peer_segment = 'grocery'
              AND peer_id IN ('peer_a', 'peer_b')
            GROUP BY peer_id
            """,
        ).fetchall()

    N_own, I_own, R_own, S_own = own
    if not N_own or not I_own or not R_own or not S_own:
        return {"per_peer": {}, "has_peers": False}
    N_own = int(N_own); I_own = float(I_own); R_own = float(R_own); S_own = int(S_own)

    peer_n = {pid: int(n) for pid, n in peer_n_rows}
    peer_i = {pid: float(i) for pid, i, _ in peer_ir_rows}
    peer_r = {pid: float(r) for pid, _, r in peer_ir_rows}
    peer_s = {pid: int(n)   for pid, n in peer_s_rows}

    per_peer: dict[str, dict] = {}
    for pid in ("peer_a", "peer_b"):
        N_p = peer_n.get(pid, 0)
        I_p = peer_i.get(pid, 0.0)
        R_p = peer_r.get(pid, 0.0)
        S_p = peer_s.get(pid, 0)
        if not N_p or not I_p or not R_p or not S_p:
            continue

        log_R  = math.log(R_own / R_p)
        log_S  = math.log(S_own / S_p)
        # Traffic-per-store: divide N by S on each side before taking
        # the ratio. log((N_o/S_o) / (N_p/S_p)).
        log_TS = math.log((N_own / S_own) / (N_p / S_p))
        log_B  = math.log((I_own / N_own) / (I_p / N_p))
        log_P  = math.log((R_own / I_own) / (R_p / I_p))

        stores_pp  = round(log_S  * 100, 1)
        traffic_pp = round(log_TS * 100, 1)
        basket_pp  = round(log_B  * 100, 1)
        ticket_pp  = round(log_P  * 100, 1)
        gap_pct    = round(log_R  * 100, 1)

        drivers = [
            {
                "label":        "Stores",
                "contribution": stores_pp,
                "own":          f"{S_own} stores",
                "peer":         f"{S_p} stores",
            },
            {
                "label":        "Traffic/store",
                "contribution": traffic_pp,
                "own":          f"{N_own / S_own:,.0f} txns/store",
                "peer":         f"{N_p / S_p:,.0f} txns/store",
            },
            {
                "label":        "Basket",
                "contribution": basket_pp,
                "own":          f"{I_own / N_own:.2f} items/txn",
                "peer":         f"{I_p / N_p:.2f} items/txn",
            },
            {
                "label":        "Ticket",
                "contribution": ticket_pp,
                "own":          f"${R_own / I_own:.2f}/item",
                "peer":         f"${R_p / I_p:.2f}/item",
            },
            {
                "label":        "Mix",
                "contribution": 0.0,
                "own":          "(deferred)",
                "peer":         "(deferred)",
            },
            {
                "label":        "Residual",
                "contribution": 0.0,
                "own":          "(deferred)",
                "peer":         "(deferred)",
            },
        ]

        # Per-store dominant driver: largest absolute pp among the
        # three per-store drivers (Stores carved out). If a runner-up
        # is within ``_D7_PER_STORE_TIE_PP`` of the leader, treat as a
        # joint pair in the takeaway (the KRG↔WDX pair sits in this
        # zone — Traffic/store and Basket within 1.2pp).
        per_store = [
            ("Traffic/store", traffic_pp),
            ("Basket",        basket_pp),
            ("Ticket",        ticket_pp),
        ]
        ranked = sorted(per_store, key=lambda d: abs(d[1]), reverse=True)
        dom_name, dom_pp = ranked[0]
        tied = [
            (n, pp) for n, pp in ranked[1:]
            if abs(abs(dom_pp) - abs(pp)) <= _D7_PER_STORE_TIE_PP
        ]

        per_peer[pid] = {
            "drivers":            drivers,
            "total_label":        "Total gap",
            "y_label":            "Contribution to gap (pp)",
            "total_gap_pct":      gap_pct,
            "stores_pp":          stores_pp,
            "dominant_per_store": dom_name,
            "dominant_pp":        dom_pp,
            "tied_with":          tied,
        }

    return {"per_peer": per_peer, "has_peers": bool(per_peer)}


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


@st.cache_data(ttl=3600)
def neighborhood_performance(merchant_id: str) -> dict:
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
    lake_t = f"lake_transactions_{merchant_id}"
    lake_s = f"lake_stores_{merchant_id}"

    own_stores_df = stores_for(merchant_id)

    with _conn() as c:
        # Own per-neighborhood txn count + store count.
        own_rows = c.execute(
            """
            SELECT s.neighborhood,
                   COUNT(DISTINCT t.txn_id) AS n_txns,
                   COUNT(DISTINCT s.store_id) AS n_stores
            FROM tenant_stores s
            LEFT JOIN tenant_transactions t ON t.store_id = s.store_id
            WHERE s.merchant_id = ?
            GROUP BY s.neighborhood
            """,
            (merchant_id,),
        ).fetchall()

        # Peer per-neighborhood txn count (line_id=1 trick recovers
        # true txn count from the per-line lake table — see
        # chart_patterns.md "Implementation gotchas for lake queries").
        peer_txn_rows = []
        peer_store_rows = []
        if has_same_segment_peers(merchant_id):
            peer_txn_rows = c.execute(
                f"""
                SELECT ls.neighborhood, COUNT(*) AS n_txns
                FROM {lake_t} lt
                JOIN {lake_s} ls ON ls.lake_store_id = lt.lake_store_id
                WHERE lt.peer_segment = 'grocery'
                  AND lt.peer_id IN ('peer_a', 'peer_b')
                  AND lt.line_id = 1
                GROUP BY ls.neighborhood
                """,
            ).fetchall()
            peer_store_rows = c.execute(
                f"""
                SELECT neighborhood, COUNT(*) AS n_stores
                FROM {lake_s}
                WHERE peer_segment = 'grocery'
                  AND peer_id IN ('peer_a', 'peer_b')
                GROUP BY neighborhood
                """,
            ).fetchall()

    own_by_nb = {n: (int(t or 0), int(s)) for n, t, s in own_rows}
    peer_txn_by_nb = {n: int(t) for n, t in peer_txn_rows}
    peer_store_by_nb = {n: int(s) for n, s in peer_store_rows}

    total_own_txns = sum(t for t, _ in own_by_nb.values())
    total_own_stores = sum(s for _, s in own_by_nb.values())
    own_baseline = (total_own_txns / total_own_stores) if total_own_stores else 0.0

    total_peer_txns = sum(peer_txn_by_nb.values())
    total_peer_stores = sum(peer_store_by_nb.values())
    peer_baseline = (
        (total_peer_txns / total_peer_stores) if total_peer_stores else 0.0
    )

    neighborhoods = []
    for nb, (n_txns, n_stores) in own_by_nb.items():
        if n_stores == 0 or own_baseline == 0:
            own_delta = None
        else:
            own_per_store = n_txns / n_stores
            own_delta = round((own_per_store / own_baseline - 1) * 100, 1)

        peer_t = peer_txn_by_nb.get(nb, 0)
        peer_s = peer_store_by_nb.get(nb, 0)
        if peer_s == 0 or peer_baseline == 0:
            peer_delta = None
        else:
            peer_delta = round(
                ((peer_t / peer_s) / peer_baseline - 1) * 100, 1
            )

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
            "own_n_txns":     n_txns,
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


@st.cache_data(ttl=3600)
def customer_home_density(merchant_id: str) -> dict:
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
    with _conn() as c:
        rows = c.execute(
            """
            SELECT c.home_zip5, COUNT(DISTINCT c.customer_id) AS n
            FROM tenant_customers c
            WHERE c.customer_id IN (
                SELECT DISTINCT customer_id FROM tenant_transactions
                WHERE merchant_id = ?
            )
            GROUP BY c.home_zip5
            """,
            (merchant_id,),
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

    # Roll zip5 counts up to neighborhoods.
    counts: dict[str, int] = {}
    for z, n in rows:
        nb = _ZIP_TO_NEIGHBORHOOD.get(z)
        if not nb:
            continue
        counts[nb] = counts.get(nb, 0) + int(n)
    own_store_by_nb = {n: int(s) for n, s in own_store_rows}

    total = sum(counts.values()) or 1
    underserved_count = 0
    out: list[dict] = []
    for nb, n in counts.items():
        own_s = own_store_by_nb.get(nb, 0)
        is_under = own_s == 0
        if is_under:
            underserved_count += n
        out.append({
            "name":         nb,
            "n_customers":  n,
            "own_n_stores": own_s,
            "is_underserved": is_under,
        })

    pct_underserved = round(underserved_count / total * 100, 1)

    # Densest under-served = neighborhood with most customers and no own store.
    underserved = sorted(
        [r for r in out if r["is_underserved"]],
        key=lambda r: r["n_customers"],
        reverse=True,
    )
    densest = underserved[0] if underserved else None

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


@st.cache_data(ttl=3600)
def expansion_opportunity(merchant_id: str) -> dict:
    """Per-neighborhood expansion-opportunity score for T4.

    Score = customer_activity_from_neighborhood / (own_store_count + 1)

    Customer activity is the total transaction count of customers who
    home-live in the neighborhood (counted at this merchant only — the
    expansion question is about *this merchant's* customer base
    travelling outside their home neighborhood, not about all panel
    customers). Higher score → more demand-side activity relative to
    own supply-side footprint → expansion candidate.

    Peer store counts (from the lake, same-segment only) are surfaced
    as a secondary signal: a high-score neighborhood with many peer
    stores is "peer-dense" (competitive expansion); few peer stores is
    "peers under-represented" (open market).

    Returns top-N neighborhoods plus an under-/over-represented
    classification for the takeaway.
    """
    lake_s = f"lake_stores_{merchant_id}"

    with _conn() as c:
        cust_rows = c.execute(
            """
            SELECT c.home_zip5, COUNT(t.txn_id) AS n_txns
            FROM tenant_customers c
            JOIN tenant_transactions t ON t.customer_id = c.customer_id
            WHERE t.merchant_id = ?
            GROUP BY c.home_zip5
            """,
            (merchant_id,),
        ).fetchall()
        own_store_rows = c.execute(
            """
            SELECT neighborhood, COUNT(*) AS n
            FROM tenant_stores WHERE merchant_id = ?
            GROUP BY neighborhood
            """,
            (merchant_id,),
        ).fetchall()
        peer_store_rows = []
        if has_same_segment_peers(merchant_id):
            peer_store_rows = c.execute(
                f"""
                SELECT neighborhood, COUNT(*) AS n
                FROM {lake_s}
                WHERE peer_segment = 'grocery'
                  AND peer_id IN ('peer_a', 'peer_b')
                GROUP BY neighborhood
                """,
            ).fetchall()

    # Roll customer txn activity to neighborhoods.
    activity: dict[str, int] = {}
    for z, n in cust_rows:
        nb = _ZIP_TO_NEIGHBORHOOD.get(z)
        if not nb:
            continue
        activity[nb] = activity.get(nb, 0) + int(n)
    own_store_by_nb = {n: int(s) for n, s in own_store_rows}
    peer_store_by_nb = {n: int(s) for n, s in peer_store_rows}

    # Score every neighborhood we have any customer-activity for.
    scored = []
    for nb, n_txns in activity.items():
        own_s  = own_store_by_nb.get(nb, 0)
        peer_s = peer_store_by_nb.get(nb, 0)
        score  = n_txns / (own_s + 1)
        scored.append({
            "name":         nb,
            "score":        round(score, 1),
            "n_txns":       n_txns,
            "own_n_stores": own_s,
            "peer_n_stores": peer_s,
        })
    scored.sort(key=lambda r: r["score"], reverse=True)

    top = scored[0] if scored else None
    if top:
        # Peer-density classification: compare top's peer store count
        # to the panel-wide peer-store mean across neighborhoods that
        # have at least one peer store. Two grocers × ~22 stores ÷ 12
        # neighborhoods ≈ 3.7 peer stores/neighborhood; use 3 as the
        # heuristic threshold.
        peer_signal = (
            "peer-dense" if top["peer_n_stores"] >= 3
            else "peers under-represented"
        )
    else:
        peer_signal = "—"

    own_stores_df = stores_for(merchant_id)
    own_markers = [
        {
            "lat": float(r.latitude),
            "lon": float(r.longitude),
            "tooltip": f"<b>{r.store_id}</b><br>{r.neighborhood}",
        }
        for r in own_stores_df.itertuples()
    ]

    # Peer markers: the lake doesn't expose per-store coords, so place
    # one circle per peer store at the neighborhood centroid plus
    # deterministic jitter. Distinct shape (hollow gray) communicates
    # "peer" at a glance.
    import hashlib
    peer_markers = []
    for nb, count in peer_store_by_nb.items():
        # Re-import the centroid table from chart_patterns (avoids
        # duplicating the ZIP centroid set in data.py).
        from . import chart_patterns as CP
        c0 = CP.neighborhood_centroid(nb)
        if c0 is None:
            continue
        for k in range(count):
            # Deterministic jitter from name+index so the same call
            # site produces the same coords across reruns.
            seed = int(hashlib.sha256(f"{nb}-{k}".encode()).hexdigest()[:8], 16)
            dlat = ((seed & 0xFFFF) / 0xFFFF - 0.5) * 0.02
            dlon = (((seed >> 16) & 0xFFFF) / 0xFFFF - 0.5) * 0.02
            peer_markers.append({
                "lat": c0[0] + dlat,
                "lon": c0[1] + dlon,
                "tooltip": f"<b>Peer store</b><br>{nb}",
            })

    return {
        "neighborhoods":     scored,
        "top":               top,
        "top_peer_signal":   peer_signal,
        "own_markers":       own_markers,
        "peer_markers":      peer_markers,
        "footnote":          CP.CUSTOMER_COVERAGE_FOOTNOTE,
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
# week binning (``DATE(t.txn_ts, 'weekday 0', '-6 days')`` rounds each
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
    if not has_same_segment_peers(merchant_id):
        return {}
    lake_t = f"lake_transactions_{merchant_id}"
    lake_s = f"lake_stores_{merchant_id}"

    rows = conn.execute(
        f"""
        WITH peer_weekly AS (
            SELECT ls.neighborhood,
                   DATE(lt.txn_date, 'weekday 0', '-6 days') AS week,
                   COUNT(*) AS n_txns
            FROM {lake_t} lt
            JOIN {lake_s} ls ON ls.lake_store_id = lt.lake_store_id
            WHERE lt.peer_segment = 'grocery'
              AND lt.peer_id IN ('peer_a', 'peer_b')
              AND lt.line_id = 1
              AND DATE(lt.txn_date, 'weekday 0', '-6 days')
                  BETWEEN ? AND ?
            GROUP BY ls.neighborhood, week
        ),
        peer_store_counts AS (
            SELECT neighborhood, COUNT(*) AS n_stores
            FROM {lake_s}
            WHERE peer_segment = 'grocery'
              AND peer_id IN ('peer_a', 'peer_b')
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


@st.cache_data(ttl=3600)
def store_anomalies(merchant_id: str) -> dict:
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
    with _conn() as c:
        own_rows = c.execute(
            """
            WITH weekly AS (
                SELECT t.store_id,
                       DATE(t.txn_ts, 'weekday 0', '-6 days') AS week,
                       COUNT(DISTINCT t.txn_id) AS n_txns
                FROM tenant_transactions t
                WHERE t.merchant_id = ?
                  AND DATE(t.txn_ts, 'weekday 0', '-6 days')
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
            WHERE s.merchant_id = ?
            GROUP BY s.store_id, s.neighborhood
            """,
            (
                merchant_id,
                _A_BASELINE_WEEK_START, _A_RECENT_WEEK_START,
                _A_RECENT_WEEK_START,
                _A_BASELINE_WEEK_START, _A_BASELINE_WEEK_END,
                merchant_id,
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


@st.cache_data(ttl=3600)
def category_anomalies(merchant_id: str) -> dict:
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
    lake_t = f"lake_transactions_{merchant_id}"

    with _conn() as c:
        own_rows = c.execute(
            """
            WITH weekly AS (
                SELECT p.category,
                       DATE(t.txn_ts, 'weekday 0', '-6 days') AS week,
                       COUNT(*) AS n_lines
                FROM tenant_transaction_items i
                JOIN tenant_products p     ON p.sku    = i.sku
                JOIN tenant_transactions t ON t.txn_id = i.txn_id
                WHERE t.merchant_id = ?
                  AND DATE(t.txn_ts, 'weekday 0', '-6 days')
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
                merchant_id,
                _A_BASELINE_WEEK_START, _A_RECENT_WEEK_START,
                _A_RECENT_WEEK_START,
                _A_BASELINE_WEEK_START, _A_BASELINE_WEEK_END,
            ),
        ).fetchall()

        peer_rows = []
        if has_same_segment_peers(merchant_id):
            peer_rows = c.execute(
                f"""
                WITH weekly AS (
                    SELECT category,
                           DATE(txn_date, 'weekday 0', '-6 days') AS week,
                           COUNT(*) AS n_lines
                    FROM {lake_t}
                    WHERE peer_segment = 'grocery'
                      AND peer_id IN ('peer_a', 'peer_b')
                      AND DATE(txn_date, 'weekday 0', '-6 days')
                          BETWEEN ? AND ?
                    GROUP BY category, week
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


@st.cache_data(ttl=3600)
def kpi_strip(merchant_id: str) -> dict:
    """Five-card KPI strip data — Revenue / Transactions / Avg basket /
    Unique customers / Anomaly count, each with a delta vs the prior
    4-week average and a 12-week trailing sparkline.

    Returns::

        {
            "revenue":          {"value": float, "delta_pct": float,
                                 "sparkline": list[float]},
            "transactions":     {...},
            "avg_basket":       {...},
            "unique_customers": {...},
            "anomaly":          {"value": int, "n_stores": int,
                                 "n_categories": int,
                                 "sparkline": list[int]},
        }
    """
    weeks = [
        "2026-03-02", "2026-03-09", "2026-03-16", "2026-03-23",
        "2026-03-30", "2026-04-06", "2026-04-13", "2026-04-20",
        "2026-04-27", "2026-05-04", "2026-05-11", "2026-05-18",
    ]
    week_lo, week_hi = weeks[0], weeks[-1]
    recent = weeks[-1]
    prior_lo, prior_hi = weeks[-5], weeks[-2]  # 4 weeks prior to recent

    with _conn() as c:
        # Per-week aggregates for the 12-week sparkline.
        rows = c.execute(
            """
            SELECT DATE(t.txn_ts, 'weekday 0', '-6 days') AS week,
                   SUM(t.txn_total)              AS revenue,
                   COUNT(DISTINCT t.txn_id)      AS n_txns,
                   COUNT(DISTINCT t.customer_id) AS n_customers
            FROM tenant_transactions t
            WHERE t.merchant_id = ?
              AND DATE(t.txn_ts, 'weekday 0', '-6 days')
                  BETWEEN ? AND ?
            GROUP BY week
            """,
            (merchant_id, week_lo, week_hi),
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

    def _delta(recent_val: float, prior_vals: list[float]) -> float:
        prior_mean = sum(prior_vals) / len(prior_vals) if prior_vals else 0.0
        if prior_mean <= 0:
            return 0.0
        return round((recent_val / prior_mean - 1) * 100, 1)

    prior_idx = slice(-5, -1)  # 4 weeks prior to recent
    rev_recent  = rev_series[-1];  rev_delta  = _delta(rev_recent,  rev_series[prior_idx])
    txn_recent  = txn_series[-1];  txn_delta  = _delta(txn_recent,  txn_series[prior_idx])
    cust_recent = cust_series[-1]; cust_delta = _delta(cust_recent, cust_series[prior_idx])
    bas_recent  = basket_series[-1]; bas_delta = _delta(bas_recent,  basket_series[prior_idx])

    # Anomaly count = stores + categories deviating >15% from
    # ``first-4w`` baseline (matches A2/A3 conventions). Computed for
    # every week so the sparkline shows the trailing-12-week anomaly
    # trajectory rather than a single point.
    anomaly_series = _anomaly_count_series(merchant_id, weeks)
    anomaly_recent = anomaly_series[-1]
    anomaly_breakdown = _anomaly_count_breakdown(merchant_id)

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
            "value":        anomaly_recent,
            "n_stores":     anomaly_breakdown["n_stores"],
            "n_categories": anomaly_breakdown["n_categories"],
            "sparkline":    anomaly_series,
        },
    }


def _anomaly_count_series(merchant_id: str, weeks: list[str]) -> list[int]:
    """Per-week anomaly count: number of stores + categories whose
    weekly transactions / line-count deviate from the merchant's
    first-4-week baseline by >15 %. Returns a list aligned to
    ``weeks`` (one int per week)."""
    week_lo, week_hi = weeks[0], weeks[-1]
    baseline_lo = "2026-03-02"
    baseline_hi = "2026-03-23"

    with _conn() as c:
        # Per (store, week) txn counts for the full window.
        store_rows = c.execute(
            """
            SELECT t.store_id,
                   DATE(t.txn_ts, 'weekday 0', '-6 days') AS week,
                   COUNT(DISTINCT t.txn_id) AS n_txns
            FROM tenant_transactions t
            WHERE t.merchant_id = ?
              AND DATE(t.txn_ts, 'weekday 0', '-6 days') BETWEEN ? AND ?
            GROUP BY t.store_id, week
            """,
            (merchant_id, week_lo, week_hi),
        ).fetchall()
        cat_rows = c.execute(
            """
            SELECT p.category,
                   DATE(t.txn_ts, 'weekday 0', '-6 days') AS week,
                   COUNT(*) AS n_lines
            FROM tenant_transaction_items i
            JOIN tenant_products p     ON p.sku    = i.sku
            JOIN tenant_transactions t ON t.txn_id = i.txn_id
            WHERE t.merchant_id = ?
              AND DATE(t.txn_ts, 'weekday 0', '-6 days') BETWEEN ? AND ?
            GROUP BY p.category, week
            """,
            (merchant_id, week_lo, week_hi),
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

    out: list[int] = []
    for wk in weeks:
        n_anom = 0
        for sid, base_list in store_baselines.items():
            base = sum(base_list) / 4 if base_list else 0
            if base <= 0:
                continue
            n = store_by_wk.get(sid, {}).get(wk, 0)
            if abs(n / base - 1) >= 0.15:
                n_anom += 1
        for cat, base_list in cat_baselines.items():
            base = sum(base_list) / 4 if base_list else 0
            if base <= 0:
                continue
            n = cat_by_wk.get(cat, {}).get(wk, 0)
            if abs(n / base - 1) >= 0.15:
                n_anom += 1
        out.append(n_anom)
    return out


def _anomaly_count_breakdown(merchant_id: str) -> dict:
    """Split this week's anomaly count into store-side and category-
    side contributions — used by the KPI hint subtitle."""
    s = store_anomalies_own_only(merchant_id)
    cat = category_anomalies(merchant_id)
    return {
        "n_stores":     s["n_flagged"],
        "n_categories": cat["n_flagged"],
    }


# ---------------------------------------------------------------------------
# Phase 4.3 — TBL / TJX question data (own-only)
# ---------------------------------------------------------------------------
#
# TBL (QSR) and TJX (off-price retail) have no same-segment peers in
# the panel, so their pricing / anomaly / demand questions are all
# tenant-only. Recent-vs-baseline questions share the A2/A3 baseline
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

_DAY_OF_WEEK_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_DOW_FROM_SQLITE = {
    "1": "Mon", "2": "Tue", "3": "Wed", "4": "Thu",
    "5": "Fri", "6": "Sat", "0": "Sun",
}


def _full_weeks(merchant_id: str) -> list[str]:
    """Return the 12 full Mon-Sun week-start ISO dates that the
    A1/A2/A3 anomaly questions also use."""
    return [
        "2026-03-02", "2026-03-09", "2026-03-16", "2026-03-23",
        "2026-03-30", "2026-04-06", "2026-04-13", "2026-04-20",
        "2026-04-27", "2026-05-04", "2026-05-11", "2026-05-18",
    ]


# ---------------------------------------------------------------------------
# T-P1 — Daypart × week mean ticket trends
# ---------------------------------------------------------------------------

@st.cache_data(ttl=3600)
def tbl_daypart_ticket_trends(merchant_id: str) -> dict:
    """Weekly mean transaction value per QSR daypart over the 12-week
    full-weeks window. Pattern 1 own-multi shape.

    Returns ``{weeks, series, y_label, top_daypart, bottom_daypart,
    top_pct, bottom_pct, top_direction, bottom_direction}`` — the
    bottom-of-card takeaway names the daypart with the largest ticket
    drift (positive or negative).
    """
    weeks = _full_weeks(merchant_id)
    week_lo = weeks[0]
    week_hi = weeks[-1]

    with _conn() as c:
        rows = c.execute(
            """
            SELECT DATE(t.txn_ts, 'weekday 0', '-6 days') AS week,
                   CAST(SUBSTR(t.txn_ts, 12, 2) AS INTEGER) AS hr,
                   AVG(t.txn_total) AS mean_ticket
            FROM tenant_transactions t
            WHERE t.merchant_id = ?
              AND DATE(t.txn_ts, 'weekday 0', '-6 days')
                  BETWEEN ? AND ?
            GROUP BY week, hr
            """,
            (merchant_id, week_lo, week_hi),
        ).fetchall()

    # Aggregate hours into dayparts. Per (week, daypart), simple mean
    # of the hour-level means weighted by row count isn't available
    # without a count column — but for the demo, mean-of-means is
    # close enough at hour granularity within a daypart band.
    by_wd: dict[tuple[str, str], list[float]] = {}
    for week, hr, mean_ticket in rows:
        dp = _QSR_HOUR_TO_DAYPART.get(int(hr))
        if dp is None:
            continue
        by_wd.setdefault((week, dp), []).append(float(mean_ticket))
    series_values: dict[str, list[float | None]] = {
        dp: [] for dp in _QSR_DAYPART_ORDER
    }
    for w in weeks:
        for dp in _QSR_DAYPART_ORDER:
            vals = by_wd.get((w, dp))
            series_values[dp].append(
                round(sum(vals) / len(vals), 2) if vals else None
            )

    # Daypart-level drift = (last_known - first_known) / first_known.
    # Use the first and last non-None values per series for robustness.
    drifts: list[tuple[str, float]] = []
    for dp, vals in series_values.items():
        nv = [v for v in vals if v is not None]
        if len(nv) >= 2 and nv[0] > 0:
            drifts.append((dp, round((nv[-1] / nv[0] - 1) * 100, 1)))
    drifts.sort(key=lambda d: abs(d[1]), reverse=True)
    top_dp, top_pct = drifts[0] if drifts else ("—", 0.0)
    if len(drifts) > 1:
        bottom_dp, bottom_pct = drifts[1]
    else:
        bottom_dp, bottom_pct = ("—", 0.0)

    def _direction(pct: float) -> str:
        return "up" if pct > 0 else ("down" if pct < 0 else "flat")

    return {
        "weeks":  weeks,
        "series": [
            {"name": dp, "values": series_values[dp]}
            for dp in _QSR_DAYPART_ORDER
        ],
        "y_label":         "Mean ticket ($)",
        "top_daypart":     top_dp,
        "top_pct":         top_pct,
        "top_direction":   _direction(top_pct),
        "bottom_daypart":  bottom_dp,
        "bottom_pct":      bottom_pct,
        "bottom_direction":_direction(bottom_pct),
    }


# ---------------------------------------------------------------------------
# T-P2 — Per-category weekly mean unit price
# ---------------------------------------------------------------------------

@st.cache_data(ttl=3600)
def category_unit_price_trends(merchant_id: str, top_n: int = 6) -> dict:
    """Weekly mean unit price per top-revenue category. Pattern 1
    own-multi. Used by T-P2 (TBL) and R-P1 (TJX, similar shape but
    framed as "ticket trends across categories"). Returns top
    ``top_n`` categories by total revenue across the 12-week window.
    """
    weeks = _full_weeks(merchant_id)
    week_lo = weeks[0]
    week_hi = weeks[-1]

    with _conn() as c:
        cat_rows = c.execute(
            """
            SELECT p.category, SUM(i.line_total) AS rev
            FROM tenant_transaction_items i
            JOIN tenant_products p     ON p.sku    = i.sku
            JOIN tenant_transactions t ON t.txn_id = i.txn_id
            WHERE t.merchant_id = ?
              AND DATE(t.txn_ts, 'weekday 0', '-6 days')
                  BETWEEN ? AND ?
            GROUP BY p.category
            ORDER BY rev DESC
            LIMIT ?
            """,
            (merchant_id, week_lo, week_hi, int(top_n)),
        ).fetchall()
        top_cats = [r[0] for r in cat_rows]
        if not top_cats:
            return {"weeks": [], "series": [], "y_label": "Mean unit price ($)",
                    "top_category": "—", "top_pct": 0.0, "top_direction": "flat",
                    "next_category": "—", "next_pct": 0.0, "next_direction": "flat"}

        ph = ",".join("?" for _ in top_cats)
        price_rows = c.execute(
            f"""
            SELECT DATE(t.txn_ts, 'weekday 0', '-6 days') AS week,
                   p.category,
                   AVG(i.unit_price) AS mean_price
            FROM tenant_transaction_items i
            JOIN tenant_products p     ON p.sku    = i.sku
            JOIN tenant_transactions t ON t.txn_id = i.txn_id
            WHERE t.merchant_id = ?
              AND p.category IN ({ph})
              AND DATE(t.txn_ts, 'weekday 0', '-6 days') BETWEEN ? AND ?
            GROUP BY week, p.category
            """,
            [merchant_id, *top_cats, week_lo, week_hi],
        ).fetchall()

    by_wc: dict[tuple[str, str], float] = {
        (w, c_): round(float(p), 3) for w, c_, p in price_rows
    }
    series_values: dict[str, list[float | None]] = {c_: [] for c_ in top_cats}
    for w in weeks:
        for c_ in top_cats:
            series_values[c_].append(by_wc.get((w, c_)))

    drifts: list[tuple[str, float]] = []
    for c_, vals in series_values.items():
        nv = [v for v in vals if v is not None]
        if len(nv) >= 2 and nv[0] > 0:
            drifts.append((c_, round((nv[-1] / nv[0] - 1) * 100, 1)))
    drifts.sort(key=lambda d: abs(d[1]), reverse=True)
    top_c, top_pct = drifts[0] if drifts else ("—", 0.0)
    next_c, next_pct = drifts[1] if len(drifts) > 1 else ("—", 0.0)

    def _dir(p: float) -> str:
        return "up" if p > 0 else ("down" if p < 0 else "flat")

    return {
        "weeks":  weeks,
        "series": [{"name": c_, "values": series_values[c_]} for c_ in top_cats],
        "y_label":         "Mean unit price ($)",
        "top_category":    top_c,
        "top_pct":         top_pct,
        "top_direction":   _dir(top_pct),
        "next_category":   next_c,
        "next_pct":        next_pct,
        "next_direction":  _dir(next_pct),
    }


# ---------------------------------------------------------------------------
# T-P3 — Per-store mean ticket distribution
# ---------------------------------------------------------------------------

@st.cache_data(ttl=3600)
def per_store_mean_ticket(merchant_id: str) -> dict:
    """Per-store mean transaction value over the 12-week window with a
    chain-mean reference line and >1σ outlier highlighting. Pattern 2
    own-only-bars shape. Used by T-P3.
    """
    with _conn() as c:
        rows = c.execute(
            """
            SELECT t.store_id, s.neighborhood,
                   AVG(t.txn_total) AS mean_ticket,
                   COUNT(*)         AS n_txns
            FROM tenant_transactions t
            JOIN tenant_stores s ON s.store_id = t.store_id
            WHERE t.merchant_id = ?
            GROUP BY t.store_id, s.neighborhood
            ORDER BY mean_ticket DESC
            """,
            (merchant_id,),
        ).fetchall()

    if not rows:
        return {
            "labels": [], "values": [], "ref_line": 0.0, "ref_label": "",
            "highlight": [], "x_label": "Mean ticket ($)",
            "top_store": "—", "top_value": 0.0,
            "bottom_store": "—", "bottom_value": 0.0,
            "range_value": 0.0,
        }

    vals = [float(r[2]) for r in rows]
    mean = sum(vals) / len(vals)
    var  = sum((v - mean) ** 2 for v in vals) / len(vals)
    std  = var ** 0.5

    labels = []
    values = []
    highlight = []
    hover_lines = []
    for store_id, nb, mt, n in rows:
        labels.append(f"{store_id}")
        values.append(round(float(mt), 2))
        highlight.append(abs(float(mt) - mean) > std)
        hover_lines.append(
            f"<b>{store_id}</b><br>{nb}<br>"
            f"Mean ticket: ${float(mt):.2f}<br>"
            f"{int(n):,} txns"
        )

    top_store = rows[0][0]; top_value = float(rows[0][2])
    bottom_store = rows[-1][0]; bottom_value = float(rows[-1][2])
    return {
        "labels":    labels,
        "values":    values,
        "x_label":   "Mean ticket ($)",
        "ref_line":  round(mean, 2),
        "ref_label": f"Chain mean ${mean:.2f}",
        "highlight": highlight,
        "value_format": "$,.2f",
        "hover_template": "%{customdata}<extra></extra>",
        "customdata": hover_lines,
        "top_store":    top_store,
        "top_value":    round(top_value, 2),
        "bottom_store": bottom_store,
        "bottom_value": round(bottom_value, 2),
        "range_value":  round(top_value - bottom_value, 2),
    }


# ---------------------------------------------------------------------------
# T-A1 / R-A1 — Per-store recent-vs-baseline (no peer column)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=3600)
def store_anomalies_own_only(merchant_id: str) -> dict:
    """Per-store deviation table for merchants without same-segment
    peers. Same first-4w baseline as A2; no peer-neighborhood column.
    """
    with _conn() as c:
        own_rows = c.execute(
            """
            WITH weekly AS (
                SELECT t.store_id,
                       DATE(t.txn_ts, 'weekday 0', '-6 days') AS week,
                       COUNT(DISTINCT t.txn_id) AS n_txns
                FROM tenant_transactions t
                WHERE t.merchant_id = ?
                  AND DATE(t.txn_ts, 'weekday 0', '-6 days')
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
            WHERE s.merchant_id = ?
            GROUP BY s.store_id, s.neighborhood
            """,
            (
                merchant_id,
                _A_BASELINE_WEEK_START, _A_RECENT_WEEK_START,
                _A_RECENT_WEEK_START,
                _A_BASELINE_WEEK_START, _A_BASELINE_WEEK_END,
                merchant_id,
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

@st.cache_data(ttl=3600)
def sku_anomalies(merchant_id: str, top_n: int = 25) -> dict:
    """Per-SKU recent-vs-baseline volume deviation. Returns the top
    ``top_n`` by absolute deviation so the table stays scannable in
    the 35 % chat panel (TBL has ~60 SKUs total).
    """
    with _conn() as c:
        rows = c.execute(
            """
            WITH weekly AS (
                SELECT p.sku, p.name AS sku_name, p.category,
                       DATE(t.txn_ts, 'weekday 0', '-6 days') AS week,
                       COUNT(*) AS n_lines
                FROM tenant_transaction_items i
                JOIN tenant_products p     ON p.sku    = i.sku
                JOIN tenant_transactions t ON t.txn_id = i.txn_id
                WHERE t.merchant_id = ?
                  AND DATE(t.txn_ts, 'weekday 0', '-6 days')
                      BETWEEN ? AND ?
                GROUP BY p.sku, week
            )
            SELECT sku, sku_name, category,
                   SUM(CASE WHEN week = ? THEN n_lines ELSE 0 END) AS recent,
                   SUM(CASE WHEN week BETWEEN ? AND ? THEN n_lines ELSE 0 END)
                        * 1.0 / 4 AS baseline
            FROM weekly
            GROUP BY sku, sku_name, category
            """,
            (
                merchant_id,
                _A_BASELINE_WEEK_START, _A_RECENT_WEEK_START,
                _A_RECENT_WEEK_START,
                _A_BASELINE_WEEK_START, _A_BASELINE_WEEK_END,
            ),
        ).fetchall()

    out: list[dict] = []
    for sku, name, cat, recent, baseline in rows:
        baseline_f = float(baseline or 0)
        recent_f   = float(recent or 0)
        if baseline_f <= 0 and recent_f <= 0:
            continue
        if baseline_f <= 0:
            # New SKU (or zero baseline) — surface but don't compute %
            dev = None
            flag = recent_f > 0
        else:
            dev = round((recent_f / baseline_f - 1) * 100, 1)
            flag = abs(dev) >= _A_DEVIATION_THRESHOLD
        out.append({
            "sku":           sku,
            "sku_name":      name,
            "category":      cat,
            "baseline":      round(baseline_f, 1),
            "recent":        int(recent_f),
            "deviation_pct": dev,
            "flag":          flag,
        })

    out.sort(key=lambda r: abs(r["deviation_pct"] or 0), reverse=True)
    out = out[:top_n]

    n_flagged = sum(1 for r in out if r["flag"])
    spikes = [r for r in out if r["deviation_pct"] and r["deviation_pct"] > 0]
    drops  = [r for r in out if r["deviation_pct"] and r["deviation_pct"] < 0]
    return {
        "rows":      out,
        "n_flagged": n_flagged,
        "top_spike": spikes[0] if spikes else None,
        "top_drop":  drops[0]  if drops  else None,
    }


# ---------------------------------------------------------------------------
# T-A3 — Day-of-week × daypart heatmap (ratios)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=3600)
def day_daypart_heatmap(merchant_id: str) -> dict:
    """Day-of-week × QSR-daypart recent-vs-baseline ratio cells. Cells
    are ``recent_count / baseline_avg`` (1.0 = on baseline). Cells
    with fewer than 5 transactions in the recent window are returned
    as ``None`` (per-cell suppression, consistent with the k=5
    convention applied elsewhere)."""
    with _conn() as c:
        rows = c.execute(
            """
            SELECT DATE(t.txn_ts, 'weekday 0', '-6 days') AS week,
                   STRFTIME('%w', t.txn_ts)               AS dow_num,
                   CAST(SUBSTR(t.txn_ts, 12, 2) AS INTEGER) AS hr,
                   COUNT(DISTINCT t.txn_id)               AS n_txns
            FROM tenant_transactions t
            WHERE t.merchant_id = ?
              AND DATE(t.txn_ts, 'weekday 0', '-6 days')
                  BETWEEN ? AND ?
            GROUP BY week, dow_num, hr
            """,
            (
                merchant_id,
                _A_BASELINE_WEEK_START, _A_RECENT_WEEK_START,
            ),
        ).fetchall()

    recent_by_cell: dict[tuple[str, str], int] = {}
    baseline_by_cell: dict[tuple[str, str], list[int]] = {}
    for week, dow_num, hr, n in rows:
        dow = _DOW_FROM_SQLITE.get(dow_num)
        dp  = _QSR_HOUR_TO_DAYPART.get(int(hr))
        if not dow or not dp:
            continue
        key = (dow, dp)
        if week == _A_RECENT_WEEK_START:
            recent_by_cell[key] = recent_by_cell.get(key, 0) + int(n)
        elif _A_BASELINE_WEEK_START <= week <= _A_BASELINE_WEEK_END:
            baseline_by_cell.setdefault(key, []).append(int(n))

    cells: list[list[float | None]] = []
    cells_meta: list[list[tuple[int, float] | None]] = []
    for dow in _DAY_OF_WEEK_ORDER:
        row_vals = []
        row_meta = []
        for dp in _QSR_DAYPART_ORDER:
            recent = recent_by_cell.get((dow, dp), 0)
            base_list = baseline_by_cell.get((dow, dp), [])
            base_avg = (sum(base_list) / 4) if base_list else 0.0
            if recent < 5 or base_avg <= 0:
                row_vals.append(None)
                row_meta.append(None)
            else:
                ratio = recent / base_avg
                row_vals.append(round(ratio, 3))
                row_meta.append((recent, round(base_avg, 1)))
        cells.append(row_vals)
        cells_meta.append(row_meta)

    # Identify weakest + strongest cells
    flat: list[tuple[float, str, str]] = []
    for i, dow in enumerate(_DAY_OF_WEEK_ORDER):
        for j, dp in enumerate(_QSR_DAYPART_ORDER):
            v = cells[i][j]
            if v is not None:
                flat.append((v, dow, dp))
    if flat:
        weakest = min(flat, key=lambda r: r[0])
        strongest = max(flat, key=lambda r: r[0])
    else:
        weakest = strongest = None

    return {
        "rows":  _DAY_OF_WEEK_ORDER,
        "cols":  _QSR_DAYPART_ORDER,
        "cells": cells,
        "weakest":   weakest,
        "strongest": strongest,
    }


# ---------------------------------------------------------------------------
# T-D1 / R-D1 — Category share bars
# ---------------------------------------------------------------------------

@st.cache_data(ttl=3600)
def category_share_own(merchant_id: str, top_n: int = 8) -> dict:
    """Per-category share of own revenue. Top ``top_n`` categories;
    smaller categories rolled into "Other". Pattern 2 own-only-bars.
    """
    with _conn() as c:
        rows = c.execute(
            """
            SELECT p.category, SUM(i.line_total) AS rev
            FROM tenant_transaction_items i
            JOIN tenant_products p     ON p.sku    = i.sku
            JOIN tenant_transactions t ON t.txn_id = i.txn_id
            WHERE t.merchant_id = ?
            GROUP BY p.category
            ORDER BY rev DESC
            """,
            (merchant_id,),
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
# T-D2 / R-D2 — Category share trajectory
# ---------------------------------------------------------------------------

@st.cache_data(ttl=3600)
def category_share_trajectory(merchant_id: str, top_n: int = 6) -> dict:
    """Weekly per-category share of own revenue over the 12-week
    window. Pattern 1 own-multi. Highlights the most-rising and
    most-falling categories for the takeaway.
    """
    weeks = _full_weeks(merchant_id)
    week_lo = weeks[0]; week_hi = weeks[-1]

    with _conn() as c:
        cat_rows = c.execute(
            """
            SELECT p.category, SUM(i.line_total) AS rev
            FROM tenant_transaction_items i
            JOIN tenant_products p     ON p.sku    = i.sku
            JOIN tenant_transactions t ON t.txn_id = i.txn_id
            WHERE t.merchant_id = ?
            GROUP BY p.category
            ORDER BY rev DESC
            LIMIT ?
            """,
            (merchant_id, int(top_n)),
        ).fetchall()
        top_cats = [r[0] for r in cat_rows]
        if not top_cats:
            return {"weeks": [], "series": [], "y_label": "Share of revenue (%)",
                    "growing_category": "—", "growing_pp": 0.0,
                    "declining_category": "—", "declining_pp": 0.0}
        ph = ",".join("?" for _ in top_cats)
        wk_rows = c.execute(
            f"""
            SELECT DATE(t.txn_ts, 'weekday 0', '-6 days') AS week,
                   p.category,
                   SUM(i.line_total) AS rev
            FROM tenant_transaction_items i
            JOIN tenant_products p     ON p.sku    = i.sku
            JOIN tenant_transactions t ON t.txn_id = i.txn_id
            WHERE t.merchant_id = ?
              AND DATE(t.txn_ts, 'weekday 0', '-6 days') BETWEEN ? AND ?
            GROUP BY week, p.category
            """,
            (merchant_id, week_lo, week_hi),
        ).fetchall()
        wk_total_rows = c.execute(
            """
            SELECT DATE(t.txn_ts, 'weekday 0', '-6 days') AS week,
                   SUM(i.line_total) AS rev
            FROM tenant_transaction_items i
            JOIN tenant_transactions t ON t.txn_id = i.txn_id
            WHERE t.merchant_id = ?
              AND DATE(t.txn_ts, 'weekday 0', '-6 days') BETWEEN ? AND ?
            GROUP BY week
            """,
            (merchant_id, week_lo, week_hi),
        ).fetchall()

    wk_total = {w: float(r) for w, r in wk_total_rows}
    cell = {(w, c_): float(r) for w, c_, r in wk_rows}
    series_values: dict[str, list[float | None]] = {c_: [] for c_ in top_cats}
    for w in weeks:
        tot = wk_total.get(w, 0.0)
        for c_ in top_cats:
            rev = cell.get((w, c_), 0.0)
            series_values[c_].append(
                round(rev / tot * 100, 2) if tot > 0 else None
            )

    # First vs last non-None share → category drift in pp.
    drifts: list[tuple[str, float]] = []
    for c_, vals in series_values.items():
        nv = [v for v in vals if v is not None]
        if len(nv) >= 2:
            drifts.append((c_, round(nv[-1] - nv[0], 2)))
    drifts.sort(key=lambda d: d[1], reverse=True)
    growing = drifts[0] if drifts and drifts[0][1] > 0 else None
    declining = drifts[-1] if drifts and drifts[-1][1] < 0 else None

    return {
        "weeks":  weeks,
        "series": [{"name": c_, "values": series_values[c_]} for c_ in top_cats],
        "y_label":   "Share of revenue (%)",
        "growing_category":   growing[0]  if growing   else "—",
        "growing_pp":         growing[1]  if growing   else 0.0,
        "declining_category": declining[0] if declining else "—",
        "declining_pp":       declining[1] if declining else 0.0,
    }


# ---------------------------------------------------------------------------
# T-D3 / R-D3 — Recent-week revenue change decomposition
# ---------------------------------------------------------------------------

_D_TIE_PP = 2.0  # same threshold as D7 cross_merchant


@st.cache_data(ttl=3600)
def revenue_change_decomposition_own(merchant_id: str) -> dict:
    """Own-vs-own decomposition of recent-week revenue vs first-4w
    baseline-week. Pattern 5 own_vs_own_baseline.

    Identity R = N × B × P (store count constant within a merchant
    across the baseline → recent window). Drivers Mix / Residual are
    0-valued placeholders matching D7's convention.
    """
    import math

    with _conn() as c:
        recent = c.execute(
            """
            SELECT COUNT(DISTINCT t.txn_id) AS n_txns,
                   SUM(i.qty)               AS items,
                   SUM(i.line_total)        AS revenue
            FROM tenant_transactions t
            JOIN tenant_transaction_items i ON i.txn_id = t.txn_id
            WHERE t.merchant_id = ?
              AND DATE(t.txn_ts, 'weekday 0', '-6 days') = ?
            """,
            (merchant_id, _A_RECENT_WEEK_START),
        ).fetchone()
        # Baseline = mean weekly aggregates across the first 4 weeks
        # (sum / 4).
        baseline = c.execute(
            """
            SELECT COUNT(DISTINCT t.txn_id) * 1.0 / 4 AS n_txns,
                   SUM(i.qty)              * 1.0 / 4 AS items,
                   SUM(i.line_total)       * 1.0 / 4 AS revenue
            FROM tenant_transactions t
            JOIN tenant_transaction_items i ON i.txn_id = t.txn_id
            WHERE t.merchant_id = ?
              AND DATE(t.txn_ts, 'weekday 0', '-6 days') BETWEEN ? AND ?
            """,
            (merchant_id, _A_BASELINE_WEEK_START, _A_BASELINE_WEEK_END),
        ).fetchone()

    N_r, I_r, R_r = recent
    N_b, I_b, R_b = baseline
    if not all([N_r, I_r, R_r, N_b, I_b, R_b]):
        return {"drivers": [], "total_label": "Δ this week",
                "y_label": "Contribution (pp)",
                "total_change_pct": 0.0, "dominant_driver": "—",
                "dominant_pp": 0.0, "tied_with": [], "has_data": False}
    N_r = int(N_r); N_b = float(N_b)
    I_r = float(I_r); I_b = float(I_b)
    R_r = float(R_r); R_b = float(R_b)

    B_r = I_r / N_r; B_b = I_b / N_b
    P_r = R_r / I_r; P_b = R_b / I_b

    log_R  = math.log(R_r / R_b)
    log_N  = math.log(N_r / N_b)
    log_B  = math.log(B_r / B_b)
    log_P  = math.log(P_r / P_b)

    change_pct = round(log_R * 100, 1)
    drivers = [
        {"label": "Traffic", "contribution": round(log_N * 100, 1),
         "own": f"{N_r:,} txns", "peer": f"{N_b:,.0f} (baseline)"},
        {"label": "Basket",  "contribution": round(log_B * 100, 1),
         "own": f"{B_r:.2f} items/txn", "peer": f"{B_b:.2f} (baseline)"},
        {"label": "Ticket",  "contribution": round(log_P * 100, 1),
         "own": f"${P_r:.2f}/item", "peer": f"${P_b:.2f} (baseline)"},
        {"label": "Mix",     "contribution": 0.0,
         "own": "(deferred)", "peer": "(deferred)"},
        {"label": "Residual","contribution": 0.0,
         "own": "(deferred)", "peer": "(deferred)"},
    ]

    real = drivers[:3]
    ranked = sorted(real, key=lambda d: abs(d["contribution"]), reverse=True)
    dom = ranked[0]
    tied = [
        (d["label"], d["contribution"])
        for d in ranked[1:]
        if abs(abs(dom["contribution"]) - abs(d["contribution"])) <= _D_TIE_PP
    ]

    return {
        "drivers":         drivers,
        "total_label":     "Δ this week",
        "y_label":         "Contribution (pp)",
        "total_change_pct": change_pct,
        "dominant_driver":  dom["label"],
        "dominant_pp":      dom["contribution"],
        "tied_with":        tied,
        "has_data":         True,
    }


# ---------------------------------------------------------------------------
# Phase 4.3b — TJX-specific helpers
# ---------------------------------------------------------------------------

# Ticket bands for R-P3. Ordered ascending so the bar chart reads
# "smallest band → largest band" top-to-bottom (autorange="reversed"
# in the helper flips this so smallest sits at the top).
_TJX_TICKET_BANDS: list[tuple[str, float, float | None]] = [
    ("$0-50",     0.0,    50.0),
    ("$50-100",   50.0,   100.0),
    ("$100-200",  100.0,  200.0),
    ("$200-500",  200.0,  500.0),
    ("$500+",     500.0,  None),
]


@st.cache_data(ttl=3600)
def category_price_spread(merchant_id: str) -> dict:
    """Per-category min / median / max unit price + spread ratio
    (max / min). Pattern 9 table shape for R-P2.
    """
    with _conn() as c:
        # SQLite doesn't ship a percentile function, so the median is
        # computed via two-step sort + index. Cheap given panel size
        # (TJX category line counts are 8K-10K each).
        cats = [r[0] for r in c.execute(
            """
            SELECT DISTINCT p.category
            FROM tenant_transaction_items i
            JOIN tenant_products p ON p.sku = i.sku
            JOIN tenant_transactions t ON t.txn_id = i.txn_id
            WHERE t.merchant_id = ?
            """,
            (merchant_id,),
        )]
        rows: list[dict] = []
        for cat in cats:
            prices = [
                float(r[0]) for r in c.execute(
                    """
                    SELECT i.unit_price
                    FROM tenant_transaction_items i
                    JOIN tenant_products p ON p.sku = i.sku
                    JOIN tenant_transactions t ON t.txn_id = i.txn_id
                    WHERE t.merchant_id = ? AND p.category = ?
                    ORDER BY i.unit_price
                    """,
                    (merchant_id, cat),
                )
            ]
            if not prices:
                continue
            mid = len(prices) // 2
            median = (
                (prices[mid - 1] + prices[mid]) / 2
                if len(prices) % 2 == 0 else prices[mid]
            )
            p_min = prices[0]
            p_max = prices[-1]
            ratio = p_max / p_min if p_min > 0 else float("nan")
            rows.append({
                "category":  cat,
                "min_price": round(p_min, 2),
                "median_price": round(median, 2),
                "max_price": round(p_max, 2),
                "spread_ratio": round(ratio, 1),
                "n_lines":   len(prices),
            })

    rows.sort(key=lambda r: r["spread_ratio"], reverse=True)
    widest = rows[0] if rows else None
    tightest = rows[-1] if len(rows) > 1 else None
    return {
        "rows":     rows,
        "widest":   widest,
        "tightest": tightest,
    }


@st.cache_data(ttl=3600)
def ticket_band_distribution(merchant_id: str) -> dict:
    """Per-ticket-band transaction count + revenue share. Returns
    grouped-bars data shape (R-P3). Both metrics expressed as
    percentages on a common 0-100 scale for clean grouped display.
    """
    with _conn() as c:
        # Each band's lower bound is inclusive, upper bound is exclusive
        # (open-ended for the top band).
        agg_rows = []
        for label, lo, hi in _TJX_TICKET_BANDS:
            if hi is None:
                row = c.execute(
                    """
                    SELECT COUNT(*), COALESCE(SUM(txn_total), 0)
                    FROM tenant_transactions
                    WHERE merchant_id = ? AND txn_total >= ?
                    """,
                    (merchant_id, lo),
                ).fetchone()
            else:
                row = c.execute(
                    """
                    SELECT COUNT(*), COALESCE(SUM(txn_total), 0)
                    FROM tenant_transactions
                    WHERE merchant_id = ?
                      AND txn_total >= ? AND txn_total < ?
                    """,
                    (merchant_id, lo, hi),
                ).fetchone()
            agg_rows.append((label, int(row[0]), float(row[1])))

    total_n = sum(r[1] for r in agg_rows) or 1
    total_rev = sum(r[2] for r in agg_rows) or 1.0

    labels = [r[0] for r in agg_rows]
    txn_share = [round(r[1] / total_n * 100, 1) for r in agg_rows]
    rev_share = [round(r[2] / total_rev * 100, 1) for r in agg_rows]

    # Top band = the one with the largest revenue share; informs the
    # takeaway sentence.
    top_idx = max(range(len(agg_rows)), key=lambda i: rev_share[i])
    return {
        "labels": labels,
        "series": [
            {"name": "Share of transactions", "values": txn_share},
            {"name": "Share of revenue",      "values": rev_share},
        ],
        "x_label":      "Share (%)",
        "value_format": ".0f",
        "top_band":     agg_rows[top_idx][0],
        "top_txn_pct":  txn_share[top_idx],
        "top_rev_pct":  rev_share[top_idx],
    }


@st.cache_data(ttl=3600)
def day_week_heatmap(merchant_id: str) -> dict:
    """Day-of-week × week ratio cells for the last 4 weeks vs each
    day's first-4w baseline mean. Pattern 3 own_only_diverging shape
    (R-A3). Cells with recent count <5 are suppressed.
    """
    # Use the same 4-week-baseline / recent-4-weeks comparison as the
    # other anomaly questions, but expanded to all 4 recent weeks so
    # the heatmap surfaces day-of-week patterns across the recent
    # window (not just the single last week, which would be a 7-cell
    # strip rather than a 2D heatmap).
    recent_weeks = ["2026-04-27", "2026-05-04", "2026-05-11", "2026-05-18"]
    week_lo = "2026-03-02"
    week_hi = "2026-05-18"

    with _conn() as c:
        rows = c.execute(
            """
            SELECT DATE(t.txn_ts, 'weekday 0', '-6 days') AS week,
                   STRFTIME('%w', t.txn_ts) AS dow_num,
                   COUNT(DISTINCT t.txn_id) AS n_txns
            FROM tenant_transactions t
            WHERE t.merchant_id = ?
              AND DATE(t.txn_ts, 'weekday 0', '-6 days')
                  BETWEEN ? AND ?
            GROUP BY week, dow_num
            """,
            (merchant_id, week_lo, week_hi),
        ).fetchall()

    # Per-day baseline = mean of (Mar 2, 9, 16, 23) counts.
    baseline_by_dow: dict[str, list[int]] = {}
    recent_by_dow_week: dict[tuple[str, str], int] = {}
    for week, dow_num, n in rows:
        dow = _DOW_FROM_SQLITE.get(dow_num)
        if not dow:
            continue
        if _A_BASELINE_WEEK_START <= week <= _A_BASELINE_WEEK_END:
            baseline_by_dow.setdefault(dow, []).append(int(n))
        if week in recent_weeks:
            recent_by_dow_week[(dow, week)] = int(n)

    # Format weeks as "May 18" etc. for column labels.
    from datetime import date
    col_labels = [
        date.fromisoformat(w).strftime("%b %-d") for w in recent_weeks
    ]

    cells: list[list[float | None]] = []
    for dow in _DAY_OF_WEEK_ORDER:
        base_list = baseline_by_dow.get(dow, [])
        base_avg = (sum(base_list) / 4) if base_list else 0.0
        row_vals = []
        for w in recent_weeks:
            n = recent_by_dow_week.get((dow, w), 0)
            if n < 5 or base_avg <= 0:
                row_vals.append(None)
            else:
                row_vals.append(round(n / base_avg, 3))
        cells.append(row_vals)

    flat: list[tuple[float, str, str]] = []
    for i, dow in enumerate(_DAY_OF_WEEK_ORDER):
        for j, w_label in enumerate(col_labels):
            v = cells[i][j]
            if v is not None:
                flat.append((v, dow, w_label))
    weakest   = min(flat, key=lambda r: r[0]) if flat else None
    strongest = max(flat, key=lambda r: r[0]) if flat else None

    return {
        "rows":      _DAY_OF_WEEK_ORDER,
        "cols":      col_labels,
        "cells":     cells,
        "weakest":   weakest,
        "strongest": strongest,
    }


# ---------------------------------------------------------------------------
# Compatibility shims for placeholders.py and earlier views (no-op now)
# ---------------------------------------------------------------------------

def has_same_segment_peers(merchant_id: str) -> bool:
    """Retained for any caller that still imports it."""
    own_seg = MERCHANT_SEGMENT[merchant_id]
    other_segs = [s for m, s in MERCHANT_SEGMENT.items() if m != merchant_id]
    return own_seg in other_segs
