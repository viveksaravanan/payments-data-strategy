"""Generate the JSON payload that ``docs/report.html`` reads.

Pure read-only against ``data/payments.db``. Output:
``docs/report_data.json`` (canonical) and ``docs/report_data.js``
(``window.REPORT_DATA = ...;`` shim for ``file://`` double-click).

In v2.5 the lake is virtual, so cross-merchant aggregates run against
the tenant tables directly here (the report shows full panel state, not
a single merchant's view of the lake). One section demonstrates the
view-builder API by emitting a peer-perspective basket comparison from
KRG's vantage; that's what the dashboard's lake tool actually uses.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "payments.db"
OUT_JSON = ROOT / "docs" / "report_data.json"
OUT_JS = ROOT / "docs" / "report_data.js"


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _q(conn: sqlite3.Connection, sql: str, **params: Any) -> pd.DataFrame:
    return pd.read_sql_query(sql, conn, params=params)


def _scalar(conn: sqlite3.Connection, sql: str, **params: Any):
    return conn.execute(sql, params).fetchone()[0]


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def _window(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute(
        "SELECT MIN(DATE(txn_ts)), MAX(DATE(txn_ts)) FROM tenant_transactions"
    ).fetchone()
    start, end = row
    days = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).days + 1
    return {"start": start, "end": end, "days": days}


def _stats(conn: sqlite3.Connection) -> dict[str, Any]:
    n_customers = _scalar(conn, "SELECT COUNT(*) FROM tenant_customers")
    n_transactions = _scalar(conn, "SELECT COUNT(*) FROM tenant_transactions")
    n_line_items = _scalar(conn, "SELECT COUNT(*) FROM tenant_transaction_items")
    n_skus = _scalar(conn, "SELECT COUNT(*) FROM tenant_products")
    n_multi = _scalar(conn, """
        SELECT COUNT(*) FROM (
            SELECT customer_id
            FROM tenant_transactions
            GROUP BY customer_id
            HAVING COUNT(DISTINCT merchant_id) >= 2
        )
    """)
    return {
        "n_customers": int(n_customers),
        "n_transactions": int(n_transactions),
        "n_line_items": int(n_line_items),
        "n_skus": int(n_skus),
        # k=5 cell suppression is applied at query time in v2.5; the
        # report doesn't run any suppressed aggregate, so this is 0.
        "n_suppressed": 0,
        "n_multi_merchant": int(n_multi),
    }


def _merchants(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    merchants = _q(conn, "SELECT merchant_id, name, segment, mcc FROM merchants ORDER BY merchant_id")
    for _, m in merchants.iterrows():
        mid = m["merchant_id"]
        n_stores = _scalar(conn, "SELECT COUNT(*) FROM tenant_stores WHERE merchant_id=:m", m=mid)
        n_customers = _scalar(conn, "SELECT COUNT(DISTINCT customer_id) FROM tenant_transactions WHERE merchant_id=:m", m=mid)
        n_transactions = _scalar(conn, "SELECT COUNT(*) FROM tenant_transactions WHERE merchant_id=:m", m=mid)
        n_line_items = _scalar(conn, """
            SELECT COUNT(*) FROM tenant_transaction_items i
            JOIN tenant_transactions t ON t.txn_id = i.txn_id
            WHERE t.merchant_id=:m
        """, m=mid)
        avg_basket = _scalar(conn, """
            SELECT ROUND(AVG(items_per_txn), 2) FROM (
                SELECT t.txn_id, SUM(i.qty) AS items_per_txn
                FROM tenant_transaction_items i
                JOIN tenant_transactions t ON t.txn_id = i.txn_id
                WHERE t.merchant_id=:m GROUP BY t.txn_id
            )
        """, m=mid) or 0.0
        avg_ticket = _scalar(conn, """
            SELECT ROUND(AVG(txn_total), 2) FROM tenant_transactions WHERE merchant_id=:m
        """, m=mid) or 0.0
        mix_df = _q(conn, """
            SELECT payment_type, COUNT(*) AS n FROM tenant_transactions
            WHERE merchant_id=:m GROUP BY payment_type
        """, m=mid)
        total = mix_df["n"].sum() or 1
        payment_mix = {row["payment_type"]: round(100.0 * row["n"] / total, 1)
                       for _, row in mix_df.iterrows()}
        out.append({
            "merchant_id": mid,
            "name": m["name"],
            "segment": m["segment"],
            "mcc": m["mcc"],
            "n_stores": int(n_stores),
            "n_customers": int(n_customers),
            "n_transactions": int(n_transactions),
            "n_line_items": int(n_line_items),
            "avg_basket_size": float(avg_basket),
            "avg_ticket": float(avg_ticket),
            "payment_mix": payment_mix,
        })
    return out


def _revenue_by_category_kroger(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Top categories by line-item revenue at Kroger, with subcategory
    breakdown nested per category."""
    cats = _q(conn, """
        SELECT p.category,
               ROUND(SUM(i.line_total), 2) AS revenue,
               COUNT(DISTINCT p.sku)         AS sku_count
        FROM tenant_transaction_items i
        JOIN tenant_transactions t ON t.txn_id = i.txn_id
        JOIN tenant_products p     ON p.sku    = i.sku
        WHERE t.merchant_id = 'KRG'
        GROUP BY p.category
        ORDER BY revenue DESC
    """)
    subs = _q(conn, """
        SELECT p.category, p.subcategory,
               ROUND(SUM(i.line_total), 2) AS revenue
        FROM tenant_transaction_items i
        JOIN tenant_transactions t ON t.txn_id = i.txn_id
        JOIN tenant_products p     ON p.sku    = i.sku
        WHERE t.merchant_id = 'KRG'
        GROUP BY p.category, p.subcategory
        ORDER BY p.category, revenue DESC
    """)
    out: list[dict[str, Any]] = []
    for _, c in cats.iterrows():
        cat = c["category"]
        sub_rows = subs[subs["category"] == cat]
        out.append({
            "category": cat,
            "revenue": float(c["revenue"]),
            "sku_count": int(c["sku_count"]),
            "subcategories": [
                {"subcategory": s["subcategory"], "revenue": float(s["revenue"])}
                for _, s in sub_rows.iterrows()
            ],
        })
    return out


def _daily_volume(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Per-day transaction counts. The report HTML hard-codes three series
    (kroger_count, tacobell_count, tjmaxx_count) — Phase 7 will widen this
    to all five merchants."""
    df = _q(conn, """
        SELECT DATE(txn_ts) AS date, merchant_id, COUNT(*) AS n
        FROM tenant_transactions
        GROUP BY DATE(txn_ts), merchant_id
        ORDER BY date
    """)
    pivot = df.pivot(index="date", columns="merchant_id", values="n").fillna(0).astype(int)
    out: list[dict[str, Any]] = []
    for date, row in pivot.iterrows():
        out.append({
            "date": date,
            "kroger_count":   int(row.get("KRG", 0)),
            "tacobell_count": int(row.get("TBL", 0)),
            "tjmaxx_count":   int(row.get("TJX", 0)),
        })
    return out


def _hour_distribution(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Share-of-day distribution: each merchant's transactions
    normalized to 100% across 24 hours."""
    df = _q(conn, """
        SELECT CAST(strftime('%H', txn_ts) AS INTEGER) AS hour,
               merchant_id, COUNT(*) AS n
        FROM tenant_transactions
        GROUP BY hour, merchant_id
    """)
    pivot = df.pivot(index="hour", columns="merchant_id", values="n").fillna(0)
    pcts = pivot.div(pivot.sum(axis=0), axis=1) * 100.0
    out: list[dict[str, Any]] = []
    for hour in range(24):
        row = pcts.loc[hour] if hour in pcts.index else None
        out.append({
            "hour": hour,
            "kroger_pct":   round(float(row["KRG"]), 2) if row is not None else 0.0,
            "tacobell_pct": round(float(row["TBL"]), 2) if row is not None else 0.0,
            "tjmaxx_pct":   round(float(row["TJX"]), 2) if row is not None else 0.0,
        })
    return out


def _customer_overlap(conn: sqlite3.Connection) -> dict[str, Any]:
    """Customer fan-out across merchants in the panel.

    `merchants_N` = customers active at exactly N merchants (0..5).
    `by_set` = customer counts keyed by sorted-merchant-id-tuple string.
    """
    df = _q(conn, """
        SELECT customer_id, GROUP_CONCAT(DISTINCT merchant_id) AS merchants
        FROM tenant_transactions
        GROUP BY customer_id
    """)
    counts: dict[int, int] = {0: 0, 1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    by_set: dict[str, int] = {}
    n_total = _scalar(conn, "SELECT COUNT(*) FROM tenant_customers")
    n_active = len(df)
    counts[0] = int(n_total - n_active)
    for _, row in df.iterrows():
        merchants = sorted((row["merchants"] or "").split(","))
        n = len(merchants)
        if n in counts:
            counts[n] += 1
        key = ",".join(merchants)
        by_set[key] = by_set.get(key, 0) + 1
    return {
        "merchants_0": counts[0],
        "merchants_1": counts[1],
        "merchants_2": counts[2],
        "merchants_3": counts[3],
        "merchants_4": counts[4],
        "merchants_5": counts[5],
        "by_set": by_set,
    }


def _pay_cycle(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Average daily transaction volume by day-of-month bucket. The
    1st-3rd / 15th-17th buckets are payday-adjacent; the report uses
    them to show that the panel responds to pay-cycle effects."""
    df = _q(conn, """
        SELECT CAST(strftime('%d', txn_ts) AS INTEGER) AS dom,
               DATE(txn_ts) AS day, merchant_id, COUNT(*) AS n
        FROM tenant_transactions
        GROUP BY day, merchant_id
    """)
    def bucket(dom: int) -> str:
        if 1 <= dom <= 3:
            return "1-3 (paychecks)"
        if 15 <= dom <= 17:
            return "15-17 (mid-month payday)"
        if 11 <= dom <= 13:
            return "11-13 (off-cycle)"
        return "other days"
    df["bucket"] = df["dom"].apply(bucket)
    avg_per_day = df.groupby(["bucket", "merchant_id"])["n"].mean().unstack(fill_value=0)
    order = ["1-3 (paychecks)", "15-17 (mid-month payday)", "11-13 (off-cycle)", "other days"]
    out: list[dict[str, Any]] = []
    for b in order:
        if b not in avg_per_day.index:
            continue
        row = avg_per_day.loc[b]
        out.append({
            "bucket": b,
            "kroger_avg":   round(float(row.get("KRG", 0)), 1),
            "tacobell_avg": round(float(row.get("TBL", 0)), 1),
            "tjmaxx_avg":   round(float(row.get("TJX", 0)), 1),
        })
    return out


def _example_transaction(conn: sqlite3.Connection) -> dict[str, Any]:
    txn = _q(conn, """
        SELECT t.txn_id, t.merchant_id, m.name AS merchant_name,
               t.store_id, t.txn_ts, t.payment_type, t.card_network,
               t.entry_mode, t.wallet_type, t.txn_total
        FROM tenant_transactions t
        JOIN merchants m ON m.merchant_id = t.merchant_id
        WHERE t.merchant_id = 'KRG'
        ORDER BY t.txn_total DESC
        LIMIT 1
    """).iloc[0]
    items = _q(conn, """
        SELECT i.line_id, i.sku, p.name, p.subcategory,
               i.qty, i.unit_price, i.line_total
        FROM tenant_transaction_items i
        JOIN tenant_products p ON p.sku = i.sku
        WHERE i.txn_id = :tid
        ORDER BY i.line_id
    """, tid=txn["txn_id"])
    return {
        "txn_id": txn["txn_id"],
        "merchant_id": txn["merchant_id"],
        "merchant_name": txn["merchant_name"],
        "store_id": txn["store_id"],
        "txn_ts": txn["txn_ts"],
        "payment_type": txn["payment_type"],
        "card_network": txn["card_network"],
        "entry_mode": txn["entry_mode"],
        "wallet_type": txn["wallet_type"],
        "txn_total": float(txn["txn_total"]),
        "items": [
            {
                "line_id":    int(r["line_id"]),
                "sku":        r["sku"],
                "name":       r["name"],
                "subcategory": r["subcategory"],
                "qty":        int(r["qty"]),
                "unit_price": float(r["unit_price"]),
                "line_total": float(r["line_total"]),
            }
            for _, r in items.iterrows()
        ],
    }


def _privacy_demo(conn: sqlite3.Connection) -> dict[str, Any]:
    """v2.5 privacy walkthrough.

    Replaces the v2 ``_anonymization_demo`` (which showed PII being
    stripped). In v2.5 no PII ever exists in the generated data — the
    privacy story is the per-row generalization the lake view-builders
    apply. This payload shows one tenant customer + one tenant
    transaction alongside what would surface in the lake (peer label,
    opaque IDs, ZIP3, hour bucket, total bin, no customer_id).
    """
    cust = _q(conn, """
        SELECT customer_id, home_zip5, behavioral_segment,
               grocer_affinity_type, primary_grocer
        FROM tenant_customers
        ORDER BY customer_id
        LIMIT 1
    """).iloc[0]
    txn = _q(conn, """
        SELECT t.txn_id, t.merchant_id, t.store_id,
               t.txn_ts, t.txn_total
        FROM tenant_transactions t
        WHERE t.merchant_id = 'KRG'
        ORDER BY t.txn_id
        LIMIT 1
    """).iloc[0]
    return {
        "raw": {
            "customer_id": cust["customer_id"],
            "home_zip5":   cust["home_zip5"],
            "txn_id":      txn["txn_id"],
            "merchant_id": txn["merchant_id"],
            "store_id":    txn["store_id"],
            "txn_ts":      txn["txn_ts"],
            "txn_total":   float(txn["txn_total"]),
        },
        "tenant": {
            "customer_id": cust["customer_id"],
            "home_zip5":   cust["home_zip5"],
            "txn_id":      txn["txn_id"],
            "merchant_id": txn["merchant_id"],
            "store_id":    txn["store_id"],
            "txn_ts":      txn["txn_ts"],
            "txn_total":   float(txn["txn_total"]),
        },
        "lake": {
            "customer_id": "(dropped — no consumer linkage)",
            "home_zip3":   str(cust["home_zip5"])[:3],
            "lake_txn_id": "<opaque 16-char id>",
            "peer_id":     "peer_a..peer_d (per viewer)",
            "lake_store_id": "<opaque 16-char id>",
            "txn_date":    str(txn["txn_ts"])[:10],
            "txn_hour_bucket": "morning / lunch / dinner / ...",
            "txn_total_bin":   "$0-5 / $5-10 / ... / $250+",
        },
    }


def _cross_merchant_finding(
    conn: sqlite3.Connection, window: dict[str, Any]
) -> dict[str, Any]:
    """Customers active at >=3 merchants in the last 30 days, with
    average spend at each. The agent SQL shown in the report uses the
    v2.5 view-builder shape (peer_id, no customer_id) so readers see
    what the lake actually exposes.
    """
    end = window["end"]
    start_30d = (datetime.fromisoformat(end).toordinal() - 29)
    start_30d_iso = datetime.fromordinal(start_30d).date().isoformat()

    # Tenant-side panel computation for the headline number.
    multi = _q(conn, """
        SELECT customer_id, COUNT(DISTINCT merchant_id) AS n
        FROM tenant_transactions
        WHERE DATE(txn_ts) >= :start
        GROUP BY customer_id
        HAVING n >= 3
    """, start=start_30d_iso)
    n_multi = int(len(multi))

    avg_spend: dict[str, float] = {}
    if n_multi:
        ids = list(multi["customer_id"])
        placeholders = ",".join("?" for _ in ids)
        sql = f"""
            SELECT m.merchant_id, ROUND(AVG(per_cust_total), 2) AS avg_spend
            FROM (
                SELECT t.customer_id, t.merchant_id, SUM(t.txn_total) AS per_cust_total
                FROM tenant_transactions t
                WHERE DATE(t.txn_ts) >= ?
                  AND t.customer_id IN ({placeholders})
                GROUP BY t.customer_id, t.merchant_id
            ) sub
            JOIN merchants m USING (merchant_id)
            GROUP BY m.merchant_id
        """
        avg_df = pd.read_sql_query(sql, conn, params=[start_30d_iso, *ids])
        avg_spend = {
            "kroger":   float(avg_df.loc[avg_df["merchant_id"] == "KRG", "avg_spend"].iloc[0])
                          if "KRG" in avg_df["merchant_id"].values else 0.0,
            "tacobell": float(avg_df.loc[avg_df["merchant_id"] == "TBL", "avg_spend"].iloc[0])
                          if "TBL" in avg_df["merchant_id"].values else 0.0,
            "tjmaxx":   float(avg_df.loc[avg_df["merchant_id"] == "TJX", "avg_spend"].iloc[0])
                          if "TJX" in avg_df["merchant_id"].values else 0.0,
        }

    agent_sql = f"""-- query_lake (Kroger's view; runner CTE-wraps lake_transactions
--                from the tenant tables and excludes KRG's own rows)
SELECT peer_id,
       peer_segment,
       ROUND(AVG(unit_price), 2)      AS peer_avg_unit_price,
       COUNT(*)                       AS lines
FROM lake_transactions
WHERE category = 'DAIRY'
  AND txn_date >= '{start_30d_iso}'
GROUP BY peer_id, peer_segment
ORDER BY peer_id"""

    return {
        "n_customers_all_three": n_multi,
        "window_days": 30,
        "window_start": start_30d_iso,
        "avg_spend_30d": avg_spend,
        "agent_sql": agent_sql,
    }


def _basket_comparison_sql() -> str:
    """The headline ``query_lake`` example as the agent would write it.
    The runner CTE-wraps the SELECT so ``lake_transactions`` resolves
    against the v2.5 virtual lake (built from tenant)."""
    return """-- query_lake (Kroger's view)
WITH per_txn AS (
  SELECT lake_txn_id, peer_id, peer_segment,
         SUM(qty) AS items_per_txn
  FROM lake_transactions
  GROUP BY lake_txn_id, peer_id, peer_segment
)
SELECT peer_id, peer_segment,
       ROUND(AVG(items_per_txn), 2) AS avg_basket_size
FROM per_txn
WHERE peer_segment = 'grocery'
GROUP BY peer_id
ORDER BY avg_basket_size DESC;"""


def _affinity_pairs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Top item co-purchase pairs at each grocer. Computed from
    transaction items by anchor SKU; we surface a small curated list
    so the report's affinity grid stays readable."""
    out: list[dict[str, Any]] = []
    grocers = [("KRG", "Kroger"), ("ACM", "Acme"), ("WDX", "Winn-Dixie")]
    for mid, name in grocers:
        anchors = _q(conn, """
            SELECT p.sku, p.name, COUNT(*) AS n
            FROM tenant_transaction_items i
            JOIN tenant_transactions t ON t.txn_id = i.txn_id
            JOIN tenant_products p     ON p.sku    = i.sku
            WHERE t.merchant_id = :m
            GROUP BY p.sku
            ORDER BY n DESC
            LIMIT 3
        """, m=mid)
        for _, a in anchors.iterrows():
            companions = _q(conn, """
                SELECT p2.name AS companion, COUNT(*) AS co
                FROM tenant_transaction_items i1
                JOIN tenant_transaction_items i2 ON i1.txn_id = i2.txn_id AND i1.sku <> i2.sku
                JOIN tenant_products p2 ON p2.sku = i2.sku
                JOIN tenant_transactions t ON t.txn_id = i1.txn_id
                WHERE i1.sku = :sku AND t.merchant_id = :m
                GROUP BY p2.sku
                ORDER BY co DESC
                LIMIT 1
            """, sku=a["sku"], m=mid)
            if companions.empty:
                continue
            comp = companions.iloc[0]
            prob = round(float(comp["co"]) / float(a["n"]), 2) if a["n"] else 0.0
            out.append({
                "merchant": name,
                "anchor": a["name"],
                "companion": comp["companion"],
                "prob": prob,
            })
    return out


def _schema(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """v2.5 schema as the report renders it — the lake is a virtual
    layer (no physical lake_* tables), so the lake group enumerates the
    two view-builder outputs from `src.lake.views`."""
    out: list[dict[str, Any]] = []
    physical_tables = [
        ("merchants", "shared"),
        ("tenant_customers", "tenant"),
        ("tenant_stores", "tenant"),
        ("tenant_products", "tenant"),
        ("tenant_promotions", "tenant"),
        ("tenant_transactions", "tenant"),
        ("tenant_transaction_items", "tenant"),
    ]
    for table, layer in physical_tables:
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info('{table}')")]
        out.append({"table": table, "layer": layer, "columns": cols})

    # Virtual lake views (computed in src/lake/views.py).
    out.append({
        "table": "lake_transactions",
        "layer": "lake",
        "columns": [
            "lake_txn_id", "line_id", "peer_id", "peer_segment",
            "lake_store_id", "txn_date", "txn_hour_bucket",
            "payment_type", "card_network", "entry_mode", "wallet_type",
            "connectivity_type", "txn_total_bin",
            "canonical_name", "category", "subcategory",
            "unit_price", "qty", "discount", "line_total",
            "discount_pct_applied",
        ],
    })
    out.append({
        "table": "lake_stores",
        "layer": "lake",
        "columns": [
            "lake_store_id", "peer_id", "peer_segment",
            "store_zip3", "neighborhood", "metro_region",
        ],
    })
    return out


def _promo_days(conn: sqlite3.Connection) -> list[str]:
    """Up to three promotion start dates that fall inside the window —
    used by the daily-volume chart to mark promo bumps."""
    df = _q(conn, """
        SELECT DISTINCT start_date FROM tenant_promotions
        ORDER BY start_date
        LIMIT 3
    """)
    return [r["start_date"] for _, r in df.iterrows()]


def _anomaly_window(conn: sqlite3.Connection) -> dict[str, str]:
    """The deepest stage of the University City decline (stage 3,
    Apr 26 – May 2). The daily-volume chart brackets this window to
    surface the planted dropoff."""
    return {"start": "2026-04-26", "end": "2026-05-02"}


def _anomaly_callouts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Three planted Phase 6 signals with the numbers as observed in
    the seeded DB. Each callout carries enough context for the report
    to render a one-line headline + a backing query."""
    callouts: list[dict[str, Any]] = []

    # 1) University City decline — stage-3 traffic vs pre-anomaly baseline.
    uc_df = _q(conn, """
        SELECT t.merchant_id, DATE(t.txn_ts) AS day, COUNT(*) AS n
        FROM tenant_transactions t
        JOIN tenant_stores s ON s.store_id = t.store_id
        WHERE s.neighborhood = 'University City'
          AND t.merchant_id IN ('KRG','ACM','WDX')
        GROUP BY t.merchant_id, DATE(t.txn_ts)
    """)
    uc_df["day"] = pd.to_datetime(uc_df["day"])
    pre = uc_df[uc_df["day"] < "2026-04-12"]
    s3 = uc_df[(uc_df["day"] >= "2026-04-26") & (uc_df["day"] <= "2026-05-02")]
    pre_avg = pre.groupby("merchant_id")["n"].mean()
    s3_avg = s3.groupby("merchant_id")["n"].mean()
    by_grocer = {
        mid: {
            "pre_anomaly_avg_per_day":  round(float(pre_avg.get(mid, 0)), 1),
            "stage3_avg_per_day":       round(float(s3_avg.get(mid, 0)), 1),
            "stage3_ratio":             round(float(s3_avg.get(mid, 0) / pre_avg.get(mid, 1)), 2)
                                          if pre_avg.get(mid, 0) > 0 else 0.0,
        }
        for mid in ("KRG", "ACM", "WDX")
    }
    callouts.append({
        "id": "university_city_decline",
        "title": "University City decline — KRG / ACM / WDX",
        "headline": (
            "Per-grocer foot-traffic decline at University City stores: "
            f"Kroger {by_grocer['KRG']['stage3_ratio']:.2f}×, "
            f"Acme {by_grocer['ACM']['stage3_ratio']:.2f}×, "
            f"Winn-Dixie {by_grocer['WDX']['stage3_ratio']:.2f}× "
            "in the Apr 26 – May 2 window vs Mar 1 – Apr 11 baseline."
        ),
        "stages": [
            {"window": "Apr 12 – Apr 18", "name": "stage 1: finals stress",   "base_multiplier": 1.10},
            {"window": "Apr 19 – Apr 25", "name": "stage 2: move-out",         "base_multiplier": 0.85},
            {"window": "Apr 26 – May 2",  "name": "stage 3: full crash",       "base_multiplier": 0.55},
            {"window": "May 3 – May 29",  "name": "stage 4: summer stable",    "base_multiplier": 0.65},
        ],
        "by_grocer": by_grocer,
        "sql": (
            "SELECT s.merchant_id, COUNT(*) AS txns\n"
            "FROM tenant_transactions t\n"
            "JOIN tenant_stores s ON s.store_id = t.store_id\n"
            "WHERE s.neighborhood = 'University City'\n"
            "  AND t.txn_ts BETWEEN '2026-04-26' AND '2026-05-02 23:59:59'\n"
            "GROUP BY s.merchant_id;"
        ),
    })

    # 2) Plaza Midwood Kroger avocado spike — daily qty around the window.
    avo_df = _q(conn, """
        SELECT t.merchant_id, DATE(t.txn_ts) AS day, SUM(i.qty) AS qty
        FROM tenant_transaction_items i
        JOIN tenant_transactions t ON t.txn_id = i.txn_id
        JOIN tenant_stores s       ON s.store_id = t.store_id
        JOIN tenant_products p     ON p.sku = i.sku
        WHERE s.neighborhood = 'Plaza Midwood'
          AND t.merchant_id = 'KRG'
          AND p.category = 'PRODUCE'
          AND p.name LIKE '%vocado%'
          AND DATE(t.txn_ts) BETWEEN '2026-04-15' AND '2026-04-26'
        GROUP BY t.merchant_id, DATE(t.txn_ts)
        ORDER BY day
    """)
    callouts.append({
        "id": "plaza_midwood_avocado",
        "title": "Plaza Midwood Kroger — avocado quantity spike",
        "headline": (
            "Avocado units at Kroger Plaza Midwood peak Apr 22 (5× design "
            "multiplier on PRODUCE selection), trailing off through Apr 24."
        ),
        "windows": {"start": "2026-04-21", "peak": "2026-04-22", "end": "2026-04-24"},
        "daily_qty": [
            {"day": str(r["day"]), "qty": int(r["qty"])} for _, r in avo_df.iterrows()
        ],
        "sql": (
            "SELECT DATE(t.txn_ts) AS day, SUM(i.qty) AS qty\n"
            "FROM tenant_transaction_items i\n"
            "JOIN tenant_transactions t ON t.txn_id = i.txn_id\n"
            "JOIN tenant_stores s       ON s.store_id = t.store_id\n"
            "JOIN tenant_products p     ON p.sku = i.sku\n"
            "WHERE s.store_zip5 = '28205' AND t.merchant_id = 'KRG'\n"
            "  AND p.category = 'PRODUCE' AND p.name LIKE '%vocado%'\n"
            "GROUP BY DATE(t.txn_ts) ORDER BY day;"
        ),
    })

    # 3) Pasta promos — in-window vs off-window line counts per grocer.
    pasta_df = _q(conn, """
        SELECT t.merchant_id, DATE(t.txn_ts) AS day, COUNT(*) AS n
        FROM tenant_transaction_items i
        JOIN tenant_transactions t ON t.txn_id = i.txn_id
        JOIN tenant_products p     ON p.sku = i.sku
        WHERE p.subcategory = 'pasta' AND t.merchant_id IN ('KRG','ACM','WDX')
        GROUP BY t.merchant_id, DATE(t.txn_ts)
    """)
    pasta_df["day"] = pd.to_datetime(pasta_df["day"])

    rules = [
        ("KRG", "Kroger Pasta Sale",        "2026-04-15", "2026-04-21", 0.25, 2.2,  "lift"),
        ("ACM", "Acme Spring Pasta Sale",   "2026-04-19", "2026-04-25", 0.20, 0.8,  "fail"),
        ("WDX", "Winn-Dixie Pasta Sale",    "2026-04-22", "2026-04-28", 0.15, 1.4,  "lift"),
    ]
    promos: list[dict[str, Any]] = []
    for mid, name, start, end, pct, target, kind in rules:
        m = pasta_df[pasta_df["merchant_id"] == mid].set_index("day")["n"]
        # Off-window baseline excludes ALL three grocers' promo windows so
        # cross-merchant overlap does not pollute the comparison.
        off_mask = pd.Series(True, index=m.index)
        for _, _, s2, e2, _, _, _ in rules:
            off_mask &= ~((m.index >= s2) & (m.index <= e2))
        in_window_avg = float(m.loc[start:end].mean()) if not m.loc[start:end].empty else 0.0
        baseline_avg = float(m[off_mask].mean()) if off_mask.any() else 0.0
        ratio = in_window_avg / baseline_avg if baseline_avg else 0.0
        promos.append({
            "merchant_id":  mid,
            "promo_name":   name,
            "start":        start,
            "end":          end,
            "discount_pct": pct,
            "design_target_ratio": target,
            "observed_ratio":      round(ratio, 2),
            "kind":         kind,
            "in_window_avg_lines_per_day": round(in_window_avg, 1),
            "baseline_avg_lines_per_day":  round(baseline_avg, 1),
        })
    callouts.append({
        "id": "acme_pasta_promo",
        "title": "Pasta promos — coordinated, divergent outcomes",
        "headline": (
            "Three grocers ran pasta promos in late April. Kroger's lifted, "
            "Winn-Dixie's lifted modestly, Acme's depressed sales during the "
            "promo (the planted failure)."
        ),
        "promos": promos,
        "sql": (
            "SELECT t.merchant_id, COUNT(*) AS pasta_lines\n"
            "FROM tenant_transaction_items i\n"
            "JOIN tenant_transactions t ON t.txn_id = i.txn_id\n"
            "JOIN tenant_products p     ON p.sku = i.sku\n"
            "WHERE p.subcategory = 'pasta'\n"
            "  AND DATE(t.txn_ts) BETWEEN '2026-04-15' AND '2026-04-28'\n"
            "GROUP BY t.merchant_id;"
        ),
    })

    return callouts


# ===========================================================================
# Phase A — V2.5 report rewrite payload extensions
# (see docs/V2_5_REPORT_PLAN.md for the contract; these are additive to the
# existing builders above and feed the new editorial report sections)
# ===========================================================================

def _stores(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Per-store metadata for the Charlotte map (§1.1). One row per store."""
    df = _q(conn, """
        SELECT store_id, merchant_id, latitude, longitude,
               neighborhood, metro_region, store_zip5
        FROM tenant_stores
        ORDER BY merchant_id, store_id
    """)
    return [
        {
            "store_id":     r["store_id"],
            "merchant_id":  r["merchant_id"],
            "latitude":     float(r["latitude"]),
            "longitude":    float(r["longitude"]),
            "neighborhood": r["neighborhood"],
            "metro_region": r["metro_region"],
            "store_zip5":   r["store_zip5"],
        }
        for _, r in df.iterrows()
    ]


def _customer_breakdown(conn: sqlite3.Connection) -> dict[str, Any]:
    """Customer panel splits for §1.2.

    Behavioral segment (filler/stocker), grocer affinity type (loyalist/
    splitter/three_chain/lapsed_light), primary grocer share among
    grocery-affinity customers, mobile-wallet adoption, primary card
    type, plus the **affinity × primary_grocer cross-tab** that feeds
    the §1.2 Sankey diagram.
    """
    def _counts(col: str) -> dict[str, int]:
        df = _q(conn, f"SELECT {col} AS k, COUNT(*) AS n FROM tenant_customers GROUP BY {col}")
        return {str(r["k"]): int(r["n"]) for _, r in df.iterrows()}

    wallet_pct = _scalar(conn,
        "SELECT ROUND(100.0 * AVG(has_mobile_wallet), 1) FROM tenant_customers")

    # Joint distribution: rows = affinity, cols = primary_grocer.
    cross = _q(conn, """
        SELECT grocer_affinity_type AS affinity,
               primary_grocer       AS grocer,
               COUNT(*)              AS n
        FROM tenant_customers
        GROUP BY grocer_affinity_type, primary_grocer
    """)
    affinity_by_grocer: dict[str, dict[str, int]] = {}
    for _, r in cross.iterrows():
        affinity_by_grocer.setdefault(str(r["affinity"]), {})[str(r["grocer"])] = int(r["n"])

    return {
        "behavioral_segment": _counts("behavioral_segment"),
        "affinity":           _counts("grocer_affinity_type"),
        "primary_grocer":     _counts("primary_grocer"),
        "card_type":          _counts("primary_card_type"),
        "has_mobile_wallet_pct": float(wallet_pct or 0),
        "affinity_by_grocer": affinity_by_grocer,
    }


def _customer_zip_distribution(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Per-neighborhood customer counts for §1.2's geographic distribution.
    Aggregated by neighborhood (not raw ZIP) because the panel sized
    by-ZIP has too many small bars to read.
    """
    df = _q(conn, """
        SELECT s.neighborhood, s.metro_region, COUNT(c.customer_id) AS n
        FROM tenant_customers c
        JOIN (
            SELECT DISTINCT store_zip5, neighborhood, metro_region
            FROM tenant_stores
        ) s ON s.store_zip5 = c.home_zip5
        GROUP BY s.neighborhood, s.metro_region
        ORDER BY n DESC
    """)
    return [
        {
            "neighborhood": r["neighborhood"],
            "metro_region": r["metro_region"],
            "n_customers":  int(r["n"]),
        }
        for _, r in df.iterrows()
    ]


def _cohorts(conn: sqlite3.Connection) -> dict[str, int]:
    """Cross-merchant cohort sizes for §1.2's headline numbers."""
    def _n(sql: str) -> int:
        return int(_scalar(conn, sql))

    return {
        "n_at_all_3_grocers": _n("""
            SELECT COUNT(*) FROM (
              SELECT customer_id FROM tenant_transactions
              WHERE merchant_id IN ('KRG','ACM','WDX')
              GROUP BY customer_id HAVING COUNT(DISTINCT merchant_id) = 3
            )
        """),
        "n_at_any_2_grocers": _n("""
            SELECT COUNT(*) FROM (
              SELECT customer_id FROM tenant_transactions
              WHERE merchant_id IN ('KRG','ACM','WDX')
              GROUP BY customer_id HAVING COUNT(DISTINCT merchant_id) >= 2
            )
        """),
        "n_at_grocer_and_qsr": _n("""
            SELECT COUNT(*) FROM (
              SELECT customer_id FROM tenant_transactions
              GROUP BY customer_id
              HAVING SUM(CASE WHEN merchant_id IN ('KRG','ACM','WDX') THEN 1 ELSE 0 END) > 0
                 AND SUM(CASE WHEN merchant_id = 'TBL' THEN 1 ELSE 0 END) > 0
            )
        """),
        "n_at_grocer_and_retail": _n("""
            SELECT COUNT(*) FROM (
              SELECT customer_id FROM tenant_transactions
              GROUP BY customer_id
              HAVING SUM(CASE WHEN merchant_id IN ('KRG','ACM','WDX') THEN 1 ELSE 0 END) > 0
                 AND SUM(CASE WHEN merchant_id = 'TJX' THEN 1 ELSE 0 END) > 0
            )
        """),
        "n_at_all_5": _n("""
            SELECT COUNT(*) FROM (
              SELECT customer_id FROM tenant_transactions
              GROUP BY customer_id HAVING COUNT(DISTINCT merchant_id) = 5
            )
        """),
    }


def _catalog_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    """Grocer catalog comparison for §1.3.

    Per-grocer counts come from `tenant_products`; overlap math is computed
    from the canonical SKU prefixes (`<MERCHANT>-PRODUCE-0017`, etc.) by
    stripping the merchant prefix. Tier multipliers come from the overlay
    JSON files — single source of truth.
    """
    overlays_dir = ROOT / "data" / "catalogs" / "overlays"

    per_grocer: list[dict[str, Any]] = []
    canonical_by_merchant: dict[str, set] = {}
    for key, mid in [("kroger", "KRG"), ("acme", "ACM"), ("winn_dixie", "WDX")]:
        overlay = json.loads((overlays_dir / f"{key}.json").read_text())
        canonical_by_merchant[mid] = set(overlay["included_canonical_skus"])
        # _scalar uses **kwargs; positional parameter binding goes through
        # conn.execute directly for these simple "WHERE merchant_id = ?".
        n_skus = conn.execute(
            "SELECT COUNT(*) FROM tenant_products WHERE merchant_id = ?", (mid,)
        ).fetchone()[0]
        n_cats = conn.execute(
            "SELECT COUNT(DISTINCT category) FROM tenant_products WHERE merchant_id = ?",
            (mid,),
        ).fetchone()[0]
        n_subs = conn.execute(
            "SELECT COUNT(DISTINCT subcategory) FROM tenant_products WHERE merchant_id = ?",
            (mid,),
        ).fetchone()[0]
        per_grocer.append({
            "merchant_id":      mid,
            "n_skus":           int(n_skus),
            "n_categories":     int(n_cats),
            "n_subcategories":  int(n_subs),
            "tight_multiplier": float(overlay["tight_multiplier"]),
            "loose_multiplier": float(overlay["loose_multiplier"]),
        })

    k = canonical_by_merchant["KRG"]
    a = canonical_by_merchant["ACM"]
    w = canonical_by_merchant["WDX"]
    overlap = {
        "shared_all_3":   len(k & a & w),
        "krg_only":       len(k - a - w),
        "acm_only":       len(a - k - w),
        "wdx_only":       len(w - k - a),
        "krg_acm_only":   len((k & a) - w),
        "krg_wdx_only":   len((k & w) - a),
        "acm_wdx_only":   len((a & w) - k),
        "union":          len(k | a | w),
    }
    return {"per_grocer": per_grocer, "overlap": overlap}


def _catalog_category_breakdown(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Per-category SKU counts for the §1.3 category-breakdown chart.

    Uses Kroger's catalog as the primary view (largest of the three;
    1,112 SKUs across 12 categories). Acme and Winn-Dixie carry
    near-identical category distributions on the subset they include.
    """
    df = _q(conn, """
        SELECT category,
               COUNT(*)                  AS n_skus,
               COUNT(DISTINCT subcategory) AS n_subcategories
        FROM tenant_products
        WHERE merchant_id = 'KRG'
        GROUP BY category
        ORDER BY n_skus DESC
    """)
    return [
        {
            "category":        r["category"],
            "n_skus":          int(r["n_skus"]),
            "n_subcategories": int(r["n_subcategories"]),
        }
        for _, r in df.iterrows()
    ]


def _catalog_sample_products(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Eight curated example products that illustrate the catalog's breadth.

    Each row resolves to a real Kroger SKU via category + name keyword;
    the keyword picks one canonical product per category. The intent is
    representative coverage of grocery shopping, not a randomized sample.
    """
    targets: list[tuple[str, str]] = [
        # (category, name keyword — case-insensitive substring on canonical name)
        ("DAIRY",     "whole milk (gallon)"),
        ("PRODUCE",   "bananas (lb)"),
        ("MEAT",      "80/20 ground beef"),
        ("BAKERY",    "sourdough sliced loaf"),
        ("PANTRY",    "spaghetti (1 lb box)"),
        ("FROZEN",    "frozen cheese pizza"),
        ("HOUSEHOLD", "paper towels (6 mega rolls)"),
        ("BABY",      "diapers size 3 huggies"),
    ]
    out: list[dict[str, Any]] = []
    for category, kw in targets:
        row = conn.execute(
            """
            SELECT name, category, subcategory, base_price
            FROM tenant_products
            WHERE merchant_id = 'KRG'
              AND category   = ?
              AND LOWER(name) LIKE ?
            ORDER BY LENGTH(name)
            LIMIT 1
            """,
            (category, "%" + kw.lower() + "%"),
        ).fetchone()
        if row is None:
            # Fallback: first SKU in category (so the table always has 8 rows).
            row = conn.execute(
                "SELECT name, category, subcategory, base_price "
                "FROM tenant_products WHERE merchant_id='KRG' AND category=? "
                "ORDER BY base_price LIMIT 1",
                (category,),
            ).fetchone()
        if row is not None:
            out.append({
                "name":        row[0],
                "category":    row[1],
                "subcategory": row[2],
                "base_price":  float(row[3]),
            })
    return out


def _daily_volume_5merchant(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """5-merchant version of `_daily_volume` for §1.5. One row per date
    with one column per merchant_id."""
    df = _q(conn, """
        SELECT DATE(txn_ts) AS date, merchant_id, COUNT(*) AS n
        FROM tenant_transactions
        GROUP BY DATE(txn_ts), merchant_id
        ORDER BY date
    """)
    pivot = df.pivot(index="date", columns="merchant_id", values="n").fillna(0).astype(int)
    out: list[dict[str, Any]] = []
    for date, row in pivot.iterrows():
        out.append({
            "date": date,
            "KRG":  int(row.get("KRG", 0)),
            "ACM":  int(row.get("ACM", 0)),
            "WDX":  int(row.get("WDX", 0)),
            "TBL":  int(row.get("TBL", 0)),
            "TJX":  int(row.get("TJX", 0)),
        })
    return out


def _anatomy_table() -> list[dict[str, Any]]:
    """The 8-row schema table for §1.4.

    Each row enumerates one category of data captured at the moment of
    sale. The data_element column names the actual columns stored in
    the v2.5 tenant tables (`tenant_transactions`,
    `tenant_transaction_items`, `tenant_stores`, `tenant_customers`,
    `merchants`). The privacy_treatment column describes the
    transformation applied by the lake view-builders in
    `src/lake/views.py`.
    """
    return [
        {
            "category":           "Transaction",
            "data_element":       "subtotal, tax_total, txn_total",
            "source":             "Payment Kernel",
            "privacy_treatment":  "txn_total binned to 10 ranges ($0-5 through $250+) in lake; subtotal and tax_total not exposed",
        },
        {
            "category":           "Instrument",
            "data_element":       "payment_type (credit/debit), card_network (visa/mc/amex/discover), entry_mode (chip/contactless/swipe/manual)",
            "source":             "Payment Kernel",
            "privacy_treatment":  "Category-level only at capture; no card details, BIN, or PAN ever stored",
        },
        {
            "category":           "Mobile Wallet",
            "data_element":       "wallet_type (apple/google/samsung)",
            "source":             "NFC subsystem",
            "privacy_treatment":  "Wallet provider flag only; no wallet credentials or device IDs",
        },
        {
            "category":           "Basket / SKU",
            "data_element":       "sku, canonical name, category, subcategory, qty, unit_price, discount, tax, line_total, promo_id",
            "source":             "POS API Bridge",
            "privacy_treatment":  "Product-level carried; canonical names enable cross-merchant matching; no consumer linkage",
        },
        {
            "category":           "Merchant",
            "data_element":       "merchant_id, mcc, store_id, store_zip5, neighborhood, latitude, longitude, terminal_id",
            "source":             "Device Agent config",
            "privacy_treatment":  "Lat/long dropped; ZIP5 → ZIP3; merchant_id → peer pseudonym; terminal_id excluded from lake",
        },
        {
            "category":           "Temporal",
            "data_element":       "txn_ts (full UTC timestamp)",
            "source":             "System clock",
            "privacy_treatment":  "Generalized to date + 2-hour bucket",
        },
        {
            "category":           "Cardholder",
            "data_element":       "customer_id (16-char SHA-256 hash)",
            "source":             "Security Layer",
            "privacy_treatment":  "Tokenized at generation; dropped entirely from lake",
        },
        {
            "category":           "Device",
            "data_element":       "connectivity_type (wifi/cellular_4g/cellular_5g/ethernet)",
            "source":             "Device Agent",
            "privacy_treatment":  "Operational data; carried to lake unchanged",
        },
    ]


def _anomaly_series(conn: sqlite3.Connection) -> dict[str, Any]:
    """Extended time series for the three Phase 6 anomaly charts (§1.6).

    Distinct from `_anomaly_callouts`: that one carries the narrative
    headlines and per-anomaly summary numbers. This one carries the
    arrays of points the charts plot directly.
    """
    # 1) University City — per-stage avg-txns-per-STORE-per-day per grocer.
    # KRG has 2 UC stores while ACM and WDX have 3. Aggregating raw totals
    # confuses readers because KRG's lower denominator depresses its bars
    # for store-count reasons rather than per-store decline. Group by
    # (merchant_id, store_id, day) and take the mean over (store, day)
    # pairs in each window — that's the true average daily transactions
    # per store.
    uc_df = _q(conn, """
        SELECT t.merchant_id, t.store_id, DATE(t.txn_ts) AS day, COUNT(*) AS n
        FROM tenant_transactions t
        JOIN tenant_stores s ON s.store_id = t.store_id
        WHERE s.neighborhood = 'University City'
          AND t.merchant_id IN ('KRG','ACM','WDX')
        GROUP BY t.merchant_id, t.store_id, DATE(t.txn_ts)
    """)
    uc_df["day"] = pd.to_datetime(uc_df["day"])
    stage_ranges = [
        ("Baseline (Mar 1 – Apr 11)",   "2026-03-01", "2026-04-11"),
        ("Stage 1 (Apr 12 – Apr 18)",   "2026-04-12", "2026-04-18"),
        ("Stage 2 (Apr 19 – Apr 25)",   "2026-04-19", "2026-04-25"),
        ("Stage 3 (Apr 26 – May 2)",    "2026-04-26", "2026-05-02"),
        ("Stage 4 (May 3 – May 29)",    "2026-05-03", "2026-05-29"),
    ]
    uc_stages: list[dict[str, Any]] = []
    baseline_by_grocer: dict[str, float] = {}
    for label, s, e in stage_ranges:
        window = uc_df[(uc_df["day"] >= s) & (uc_df["day"] <= e)]
        by_grocer = window.groupby("merchant_id")["n"].mean().round(2).to_dict()
        krg = float(by_grocer.get("KRG", 0))
        acm = float(by_grocer.get("ACM", 0))
        wdx = float(by_grocer.get("WDX", 0))
        if label.startswith("Baseline"):
            baseline_by_grocer = {"KRG": krg, "ACM": acm, "WDX": wdx}
        uc_stages.append({
            "stage":      label,
            "window":     f"{s} → {e}",
            "KRG":        krg,
            "ACM":        acm,
            "WDX":        wdx,
        })

    # Per-grocer per-store ratio at Stage 3 vs baseline (annotated on chart).
    stage3 = next(s for s in uc_stages if s["stage"].startswith("Stage 3"))
    uc_ratios_stage3 = {
        mid: round(stage3[mid] / baseline_by_grocer[mid], 2)
                if baseline_by_grocer.get(mid) else None
        for mid in ("KRG", "ACM", "WDX")
    }

    # 2) Plaza Midwood avocado — daily qty per grocer (KRG/ACM/WDX) Apr 15–26.
    avo_df = _q(conn, """
        SELECT t.merchant_id, DATE(t.txn_ts) AS day, SUM(i.qty) AS qty
        FROM tenant_transaction_items i
        JOIN tenant_transactions t ON t.txn_id = i.txn_id
        JOIN tenant_stores s       ON s.store_id = t.store_id
        JOIN tenant_products p     ON p.sku = i.sku
        WHERE s.neighborhood = 'Plaza Midwood'
          AND t.merchant_id IN ('KRG','ACM','WDX')
          AND p.category = 'PRODUCE'
          AND p.name LIKE '%vocado%'
          AND DATE(t.txn_ts) BETWEEN '2026-04-15' AND '2026-04-26'
        GROUP BY t.merchant_id, DATE(t.txn_ts)
    """)
    days = sorted({str(d) for d in avo_df["day"]})
    avo_series: list[dict[str, Any]] = []
    for d in days:
        row = {"day": d}
        for mid in ("KRG", "ACM", "WDX"):
            cell = avo_df[(avo_df["merchant_id"] == mid) & (avo_df["day"] == d)]
            row[mid] = int(cell["qty"].iloc[0]) if not cell.empty else 0
        avo_series.append(row)

    # 3) Pasta promos — daily line counts per grocer, Apr 14 – Apr 29.
    pasta_df = _q(conn, """
        SELECT t.merchant_id, DATE(t.txn_ts) AS day, COUNT(*) AS n
        FROM tenant_transaction_items i
        JOIN tenant_transactions t ON t.txn_id = i.txn_id
        JOIN tenant_products p     ON p.sku = i.sku
        WHERE p.subcategory = 'pasta'
          AND t.merchant_id IN ('KRG','ACM','WDX')
          AND DATE(t.txn_ts) BETWEEN '2026-04-14' AND '2026-04-29'
        GROUP BY t.merchant_id, DATE(t.txn_ts)
    """)
    pasta_days = sorted({str(d) for d in pasta_df["day"]})
    pasta_series: list[dict[str, Any]] = []
    for d in pasta_days:
        row = {"day": d}
        for mid in ("KRG", "ACM", "WDX"):
            cell = pasta_df[(pasta_df["merchant_id"] == mid) & (pasta_df["day"] == d)]
            row[mid] = int(cell["n"].iloc[0]) if not cell.empty else 0
        pasta_series.append(row)

    return {
        "university_city_decline": {
            "by_stage":        uc_stages,
            "stage3_ratio":    uc_ratios_stage3,
            "metric":          "avg_daily_txns_per_store",
        },
        "plaza_midwood_avocado": {
            "daily_qty_by_merchant": avo_series,
            "windows": {"start": "2026-04-21", "peak": "2026-04-22", "end": "2026-04-24"},
        },
        "acme_pasta_promo": {
            "daily_lines_by_grocer": pasta_series,
            "promo_windows": {
                "KRG": {"start": "2026-04-15", "end": "2026-04-21", "discount_pct": 0.25, "design_target_ratio": 2.2},
                "ACM": {"start": "2026-04-19", "end": "2026-04-25", "discount_pct": 0.20, "design_target_ratio": 0.8},
                "WDX": {"start": "2026-04-22", "end": "2026-04-28", "discount_pct": 0.15, "design_target_ratio": 1.4},
            },
        },
    }


def _peer_mapping() -> dict[str, dict[str, str]]:
    """5×4 per-merchant peer mapping for §2.2.

    Pulls directly from `src.generate.parameters.PEER_MAPPING` so the
    report cannot drift from the runtime mapping.
    """
    import sys
    sys.path.insert(0, str(ROOT))
    from src.generate.parameters import PEER_MAPPING  # noqa: E402

    return {
        viewer: {f"peer_{chr(ord('a') + i)}": peer for i, peer in enumerate(peers)}
        for viewer, peers in PEER_MAPPING.items()
    }


def _lake_schema_annotations() -> dict[str, list[dict[str, str]]]:
    """Per-column annotations for the tenant→lake schema comparison (§2.3).

    Treatment values:
      - "carried"     — column appears in lake with the same value
      - "transformed" — column generalized / binned / bucketed
      - "derived"     — column added in the lake (computed from tenant)
      - "dropped"     — column exists in tenant but not surfaced in lake
    """
    return {
        "lake_transactions": [
            {"name": "lake_txn_id",            "source": "txn_id, line_id",  "treatment": "transformed", "note": "salted SHA-256, 16 chars; replaces merchant-prefixed txn_id"},
            {"name": "line_id",                "source": "line_id",           "treatment": "carried",     "note": "row position within the lake_txn_id"},
            {"name": "peer_id",                "source": "merchant_id",       "treatment": "transformed", "note": "pseudonymized as peer_a..peer_d per viewer-specific mapping"},
            {"name": "peer_segment",           "source": "merchant.segment",  "treatment": "carried",     "note": "grocery / qsr / off_price_retail"},
            {"name": "lake_store_id",          "source": "store_id",          "treatment": "transformed", "note": "salted SHA-256, 16 chars"},
            {"name": "txn_date",               "source": "txn_ts",            "treatment": "transformed", "note": "full timestamp → date"},
            {"name": "txn_hour_bucket",        "source": "txn_ts",            "treatment": "transformed", "note": "full timestamp → 10-bucket time-of-day label"},
            {"name": "payment_type",           "source": "payment_type",      "treatment": "carried",     "note": "credit / debit"},
            {"name": "card_network",           "source": "card_network",      "treatment": "carried",     "note": "visa / mc / amex / discover"},
            {"name": "entry_mode",             "source": "entry_mode",        "treatment": "carried",     "note": "contactless / chip / swipe / manual"},
            {"name": "wallet_type",            "source": "wallet_type",       "treatment": "carried",     "note": "nullable: apple / google / samsung"},
            {"name": "connectivity_type",      "source": "connectivity_type", "treatment": "carried",     "note": "wifi / cellular_4g / cellular_5g / ethernet"},
            {"name": "txn_total_bin",          "source": "txn_total",         "treatment": "transformed", "note": "exact dollar amount → 10-bin label ($0-5 ... $250+)"},
            {"name": "canonical_name",         "source": "products.name",     "treatment": "carried",     "note": "product name; cross-merchant matching key"},
            {"name": "category",               "source": "products.category", "treatment": "carried",     "note": ""},
            {"name": "subcategory",            "source": "products.subcategory","treatment": "carried",   "note": ""},
            {"name": "unit_price",             "source": "items.unit_price",  "treatment": "carried",     "note": "publicly observable, kept exact"},
            {"name": "qty",                    "source": "items.qty",         "treatment": "carried",     "note": ""},
            {"name": "discount",               "source": "items.discount",    "treatment": "carried",     "note": "publicly observable"},
            {"name": "line_total",             "source": "items.line_total",  "treatment": "carried",     "note": "publicly observable"},
            {"name": "discount_pct_applied",   "source": "discount / (unit_price × qty)", "treatment": "derived", "note": "computed in the view; nullable when no discount"},
            {"name": "—",                      "source": "customer_id",       "treatment": "dropped",     "note": "no consumer linkage in the lake"},
            {"name": "—",                      "source": "merchant_id (real)","treatment": "dropped",     "note": "pseudonymized via peer_id"},
            {"name": "—",                      "source": "terminal_id",       "treatment": "dropped",     "note": "device-level not surfaced (v2.5 scope)"},
            {"name": "—",                      "source": "subtotal/tax_total/txn_total (line-level)", "treatment": "dropped", "note": "exact transaction-level rollups not exposed; txn_total_bin only"},
        ],
        "lake_stores": [
            {"name": "lake_store_id",  "source": "store_id",       "treatment": "transformed", "note": "salted SHA-256; same value across viewers"},
            {"name": "peer_id",        "source": "merchant_id",    "treatment": "transformed", "note": "viewer-specific peer label"},
            {"name": "peer_segment",   "source": "merchant.segment","treatment": "carried",    "note": ""},
            {"name": "store_zip3",     "source": "store_zip5",     "treatment": "transformed", "note": "ZIP5 → ZIP3"},
            {"name": "neighborhood",   "source": "neighborhood",   "treatment": "carried",     "note": "publicly observable"},
            {"name": "metro_region",   "source": "metro_region",   "treatment": "carried",     "note": ""},
            {"name": "—",              "source": "store_zip5",     "treatment": "dropped",     "note": "full ZIP not surfaced"},
            {"name": "—",              "source": "latitude/longitude", "treatment": "dropped", "note": "exact coords not surfaced"},
            {"name": "—",              "source": "open_date",      "treatment": "dropped",     "note": ""},
        ],
    }


def _preservation_accounting() -> dict[str, list[str]]:
    """Honest accounting for §2.4 — preserved / approximate / lost.

    Each list is short editorial strings the report renders as a
    three-column comparison. Phrased matter-of-fact, no hedging.
    """
    return {
        "preserved": [
            "Peer pricing per canonical product (exact)",
            "Peer category mix and basket composition",
            "Peer payment mix (card type, network, entry mode, wallet, connectivity)",
            "Peer geographic distribution at neighborhood level",
            "Peer day-of-week and 2-hour bucket patterns",
            "Cross-merchant SKU-level demand patterns",
        ],
        "approximate": [
            "Peer average ticket — via bin midpoints, ~$5–25 imprecision",
            "Peer transaction counts — subject to k=5 suppression on customer-dimensioned queries",
            "Peer geographic precision — neighborhood / ZIP3, not full address",
            "Promo timing — visible via discount activity windows, not exact start / end",
            "Peer hour-of-day — 2-hour buckets, not minute precision",
            "Peer revenue — via amount bin midpoints, not exact totals",
        ],
        "lost": [
            "Per-peer customer cohorts — no customer_id in lake",
            "Cross-merchant customer tracking — linkage stripped",
            "Sub-hour transaction timing",
            "Exact peer revenue totals",
            "Promotion configuration metadata — names, types, exact schedules",
            "Long-tail SKU presence — only SKUs with at least one transaction appear",
        ],
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def main() -> None:
    if not DB_PATH.exists():
        raise SystemExit(
            f"DB not found at {DB_PATH}. Run `make seed` first."
        )

    conn = _connect()
    try:
        window = _window(conn)
        payload: dict[str, Any] = {
            "generated_at":            datetime.now().isoformat(timespec="seconds"),
            "window":                  window,
            "promo_days":              _promo_days(conn),
            "anomaly_window":          _anomaly_window(conn),
            "stats":                   _stats(conn),
            "merchants":               _merchants(conn),
            "revenue_by_category_kroger": _revenue_by_category_kroger(conn),
            "daily_volume":            _daily_volume(conn),
            "hour_distribution":       _hour_distribution(conn),
            "customer_overlap":        _customer_overlap(conn),
            "pay_cycle":               _pay_cycle(conn),
            "example_transaction":     _example_transaction(conn),
            "anonymization_demo":      _privacy_demo(conn),
            "cross_merchant_finding":  _cross_merchant_finding(conn, window),
            "sql_basket_comparison":   _basket_comparison_sql(),
            "affinity_pairs":          _affinity_pairs(conn),
            "schema":                  _schema(conn),
            "anomaly_callouts":        _anomaly_callouts(conn),
            # Phase A — V2.5 report rewrite payload extensions.
            "stores":                  _stores(conn),
            "customer_breakdown":      _customer_breakdown(conn),
            "customer_zip_distribution": _customer_zip_distribution(conn),
            "cohorts":                 _cohorts(conn),
            "catalog_stats":           _catalog_stats(conn),
            "catalog_category_breakdown": _catalog_category_breakdown(conn),
            "catalog_sample_products":    _catalog_sample_products(conn),
            "daily_volume_5merchant":  _daily_volume_5merchant(conn),
            "anatomy_table":           _anatomy_table(),
            "anomaly_series":          _anomaly_series(conn),
            "peer_mapping":            _peer_mapping(),
            "lake_schema_annotations": _lake_schema_annotations(),
            "preservation_accounting": _preservation_accounting(),
        }
    finally:
        conn.close()

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, indent=2, default=str) + "\n")
    OUT_JS.write_text(
        "// Auto-generated by scripts/generate_report_data.py — do not edit.\n"
        "window.REPORT_DATA = "
        + json.dumps(payload, indent=2, default=str)
        + ";\n"
    )
    print(f"[report] wrote {OUT_JSON.relative_to(ROOT)} and {OUT_JS.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
