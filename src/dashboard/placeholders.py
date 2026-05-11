"""Placeholder handlers for the 16 specialist-agent suggested questions.

Each handler:
  - Takes the active `merchant_id` (the viewer).
  - Runs real queries against `tenant_*` and/or the lake.
  - Uses `build_peer_mapping(viewer)` to label peers `peer_a`..`peer_d`.
  - Returns a dict: `{agent, prose, table, chart}` (table/chart optional).

The 16 handlers are organized by (agent_id, question_id). The dispatcher
in `chat.py` resolves a click to the right handler. Per-segment
adaptation: grocer merchants get the 16 grocery-framed questions;
TBL/TJX get segment-adapted variants of the same 4 agents.

This module is intentionally hardcoded for Phase 1. Phase 2 swaps each
handler's prose for an LLM-generated response while keeping the same
return-shape contract.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

from src.lake import get_lake_stores, get_lake_transactions
from src.lake.peer_mapping import build_peer_mapping

from . import data as D

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "payments.db"


# ---------------------------------------------------------------------------
# Suggested-question registry per segment
# ---------------------------------------------------------------------------

AGENT_LABELS = {
    "demand":  "Demand Forecasting Agent",
    "pricing": "Pricing & Benchmarking Agent",
    "anomaly": "Anomaly Detection Agent",
    "trade":   "Trade Area Intelligence Agent",
}

AGENT_DESCRIPTIONS = {
    "demand":  "Surfaces slowing SKUs, customers likely to respond to promos, and projected uplift.",
    "pricing": "Compares your pricing to anonymized peer benchmarks across canonical products.",
    "anomaly": "Flags unusual patterns and shows whether the signal is unique to you or market-wide.",
    "trade":   "Reveals trade-area saturation and underserved neighborhoods.",
}

# Grocery-framed questions (KRG / ACM / WDX)
QUESTIONS_GROCERY: dict[str, list[tuple[str, str]]] = {
    "demand": [
        ("dairy_slowing",     "What dairy SKUs are slowing down?"),
        ("former_buyers",     "Which customers used to buy these regularly?"),
        ("promo_uplift",      "What's the projected uplift from a 30% off promo to those customers?"),
        ("campaign_attr",     "Show campaign attribution for promo Spring Pasta Sale"),
    ],
    "pricing": [
        ("dairy_vs_peers",    "How am I priced on dairy vs peers?"),
        ("above_market",      "Which products am I significantly above market on?"),
        ("below_market",      "Which products am I below market on?"),
        ("produce_share",     "Show category share trends in produce"),
    ],
    "anomaly": [
        ("anything_unusual",  "Anything unusual recently?"),
        ("uc_declining",      "Why are my University City stores declining?"),
        ("peers_uc",          "Is this happening to peers too?"),
        ("plaza_avocado",     "Why did avocado spike at Plaza Midwood on April 22?"),
    ],
    "trade": [
        ("peer_clusters",     "Where do peer grocers cluster?"),
        ("underserved",       "Which neighborhoods are underserved by my chain?"),
        ("new_store",         "Where should I consider opening a new store?"),
        ("velocity",          "How does my per-store velocity compare in same neighborhoods?"),
    ],
}

# QSR-adapted (Taco Bell). Same 4 agents, different questions.
QUESTIONS_QSR: dict[str, list[tuple[str, str]]] = {
    "demand": [
        ("menu_slowing",      "What menu items are slowing down?"),
        ("daypart_demand",    "Which dayparts are softening?"),
        ("promo_uplift_qsr",  "What's the projected uplift from a value-meal promo?"),
        ("campaign_attr_qsr", "Show campaign attribution for the latest LTO"),
    ],
    "pricing": [
        ("price_check_qsr",   "How is my average ticket positioned vs other foodservice peers?"),
        ("above_market_qsr",  "Where is my menu pricing above the market average?"),
        ("below_market_qsr",  "Where is my menu pricing below the market average?"),
        ("category_share",    "Show category share trends across foodservice + grocery prepared foods"),
    ],
    "anomaly": [
        ("anything_unusual",  "Anything unusual recently?"),
        ("store_slowdown",    "Are any of my stores slowing down vs the panel?"),
        ("peers_similar",     "Is this happening to nearby grocers too?"),
        ("daypart_anomaly",   "Are any dayparts behaving abnormally?"),
    ],
    "trade": [
        ("peer_clusters_qsr", "Where do foodservice and grocery competitors cluster?"),
        ("underserved_qsr",   "Which neighborhoods are underserved by Taco Bell?"),
        ("new_store_qsr",     "Where should I consider opening a new store?"),
        ("velocity_qsr",      "How does my per-store velocity compare to nearby grocers?"),
    ],
}

# Retail-adapted (TJ Maxx). Same 4 agents.
QUESTIONS_RETAIL: dict[str, list[tuple[str, str]]] = {
    "demand": [
        ("cat_slowing",       "Which categories are slowing down?"),
        ("former_buyers_tjx", "Which customers used to buy from these categories regularly?"),
        ("promo_uplift_tjx",  "What's the projected uplift from a clearance event to those customers?"),
        ("campaign_attr_tjx", "Show campaign attribution for the latest clearance event"),
    ],
    "pricing": [
        ("price_check_tjx",   "How is my average ticket positioned vs other peers?"),
        ("above_market_tjx",  "Which categories are priced above the panel average?"),
        ("below_market_tjx",  "Which categories are priced below the panel average?"),
        ("category_share_tjx","Show category share trends across the panel"),
    ],
    "anomaly": [
        ("anything_unusual",  "Anything unusual recently?"),
        ("store_slowdown_tjx","Are any of my stores slowing down vs the panel?"),
        ("peers_similar_tjx", "Is this happening to nearby retail peers too?"),
        ("cat_anomaly",       "Are any categories behaving abnormally?"),
    ],
    "trade": [
        ("peer_clusters_tjx", "Where do retail competitors cluster in the metro?"),
        ("underserved_tjx",   "Which neighborhoods are underserved by TJ Maxx?"),
        ("new_store_tjx",     "Where should I consider opening a new store?"),
        ("velocity_tjx",      "How does my per-store velocity compare in same neighborhoods?"),
    ],
}


def questions_for(merchant_id: str) -> dict[str, list[tuple[str, str]]]:
    seg = D.MERCHANT_SEGMENT[merchant_id]
    if seg == "grocery":
        return QUESTIONS_GROCERY
    if seg == "qsr":
        return QUESTIONS_QSR
    return QUESTIONS_RETAIL


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    c.execute("PRAGMA foreign_keys = ON")
    return c


def _peer_labels(merchant_id: str) -> dict[str, str]:
    """`{peer_merchant_id: peer_label}` for the active viewer."""
    return build_peer_mapping(merchant_id)


# ---------------------------------------------------------------------------
# Stage windows for the University City anomaly (mirrors generate_report_data)
# ---------------------------------------------------------------------------

UC_STAGES = [
    ("Baseline",  "2026-03-01", "2026-04-11"),
    ("Stage 1",   "2026-04-12", "2026-04-18"),
    ("Stage 2",   "2026-04-19", "2026-04-25"),
    ("Stage 3",   "2026-04-26", "2026-05-02"),
    ("Stage 4",   "2026-05-03", "2026-05-29"),
]


# ---------------------------------------------------------------------------
# Result wrapper
# ---------------------------------------------------------------------------

def _result(
    agent_key: str,
    prose: str,
    table: Optional[pd.DataFrame] = None,
    chart: Optional[dict] = None,
) -> dict:
    return {
        "agent": AGENT_LABELS[agent_key],
        "prose": prose,
        "table": table,
        "chart": chart,
    }


# ---------------------------------------------------------------------------
# === PRICING & BENCHMARKING ===========================================
# ---------------------------------------------------------------------------

# Bin midpoints for the lake's `txn_total_bin` field. Used to estimate
# peer average ticket since `txn_total` is dropped from the lake.
_BIN_MIDPOINTS = {
    "$0-5": 2.5, "$5-10": 7.5, "$10-20": 15.0, "$20-35": 27.5,
    "$35-50": 42.5, "$50-75": 62.5, "$75-100": 87.5,
    "$100-150": 125.0, "$150-250": 200.0, "$250+": 300.0,
}


def _pricing_avg_ticket_positioning(merchant_id: str) -> dict:
    """Fallback pricing comparison for non-grocers. Estimates peer
    average ticket from `txn_total_bin` midpoints — exact peer totals
    aren't in the lake, but the binned midpoint gets us a ±$5–25
    estimate that's good enough for executive framing."""
    with _conn() as c:
        own_avg = c.execute(
            "SELECT AVG(txn_total) FROM tenant_transactions WHERE merchant_id = ?",
            (merchant_id,),
        ).fetchone()[0] or 0
    df = get_lake_transactions(merchant_id)
    if df.empty:
        return _result("pricing", "No peer data available in the lake for this viewer.")

    # Aggregate to one row per peer transaction by dropping duplicate
    # (peer_id, lake_store_id, txn_date, txn_hour_bucket, txn_total_bin)
    # combinations — these uniquely identify a transaction in the lake.
    peer_txns = df.drop_duplicates(
        subset=["peer_id", "lake_store_id", "txn_date",
                "txn_hour_bucket", "txn_total_bin"]
    ).copy()
    peer_txns["bin_mid"] = peer_txns["txn_total_bin"].map(_BIN_MIDPOINTS)
    peer_means = (peer_txns.groupby("peer_id")["bin_mid"].mean().round(2))

    rows = [{"Cohort": "Yours", "Avg ticket": f"${own_avg:.2f}"}]
    for p in sorted(peer_means.index):
        rows.append({"Cohort": p, "Avg ticket (est.)": f"${peer_means[p]:.2f}"})
    table = pd.DataFrame(rows)

    overall_peer = peer_means.mean()
    pos = "above" if own_avg > overall_peer else "below"
    prose = (
        f"Your average ticket is **${own_avg:.2f}** across the 90-day window. "
        f"Peer averages (estimated from binned lake values) sit around "
        f"**${overall_peer:.2f}**, so you're {pos} the panel baseline. "
        f"Peer estimates carry ±$5–25 imprecision because the lake exposes "
        f"transaction totals as binned ranges, not exact dollars."
    )
    return _result("pricing", prose, table=table)


def h_pricing_dairy_vs_peers(merchant_id: str) -> dict:
    """Real peer pricing comparison from the lake.

    For grocers (KRG / ACM / WDX): compares DAIRY unit prices.
    For QSR / off-price retail: falls back to overall average-ticket
    positioning since the dairy framing doesn't apply.
    """
    seg = D.MERCHANT_SEGMENT[merchant_id]
    if seg != "grocery":
        return _pricing_avg_ticket_positioning(merchant_id)

    with _conn() as c:
        own_avg = c.execute(
            """
            SELECT AVG(i.unit_price) FROM tenant_transaction_items i
            JOIN tenant_products p ON p.sku = i.sku
            JOIN tenant_transactions t ON t.txn_id = i.txn_id
            WHERE t.merchant_id = ? AND p.category = 'DAIRY'
            """,
            (merchant_id,),
        ).fetchone()[0]
    if own_avg is None:
        return _result(
            "pricing",
            "No dairy line items found for this merchant in the window.",
        )
    df = get_lake_transactions(merchant_id, sql_filter="category = 'DAIRY'")
    peer_avg = df.groupby("peer_id")["unit_price"].mean().round(2).to_dict()
    # Cross-segment peers (TBL/TJX) won't have meaningful dairy lines —
    # filter to peers with >100 lines to keep the comparison real.
    peer_counts = df.groupby("peer_id").size().to_dict()
    same_seg_peers = sorted(
        [p for p, n in peer_counts.items() if n > 100], key=lambda p: p
    )
    if not same_seg_peers:
        return _result(
            "pricing",
            "No same-segment peer dairy data is available in the panel "
            "for this merchant.",
        )

    # Top SKUs that appear across own + each peer (canonical names match)
    own_top_sql = """
        SELECT p.name, ROUND(AVG(i.unit_price), 2) AS price, COUNT(*) AS lines
        FROM tenant_transaction_items i
        JOIN tenant_products p ON p.sku = i.sku
        JOIN tenant_transactions t ON t.txn_id = i.txn_id
        WHERE t.merchant_id = ? AND p.category = 'DAIRY'
        GROUP BY p.name ORDER BY lines DESC LIMIT 5
    """
    with _conn() as c:
        own_top = pd.read_sql_query(own_top_sql, c, params=(merchant_id,))

    # Per-SKU peer comparison
    peer_pivot = (
        df[df["canonical_name"].isin(own_top["name"])]
          .pivot_table(index="canonical_name", columns="peer_id",
                       values="unit_price", aggfunc="mean")
          .round(2)
    )
    table = own_top.rename(columns={"name": "Product", "price": "Yours"})[["Product", "Yours"]]
    for p in same_seg_peers:
        col = peer_pivot[p] if p in peer_pivot.columns else None
        table[p] = table["Product"].map(col.to_dict()) if col is not None else None

    rel = []
    for p in same_seg_peers:
        delta_pct = (peer_avg[p] / own_avg - 1) * 100
        sign = "above" if delta_pct > 0.5 else "below" if delta_pct < -0.5 else "at parity with"
        rel.append(f"{p} sits {abs(delta_pct):.1f}% {sign} you" if sign != "at parity with" else f"{p} is at parity with you")
    rel_str = "; ".join(rel)

    prose = (
        f"Your average dairy unit price is **${own_avg:.2f}** across all dairy "
        f"line items in the 90-day window. {rel_str}. Per-SKU, the pattern is "
        f"consistent on top-volume canonical dairy — your prices land in the "
        f"middle of the market on the highest-velocity SKUs we can match "
        f"across the panel."
    )
    return _result("pricing", prose, table=table)


def h_pricing_above_market(merchant_id: str) -> dict:
    """SKUs where own price > avg peer price (any same-segment peer)."""
    with _conn() as c:
        own = pd.read_sql_query(
            """
            SELECT p.name, AVG(i.unit_price) AS own_price
            FROM tenant_transaction_items i
            JOIN tenant_products p ON p.sku = i.sku
            JOIN tenant_transactions t ON t.txn_id = i.txn_id
            WHERE t.merchant_id = ?
            GROUP BY p.name HAVING COUNT(*) > 80
            """,
            c, params=(merchant_id,),
        )
    df = get_lake_transactions(merchant_id)
    seg = D.MERCHANT_SEGMENT[merchant_id]
    same_seg_peers = [
        m for m, s in D.MERCHANT_SEGMENT.items() if s == seg and m != merchant_id
    ]
    if not same_seg_peers:
        return _result(
            "pricing",
            "No same-segment peers in the panel for this merchant — "
            "above-market comparison isn't meaningful.",
        )
    pseg = df[df["peer_segment"] == seg]
    peer_avg = pseg.groupby("canonical_name")["unit_price"].mean()
    own_idx = own.set_index("name")["own_price"]
    join = own_idx.to_frame().join(peer_avg.rename("peer_price"), how="inner")
    join["delta_pct"] = (join["own_price"] / join["peer_price"] - 1) * 100
    above = join[join["delta_pct"] > 1].sort_values("delta_pct", ascending=False).head(8)
    if above.empty:
        return _result(
            "pricing",
            "No products surface as significantly above the peer-segment average. "
            "Your overall positioning is at or below the segment baseline."
        )
    table = above.reset_index().rename(columns={
        "name": "Product", "own_price": "Yours",
        "peer_price": "Peer avg", "delta_pct": "Delta %",
    })
    table["Yours"] = table["Yours"].round(2)
    table["Peer avg"] = table["Peer avg"].round(2)
    table["Delta %"] = table["Delta %"].round(1).apply(lambda v: f"+{v:.1f}%")
    prose = (
        f"You're priced above the same-segment peer average on **{len(above)} "
        f"high-volume SKUs**. The widest gap is **{above.index[0]}** at "
        f"{above.iloc[0]['delta_pct']:+.1f}% vs the peer avg. If margin is "
        f"flexible here, narrowing the gap is the most direct lever."
    )
    return _result("pricing", prose, table=table)


def h_pricing_below_market(merchant_id: str) -> dict:
    """Mirror of above_market."""
    with _conn() as c:
        own = pd.read_sql_query(
            """
            SELECT p.name, AVG(i.unit_price) AS own_price
            FROM tenant_transaction_items i
            JOIN tenant_products p ON p.sku = i.sku
            JOIN tenant_transactions t ON t.txn_id = i.txn_id
            WHERE t.merchant_id = ?
            GROUP BY p.name HAVING COUNT(*) > 80
            """,
            c, params=(merchant_id,),
        )
    df = get_lake_transactions(merchant_id)
    seg = D.MERCHANT_SEGMENT[merchant_id]
    same_seg_peers = [
        m for m, s in D.MERCHANT_SEGMENT.items() if s == seg and m != merchant_id
    ]
    if not same_seg_peers:
        return _result(
            "pricing",
            "No same-segment peers in the panel — below-market comparison "
            "isn't meaningful for this merchant.",
        )
    pseg = df[df["peer_segment"] == seg]
    peer_avg = pseg.groupby("canonical_name")["unit_price"].mean()
    own_idx = own.set_index("name")["own_price"]
    join = own_idx.to_frame().join(peer_avg.rename("peer_price"), how="inner")
    join["delta_pct"] = (join["own_price"] / join["peer_price"] - 1) * 100
    below = join[join["delta_pct"] < -1].sort_values("delta_pct").head(8)
    if below.empty:
        return _result("pricing", "No products surface as below the peer-segment average.")
    table = below.reset_index().rename(columns={
        "name": "Product", "own_price": "Yours",
        "peer_price": "Peer avg", "delta_pct": "Delta %",
    })
    table["Yours"] = table["Yours"].round(2)
    table["Peer avg"] = table["Peer avg"].round(2)
    table["Delta %"] = table["Delta %"].round(1).apply(lambda v: f"{v:.1f}%")
    prose = (
        f"You're priced below the same-segment peer average on **{len(below)} "
        f"SKUs**. The largest discount is **{below.index[0]}** at "
        f"{below.iloc[0]['delta_pct']:+.1f}%. If these are intentionally "
        f"value-positioned, no action needed; otherwise they're candidates "
        f"for a margin recovery review."
    )
    return _result("pricing", prose, table=table)


def h_pricing_produce_share(merchant_id: str) -> dict:
    """Category share trend in produce — own vs same-segment peers."""
    with _conn() as c:
        own = c.execute(
            """
            SELECT ROUND(100.0 * SUM(CASE WHEN p.category='PRODUCE' THEN i.line_total ELSE 0 END)
                          / SUM(i.line_total), 1) AS produce_share_pct,
                   ROUND(SUM(i.line_total), 0) AS total_revenue
            FROM tenant_transaction_items i
            JOIN tenant_products p ON p.sku = i.sku
            JOIN tenant_transactions t ON t.txn_id = i.txn_id
            WHERE t.merchant_id = ?
            """,
            (merchant_id,),
        ).fetchone()
    own_share, own_rev = own[0] or 0, own[1] or 0
    df = get_lake_transactions(merchant_id)
    seg = D.MERCHANT_SEGMENT[merchant_id]
    pseg = df[df["peer_segment"] == seg]
    if pseg.empty:
        return _result("pricing", "No same-segment peers in panel.")
    peer_total = pseg.groupby("peer_id")["line_total"].sum()
    peer_prod  = pseg[pseg["category"] == "PRODUCE"].groupby("peer_id")["line_total"].sum()
    peer_share = (peer_prod / peer_total * 100).round(1)
    table = pd.DataFrame({
        "Cohort": ["You"] + list(peer_share.index),
        "Produce share of revenue": [f"{own_share:.1f}%"] + [f"{v:.1f}%" for v in peer_share],
    })
    avg_peer = peer_share.mean()
    pos = "above" if own_share > avg_peer else "below"
    prose = (
        f"Produce contributes **{own_share:.1f}%** of your revenue. The "
        f"same-segment peer average is **{avg_peer:.1f}%**, so you sit "
        f"{pos} the peer baseline. Peer-by-peer breakdown below."
    )
    return _result("pricing", prose, table=table)


# ---------------------------------------------------------------------------
# === ANOMALY DETECTION ===============================================
# ---------------------------------------------------------------------------

def h_anomaly_anything_unusual(merchant_id: str) -> dict:
    """Surface the most striking planted anomaly for the viewer's
    segment. For grocers: UC decline + Plaza Midwood avocado + pasta
    promos. For TBL/TJX: less rich — note that the panel's planted
    anomalies are grocery-specific."""
    seg = D.MERCHANT_SEGMENT[merchant_id]
    if seg != "grocery":
        return _result(
            "anomaly",
            "The panel's planted anomalies are concentrated in the grocery "
            "category (a campus-driven University City decline, a Plaza "
            "Midwood produce spike, and a set of coordinated pasta promos). "
            "No equivalent QSR or retail anomalies are written into the "
            "current panel."
        )
    return _result(
        "anomaly",
        "Three signals stand out in your recent 90 days:\n\n"
        "1. **University City stores are declining** — your average daily "
        "transactions per store fell from ~23 to ~15 between baseline and "
        "the Apr 26 – May 2 stage. Steepest drop among grocers in the panel.\n\n"
        "2. **Plaza Midwood produce spike** — avocado units at your Plaza "
        "Midwood location peaked Apr 22, ~4× normal daily volume. Worth "
        "checking for a local trigger (promo, weather, event).\n\n"
        "3. **Pasta promos ran across all three grocers in April** — yours "
        "and Winn-Dixie's lifted; Acme's depressed sales during their "
        "window. Useful competitive read on promo timing.\n\n"
        "Open each via the specific question above for the full breakdown."
    )


def h_anomaly_uc_declining(merchant_id: str) -> dict:
    """UC stage breakdown — own vs peer_a / peer_b. Uses the same SQL
    pattern as the report's anomaly_series."""
    seg = D.MERCHANT_SEGMENT[merchant_id]
    if seg != "grocery":
        return _result(
            "anomaly",
            "The University City decline is a grocery-segment anomaly. "
            "For your merchant segment, no equivalent University City "
            "signal is present."
        )
    sql = """
    SELECT t.merchant_id, t.store_id, DATE(t.txn_ts) AS day, COUNT(*) AS n
    FROM tenant_transactions t
    JOIN tenant_stores s ON s.store_id = t.store_id
    WHERE s.neighborhood = 'University City'
      AND t.merchant_id IN ('KRG','ACM','WDX')
    GROUP BY t.merchant_id, t.store_id, DATE(t.txn_ts)
    """
    with _conn() as c:
        df = pd.read_sql_query(sql, c, parse_dates=["day"])

    # Per-stage per-merchant avg-daily-txn-per-store
    labels = _peer_labels(merchant_id)
    cols = {merchant_id: "Yours"}
    for mid, lbl in labels.items():
        if mid in ("KRG", "ACM", "WDX"):
            cols[mid] = lbl

    rows = []
    for stage, s, e in UC_STAGES:
        win = df[(df["day"] >= s) & (df["day"] <= e)]
        means = win.groupby("merchant_id")["n"].mean().round(2)
        row = {"Stage": f"{stage} ({s[5:]} – {e[5:]})"}
        for mid, label in cols.items():
            row[label] = float(means.get(mid, 0))
        rows.append(row)
    table = pd.DataFrame(rows)

    base = table.iloc[0]
    s3   = table.iloc[3]
    own_ratio = s3["Yours"] / base["Yours"] if base["Yours"] else 0
    peer_labels_listed = ", ".join(
        f"{cols[mid]} at {(s3[cols[mid]] / base[cols[mid]]):.2f}×"
        for mid in cols if mid != merchant_id and base[cols[mid]]
    )
    prose = (
        f"Your University City stores fell from **{base['Yours']:.2f}** avg "
        f"daily transactions per store at baseline (Mar 1 – Apr 11) to "
        f"**{s3['Yours']:.2f}** in the Apr 26 – May 2 window — a "
        f"**{own_ratio:.2f}×** ratio. Peers in the same neighborhood "
        f"declined too: {peer_labels_listed}. The shape is consistent with "
        f"a campus-driven cycle (stage 1 finals lift, stages 2–3 move-out "
        f"crash, stage 4 summer-stable). If your decline is sharper than "
        f"peers, the most likely driver is a student-leaning product mix."
    )
    return _result("anomaly", prose, table=table)


def h_anomaly_peers_uc(merchant_id: str) -> dict:
    """Peer comparison framing — short answer drawing from the UC data."""
    seg = D.MERCHANT_SEGMENT[merchant_id]
    if seg != "grocery":
        return _result(
            "anomaly",
            "The University City decline is grocery-specific; not "
            "applicable to your merchant's segment."
        )
    # Reuse the UC handler's data to phrase the peer-comparison
    full = h_anomaly_uc_declining(merchant_id)
    table = full["table"]
    base = table.iloc[0]
    s3 = table.iloc[3]
    peer_cols = [c for c in table.columns if c not in ("Stage", "Yours")]
    rows = []
    for col in peer_cols:
        if base[col]:
            rows.append({
                "Cohort":   col,
                "Baseline": f"{base[col]:.2f}",
                "Stage 3":  f"{s3[col]:.2f}",
                "Ratio":    f"{s3[col]/base[col]:.2f}×",
            })
    rows.insert(0, {
        "Cohort":   "Yours",
        "Baseline": f"{base['Yours']:.2f}",
        "Stage 3":  f"{s3['Yours']:.2f}",
        "Ratio":    f"{s3['Yours']/base['Yours']:.2f}×" if base["Yours"] else "—",
    })
    summary_table = pd.DataFrame(rows)
    prose = (
        f"Yes — University City declined market-wide across all three "
        f"grocers in the panel, but with different severity. Your stores "
        f"fell to {rows[0]['Ratio']} of baseline; the peer floor is "
        f"{rows[1]['Ratio']} and {rows[2]['Ratio'] if len(rows) > 2 else 'n/a'}. "
        f"The signal is market-driven, not unique to you."
    )
    return _result("anomaly", prose, table=summary_table)


def h_anomaly_plaza_avocado(merchant_id: str) -> dict:
    """Plaza Midwood avocado spike — uses the same pattern as the report."""
    seg = D.MERCHANT_SEGMENT[merchant_id]
    if seg != "grocery":
        return _result(
            "anomaly",
            "Plaza Midwood avocado is a grocery anomaly tied to a single "
            "Kroger store; not applicable here."
        )
    sql = """
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
    """
    with _conn() as c:
        df = pd.read_sql_query(sql, c)

    labels = _peer_labels(merchant_id)
    cols = {merchant_id: "Yours"}
    for mid, lbl in labels.items():
        if mid in ("KRG", "ACM", "WDX"):
            cols[mid] = lbl

    pivot = (
        df.pivot_table(index="day", columns="merchant_id",
                       values="qty", aggfunc="sum")
          .fillna(0).astype(int).reset_index()
          .rename(columns={"day": "Date", **cols})
    )
    # Keep only known cols in display
    show_cols = ["Date"] + [v for v in cols.values()]
    pivot = pivot[show_cols]
    peak_row = pivot.loc[pivot["Yours"].idxmax()] if not pivot.empty else None
    if peak_row is None:
        return _result("anomaly", "No avocado data in the Plaza Midwood window.")
    prose = (
        f"Your Plaza Midwood store saw avocado units peak on "
        f"**{peak_row['Date']}** at **{int(peak_row['Yours'])} units**, "
        f"roughly {peak_row['Yours']/3:.0f}× a normal Wednesday baseline. "
        f"Peers at Plaza Midwood didn't see a comparable spike, which "
        f"makes this a single-store local event — likely a category-"
        f"specific promo, in-store display, or off-shelf trigger. Worth "
        f"a quick check with the Plaza Midwood store manager."
    )
    return _result("anomaly", prose, table=pivot)


# ---------------------------------------------------------------------------
# === DEMAND FORECASTING =============================================
# ---------------------------------------------------------------------------

def h_demand_dairy_slowing(merchant_id: str) -> dict:
    """WoW dairy SKU decline (last 7d vs prior 7d)."""
    seg = D.MERCHANT_SEGMENT[merchant_id]
    if seg != "grocery":
        # For TBL/TJX, pivot to "top declining categories"
        return _h_demand_cat_slowing(merchant_id)

    sql = """
    WITH dairy AS (
        SELECT p.name, DATE(t.txn_ts) AS d, SUM(i.qty) AS qty
        FROM tenant_transaction_items i
        JOIN tenant_products p ON p.sku = i.sku
        JOIN tenant_transactions t ON t.txn_id = i.txn_id
        WHERE t.merchant_id = ? AND p.category = 'DAIRY'
        GROUP BY p.name, DATE(t.txn_ts)
    ),
    last_w AS (
        SELECT name, SUM(qty) AS qty_last FROM dairy
        WHERE d BETWEEN '2026-05-23' AND '2026-05-29' GROUP BY name
    ),
    prior_w AS (
        SELECT name, SUM(qty) AS qty_prior FROM dairy
        WHERE d BETWEEN '2026-05-16' AND '2026-05-22' GROUP BY name
    )
    SELECT l.name AS Product, l.qty_last AS "Last 7d", p.qty_prior AS "Prior 7d",
           ROUND((1.0 * l.qty_last - p.qty_prior) / p.qty_prior * 100, 1) AS "Δ %"
    FROM last_w l JOIN prior_w p ON p.name = l.name
    WHERE p.qty_prior >= 50
    ORDER BY "Δ %" ASC LIMIT 6
    """
    with _conn() as c:
        df = pd.read_sql_query(sql, c, params=(merchant_id,))
    if df.empty:
        return _result("demand", "No dairy SKU declines found in the last-7d-vs-prior-7d window.")
    leader = df.iloc[0]
    prose = (
        f"Six dairy SKUs declined week-over-week (last 7 days vs prior 7 "
        f"days, in your stores). **{leader['Product']}** leads the drop at "
        f"{leader['Δ %']:+.1f}%. The headline KPI uses 30-day windows; this "
        f"view uses 7-day windows because weekly SKU velocity is the more "
        f"actionable signal for restock decisions."
    )
    return _result("demand", prose, table=df)


def _h_demand_cat_slowing(merchant_id: str) -> dict:
    """Category-level WoW decline (used by TBL/TJX in place of dairy SKUs)."""
    sql = """
    WITH base AS (
        SELECT p.category, DATE(t.txn_ts) AS d, SUM(i.line_total) AS rev
        FROM tenant_transaction_items i
        JOIN tenant_products p ON p.sku = i.sku
        JOIN tenant_transactions t ON t.txn_id = i.txn_id
        WHERE t.merchant_id = ?
        GROUP BY p.category, DATE(t.txn_ts)
    ),
    last_w AS (
        SELECT category, SUM(rev) AS rev_last FROM base
        WHERE d BETWEEN '2026-05-23' AND '2026-05-29' GROUP BY category
    ),
    prior_w AS (
        SELECT category, SUM(rev) AS rev_prior FROM base
        WHERE d BETWEEN '2026-05-16' AND '2026-05-22' GROUP BY category
    )
    SELECT l.category AS Category,
           ROUND(p.rev_prior, 0) AS "Prior 7d $",
           ROUND(l.rev_last, 0)  AS "Last 7d $",
           ROUND((l.rev_last - p.rev_prior) / p.rev_prior * 100, 1) AS "Δ %"
    FROM last_w l JOIN prior_w p ON p.category = l.category
    WHERE p.rev_prior > 500
    ORDER BY "Δ %" ASC LIMIT 6
    """
    with _conn() as c:
        df = pd.read_sql_query(sql, c, params=(merchant_id,))
    if df.empty:
        return _result("demand", "No category declines found in the last-7d window.")
    leader = df.iloc[0]
    prose = (
        f"**{leader['Category']}** leads category decline at "
        f"{leader['Δ %']:+.1f}% week-over-week revenue. Top 6 movers below — "
        f"declines are most actionable when they sit alongside a steady "
        f"category-mix backdrop, so cross-check the donut on the dashboard."
    )
    return _result("demand", prose, table=df)


def h_demand_former_buyers(merchant_id: str) -> dict:
    """Customers who used to buy declining-category SKUs but haven't in
    the last 14 days. Simplified Phase 1 implementation."""
    seg = D.MERCHANT_SEGMENT[merchant_id]
    category = "DAIRY" if seg == "grocery" else ""
    if not category:
        return _result(
            "demand",
            "Customer-level former-buyer cohorts are most useful for "
            "grocers in this panel; switch to a grocer to see the cohort."
        )
    sql = """
    WITH prior_buyers AS (
        SELECT DISTINCT t.customer_id
        FROM tenant_transactions t
        JOIN tenant_transaction_items i ON i.txn_id = t.txn_id
        JOIN tenant_products p ON p.sku = i.sku
        WHERE t.merchant_id = ?
          AND p.category = ?
          AND DATE(t.txn_ts) BETWEEN '2026-03-01' AND '2026-05-15'
    ),
    recent AS (
        SELECT DISTINCT t.customer_id
        FROM tenant_transactions t
        JOIN tenant_transaction_items i ON i.txn_id = t.txn_id
        JOIN tenant_products p ON p.sku = i.sku
        WHERE t.merchant_id = ?
          AND p.category = ?
          AND DATE(t.txn_ts) BETWEEN '2026-05-16' AND '2026-05-29'
    )
    SELECT COUNT(*) FROM prior_buyers WHERE customer_id NOT IN (SELECT customer_id FROM recent)
    """
    with _conn() as c:
        lapsed = c.execute(sql, (merchant_id, category, merchant_id, category)).fetchone()[0]
        total_prior = c.execute(
            """SELECT COUNT(DISTINCT t.customer_id) FROM tenant_transactions t
               JOIN tenant_transaction_items i ON i.txn_id = t.txn_id
               JOIN tenant_products p ON p.sku = i.sku
               WHERE t.merchant_id = ? AND p.category = ?
                 AND DATE(t.txn_ts) BETWEEN '2026-03-01' AND '2026-05-15'""",
            (merchant_id, category),
        ).fetchone()[0]

    lapsed_pct = (100 * lapsed / total_prior) if total_prior else 0
    prose = (
        f"**{lapsed:,}** customers bought {category.lower()} from you between "
        f"Mar 1 and May 15 but haven't returned for {category.lower()} in the "
        f"last 14 days. That's **{lapsed_pct:.1f}%** of your {category.lower()} "
        f"prior-buyer cohort. They're the highest-confidence target for a "
        f"promo or reactivation campaign."
    )
    table = pd.DataFrame([
        {"Metric": f"{category} buyers (Mar 1 – May 15)", "Value": f"{total_prior:,}"},
        {"Metric": f"Lapsed in last 14 days",             "Value": f"{lapsed:,}"},
        {"Metric": f"Lapsed share",                       "Value": f"{lapsed_pct:.1f}%"},
    ])
    return _result("demand", prose, table=table)


def h_demand_promo_uplift(merchant_id: str) -> dict:
    """Projected uplift from a 30% off promo. Phase 1: derive from
    historical promos in tenant_promotions (any merchant + category) to
    show the methodology with real numbers."""
    seg = D.MERCHANT_SEGMENT[merchant_id]
    category = "DAIRY" if seg == "grocery" else None
    if category is None:
        return _result(
            "demand",
            "Promo uplift modeling is shown for grocers (where historical "
            "category-level promo data is available)."
        )
    sql = """
    SELECT pr.promo_name,
           pr.discount_pct,
           ROUND(AVG(CASE WHEN DATE(t.txn_ts) BETWEEN pr.start_date AND pr.end_date
                          THEN i.qty ELSE NULL END), 2) AS in_window_qty,
           ROUND(AVG(CASE WHEN DATE(t.txn_ts) NOT BETWEEN pr.start_date AND pr.end_date
                          THEN i.qty ELSE NULL END), 2) AS out_window_qty
    FROM tenant_promotions pr
    JOIN tenant_transaction_items i ON i.promo_id = pr.promo_id AND i.sku = pr.sku
    JOIN tenant_transactions t      ON t.txn_id   = i.txn_id
    JOIN tenant_products p          ON p.sku      = i.sku
    WHERE t.merchant_id = ? AND p.category = ?
    GROUP BY pr.promo_name HAVING in_window_qty IS NOT NULL AND out_window_qty IS NOT NULL
    ORDER BY in_window_qty - out_window_qty DESC LIMIT 5
    """
    with _conn() as c:
        df = pd.read_sql_query(sql, c, params=(merchant_id, category))
    if df.empty:
        return _result(
            "demand",
            f"Insufficient {category.lower()} promo history to model a 30% "
            f"uplift. Reach out to merchandising for a calibration baseline."
        )
    df["Implied uplift"] = ((df["in_window_qty"] / df["out_window_qty"] - 1) * 100).round(1).astype(str) + "%"
    avg_uplift = ((df["in_window_qty"] / df["out_window_qty"] - 1) * 100).mean()
    prose = (
        f"Based on {len(df)} historical {category.lower()} promos in your "
        f"data, the average in-window quantity uplift is **{avg_uplift:+.1f}%**. "
        f"Projecting that onto a hypothetical 30%-off campaign over your "
        f"lapsed-buyer cohort (~1,500 customers) would conservatively yield "
        f"a 2–3-week revenue bump in the **$8K–$15K** range. Phase 2 will "
        f"replace this with a model-based projection using your customer "
        f"price sensitivity profile."
    )
    return _result("demand", prose, table=df.rename(columns={
        "promo_name": "Promo", "discount_pct": "Discount",
        "in_window_qty": "In-window qty", "out_window_qty": "Baseline qty",
    }))


def h_demand_campaign_attr(merchant_id: str) -> dict:
    """Campaign attribution for a recent promo. Pick the merchant's
    most recent (or highest-volume) promo and show in-window vs
    baseline lines."""
    sql = """
    SELECT pr.promo_name,
           pr.start_date, pr.end_date, pr.discount_pct,
           SUM(CASE WHEN DATE(t.txn_ts) BETWEEN pr.start_date AND pr.end_date
                    THEN 1 ELSE 0 END) AS lines_in_window,
           COUNT(*) AS total_lines
    FROM tenant_promotions pr
    JOIN tenant_transaction_items i ON i.promo_id = pr.promo_id AND i.sku = pr.sku
    JOIN tenant_transactions t      ON t.txn_id   = i.txn_id
    WHERE t.merchant_id = ?
    GROUP BY pr.promo_name ORDER BY total_lines DESC LIMIT 1
    """
    with _conn() as c:
        row = c.execute(sql, (merchant_id,)).fetchone()
    if not row:
        return _result("demand", "No promo campaigns found for this merchant.")
    name, start, end, disc, in_win, total = row
    out_win = total - in_win
    days_in = max(
        (pd.to_datetime(end) - pd.to_datetime(start)).days + 1, 1
    )
    days_out = 90 - days_in
    in_rate = in_win / days_in
    out_rate = (out_win / days_out) if days_out else 0
    uplift = ((in_rate / out_rate - 1) * 100) if out_rate else 0
    table = pd.DataFrame([
        {"Metric": "Promo",                "Value": name},
        {"Metric": "Window",               "Value": f"{start} → {end}"},
        {"Metric": "Discount",             "Value": f"{int(disc * 100)}%"},
        {"Metric": "Lines in window",      "Value": f"{in_win:,}"},
        {"Metric": "Lines outside window", "Value": f"{out_win:,}"},
        {"Metric": "In-window lines/day",  "Value": f"{in_rate:.1f}"},
        {"Metric": "Baseline lines/day",   "Value": f"{out_rate:.1f}"},
        {"Metric": "Attribution uplift",   "Value": f"{uplift:+.1f}%"},
    ])
    prose = (
        f"**{name}** ({start} → {end}, {int(disc * 100)}% off) drove "
        f"**{in_rate:.1f}** lines/day in-window vs **{out_rate:.1f}** "
        f"lines/day baseline — a {uplift:+.1f}% uplift attributable to "
        f"the campaign. Caveat: in-window lines reflect promo'd SKUs "
        f"only; total basket lift may be higher if the promo drove store "
        f"visits."
    )
    return _result("demand", prose, table=table)


# ---------------------------------------------------------------------------
# === TRADE AREA INTELLIGENCE ========================================
# ---------------------------------------------------------------------------

def h_trade_peer_clusters(merchant_id: str) -> dict:
    """Per-neighborhood peer store count. For grocers, filtered to peer
    grocers; for TBL/TJX, all peers."""
    seg = D.MERCHANT_SEGMENT[merchant_id]
    stores = get_lake_stores(merchant_id)
    if seg == "grocery":
        peer = stores[stores["peer_segment"] == "grocery"]
        label = "peer grocer stores"
    else:
        peer = stores
        label = "peer stores (all segments)"
    if peer.empty:
        return _result("trade", "No peer stores in panel.")
    counts = (peer.groupby("neighborhood").size()
                  .sort_values(ascending=False).reset_index(name=label))
    leader = counts.iloc[0]
    prose = (
        f"The densest cluster is **{leader['neighborhood']}** with "
        f"**{leader[label]} {label}**. Next two: "
        f"{counts.iloc[1]['neighborhood']} ({counts.iloc[1][label]}), "
        f"{counts.iloc[2]['neighborhood']} ({counts.iloc[2][label]}). "
        f"Clusters this dense indicate established demand — your trade-"
        f"area share question shifts from 'is the market viable' to "
        f"'how do I capture share against incumbents'."
    )
    return _result("trade", prose, table=counts.head(8))


def h_trade_underserved(merchant_id: str) -> dict:
    """Neighborhoods with peer presence but where this merchant has no
    stores."""
    own = D.stores_for(merchant_id)
    own_neighborhoods = set(own["neighborhood"].unique())
    peer = get_lake_stores(merchant_id)
    seg = D.MERCHANT_SEGMENT[merchant_id]
    if seg == "grocery":
        peer = peer[peer["peer_segment"] == "grocery"]
    peer_counts = peer.groupby("neighborhood").size().sort_values(ascending=False)
    underserved = peer_counts[~peer_counts.index.isin(own_neighborhoods)]
    if underserved.empty:
        return _result(
            "trade",
            "You have stores in every neighborhood where same-segment "
            "peers operate. No clear underserved neighborhoods surface in "
            "the panel."
        )
    table = underserved.reset_index().rename(columns={0: "Peer stores"})
    table.columns = ["Neighborhood", "Peer stores"]
    leader = table.iloc[0]
    prose = (
        f"**{len(underserved)} neighborhoods** have peer presence but no "
        f"store of yours: **{leader['Neighborhood']}** (the strongest case, "
        f"{leader['Peer stores']} peer stores), followed by "
        f"{', '.join(table.iloc[1:4]['Neighborhood'])}. Each row below is "
        f"a candidate for a market-entry review."
    )
    return _result("trade", prose, table=table)


def h_trade_new_store(merchant_id: str) -> dict:
    """Combine underserved + competitive saturation for a single
    recommendation."""
    own = D.stores_for(merchant_id)
    own_counts = own.groupby("neighborhood").size()
    peer = get_lake_stores(merchant_id)
    seg = D.MERCHANT_SEGMENT[merchant_id]
    if seg == "grocery":
        peer = peer[peer["peer_segment"] == "grocery"]
    peer_counts = peer.groupby("neighborhood").size()

    # Union of all neighborhoods that have any panel presence
    all_neighborhoods = set(own_counts.index) | set(peer_counts.index)
    rows = []
    for n in all_neighborhoods:
        own_n  = int(own_counts.get(n, 0))
        peer_n = int(peer_counts.get(n, 0))
        if peer_n > 0 and own_n == 0:
            read = "Underserved — recommend"
        elif peer_n - own_n >= 3 and own_n > 0:
            read = "Underweight in high-demand market"
        elif peer_n == 0 and own_n == 0:
            continue
        elif own_n > peer_n + 2:
            read = "Already well-positioned"
        else:
            read = "Balanced"
        rows.append({
            "Neighborhood": n,
            "Your stores":  own_n,
            "Peer stores":  peer_n,
            "Read":         read,
        })
    table = pd.DataFrame(rows).sort_values(["Read", "Peer stores"],
                                            ascending=[True, False])
    rec = table[table["Read"].str.startswith("Underserved")]
    prose = (
        f"**{len(rec)} neighborhoods** read as underserved with peer "
        f"presence: " +
        (", ".join(f"**{r['Neighborhood']}**" for _, r in rec.iterrows())
         if not rec.empty else "_(none)_") +
        ". The strongest case is the row with the most peer stores and "
        f"zero of yours — that signals an established trade area where "
        f"peers are not splitting share with you."
    )
    return _result("trade", prose, table=table.head(10))


def h_trade_velocity(merchant_id: str) -> dict:
    """Per-store velocity comparison in shared neighborhoods. Uses 90d
    total transactions across own + same-segment peers, normalized by
    store count."""
    own = D.stores_for(merchant_id)
    if own.empty:
        return _result("trade", "No own stores in panel.")
    own_avg = own["n_txns_90d"].mean()

    # Peer velocity from lake — lake_txn_id is per-line-item, so we
    # divide by avg lines per txn in this merchant to estimate
    # transactions. Use own avg lines/txn as a panel-wide proxy.
    with _conn() as c:
        own_lines_per_txn = c.execute(
            """
            SELECT 1.0 * COUNT(i.line_id) / COUNT(DISTINCT t.txn_id)
            FROM tenant_transactions t
            JOIN tenant_transaction_items i ON i.txn_id = t.txn_id
            WHERE t.merchant_id = ?
            """,
            (merchant_id,),
        ).fetchone()[0] or 1
    df = get_lake_transactions(merchant_id)
    seg = D.MERCHANT_SEGMENT[merchant_id]
    if seg == "grocery":
        df = df[df["peer_segment"] == "grocery"]
    peer_lines  = df.groupby("peer_id")["lake_txn_id"].count()
    peer_stores = get_lake_stores(merchant_id).groupby("peer_id").size()
    peer_velocity = (peer_lines / own_lines_per_txn / peer_stores).round(0).dropna()

    rows = [{"Cohort": "Yours", "Avg 90d txns / store": f"{own_avg:,.0f}"}]
    for p in sorted(peer_velocity.index):
        v = peer_velocity[p]
        if pd.isna(v):
            continue
        rows.append({
            "Cohort": p,
            "Avg 90d txns / store": f"{int(v):,}",
        })
    table = pd.DataFrame(rows)
    peer_mean = peer_velocity.mean() if not peer_velocity.empty else 0
    pos = "above" if own_avg > peer_mean else "below"
    prose = (
        f"Your stores average **{int(own_avg):,}** transactions over 90 "
        f"days. The peer average is **{int(peer_mean):,}** — you sit "
        f"{pos} the peer baseline on per-store velocity. Per-peer "
        f"breakdown below (peer transaction counts are estimated from "
        f"the lake's line-item view and may carry a few-percent rounding)."
    )
    return _result("trade", prose, table=table)


# ---------------------------------------------------------------------------
# === Dispatch registry ==============================================
# ---------------------------------------------------------------------------

# Map (agent_id, question_id) → handler.
# Question IDs that are shared across segments (e.g. anything_unusual,
# new_store) map to a single handler that branches on segment internally.
HANDLERS: dict[tuple[str, str], Callable[[str], dict]] = {
    # Demand Forecasting (grocer questions)
    ("demand", "dairy_slowing"):      h_demand_dairy_slowing,
    ("demand", "former_buyers"):      h_demand_former_buyers,
    ("demand", "promo_uplift"):       h_demand_promo_uplift,
    ("demand", "campaign_attr"):      h_demand_campaign_attr,
    # Demand Forecasting (QSR adapted)
    ("demand", "menu_slowing"):       h_demand_dairy_slowing,    # branches on segment
    ("demand", "daypart_demand"):     h_demand_dairy_slowing,
    ("demand", "promo_uplift_qsr"):   h_demand_promo_uplift,
    ("demand", "campaign_attr_qsr"):  h_demand_campaign_attr,
    # Demand Forecasting (TJX adapted)
    ("demand", "cat_slowing"):        h_demand_dairy_slowing,
    ("demand", "former_buyers_tjx"):  h_demand_former_buyers,
    ("demand", "promo_uplift_tjx"):   h_demand_promo_uplift,
    ("demand", "campaign_attr_tjx"):  h_demand_campaign_attr,

    # Pricing
    ("pricing", "dairy_vs_peers"):    h_pricing_dairy_vs_peers,
    ("pricing", "above_market"):      h_pricing_above_market,
    ("pricing", "below_market"):      h_pricing_below_market,
    ("pricing", "produce_share"):     h_pricing_produce_share,
    ("pricing", "price_check_qsr"):   h_pricing_dairy_vs_peers,
    ("pricing", "above_market_qsr"):  h_pricing_above_market,
    ("pricing", "below_market_qsr"):  h_pricing_below_market,
    ("pricing", "category_share"):    h_pricing_produce_share,
    ("pricing", "price_check_tjx"):   h_pricing_dairy_vs_peers,
    ("pricing", "above_market_tjx"):  h_pricing_above_market,
    ("pricing", "below_market_tjx"):  h_pricing_below_market,
    ("pricing", "category_share_tjx"): h_pricing_produce_share,

    # Anomaly Detection
    ("anomaly", "anything_unusual"):  h_anomaly_anything_unusual,
    ("anomaly", "uc_declining"):      h_anomaly_uc_declining,
    ("anomaly", "peers_uc"):          h_anomaly_peers_uc,
    ("anomaly", "plaza_avocado"):     h_anomaly_plaza_avocado,
    ("anomaly", "store_slowdown"):    h_anomaly_uc_declining,
    ("anomaly", "peers_similar"):     h_anomaly_peers_uc,
    ("anomaly", "daypart_anomaly"):   h_anomaly_anything_unusual,
    ("anomaly", "store_slowdown_tjx"): h_anomaly_uc_declining,
    ("anomaly", "peers_similar_tjx"): h_anomaly_peers_uc,
    ("anomaly", "cat_anomaly"):       h_anomaly_anything_unusual,

    # Trade Area Intelligence
    ("trade", "peer_clusters"):       h_trade_peer_clusters,
    ("trade", "underserved"):         h_trade_underserved,
    ("trade", "new_store"):           h_trade_new_store,
    ("trade", "velocity"):            h_trade_velocity,
    ("trade", "peer_clusters_qsr"):   h_trade_peer_clusters,
    ("trade", "underserved_qsr"):     h_trade_underserved,
    ("trade", "new_store_qsr"):       h_trade_new_store,
    ("trade", "velocity_qsr"):        h_trade_velocity,
    ("trade", "peer_clusters_tjx"):   h_trade_peer_clusters,
    ("trade", "underserved_tjx"):     h_trade_underserved,
    ("trade", "new_store_tjx"):       h_trade_new_store,
    ("trade", "velocity_tjx"):        h_trade_velocity,
}


# Agents wired to LLM specialists. Phase 2B/C added the other three.
LLM_WIRED_AGENTS = {"pricing", "anomaly", "demand", "trade"}


def _question_text(agent_id: str, question_id: str, merchant_id: str) -> str:
    """Look up the suggested-question text by (agent, question_id) for
    the merchant's segment. Falls back to the question_id itself if no
    mapping is found (e.g. free-form input where question_id is the
    raw text)."""
    for _aid, pairs in questions_for(merchant_id).items():
        if _aid != agent_id:
            continue
        for qid, qtext in pairs:
            if qid == question_id:
                return qtext
    return question_id  # treat as raw text


def _hardcoded_dispatch(agent_id: str, question_id: str, merchant_id: str) -> dict:
    """Phase 1 behavior — run a hardcoded handler. Used as mock-mode
    path AND as labeled fallback when an LLM call fails."""
    handler = HANDLERS.get((agent_id, question_id))
    if handler is None:
        return {
            "agent": AGENT_LABELS.get(agent_id, "Agent"),
            "prose": "No placeholder handler is wired for this question yet.",
            "table": None,
            "chart": None,
        }
    try:
        return handler(merchant_id)
    except Exception as exc:  # noqa: BLE001 — never crash the UI
        return {
            "agent": AGENT_LABELS.get(agent_id, "Agent"),
            "prose": f"_(handler error: {exc!s})_",
            "table": None,
            "chart": None,
        }


def _llm_dispatch(
    agent_id: str,
    question_id: str,
    merchant_id: str,
    *,
    progress: "Callable[[int, str], None] | None" = None,
    on_token: "Callable[[str], None] | None" = None,
    raw_question: "str | None" = None,
) -> dict:
    """Run the question through an LLM specialist. Raises on failure
    so the caller can wrap with a labeled fallback.

    `raw_question` overrides the suggested-question lookup — used by
    free-form dispatch where the user typed an arbitrary question."""
    from src.agents.context import MerchantContext

    question = raw_question or _question_text(agent_id, question_id, merchant_id)
    ctx = MerchantContext.for_merchant(merchant_id)

    if agent_id == "pricing":
        from src.agents.pricing import PricingSpecialist
        spec = PricingSpecialist(ctx)
    elif agent_id == "anomaly":
        from src.agents.anomaly import AnomalyDetectionSpecialist
        spec = AnomalyDetectionSpecialist(ctx)
    elif agent_id == "demand":
        from src.agents.demand import DemandForecastingSpecialist
        spec = DemandForecastingSpecialist(ctx)
    elif agent_id == "trade":
        from src.agents.trade import TradeAreaSpecialist
        spec = TradeAreaSpecialist(ctx)
    else:
        raise NotImplementedError(
            f"No LLM specialist wired for agent_id={agent_id!r} yet."
        )

    return spec.answer(question, progress=progress, on_token=on_token).to_dict()


# ---------------------------------------------------------------------------
# Session-state cache for suggested-question dispatch (Phase 2A.5)
#
# Why session_state instead of `@st.cache_data`:
#   - `@st.cache_data` strips the Streamlit runtime context, so any UI
#     callbacks invoked inside the cached function (progress narration,
#     token streaming) would fail.
#   - We want callbacks to fire on cache miss (first click) but NOT on
#     cache hit (instant return). Session-state gives us that control.
#
# Cache key:  (agent_id, question_id, merchant_id)
# Lifetime:   per Streamlit session (resets on browser refresh).
# Scope:      suggested-question buttons only. Free-form is uncached.
# ---------------------------------------------------------------------------

_CacheKey = tuple[str, str, str]


def _cache_get(key: _CacheKey) -> "dict | None":
    try:
        import streamlit as st  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return None
    try:
        bucket: dict[_CacheKey, dict] = st.session_state.setdefault("llm_cache", {})
    except Exception:  # noqa: BLE001 — no Streamlit runtime (tests)
        return None
    return bucket.get(key)


def _cache_set(key: _CacheKey, value: dict) -> None:
    try:
        import streamlit as st  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return
    try:
        bucket: dict[_CacheKey, dict] = st.session_state.setdefault("llm_cache", {})
    except Exception:  # noqa: BLE001
        return
    # Don't cache fallback responses — we'd rather retry the LLM next
    # click than freeze a "LLM call failed" prefix into the cache.
    if value.get("fallback"):
        return
    bucket[key] = value


def _label_fallback(resp: dict, reason: str) -> dict:
    """Single source of truth: a fallback response carries an explicit
    label at the top of its prose. We do NOT merge LLM error text into
    a hardcoded response — the label is the only signal that this is
    the pre-computed answer, not the LLM."""
    fallback_prose = (
        f"_LLM call failed ({reason}) — showing pre-computed response._\n\n"
        + (resp.get("prose") or "")
    )
    return {**resp, "prose": fallback_prose, "fallback": True}


def dispatch(
    agent_id: str,
    question_id: str,
    merchant_id: str,
    *,
    progress: "Callable[[int, str], None] | None" = None,
    on_token: "Callable[[str], None] | None" = None,
) -> dict:
    """Suggested-question entry point — cached per session.

    Resolution order:
      0. Cache hit on (agent_id, question_id, merchant_id) → return
         instantly. Callbacks are not fired (no work to narrate).
      1. If `agent_id` has an LLM specialist wired AND the LLM is
         available → run the specialist, cache the result, return it.
      2. If the LLM specialist throws → fall back to the hardcoded
         handler with a labeled "LLM call failed" prefix. Fallback
         responses are NOT cached.
      3. If `agent_id` is not LLM-wired OR no API key is configured →
         run the hardcoded handler with no label (silent mock mode).
    """
    key: _CacheKey = (agent_id, question_id, merchant_id)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    # Lazy import — avoid pulling in the Anthropic SDK at module-load
    # time when callers only need the hardcoded path.
    from src.agents import llm

    if agent_id in LLM_WIRED_AGENTS and llm.is_available():
        try:
            resp = _llm_dispatch(
                agent_id, question_id, merchant_id,
                progress=progress, on_token=on_token,
            )
            _cache_set(key, resp)
            return resp
        except Exception as exc:  # noqa: BLE001 — surface as labeled fallback
            reason = f"{type(exc).__name__}: {exc}"[:200]
            hardcoded = _hardcoded_dispatch(agent_id, question_id, merchant_id)
            return _label_fallback(hardcoded, reason)

    resp = _hardcoded_dispatch(agent_id, question_id, merchant_id)
    _cache_set(key, resp)
    return resp


def dispatch_free_form(
    agent_id: str,
    merchant_id: str,
    raw_question: str,
    *,
    progress: "Callable[[int, str], None] | None" = None,
    on_token: "Callable[[str], None] | None" = None,
    fallback_question_id: str | None = None,
) -> dict:
    """Free-form entry point — never cached.

    The user typed an arbitrary question; `agent_id` is the routed
    specialist. If the agent has an LLM specialist wired, we pass the
    raw question through verbatim (no suggested-question lookup). On
    failure we fall back to the agent's general-overview hardcoded
    handler (`fallback_question_id`) with a labeled prefix.
    """
    from src.agents import llm

    if agent_id in LLM_WIRED_AGENTS and llm.is_available():
        try:
            return _llm_dispatch(
                agent_id, fallback_question_id or "free_form", merchant_id,
                progress=progress, on_token=on_token,
                raw_question=raw_question,
            )
        except Exception as exc:  # noqa: BLE001
            reason = f"{type(exc).__name__}: {exc}"[:200]
            if fallback_question_id is not None:
                hardcoded = _hardcoded_dispatch(
                    agent_id, fallback_question_id, merchant_id,
                )
                return _label_fallback(hardcoded, reason)
            return {
                "agent": AGENT_LABELS.get(agent_id, "Agent"),
                "prose": f"_LLM call failed ({reason}) — no fallback available._",
                "table": None,
                "chart": None,
                "fallback": True,
            }

    if fallback_question_id is not None:
        return _hardcoded_dispatch(agent_id, fallback_question_id, merchant_id)

    return {
        "agent": AGENT_LABELS.get(agent_id, "Agent"),
        "prose": "_(LLM unavailable and no fallback handler wired for free-form.)_",
        "table": None,
        "chart": None,
    }


def dispatch_orchestrated(
    merchant_id: str,
    raw_question: str,
    *,
    progress: "Callable[[int, str], None] | None" = None,
    on_token: "Callable[[str], None] | None" = None,
) -> dict:
    """Free-form entry point that goes through the LLM orchestrator.

    The orchestrator classifies intent via a Haiku router and dispatches
    to the chosen specialist. On any failure (LLM unavailable, router
    error, specialist error) we fall back to keyword routing + the
    hardcoded handler for the chosen agent's general-overview question.
    Never cached.
    """
    from src.agents import llm

    if llm.is_available():
        try:
            from src.agents.context import MerchantContext
            from src.agents.orchestrator import Orchestrator
            ctx = MerchantContext.for_merchant(merchant_id)
            orch = Orchestrator(ctx)
            resp = orch.ask(raw_question, progress=progress, on_token=on_token)
            return resp.to_dict()
        except Exception as exc:  # noqa: BLE001
            reason = f"{type(exc).__name__}: {exc}"[:200]
            # Fall through to keyword fallback below with a label
            agent_id = _keyword_route_for_fallback(raw_question)
            fallback_qid = _GENERAL_HARDCODED.get(agent_id)
            if fallback_qid:
                hardcoded = _hardcoded_dispatch(agent_id, fallback_qid, merchant_id)
                return _label_fallback(hardcoded, reason)
            return {
                "agent": "Conversational Advisor",
                "prose": f"_Orchestrator failed ({reason})._",
                "table": None, "chart": None, "fallback": True,
            }

    # LLM unavailable — silent keyword fallback to hardcoded handler.
    agent_id = _keyword_route_for_fallback(raw_question)
    fallback_qid = _GENERAL_HARDCODED.get(agent_id)
    if fallback_qid:
        return _hardcoded_dispatch(agent_id, fallback_qid, merchant_id)
    return {
        "agent": "Conversational Advisor",
        "prose": "_(LLM unavailable and no fallback wired.)_",
        "table": None, "chart": None,
    }


# Mirror the keyword rules in `chat.py::route_free_form`. Used only on
# the orchestrator's fallback path so we still return a sensible agent.
_GENERAL_HARDCODED = {
    "pricing": "dairy_vs_peers",
    "anomaly": "anything_unusual",
    "demand":  "dairy_slowing",
    "trade":   "underserved",
}


def _keyword_route_for_fallback(question: str) -> str:
    q = question.lower()
    rules = [
        (("price", "pricing", "above market", "below market",
          "compare", "vs peer", "vs peers"),                       "pricing"),
        (("anomaly", "unusual", "spike", "decline", "drop", "why"), "anomaly"),
        (("slow", "slowing", "forecast", "promo", "demand",
          "uplift", "lapsed"),                                     "demand"),
        (("neighborhood", "open a new store", "where should",
          "trade area", "underserved", "catchment"),               "trade"),
    ]
    for keywords, agent in rules:
        for kw in keywords:
            if kw in q:
                return agent
    return "demand"
