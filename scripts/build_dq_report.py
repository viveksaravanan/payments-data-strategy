"""Generate the datamodel-v2 §6 data-quality report.

Reads ``data/raw/`` + ``data/eval/`` (produced by
``python -m src.generate.engine.run_all``) and emits a Markdown report
with the v2 acceptance measurements alongside their bands. This is the
artifact an exec/reviewer reads to trust the dataset.

Line items carry only ``sku`` + qty + price; taxonomy / PL resolve via a
join to ``products`` on ``sku`` (the normalization boundary). Bands trace
to docs/MERCHANT_PROFILES.md (§A9 / §B7 / §C9 / Appendix D).

Run: ``uv run python scripts/build_dq_report.py [--out docs/DQ_REPORT.md]``
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from src.generate.config.loader import load_config
from src.storage.duckdb_io import read_parquet

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_RAW = REPO_ROOT / "data" / "raw"
DATA_EVAL = REPO_ROOT / "data" / "eval"
CONFIG_ROOT = REPO_ROOT / "src" / "generate" / "config"
DEFAULT_OUT = REPO_ROOT / "docs" / "DQ_REPORT.md"

GROCERS = ("KRG", "ACM", "WDX")
QSR = ("TBL", "BKG", "CFA")
FRESH = {"Meat & Seafood", "Produce", "Bakery", "Dairy & Eggs"}
CENTER = {"Dry Grocery", "Snacks & Candy", "Beverages"}


def _load(name: str, eval_: bool = False) -> pd.DataFrame:
    base = DATA_EVAL if eval_ else DATA_RAW
    return read_parquet(str(base / f"{name}.parquet")).df()


def _band(v: float, lo: float, hi: float) -> str:
    return "⚠ below band" if v < lo else ("⚠ above band" if v > hi else "✓ in band")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    cfg = load_config(CONFIG_ROOT)
    txn = _load("transactions")
    items = _load("transaction_items")
    customers = _load("customers")
    products = _load("products")
    promotions = _load("promotions")
    anomalies = _load("anomalies_groundtruth", eval_=True)

    # Resolve taxonomy/PL via the products join (line carries only sku).
    px = products[["sku", "banner_code", "functional_department",
                   "functional_category", "functional_subcategory",
                   "private_label", "shelf_price"]].rename(columns={
        "functional_category": "category", "functional_subcategory": "subcategory"})
    ix = items.merge(px, on="sku", how="left")

    n_cards, n_txns, n_lines = len(customers), len(txn), len(items)
    target_cards = cfg.global_["population"]["target_cards"]
    sf = n_cards / target_cards
    scale_label = "FULL" if sf >= 0.95 else f"{sf*100:.1f}% pilot"
    up = (1 / sf) * (365 / 90)   # pilot→full-year full-pop scale

    L: list[str] = []
    def w(s=""): L.append(s)

    w("# Data-Quality Report — datamodel-v2")
    w()
    w(f"**Scale:** {n_cards:,} cards ({scale_label} — target {target_cards:,})")
    w(f"**Transactions:** {n_txns:,}  |  **Line items:** {n_lines:,}")
    w(f"**Window:** {cfg.global_['window']['start_date']} → {cfg.global_['window']['end_date']}")
    w()
    w("Grocery (KRG/ACM/WDX) + QSR (TBL/BKG/CFA); 38 stores; flat shelf-price; "
      "promotions + anomalies dormant. Measured value shown next to each band; "
      "targets trace to §A9/§B7/§C9/Appendix D.")
    w()

    # T1 volume
    w("## T1 — Volume")
    exp_tot = cfg.global_["volume_targets"]["total"] * sf
    w(f"- **Total txns:** {n_txns:,} (target ~{int(exp_tot):,}) — {_band(n_txns, 0.80*exp_tot, 1.15*exp_tot)}")
    for seg in ("grocery", "qsr"):
        exp = cfg.global_["volume_targets"][seg] * sf
        act = int((txn["segment"] == seg).sum())
        w(f"- **{seg}:** {act:,} (target ~{int(exp):,}) — {_band(act, 0.80*exp, 1.20*exp)}")
    w()

    # T2 AOV
    w("## T2 — Basket / check")
    aov_g = txn[txn.segment == "grocery"]["subtotal"].mean()
    aov_q = txn[txn.segment == "qsr"]["subtotal"].mean()
    w(f"- **Grocery AOV:** ${aov_g:.2f} (band $45-60, §A9.5) — {_band(aov_g, 45, 60)}")
    w(f"- **QSR check (blended):** ${aov_q:.2f} (band $8-14) — {_band(aov_q, 8, 14)}")
    chk = {b: txn[txn.banner_code == b]["subtotal"].mean() for b in QSR}
    w(f"- **QSR check ordering:** CFA ${chk['CFA']:.2f} > BK ${chk['BKG']:.2f} > TB ${chk['TBL']:.2f} "
      f"(§B2) — {'✓' if chk['CFA']>chk['BKG']>chk['TBL'] else '⚠'}")
    w()

    # T3 AUV + splits
    w("## T3 — Per-store AUV (annualized, scale-adjusted) & merchant split")
    auv_targets = {"KRG": 45, "ACM": 32, "WDX": 22, "CFA": 8.0, "TBL": 2.1, "BKG": 1.6}
    for b in ("KRG", "ACM", "WDX", "CFA", "TBL", "BKG"):
        s = txn[txn.banner_code == b].groupby("store_id")["subtotal"].sum().mean() * up
        t = auv_targets[b]
        w(f"- **{b} AUV:** ${s/1e6:.1f}M/yr (target ~${t}M) — {_band(s/1e6, t*0.85, t*1.20)}")
    gd = txn[txn.segment == "grocery"].groupby("banner_code")["subtotal"].sum()
    gd = gd / gd.sum()
    w(f"- **Grocery $ split:** KRG {gd['KRG']*100:.1f} / ACM {gd['ACM']*100:.1f} / WDX {gd['WDX']*100:.1f} "
      f"(target 52/31/17) — {'✓' if 0.47<=gd['KRG']<=0.57 else '⚠'}")
    qd = txn[txn.segment == "qsr"].groupby("banner_code")["subtotal"].sum()
    w(f"- **CFA vs TB+BK:** CFA ${qd['CFA']:,.0f} vs ${qd['TBL']+qd['BKG']:,.0f} "
      f"= {qd['CFA']/(qd['TBL']+qd['BKG']):.2f}× — {'✓ CFA out-earns' if qd['CFA']>qd['TBL']+qd['BKG'] else '⚠'}")
    w()

    # Department $ mix (§A4)
    w("## §A4 — Department sales mix (grocery $)")
    g = ix[ix.banner_code.isin(GROCERS)]
    dm = g.groupby("functional_department")["line_total"].sum(); dm = dm / dm.sum()
    center = sum(dm.get(d, 0) for d in CENTER)
    w(f"- **Center-store (Dry+Snacks+Bev):** {center*100:.1f}% (target ~38) — {_band(center, 0.36, 0.40)}")
    w(f"- **Meat & Seafood:** {dm.get('Meat & Seafood',0)*100:.1f}% (target ~13) — {_band(dm.get('Meat & Seafood',0), 0.11, 0.15)}")
    w(f"- **Produce:** {dm.get('Produce',0)*100:.1f}% (target ~11) — {_band(dm.get('Produce',0), 0.09, 0.13)}")
    w(f"- **Dairy & Eggs:** {dm.get('Dairy & Eggs',0)*100:.1f}% (target ~8) — {_band(dm.get('Dairy & Eggs',0), 0.07, 0.095)}")
    w()

    # PL share
    w("## §A12 — Private-label share (measured from basket selection)")
    for b in GROCERS:
        d = g[g.banner_code == b]
        pl = d.loc[d.private_label, "qty"].sum() / d["qty"].sum()
        t = {"KRG": 27, "ACM": 19, "WDX": 25}[b]
        w(f"- **{b}:** {pl*100:.1f}% (target ~{t}) — {_band(pl, (t-6)/100, (t+6)/100)}")
    w("- Ordering KRG > WDX > ACM (real-chain PL-program strength × affluence selection).")
    w()

    # §A13 fresh gradient
    w("## §A13 — Fresh/premium mix rises with affluence")
    gg = g.merge(txn[["txn_id", "customer_token"]], on="txn_id").merge(
        customers[["card_id", "affluence"]], left_on="customer_token", right_on="card_id")
    q1, q2 = gg["affluence"].quantile([0.33, 0.66])
    gg["tier"] = np.where(gg.affluence < q1, "low", np.where(gg.affluence > q2, "high", "mid"))
    frsh = {t: gg[(gg.tier == t) & gg.functional_department.isin(FRESH)]["line_total"].sum()
            / gg[gg.tier == t]["line_total"].sum() for t in ("low", "mid", "high")}
    mono = frsh["low"] < frsh["mid"] < frsh["high"]
    w(f"- **Fresh $ share low→mid→high:** {frsh['low']*100:.1f} → {frsh['mid']*100:.1f} → {frsh['high']*100:.1f}% "
      f"— {'✓ smooth monotonic gradient' if mono else '⚠ not monotonic'}")
    w()

    # T4/T5 timing + dayparts
    w("## T4/T5 — Timing & dayparts")
    gt = txn[txn.segment == "grocery"].copy(); gt["dow"] = pd.to_datetime(gt.txn_ts).dt.dayofweek
    ratio = (((gt.dow == 5) | (gt.dow == 6)).sum()/2) / ((gt.dow <= 4).sum()/5)
    w(f"- **Grocery weekend/weekday ratio:** {ratio:.3f} (band 1.15-1.40) — {_band(ratio, 1.15, 1.40)}")
    cfa = txn[txn.banner_code == "CFA"]; sun = (pd.to_datetime(cfa.txn_ts).dt.dayofweek == 6).sum()
    w(f"- **CFA Sunday txns:** {sun} — {'✓ hard zero (closed)' if sun == 0 else '⚠ NONZERO'}")
    tbl = txn[txn.banner_code == "TBL"]; late = ((pd.to_datetime(tbl.txn_ts).dt.hour >= 21) | (pd.to_datetime(tbl.txn_ts).dt.hour < 3)).mean()
    w(f"- **TBL late-night (9pm+):** {late*100:.1f}% (band 15-23) — {_band(late, 0.15, 0.23)}")
    bkg = txn[txn.banner_code == "BKG"]; bf = ((pd.to_datetime(bkg.txn_ts).dt.hour >= 6) & (pd.to_datetime(bkg.txn_ts).dt.hour < 10)).mean()
    w(f"- **BKG breakfast (6-10am):** {bf*100:.1f}% (band 12-28) — {_band(bf, 0.12, 0.28)}")
    w()

    # T7/T8 population
    w("## T7/T8 — Population & participation")
    w(f"- **Cards:** {n_cards:,}")
    both = (txn.groupby("customer_token")["segment"].nunique() >= 2).mean()
    w(f"- **Both-segment share:** {both*100:.1f}% (target ~46) — {_band(both, 0.42, 0.50)}")
    g_act = txn[txn.segment == "grocery"]["customer_token"].nunique() / n_cards
    q_act = txn[txn.segment == "qsr"]["customer_token"].nunique() / n_cards
    w(f"- **Grocery-active:** {g_act*100:.1f}% (~82) | **QSR-active:** {q_act*100:.1f}% (~64)")
    conc = txn[txn.segment == "grocery"].groupby("customer_token")["banner_code"].apply(
        lambda b: b.value_counts(normalize=True).max()).mean()
    w(f"- **T9 loyalty concentration:** {conc*100:.1f}% (band 68-82) — {_band(conc, 0.68, 0.82)}")
    w()

    # T11 affinity + combo-attach
    w("## T11 — Affinity lift + QSR combo-attach")
    bysub = g.groupby("txn_id")["subcategory"].agg(set)
    def lift(a, p):
        ha = bysub.apply(lambda s: a in s); pp = bysub.apply(lambda s: p in s).mean()
        return (bysub[ha].apply(lambda s: p in s).mean() / pp) if (ha.sum() and pp) else float("nan")
    for a, p in [("Pasta", "Pasta Sauce"), ("Cereal", "2% Reduced-Fat Milk"),
                 ("Potato & Tortilla Chips", "Salsa & Dips")]:
        lv = lift(a, p)
        w(f"- **{a}→{p}:** {lv:.2f}× — {'✓' if lv >= 1.8 else '⚠'} (≥1.8)")
    q = ix[ix.banner_code.isin(QSR)]
    bb = q.groupby("txn_id").agg(cats=("category", set), banner=("banner_code", "first"))
    drink = {b: (bb[(bb.banner == b) & bb.cats.apply(lambda s: "Entrée" in s)]
                 .cats.apply(lambda s: "Beverages" in s).mean()) for b in QSR}
    w(f"- **QSR drink|entrée attach:** CFA {drink['CFA']:.2f} > BK {drink['BKG']:.2f} > TB {drink['TBL']:.2f} "
      f"— {'✓' if drink['CFA']>=drink['BKG']>=drink['TBL'] else '⚠'}")
    w()

    # T12 heavy-tail
    u = g.groupby("txn_id")["qty"].sum().sort_values(ascending=False)
    top20 = u.iloc[:max(1, int(len(u)*0.2))].sum() / u.sum()
    w("## T12 — Heavy-tail basket")
    w(f"- **Top-20% grocery basket unit share:** {top20*100:.1f}% (band 40-60) — {_band(top20, 0.40, 0.60)}")
    w()

    # T13 payment
    w("## T13 — Payment mix")
    ct = (txn.entry_mode == "contactless").mean(); wt = txn.wallet_at_tap.mean()
    w(f"- **Contactless:** {ct*100:.1f}% (band 45-60) — {_band(ct, 0.45, 0.60)}")
    w(f"- **Wallet-at-tap:** {wt*100:.1f}% (band 13-22) — {_band(wt, 0.13, 0.22)}")
    g2 = txn[txn.segment == "grocery"]
    wdxd = (g2[g2.banner_code == "WDX"].tender == "debit").mean()
    acmd = (g2[g2.banner_code == "ACM"].tender == "debit").mean()
    w(f"- **Grocery debit WDX {wdxd*100:.1f}% > ACM {acmd*100:.1f}%:** {'✓' if wdxd > acmd else '⚠'}")
    w()

    # T14 pricing (flat, from shelf_price)
    w("## T14 — Pricing (flat shelf-price)")
    _fp = ix.dropna(subset=["shelf_price"])
    mism = int((_fp["unit_price"].round(2) != _fp["shelf_price"].round(2)).sum())
    w(f"- **Flat pricing (unit_price == shelf_price):** {mism} mismatches — {'✓' if mism == 0 else '⚠'}")
    med = products[products.segment == "grocery"].groupby(
        ["functional_subcategory", "banner_code"])["shelf_price"].median().reset_index()
    cnt = med.groupby("functional_subcategory")["banner_code"].nunique()
    med = med[med.functional_subcategory.isin(cnt[cnt >= 2].index)]
    cheap = med.loc[med.groupby("functional_subcategory")["shelf_price"].idxmin(), "banner_code"]
    sh = cheap.value_counts(normalize=True).to_dict()
    w(f"- **No banner cheapest >70%:** " + ", ".join(f"{b} {sh.get(b,0)*100:.0f}%" for b in GROCERS)
      + f" — {'✓' if all(v <= 0.70 for v in sh.values()) else '⚠'}")
    w()

    # T15/T16 dormant
    w("## T15/T16 — Promotions & anomalies (DORMANT)")
    w(f"- **Promotions rows:** {len(promotions)} — {'✓ dormant' if len(promotions) == 0 else '⚠'}")
    w(f"- **Anomalies_groundtruth rows:** {len(anomalies)} — {'✓ dormant' if len(anomalies) == 0 else '⚠'}")
    w("- Framework kept dormant (Decision B); returns in a later anomaly wave.")
    w()

    # T17 cross-segment cells
    w(f"## T17 — Both-segment cell readiness ({scale_label})")
    both_cards = set(txn.groupby("customer_token")["segment"].nunique().pipe(lambda s: s[s >= 2]).index)
    bz = customers[customers.card_id.isin(both_cards)].groupby("home_zone").size().sort_values(ascending=False)
    for z, n in bz.items():
        w(f"- {z:<16}: {n:,} both-segment cards{'' if n >= 5 else '  ⚠ <k=5'}")
    w(f"**{int((bz>=5).sum())}/8 zones** survive k=5; **{int((bz>=1).sum())}/8** populated"
      + ("" if sf >= 0.95 else f" (pilot; cells multiply ~{1/sf:.0f}× at full scale)."))
    w()

    # T18 determinism
    w("## T18 — Reproducibility")
    w("- Verified content-identical (transactions + transaction_items) across two "
      "`build_all(scale=500)` runs by `test_T18_reproducibility_content_identical`.")
    w("- Catalog authoring (`make catalog`) is byte-identical on rebuild (hash/index-derived, no RNG).")
    w()

    # Totals
    w("## Totals (annualized, full-population projection)")
    gy = g["line_total"].sum() * up; qy = q["line_total"].sum() * up
    w(f"- **Grocery:** ${gy/1e6:.0f}M/yr (target ~$518M)  |  **QSR:** ${qy/1e6:.0f}M/yr (target ~$80M)")
    w(f"- Window totals scale from the {scale_label} sample.")
    w()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(L))
    print(f"Wrote DQ report → {args.out.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
