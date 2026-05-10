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


def _agents_status() -> list[dict[str, Any]]:
    """Strategy doc §10.2 has seven specialist personas. v2.5 ships the
    Conversational Business Advisor (Merchant). The other six are
    deferred — same architectural pattern, incremental build."""
    return [
        {"name": "Conversational Business Advisor — Merchant",  "status": "Built",    "section": "§10.2"},
        {"name": "Demand Forecasting",                          "status": "Deferred", "section": "§10.2"},
        {"name": "Dynamic Pricing & Benchmarking",              "status": "Deferred", "section": "§10.2"},
        {"name": "Consumer Segmentation",                       "status": "Deferred", "section": "§10.2"},
        {"name": "Location & Trade Area Intelligence",          "status": "Deferred", "section": "§10.2"},
        {"name": "Payment Optimization Advisor",                "status": "Deferred", "section": "§10.2"},
        {"name": "Anomaly Detection & Fraud Intelligence",      "status": "Deferred", "section": "§10.2"},
    ]


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
            "agents_status":           _agents_status(),
            "schema":                  _schema(conn),
            "anomaly_callouts":        _anomaly_callouts(conn),
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
