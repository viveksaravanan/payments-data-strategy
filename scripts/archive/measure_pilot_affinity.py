"""One-shot pilot affinity report for T11 magnitudes.

Builds the full pilot chain (5k cards → ~85k trips → ~470k items)
then prints, per pair:
  - base co-occurrence rate P(partner ∈ basket)
  - conditional P(partner ∈ basket | anchor ∈ basket)
  - lift = conditional / base
  - support (number of baskets containing the anchor)

Run: ``uv run python scripts/measure_pilot_affinity.py``
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from src.generate.config.loader import load_config
from src.generate.engine.baskets import build_basket_items
from src.generate.engine.catalog import build_catalog
from src.generate.engine.customers import build_customers
from src.generate.engine.geography import build_stores, build_zones
from src.generate.engine.population import build_population
from src.generate.engine.trips import build_trips

PILOT_CARDS = 5_000


def lift(items: pd.DataFrame, anchor_subcat: str, partner_subcat: str) -> dict:
    by_trip = items.groupby("trip_id")["subcategory"].apply(set)
    n_total = len(by_trip)
    has_anchor = by_trip.apply(lambda s: anchor_subcat in s)
    has_partner = by_trip.apply(lambda s: partner_subcat in s)
    n_anchor = int(has_anchor.sum())
    p_partner_unconditional = float(has_partner.mean())
    if n_anchor == 0 or p_partner_unconditional == 0:
        return {
            "anchor": anchor_subcat, "partner": partner_subcat,
            "n_baskets": n_total, "support_anchor": n_anchor,
            "p_partner": p_partner_unconditional,
            "p_partner_given_anchor": float("nan"),
            "lift": float("nan"),
        }
    p_partner_given_anchor = float(has_partner[has_anchor].mean())
    return {
        "anchor": anchor_subcat, "partner": partner_subcat,
        "n_baskets": n_total, "support_anchor": n_anchor,
        "p_partner": p_partner_unconditional,
        "p_partner_given_anchor": p_partner_given_anchor,
        "lift": p_partner_given_anchor / p_partner_unconditional,
    }


def category_lift(items: pd.DataFrame, anchor_cat: str, partner_subcat: str) -> dict:
    """For the mission-emergent check: anchor at category level, partner
    at subcategory level."""
    by_trip = items.groupby("trip_id").apply(
        lambda x: (set(x["category"]), set(x["subcategory"])),
        include_groups=False,
    )
    has_anchor = by_trip.apply(lambda t: anchor_cat in t[0])
    has_partner = by_trip.apply(lambda t: partner_subcat in t[1])
    n_anchor = int(has_anchor.sum())
    p_partner_unconditional = float(has_partner.mean())
    if n_anchor == 0 or p_partner_unconditional == 0:
        return {
            "anchor": anchor_cat, "partner": partner_subcat,
            "n_baskets": len(by_trip), "support_anchor": n_anchor,
            "p_partner": p_partner_unconditional,
            "p_partner_given_anchor": float("nan"),
            "lift": float("nan"),
        }
    p_partner_given_anchor = float(has_partner[has_anchor].mean())
    return {
        "anchor": anchor_cat, "partner": partner_subcat,
        "n_baskets": len(by_trip), "support_anchor": n_anchor,
        "p_partner": p_partner_unconditional,
        "p_partner_given_anchor": p_partner_given_anchor,
        "lift": p_partner_given_anchor / p_partner_unconditional,
    }


def main() -> None:
    cfg = load_config(Path(__file__).resolve().parents[1] / "src" / "generate" / "config")
    print(f"[pilot] building chain at {PILOT_CARDS:,} cards…")

    rng_stores = np.random.default_rng(cfg.global_["seed"])
    stores = build_stores(cfg, rng_stores)
    zones = build_zones(cfg)

    rng_catalog = np.random.default_rng(cfg.global_["seed"] + 10)
    catalog = build_catalog(cfg, rng_catalog)

    rng_pop = np.random.default_rng(cfg.global_["seed"])
    population = build_population(cfg, rng_pop, n_cards=PILOT_CARDS)

    rng_cust = np.random.default_rng(cfg.global_["seed"] + 1)
    customers = build_customers(cfg, population, rng_cust)

    rng_trips = np.random.default_rng(cfg.global_["seed"] + 2)
    trips = build_trips(cfg, population, customers, stores, zones, rng_trips)
    print(f"[pilot] trips={len(trips):,}")

    rng_items = np.random.default_rng(cfg.global_["seed"] + 3)
    items = build_basket_items(cfg, trips, customers, catalog, rng_items)
    print(f"[pilot] line items={len(items):,}\n")

    # T11 designed pairs with their boost factors.
    designed = [
        ("PASTA",   "SAUCE",   3.5),
        ("SAUCE",   "PASTA",   3.5),
        ("CHIPS",   "SALSA",   3.0),
        ("SALSA",   "CHIPS",   3.0),
        ("DIAPERS", "WIPES",   3.0),
        ("WIPES",   "DIAPERS", 3.0),
        ("DIAPERS", "FORMULA", 2.2),
        ("MILK",    "CEREAL",  2.5),
        ("CEREAL",  "MILK",    2.5),
        ("BREAD",   "BUTTER",  2.0),
    ]

    print("T11 — designed subcategory affinity pairs")
    print(f"{'anchor':>9} → {'partner':<9}  {'boost':>6}  "
          f"{'P(B)':>8}  {'P(B|A)':>8}  {'lift':>7}  {'support_A':>10}")
    print("-" * 80)
    for anchor, partner, boost in designed:
        r = lift(items, anchor, partner)
        print(
            f"{r['anchor']:>9} → {r['partner']:<9}  "
            f"{boost:>5.1f}x  {r['p_partner']:>8.4f}  "
            f"{r['p_partner_given_anchor']:>8.4f}  "
            f"{r['lift']:>6.2f}x  {r['support_anchor']:>10,}"
        )

    print("\nMission-emergent (no explicit pair in affinity matrix)")
    print(f"{'anchor':>9} → {'partner':<9}  {'boost':>6}  "
          f"{'P(B)':>8}  {'P(B|A)':>8}  {'lift':>7}  {'support_A':>10}")
    print("-" * 80)
    emergent = [
        ("DAIRY",  "CEREAL"),       # breakfast mission
        ("MEAT",   "SAUCE"),        # meal_tonight
        ("HOUSEHOLD", "TOOTHPASTE"),# household_cleaning + personal
        ("BAKERY", "BUTTER"),       # general affinity (also explicit)
    ]
    for anchor_cat, partner_subcat in emergent:
        r = category_lift(items, anchor_cat, partner_subcat)
        print(
            f"{r['anchor']:>9} → {r['partner']:<9}  "
            f"{'—':>6}  {r['p_partner']:>8.4f}  "
            f"{r['p_partner_given_anchor']:>8.4f}  "
            f"{r['lift']:>6.2f}x  {r['support_anchor']:>10,}"
        )


if __name__ == "__main__":
    main()
