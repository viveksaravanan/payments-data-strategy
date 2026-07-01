"""Pilot calibration harness (datamodel-v2, Phase 4/5).

Runs the behavioral pipeline (population → customers → trips → baskets)
at pilot scale, joins the committed catalog's shelf_price for dollars,
and measures the validation targets: per-store AUV, grocery/QSR dollar
splits, department sales mix, emergent PL share, affinity lift, and QSR
combo-attach. Also drives the A_s calibration loop by mutating
cfg.merchants['attractiveness'] in memory between rebuilds (no YAML I/O
per iteration — the converged values are written back to config by hand).

Usage: python scripts/calibrate_pilot.py [--scale N] [--calibrate]
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from src.generate.config.loader import load_config
from src.generate.engine.baskets import build_basket_items
from src.generate.engine.catalog import load_products
from src.generate.engine.customers import build_customers
from src.generate.engine.geography import build_stores, build_zones
from src.generate.engine.population import build_population
from src.generate.engine.trips import build_trips

CONFIG_ROOT = Path(__file__).resolve().parents[1] / "src" / "generate" / "config"

# Validation targets.
AUV_TARGET = {"KRG": 45e6, "ACM": 32e6, "WDX": 22e6,
              "CFA": 8.0e6, "TBL": 2.1e6, "BKG": 1.6e6}
GROCERY_SPLIT_TARGET = {"KRG": 0.52, "ACM": 0.31, "WDX": 0.17}
QSR_DOLLAR_SPLIT_TARGET = {"CFA": 0.60, "TBL": 0.24, "BKG": 0.16}


def run_pipeline(cfg, scale, catalog):
    seed = int(cfg.global_["seed"])
    zones = build_zones(cfg)
    stores = build_stores(cfg, np.random.default_rng(seed))
    pop = build_population(cfg, np.random.default_rng(seed), n_cards=scale)
    cust = build_customers(cfg, pop, np.random.default_rng(seed + 1))
    trips = build_trips(cfg, pop, cust, stores, zones, np.random.default_rng(seed + 2))
    baskets = build_basket_items(cfg, trips, cust, catalog, np.random.default_rng(seed + 3))
    # Join shelf_price + taxonomy; attach banner/store/date from trips.
    cat = catalog.set_index("sku")
    lines = baskets.join(
        cat[["banner_code", "segment", "shelf_price", "private_label",
             "functional_department", "functional_category",
             "functional_subcategory"]], on="sku")
    lines["line_total"] = lines["shelf_price"] * lines["qty"]
    lines = lines.merge(trips[["trip_id", "store_id", "txn_ts"]], on="trip_id")
    return dict(cust=cust, trips=trips, baskets=baskets, lines=lines,
                stores=stores, n_cards=len(pop))


def measure(state, scale):
    lines, trips = state["lines"], state["trips"]
    scale_up = (155000 / state["n_cards"]) * (365 / 90)   # pilot→full-year
    g = lines[lines.segment == "grocery"]
    q = lines[lines.segment == "qsr"]
    out = {}
    # Per-store AUV (annualized, full population).
    auv = {}
    for seg in (g, q):
        st = seg.groupby("store_id")["line_total"].sum()
        # map store→banner via trips
        pass
    store_banner = trips.drop_duplicates("store_id").set_index("store_id")["banner_code"]
    for seg_lines in (g, q):
        s = seg_lines.groupby("store_id")["line_total"].sum() * scale_up
        for store_id, dollars in s.items():
            b = store_banner.get(store_id)
            auv.setdefault(b, []).append(dollars)
    out["auv"] = {b: np.mean(v) for b, v in auv.items()}
    # Dollar splits.
    gd = g.groupby("banner_code")["line_total"].sum()
    out["grocery_split"] = (gd / gd.sum()).to_dict()
    qd = q.groupby("banner_code")["line_total"].sum()
    out["qsr_dollar_split"] = (qd / qd.sum()).to_dict()
    out["qsr_dollars"] = qd.to_dict()
    out["cfa_vs_others"] = qd.get("CFA", 0) / (qd.get("TBL", 0) + qd.get("BKG", 0) + 1e-9)
    # Department sales mix (grocery $).
    dm = g.groupby("functional_department")["line_total"].sum()
    out["dept_mix"] = (dm / dm.sum()).sort_values(ascending=False).to_dict()
    # PL share (grocery, by banner) — emergent from selection.
    pl = g.groupby("banner_code").apply(
        lambda d: d.loc[d.private_label, "line_total"].sum() / d["line_total"].sum(),
        include_groups=False)
    out["pl_share"] = pl.to_dict()
    # Basket AOV.
    tot = lines.groupby("trip_id")["line_total"].sum()
    tg = g.groupby("trip_id")["line_total"].sum()
    tq = q.groupby("trip_id")["line_total"].sum()
    out["grocery_aov"] = tg.mean()
    out["qsr_check"] = tq.mean()
    out["qsr_check_by_banner"] = q.groupby("banner_code").apply(
        lambda d: d.groupby("trip_id")["line_total"].sum().mean(),
        include_groups=False).to_dict()
    # Totals (annualized full-pop).
    out["grocery_year"] = g["line_total"].sum() * scale_up
    out["qsr_year"] = q["line_total"].sum() * scale_up
    return out


def affinity_lift(state):
    """P(partner in basket | anchor) / P(partner) for key pairs."""
    lines = state["lines"]
    g = lines[lines.segment == "grocery"]
    bybasket = g.groupby("trip_id")["functional_subcategory"].agg(set)
    n = len(bybasket)
    def lift(anchor, partner):
        has_anchor = bybasket.apply(lambda s: anchor in s)
        p_partner = bybasket.apply(lambda s: partner in s).mean()
        if has_anchor.sum() == 0 or p_partner == 0:
            return float("nan")
        p_cond = bybasket[has_anchor].apply(lambda s: partner in s).mean()
        return p_cond / p_partner
    pairs = [("Pasta", "Pasta Sauce"), ("Potato & Tortilla Chips", "Salsa & Dips"),
             ("Cereal", "2% Reduced-Fat Milk"), ("Sandwich Bread", "Butter")]
    return {f"{a}->{b}": round(lift(a, b), 2) for a, b in pairs}


def combo_attach(state):
    """QSR: P(beverage | entrée) and P(side | entrée) by chain."""
    q = state["lines"][state["lines"].segment == "qsr"]
    bb = q.groupby("trip_id").agg(
        cats=("functional_category", set), banner=("banner_code", "first"))
    out = {}
    for banner in ("CFA", "TBL", "BKG"):
        sub = bb[bb.banner == banner]
        ent = sub[sub.cats.apply(lambda s: "Entrée" in s)]
        if len(ent) == 0:
            out[banner] = {}
            continue
        out[banner] = {
            "bev|entrée": round(ent.cats.apply(lambda s: "Beverages" in s).mean(), 2),
            "side|entrée": round(ent.cats.apply(lambda s: "Sides" in s).mean(), 2),
            "n_entrée_trips": len(ent),
        }
    return out


def report(m, state):
    print("\n=== AUV (annualized, full-pop) vs target ===")
    for b in ("KRG", "ACM", "WDX", "CFA", "TBL", "BKG"):
        got = m["auv"].get(b, 0); tgt = AUV_TARGET[b]
        print(f"  {b}: ${got/1e6:6.1f}M  (target ${tgt/1e6:.1f}M, {got/tgt:.2f}x)")
    print("=== grocery dollar split (target 52/31/17) ===")
    print("  " + ", ".join(f"{b} {m['grocery_split'].get(b,0)*100:.1f}%"
                            for b in ("KRG", "ACM", "WDX")))
    print("=== QSR dollar split (target CFA60/TB24/BK16) ===")
    print("  " + ", ".join(f"{b} {m['qsr_dollar_split'].get(b,0)*100:.1f}%"
                            for b in ("CFA", "TBL", "BKG")))
    print(f"  CFA vs TB+BK: {m['cfa_vs_others']:.2f}x (target >1.0)")
    print("=== department $ mix (grocery) ===")
    for d, s in m["dept_mix"].items():
        print(f"  {d}: {s*100:.1f}%")
    print("=== PL share by banner (emergent; target KRG27/ACM19/WDX25) ===")
    print("  " + ", ".join(f"{b} {m['pl_share'].get(b,0)*100:.1f}%"
                            for b in ("KRG", "ACM", "WDX")))
    print(f"=== AOV: grocery ${m['grocery_aov']:.2f} (~$50), qsr check ${m['qsr_check']:.2f} ===")
    print("  qsr check by banner:", {k: round(v, 2) for k, v in m["qsr_check_by_banner"].items()})
    print(f"=== totals (annualized): grocery ${m['grocery_year']/1e6:.0f}M (~$518M), "
          f"qsr ${m['qsr_year']/1e6:.0f}M (~$80M) ===")
    print("=== grocery affinity lift (target >= ~2-3x) ===")
    print("  ", affinity_lift(state))
    print("=== QSR combo-attach ===")
    for b, v in combo_attach(state).items():
        print(f"  {b}: {v}")


def calibrate_a_s(cfg, scale, catalog, rounds=6):
    """Iteratively adjust A_s toward the dollar-split targets."""
    for r in range(rounds):
        state = run_pipeline(cfg, scale, catalog)
        m = measure(state, scale)
        gs, qs = m["grocery_split"], m["qsr_dollar_split"]
        # Damped proportional update.
        damp = 0.6
        for name, mm in cfg.merchants.items():
            b = mm["banner_code"]
            if b in GROCERY_SPLIT_TARGET:
                tgt, got = GROCERY_SPLIT_TARGET[b], gs.get(b, 1e-6)
            elif b in QSR_DOLLAR_SPLIT_TARGET:
                tgt, got = QSR_DOLLAR_SPLIT_TARGET[b], qs.get(b, 1e-6)
            else:
                continue
            factor = (tgt / max(got, 1e-6)) ** damp
            mm["attractiveness"] = round(mm["attractiveness"] * factor, 3)
        cur_a = {mm["banner_code"]: mm["attractiveness"] for mm in cfg.merchants.values()}
        print(f"round {r}: grocery {[round(gs.get(b,0),3) for b in ('KRG','ACM','WDX')]} "
              f"qsr {[round(qs.get(b,0),3) for b in ('CFA','TBL','BKG')]} -> A_s {cur_a}")
    return cfg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", type=int, default=8000)
    ap.add_argument("--calibrate", action="store_true")
    args = ap.parse_args()
    cfg = load_config(CONFIG_ROOT)
    catalog = load_products()
    if args.calibrate:
        cfg = calibrate_a_s(cfg, args.scale, catalog)
        print("\nFinal A_s:", {mm["banner_code"]: mm["attractiveness"]
                                for mm in cfg.merchants.values()})
    state = run_pipeline(cfg, args.scale, catalog)
    m = measure(state, args.scale)
    report(m, state)


if __name__ == "__main__":
    main()
