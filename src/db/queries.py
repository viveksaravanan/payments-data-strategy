"""Canned SQL for the Kroger-role demo questions in PLAN.md §9.3.

The agent runs its own SQL through the tools; these are reference queries used
by the dashboard and by tests to validate the schema actually answers the
demo's headline questions.
"""
from __future__ import annotations

from datetime import timedelta

from src.generate.parameters import END_DATE


def top_categories_by_revenue_last_week(merchant_id: str = "KRG", days: int = 7) -> str:
    """Q1 — Top categories by revenue in the last `days`, with the subcategories
    that drove each category's revenue. Tenant only.
    """
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


def items_co_purchased_with(merchant_id: str = "KRG",
                            anchor_sku: str = "KRG-DAIRY-0001") -> str:
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
    """Q3 — Stores with the biggest week-over-week transaction-count drop. Tenant only.

    Surfaces the planted store-dropout anomaly at ``KRG-OH-0011``.
    """
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
        SELECT s.store_id, s.region,
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


def my_basket_size_and_grocery_peer_basket_size(merchant_id: str = "KRG") -> dict[str, str]:
    """Q4 — My basket size vs grocery peers. Returns two SQL strings.

    The agent runs the tenant query for its own number and the lake query for
    peer benchmarks.
    """
    tenant_sql = f"""
        SELECT AVG(items_per_txn) AS avg_basket_size
        FROM (
            SELECT txn_id, SUM(qty) AS items_per_txn
            FROM tenant_transaction_items
            WHERE txn_id IN (
                SELECT txn_id FROM tenant_transactions WHERE merchant_id = '{merchant_id}'
            )
            GROUP BY txn_id
        )
    """
    lake_sql = """
        SELECT m.merchant_id, m.name AS merchant,
               AVG(items_per_txn)    AS avg_basket_size
        FROM (
            SELECT t.merchant_id, t.txn_id, SUM(i.qty) AS items_per_txn
            FROM lake_transactions t
            JOIN lake_transaction_items i ON t.txn_id = i.txn_id
            JOIN merchants m              ON t.merchant_id = m.merchant_id
            WHERE m.segment = 'grocery'
            GROUP BY t.txn_id
        ) sub
        JOIN merchants m ON sub.merchant_id = m.merchant_id
        GROUP BY m.merchant_id
        ORDER BY avg_basket_size DESC
    """
    return {"tenant": tenant_sql, "lake": lake_sql}


def my_customers_qsr_overlap(merchant_id: str = "KRG") -> dict[str, str]:
    """Q5 — Share of my customers who also shop QSR, and their behavior at me. Tenant + lake."""
    tenant_sql = f"""
        SELECT DISTINCT customer_id
        FROM tenant_transactions
        WHERE merchant_id = '{merchant_id}'
    """
    lake_sql = f"""
        WITH my_customers AS (
            SELECT DISTINCT customer_id
            FROM lake_transactions
            WHERE merchant_id = '{merchant_id}'
        ),
        qsr_customers AS (
            SELECT DISTINCT t.customer_id
            FROM lake_transactions t
            JOIN merchants m ON t.merchant_id = m.merchant_id
            WHERE m.segment = 'qsr'
        )
        SELECT
            (SELECT COUNT(*) FROM my_customers) AS my_total,
            (SELECT COUNT(*) FROM my_customers WHERE customer_id IN (SELECT customer_id FROM qsr_customers)) AS overlap,
            ROUND(
                100.0 * (SELECT COUNT(*) FROM my_customers WHERE customer_id IN (SELECT customer_id FROM qsr_customers))
                      / (SELECT COUNT(*) FROM my_customers), 2
            ) AS overlap_pct
    """
    return {"tenant": tenant_sql, "lake": lake_sql}


CANNED_QUERIES = {
    "top_categories_by_revenue_last_week":       top_categories_by_revenue_last_week,
    "items_co_purchased_with":                   items_co_purchased_with,
    "store_dropouts_last_7_days":                store_dropouts_last_7_days,
    "my_basket_size_and_grocery_peer_basket_size": my_basket_size_and_grocery_peer_basket_size,
    "my_customers_qsr_overlap":                  my_customers_qsr_overlap,
}
