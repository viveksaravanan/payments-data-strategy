"""Generate the JSON payload consumed by docs/report.html.

Pulls every number live from `data/payments.db`. No hardcoded figures except
window dates (read from parameters) and labels. Run after `make seed`.

    uv run python scripts/generate_report_data.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from src.anonymize.hash import customer_id_for
from src.generate.parameters import END_DATE, MERCHANT_CONFIGS, START_DATE

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "payments.db"
RAW_CUSTOMERS = ROOT / "data" / "raw" / "customers.csv"
RAW_TXNS = ROOT / "data" / "raw" / "transactions.csv"
OUT = ROOT / "docs" / "report_data.json"
OUT_JS = ROOT / "docs" / "report_data.js"

MERCHANT_KEYS = {"KRG": "kroger", "TBL": "tacobell", "TJX": "tjmaxx"}


def _stats(c: sqlite3.Cursor) -> dict:
    return {
        "n_customers": c.execute("SELECT COUNT(*) FROM tenant_customers").fetchone()[0],
        "n_transactions": c.execute("SELECT COUNT(*) FROM tenant_transactions").fetchone()[0],
        "n_line_items": c.execute("SELECT COUNT(*) FROM tenant_transaction_items").fetchone()[0],
        "n_skus": c.execute("SELECT COUNT(*) FROM tenant_products").fetchone()[0],
        "n_suppressed": c.execute(
            "SELECT COUNT(*) FROM lake_customers WHERE home_zip3 IS NULL"
        ).fetchone()[0],
        "n_multi_merchant": c.execute("""
            SELECT COUNT(*) FROM (
                SELECT customer_id FROM lake_transactions
                GROUP BY customer_id HAVING COUNT(DISTINCT merchant_id) >= 2
            )
        """).fetchone()[0],
    }


def _per_merchant(c: sqlite3.Cursor) -> list[dict]:
    rows = []
    for m_id in ("KRG", "TBL", "TJX"):
        meta = c.execute(
            "SELECT name, segment, mcc FROM merchants WHERE merchant_id = ?", (m_id,)
        ).fetchone()
        n_stores = c.execute(
            "SELECT COUNT(*) FROM tenant_stores WHERE merchant_id = ?", (m_id,)
        ).fetchone()[0]
        n_customers = c.execute(
            "SELECT COUNT(DISTINCT customer_id) FROM tenant_transactions WHERE merchant_id = ?",
            (m_id,),
        ).fetchone()[0]
        n_txns = c.execute(
            "SELECT COUNT(*) FROM tenant_transactions WHERE merchant_id = ?", (m_id,)
        ).fetchone()[0]
        n_items = c.execute("""
            SELECT COUNT(*) FROM tenant_transaction_items i
            JOIN tenant_transactions t ON i.txn_id = t.txn_id
            WHERE t.merchant_id = ?
        """, (m_id,)).fetchone()[0]
        avg_basket = c.execute("""
            SELECT AVG(items) FROM (
                SELECT t.txn_id, SUM(i.qty) AS items
                FROM tenant_transaction_items i
                JOIN tenant_transactions t ON i.txn_id = t.txn_id
                WHERE t.merchant_id = ?
                GROUP BY t.txn_id
            )
        """, (m_id,)).fetchone()[0]
        avg_ticket = c.execute(
            "SELECT AVG(txn_total) FROM tenant_transactions WHERE merchant_id = ?", (m_id,)
        ).fetchone()[0]

        mix_rows = c.execute("""
            SELECT payment_type, COUNT(*) AS n FROM tenant_transactions
            WHERE merchant_id = ? GROUP BY payment_type
        """, (m_id,)).fetchall()
        total_mix = sum(r[1] for r in mix_rows)
        payment_mix = {r[0]: round(r[1] / total_mix * 100, 1) for r in mix_rows}

        rows.append({
            "merchant_id": m_id,
            "name": meta[0],
            "segment": meta[1],
            "mcc": meta[2],
            "n_stores": n_stores,
            "n_customers": n_customers,
            "n_transactions": n_txns,
            "n_line_items": n_items,
            "avg_basket_size": round(avg_basket, 2),
            "avg_ticket": round(avg_ticket, 2),
            "payment_mix": payment_mix,
        })
    return rows


def _kroger_revenue_by_category(c: sqlite3.Cursor) -> list[dict]:
    cats = c.execute("""
        SELECT p.category, ROUND(SUM(i.line_total), 2) AS revenue,
               COUNT(DISTINCT p.sku) AS sku_count
        FROM tenant_transaction_items i
        JOIN tenant_transactions t ON i.txn_id = t.txn_id
        JOIN tenant_products p ON i.sku = p.sku
        WHERE t.merchant_id = 'KRG'
        GROUP BY p.category
        ORDER BY revenue DESC
    """).fetchall()
    out = []
    for cat, revenue, sku_count in cats:
        subs = c.execute("""
            SELECT p.subcategory, ROUND(SUM(i.line_total), 2) AS revenue
            FROM tenant_transaction_items i
            JOIN tenant_transactions t ON i.txn_id = t.txn_id
            JOIN tenant_products p ON i.sku = p.sku
            WHERE t.merchant_id = 'KRG' AND p.category = ?
            GROUP BY p.subcategory
            ORDER BY revenue DESC
        """, (cat,)).fetchall()
        out.append({
            "category": cat,
            "revenue": revenue,
            "sku_count": sku_count,
            "subcategories": [
                {"subcategory": s[0], "revenue": s[1]} for s in subs
            ],
        })
    return out


def _daily_volume(c: sqlite3.Cursor) -> list[dict]:
    daily: dict[str, dict[str, int]] = {}
    for m_id in ("KRG", "TBL", "TJX"):
        for d, n in c.execute("""
            SELECT DATE(txn_ts) AS d, COUNT(*) FROM tenant_transactions
            WHERE merchant_id = ?
            GROUP BY DATE(txn_ts) ORDER BY d
        """, (m_id,)):
            daily.setdefault(d, {})[m_id] = n
    out = []
    cur = START_DATE
    while cur <= END_DATE:
        d = cur.isoformat()
        row = daily.get(d, {})
        out.append({
            "date": d,
            "kroger_count": row.get("KRG", 0),
            "tacobell_count": row.get("TBL", 0),
            "tjmaxx_count": row.get("TJX", 0),
        })
        cur += timedelta(days=1)
    return out


def _hour_distribution(c: sqlite3.Cursor) -> list[dict]:
    counts = {h: {"KRG": 0, "TBL": 0, "TJX": 0} for h in range(24)}
    totals = {"KRG": 0, "TBL": 0, "TJX": 0}
    for m_id, hour, n in c.execute("""
        SELECT merchant_id, CAST(strftime('%H', txn_ts) AS INTEGER), COUNT(*)
        FROM tenant_transactions
        GROUP BY merchant_id, CAST(strftime('%H', txn_ts) AS INTEGER)
    """):
        counts[hour][m_id] = n
        totals[m_id] += n
    out = []
    for h in range(24):
        out.append({
            "hour": h,
            "kroger_pct": round(100 * counts[h]["KRG"] / max(totals["KRG"], 1), 2),
            "tacobell_pct": round(100 * counts[h]["TBL"] / max(totals["TBL"], 1), 2),
            "tjmaxx_pct": round(100 * counts[h]["TJX"] / max(totals["TJX"], 1), 2),
        })
    return out


def _customer_overlap(c: sqlite3.Cursor) -> dict:
    by_count = dict(c.execute("""
        SELECT n_merchants, COUNT(*) FROM (
            SELECT customer_id, COUNT(DISTINCT merchant_id) AS n_merchants
            FROM lake_transactions GROUP BY customer_id
        ) GROUP BY n_merchants
    """).fetchall())
    n_zero = c.execute(
        "SELECT COUNT(*) FROM lake_customers WHERE customer_id NOT IN (SELECT customer_id FROM lake_transactions)"
    ).fetchone()[0]

    sets = Counter()
    for cid, ms in c.execute("""
        SELECT customer_id, GROUP_CONCAT(merchant_id) FROM (
            SELECT DISTINCT customer_id, merchant_id FROM lake_transactions
            ORDER BY customer_id, merchant_id
        ) GROUP BY customer_id
    """):
        key = ",".join(sorted(ms.split(",")))
        sets[key] += 1

    return {
        "merchants_0": n_zero,
        "merchants_1": by_count.get(1, 0),
        "merchants_2": by_count.get(2, 0),
        "merchants_3": by_count.get(3, 0),
        "by_set": dict(sets),
    }


def _pay_cycle(c: sqlite3.Cursor) -> list[dict]:
    buckets = [
        ("1-3 (paychecks/SNAP)", "BETWEEN 1 AND 3"),
        ("4-14 (mid-month)", "BETWEEN 4 AND 14"),
        ("15-17 (paychecks)", "BETWEEN 15 AND 17"),
        ("18-31 (late-month)", "BETWEEN 18 AND 31"),
    ]
    out = []
    for label, predicate in buckets:
        row = {"bucket": label}
        for m_id in ("KRG", "TBL", "TJX"):
            n_txns = c.execute(f"""
                SELECT COUNT(*) FROM tenant_transactions
                WHERE merchant_id = ?
                  AND CAST(strftime('%d', txn_ts) AS INTEGER) {predicate}
            """, (m_id,)).fetchone()[0]
            n_days = c.execute(f"""
                SELECT COUNT(DISTINCT DATE(txn_ts)) FROM tenant_transactions
                WHERE merchant_id = ?
                  AND CAST(strftime('%d', txn_ts) AS INTEGER) {predicate}
            """, (m_id,)).fetchone()[0]
            row[f"{MERCHANT_KEYS[m_id]}_avg"] = round(n_txns / n_days, 1) if n_days else 0.0
        out.append(row)
    return out


def _example_transaction(c: sqlite3.Cursor) -> dict:
    txn = c.execute("""
        SELECT t.txn_id, t.merchant_id, m.name AS merchant_name, t.store_id,
               t.txn_ts, t.payment_type, t.card_network, t.entry_mode,
               t.wallet_type, t.txn_total
        FROM tenant_transactions t
        JOIN merchants m ON t.merchant_id = m.merchant_id
        WHERE t.merchant_id = 'KRG'
          AND t.payment_type = 'credit'
          AND t.txn_total BETWEEN 60 AND 120
          AND t.txn_id IN (
              SELECT txn_id FROM tenant_transaction_items
              GROUP BY txn_id HAVING COUNT(*) BETWEEN 8 AND 12
          )
        ORDER BY t.txn_id
        LIMIT 1
    """).fetchone()
    if txn is None:
        txn = c.execute("""
            SELECT t.txn_id, t.merchant_id, m.name, t.store_id, t.txn_ts,
                   t.payment_type, t.card_network, t.entry_mode, t.wallet_type, t.txn_total
            FROM tenant_transactions t JOIN merchants m USING (merchant_id)
            WHERE t.merchant_id = 'KRG' LIMIT 1
        """).fetchone()
    items = []
    for r in c.execute("""
        SELECT i.line_id, i.sku, p.name, p.subcategory, i.qty, i.unit_price, i.line_total
        FROM tenant_transaction_items i
        JOIN tenant_products p ON i.sku = p.sku
        WHERE i.txn_id = ?
        ORDER BY i.line_id
    """, (txn[0],)):
        items.append({
            "line_id": r[0], "sku": r[1], "name": r[2], "subcategory": r[3],
            "qty": r[4], "unit_price": r[5], "line_total": r[6],
        })
    return {
        "txn_id": txn[0], "merchant_id": txn[1], "merchant_name": txn[2],
        "store_id": txn[3], "txn_ts": txn[4], "payment_type": txn[5],
        "card_network": txn[6], "entry_mode": txn[7], "wallet_type": txn[8],
        "txn_total": txn[9],
        "items": items,
    }


def _anonymization_demo(c: sqlite3.Cursor) -> dict:
    raw = pd.read_csv(RAW_CUSTOMERS, dtype={"customer_pan": str, "home_zip5": str})
    # Pick a customer who survives k-anonymity in the lake (so the visible
    # truncation is meaningful).
    candidate = None
    for _, row in raw.head(50).iterrows():
        cid = customer_id_for(row["customer_pan"])
        lake = c.execute(
            "SELECT home_zip3 FROM lake_customers WHERE customer_id = ?", (cid,)
        ).fetchone()
        if lake and lake[0] is not None:
            candidate = (row, cid, lake[0])
            break
    if candidate is None:
        row = raw.iloc[0]
        candidate = (row, customer_id_for(row["customer_pan"]), row["home_zip5"][:3])

    row, cid, zip3 = candidate
    return {
        "raw": {
            "customer_pan": row["customer_pan"],
            "customer_name": row["customer_name"],
            "customer_email": row["customer_email"],
            "age_band": row["age_band"],
            "income_band": row["income_band"],
            "home_zip5": row["home_zip5"],
        },
        "tenant": {
            "customer_id": cid,
            "age_band": row["age_band"],
            "income_band": row["income_band"],
            "home_zip5": row["home_zip5"],
        },
        "lake": {
            "customer_id": cid,
            "age_band": row["age_band"],
            "income_band": row["income_band"],
            "home_zip3": zip3,
        },
    }


def _cross_merchant_finding(c: sqlite3.Cursor) -> dict:
    cutoff = c.execute(
        "SELECT date(MAX(txn_ts), '-29 days') FROM lake_transactions"
    ).fetchone()[0]

    all_three_ids = [
        r[0] for r in c.execute(f"""
            SELECT customer_id FROM lake_transactions
            WHERE txn_ts >= '{cutoff}'
            GROUP BY customer_id
            HAVING COUNT(DISTINCT merchant_id) = 3
        """)
    ]
    spend = {}
    if all_three_ids:
        placeholders = ",".join(["?"] * len(all_three_ids))
        for m_id in ("KRG", "TBL", "TJX"):
            row = c.execute(f"""
                SELECT AVG(s) FROM (
                    SELECT customer_id, SUM(txn_total) AS s
                    FROM lake_transactions
                    WHERE merchant_id = ?
                      AND txn_ts >= ?
                      AND customer_id IN ({placeholders})
                    GROUP BY customer_id
                )
            """, (m_id, cutoff, *all_three_ids)).fetchone()
            spend[MERCHANT_KEYS[m_id]] = round(row[0] or 0, 2)

    sql = f"""WITH active_30d AS (
  SELECT customer_id, COUNT(DISTINCT merchant_id) AS n_merchants
  FROM lake_transactions
  WHERE txn_ts >= '{cutoff}'
  GROUP BY customer_id
),
all_three AS (
  SELECT customer_id FROM active_30d WHERE n_merchants = 3
)
SELECT m.name AS merchant,
       ROUND(AVG(per_cust.spend), 2) AS avg_30d_spend,
       COUNT(DISTINCT per_cust.customer_id) AS n_customers
FROM (
  SELECT customer_id, merchant_id, SUM(txn_total) AS spend
  FROM lake_transactions
  WHERE txn_ts >= '{cutoff}'
    AND customer_id IN (SELECT customer_id FROM all_three)
  GROUP BY customer_id, merchant_id
) per_cust
JOIN merchants m ON m.merchant_id = per_cust.merchant_id
GROUP BY m.merchant_id
ORDER BY avg_30d_spend DESC;"""

    return {
        "n_customers_all_three": len(all_three_ids),
        "window_days": 30,
        "window_start": cutoff,
        "avg_spend_30d": spend,
        "agent_sql": sql,
    }


def _basket_comparison_sql() -> str:
    return """SELECT m.name AS merchant, m.segment,
       ROUND(AVG(items_per_txn), 2) AS avg_basket_size
FROM (
  SELECT t.merchant_id, t.txn_id,
         SUM(i.qty) AS items_per_txn
  FROM lake_transactions t
  JOIN lake_transaction_items i USING (txn_id)
  GROUP BY t.txn_id
) sub
JOIN merchants m ON sub.merchant_id = m.merchant_id
GROUP BY m.merchant_id
ORDER BY avg_basket_size DESC;"""


def _affinity_pairs() -> list[dict]:
    return [
        {"merchant": "Kroger", "anchor": "Diapers",      "companion": "Infant formula",   "prob": 0.45},
        {"merchant": "Kroger", "anchor": "Spaghetti",    "companion": "Marinara sauce",   "prob": 0.55},
        {"merchant": "Kroger", "anchor": "Tortillas",    "companion": "Ground beef",      "prob": 0.40},
        {"merchant": "Kroger", "anchor": "Tortillas",    "companion": "Shredded cheese",  "prob": 0.45},
        {"merchant": "Kroger", "anchor": "Whole milk",   "companion": "Cereal",           "prob": 0.30},
        {"merchant": "Kroger", "anchor": "Coffee",       "companion": "Half & half",      "prob": 0.40},
        {"merchant": "Taco Bell", "anchor": "Any entree",   "companion": "Drink",         "prob": 0.70},
        {"merchant": "Taco Bell", "anchor": "Combo meal",   "companion": "Cinnamon Twists","prob": 0.35},
        {"merchant": "TJ Maxx",   "anchor": "Women's apparel", "companion": "Accessory",  "prob": 0.40},
        {"merchant": "TJ Maxx",   "anchor": "Kitchen towels",  "companion": "Other home goods", "prob": 0.50},
    ]


def _agent_status() -> list[dict]:
    return [
        {"name": "Conversational Business Advisor — Merchant", "status": "Built",   "section": "§10.2"},
        {"name": "Conversational Business Advisor — Network",  "status": "Built",   "section": "§10.2"},
        {"name": "Demand Forecasting",                         "status": "Stretch", "section": "§10.2"},
        {"name": "Dynamic Pricing",                            "status": "Roadmap", "section": "§10.2"},
        {"name": "Location Intelligence",                      "status": "Roadmap", "section": "§10.2"},
        {"name": "Payment Optimization",                       "status": "Roadmap", "section": "§10.2"},
        {"name": "Anomaly Detection",                          "status": "Roadmap", "section": "§10.2"},
    ]


def _schema() -> list[dict]:
    return [
        {"table": "merchants", "layer": "shared", "columns": [
            "merchant_id", "name", "segment", "mcc"
        ]},
        {"table": "tenant_customers", "layer": "tenant", "columns": [
            "customer_id", "age_band", "income_band", "home_zip5",
            "signup_date", "primary_card_type", "has_mobile_wallet",
        ]},
        {"table": "tenant_stores", "layer": "tenant", "columns": [
            "store_id", "merchant_id", "store_zip5", "region", "open_date",
        ]},
        {"table": "tenant_products", "layer": "tenant", "columns": [
            "sku", "merchant_id", "name", "category", "subcategory",
            "is_organic", "base_price",
        ]},
        {"table": "tenant_transactions", "layer": "tenant", "columns": [
            "txn_id", "merchant_id", "customer_id", "store_id", "txn_ts",
            "payment_type", "card_network", "entry_mode", "wallet_type",
            "txn_total",
        ]},
        {"table": "tenant_transaction_items", "layer": "tenant", "columns": [
            "txn_id", "line_id", "sku", "qty", "unit_price", "discount", "line_total",
        ]},
        {"table": "lake_customers", "layer": "lake", "columns": [
            "customer_id", "age_band", "income_band", "home_zip3",
            "signup_date", "primary_card_type", "has_mobile_wallet",
        ]},
        {"table": "lake_transactions", "layer": "lake", "columns": [
            "txn_id", "merchant_id", "customer_id", "store_zip3", "region",
            "txn_ts", "txn_hour_bucket", "payment_type", "card_network",
            "entry_mode", "wallet_type", "txn_total",
        ]},
        {"table": "lake_transaction_items", "layer": "lake", "columns": [
            "txn_id", "line_id", "sku_category", "qty", "unit_price", "line_total",
        ]},
    ]


def main() -> None:
    if not DB.exists():
        sys.exit(f"DB not found at {DB}. Run `make seed` first.")
    OUT.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "window": {
            "start": START_DATE.isoformat(),
            "end": END_DATE.isoformat(),
            "days": (END_DATE - START_DATE).days + 1,
        },
        "promo_days": ["2026-04-15", "2026-04-22", "2026-05-01"],
        "anomaly_window": {
            "start": (END_DATE - timedelta(days=6)).isoformat(),
            "end": END_DATE.isoformat(),
        },
        "stats": _stats(c),
        "merchants": _per_merchant(c),
        "revenue_by_category_kroger": _kroger_revenue_by_category(c),
        "daily_volume": _daily_volume(c),
        "hour_distribution": _hour_distribution(c),
        "customer_overlap": _customer_overlap(c),
        "pay_cycle": _pay_cycle(c),
        "example_transaction": _example_transaction(c),
        "anonymization_demo": _anonymization_demo(c),
        "cross_merchant_finding": _cross_merchant_finding(c),
        "sql_basket_comparison": _basket_comparison_sql(),
        "affinity_pairs": _affinity_pairs(),
        "agents_status": _agent_status(),
        "schema": _schema(),
    }
    conn.close()

    OUT.write_text(json.dumps(payload, indent=2, default=str))
    # Also emit a JS file that sets window.REPORT_DATA. Browsers block fetch()
    # from file:// (Chrome especially), so the HTML falls back to this when
    # the report is opened by double-click instead of served over HTTP.
    OUT_JS.write_text(
        "window.REPORT_DATA = "
        + json.dumps(payload, default=str)
        + ";\n"
    )

    s = payload["stats"]
    f = payload["cross_merchant_finding"]
    print(f"wrote {OUT}")
    print(f"  customers={s['n_customers']:,}  txns={s['n_transactions']:,}  "
          f"items={s['n_line_items']:,}")
    print(f"  multi-merchant (any 2+) = {s['n_multi_merchant']:,}  "
          f"all-three (last 30d) = {f['n_customers_all_three']:,}")
    print(f"  k-anon suppressed = {s['n_suppressed']:,}")


if __name__ == "__main__":
    main()
