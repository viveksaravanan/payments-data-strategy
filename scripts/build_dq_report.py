"""Generate the Wave 1 §6 data-quality report.

Reads ``data/raw/`` + ``data/eval/`` (produced by
``python -m src.generate.engine.run_all``) and emits a human-readable
Markdown report with T1-T18 measured numbers alongside their bands.
This is the artifact an exec/reviewer reads to trust the dataset.

Run: ``uv run python scripts/build_dq_report.py [--out docs/DQ_REPORT.md]``
"""
from __future__ import annotations

import argparse
from datetime import date
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


def _load(name: str, eval_: bool = False) -> pd.DataFrame:
    base = DATA_EVAL if eval_ else DATA_RAW
    return read_parquet(str(base / f"{name}.parquet")).df()


def _band(measured: float, lo: float, hi: float) -> str:
    if measured < lo:
        return "⚠ below band"
    if measured > hi:
        return "⚠ above band"
    return "✓ in band"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    cfg = load_config(CONFIG_ROOT)
    transactions = _load("transactions")
    items = _load("transaction_items")
    customers = _load("customers")
    products = _load("products")
    stores = _load("stores")
    promotions = _load("promotions")
    anomalies = _load("anomalies_groundtruth", eval_=True)

    n_cards = len(customers)
    n_txns = len(transactions)
    n_lines = len(items)
    target_cards = cfg.global_["population"]["target_cards"]
    scale_frac = n_cards / target_cards
    scale_label = "FULL" if scale_frac >= 0.95 else f"{scale_frac*100:.1f}% pilot"

    lines: list[str] = []
    lines.append("# Wave 1 Data-Quality Report")
    lines.append("")
    lines.append(f"**Generated at scale:** {n_cards:,} cards ({scale_label} — target {target_cards:,})")
    lines.append(f"**Transactions:** {n_txns:,}  |  **Line items:** {n_lines:,}")
    lines.append(f"**Window:** {cfg.global_['window']['start_date']} → {cfg.global_['window']['end_date']}")
    lines.append("")
    lines.append("Each acceptance invariant below shows the measured value next to its target band.")
    lines.append("Bands match SPEC §6 unless noted as pilot-adjusted.")
    lines.append("")

    # ----- T1 volume
    expected_total = cfg.global_["volume_targets"]["total"] * scale_frac
    lines.append("## T1 — Volume")
    lines.append(f"- **Total txns:** {n_txns:,} (target ~{int(expected_total):,}, ±10%) — {_band(n_txns, 0.85*expected_total, 1.15*expected_total)}")
    for seg, key in (("grocery","grocery"),("qsr","qsr"),("off_price","off_price")):
        expected = cfg.global_["volume_targets"][key] * scale_frac
        actual = int((transactions["segment"] == seg).sum())
        lines.append(f"- **{seg}:** {actual:,} (target ~{int(expected):,}) — {_band(actual, 0.85*expected, 1.15*expected)}")
    lines.append("")

    # ----- T2 AOV
    lines.append("## T2 — Per-segment AOV")
    aov_g = transactions[transactions["segment"]=="grocery"]["subtotal"].mean()
    aov_q = transactions[transactions["segment"]=="qsr"]["subtotal"].mean()
    aov_o = transactions[transactions["segment"]=="off_price"]["subtotal"].mean()
    lines.append(f"- **Grocery:** ${aov_g:.2f} (band $48-62, D17.4 anchor ~$55) — {_band(aov_g, 48, 62)}")
    lines.append(f"- **QSR:** ${aov_q:.2f} (band $9-12) — {_band(aov_q, 9, 12)}")
    lines.append(f"- **Off-price:** ${aov_o:.2f} (band $30-50) — {_band(aov_o, 30, 50)}")
    lines.append("")

    # ----- T3 AUV
    lines.append("## T3 — Grocery store AUV (annualized)")
    g_revenue = transactions[transactions["segment"]=="grocery"].groupby("store_id")["subtotal"].sum()
    auv_eq = g_revenue.mean() * (365 / 90) / scale_frac
    auv_native = g_revenue.mean() * (365 / 90)
    if scale_frac >= 0.95:
        lines.append(f"- **AUV:** ${auv_native/1e6:.2f}M/yr (band $14-18M, full-scale direct) — {_band(auv_native, 14e6, 18e6)}")
    else:
        lines.append(f"- **AUV-equivalent (scaled 1/{scale_frac:.3f}):** ${auv_eq/1e6:.2f}M/yr (band $14-18M) — {_band(auv_eq, 14e6, 18e6)}")
    lines.append("")

    # ----- T4 DoW
    g_txn = transactions[transactions["segment"]=="grocery"].copy()
    g_txn["dow"] = pd.to_datetime(g_txn["txn_ts"]).dt.dayofweek
    weekend = ((g_txn["dow"]==5)|(g_txn["dow"]==6)).sum()/2
    weekday = (g_txn["dow"]<=4).sum()/5
    ratio = weekend / weekday
    lines.append("## T4 — Grocery day-of-week")
    lines.append(f"- **Weekend/weekday ratio:** {ratio:.3f} (band 1.20-1.35) — {_band(ratio, 1.20, 1.35)}")
    lines.append("")

    # ----- T5 TBL late-night
    tbl = transactions[transactions["banner_code"]=="TBL"]
    h = pd.to_datetime(tbl["txn_ts"]).dt.hour
    late = ((h>=21)|(h<3)).mean()
    lines.append("## T5 — Taco Bell late-night daypart")
    lines.append(f"- **9pm+ share:** {late*100:.1f}% (band 17-21%, industry avg 4%) — {_band(late, 0.17, 0.21)}")
    lines.append("")

    # ----- T6 Pay-cycle
    g_txn["day"] = pd.to_datetime(g_txn["txn_ts"]).dt.day
    early = (g_txn["day"]<=10).sum()/10
    mid = ((g_txn["day"]>=15)&(g_txn["day"]<=17)).sum()/3
    flat = ((g_txn["day"]>=20)&(g_txn["day"]<=28)).sum()/9
    lines.append("## T6 — Pay-cycle lift")
    lines.append(f"- **Early-month (1-10):** {early:.0f}/day vs flat {flat:.0f}/day — lift {(early/flat-1)*100:.1f}%")
    lines.append(f"- **Mid-month (15-17):** {mid:.0f}/day vs flat {flat:.0f}/day — lift {(mid/flat-1)*100:.1f}%")
    lines.append("")

    # ----- T7-T9 population shape + loyalty
    lines.append("## T7-T8 — Population")
    lines.append(f"- **Total cards:** {n_cards:,}")
    by_card_seg = transactions.groupby("customer_token")["segment"].nunique()
    multi = (by_card_seg>=2).mean()
    all_three = (by_card_seg==3).mean()
    lines.append(f"- **Multi-merchant share:** {multi*100:.1f}% (band 25-35%) — {_band(multi, 0.25, 0.35)}")
    lines.append(f"- **All-three share:** {all_three*100:.1f}% (band 4-8%) — {_band(all_three, 0.04, 0.08)}")
    lines.append("")
    g_only = transactions[transactions["segment"]=="grocery"]
    by_card_banner = g_only.groupby("customer_token")["banner_code"]
    primary_share = by_card_banner.apply(lambda b: b.value_counts(normalize=True).max()).mean()
    lines.append("## T9 — Grocery loyalty concentration")
    lines.append(f"- **Pop-weighted primary-banner share:** {primary_share*100:.1f}% (band 70-78%) — {_band(primary_share, 0.70, 0.78)}")
    lines.append("")

    # ----- T11 affinity
    by_trip = items.groupby("txn_id")["subcategory"].apply(set)
    def _lift(a, p):
        ha = by_trip.apply(lambda s: a in s)
        hp = by_trip.apply(lambda s: p in s)
        pp = hp.mean()
        if pp == 0 or ha.sum() == 0:
            return float("nan"), float("nan")
        return hp[ha].mean()/pp, hp[ha].mean()
    pairs = [("PASTA","SAUCE",3.0),("CHIPS","SALSA",2.5),("DIAPERS","WIPES",3.0),("MILK","CEREAL",2.0)]
    lines.append("## T11 — Affinity lift (designed + emergent)")
    for a,p,thr in pairs:
        l, cond = _lift(a, p)
        lines.append(f"- **{a}→{p}:** lift {l:.2f}x (P(B|A) {cond:.3f}) — threshold ≥{thr}x {'✓' if l>=thr else '⚠'}")
    # Emergent
    flags = items.groupby("txn_id").apply(
        lambda x: ("DAIRY" in set(x["category"]), "CEREAL" in set(x["subcategory"])),
        include_groups=False,
    )
    fdf = pd.DataFrame(flags.tolist(), columns=["d","c"])
    p_c = fdf["c"].mean()
    p_cd = fdf[fdf["d"]]["c"].mean()
    lines.append(f"- **DAIRY→CEREAL (mission-emergent):** lift {p_cd/p_c:.2f}x — threshold ≥1.3x {'✓' if p_cd/p_c>=1.3 else '⚠'}")
    lines.append("")

    # ----- T12 heavy-tail
    g_items = items[items["txn_id"].isin(transactions[transactions["segment"]=="grocery"]["txn_id"])]
    u = g_items.groupby("txn_id")["qty"].sum().sort_values(ascending=False)
    top20 = u.iloc[:max(1, int(len(u)*0.2))].sum() / u.sum()
    lines.append("## T12 — Heavy-tail basket size")
    lines.append(f"- **Top-20% grocery basket unit share:** {top20*100:.1f}% (band 45-55%) — {_band(top20, 0.45, 0.55)}")
    lines.append("")

    # ----- T13 payment
    ct = (transactions["entry_mode"]=="contactless").mean()
    wt = transactions["wallet_at_tap"].mean()
    lines.append("## T13 — Payment mix")
    lines.append(f"- **Blended contactless:** {ct*100:.1f}% (band 48-55%) — {_band(ct, 0.48, 0.55)}")
    lines.append(f"- **Mobile wallet at-tap:** {wt*100:.1f}% (band 16-20%) — {_band(wt, 0.16, 0.20)}")
    g_txn2 = transactions[transactions["segment"]=="grocery"]
    wdx_d = (g_txn2[g_txn2["banner_code"]=="WDX"]["tender"]=="debit").mean()
    acm_d = (g_txn2[g_txn2["banner_code"]=="ACM"]["tender"]=="debit").mean()
    krg_d = (g_txn2[g_txn2["banner_code"]=="KRG"]["tender"]=="debit").mean()
    blended_d = (g_txn2["tender"]=="debit").mean()
    lines.append(f"- **Grocery debit (per-banner emergence):** WDX {wdx_d*100:.1f}% > KRG {krg_d*100:.1f}% ≈ ACM {acm_d*100:.1f}% (blended {blended_d*100:.1f}%)")
    lines.append("")

    # ----- T14 pricing
    from src.generate.engine.pricing import _effective_category_mult, _PL_FACTOR, _per_sku_competitive_index
    rows = []
    g_prod = products[products["segment"]=="grocery"]
    for r in g_prod.itertuples(index=False):
        cm = _effective_category_mult(r.banner_code, r.category, r.subcategory)
        plm = _PL_FACTOR.get(r.banner_code, 1.0) if r.private_label else 1.0
        ci = _per_sku_competitive_index(r.banner_code, r.canonical_id)
        rows.append({
            "banner_code": r.banner_code, "canonical_id": r.canonical_id,
            "rack_price": r.base_price * cm * plm * ci,
            "private_label": r.private_label, "subcategory": r.subcategory,
        })
    rack = pd.DataFrame(rows)
    cheap = rack.loc[rack.groupby("canonical_id")["rack_price"].idxmin()]
    shares = cheap["banner_code"].value_counts(normalize=True).to_dict()
    lines.append("## T14 — Pricing variation")
    lines.append(f"- **Cheapest banner share:** KRG {shares.get('KRG',0)*100:.1f}%, ACM {shares.get('ACM',0)*100:.1f}%, WDX {shares.get('WDX',0)*100:.1f}% (none >70%) — {'✓' if all(s<=0.70 for s in shares.values()) else '⚠'}")
    by_pair = rack.groupby(["banner_code","subcategory","private_label"])["rack_price"].mean().unstack("private_label").dropna()
    by_pair.columns = ["nb","pl"]
    pl_gap = (1 - by_pair["pl"]/by_pair["nb"]).mean()
    lines.append(f"- **PL gap blended:** {pl_gap*100:.1f}% (anchor ~25%) — {_band(pl_gap, 0.20, 0.30)}")
    lines.append("")

    # ----- T15 promos
    g_items_promo = items[items["txn_id"].isin(transactions[transactions["segment"]=="grocery"]["txn_id"])]
    promo_units = g_items_promo[g_items_promo["promo_id"].notna()]["qty"].sum()
    total_units = g_items_promo["qty"].sum()
    promo_share = promo_units / total_units if total_units else 0
    lines.append("## T15 — Promo behavior")
    lines.append(f"- **Grocery units on promo:** {promo_share*100:.1f}% (band 25-35%) — {_band(promo_share, 0.25, 0.35)}")
    lines.append("")

    # ----- T16 anomalies
    s2z = stores.set_index("store_id")["zone_id"].to_dict()
    txn_zones = np.array([s2z.get(s, "") for s in transactions["store_id"]])
    txn_dates = pd.to_datetime(transactions["txn_ts"]).dt.date.to_numpy()
    wdx_uc = (transactions["banner_code"]=="WDX").to_numpy() & np.isin(txn_zones, ["university_city","eastway"])
    a1_in = (txn_dates>=date(2026,4,19))&(txn_dates<=date(2026,5,29))
    a1_pre = txn_dates < date(2026,4,19)
    a1_during = (wdx_uc & a1_in).sum() / 41
    a1_before = (wdx_uc & a1_pre).sum() / 49
    a1_drop = 1 - a1_during/a1_before if a1_before>0 else 0
    lines.append("## T16 — Planted anomalies (A1-A3)")
    lines.append(f"- **A1 WDX UC+Eastway decline:** before {a1_before:.0f}/d, during {a1_during:.0f}/d — drop {a1_drop*100:.1f}% (target 40%)")
    lines.append(f"- **A2 ground truth:** {(anomalies['type']=='category_spike').sum()} entries")
    lines.append(f"- **A3 ground truth:** {(anomalies['type']=='share_shift').sum()} entries")
    lines.append(f"- **A1 ground truth:** {(anomalies['type']=='demand_decline').sum()} entries (zone × banner rows)")
    lines.append("")

    # ----- T17 cross-merchant cells
    all_three_cards = set(by_card_seg[by_card_seg==3].index)
    cz = customers[customers["card_id"].isin(all_three_cards)][["card_id","home_zone"]]
    by_zone = cz.groupby("home_zone").size().sort_values(ascending=False)
    if scale_frac >= 0.95:
        lines.append("## T17 — Cross-merchant cell readiness (FULL SCALE — Wave 2 gate)")
    else:
        lines.append(f"## T17 — Cross-merchant cell readiness ({scale_label})")
    n_above_k5 = int((by_zone >= 5).sum())
    n_zones_populated = int((by_zone >= 1).sum())
    for z, n in by_zone.items():
        flag = "" if n >= 5 else "  ⚠ <k=5"
        lines.append(f"- {z:<16}: {n:,} all-three cards{flag}")
    lines.append("")
    lines.append(f"**{n_above_k5}/8 zones** survive k=5 anonymity.  **{n_zones_populated}/8 zones** populated.")
    if scale_frac >= 0.95:
        if n_above_k5 == 8:
            lines.append("✓ Wave 2 lake grain (per-zone × all-three) holds as designed.")
        else:
            lines.append(f"⚠ Wave 2 must coarsen grain — {8 - n_above_k5} zone(s) below k=5 at full scale.")
    else:
        lines.append(f"Pilot projection: cells should multiply ~{1/scale_frac:.0f}× at full scale. Real gate requires full-scale measurement.")
    lines.append("")

    # ----- T18 reproducibility
    lines.append("## T18 — Reproducibility")
    lines.append("- **Verified content-identical at 500 cards** by the in-test reproducibility check (`test_T18_reproducibility_byte_or_content`).")
    if scale_frac >= 0.95:
        lines.append("- **Full-scale (100k) two-run hash diff: not performed.** Determinism at scale rests on construction: pinned pyarrow, single-threaded writes, sorted iteration, single-file (not chunked) writes, no parallelism. If you want a direct full-scale guarantee, rerun and compare via `scripts/hash_parquet.py`.")
    else:
        lines.append("- Full-scale (100k) two-run hash diff: not performed at this pilot scale.")
    lines.append("")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines))
    print(f"Wrote DQ report → {args.out.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
