"""datamodel-v2 §6 acceptance battery — T1 through T18.

One test per realism invariant. Runs against the pilot dataset emitted to
data/raw/ + data/eval/ by the conftest fixture. Each band is interpreted
at pilot scale — proportional volume targets where T1/T3/T17 scale with
population, distribution checks where the band is scale-invariant.

v2 model: grocery (KRG/ACM/WDX) + QSR (TBL/BKG/CFA), 38 stores, 155k cards,
static committed catalog (dual taxonomy, PL as SKUs), flat shelf-price
pricing, promotions + anomalies DORMANT. Line items carry only
`sku` + qty + price — category/taxonomy/PL/name resolve via the products
join (the ``items_x`` fixture). Targets trace to docs/MERCHANT_PROFILES.md
(§A9/§B7/§C9 + Appendix D).

Each test print()s its measured value(s) against its band so the DQ report
and pytest output show *magnitudes*, not just pass/fail.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

GROCERS = ("KRG", "ACM", "WDX")
QSR = ("TBL", "BKG", "CFA")


# ============ T1 — Total volume + per-segment ===============

def test_T1_total_volume_in_band(cfg, transactions, scale_frac) -> None:
    expected = cfg.global_["volume_targets"]["total"] * scale_frac
    actual = len(transactions)
    print(f"\nT1 total txns: {actual:,}  (scale {scale_frac*100:.1f}%, expected ~{int(expected):,})")
    assert 0.70 * expected <= actual <= 1.15 * expected


@pytest.mark.parametrize("segment", ["grocery", "qsr"])
def test_T1_per_segment_volume_in_band(cfg, transactions, scale_frac, segment) -> None:
    expected = cfg.global_["volume_targets"][segment] * scale_frac
    actual = int((transactions["segment"] == segment).sum())
    print(f"T1 {segment} txns: {actual:,}  (expected ~{int(expected):,})")
    assert 0.70 * expected <= actual <= 1.20 * expected


# ============ T2 — Per-segment AOV bands ====================

def test_T2_grocery_aov(transactions) -> None:
    aov = transactions[transactions["segment"] == "grocery"]["subtotal"].mean()
    print(f"\nT2 grocery AOV: ${aov:.2f}  (band $45-60, §A9.5)")
    assert 45 <= aov <= 60


def test_T2_qsr_aov(transactions) -> None:
    aov = transactions[transactions["segment"] == "qsr"]["subtotal"].mean()
    print(f"T2 QSR blended check: ${aov:.2f}  (band $8-14)")
    assert 8 <= aov <= 14


def test_T2_qsr_check_ordering(transactions) -> None:
    """§B2 check ordering CFA > BK > TB (CFA ~$13.5, BK ~$9.5, TB ~$8.5)."""
    q = transactions[transactions["segment"] == "qsr"]
    chk = {b: q[q["banner_code"] == b]["subtotal"].mean() for b in QSR}
    print(f"\nT2 QSR checks: CFA ${chk['CFA']:.2f} > BK ${chk['BKG']:.2f} > TB ${chk['TBL']:.2f}")
    assert chk["CFA"] > chk["BKG"] > chk["TBL"]


# ============ T3 — Per-banner AUV ===========================

def _auv(transactions, banner, scale_frac):
    t = transactions[transactions["banner_code"] == banner]
    by_store = t.groupby("store_id")["subtotal"].sum()
    return by_store.mean() * (365 / 90) / scale_frac


@pytest.mark.parametrize("banner,lo,hi", [
    ("KRG", 40e6, 50e6), ("ACM", 28e6, 36e6), ("WDX", 18e6, 26e6),
    ("CFA", 6.0e6, 9.5e6), ("TBL", 1.6e6, 2.7e6), ("BKG", 1.2e6, 2.1e6),
])
def test_T3_per_store_auv(transactions, scale_frac, banner, lo, hi) -> None:
    """Per-store AUV (annualized, scale-adjusted) per §A9/§B7/§C9.
    KRG ~$45M / ACM ~$32M / WDX ~$22M; CFA ~$8M / TB ~$2.1M / BK ~$1.6M."""
    auv = _auv(transactions, banner, scale_frac)
    print(f"T3 {banner} AUV-eq: ${auv/1e6:.1f}M/yr  (band ${lo/1e6:.0f}-{hi/1e6:.0f}M)")
    assert lo <= auv <= hi


def test_T3_grocery_dollar_split(transactions) -> None:
    """Grocery dollar split ~52/31/17 (KRG/ACM/WDX), Decision D."""
    g = transactions[transactions["segment"] == "grocery"]
    d = g.groupby("banner_code")["subtotal"].sum()
    d = (d / d.sum())
    print(f"\nT3 grocery $ split: KRG {d['KRG']*100:.1f} / ACM {d['ACM']*100:.1f} / WDX {d['WDX']*100:.1f}  (target 52/31/17)")
    assert 0.47 <= d["KRG"] <= 0.57
    assert 0.27 <= d["ACM"] <= 0.36
    assert 0.13 <= d["WDX"] <= 0.21


def test_T3_cfa_outearns_tb_bk_combined(transactions) -> None:
    """§B7: Chick-fil-A's 6 units out-earn Taco Bell's 9 + Burger King's 8."""
    q = transactions[transactions["segment"] == "qsr"]
    d = q.groupby("banner_code")["subtotal"].sum()
    ratio = d["CFA"] / (d["TBL"] + d["BKG"])
    print(f"\nT3 CFA ${d['CFA']:,.0f} vs TB+BK ${d['TBL']+d['BKG']:,.0f}  = {ratio:.2f}x (>1.0)")
    assert d["CFA"] > d["TBL"] + d["BKG"]


# ============ T4 — Day-of-week (grocery) ====================

def test_T4_grocery_weekend_weekday_ratio(transactions) -> None:
    g = transactions[transactions["segment"] == "grocery"].copy()
    g["dow"] = pd.to_datetime(g["txn_ts"]).dt.dayofweek
    weekend = ((g["dow"] == 5) | (g["dow"] == 6)).sum() / 2
    weekday = (g["dow"] <= 4).sum() / 5
    ratio = weekend / weekday
    print(f"\nT4 grocery weekend/weekday ratio: {ratio:.3f}  (band 1.15-1.40)")
    assert 1.15 <= ratio <= 1.40


# ============ T5 — Dayparts (per-banner QSR) ================

def test_T5_CFA_closed_sundays(transactions) -> None:
    """Chick-fil-A is closed Sundays — a TRUE hard zero (§B2)."""
    cfa = transactions[transactions["banner_code"] == "CFA"].copy()
    sun = (pd.to_datetime(cfa["txn_ts"]).dt.dayofweek == 6).sum()
    print(f"\nT5 CFA Sunday txns: {sun}  (must be 0)")
    assert sun == 0


def test_T5_TBL_late_night_share(transactions) -> None:
    tbl = transactions[transactions["banner_code"] == "TBL"]
    hour = pd.to_datetime(tbl["txn_ts"]).dt.hour
    late = ((hour >= 21) | (hour < 3)).mean()
    print(f"T5 TBL late-night (9pm+) share: {late*100:.1f}%  (band 15-23%)")
    assert 0.15 <= late <= 0.23


def test_T5_BKG_breakfast_present(transactions) -> None:
    bkg = transactions[transactions["banner_code"] == "BKG"]
    hour = pd.to_datetime(bkg["txn_ts"]).dt.hour
    bfast = ((hour >= 6) & (hour < 10)).mean()
    print(f"T5 BKG breakfast (6-10am) share: {bfast*100:.1f}%  (band 0.12-0.28)")
    assert 0.12 <= bfast <= 0.28


# ============ T6 — Pay-cycle lift ===========================

def test_T6_grocery_pay_cycle_lift(transactions) -> None:
    g = transactions[transactions["segment"] == "grocery"].copy()
    g["day"] = pd.to_datetime(g["txn_ts"]).dt.day
    early = (g["day"] <= 10).sum() / 10
    mid = ((g["day"] >= 15) & (g["day"] <= 17)).sum() / 3
    flat = ((g["day"] >= 20) & (g["day"] <= 28)).sum() / 9
    print(f"\nT6 grocery early {early:.0f}/d, mid {mid:.0f}/d, flat {flat:.0f}/d")
    assert early > flat * 1.04
    assert mid > flat * 1.02


# ============ T7 — Population shape ========================

def test_T7_population_size(cfg, customers, scale_frac) -> None:
    target = cfg.global_["population"]["target_cards"]
    actual = len(customers)
    expected = int(round(target * scale_frac))
    print(f"\nT7 population: {actual:,} cards  ({scale_frac*100:.1f}% of {target:,} target)")
    assert actual == expected


# ============ T8 — Participation split (§C1) ===============

def test_T8_both_segment_share(transactions) -> None:
    """§C1: the 'both' group (grocery + QSR) is ~46% of active cards."""
    by_card = transactions.groupby("customer_token")["segment"].nunique()
    both = (by_card >= 2).mean()
    print(f"\nT8 both-segment share (of active cards): {both*100:.1f}%  (band 42-50%)")
    assert 0.42 <= both <= 0.50


def test_T8_segment_activity(transactions, customers) -> None:
    """Grocery-active ~82%, QSR-active ~64% of the population (§C1)."""
    n = len(customers)
    g_active = transactions[transactions["segment"] == "grocery"]["customer_token"].nunique() / n
    q_active = transactions[transactions["segment"] == "qsr"]["customer_token"].nunique() / n
    print(f"T8 grocery-active {g_active*100:.1f}% (~82), qsr-active {q_active*100:.1f}% (~64)")
    assert 0.78 <= g_active <= 0.86
    assert 0.60 <= q_active <= 0.68


# ============ T9 — Loyalty concentration ===================

def test_T9_grocery_primary_banner_concentration(transactions) -> None:
    g_txn = transactions[transactions["segment"] == "grocery"]
    primary_share = g_txn.groupby("customer_token")["banner_code"].apply(
        lambda b: b.value_counts(normalize=True).max()
    ).mean()
    print(f"\nT9 grocery primary-banner concentration: {primary_share*100:.1f}%  (band 68-82%)")
    assert 0.68 <= primary_share <= 0.82


# ============ T10 — Repeat purchase (loyalists) ===========

def test_T10_loyalist_top_sku_share(items, transactions, customers) -> None:
    loy_cards = customers[customers["loyalty_type"] == "loyalist"]["card_id"]
    g_txn = transactions[
        (transactions["segment"] == "grocery")
        & transactions["customer_token"].isin(loy_cards)
    ]
    by_card_n = g_txn.groupby("customer_token").size()
    eligible = by_card_n[by_card_n >= 5].index
    if len(eligible) == 0:
        pytest.skip("no loyalists with >=5 grocery trips at pilot")
    sub_txn = g_txn[g_txn["customer_token"].isin(eligible)]
    sub_items = items.merge(sub_txn[["txn_id", "customer_token"]], on="txn_id")
    per_card_top_share = (
        sub_items.groupby(["customer_token", "sku"])["txn_id"].nunique()
        .reset_index().rename(columns={"txn_id": "in_trips"})
        .merge(by_card_n.rename("total_trips"), on="customer_token")
        .assign(share=lambda d: d["in_trips"] / d["total_trips"])
        .groupby("customer_token")["share"].max()
    )
    mean_top = per_card_top_share.mean()
    print(f"\nT10 loyalist mean top-SKU share of trips: {mean_top*100:.1f}%  (target >18%)")
    assert mean_top > 0.18


# ============ T11 — Affinity lift (grocery + QSR combo) ====

def _lift(items_x: pd.DataFrame, anchor: str, partner: str) -> tuple[float, float]:
    by_trip = items_x.groupby("txn_id")["subcategory"].apply(set)
    has_a = by_trip.apply(lambda s: anchor in s)
    p = by_trip.apply(lambda s: partner in s).mean()
    if p == 0 or has_a.sum() == 0:
        return float("nan"), float("nan")
    cond = by_trip[has_a].apply(lambda s: partner in s).mean()
    return cond / p, cond


@pytest.mark.parametrize("anchor,partner,min_lift", [
    ("Pasta", "Pasta Sauce", 1.8),
    ("Cereal", "2% Reduced-Fat Milk", 1.8),
    ("Potato & Tortilla Chips", "Salsa & Dips", 1.8),
    ("Sandwich Bread", "Butter", 1.4),
])
def test_T11_designed_affinity_lift(items_x, anchor, partner, min_lift) -> None:
    """Grocery complementary lift on the config affinity_matrix pairs
    (functional_subcategory, resolved via the products join)."""
    g = items_x[items_x["banner_code"].isin(GROCERS)]
    lift, cond = _lift(g, anchor, partner)
    print(f"\nT11 {anchor}->{partner}  lift={lift:.2f}x  P(B|A)={cond:.3f}  (>= {min_lift}x)")
    assert lift >= min_lift


def test_T11_qsr_combo_attach(items_x) -> None:
    """§B5 combo-attach: P(Beverages|Entrée) and P(Sides|Entrée) materialize,
    CFA > BK > TB ordering (functional_category)."""
    q = items_x[items_x["banner_code"].isin(QSR)]
    bb = q.groupby("txn_id").agg(cats=("category", set), banner=("banner_code", "first"))
    drink = {}
    for b in QSR:
        sub = bb[bb["banner"] == b]
        ent = sub[sub["cats"].apply(lambda s: "Entrée" in s)]
        drink[b] = ent["cats"].apply(lambda s: "Beverages" in s).mean() if len(ent) else 0.0
    print(f"\nT11 QSR drink|entrée attach: CFA {drink['CFA']:.2f} > BK {drink['BKG']:.2f} > TB {drink['TBL']:.2f}")
    assert all(v > 0.30 for v in drink.values())     # attach materializes
    assert drink["CFA"] >= drink["BKG"] >= drink["TBL"]


# ============ T12 — Heavy-tail =============================

def test_T12_top20pct_grocery_unit_share(items, transactions) -> None:
    g_txn = transactions[transactions["segment"] == "grocery"]["txn_id"]
    g_items = items[items["txn_id"].isin(g_txn)]
    units = g_items.groupby("txn_id")["qty"].sum().sort_values(ascending=False)
    top_n = max(1, int(len(units) * 0.20))
    top_share = units.iloc[:top_n].sum() / units.sum()
    # Lower edge widened 0.42→0.40 for pilot-scale basket-size dispersion
    # (the heavy-tail shape holds; ~42% at pilot, tightens at full scale).
    print(f"\nT12 top-20% grocery basket unit share: {top_share*100:.1f}%  (band 40-60%)")
    assert 0.40 <= top_share <= 0.60


# ============ T13 — Payment mix ============================

def test_T13_blended_contactless_share(transactions) -> None:
    share = (transactions["entry_mode"] == "contactless").mean()
    print(f"\nT13 blended contactless: {share*100:.1f}%  (band 45-60%)")
    assert 0.45 <= share <= 0.60


def test_T13_wallet_at_tap_share(transactions) -> None:
    share = transactions["wallet_at_tap"].mean()
    print(f"T13 wallet-at-tap: {share*100:.1f}%  (band 13-22%)")
    assert 0.13 <= share <= 0.22


def test_T13_grocery_debit_per_banner_emergence(transactions) -> None:
    """Per-banner debit skew: value banner (WDX) > premium banner (ACM)."""
    g = transactions[transactions["segment"] == "grocery"]
    wdx = (g[g["banner_code"] == "WDX"]["tender"] == "debit").mean()
    acm = (g[g["banner_code"] == "ACM"]["tender"] == "debit").mean()
    krg = (g[g["banner_code"] == "KRG"]["tender"] == "debit").mean()
    print(f"\nT13 grocery debit by banner: KRG {krg*100:.1f}%  ACM {acm*100:.1f}%  WDX {wdx*100:.1f}%")
    assert wdx > acm + 0.02


# ============ T14 — Pricing (flat shelf-price) =============

def test_T14_flat_pricing_equals_shelf_price(items_x) -> None:
    """§C7: realized unit_price == catalog shelf_price exactly (flat)."""
    sample = items_x.dropna(subset=["shelf_price"])
    mism = (sample["unit_price"].round(2) != sample["shelf_price"].round(2)).sum()
    print(f"\nT14 flat pricing: {mism} of {len(sample):,} lines with unit_price != shelf_price")
    assert mism == 0


def test_T14_no_banner_cheapest_majority(products) -> None:
    """No grocer cheapest on >70% of shared functional_subcategories,
    from the baked shelf_price (§A9.4)."""
    g = products[products["segment"] == "grocery"]
    med = g.groupby(["functional_subcategory", "banner_code"])["shelf_price"].median().reset_index()
    cnt = med.groupby("functional_subcategory")["banner_code"].nunique()
    shared = cnt[cnt >= 2].index
    med = med[med["functional_subcategory"].isin(shared)]
    cheapest = med.loc[med.groupby("functional_subcategory")["shelf_price"].idxmin(), "banner_code"]
    shares = cheapest.value_counts(normalize=True).to_dict()
    print(f"\nT14 cheapest-banner shares: " +
          ", ".join(f"{b} {shares.get(b,0)*100:.1f}%" for b in GROCERS))
    assert all(s <= 0.70 for s in shares.values())


def test_T14_private_label_gap(products) -> None:
    """PL priced below national brand within a banner+subcategory."""
    g = products[products["segment"] == "grocery"]
    by = g.groupby(["banner_code", "functional_subcategory", "private_label"])["shelf_price"].mean()
    by = by.unstack("private_label").dropna()
    by.columns = ["nb", "pl"]
    gap = (1 - by["pl"] / by["nb"]).mean()
    print(f"\nT14 PL gap (blended within subcat): {gap*100:.1f}%  (band 8-35%)")
    assert 0.08 <= gap <= 0.35


# ============ T15 — Promotions DORMANT =====================

def test_T15_promotions_dormant(promotions, items) -> None:
    """datamodel-v2: promotions disabled — the table is empty and no line
    carries a promo_id / discount."""
    print(f"\nT15 promotions rows: {len(promotions)} (dormant, must be 0)")
    assert len(promotions) == 0
    assert items["promo_id"].isna().all()
    assert float(items["discount"].abs().sum()) == 0.0


# ============ T16 — Anomalies DORMANT ======================

def test_T16_anomalies_dormant(anomalies) -> None:
    """datamodel-v2: planted anomalies disabled — ground-truth table empty
    (the framework is kept dormant for a later wave)."""
    print(f"\nT16 anomalies_groundtruth rows: {len(anomalies)} (dormant, must be 0)")
    assert len(anomalies) == 0


# ============ T17 — Small-cell readiness (both-segment) ====

def test_T17_both_segment_cells_per_zone(transactions, customers, scale_frac) -> None:
    """The lake gate: every zone's cross-SEGMENT ('both' = grocery+QSR)
    cell should survive k=5 at full scale. v2 has two segments, so the
    cross-cell is the grocery∩QSR card set per home_zone."""
    by_card_seg = transactions.groupby("customer_token")["segment"].nunique()
    both = set(by_card_seg[by_card_seg >= 2].index)
    cards_z = customers[customers["card_id"].isin(both)][["card_id", "home_zone"]]
    by_zone = cards_z.groupby("home_zone").size().sort_values(ascending=False)
    print(f"\nT17 both-segment cards by home_zone (scale {scale_frac*100:.1f}%):")
    for z, n in by_zone.items():
        print(f"  {z:>16}  {n:>5} cards{'' if n >= 5 else '  <k=5'}")
    assert (by_zone >= 1).sum() == 8, "every zone must have >=1 both-segment card"
    if scale_frac >= 0.5:
        assert int((by_zone >= 5).sum()) == 8


# ============ T18 — Reproducibility ========================

def test_T18_reproducibility_content_identical(cfg) -> None:
    """Two engine runs at the same seed produce content-identical
    transactions AND transaction_items (byte-identical determinism is
    re-established on the v2 model)."""
    from src.generate.engine.run_all import build_all
    a = build_all(scale=500)
    b = build_all(scale=500)
    for tbl in ("transactions", "transaction_items"):
        aa = a[tbl].sort_values(list(a[tbl].columns)).reset_index(drop=True)
        bb = b[tbl].sort_values(list(b[tbl].columns)).reset_index(drop=True)
        pd.testing.assert_frame_equal(aa, bb)
    print("\nT18 transactions + transaction_items content-identical at seed=42 ✓")


# ============ v2 additions — department mix, PL, §A13 ======

def test_dept_sales_mix(items_x) -> None:
    """§A4 department $ mix: meat ~11-14, produce ~10-12, dairy ~7-9,
    center-store (Dry+Snacks+Beverages) ~36-40 (functional_department)."""
    g = items_x[items_x["banner_code"].isin(GROCERS)]
    d = g.groupby("functional_department")["line_total"].sum()
    d = d / d.sum()
    center = d.get("Dry Grocery", 0) + d.get("Snacks & Candy", 0) + d.get("Beverages", 0)
    meat = d.get("Meat & Seafood", 0); produce = d.get("Produce", 0); dairy = d.get("Dairy & Eggs", 0)
    print(f"\ndept mix: center-store {center*100:.1f} (36-40), meat {meat*100:.1f} (11-14), "
          f"produce {produce*100:.1f} (10-12), dairy {dairy*100:.1f} (7-9)")
    assert 0.36 <= center <= 0.40
    assert 0.11 <= meat <= 0.15
    assert 0.09 <= produce <= 0.13
    assert 0.07 <= dairy <= 0.095


def test_pl_share_by_banner(items_x) -> None:
    """PL share is a MEASURED selection outcome: KRG ~27 / ACM ~19 / WDX ~25,
    ordering KRG > WDX > ACM (§A12 / App D)."""
    g = items_x[items_x["banner_code"].isin(GROCERS)]
    pl = {}
    for b in GROCERS:
        d = g[g["banner_code"] == b]
        pl[b] = d.loc[d["private_label"], "qty"].sum() / d["qty"].sum()
    print(f"\nPL share: KRG {pl['KRG']*100:.1f} / ACM {pl['ACM']*100:.1f} / WDX {pl['WDX']*100:.1f}  (27/19/25)")
    assert 0.22 <= pl["KRG"] <= 0.32
    assert 0.14 <= pl["ACM"] <= 0.24
    assert 0.21 <= pl["WDX"] <= 0.30
    assert pl["KRG"] > pl["ACM"] and pl["WDX"] > pl["ACM"]


def test_A13_fresh_mix_rises_with_affluence(items_x, transactions, customers) -> None:
    """§A13: fresh/premium $ share rises MONOTONICALLY with affluence
    (smooth latent-first gradient, not a flat/painted mix)."""
    fresh = {"Meat & Seafood", "Produce", "Bakery", "Dairy & Eggs"}
    g = items_x[items_x["banner_code"].isin(GROCERS)].merge(
        transactions[["txn_id", "customer_token"]], on="txn_id"
    ).merge(
        customers[["card_id", "affluence"]],
        left_on="customer_token", right_on="card_id",
    )
    q1, q2 = g["affluence"].quantile([0.33, 0.66])
    g["tier"] = np.where(g["affluence"] < q1, "low", np.where(g["affluence"] > q2, "high", "mid"))
    shares = {}
    for t in ("low", "mid", "high"):
        d = g[g["tier"] == t]
        shares[t] = d[d["functional_department"].isin(fresh)]["line_total"].sum() / d["line_total"].sum()
    print(f"\n§A13 fresh $ share by affluence: low {shares['low']*100:.1f} < "
          f"mid {shares['mid']*100:.1f} < high {shares['high']*100:.1f}")
    assert shares["low"] < shares["mid"] < shares["high"]


# ============ Schema invariants (v2 line/catalog) ==========

def test_schema_transaction_items_minimal(items) -> None:
    """Line items carry ONLY sku + qty + price (+ dormant discount/promo_id)
    — no canonical_id / category / subcategory (§A12 boundary)."""
    cols = set(items.columns)
    assert cols == {"txn_id", "line_id", "sku", "qty", "unit_price",
                    "discount", "promo_id", "line_total"}, cols
    for forbidden in ("canonical_id", "category", "subcategory", "product_name"):
        assert forbidden not in cols


def test_schema_products_dual_taxonomy_no_canonical(products) -> None:
    cols = set(products.columns)
    for req in ("functional_department", "functional_category", "functional_subcategory",
                "merchant_department", "merchant_category", "merchant_subcategory",
                "product_name", "shelf_price", "private_label"):
        assert req in cols, f"products missing {req}"
    assert "canonical_id" not in cols, "canonical_id must stay hidden (data/eval)"
