"""Canned SQL for the Merchant Advisor demo questions.

The agent runs its own SQL through the tools; these are reference
queries used by the dashboard and by tests to validate the schema
actually answers the demo's headline questions.

Lake queries reference the v2.5 virtual lake (``lake_transactions`` /
``lake_stores``) — the agent runner CTE-wraps those names into the
view-builder SQL with the viewing merchant baked in. ``customer_id``
is not in the lake; cross-merchant customer-cohort questions resolve
at the tenant layer only.
"""
from __future__ import annotations

from datetime import timedelta

from src.generate.parameters import END_DATE


def top_categories_by_revenue_last_week(
    merchant_id: str = "KRG", days: int = 7
) -> str:
    """Q1 — Top categories by revenue in the last `days`, with the
    subcategories that drove each category's revenue. Tenant only."""
    cutoff = (END_DATE - timedelta(days=days)).isoformat()
    return f"""
        SELECT p.category, p.subcategory,
               SUM(i.line_total) AS revenue,
               SUM(i.qty)        AS units_sold
        FROM tenant_transaction_items i
        JOIN tenant_transactions t ON i.txn_id = t.txn_id
        JOIN tenant_products p     ON i.sku    = p.sku
        WHERE t.merchant_id = '{merchant_id}'
          AND t.txn_ts >= '{cutoff}'
        GROUP BY p.category, p.subcategory
        ORDER BY p.category, revenue DESC
    """


def items_co_purchased_with(
    merchant_id: str = "KRG", anchor_sku: str = "KRG-DAIRY-0001"
) -> str:
    """Q2 — Items most often bought with a given SKU (e.g. whole milk). Tenant only."""
    return f"""
        SELECT p.sku, p.name, p.category,
               COUNT(*) AS co_purchases
        FROM tenant_transaction_items i
        JOIN tenant_transactions t ON i.txn_id = t.txn_id
        JOIN tenant_products p     ON i.sku    = p.sku
        WHERE t.merchant_id = '{merchant_id}'
          AND i.txn_id IN (
            SELECT txn_id FROM tenant_transaction_items WHERE sku = '{anchor_sku}'
          )
          AND i.sku <> '{anchor_sku}'
        GROUP BY p.sku
        ORDER BY co_purchases DESC
        LIMIT 10
    """


def store_dropouts_last_7_days(merchant_id: str = "KRG") -> str:
    """Q3 — Stores with the biggest week-over-week transaction-count drop.
    Tenant only. Phase 6 will reinstate planted anomalies (University City
    decline) that this query is designed to surface."""
    last7 = (END_DATE - timedelta(days=6)).isoformat()
    prior7 = (END_DATE - timedelta(days=13)).isoformat()
    return f"""
        WITH last_7 AS (
            SELECT store_id, COUNT(*) AS n
            FROM tenant_transactions
            WHERE merchant_id = '{merchant_id}' AND txn_ts >= '{last7}'
            GROUP BY store_id
        ),
        prior_7 AS (
            SELECT store_id, COUNT(*) AS n
            FROM tenant_transactions
            WHERE merchant_id = '{merchant_id}'
              AND txn_ts >= '{prior7}' AND txn_ts < '{last7}'
            GROUP BY store_id
        )
        SELECT s.store_id, s.neighborhood, s.metro_region,
               COALESCE(p.n, 0)  AS prior_week_txns,
               COALESCE(l.n, 0)  AS last_week_txns,
               COALESCE(l.n, 0) - COALESCE(p.n, 0) AS delta
        FROM tenant_stores s
        LEFT JOIN last_7  l USING(store_id)
        LEFT JOIN prior_7 p USING(store_id)
        WHERE s.merchant_id = '{merchant_id}'
        ORDER BY delta ASC
        LIMIT 10
    """


def my_basket_size_and_grocery_peer_basket_size(
    merchant_id: str = "KRG",
) -> dict[str, str]:
    """Q4 — My basket size vs grocery peers. Returns two SQL strings.

    The agent runs the tenant query for its own number and the lake
    query for peer benchmarks. Lake side aggregates by `peer_id` (no
    merchants-table join needed; `peer_segment` is carried in the lake
    view itself).
    """
    tenant_sql = f"""
        SELECT AVG(items_per_txn) AS avg_basket_size
        FROM (
            SELECT t.txn_id, SUM(i.qty) AS items_per_txn
            FROM tenant_transaction_items i
            JOIN tenant_transactions t ON t.txn_id = i.txn_id
            WHERE t.merchant_id = '{merchant_id}'
            GROUP BY t.txn_id
        )
    """
    lake_sql = """
        SELECT peer_id, peer_segment,
               ROUND(AVG(items_per_txn), 2) AS avg_basket_size
        FROM (
            SELECT lake_txn_id, peer_id, peer_segment,
                   SUM(qty) AS items_per_txn
            FROM lake_transactions
            GROUP BY lake_txn_id, peer_id, peer_segment
        ) sub
        WHERE peer_segment = 'grocery'
        GROUP BY peer_id
        ORDER BY avg_basket_size DESC
    """
    return {"tenant": tenant_sql, "lake": lake_sql}


def my_dairy_pricing_vs_peers(merchant_id: str = "KRG") -> dict[str, str]:
    """Q5 — My dairy unit prices vs peers, by canonical product.

    Replaces the v2 "my customers QSR overlap" query, which relied on
    `customer_id` joins at the lake. The v2.5 lake drops `customer_id`
    per "no consumer linkage" — that question can't be answered at the
    lake any more. This is the closest like-for-like Phase 5 question
    the lake actually supports: peer pricing per canonical product.
    """
    tenant_sql = f"""
        SELECT p.name AS canonical_name,
               ROUND(AVG(i.unit_price), 2) AS my_avg_price
        FROM tenant_transaction_items i
        JOIN tenant_transactions t ON t.txn_id = i.txn_id
        JOIN tenant_products p     ON p.sku    = i.sku
        WHERE t.merchant_id = '{merchant_id}'
          AND p.category = 'DAIRY'
        GROUP BY p.name
        ORDER BY my_avg_price DESC
        LIMIT 50
    """
    lake_sql = """
        SELECT canonical_name, peer_id, peer_segment,
               ROUND(AVG(unit_price), 2) AS peer_avg_price,
               COUNT(*) AS lines
        FROM lake_transactions
        WHERE category = 'DAIRY'
        GROUP BY canonical_name, peer_id, peer_segment
        HAVING COUNT(*) >= 5
        ORDER BY canonical_name, peer_id
        LIMIT 500
    """
    return {"tenant": tenant_sql, "lake": lake_sql}


CANNED_QUERIES = {
    "top_categories_by_revenue_last_week":           top_categories_by_revenue_last_week,
    "items_co_purchased_with":                       items_co_purchased_with,
    "store_dropouts_last_7_days":                    store_dropouts_last_7_days,
    "my_basket_size_and_grocery_peer_basket_size":   my_basket_size_and_grocery_peer_basket_size,
    "my_dairy_pricing_vs_peers":                     my_dairy_pricing_vs_peers,
}
