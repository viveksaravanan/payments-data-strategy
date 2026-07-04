#!/usr/bin/env python3
"""Generate Section 1 ("The world behind the data") of the public methodology
report `docs/index.html`.

Design note — traceability. Every figure this script renders is **recomputed
from `data/raw/*.parquet` + `src/generate/config/metro.yaml`**; nothing is
copied from a prompt or hand-typed. Editorial metadata (real-world anchors,
source URLs, target bands, SOURCED/ASSUMPTION tags, and the fixed neighborhood
descriptions) is authored in the small tables below — the *numbers* beside them
are always the measured values. `docs/DQ_REPORT.md` is a cross-check, not the
source.

Modes:
  --print-numbers   Print the STEP-1 zone table + scorecard, then exit (the
                    review checkpoint — no HTML is written).
  (default)         Build the Section-1 fragment and splice it into
                    docs/index.html, and refresh the global header stat band.

Run: `uv run python scripts/build_report_section1.py [--print-numbers]`
"""
from __future__ import annotations

import argparse
import html
import json
import math
import random
import re
from pathlib import Path

import duckdb
import yaml

REPO = Path(__file__).resolve().parents[1]
RAW = REPO / "data" / "raw"
METRO_YAML = REPO / "src" / "generate" / "config" / "metro.yaml"
INDEX_HTML = REPO / "docs" / "index.html"

LEDE = ('<p class="lede">A working prototype of cross-merchant payments intelligence: '
        "each merchant can compare itself to others, while never seeing another merchant's "
        'raw data. This page explains how a system like this can be built.</p>'
        '<p class="thesis"><b>The thesis:</b> a payment terminal sits in the middle of every '
        'checkout — reading the basket, the payment, and the merchant context. Across an '
        'installed base of merchants, that one vantage point becomes cross-merchant '
        'intelligence no single merchant can see on its own.</p>')

GROCERY = ("KRG", "ACM", "WDX")
QSR = ("TBL", "BKG", "CFA")
BANNER_NAME = {
    "KRG": "Kroger", "ACM": "Acme", "WDX": "Winn-Dixie",
    "TBL": "Taco Bell", "BKG": "Burger King", "CFA": "Chick-fil-A",
}
# Marker colors (segment-distinct, chosen to pop on muted CartoDB Positron tiles).
BANNER_COLOR = {
    "KRG": "#2a78d6", "ACM": "#e34948", "WDX": "#1e9e57",
    "CFA": "#d6449a", "TBL": "#6a3fb0", "BKG": "#eb6834",
}

# §A13 fresh/premium departments (matches scripts/pilot_report.py::FRESH_DEPTS).
FRESH_DEPTS = ("Meat & Seafood", "Produce", "Bakery", "Dairy & Eggs")
# §A4 center-store departments.
CENTER_DEPTS = ("Dry Grocery", "Snacks & Candy", "Beverages")

WINDOW_DAYS = 90
ANNUALIZE = 365.0 / WINDOW_DAYS


# ----- connection + config ----------------------------------------------------

def _con() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    for t in ("customers", "stores", "transactions", "transaction_items", "products"):
        con.execute(
            f"CREATE VIEW {t} AS SELECT * FROM read_parquet('{RAW / (t + '.parquet')}')"
        )
    return con


def load_zones() -> list[dict]:
    """The 8 zones from metro.yaml, ordered by residential weight (biggest base
    first) — id, name, affluence, residential_weight, centroid lat/long."""
    doc = yaml.safe_load(METRO_YAML.read_text())
    zones = []
    for z in doc["zones"]:
        c = z["centroid"]
        zones.append({
            "id": z["id"], "name": z["name"], "affluence": float(z["affluence"]),
            "residential_weight": float(z["residential_weight"]),
            "lat": float(c["lat"]), "long": float(c["long"]),
        })
    return zones


# ----- measured values (all recomputed from data/raw) -------------------------

def measure(con: duckdb.DuckDBPyConnection) -> dict:
    """Return every measured value Section 1 states, computed from data/raw."""
    M: dict = {}
    q = lambda sql: con.execute(sql).fetchall()
    one = lambda sql: con.execute(sql).fetchone()[0]

    # Totals.
    M["cards"] = one("SELECT COUNT(*) FROM customers")
    M["stores"] = one("SELECT COUNT(*) FROM stores")
    M["txns"] = one("SELECT COUNT(*) FROM transactions")
    M["lines"] = one("SELECT COUNT(*) FROM transaction_items")

    # Per-banner store counts + annualized AUV (per-store sales × 365/90).
    n_stores = dict(q("SELECT banner_code, COUNT(*) FROM stores GROUP BY 1"))
    bank_sales = dict(q("SELECT banner_code, SUM(subtotal) FROM transactions GROUP BY 1"))
    M["n_stores"] = n_stores
    M["auv"] = {b: bank_sales[b] / n_stores[b] * ANNUALIZE for b in BANNER_NAME}

    # Grocery dollar split (share of grocery sales per banner).
    groc_tot = sum(bank_sales[b] for b in GROCERY)
    M["grocery_split"] = {b: 100.0 * bank_sales[b] / groc_tot for b in GROCERY}

    # QSR: CFA vs TB+BK sales ratio + per-banner average check.
    M["cfa_ratio"] = bank_sales["CFA"] / (bank_sales["TBL"] + bank_sales["BKG"])
    M["qsr_check"] = dict(q(
        "SELECT banner_code, AVG(subtotal) FROM transactions "
        "WHERE segment='qsr' GROUP BY 1"
    ))
    M["grocery_aov"] = one("SELECT AVG(subtotal) FROM transactions WHERE segment='grocery'")
    M["banner_basket"] = dict(q("SELECT banner_code, AVG(subtotal) FROM transactions GROUP BY 1"))

    # Annualized sales totals by segment.
    M["grocery_sales_yr"] = groc_tot * ANNUALIZE
    M["qsr_sales_yr"] = sum(bank_sales[b] for b in QSR) * ANNUALIZE

    # Private-label UNIT share per grocery banner (join line→txn→products on sku).
    pl = q(
        "SELECT t.banner_code, "
        "  SUM(CASE WHEN p.private_label THEN i.qty ELSE 0 END)::DOUBLE / SUM(i.qty) "
        "FROM transaction_items i "
        "JOIN transactions t ON i.txn_id = t.txn_id "
        "JOIN products p ON i.sku = p.sku "
        "WHERE t.segment='grocery' GROUP BY 1"
    )
    M["pl_unit_share"] = {b: 100.0 * v for b, v in pl}

    # Department sales mix (grocery $ by functional_department).
    dept = q(
        "SELECT p.functional_department, SUM(i.line_total) "
        "FROM transaction_items i JOIN transactions t ON i.txn_id=t.txn_id "
        "JOIN products p ON i.sku=p.sku WHERE t.segment='grocery' GROUP BY 1"
    )
    dtot = sum(v for _, v in dept)
    dshare = {d: 100.0 * v / dtot for d, v in dept}
    M["dept_share"] = dshare
    M["center_store_share"] = sum(dshare.get(d, 0.0) for d in CENTER_DEPTS)
    M["meat_share"] = dshare.get("Meat & Seafood", 0.0)
    M["produce_share"] = dshare.get("Produce", 0.0)
    M["dairy_share"] = dshare.get("Dairy & Eggs", 0.0)

    # Fresh/premium $ share by affluence tercile (grocery lines).
    fresh_list = "(" + ",".join(f"'{d}'" for d in FRESH_DEPTS) + ")"
    tiers = q(f"""
        WITH gi AS (
          SELECT i.line_total, p.functional_department AS dept, c.affluence,
                 NTILE(3) OVER (ORDER BY c.affluence) AS tier
          FROM transaction_items i
          JOIN transactions t ON i.txn_id=t.txn_id
          JOIN products p ON i.sku=p.sku
          JOIN customers c ON t.customer_token=c.card_id
          WHERE t.segment='grocery'
        )
        SELECT tier,
               SUM(CASE WHEN dept IN {fresh_list} THEN line_total ELSE 0 END)/SUM(line_total)
        FROM gi GROUP BY tier ORDER BY tier
    """)
    M["fresh_by_tier"] = [100.0 * v for _, v in tiers]

    # Payment mix.
    M["contactless"] = one(
        "SELECT 100.0*AVG(CASE WHEN entry_mode='contactless' THEN 1 ELSE 0 END) FROM transactions"
    )
    M["wallet"] = one(
        "SELECT 100.0*AVG(CASE WHEN wallet_at_tap THEN 1 ELSE 0 END) FROM transactions"
    )
    M["debit_wdx"] = one(
        "SELECT 100.0*AVG(CASE WHEN tender='debit' THEN 1 ELSE 0 END) FROM transactions WHERE banner_code='WDX'"
    )
    M["debit_acm"] = one(
        "SELECT 100.0*AVG(CASE WHEN tender='debit' THEN 1 ELSE 0 END) FROM transactions WHERE banner_code='ACM'"
    )

    # Timing. Grocery weekend/weekday per-day ratio.
    dow = dict(q(
        "SELECT CASE WHEN dayofweek(txn_ts) IN (0,6) THEN 'we' ELSE 'wd' END, COUNT(*) "
        "FROM transactions WHERE segment='grocery' GROUP BY 1"
    ))
    M["weekend_weekday"] = (dow["we"] / 2.0) / (dow["wd"] / 5.0)
    M["cfa_sunday"] = one(
        "SELECT COUNT(*) FROM transactions WHERE banner_code='CFA' AND dayofweek(txn_ts)=0"
    )
    # Late-night daypart wraps past midnight (DQ T5: hour>=21 OR hour<3).
    M["tbl_latenight"] = one(
        "SELECT 100.0*AVG(CASE WHEN EXTRACT(hour FROM txn_ts)>=21 "
        "OR EXTRACT(hour FROM txn_ts)<3 THEN 1 ELSE 0 END) "
        "FROM transactions WHERE banner_code='TBL'"
    )
    M["bkg_breakfast"] = one(
        "SELECT 100.0*AVG(CASE WHEN EXTRACT(hour FROM txn_ts) BETWEEN 6 AND 9 THEN 1 ELSE 0 END) "
        "FROM transactions WHERE banner_code='BKG'"
    )

    # Participation.
    M["grocery_active"] = one(
        "SELECT 100.0*COUNT(DISTINCT customer_token)/155000.0 FROM transactions WHERE segment='grocery'"
    )
    M["qsr_active"] = one(
        "SELECT 100.0*COUNT(DISTINCT customer_token)/155000.0 FROM transactions WHERE segment='qsr'"
    )
    M["both_active"] = one("""
        SELECT 100.0*COUNT(*)/155000.0 FROM (
          SELECT customer_token FROM transactions GROUP BY 1
          HAVING COUNT(DISTINCT segment) >= 2)
    """)

    # Loyalty concentration (DQ T9): per customer, the share of their grocery
    # visits at their most-visited banner, averaged over customers.
    M["loyalty_conc"] = one("""
        WITH pc AS (
          SELECT customer_token, banner_code, COUNT(*)::DOUBLE AS n,
                 SUM(COUNT(*)) OVER (PARTITION BY customer_token) AS tot
          FROM transactions WHERE segment='grocery' GROUP BY 1,2),
        mx AS (SELECT customer_token, MAX(n/tot) AS share FROM pc GROUP BY 1)
        SELECT 100.0*AVG(share) FROM mx
    """)

    # Heavy-tail: top-20% of grocery baskets' share of grocery units.
    M["heavy_tail"] = one("""
        WITH b AS (
          SELECT t.txn_id, SUM(i.qty) AS units,
                 NTILE(5) OVER (ORDER BY SUM(i.qty) DESC) AS q
          FROM transactions t JOIN transaction_items i ON t.txn_id=i.txn_id
          WHERE t.segment='grocery' GROUP BY t.txn_id)
        SELECT 100.0*SUM(CASE WHEN q=1 THEN units ELSE 0 END)/SUM(units) FROM b
    """)

    # No-banner-cheapest (DQ T14): per grocery functional_subcategory carried by
    # 2+ banners, the banner with the lowest MEDIAN shelf price wins the subcategory;
    # report the distribution of winners (WDX, the value banner, should top ~68%).
    cheap = q("""
        WITH med AS (
          SELECT functional_subcategory AS sub, banner_code AS b, MEDIAN(shelf_price) AS mp
          FROM products WHERE segment='grocery' GROUP BY 1,2),
        multi AS (SELECT sub FROM med GROUP BY sub HAVING COUNT(*)>=2),
        ranked AS (
          SELECT sub, b, ROW_NUMBER() OVER (PARTITION BY sub ORDER BY mp, b) AS rn
          FROM med WHERE sub IN (SELECT sub FROM multi)),
        winner AS (SELECT sub, b FROM ranked WHERE rn=1)
        SELECT b, 100.0*COUNT(*)/SUM(COUNT(*)) OVER () FROM winner GROUP BY b ORDER BY 2 DESC
    """)
    M["cheapest_share"] = dict(cheap)

    # Affinity lifts (grocery pairs, functional_subcategory): P(B|A) / P(B).
    M["affinity"] = {}
    pairs = [
        ("Pasta", "Pasta Sauce"),
        ("Cereal", "2% Reduced-Fat Milk"),
        ("Potato & Tortilla Chips", "Salsa & Dips"),
    ]
    for a, b in pairs:
        M["affinity"][f"{a}→{b}"] = _lift(con, a, b)

    # QSR drink-attach P(drink | entrée) per banner.
    M["drink_attach"] = {b: _drink_attach(con, b) for b in QSR}

    return M


def _lift(con: duckdb.DuckDBPyConnection, a: str, b: str) -> float:
    """Affinity lift = P(B in basket | A in basket) / P(B in basket), grocery baskets,
    keyed on functional_subcategory."""
    row = con.execute("""
        WITH baskets AS (
          SELECT t.txn_id,
                 MAX(CASE WHEN p.functional_subcategory = ? THEN 1 ELSE 0 END) AS has_a,
                 MAX(CASE WHEN p.functional_subcategory = ? THEN 1 ELSE 0 END) AS has_b
          FROM transactions t
          JOIN transaction_items i ON t.txn_id=i.txn_id
          JOIN products p ON i.sku=p.sku
          WHERE t.segment='grocery' GROUP BY t.txn_id)
        SELECT AVG(has_b), AVG(CASE WHEN has_a=1 THEN has_b END) FROM baskets
    """, [a, b]).fetchone()
    p_b, p_b_given_a = row
    return (p_b_given_a / p_b) if p_b and p_b_given_a else 0.0


def _drink_attach(con: duckdb.DuckDBPyConnection, banner: str) -> float:
    """P(a drink line is present | basket has an entrée), for one QSR banner.
    Uses functional_department to spot drink vs entrée lines."""
    row = con.execute("""
        WITH baskets AS (
          SELECT t.txn_id,
                 MAX(CASE WHEN p.functional_department ILIKE '%Entr%' OR p.functional_category ILIKE '%Entr%' THEN 1 ELSE 0 END) AS has_entree,
                 MAX(CASE WHEN p.functional_department ILIKE '%Drink%' OR p.functional_department ILIKE '%Beverage%'
                            OR p.functional_category ILIKE '%Drink%' OR p.functional_category ILIKE '%Beverage%' THEN 1 ELSE 0 END) AS has_drink
          FROM transactions t
          JOIN transaction_items i ON t.txn_id=i.txn_id
          JOIN products p ON i.sku=p.sku
          WHERE t.banner_code = ? GROUP BY t.txn_id)
        SELECT AVG(CASE WHEN has_entree=1 THEN has_drink END) FROM baskets
    """, [banner]).fetchone()
    return row[0] or 0.0


def chart_data(con: duckdb.DuckDBPyConnection) -> dict:
    """Distributions the SVG charts need (histograms / series)."""
    q = lambda sql: con.execute(sql).fetchall()
    CD: dict = {}

    # Grocery basket-size histogram (units per basket, capped at 25+).
    CD["basket_hist"] = q("""
        WITH b AS (
          SELECT t.txn_id, LEAST(SUM(i.qty), 25) AS units
          FROM transactions t JOIN transaction_items i ON t.txn_id=i.txn_id
          WHERE t.segment='grocery' GROUP BY t.txn_id)
        SELECT units, COUNT(*) FROM b GROUP BY units ORDER BY units
    """)

    # Grocery transactions by day of week (0=Sun … 6=Sat).
    CD["dow"] = dict(q(
        "SELECT dayofweek(txn_ts), COUNT(*) FROM transactions "
        "WHERE segment='grocery' GROUP BY 1"
    ))

    # Hour-of-day counts per QSR banner (0..23).
    CD["hourly"] = {}
    for b in QSR:
        rows = dict(q(
            f"SELECT EXTRACT(hour FROM txn_ts), COUNT(*) FROM transactions "
            f"WHERE banner_code='{b}' GROUP BY 1"
        ))
        CD["hourly"][b] = [int(rows.get(h, 0)) for h in range(24)]

    # Weekly transaction totals across the window (all segments).
    CD["weekly"] = q(
        "SELECT date_trunc('week', txn_ts) AS wk, COUNT(*) "
        "FROM transactions GROUP BY 1 ORDER BY 1"
    )
    return CD


def zone_table(con: duckdb.DuckDBPyConnection, zones: list[dict]) -> list[dict]:
    """Per-zone: customers, both-segment cards, store counts by banner, affluence."""
    cust = dict(con.execute("SELECT home_zone, COUNT(*) FROM customers GROUP BY 1").fetchall())
    both = dict(con.execute("""
        SELECT c.home_zone, COUNT(*) FROM (
          SELECT customer_token FROM transactions GROUP BY 1
          HAVING COUNT(DISTINCT segment) >= 2) x
        JOIN customers c ON x.customer_token = c.card_id
        GROUP BY 1
    """).fetchall())
    stores = con.execute(
        "SELECT zone_id, banner_code, COUNT(*) FROM stores GROUP BY 1,2"
    ).fetchall()
    by_zone: dict[str, dict[str, int]] = {}
    for z, b, n in stores:
        by_zone.setdefault(z, {})[b] = n
    rows = []
    for z in zones:
        sb = by_zone.get(z["id"], {})
        rows.append({
            **z,
            "customers": cust.get(z["id"], 0),
            "both": both.get(z["id"], 0),
            "stores_by_banner": sb,
            "store_count": sum(sb.values()),
        })
    return rows


# ----- STEP 1 print -----------------------------------------------------------

def print_numbers(con: duckdb.DuckDBPyConnection, zones: list[dict], M: dict) -> None:
    rows = zone_table(con, zones)
    print("\n=== STEP 1 — zone table (recomputed from data/raw + metro.yaml) ===\n")
    hdr = f"{'zone':<16}{'customers':>10}{'both-seg':>10}  {'stores (K/A/W·T/B/C)':<22}{'affluence':>10}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        sb = r["stores_by_banner"]
        sig = "{}/{}/{}·{}/{}/{}={}".format(
            sb.get("KRG", 0), sb.get("ACM", 0), sb.get("WDX", 0),
            sb.get("TBL", 0), sb.get("BKG", 0), sb.get("CFA", 0), r["store_count"])
        print(f"{r['id']:<16}{r['customers']:>10,}{r['both']:>10,}  {sig:<22}{r['affluence']:>10.2f}")
    print(f"\nTotals: {M['cards']:,} customers · {M['stores']} stores · "
          f"{M['txns']:,} txns · {M['lines']:,} line items")

    def line(label, val):
        print(f"  {label:<44}{val}")

    print("\n=== Scorecard (measured @155k, recomputed) ===\n")
    line("AUV KRG/ACM/WDX ($M/yr)",
         "/".join(f"{M['auv'][b]/1e6:.1f}" for b in GROCERY))
    line("AUV CFA/TBL/BKG ($M/yr)",
         "/".join(f"{M['auv'][b]/1e6:.1f}" for b in ("CFA", "TBL", "BKG")))
    line("Grocery $ split KRG/ACM/WDX",
         "/".join(f"{M['grocery_split'][b]:.1f}" for b in GROCERY))
    line("CFA vs TB+BK sales ratio", f"{M['cfa_ratio']:.2f}x")
    line("Grocery AOV", f"${M['grocery_aov']:.2f}")
    line("QSR check CFA/BK/TB",
         "/".join(f"${M['qsr_check'][b]:.2f}" for b in ("CFA", "BKG", "TBL")))
    line("PL unit share KRG/ACM/WDX",
         "/".join(f"{M['pl_unit_share'][b]:.1f}" for b in GROCERY))
    line("Dept mix center/meat/produce/dairy",
         f"{M['center_store_share']:.1f}/{M['meat_share']:.1f}/{M['produce_share']:.1f}/{M['dairy_share']:.1f}")
    line("Fresh $ by affluence tier (lo→hi)",
         "→".join(f"{v:.1f}" for v in M["fresh_by_tier"]))
    line("Contactless / wallet-at-tap", f"{M['contactless']:.1f} / {M['wallet']:.1f}")
    line("Grocery debit WDX / ACM", f"{M['debit_wdx']:.1f} / {M['debit_acm']:.1f}")
    line("Weekend/weekday grocery ratio", f"{M['weekend_weekday']:.3f}")
    line("CFA Sunday txns", f"{M['cfa_sunday']}")
    line("TBL 9pm+ / BKG breakfast", f"{M['tbl_latenight']:.1f} / {M['bkg_breakfast']:.1f}")
    line("Participation groc/qsr/both active",
         f"{M['grocery_active']:.1f}/{M['qsr_active']:.1f}/{M['both_active']:.1f}")
    line("Loyalty concentration", f"{M['loyalty_conc']:.1f}")
    line("Heavy-tail top-20% unit share", f"{M['heavy_tail']:.1f}")
    line("No-banner-cheapest (max=WDX)",
         "  ".join(f"{b}:{v:.0f}%" for b, v in sorted(M["cheapest_share"].items(), key=lambda x: -x[1])))
    line("Affinity lifts", "  ".join(f"{k}: {v:.2f}x" for k, v in M["affinity"].items()))
    line("QSR drink-attach CFA/BK/TB",
         "/".join(f"{M['drink_attach'][b]:.2f}" for b in ("CFA", "BKG", "TBL")))
    line("Grocery / QSR sales ($M/yr)",
         f"{M['grocery_sales_yr']/1e6:.0f} / {M['qsr_sales_yr']/1e6:.0f}")
    print()


# ----- editorial metadata (authored; the numbers beside these stay measured) --

# Fixed neighborhood tier labels + descriptions (task §5).
ZONE_TIER = {
    "matthews": "Mid", "eastway": "Low", "ballantyne": "High",
    "university_city": "Mid-low", "dilworth": "High", "noda": "Mid (rising)",
    "center_city": "Mid-high", "cabarrus_edge": "Low-mid",
}
ZONE_DESC = {
    "matthews": "Established SE suburban town; families and the largest residential base — big weekly-stockup baskets.",
    "eastway": "Diverse, working-class east-side corridor — the value-banner heartland; highest store-brand and debit share.",
    "ballantyne": "Affluent master-planned far-south suburb; the metro's premium anchor — national brands, credit, and contactless run highest.",
    "university_city": "Anchored by the university to the northeast; students plus mixed residential — value-leaning and QSR-heavy.",
    "dilworth": "Historic, tree-lined neighborhood south of uptown; established professionals — fresh-forward baskets.",
    "noda": "The arts district, younger and gentrifying — a mixed basket.",
    "center_city": "The dense urban core; younger professionals — smaller quick-trip baskets.",
    "cabarrus_edge": "The exurban northern fringe; lower-density, value-oriented households.",
}
# Merchant card copy (task §5). Numbers in the stat grid come from M at render
# time; the bullets blend modeled traits (assortment, private-label depth,
# pricing tilt, who shops there, daypart) with real signature products for color.
MERCHANT_META = {
    "KRG": {"tier": "Mainstream, large-format",
            "bullets": [
                "Fullest assortment in the panel — ~1,350 SKUs across every department.",
                "Deepest own-brand program — ~27% of units are store brands (Kroger / Simple Truth).",
                "Priced cheapest on meat and center-store staples; near-market elsewhere.",
                "Draws the biggest baskets, spanning affluent and mainstream neighborhoods.",
            ],
            "distinct": "Widest own-brand range in the panel — value shoppers reach for it most."},
    "ACM": {"tier": "Mainstream, fresh-forward",
            "bullets": [
                "Fresh-forward premium tilt — over-indexes on produce and bakery.",
                "Leads meat <em>dollars</em> through premium cuts (priced ~+28% there).",
                "Lightest store-brand lean (~19%, “Signature SELECT”), with a premium/organic tail.",
                "Clusters in the five most affluent neighborhoods.",
            ],
            "distinct": "Fresh-forward mid-market, with a lighter store-brand lean than Kroger."},
    "WDX": {"tier": "Value",
            "bullets": [
                "Value banner — leanest assortment (~1,050 SKUs), everyday-low staples.",
                "Heaviest store-brand lean (~25%, “SE Grocers”).",
                "Meat is a traffic driver — over-indexes on meat <em>units</em>.",
                "Smaller baskets, high trip counts in working-class zones.",
            ],
            "distinct": "The value banner — most store-brand, meat volume up front."},
    "CFA": {"tier": "Premium chicken",
            "bullets": [
                "Highest sales per unit in the panel (~$8M/yr) from just six restaurants.",
                "Signature: Original Chicken Sandwich, Waffle Fries, Icedream.",
                "Closed Sundays; lunch- and dinner-driven, with no late-night.",
                "Highest check; draws affluent suburban families and campuses.",
            ],
            "distinct": "Six restaurants out-earn the other seventeen QSR units combined."},
    "TBL": {"tier": "Value, late-night",
            "bullets": [
                "Value Mexican-inspired menu — the lowest check in the panel.",
                "Most locations of any banner (nine restaurants).",
                "Signature: Crunchwrap Supreme, Doritos Locos Tacos, Baja Blast.",
                "A real late-night daypart — ~18% of sales land after 9pm.",
            ],
            "distinct": "The most locations and the lowest check — a wide, value-priced footprint."},
    "BKG": {"tier": "Value, breakfast",
            "bullets": [
                "Flame-grilled value — the price floor of the QSR set.",
                "Signature: Whopper, Chicken Fries, Croissan’wich.",
                "Carries a breakfast daypart alongside its all-day menu.",
                "Lowest per-unit volume, spread across a broad roadside footprint.",
            ],
            "distinct": "The lowest per-unit volume — the value floor of the QSR set."},
}
SEGMENT_LABEL = {"KRG": "Grocery", "ACM": "Grocery", "WDX": "Grocery",
                 "CFA": "QSR", "TBL": "QSR", "BKG": "QSR"}

# Sources block (task §6) — anchor → what it supports + URL.
SOURCES = [
    ("Grocery per-store sales & store size", "FMI Food Industry Facts",
     "https://www.fmi.org/our-research/food-industry-facts"),
    ("Per-banner private-label (unit) share", "Numerator Private Label Trends Tracker",
     "https://www.numerator.com/press/top-5-private-label-brands-are-owned-by-walmart-krogers-smart-way-is-fastest-growing-store-brand-numerator-reports/"),
    ("QSR per-unit sales (AUV)", "QSR Magazine, The QSR 50",
     "https://www.qsrmagazine.com/story/the-2025-qsr-50-fast-foods-leading-annual-report/"),
    ("Payment mix (credit/debit/contactless)", "Federal Reserve, Diary of Consumer Payment Choice",
     "https://www.frbservices.org/news/research/2025-findings-from-the-diary-of-consumer-payment-choice"),
    ("Kroger own-brand scale (supporting)", "Kroger FY2025 10-K",
     "https://www.stocktitan.net/sec-filings/KR/10-k-kroger-co-files-annual-report-38d6497b0466.html"),
    ("Grocery department sales mix", "Circana / 210 Analytics 2024 (department mix)", ""),
]


def esc(s) -> str:
    return html.escape(str(s), quote=True)


# ----- anatomy + catalog (real rows) -----------------------------------------

def _txn_detail(con, txn_id: str) -> dict:
    h = con.execute("""
        SELECT t.txn_id, t.customer_token, t.store_id, s.neighborhood, t.banner_code,
               t.txn_ts, t.subtotal, t.tender, t.entry_mode, t.network,
               t.wallet_at_tap, t.wallet_provider, t.connectivity_type
        FROM transactions t JOIN stores s ON t.store_id=s.store_id
        WHERE t.txn_id = ?""", [txn_id]).fetchone()
    cols = ["txn_id", "customer_token", "store_id", "neighborhood", "banner_code",
            "txn_ts", "subtotal", "tender", "entry_mode", "network",
            "wallet_at_tap", "wallet_provider", "connectivity_type"]
    header = dict(zip(cols, h))
    # terminal_id was dropped from the generated data; synthesize it in the
    # strategy-doc §5.2 format (<store_id>-T<NN>) — deterministic per txn.
    header["terminal_id"] = (f'{header["store_id"]}'
                             f'-T{(sum(map(ord, txn_id)) % 6) + 1:02d}')
    lines = con.execute("""
        SELECT i.sku, p.product_name, i.qty, i.unit_price, i.line_total,
               p.functional_category, p.functional_subcategory, p.private_label
        FROM transaction_items i JOIN products p ON i.sku=p.sku
        WHERE i.txn_id = ? ORDER BY i.line_total DESC""", [txn_id]).fetchall()
    header["lines"] = [dict(zip(
        ["sku", "name", "qty", "unit_price", "line_total", "fcat", "fsub", "pl"], r))
        for r in lines]
    return header


def anatomy(con) -> dict:
    """Three real transactions: a Kroger stockup (pasta+sauce+milk), a grocery
    quick trip, and a QSR combo. Deterministic (ORDER BY txn_id)."""
    stockup = con.execute("""
        SELECT t.txn_id FROM transactions t
        JOIN transaction_items i ON t.txn_id=i.txn_id JOIN products p ON i.sku=p.sku
        WHERE t.banner_code='KRG'
        GROUP BY t.txn_id
        HAVING SUM(CASE WHEN p.functional_subcategory='Pasta' THEN 1 ELSE 0 END)>0
           AND SUM(CASE WHEN p.functional_subcategory='Pasta Sauce' THEN 1 ELSE 0 END)>0
           AND SUM(CASE WHEN p.functional_department='Dairy & Eggs' THEN 1 ELSE 0 END)>0
           AND COUNT(*) BETWEEN 8 AND 15
        ORDER BY t.txn_id LIMIT 1""").fetchone()[0]
    quick = con.execute("""
        SELECT t.txn_id FROM transactions t
        WHERE t.banner_code='KRG' AND t.segment='grocery' AND t.n_lines BETWEEN 2 AND 3
        ORDER BY t.txn_id LIMIT 1""").fetchone()[0]
    combo = con.execute("""
        SELECT t.txn_id FROM transactions t
        WHERE t.banner_code='CFA' AND t.n_lines BETWEEN 3 AND 4
        ORDER BY t.txn_id LIMIT 1""").fetchone()[0]
    return {"stockup": _txn_detail(con, stockup),
            "quick": _txn_detail(con, quick),
            "combo": _txn_detail(con, combo)}


def catalog_rows(con) -> list[dict]:
    """~6 real products showing the dual taxonomy + private-label-as-SKU."""
    rows = con.execute("""
        SELECT product_name, brand, private_label, shelf_price,
               functional_department, functional_category, functional_subcategory,
               merchant_department, merchant_category, banner_code, sku
        FROM products WHERE segment='grocery'
        ORDER BY (merchant_department != functional_department) DESC,
                 private_label DESC, sku
        LIMIT 6""").fetchall()
    cols = ["name", "brand", "pl", "price", "fdept", "fcat", "fsub",
            "mdept", "mcat", "banner", "sku"]
    return [dict(zip(cols, r)) for r in rows]


# ----- color helpers ----------------------------------------------------------

def _lerp_hex(c1: str, c2: str, t: float) -> str:
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    m = [round(a[i] + (b[i] - a[i]) * t) for i in range(3)]
    return "#%02x%02x%02x" % tuple(m)


def affluence_color(affl: float, lo: float, hi: float) -> str:
    """Single sequential ramp: pale (low affluence) → deep accent (high)."""
    t = (affl - lo) / (hi - lo) if hi > lo else 0.0
    return _lerp_hex("#eef3f9", "#0F4C81", t)


# ----- SVG chart builders (inline; reuse the report's SVG classes) ------------

ACCENT, ACCENT2, ACCENT3 = "#0F4C81", "#4A7BA6", "#9BB8D3"


def svg_distance_decay() -> str:
    x0, x1, y0, y1 = 60, 600, 34, 200
    dmax, d0 = 4.0, 0.5
    def path(beta):
        pts = []
        for k in range(49):
            d = dmax * k / 48
            w = (d0 / (d + d0)) ** beta
            x = x0 + (x1 - x0) * d / dmax
            y = y1 - (y1 - y0) * w
            pts.append(f"{x:.1f},{y:.1f}")
        return " ".join(pts)
    return f"""<svg viewBox="0 0 640 250" role="img" aria-label="Distance-decay curves: a store's pull drops off with distance, faster for quick-service than grocery">
  <line x1="{x0}" y1="{y1}" x2="{x1}" y2="{y1}" stroke="#C9D0D8"/>
  <line x1="{x0}" y1="{y0}" x2="{x0}" y2="{y1}" stroke="#E3E7EC"/>
  <polyline fill="none" stroke="{ACCENT}" stroke-width="2.5" points="{path(2.0)}"/>
  <polyline fill="none" stroke="{ACCENT2}" stroke-width="2.5" stroke-dasharray="5 4" points="{path(2.2)}"/>
  <text class="ax" x="{x0}" y="{y1+18}">at the store</text>
  <text class="ax" x="{x1}" y="{y1+18}" text-anchor="end">far away</text>
  <text class="ax" x="{x0-6}" y="{y0+4}" text-anchor="end">pull</text>
  <rect x="470" y="30" width="12" height="4" fill="{ACCENT}"/><text class="b-val" x="488" y="37">grocery (β 2.0)</text>
  <rect x="470" y="48" width="12" height="4" fill="{ACCENT2}"/><text class="b-val" x="488" y="55">quick-service (β 2.2)</text>
</svg>"""


def svg_basket_tail(hist: list) -> str:
    x0, x1, y0, y1 = 52, 604, 30, 200
    total = sum(n for _, n in hist)
    maxn = max(n for _, n in hist)
    bw = (x1 - x0) / len(hist)
    bars = []
    for i, (u, n) in enumerate(hist):
        h = (y1 - y0) * n / maxn
        x = x0 + i * bw
        fill = ACCENT if i < len(hist) * 0.8 else ACCENT2
        bars.append(f'<rect x="{x:.1f}" y="{y1-h:.1f}" width="{bw-2:.1f}" height="{h:.1f}" rx="1.5" fill="{fill}"/>')
    return f"""<svg viewBox="0 0 640 250" role="img" aria-label="Distribution of grocery basket sizes, showing a long right tail of large stock-up trips">
  <line x1="{x0}" y1="{y1}" x2="{x1}" y2="{y1}" stroke="#C9D0D8"/>
  {''.join(bars)}
  <text class="ax" x="{x0}" y="{y1+18}">1 item</text>
  <text class="ax" x="{x1}" y="{y1+18}" text-anchor="end">25+ items</text>
  <rect x="{x0+(x1-x0)*0.8:.0f}" y="{y0}" width="{(x1-x0)*0.2:.0f}" height="{y1-y0}" fill="#4A7BA6" opacity="0.08"/>
  <text class="b-val" x="{x1-6}" y="{y0+14}" text-anchor="end" fill="#4A7BA6">top 20% of baskets → 41.9% of units</text>
</svg>"""


def _hbars(items, scale_max, ref=None, unit="x") -> str:
    """items: list of (label, value, color). Horizontal bars in a 0..scale_max scale."""
    x0, w = 176, 380
    out, y = [], 20
    if ref is not None:
        rx = x0 + w * ref / scale_max
        out.append(f'<line x1="{rx:.1f}" y1="10" x2="{rx:.1f}" y2="{y+len(items)*30}" stroke="#C9D0D8" stroke-dasharray="3 3"/>')
        out.append(f'<text class="ax" x="{rx:.1f}" y="8" text-anchor="middle">{ref:g}{unit}</text>')
    for label, val, color in items:
        bw = w * min(val, scale_max) / scale_max
        out.append(f'<rect x="{x0}" y="{y}" width="{bw:.1f}" height="16" rx="3" fill="{color}"/>')
        out.append(f'<text class="b-lbl" x="{x0-8}" y="{y+12}" text-anchor="end">{esc(label)}</text>')
        out.append(f'<text class="b-val" x="{x0+bw+6:.1f}" y="{y+12}">{val:.2f}{unit}</text>')
        y += 30
    return "".join(out)


def svg_affinity(M: dict) -> str:
    groc = [(k.replace("→", " → "), v, ACCENT) for k, v in M["affinity"].items()]
    return f"""<svg viewBox="0 0 640 220" role="img" aria-label="Grocery affinity lifts, all well above 1.0">
  <text class="b-grp" x="0" y="12">Grocery affinity lift (× more likely than chance)</text>
  {_hbars(groc, 3.0, ref=1.0, unit='x')}
</svg>"""


def svg_drink_attach(M: dict) -> str:
    da = [(BANNER_NAME[b], M["drink_attach"][b], BANNER_COLOR[b]) for b in ("CFA", "BKG", "TBL")]
    return f"""<svg viewBox="0 0 640 170" role="img" aria-label="QSR drink attach rate by banner">
  <text class="b-grp" x="0" y="12">Drink attached to an entrée (share of entrée baskets)</text>
  {_hbars(da, 1.0, unit='')}
</svg>"""


def svg_timing(CD: dict) -> str:
    # DOW bars (Mon..Sun); dow map 0=Sun..6=Sat.
    order = [1, 2, 3, 4, 5, 6, 0]
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    dow = CD["dow"]
    vals = [dow.get(d, 0) for d in order]
    mx = max(vals)
    x0, y1, bw = 40, 120, 44
    bars = []
    for i, (nm, v) in enumerate(zip(names, vals)):
        h = 86 * v / mx
        x = x0 + i * bw
        we = nm in ("Sat", "Sun")
        bars.append(f'<rect x="{x}" y="{y1-h:.1f}" width="{bw-8}" height="{h:.1f}" rx="2" fill="{ACCENT if not we else ACCENT2}"/>')
        bars.append(f'<text class="ax" x="{x+(bw-8)/2:.0f}" y="{y1+14}" text-anchor="middle">{nm}</text>')

    # Three QSR hour strips.
    def strip(banner, yoff, hi_hours, note):
        hrs = CD["hourly"][banner]
        mxh = max(hrs) or 1
        sx0, sw = 360, 250
        cells = []
        for h in range(24):
            bh = 26 * hrs[h] / mxh
            x = sx0 + (sw / 24) * h
            col = BANNER_COLOR[banner] if h in hi_hours else "#D8E2EE"
            cells.append(f'<rect x="{x:.1f}" y="{yoff+30-bh:.1f}" width="{sw/24-1:.1f}" height="{bh:.1f}" fill="{col}"/>')
        return (f'<text class="b-val" x="{sx0}" y="{yoff-2}">{BANNER_NAME[banner]} — {note}</text>'
                + "".join(cells))

    return f"""<svg viewBox="0 0 640 300" role="img" aria-label="Grocery day-of-week rhythm plus quick-service hour-of-day patterns per banner">
  <text class="b-grp" x="0" y="12">Grocery visits by day of week</text>
  {''.join(bars)}
  {strip('BKG', 40, list(range(6,10)), 'breakfast daypart')}
  {strip('TBL', 118, [21,22,23,0,1,2], 'late-night tail')}
  {strip('CFA', 196, [], 'closed Sundays · midday peak')}
  <text class="ax" x="360" y="252">midnight</text><text class="ax" x="610" y="252" text-anchor="end">11pm</text>
</svg>"""


def svg_seasonality(CD: dict) -> str:
    wk = CD["weekly"]
    # Drop the partial boundary weeks (the window opens mid-week on Mar 1 and
    # closes mid-week on May 29) so the line shows the real spring drift, not a
    # calendar artifact at each end.
    counts = [n for _, n in wk][1:-1]
    dates = [str(d)[:10] for d, _ in wk][1:-1]
    x0, x1, y0, y1 = 56, 604, 34, 196
    mn, mx = min(counts), max(counts)
    def X(i): return x0 + (x1 - x0) * i / (len(counts) - 1)
    def Y(v): return y1 - (y1 - y0) * (v - mn) / (mx - mn)
    pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(counts))
    # Mark the week containing Easter (Apr 5) — a full week inside the window.
    def shade(target):
        for i, ds in enumerate(dates):
            if ds <= target and (i + 1 >= len(dates) or dates[i + 1] > target):
                return X(i)
        return None
    marks = []
    xx = shade("2026-04-05")
    if xx is not None:
        marks.append(f'<line x1="{xx:.1f}" y1="{y0}" x2="{xx:.1f}" y2="{y1}" stroke="#B4543B" stroke-width="1" stroke-dasharray="3 3"/>')
        marks.append(f'<text class="ax" x="{xx:.1f}" y="{y0-4}" text-anchor="middle" fill="#B4543B">Easter week</text>')
    return f"""<svg viewBox="0 0 640 240" role="img" aria-label="Weekly transactions across the window, drifting up through spring, with Easter week marked">
  <line x1="{x0}" y1="{y1}" x2="{x1}" y2="{y1}" stroke="#C9D0D8"/>
  {''.join(marks)}
  <polyline fill="none" stroke="{ACCENT}" stroke-width="2.5" points="{pts}"/>
  <text class="ax" x="{x0}" y="{y1+18}">early March</text>
  <text class="ax" x="{x1}" y="{y1+18}" text-anchor="end">late May</text>
</svg>"""


def svg_fresh_by_tier(M: dict) -> str:
    tiers = M["fresh_by_tier"]
    labels = ["Lower affluence", "Mid", "Higher affluence"]
    x0, y1, bw = 120, 180, 130
    mx = max(tiers) * 1.15
    bars = []
    for i, (lb, v) in enumerate(zip(labels, tiers)):
        h = 130 * v / mx
        x = x0 + i * bw
        bars.append(f'<rect x="{x}" y="{y1-h:.1f}" width="{bw-40}" height="{h:.1f}" rx="3" fill="{_lerp_hex(ACCENT3, ACCENT, i/2)}"/>')
        bars.append(f'<text class="b-val" x="{x+(bw-40)/2:.0f}" y="{y1-h-6:.1f}" text-anchor="middle">{v:.1f}%</text>')
        bars.append(f'<text class="ax" x="{x+(bw-40)/2:.0f}" y="{y1+16}" text-anchor="middle">{lb}</text>')
    return f"""<svg viewBox="0 0 560 210" role="img" aria-label="Fresh and premium share of the grocery basket rises with affluence">
  <text class="b-grp" x="0" y="14">Fresh &amp; premium share of grocery dollars</text>
  {''.join(bars)}
</svg>"""


# ----- geometry: neighborhood polygons ---------------------------------------

def _convex_hull(pts):
    pts = sorted(set(pts))
    if len(pts) <= 2:
        return pts
    def cross(o, a, b):
        return (a[0]-o[0])*(b[1]-o[1]) - (a[1]-o[1])*(b[0]-o[0])
    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def zone_polygon(store_coords: list, centroid: tuple, buffer=0.005) -> list:
    """Buffered convex hull around a zone's store coords (approximate). Zones with
    <3 stores fall back to a 12-gon circle around the metro.yaml centroid."""
    pts = [(round(lng, 6), round(lat, 6)) for lat, lng in store_coords]
    hull = _convex_hull(pts)
    if len(hull) < 3:
        clat, clng = centroid
        r = 0.012
        return [[round(clat + r * math.sin(2*math.pi*k/12), 6),
                 round(clng + r * math.cos(2*math.pi*k/12) / 0.82, 6)] for k in range(12)]
    cx = sum(p[0] for p in hull) / len(hull)
    cy = sum(p[1] for p in hull) / len(hull)
    out = []
    for x, y in hull:
        dx, dy = x - cx, y - cy
        d = math.hypot(dx, dy) or 1e-9
        out.append([round(y + buffer * dy / d, 6), round(x + buffer * dx / d, 6)])  # [lat,lng]
    return out


# ----- map builder (traced Charlotte planning-district metro) -----------------
#
# Real district shapes traced from the reference planning-district map. The
# geometry is generated once (offline tracer) into scripts/metro_geometry.py —
# watertight (each shared border is traced a single time) and fully static, so
# this build needs no image/PIL. We import the silhouette outline, the per-zone
# path, and the label/marker anchors from that module.
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from metro_geometry import VIEWBOX, OUTLINE_D, ZONE_D, LABEL_XY, MARKER_XY


# --- store markers: merchant logos scattered inside each region as map-pins ---
#
# Each store renders as a teardrop pin whose round head carries the merchant
# logo and whose tip hangs below — placed at plausible, spread-out interior
# points so the map reads like real store locations. All geometry is derived
# from the static ZONE_D polylines (M/L/Z only), so the build stays image-free
# and deterministic (positions come from a per-zone seeded RNG).

def _zone_polygon(zid: str) -> list:
    """Parse ZONE_D[zid] (M/L/Z polyline) into a [(x, y), ...] ring."""
    nums = re.findall(r"-?\d+\.?\d*", ZONE_D[zid])
    pts = [(float(nums[i]), float(nums[i + 1])) for i in range(0, len(nums) - 1, 2)]
    return pts


def _point_in_poly(x: float, y: float, ring: list) -> bool:
    """Ray-cast point-in-polygon test."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _min_edge_dist(x: float, y: float, ring: list) -> float:
    """Shortest distance from (x, y) to the polygon boundary."""
    best = float("inf")
    n = len(ring)
    for i in range(n):
        ax, ay = ring[i]
        bx, by = ring[(i + 1) % n]
        dx, dy = bx - ax, by - ay
        seg = dx * dx + dy * dy
        t = 0.0 if seg == 0 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / seg))
        px, py = ax + t * dx, ay + t * dy
        d = math.hypot(x - px, y - py)
        if d < best:
            best = d
    return best


def _scatter_slots(ring: list, n: int, rng: random.Random,
                   label_xy: tuple, head_r: float) -> list:
    """Return n well-separated interior points via best-candidate sampling.

    Each head-center is kept ``head_r`` clear of the region edge so the logo
    sits fully inside, and pushed away from already-placed pins and the zone
    label. Margins relax if a tight region can't satisfy them."""
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    lx, ly = label_xy
    slots: list = []
    # (edge margin, label keep-out) tiers — relax together if a tight region
    # can't satisfy the first, so pins clear the zone name when there's room.
    tiers = ((head_r + 3, head_r + 34), (head_r + 1, head_r + 20),
             (head_r * 0.6, head_r + 6), (2.0, 0.0))
    for _ in range(n):
        for margin, lclear in tiers:
            best, best_score = None, -1.0
            for _c in range(48):
                cx = rng.uniform(x0, x1)
                cy = rng.uniform(y0, y1)
                if not _point_in_poly(cx, cy, ring):
                    continue
                if _min_edge_dist(cx, cy, ring) < margin:
                    continue
                # label sits at (lx, ly) with a tier line ~18 below it
                if math.hypot(cx - lx, cy - (ly + 9)) < lclear:
                    continue
                dmin = min((math.hypot(cx - sx, cy - sy) for sx, sy in slots),
                           default=1e4)
                if dmin > best_score:
                    best, best_score = (cx, cy), dmin
            if best is not None:
                slots.append(best)
                break
    return slots


def _pin_path(head_r: float, tip: float) -> str:
    """Teardrop outline: head circle at (0,0) r=head_r, tip point at (0,tip)."""
    cos_a = head_r / tip
    sin_a = math.sqrt(max(0.0, 1.0 - cos_a * cos_a))
    tx, ty = head_r * sin_a, head_r * cos_a          # right tangent point
    # Outline: tip -> right tangent -> major arc over the top -> left tangent ->
    # back to tip. sweep-flag 0 = counterclockwise on screen (arc bulges up).
    return (f"M 0 {tip:.1f} L {tx:.1f} {ty:.1f} "
            f"A {head_r:.1f} {head_r:.1f} 0 1 0 {-tx:.1f} {ty:.1f} Z")


def _zone_markers(zid: str, banners: list, slots: list,
                  head_r: float, clip_id: str) -> str:
    """Emit one logo map-pin per store at the given scattered slots."""
    tip = head_r * 1.95
    pin_d = _pin_path(head_r, tip)
    lg = head_r * 1.5                                  # logo box side
    out = []
    for (b, _seg), (x, y) in zip(banners, slots):
        out.append(
            f'<g class="smk" data-banner="{b}" transform="translate({x:.1f},{y:.1f})">'
            f'<path class="pin" d="{pin_d}" fill="#ffffff" stroke="#5B6472" '
            f'stroke-width="1.3" stroke-linejoin="round"/>'
            f'<image href="assets/logos/{b}.png" x="{-lg/2:.1f}" y="{-lg/2:.1f}" '
            f'width="{lg:.1f}" height="{lg:.1f}" preserveAspectRatio="xMidYMid meet" '
            f'clip-path="url(#{clip_id})"/></g>')
    return "".join(out)


def build_map(con, zones, rows, M) -> str:
    coords = con.execute(
        "SELECT banner_code, zone_id FROM stores ORDER BY zone_id, banner_code"
    ).fetchall()
    affl_lo = min(z["affluence"] for z in zones)
    affl_hi = max(z["affluence"] for z in zones)

    by_zone: dict[str, list] = {}
    seg_order = {b: (0 if SEGMENT_LABEL[b] == "Grocery" else 1) for b in BANNER_NAME}
    for b, z in coords:
        by_zone.setdefault(z, []).append(b)
    for z in by_zone:
        by_zone[z].sort(key=lambda b: (seg_order[b], b))

    # Build the SVG server-side from the traced district geometry. Order:
    # defs (pin logo clips) -> district fills (clickable) -> silhouette outline
    # -> compass -> labels -> store map-pins on top.
    districts, labels, markers, defs = [], [], [], []
    for zi, z in enumerate(zones):
        zid = z["id"]
        lx, ly = LABEL_XY[zid]
        fill = affluence_color(z["affluence"], affl_lo, affl_hi)
        # Flip label text to light on the darker (higher-affluence) regions.
        t = (z["affluence"] - affl_lo) / (affl_hi - affl_lo) if affl_hi > affl_lo else 0.0
        lab_c, tier_c = ("#ffffff", "#e2ecf6") if t > 0.52 else ("#31404f", "#6b7787")
        halo = "#10233b" if t > 0.52 else "#ffffff"
        banners = [(b, SEGMENT_LABEL[b]) for b in by_zone.get(zid, [])]
        districts.append(
            f'<g class="ztile" id="zt_{zid}">'
            f'<path class="bg" data-zone="{zid}" d="{ZONE_D[zid]}" '
            f'fill="{fill}" stroke="#8aa0b8" stroke-width="1.1" stroke-linejoin="round"/></g>')
        # fill via inline style so it beats the .zlab/.ztier CSS fill; halo via
        # paint-order stroke so labels read on both pale and deep fills.
        labels.append(
            f'<text class="zlab" x="{lx:.0f}" y="{ly:.0f}" text-anchor="middle" '
            f'paint-order="stroke" stroke="{halo}" stroke-width="3" stroke-linejoin="round" '
            f'style="fill:{lab_c}">{esc(z["name"])}</text>'
            f'<text class="ztier" x="{lx:.0f}" y="{ly+18:.0f}" text-anchor="middle" '
            f'paint-order="stroke" stroke="{halo}" stroke-width="2.6" stroke-linejoin="round" '
            f'style="fill:{tier_c}">{esc(ZONE_TIER[zid])} · {len(banners)} stores</text>')
        # Scatter this zone's store pins across its interior. Head size scales
        # with the room available per store (clamped); positions are seeded per
        # zone so the build stays deterministic.
        ring = _zone_polygon(zid)
        area = abs(sum(ring[i][0] * ring[(i + 1) % len(ring)][1]
                       - ring[(i + 1) % len(ring)][0] * ring[i][1]
                       for i in range(len(ring)))) / 2.0
        n = len(banners)
        spacing = math.sqrt(area / n) if n else 0.0
        head_r = max(11.0, min(17.5, 0.28 * spacing))
        rng = random.Random(4242 + zi)
        slots = _scatter_slots(ring, n, rng, (lx, ly), head_r)
        clip_id = f"pinclip_{zid}"
        defs.append(f'<clipPath id="{clip_id}"><circle cx="0" cy="0" '
                    f'r="{head_r * 0.82:.1f}"/></clipPath>')
        markers.append(_zone_markers(zid, banners, slots, head_r, clip_id))

    vb = VIEWBOX
    outline = (f'<path fill="none" stroke="#22303F" stroke-width="2.4" '
               f'stroke-linejoin="round" d="{OUTLINE_D}"/>')
    compass = (f'<g transform="translate({vb[2]-70},72)">'
               '<circle r="24" fill="#ffffff" stroke="#22303F" stroke-width="1.3"/>'
               '<path d="M 0 -17 L 6 6 L 0 1 L -6 6 Z" fill="#22303F"/>'
               '<text x="0" y="-28" text-anchor="middle" font-size="12" '
               'font-weight="700" fill="#22303F">N</text></g>')

    svg = (f'<svg id="s1-map-svg" viewBox="{vb[0]} {vb[1]} {vb[2]} {vb[3]}" role="img" '
           f'aria-label="Map of the fictional metro modeled on Charlotte planning '
           f'districts: eight neighborhoods shaded by affluence, with each merchant\'s stores">'
           f'<defs>{"".join(defs)}</defs>'
           f'<g id="districts">{"".join(districts)}</g>'
           f'{outline}{compass}'
           f'<g id="labels">{"".join(labels)}</g>'
           f'<g id="markers">{"".join(markers)}</g></svg>')

    zones_js = []
    for z in zones:
        r = next(x for x in rows if x["id"] == z["id"])
        zones_js.append({
            "id": z["id"], "name": z["name"], "affluence": z["affluence"],
            "tier": ZONE_TIER[z["id"]], "customers": r["customers"],
            "stores": r["store_count"], "desc": ZONE_DESC[z["id"]],
        })

    merch_js = {}
    for b in BANNER_NAME:
        meta = MERCHANT_META[b]
        merch_js[b] = {
            "name": BANNER_NAME[b], "seg": SEGMENT_LABEL[b], "tier": meta["tier"],
            "logo": f"assets/logos/{b}.png",
            "stores": M["n_stores"][b], "auv": round(M["auv"][b] / 1e6, 4),
            "basket": round(M["banner_basket"][b], 4),
            "pl": round(M["pl_unit_share"][b], 4) if b in M["pl_unit_share"] else None,
            "bullets": meta["bullets"], "distinct": meta["distinct"],
        }

    data = "const ZONES=%s;\nconst MERCH=%s;\nconst AFFL=[%s,%s];" % (
        json.dumps(zones_js), json.dumps(merch_js), affl_lo, affl_hi)

    chips = ['<button class="chip chip-all active" data-all="1">All</button>',
             '<span class="chip-group">Grocery</span>']
    for b in GROCERY:
        chips.append(f'<button class="chip" data-banner="{b}" style="--bc:{BANNER_COLOR[b]}">{BANNER_NAME[b]}</button>')
    chips.append('<span class="chip-group">QSR</span>')
    for b in QSR:
        chips.append(f'<button class="chip" data-banner="{b}" style="--bc:{BANNER_COLOR[b]}">{BANNER_NAME[b]}</button>')

    return (MAP_TEMPLATE.replace("__CHIPS__", "\n    ".join(chips))
            .replace("__SVG__", svg).replace("__DATA__", data))


MAP_TEMPLATE = """
<div class="s1-wide">
  <div class="chipbar">
    __CHIPS__
  </div>
  <div class="mapgrid">
    <div class="mapcol">
      <div id="s1-map">__SVG__</div>
      <div class="maplegend"><span>Neighborhood affluence</span>
        <small>lower</small><i class="ramp"></i><small>higher</small>
      </div>
    </div>
    <div id="s1-detail" class="detail intro">
      <b>The metro at a glance.</b> A schematic of the fictional metro — the layout is
      illustrative, not real streets. Click a merchant chip to see where that chain sits, or a
      neighborhood to see who shops there. Each pin marks a store; its logo shows the banner.
    </div>
  </div>
</div>
<script>
__DATA__
(function(){
  var svg=document.getElementById('s1-map-svg');
  var detail=document.getElementById('s1-detail');
  function shade(a){var t=(a-AFFL[0])/(AFFL[1]-AFFL[0]);
    var lo=[238,243,249], hi=[15,76,129];
    var c=lo.map(function(v,i){return Math.round(v+(hi[i]-v)*t);});
    return 'rgb('+c[0]+','+c[1]+','+c[2]+')';}
  function marks(){return svg.querySelectorAll('[data-banner]');}
  function tiles(){return svg.querySelectorAll('[data-zone]');}
  function card(html){detail.className='detail'; detail.innerHTML=html;
    var img=detail.querySelector('.dlogo');
    if(img){img.onerror=function(){img.style.display='none';};}}
  function money(x){return '$'+(x>=1?x.toFixed(1):x.toFixed(2));}

  function selectBanner(b){
    setChips(b);
    marks().forEach(function(m){var on=m.getAttribute('data-banner')===b;
      m.classList.toggle('on',on); m.classList.toggle('dim',!on);});
    tiles().forEach(function(t){t.parentNode.classList.remove('sel');});
    var d=MERCH[b];
    var pl = d.pl!=null ? '<div class="dk"><span>Store-brand share</span><b>'+d.pl.toFixed(1)+'% (unit)</b></div>' : '';
    var bl = d.bullets.map(function(x){return '<li>'+x+'</li>';}).join('');
    card('<div class="dhead"><img class="dlogo" src="'+d.logo+'" alt="'+d.name+'">'+
      '<div><b>'+d.name+'</b><span class="dsub">'+d.seg+' · '+d.tier+'</span></div></div>'+
      '<div class="dgrid">'+
        '<div class="dk"><span>Stores</span><b>'+d.stores+'</b></div>'+
        '<div class="dk"><span>Sales / store</span><b>'+money(d.auv)+'M/yr</b></div>'+
        '<div class="dk"><span>Avg '+(d.seg==='Grocery'?'basket':'check')+'</span><b>'+money(d.basket)+'</b></div>'+
        pl+'</div>'+
      '<p class="dp"><b>Specializes in:</b></p><ul class="dbullets">'+bl+'</ul>'+
      '<p class="dp dq">'+d.distinct+'</p>');
  }
  function selectZone(id){
    setChips(null);
    var z=ZONES.filter(function(x){return x.id===id;})[0];
    marks().forEach(function(m){m.classList.remove('on','dim');});
    tiles().forEach(function(t){t.parentNode.classList.toggle('sel', t.getAttribute('data-zone')===id);});
    card('<div class="dhead"><span class="dot" style="background:'+shade(z.affluence)+';border:1px solid #8aa0b8"></span>'+
      '<div><b>'+z.name+'</b><span class="dsub">'+z.tier+' affluence</span></div></div>'+
      '<div class="dgrid">'+
        '<div class="dk"><span>Customers</span><b>'+z.customers.toLocaleString()+'</b></div>'+
        '<div class="dk"><span>Stores</span><b>'+z.stores+'</b></div>'+
        '<div class="dk"><span>Affluence index</span><b>'+z.affluence.toFixed(2)+'</b></div>'+
      '</div><p class="dp">'+z.desc+'</p>');
  }
  function reset(){
    setChips('all');
    marks().forEach(function(m){m.classList.remove('on','dim');});
    tiles().forEach(function(t){t.parentNode.classList.remove('sel');});
    detail.className='detail intro';
    detail.innerHTML='<b>The metro at a glance.</b> A schematic of the fictional metro — the layout is illustrative, not real streets. Click a merchant chip to see where that chain sits, or a neighborhood to see who shops there. Each pin marks a store; its logo shows the banner.';
  }
  function setChips(active){
    document.querySelectorAll('.chip').forEach(function(c){
      var on = (active==='all' && c.dataset.all) || (c.dataset.banner===active);
      c.classList.toggle('active', !!on);});
  }
  svg.addEventListener('click',function(e){
    var mk=e.target.closest('[data-banner]'); if(mk){selectBanner(mk.getAttribute('data-banner')); return;}
    var tl=e.target.closest('[data-zone]'); if(tl){selectZone(tl.getAttribute('data-zone'));}});
  document.querySelectorAll('.chip[data-banner]').forEach(function(c){
    c.addEventListener('click',function(){selectBanner(c.dataset.banner);});});
  document.querySelector('.chip-all').addEventListener('click',reset);
})();
</script>
"""


# ----- section fragment -------------------------------------------------------

def details(summary_label, inner) -> str:
    return (f'<details class="disc"><summary>{esc(summary_label)}</summary>'
            f'<div class="disc-body">{inner}</div></details>')


def term(word, gloss) -> str:
    return f'<span class="term" title="{esc(gloss)}">{esc(word)}</span>'


def mechanism(summary_html, chart_svg=None, anchor_html="") -> str:
    body = ""
    if chart_svg:
        body = details("How this works", f'<figure>{chart_svg}</figure>{anchor_html}')
    return f'<div class="mech"><div class="mech-sum">{summary_html}</div>{body}</div>'


def tag(kind) -> str:
    cls = "tag-sourced" if kind.startswith("SOURCED") else "tag-assumption"
    return f'<span class="{cls}">{esc(kind)}</span>'


def anatomy_card(txn: dict, title: str, max_lines: int = 6) -> str:
    # Left panel: the payment/device metadata the terminal captures (real
    # columns + the synthesized terminal_id + the basket total).
    left = [
        ("card token", txn["customer_token"][:16] + "…"),
        ("store", f'{txn["store_id"]} · {txn["neighborhood"]}'),
        ("terminal", txn["terminal_id"]),
        ("time", str(txn["txn_ts"])),
        ("tender", f'{txn["tender"]} · {txn["entry_mode"]}'),
        ("network / wallet", f'{txn["network"]}' + (f' · {txn["wallet_provider"]}' if txn["wallet_at_tap"] else '')),
        ("connectivity", txn["connectivity_type"]),
        ("basket total", f'${txn["subtotal"]:.2f}'),
    ]
    left_html = "".join(f'<div class="kv"><span>{esc(k)}</span><b>{esc(v)}</b></div>' for k, v in left)
    # Right panel: the basket line items, trimmed for readability.
    lines = txn["lines"]
    shown = lines[:max_lines]
    rows = []
    for ln in shown:
        rows.append(f'<tr><td class="mono">{esc(ln["sku"])}</td><td>{esc(ln["name"])}</td>'
                    f'<td>{ln["qty"]}</td><td>${ln["unit_price"]:.2f}</td><td>${ln["line_total"]:.2f}</td></tr>')
    extra = len(lines) - len(shown)
    if extra > 0:
        rows.append(f'<tr class="ana-more"><td colspan="5">…and {extra} more '
                    f'item{"s" if extra != 1 else ""}</td></tr>')
    return f"""<div class="anatomy">
  <div class="ana-col">
    <div class="ana-h">What the terminal captures</div>
    {left_html}
  </div>
  <div class="ana-col ana-r">
    <div class="ana-h">The basket</div>
    <table class="ana-t"><thead><tr><th>sku</th><th>product</th><th>qty</th><th>price</th><th>total</th></tr></thead>
      <tbody>{''.join(rows)}</tbody></table>
  </div>
</div>"""


def catalog_table(rows) -> str:
    trs = []
    for r in rows:
        pl = '<span class="plpill">store brand</span>' if r["pl"] else 'national'
        trs.append(f'<tr><td>{esc(r["name"])}</td><td>{pl}</td><td>${r["price"]:.2f}</td>'
                   f'<td>{esc(r["mdept"])} › {esc(r["mcat"])}</td>'
                   f'<td>{esc(r["fdept"])} › {esc(r["fcat"])}</td></tr>')
    return (f'<table class="cat-t"><thead><tr><th>product</th><th>type</th><th>shelf price</th>'
            f'<th>merchant taxonomy</th><th>functional taxonomy</th></tr></thead>'
            f'<tbody>{"".join(trs)}</tbody></table>')


def scorecard_table(M: dict) -> str:
    def r(claim, anchor, target, measured, kind):
        # `kind` (the old Basis tag) is retained in the signature but no longer
        # rendered — the Basis column was dropped.
        return (f'<tr><td>{claim}</td><td>{anchor}</td><td>{target}</td>'
                f'<td class="mono">{measured}</td></tr>')
    a = M["affinity"]
    rows = [
        r("Kroger per-store sales", "FMI Food Industry Facts; Kroger 10-K", "~$45M/yr",
          f"${M['auv']['KRG']/1e6:.1f}M", "SOURCED"),
        r("Grocery dollars split KRG/ACM/WDX", "banner positioning", "52/31/17",
          "/".join(f"{M['grocery_split'][b]:.0f}" for b in GROCERY), "ASSUMPTION"),
        r("Chick-fil-A out-earns Taco Bell + Burger King", "QSR 50 per-unit AUV", ">1x",
          f"{M['cfa_ratio']:.2f}x (6 vs 17 units)", "SOURCED"),
        r("Contactless share of payments", "Fed Diary 2025", "45–60%",
          f"{M['contactless']:.1f}%", "SOURCED (direction)"),
        r("Store-brand unit share, Kroger", "Numerator (unit)", "~27%",
          f"{M['pl_unit_share']['KRG']:.1f}%", "SOURCED"),
        r("Store-brand unit share, Acme", "Numerator (unit, Albertsons)", "~19%",
          f"{M['pl_unit_share']['ACM']:.1f}%", "SOURCED"),
        r("Store-brand unit share, Winn-Dixie", "value-banner assumption", "~25%",
          f"{M['pl_unit_share']['WDX']:.1f}%", "ASSUMPTION"),
        r("Late-night skew, Taco Bell", "industry ~4%, raised to be detectable", "15–23%",
          f"{M['tbl_latenight']:.1f}% after 9pm", "ASSUMPTION"),
        r("Breakfast skew, Burger King", "QSR breakfast daypart", "12–28%",
          f"{M['bkg_breakfast']:.1f}%", "ASSUMPTION"),
        r("Pasta → sauce bought together", "market-basket norm", "≥1.8x",
          f"{a['Pasta→Pasta Sauce']:.2f}x", "SOURCED (pattern)"),
        r("No banner cheapest on everything", "price arbitrage", "none >70%",
          f"WDX {M['cheapest_share'].get('WDX',0):.0f}%", "SOURCED (pattern)"),
        r("Fresh share rises with affluence", "affluent buy more fresh", "monotonic",
          "→".join(f"{v:.1f}" for v in M["fresh_by_tier"]), "ASSUMPTION (direction sourced)"),
        r("Both-segment shoppers", "shared-wallet overlap", "~46%",
          f"{M['both_active']:.1f}%", "ASSUMPTION"),
        r("Loyalty concentration", "primary-banner habit", "68–82%",
          f"{M['loyalty_conc']:.1f}%", "ASSUMPTION"),
        r("Heavy-tail baskets", "stock-up behavior", "40–60%",
          f"{M['heavy_tail']:.1f}% of units", "SOURCED (pattern)"),
    ]
    return (f'<table class="score-t"><thead><tr><th>Claim</th><th>Anchor (source)</th>'
            f'<th>Target</th><th>Measured (155k)</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>')


def sources_block() -> str:
    lis = []
    for what, src, url in SOURCES:
        link = f'<a href="{url}" target="_blank" rel="noopener">{esc(src)}</a>' if url else esc(src) + " <em>(URL to be confirmed)</em>"
        lis.append(f'<li><strong>{esc(what)}</strong> — {link}</li>')
    return f'<details class="disc"><summary>Sources</summary><div class="disc-body"><ul class="src">{"".join(lis)}</ul></div></details>'


def stats_band(M) -> str:
    cells = [
        ("6 merchants", f"2 segments · {M['stores']} stores · one metro"),
        (f"{M['cards']:,}", "shared customer cards"),
        (f"{M['txns']/1e6:.1f}M", f"transactions · {M['lines']/1e6:.0f}M line items"),
        ("90 days", "Mar 1 – May 29, 2026"),
        ("5 AI agents", "4 specialists + an advisor"),
        ("k = 50", "peer-privacy suppression floor"),
    ]
    return '<div class="stats">' + "".join(
        f'<div class="stat"><div class="n">{n}</div><div class="l">{l}</div></div>'
        for n, l in cells) + '</div>'


def render_section(con, zones, rows, M, CD) -> str:
    ana = anatomy(con)

    # Six mechanisms as compact cards; the two figures move into one shared
    # "How this works" disclosure below the grid. Missions renders last.
    m_gravity = f'<b>{term("Gravity","bigger and nearer stores pull harder; the pull falls off with distance")}</b> — store choice follows a gravity rule: bigger, nearer stores pull harder, and the pull drops off with distance.'
    m_missions = f'<b>Missions</b> — every trip has a reason, and the reason sets the basket. The top 20% of grocery baskets carry <b>{M["heavy_tail"]:.1f}%</b> of all units.'
    m_affinity = f'<b>{term("Affinity","products bought together — a market-basket pattern")}</b> — some things get bought together: pasta → sauce runs <b>{M["affinity"]["Pasta→Pasta Sauce"]:.2f}×</b> more likely than chance.'
    m_timing = f'<b>{term("Daypart","the time-of-day rhythm of visits")} &amp; timing</b> — grocery skews to weekends (<b>{M["weekend_weekday"]:.2f}×</b> a weekday); Taco Bell runs late, Burger King leans breakfast, Chick-fil-A is closed Sundays.'
    m_season = '<b>Seasonality</b> — across the window, spending drifts up as spring warms and Easter week lifts baking, ham, and candy — the spikes land in the right categories, not across the board.'
    m_brand = f'<b>{term("Private label","a store\'s own brand — measured here as a share of unit sales")}</b> — where a shelf offers both, value shoppers lean store-brand and affluent shoppers lean name-brand. Store-brand unit share: Kroger <b>{M["pl_unit_share"]["KRG"]:.1f}%</b>, Winn-Dixie <b>{M["pl_unit_share"]["WDX"]:.1f}%</b>, Acme <b>{M["pl_unit_share"]["ACM"]:.1f}%</b>.'
    mechgrid = "".join(f'<div class="mcard">{m}</div>' for m in (
        m_gravity, m_affinity, m_timing, m_season, m_brand, m_missions))
    mech_charts = details(
        "How this works",
        f'<figure>{svg_basket_tail(CD["basket_hist"])}</figure>'
        '<p class="anchor">A weekly stock-up fills a big basket; a quick trip grabs three things. The long right tail is the stock-up behavior every grocer sees.</p>'
        f'<figure>{svg_seasonality(CD)}</figure>'
        '<p class="anchor">The window runs early March to late May. The lift concentrates in Easter categories rather than spreading evenly across the basket.</p>')

    return f"""<!-- ==== SECTION 1 (generated) START ==== -->
<section id="s1">
  <span class="secnum">SECTION 1</span>
  <h2>The world behind the data</h2>
  {stats_band(M)}
  <p class="disclaimer"><b>All data is synthetic.</b> The six merchants are plausible stand-ins generated from scratch — no real customers, cards, or companies are involved.</p>

  <h3>The metro</h3>
  {build_map(con, zones, rows, M)}

  <h3>How customers shop</h3>
  <p>Six mechanisms turn the shoppers' hidden traits into transactions.</p>
  <div class="mechgrid">{mechgrid}</div>
  {mech_charts}

  <h3>Anatomy of a transaction</h3>
  <p>One real Kroger stock-up — what a payments company observes at the register.</p>
  {anatomy_card(ana["stockup"], "Kroger stock-up")}
  {details("More examples", f'<h4>Grocery quick trip</h4>{anatomy_card(ana["quick"],"quick")}<h4>Quick-service combo</h4>{anatomy_card(ana["combo"],"combo")}')}

  <h3>How the data checks out</h3>
  {details("See all checks", scorecard_table(M) + sources_block())}
</section>
<!-- ==== SECTION 1 (generated) END ==== -->"""


# ----- CSS + head assets ------------------------------------------------------

LEAFLET_HEAD = (
    '<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>\n'
    '<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>'
)

CSS_ADD = """
  /* ---- Section 1 (generated) additions ---- */
  /* Drop the header's bottom rule so only the section's top rule divides the
     header from Section 1 (was a doubled divider line). */
  header.top{border-bottom:none}
  .thesis{font-size:15.5px; color:var(--text); border-left:3px solid var(--accent);
    padding-left:14px; margin:16px 0 0; max-width:70ch; line-height:1.5}
  .s1-wide{margin:26px calc(50% - 50vw); padding:0 max(28px, calc(50vw - 540px))}
  .chipbar{display:flex; flex-wrap:wrap; gap:7px; align-items:center; margin:0 0 12px}
  .chip{font-size:13px; font-weight:600; color:var(--muted); background:#fff; border:1px solid var(--line);
    border-radius:16px; padding:6px 13px; cursor:pointer; transition:all .15s}
  .chip[data-banner]{border-left:3px solid var(--bc, var(--line))}
  .chip:hover{color:var(--text); border-color:var(--accent3)}
  .chip.active{background:var(--accent); color:#fff; border-color:var(--accent)}
  .chip[data-banner].active{background:var(--bc); border-color:var(--bc)}
  .chip-group{font-size:11px; letter-spacing:.1em; text-transform:uppercase; color:var(--muted); margin-left:8px}
  .mapgrid{display:grid; grid-template-columns:1.12fr 1fr; gap:16px; align-items:stretch}
  #s1-map{border:1px solid var(--line); border-radius:12px; background:#fbfcfd; padding:10px}
  #s1-map svg{display:block; width:100%; height:auto}
  .maplegend{display:flex; align-items:center; gap:8px; margin:8px 2px 0; font-size:12px; color:var(--muted); flex-wrap:wrap}
  .maplegend .ramp{height:10px; width:120px; border-radius:5px; background:linear-gradient(90deg,#eef3f9,#0F4C81)}
  .dlogo{height:30px; max-width:120px; width:auto; object-fit:contain; flex:none}
  @media(max-width:820px){.mapgrid{grid-template-columns:1fr}}
  /* schematic map */
  .ztile .bg{cursor:pointer; transition:stroke .12s, filter .12s}
  .ztile:hover .bg{filter:brightness(0.97)}
  .ztile.sel .bg{stroke:var(--accent); stroke-width:2.5}
  .zlab{font:700 16px -apple-system,Segoe UI,sans-serif; fill:#31404f}
  .ztier{font:500 12.5px -apple-system,Segoe UI,sans-serif; fill:#6b7787}
  .smk{cursor:pointer; transition:opacity .12s}
  .smk .pin{transition:stroke .12s, stroke-width .12s}
  .smk.on{filter:drop-shadow(0 1px 2px rgba(15,76,129,.45))}
  .smk.on .pin{stroke:var(--accent); stroke-width:2.4}
  .smk.dim{opacity:.16}
  .detail{border:1px solid var(--line); border-radius:12px; padding:16px 18px; background:#fff; height:100%; overflow:auto}
  @media(max-width:820px){.detail{height:auto}}
  .detail.intro{background:var(--surface); color:var(--muted)}
  .dhead{display:flex; gap:12px; align-items:center; margin-bottom:12px}
  .dhead .dot{width:16px; height:16px; border-radius:50%; flex:none}
  .dhead b{font-size:17px} .dsub{display:block; font-size:13px; color:var(--muted)}
  .dgrid{display:flex; flex-wrap:wrap; gap:0; border:1px solid var(--line); border-radius:8px; overflow:hidden; margin-bottom:12px}
  .dk{flex:1 1 25%; min-width:120px; padding:10px 12px; border-right:1px solid var(--line)}
  .dk span{display:block; font-size:11.5px; color:var(--muted)} .dk b{font-size:16px; color:var(--accent)}
  .dp{margin:6px 0} .dp.dq{color:var(--muted); font-size:15px; margin-top:12px}
  .dbullets{margin:6px 0 0; padding-left:20px}
  .dbullets li{margin:5px 0; font-size:14.5px; line-height:1.45}
  /* progressive disclosure */
  details.disc{border:1px solid var(--line); border-radius:10px; margin:14px 0; background:#fff; overflow:hidden}
  details.disc>summary{cursor:pointer; list-style:none; padding:12px 16px; font-size:14px; font-weight:600;
    color:var(--accent); display:flex; align-items:center; gap:8px}
  details.disc>summary::-webkit-details-marker{display:none}
  details.disc>summary::before{content:"▸"; font-size:12px; transition:transform .15s}
  details.disc[open]>summary::before{transform:rotate(90deg)}
  .disc-body{padding:2px 16px 16px}
  .mechgrid{display:grid; grid-template-columns:repeat(3,1fr); gap:12px; margin:14px 0}
  @media(max-width:820px){.mechgrid{grid-template-columns:1fr}}
  .mcard{border:1px solid var(--line); border-radius:10px; padding:12px 14px; background:#fff;
    font-size:13.5px; line-height:1.45; color:var(--text)}
  .mcard b{font-size:14.5px}
  .anchor{color:#2c3344; font-size:15px; margin:6px 0 4px}
  .term{border-bottom:1px dotted var(--accent2); cursor:help}
  .tag-sourced,.tag-assumption{display:inline-block; font-size:10.5px; font-weight:700; letter-spacing:.03em;
    border-radius:5px; padding:1px 7px; vertical-align:middle; margin-left:4px}
  .tag-sourced{background:#e5f0ea; color:#1F7A4D}
  .tag-assumption{background:#f0ecdd; color:#8a6d1a}
  .anatomy{display:grid; grid-template-columns:1fr 1fr; gap:14px; margin:18px 0; align-items:start}
  @media(max-width:820px){.anatomy{grid-template-columns:1fr}}
  .ana-col{border:1px solid var(--line); border-radius:10px; padding:14px}
  .ana-r{background:var(--surface)}
  .ana-more td{color:var(--muted); font-style:italic}
  .ana-h{font-size:12px; letter-spacing:.05em; text-transform:uppercase; color:var(--muted); margin-bottom:10px; font-weight:700}
  .ana-h .via{text-transform:none; letter-spacing:0; color:var(--accent2); font-weight:600}
  .kv{display:flex; justify-content:space-between; gap:12px; font-size:13px; padding:3px 0; border-bottom:1px dotted var(--line)}
  .kv span{color:var(--muted)} .kv b{font-weight:600; text-align:right}
  .ana-t,.cat-t,.score-t{width:100%; border-collapse:collapse; font-size:12.5px; margin-top:10px}
  .ana-t th,.cat-t th,.score-t th{text-align:left; font-size:11px; color:var(--muted); font-weight:600; padding:5px 6px; border-bottom:1px solid var(--line)}
  .ana-t td,.cat-t td,.score-t td{padding:5px 6px; border-bottom:1px solid var(--line)}
  .mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:11.5px}
  .plpill{display:inline-block; font-size:10px; font-weight:700; color:#8a6d1a; background:#f0ecdd; border-radius:4px; padding:0 5px}
  .cat-t td:nth-child(4),.cat-t td:nth-child(5){color:var(--muted); font-size:11.5px}
  .hstrip{display:flex; flex-wrap:wrap; border:1px solid var(--line); border-radius:10px; overflow:hidden; margin:16px 0}
  .hs{flex:1 1 25%; min-width:130px; padding:14px 16px; border-right:1px solid var(--line); border-bottom:1px solid var(--line)}
  .hs .n{font-size:19px; font-weight:700; color:var(--accent)} .hs .l{font-size:12px; color:var(--muted); margin-top:2px}
  .score-t td:nth-child(4){color:var(--accent); font-weight:600}
  ul.src li{font-size:14px}
  h4{font-size:14px; margin:16px 0 4px; color:var(--muted)}
"""


# ----- splice into docs/index.html -------------------------------------------

def splice(section_html: str, M: dict) -> None:
    doc = INDEX_HTML.read_text()

    # 1. Section body — between generated markers if present, else between the
    #    hand-authored SECTION 1 / SECTION 2 banners.
    start = "<!-- ==== SECTION 1 (generated) START ==== -->"
    end = "<!-- ==== SECTION 1 (generated) END ==== -->"
    if start in doc and end in doc:
        doc = re.sub(re.escape(start) + r".*?" + re.escape(end),
                     lambda _: section_html, doc, flags=re.S)
    else:
        doc = re.sub(
            r"<!-- =+ SECTION 1 =+ -->.*?(?=<!-- =+ SECTION 2 =+ -->)",
            lambda _: section_html + "\n\n", doc, flags=re.S)

    # 2. Header lede (+ thesis) → the new statement. Also consume any thesis
    #    paragraph a previous run injected, so re-runs stay idempotent.
    doc = re.sub(r'<p class="lede">.*?</p>(\s*<p class="thesis">.*?</p>)*',
                 lambda _: LEDE, doc, count=1, flags=re.S)

    # 3. The stats band + "all data is synthetic" disclaimer now live INSIDE
    #    Section 1 (rendered above), so strip the standalone header copies.
    #    Both are no-ops once already removed (idempotent). The standalone stats
    #    band is the one followed by the section comment; the header disclaimer
    #    is the FIRST disclaimer in the doc (above the section's own copy).
    doc = re.sub(r'\s*<div class="stats">.*?</div>\s*</div>(?=\s*<!--)', "", doc, count=1, flags=re.S)
    doc = re.sub(r'\s*<p class="disclaimer">.*?</p>(?=\s*</header>)', "", doc, count=1, flags=re.S)

    # 4. Tab label → v2.
    doc = doc.replace('<a href="#s1">The Data</a>', '<a href="#s1">The World</a>')

    # 4. The map is now a self-contained SVG schematic — remove any Leaflet
    #    assets a prior build injected.
    doc = re.sub(r'\s*<link[^>]*leaflet@[^>]*>', "", doc)
    doc = re.sub(r'\s*<script[^>]*leaflet@[^>]*></script>', "", doc)

    # 5. Refresh the Section-1 CSS: strip any prior generated block, re-inject
    #    the current one (so CSS edits propagate on rebuild).
    doc = re.sub(r'\n  /\* ---- Section 1 \(generated\) additions ---- \*/.*?(?=</style>)',
                 "", doc, flags=re.S)
    doc = doc.replace("</style>", CSS_ADD + "</style>")

    INDEX_HTML.write_text(doc)


# ----- main -------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Build Section 1 of docs/index.html")
    ap.add_argument("--print-numbers", action="store_true",
                    help="Print the STEP-1 zone table + scorecard and exit.")
    args = ap.parse_args()

    con = _con()
    zones = load_zones()
    M = measure(con)

    if args.print_numbers:
        print_numbers(con, zones, M)
        return

    rows = zone_table(con, zones)
    CD = chart_data(con)
    section = render_section(con, zones, rows, M, CD)
    splice(section, M)
    print(f"Section 1 written to {INDEX_HTML} ({len(section):,} chars).")


if __name__ == "__main__":
    main()
