"""Layer 5 — Baskets & affinity (D17).

Per trip: pick a mission (D17.1), pick an archetype (mission-bound +
pay-cycle nudged per D17.4 + D15.3), sample basket size (triangular
per archetype), then fill the basket from a mission category
distribution intersected with customer preference (staples / D17.3)
and modulated by the complementary-affinity matrix (D17.2).

T11 (the "discoverable via lift" test) is the design gate for this
layer: the designed affinity pairs (pasta↔sauce, chips↔salsa,
diapers↔wipes, milk↔cereal, etc.) plus mission-driven category
co-occurrence must produce SKU-level lift detectable by an analyst,
without the analyst being told the rules. T12 (basket heavy-tail)
falls out of the archetype size distribution × pay-cycle weighting.

Catalog SKU list (per merchant, with category/subcategory/PL flag/
canonical_id) lives in ``engine/catalog.py`` because baskets need
to know what SKUs exist before placing them; pricing of those SKUs
(D19) stays at Stage 4.7.
"""
from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd

from src.generate.config.loader import Config, ConfigInvariantError


# ----- D17.1 missions: per-segment mission menu --------------------
# Each mission has a category distribution (weights summing to ~1.0)
# and a bound archetype that determines basket size.

_GROCERY_MISSIONS: dict[str, dict] = {
    "weekly_stockup": {
        "categories": {
            "DAIRY": 0.14, "PRODUCE": 0.14, "MEAT": 0.10, "PANTRY": 0.13,
            "BAKERY": 0.08, "FROZEN": 0.10, "BEVERAGES": 0.08, "SNACKS": 0.08,
            "HOUSEHOLD": 0.06, "PERSONAL": 0.03, "BABY": 0.03, "PET": 0.03,
        },
        "archetype": "stockup",
        "base_share": 0.15,
    },
    "meal_tonight": {
        "categories": {
            "MEAT": 0.30, "PRODUCE": 0.22, "PANTRY": 0.20, "DAIRY": 0.10,
            "BAKERY": 0.08, "FROZEN": 0.05, "BEVERAGES": 0.05,
        },
        "archetype": "fill_in",
        "base_share": 0.25,
    },
    "breakfast_staples": {
        "categories": {
            "DAIRY": 0.32, "BAKERY": 0.18, "PANTRY": 0.25,  # cereal/coffee
            "FROZEN": 0.05, "PRODUCE": 0.10, "BEVERAGES": 0.10,
        },
        "archetype": "fill_in",
        "base_share": 0.15,
    },
    "snacks_beverages": {
        "categories": {
            "SNACKS": 0.45, "BEVERAGES": 0.35, "FROZEN": 0.10, "PANTRY": 0.10,
        },
        "archetype": "quick",
        "base_share": 0.15,
    },
    "household_cleaning": {
        "categories": {
            "HOUSEHOLD": 0.55, "PERSONAL": 0.25, "PANTRY": 0.10,
            "BABY": 0.05, "PET": 0.05,
        },
        "archetype": "fill_in",
        "base_share": 0.10,
    },
    "baby_pet": {
        "categories": {
            "BABY": 0.40, "PET": 0.30, "HOUSEHOLD": 0.10, "DAIRY": 0.10,
            "PANTRY": 0.10,
        },
        "archetype": "fill_in",
        "base_share": 0.10,
    },
    "occasion_bbq": {
        "categories": {
            "MEAT": 0.28, "SNACKS": 0.20, "BEVERAGES": 0.20, "PRODUCE": 0.15,
            "BAKERY": 0.10, "PANTRY": 0.07,
        },
        "archetype": "stockup",
        "base_share": 0.10,
    },
}

_QSR_MISSIONS: dict[str, dict] = {
    "combo_meal": {
        "categories": {"COMBO": 0.45, "TACO": 0.15, "BURR": 0.15,
                       "DRINK": 0.15, "SIDE": 0.10},
        "archetype": "combo",
        "base_share": 0.40,
    },
    "snack_run": {
        "categories": {"SIDE": 0.45, "DRINK": 0.30, "TACO": 0.25},
        "archetype": "quick",
        "base_share": 0.30,
    },
    "group_order": {
        "categories": {"TACO": 0.25, "BURR": 0.22, "COMBO": 0.20,
                       "SIDE": 0.18, "DRINK": 0.15},
        "archetype": "stockup",
        "base_share": 0.10,
    },
    "breakfast": {
        "categories": {"BFAST": 0.55, "DRINK": 0.30, "SIDE": 0.15},
        "archetype": "fill_in",
        "base_share": 0.20,
    },
}

_OFF_PRICE_MISSIONS: dict[str, dict] = {
    "apparel_refresh": {
        "categories": {"WOM": 0.40, "MEN": 0.20, "KID": 0.15, "SHO": 0.10, "ACC": 0.15},
        "archetype": "browse",
        "base_share": 0.50,
    },
    "home_goods": {
        "categories": {"HOM": 0.70, "ACC": 0.15, "BTY": 0.15},
        "archetype": "focused",
        "base_share": 0.30,
    },
    "gifting": {
        "categories": {"ACC": 0.30, "BTY": 0.25, "HOM": 0.20, "WOM": 0.15, "KID": 0.10},
        "archetype": "focused",
        "base_share": 0.20,
    },
}

_SEGMENT_MISSIONS = {
    "grocery":   _GROCERY_MISSIONS,
    "qsr":       _QSR_MISSIONS,
    "off_price": _OFF_PRICE_MISSIONS,
}


# ----- D17.4 archetype size distributions ---------------------------

# Archetype sizes tuned at Stage 4.7 for AOV reconciliation. D17.4
# ratified grocery ranges: stockup 15-30, fill_in 5-8, quick 2-3.
# Stockup tightened to (15,17,22) at the low end of its range to
# land its AOV on the $110 D17.4 anchor (measured: $115.82).
# Fill_in tightened to (4,5,7) at the low end of its range —
# blended AOV diagnostic showed fill_in running 70% over its
# D17.4 $28 anchor because the mission mix includes baby_pet +
# household_cleaning trips (high-anchor BABY/PET/HOUSEHOLD items
# at real-world prices). D17.4's $28 implicitly assumed a narrower
# mission mix; tightening fill_in size compensates without
# perturbing real-world per-line prices. Lo=4 sits 1 below D17.4's
# stated 5; flagged for a future D17 clarification but stays close
# to spirit ("small fill-in trip with a couple of items").
# QSR combo archetype dropped from (3,4,5) → (1,2,3): a single
# "COMBO" SKU in the catalog represents the whole bundled meal
# (entree + side + drink), not a constituent line, so a combo trip
# is usually 1 combo + maybe a drink upgrade — basket size 1-3.
_GROCERY_ARCHETYPES = {
    "stockup": (15, 17, 22),
    "fill_in": (4, 5, 7),
    "quick":   (2, 2, 3),
}
_QSR_ARCHETYPES = {
    "combo":   (1, 2, 3),
    "quick":   (1, 2, 3),
    "fill_in": (1, 2, 3),
    "stockup": (4, 6, 9),
}
_OFF_PRICE_ARCHETYPES = {
    "browse":  (2, 4, 8),
    "focused": (1, 2, 4),
}

_SEGMENT_ARCHETYPES = {
    "grocery":   _GROCERY_ARCHETYPES,
    "qsr":       _QSR_ARCHETYPES,
    "off_price": _OFF_PRICE_ARCHETYPES,
}


# D17.2 designed affinity pairs are now config-driven — each segment's
# ``affinity_matrix`` in ``config/segments/{segment}.yaml`` lists
# [anchor_subcat, partner_subcat, boost]. See ``_build_affinity_lookup``.


# Default staple count by segment (D16.2 / D17.3).
_STAPLES_PER_CARD = {
    "grocery":   5,
    "qsr":       2,
    "off_price": 0,    # treasure-hunt; no staples
}
# Per-trip per-staple inclusion probability.
_STAPLE_INCLUDE_PROB = 0.45


# Pay-cycle mission re-weighting (D17.4 × D15.3).
_PAY_CYCLE_MISSION_WEIGHTS = {
    "early": {     # days 1-10
        "weekly_stockup": 2.0,
        "occasion_bbq":   1.5,
        "baby_pet":       1.2,
    },
    "mid": {       # days 15-17
        "weekly_stockup": 1.3,
        "meal_tonight":   1.2,
    },
    "late": {      # other days
        "meal_tonight":     1.15,
        "snacks_beverages": 1.1,
        "breakfast_staples": 1.05,
    },
}


def _pay_cycle_bucket(day_of_month: int) -> str:
    if 1 <= day_of_month <= 10:
        return "early"
    if day_of_month in (15, 16, 17):
        return "mid"
    return "late"


# Per-line quantity by category. Tightened at Stage 4.7 — initial
# distributions inherited from v3 baseline had means ~1.6 per line,
# inflating AOV by ~60% across all segments (mostly an artifact —
# real basket lines are predominantly qty=1; multi-buy is the
# exception). Tuned to mean ~1.15-1.25 for typical items, qty=1
# strongly dominant for clothing and QSR fast food.
_QTY_DISTRIBUTION_DEFAULT = {1: 0.85, 2: 0.12, 3: 0.025, 4: 0.005}
_QTY_DISTRIBUTION_BY_CATEGORY = {
    # Grocery — produce + beverages have *some* multi-buy (a bag of
    # apples + a bunch of bananas + a head of lettuce per produce
    # category; a 12-pack of soda + a case of water per beverages),
    # but mean qty for these used to run 1.78 / 1.55 in v3 baseline
    # which inflated AOV well above D17.4's $55 anchor. Tightened
    # at Stage 4.7 to means ~1.40 / 1.29.
    "PRODUCE":   {1: 0.70, 2: 0.20, 3: 0.07, 4: 0.025, 5: 0.005},
    "DAIRY":     {1: 0.80, 2: 0.16, 3: 0.04},
    "BEVERAGES": {1: 0.78, 2: 0.16, 3: 0.05, 4: 0.01},
    "BAKERY":    {1: 0.80, 2: 0.15, 3: 0.05},
    "PANTRY":    {1: 0.78, 2: 0.18, 3: 0.04},
    "SNACKS":    {1: 0.75, 2: 0.18, 3: 0.05, 4: 0.02},
    "MEAT":      {1: 0.78, 2: 0.18, 3: 0.04},
    "FROZEN":    {1: 0.80, 2: 0.15, 3: 0.05},
    # QSR — almost always one of each item; tacos can stack
    "TACO":      {1: 0.55, 2: 0.25, 3: 0.15, 4: 0.05},
    "BURR":      {1: 0.80, 2: 0.15, 3: 0.05},
    "DRINK":     {1: 0.88, 2: 0.10, 3: 0.02},
    "COMBO":     {1: 0.92, 2: 0.07, 3: 0.01},
    "SIDE":      {1: 0.78, 2: 0.18, 3: 0.04},
    "BFAST":     {1: 0.85, 2: 0.12, 3: 0.03},
    # Off-price — clothing / home items overwhelmingly qty=1
    "WOM":       {1: 0.90, 2: 0.08, 3: 0.02},
    "MEN":       {1: 0.92, 2: 0.06, 3: 0.02},
    "KID":       {1: 0.85, 2: 0.12, 3: 0.03},
    "HOM":       {1: 0.88, 2: 0.10, 3: 0.02},
    "ACC":       {1: 0.92, 2: 0.07, 3: 0.01},
    "SHO":       {1: 0.95, 2: 0.04, 3: 0.01},
    "BTY":       {1: 0.85, 2: 0.12, 3: 0.03},
}


def _sample_qty(rng: np.random.Generator, category: str) -> int:
    table = _QTY_DISTRIBUTION_BY_CATEGORY.get(category, _QTY_DISTRIBUTION_DEFAULT)
    qty_vals = list(table.keys())
    qty_probs = np.array(list(table.values()), dtype=float)
    qty_probs = qty_probs / qty_probs.sum()
    return int(rng.choice(qty_vals, p=qty_probs))


# ----- Staples ------------------------------------------------------

def _derive_staples_for_card(
    card_id: str,
    segment: str,
    catalog: pd.DataFrame,
) -> list[str]:
    """Pick a stable set of staple SKUs for a card from the segment's
    catalog. Deterministic from ``(card_id, segment)`` via a SHA-256
    hash → seed → numpy choice. Independent of the main RNG stream so
    staple draws don't perturb trip placement reproducibility."""
    k = _STAPLES_PER_CARD[segment]
    if k == 0:
        return []
    pool = catalog.loc[catalog["segment"] == segment, "sku"].to_numpy()
    if len(pool) == 0:
        return []
    h = hashlib.sha256(f"staples|{card_id}|{segment}".encode()).digest()
    seed = int.from_bytes(h[:8], "big")
    local = np.random.default_rng(seed)
    return local.choice(pool, size=min(k, len(pool)), replace=False).tolist()


# ----- Per-segment per-merchant SKU pool -----------------------------

def _build_sku_pool(catalog: pd.DataFrame) -> dict[tuple[str, str], pd.DataFrame]:
    """Index catalog by (banner_code, category) for fast within-category
    SKU sampling. Returned mapping points each (banner, category) to a
    DataFrame with columns sku + subcategory + canonical_id."""
    out: dict[tuple[str, str], pd.DataFrame] = {}
    cols = ["sku", "subcategory", "canonical_id"]
    for (banner, cat), grp in catalog.groupby(["banner_code", "category"]):
        out[(banner, cat)] = grp[cols].reset_index(drop=True)
    return out


def _build_affinity_lookup(cfg: Config) -> dict[str, list[tuple[str, float]]]:
    """{anchor_subcat: [(partner_subcat, boost), ...]} sourced from each
    segment's ``affinity_matrix`` (D17.2, config-driven).

    Segments are walked in sorted name order and entries in declared list
    order, so the lookup — and therefore the basket draw sequence — is
    deterministic. The loader (``ConfigInvariantError``) has already
    checked each entry is well-formed with a positive boost.
    """
    out: dict[str, list[tuple[str, float]]] = {}
    for seg_name in sorted(cfg.segments):
        for anchor, partner, boost in cfg.segments[seg_name].get("affinity_matrix") or []:
            out.setdefault(str(anchor), []).append((str(partner), float(boost)))
    return out


def _assert_affinity_subcats_exist(
    affinity: dict[str, list[tuple[str, float]]],
    catalog: pd.DataFrame,
) -> None:
    """Fail loudly if an ``affinity_matrix`` entry names a subcategory the
    catalog doesn't contain. A typo'd subcat would otherwise silently
    no-op — the boost would never fire — and quietly erode the T11
    cross-sell lift the matrix exists to plant."""
    valid = set(catalog["subcategory"].unique())
    referenced = set(affinity) | {
        partner for partners in affinity.values() for partner, _ in partners
    }
    missing = sorted(referenced - valid)
    if missing:
        raise ConfigInvariantError(
            f"D17.2: affinity_matrix references subcategories absent from "
            f"the catalog: {missing}"
        )


# ----- mission + archetype + size -----------------------------------

def _pick_mission(
    rng: np.random.Generator,
    segment: str,
    pay_cycle_bucket: str,
) -> str:
    missions = _SEGMENT_MISSIONS[segment]
    names = list(missions.keys())
    base = np.array([missions[m]["base_share"] for m in names], dtype=float)
    if segment == "grocery":
        adj = _PAY_CYCLE_MISSION_WEIGHTS.get(pay_cycle_bucket, {})
        weights = np.array(
            [base[i] * adj.get(names[i], 1.0) for i in range(len(names))],
            dtype=float,
        )
    else:
        weights = base
    weights = weights / weights.sum()
    return str(rng.choice(names, p=weights))


def _sample_basket_size(
    rng: np.random.Generator,
    segment: str,
    archetype: str,
) -> int:
    lo, mu, hi = _SEGMENT_ARCHETYPES[segment][archetype]
    draw = rng.triangular(lo, mu, hi)
    return int(np.clip(round(draw), lo, hi))


# ----- main builder -------------------------------------------------

def build_basket_items(
    cfg: Config,
    trips: pd.DataFrame,
    customers: pd.DataFrame,
    catalog: pd.DataFrame,
    rng: np.random.Generator,
    *,
    promo_depth_lookup: dict | None = None,
    a2_boost_lookup: dict | None = None,
    a3_basket_mult_lookup: dict | None = None,
    lift_coefficient: float = 6.0,
) -> pd.DataFrame:
    """Per-trip basket items.

    Returns one row per line item with columns:
    ``[trip_id, line_id, sku, canonical_id, category, subcategory,
       qty]``.

    Event hooks (Stage 4.8 — D20):
    - ``promo_depth_lookup`` {(date, sku) → depth}: boost SKU draw
      weight by (1 + lift_coefficient × depth) when on promo. Drives
      T15 demand lift.
    - ``a2_boost_lookup`` {(date, store_id, category) → mult}: boost
      category weight at one store during the window (A2 spike).
    - ``a3_basket_mult_lookup`` {(date, banner) → mult}: multiplier
      on PASTA subcategory weight during A3 pasta-promo windows.
    """
    promo_depth_lookup = promo_depth_lookup or {}
    a2_boost_lookup = a2_boost_lookup or {}
    a3_basket_mult_lookup = a3_basket_mult_lookup or {}
    sku_pool = _build_sku_pool(catalog)
    affinity = _build_affinity_lookup(cfg)
    _assert_affinity_subcats_exist(affinity, catalog)
    cust_idx = customers.set_index("card_id")

    # Build per-card staples per segment (deterministic, independent
    # RNG stream so staples don't perturb main draw order).
    staple_cache: dict[tuple[str, str], list[str]] = {}

    # SKU → metadata for quick lookup when expanding staples.
    sku_meta = catalog.set_index("sku")[["category", "subcategory", "canonical_id"]]

    records: list[tuple] = []

    # Iterate trips in trip_id order (already sorted by build_trips).
    have_events = bool(
        promo_depth_lookup or a2_boost_lookup or a3_basket_mult_lookup
    )
    trips_iter = trips.itertuples(index=False)
    for tr in trips_iter:
        trip_id = tr.trip_id
        card_id = tr.card_id
        segment = tr.segment
        banner  = tr.banner_code
        store_id = str(tr.store_id)
        txn_ts  = tr.txn_ts

        # Pay-cycle bucket from trip date.
        ts = pd.Timestamp(txn_ts)
        dom = ts.day
        trip_date = ts.date()
        bucket = _pay_cycle_bucket(dom)
        mission_name = _pick_mission(rng, segment, bucket)
        mission = _SEGMENT_MISSIONS[segment][mission_name]
        archetype = mission["archetype"]
        size = _sample_basket_size(rng, segment, archetype)

        # Per-trip A3 pasta basket multiplier (1.0 = neutral).
        a3_pasta_mult = (
            float(a3_basket_mult_lookup.get((trip_date, banner), 1.0))
            if a3_basket_mult_lookup else 1.0
        )

        # Resolve staples for this card+segment.
        staple_key = (card_id, segment)
        if staple_key not in staple_cache:
            staple_cache[staple_key] = _derive_staples_for_card(
                card_id, segment, catalog,
            )
        staples = staple_cache[staple_key]

        # Build basket: track subcategories already in basket for affinity.
        chosen_skus: set[str] = set()
        chosen_subcats: set[str] = set()
        basket_lines: list[tuple[str, str, str, str]] = []
        # tuple: (sku, category, subcategory, canonical_id)

        # 1) Include each staple with probability — if its banner is this
        # trip's banner (we keep staples banner-specific because cards
        # shop across multiple banners in grocery).
        for sku in staples:
            if not sku.startswith(banner + "-"):
                continue
            if rng.uniform() < _STAPLE_INCLUDE_PROB and len(basket_lines) < size:
                meta = sku_meta.loc[sku]
                if sku not in chosen_skus:
                    chosen_skus.add(sku)
                    chosen_subcats.add(str(meta["subcategory"]))
                    basket_lines.append((
                        sku, str(meta["category"]), str(meta["subcategory"]),
                        str(meta["canonical_id"]),
                    ))

        # 2) Fill remaining slots from mission categories.
        cat_names = list(mission["categories"].keys())
        cat_probs_raw = list(mission["categories"].values())
        # A2 category boost — if a category-spike anomaly is active
        # at this store today, multiply the category's weight.
        if a2_boost_lookup:
            cat_probs_raw = [
                cp * float(a2_boost_lookup.get((trip_date, store_id, cn), 1.0))
                for cn, cp in zip(cat_names, cat_probs_raw)
            ]
        cat_probs = np.array(cat_probs_raw, dtype=float)
        cat_probs = cat_probs / cat_probs.sum()

        attempts = 0
        max_attempts = size * 6   # avoid pathological infinite loops
        while len(basket_lines) < size and attempts < max_attempts:
            attempts += 1
            category = str(rng.choice(cat_names, p=cat_probs))
            pool = sku_pool.get((banner, category))
            if pool is None or len(pool) == 0:
                continue
            # Within category, sample a SKU. Apply affinity boost: any
            # SKU whose subcategory is the partner of a subcategory
            # already in the basket gets a multiplicative weight.
            weights = np.ones(len(pool), dtype=float)
            for anchor_sub in chosen_subcats:
                for partner, boost in affinity.get(anchor_sub, []):
                    mask = pool["subcategory"].to_numpy() == partner
                    weights[mask] *= boost
            # Event-driven weight adjustments (Stage 4.8):
            if have_events:
                pool_skus = pool["sku"].to_numpy()
                pool_subcats = pool["subcategory"].to_numpy()
                # Promo demand lift: SKUs on promo today get boost.
                if promo_depth_lookup:
                    for j, s in enumerate(pool_skus):
                        depth = promo_depth_lookup.get((trip_date, str(s)))
                        if depth is not None:
                            weights[j] *= (1.0 + lift_coefficient * depth)
                # A3 pasta basket multiplier (per banner + date).
                if a3_pasta_mult != 1.0 and category == "PANTRY":
                    pasta_mask = pool_subcats == "PASTA"
                    weights[pasta_mask] *= a3_pasta_mult
            weights = weights / weights.sum()
            idx = int(rng.choice(len(pool), p=weights))
            sku = str(pool.iloc[idx]["sku"])
            if sku in chosen_skus:
                continue
            subcat = str(pool.iloc[idx]["subcategory"])
            canon  = str(pool.iloc[idx]["canonical_id"])
            chosen_skus.add(sku)
            chosen_subcats.add(subcat)
            basket_lines.append((sku, category, subcat, canon))

        # 3) Emit one record per line.
        for line_id, (sku, cat, subcat, canon) in enumerate(basket_lines, start=1):
            qty = _sample_qty(rng, cat)
            records.append((
                trip_id, line_id, sku, canon, cat, subcat, qty,
            ))

    df = pd.DataFrame(
        records,
        columns=["trip_id", "line_id", "sku", "canonical_id",
                 "category", "subcategory", "qty"],
    )
    return df.sort_values(["trip_id", "line_id"], kind="mergesort").reset_index(drop=True)
