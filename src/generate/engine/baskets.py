"""Layer 5 — Baskets & affinity (D17 / datamodel-v2 §C5).

Per trip: pick a mission → archetype → triangular basket size, then
fill the basket from a mission group-distribution modulated by:

* **Complementary affinity** — grocery subcategory pairs (Pasta↔Sauce,
  Chips↔Salsa, Cereal↔Milk, Bread↔Butter, Diapers↔Formula), config-
  driven (``affinity_matrix`` in segments/grocery.yaml).
* **QSR combo-attach (§B5, new)** — functional-category attach (entrée →
  side → drink), config-driven (``combo_attach`` in segments/qsr.yaml),
  applied at category-selection so beverage/side attach materializes as
  a comparable peer metric.
* **National-vs-PL selection by affluence (§C5)** — within a grocery
  department, private-label SKUs are up/down-weighted by the card's
  affluence (value shoppers pick PL, affluent pick national). The
  realized PL share is therefore an EMERGENT purchasing outcome measured
  from what lands in baskets — not a flag read off the catalog.
* **Seasonality category boost (§C3)** — during event weeks the boosted
  functional departments (baking/meat/etc.) get extra mission weight.

Reads the static committed catalog (dual taxonomy + private_label +
shelf_price). The grouping column is functional_department for grocery
and functional_category for QSR. Output is minimal — ``[trip_id,
line_id, sku, qty]``; category / taxonomy / price resolve via a join to
the products table (the normalization boundary stays at the catalog).
"""
from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd

from src.generate.config.loader import Config, ConfigInvariantError


# ----- Missions: per-segment mission menu ---------------------------
# Grocery mission group keys are functional_department names; QSR keys
# are functional_category names. Weights approximate the §A4 department
# sales mix once velocity/price/qty are applied.

_GROCERY_MISSIONS: dict[str, dict] = {
    "weekly_stockup": {
        "categories": {
            "Dry Grocery": 0.25, "Snacks & Candy": 0.10, "Beverages": 0.09,
            "Meat & Seafood": 0.09, "Produce": 0.13, "Dairy & Eggs": 0.11,
            "Frozen": 0.07, "Bakery": 0.06, "Health & Household": 0.08,
            "Baby & Pet": 0.01,
        },
        "archetype": "stockup", "base_share": 0.24,
    },
    "meal_tonight": {
        "categories": {
            "Meat & Seafood": 0.17, "Produce": 0.26, "Dry Grocery": 0.25,
            "Dairy & Eggs": 0.12, "Bakery": 0.08, "Frozen": 0.06,
            "Beverages": 0.06,
        },
        "archetype": "fill_in", "base_share": 0.20,
    },
    "breakfast_staples": {
        "categories": {
            "Dairy & Eggs": 0.26, "Bakery": 0.16, "Dry Grocery": 0.30,
            "Produce": 0.10, "Beverages": 0.12, "Frozen": 0.06,
        },
        "archetype": "fill_in", "base_share": 0.15,
    },
    "snacks_beverages": {
        "categories": {
            "Snacks & Candy": 0.42, "Beverages": 0.38, "Frozen": 0.10,
            "Dry Grocery": 0.10,
        },
        "archetype": "quick", "base_share": 0.18,
    },
    "household_cleaning": {
        "categories": {
            "Health & Household": 0.72, "Dry Grocery": 0.16, "Beverages": 0.06,
            "Snacks & Candy": 0.06,
        },
        "archetype": "fill_in", "base_share": 0.08,
    },
    "baby_pet": {
        "categories": {
            "Baby & Pet": 0.72, "Health & Household": 0.12, "Dairy & Eggs": 0.09,
            "Dry Grocery": 0.07,
        },
        # Rare: only a minority of cards buy baby/pet; expensive items
        # (diapers/formula/pet food) inflate $ share fast, so keep the
        # mission scarce to hold Baby & Pet near its ~3% §A4 sales share.
        "archetype": "fill_in", "base_share": 0.012,
    },
    "occasion_bbq": {
        "categories": {
            "Meat & Seafood": 0.24, "Snacks & Candy": 0.24, "Beverages": 0.22,
            "Produce": 0.14, "Bakery": 0.09, "Dry Grocery": 0.07,
        },
        "archetype": "stockup", "base_share": 0.06,
    },
}

_QSR_MISSIONS: dict[str, dict] = {
    "combo_meal": {
        "categories": {"Entrée": 0.35, "Combos": 0.10, "Sides": 0.25,
                       "Beverages": 0.25, "Chicken": 0.05},
        "archetype": "combo", "base_share": 0.34,
    },
    "snack_run": {
        "categories": {"Sides": 0.40, "Beverages": 0.35, "Chicken": 0.15,
                       "Desserts": 0.10},
        "archetype": "quick", "base_share": 0.40,
    },
    "group_order": {
        "categories": {"Entrée": 0.25, "Chicken": 0.20, "Combos": 0.15,
                       "Sides": 0.22, "Beverages": 0.18},
        "archetype": "group", "base_share": 0.08,
    },
    "breakfast": {
        "categories": {"Breakfast": 0.55, "Beverages": 0.30, "Sides": 0.15},
        "archetype": "fill_in", "base_share": 0.18,
    },
}

_SEGMENT_MISSIONS = {"grocery": _GROCERY_MISSIONS, "qsr": _QSR_MISSIONS}

# Per-banner QSR mission tilt — Chick-fil-A skews to full combo meals
# (bigger checks), Taco Bell to snack runs (smaller/value), Burger King
# balanced. Produces the §B2 check ordering CFA > BK > TB from behavior,
# not a per-chain price dial.
_QSR_BANNER_MISSION_TILT = {
    "CFA": {"combo_meal": 1.6, "snack_run": 0.7, "breakfast": 1.1},
    "TBL": {"combo_meal": 0.7, "snack_run": 1.5},
    "BKG": {"combo_meal": 1.0, "snack_run": 1.0, "breakfast": 1.2},
}

# The catalog column each segment's mission distribution groups on.
_GROUP_COL = {
    "grocery": "functional_department",
    "qsr":     "functional_category",
}


# ----- Archetype size distributions (triangular lo, μ, hi) ----------
_GROCERY_ARCHETYPES = {
    "stockup": (15, 18, 22),
    "fill_in": (4, 5, 7),
    "quick":   (2, 2, 3),
}
_QSR_ARCHETYPES = {
    "combo":   (2, 3, 4),      # entrée + side + drink
    "quick":   (1, 1, 2),      # a snack / single item
    "group":   (3, 4, 6),
    "fill_in": (2, 2, 3),
}
_SEGMENT_ARCHETYPES = {"grocery": _GROCERY_ARCHETYPES, "qsr": _QSR_ARCHETYPES}


# ----- Private-label selection by affluence (§C5) -------------------
# PL SKUs get a weight multiplier exp(slope × (1 − affluence)): value
# shoppers up-weight PL, affluent shoppers down-weight it. On top of
# that, each chain has a real PL-PROGRAM strength (Numerator: Kroger's
# private-label program is the largest at ~27%, Acme's smallest ~19%,
# Winn-Dixie ~25%) — a banner-level propensity so the realized (measured)
# per-banner PL share lands ~27/19/25. The share is still an emergent
# purchasing outcome (counted from baskets), not a catalog flag read.
_PL_AFFLUENCE_SLOPE = 2.2
_BANNER_PL_PROPENSITY = {"KRG": 1.25, "ACM": 0.42, "WDX": 1.05}


# Default staple count / inclusion probability.
_STAPLES_PER_CARD = {"grocery": 5, "qsr": 2}
_STAPLE_INCLUDE_PROB = 0.45


# Pay-cycle mission re-weighting (grocery).
_PAY_CYCLE_MISSION_WEIGHTS = {
    "early": {"weekly_stockup": 2.0, "occasion_bbq": 1.5, "baby_pet": 1.2},
    "mid":   {"weekly_stockup": 1.3, "meal_tonight": 1.2},
    "late":  {"meal_tonight": 1.15, "snacks_beverages": 1.1,
              "breakfast_staples": 1.05},
}


def _pay_cycle_bucket(day_of_month: int) -> str:
    if 1 <= day_of_month <= 10:
        return "early"
    if day_of_month in (15, 16, 17):
        return "mid"
    return "late"


# Per-line quantity by functional_department (grocery) /
# functional_category (QSR). qty=1 dominant; some multi-buy on
# produce/beverages/tacos.
_QTY_DISTRIBUTION_DEFAULT = {1: 0.85, 2: 0.12, 3: 0.025, 4: 0.005}
_QTY_DISTRIBUTION_BY_GROUP = {
    "Produce":         {1: 0.70, 2: 0.20, 3: 0.07, 4: 0.025, 5: 0.005},
    "Dairy & Eggs":    {1: 0.80, 2: 0.16, 3: 0.04},
    "Beverages":       {1: 0.78, 2: 0.16, 3: 0.05, 4: 0.01},
    "Bakery":          {1: 0.80, 2: 0.15, 3: 0.05},
    "Dry Grocery":     {1: 0.78, 2: 0.18, 3: 0.04},
    "Snacks & Candy":  {1: 0.75, 2: 0.18, 3: 0.05, 4: 0.02},
    "Meat & Seafood":  {1: 0.78, 2: 0.18, 3: 0.04},
    "Frozen":          {1: 0.80, 2: 0.15, 3: 0.05},
    # QSR
    "Entrée":     {1: 0.70, 2: 0.20, 3: 0.08, 4: 0.02},
    "Chicken":    {1: 0.80, 2: 0.15, 3: 0.05},
    "Beverages_qsr": {1: 0.85, 2: 0.12, 3: 0.03},
    "Combos":     {1: 0.90, 2: 0.09, 3: 0.01},
    "Sides":      {1: 0.78, 2: 0.18, 3: 0.04},
    "Breakfast":  {1: 0.82, 2: 0.14, 3: 0.04},
    "Desserts":   {1: 0.85, 2: 0.12, 3: 0.03},
}


def _sample_qty(rng: np.random.Generator, group: str) -> int:
    table = _QTY_DISTRIBUTION_BY_GROUP.get(group, _QTY_DISTRIBUTION_DEFAULT)
    vals = list(table.keys())
    probs = np.array(list(table.values()), dtype=float)
    probs = probs / probs.sum()
    return int(rng.choice(vals, p=probs))


# ----- Staples ------------------------------------------------------

def _derive_staples_for_card(card_id: str, segment: str, catalog: pd.DataFrame) -> list[str]:
    """Stable staple SKU set for a card. Deterministic from (card_id,
    segment) via an independent hashed RNG so staples don't perturb the
    main draw order."""
    k = _STAPLES_PER_CARD[segment]
    if k == 0:
        return []
    pool = catalog.loc[catalog["segment"] == segment, "sku"].to_numpy()
    if len(pool) == 0:
        return []
    h = hashlib.sha256(f"staples|{card_id}|{segment}".encode()).digest()
    local = np.random.default_rng(int.from_bytes(h[:8], "big"))
    return local.choice(pool, size=min(k, len(pool)), replace=False).tolist()


# ----- SKU pool + affinity ------------------------------------------

def _build_sku_pool(catalog: pd.DataFrame) -> dict[tuple[str, str], dict]:
    """Index catalog by (banner_code, group value) → arrays of sku,
    functional_subcategory, private_label. group = functional_department
    for grocery, functional_category for QSR."""
    out: dict[tuple[str, str], dict] = {}
    for segment, group_col in _GROUP_COL.items():
        seg = catalog[catalog["segment"] == segment]
        for (banner, grp), g in seg.groupby(["banner_code", group_col]):
            out[(banner, grp)] = {
                "sku": g["sku"].to_numpy(),
                "subcat": g["functional_subcategory"].to_numpy(),
                "pl": g["private_label"].to_numpy().astype(bool),
            }
    return out


def _build_affinity_lookup(cfg: Config) -> dict[str, list[tuple[str, float]]]:
    """{anchor_subcat: [(partner_subcat, boost)]} from grocery's
    affinity_matrix (subcategory level, config-driven, deterministic)."""
    out: dict[str, list[tuple[str, float]]] = {}
    for seg_name in sorted(cfg.segments):
        for anchor, partner, boost in cfg.segments[seg_name].get("affinity_matrix") or []:
            out.setdefault(str(anchor), []).append((str(partner), float(boost)))
    return out


def _build_combo_attach(cfg: Config) -> dict[str, list[tuple[str, float]]]:
    """{anchor_category: [(partner_category, boost)]} from QSR's
    combo_attach (functional_category level) — the §B5 attach mechanism."""
    out: dict[str, list[tuple[str, float]]] = {}
    for anchor, partner, boost in cfg.segments.get("qsr", {}).get("combo_attach") or []:
        out.setdefault(str(anchor), []).append((str(partner), float(boost)))
    return out


def _assert_affinity_subcats_exist(
    affinity: dict[str, list[tuple[str, float]]], catalog: pd.DataFrame,
) -> None:
    """Fail loudly if an affinity entry names a functional_subcategory the
    catalog doesn't contain (a typo would silently kill the T11 lift)."""
    valid = set(catalog["functional_subcategory"].unique())
    referenced = set(affinity) | {
        p for parts in affinity.values() for p, _ in parts
    }
    missing = sorted(referenced - valid)
    if missing:
        raise ConfigInvariantError(
            f"D17.2: affinity_matrix references functional_subcategories "
            f"absent from the catalog: {missing}"
        )


# ----- mission + archetype ------------------------------------------

def _pick_mission(rng: np.random.Generator, segment: str, bucket: str,
                  banner: str | None = None) -> str:
    missions = _SEGMENT_MISSIONS[segment]
    names = list(missions.keys())
    base = np.array([missions[m]["base_share"] for m in names], dtype=float)
    if segment == "grocery":
        adj = _PAY_CYCLE_MISSION_WEIGHTS.get(bucket, {})
        base = np.array([base[i] * adj.get(names[i], 1.0) for i in range(len(names))])
    elif segment == "qsr" and banner in _QSR_BANNER_MISSION_TILT:
        tilt = _QSR_BANNER_MISSION_TILT[banner]
        base = np.array([base[i] * tilt.get(names[i], 1.0) for i in range(len(names))])
    base = base / base.sum()
    return str(rng.choice(names, p=base))


def _sample_basket_size(rng: np.random.Generator, segment: str, archetype: str) -> int:
    lo, mu, hi = _SEGMENT_ARCHETYPES[segment][archetype]
    return int(np.clip(round(rng.triangular(lo, mu, hi)), lo, hi))


def _seasonality_boosts(cfg: Config) -> list[tuple[object, object, float, set[str]]]:
    """Return [(start_date, end_date, category_boost, {departments})]."""
    out = []
    for ev in (cfg.global_.get("seasonality") or {}).get("events", []):
        out.append((
            pd.Timestamp(ev["start_date"]).date(),
            pd.Timestamp(ev["end_date"]).date(),
            float(ev.get("category_boost", 1.0)),
            set(ev.get("categories", [])),
        ))
    return out


# ----- main builder -------------------------------------------------

def build_basket_items(
    cfg: Config,
    trips: pd.DataFrame,
    customers: pd.DataFrame,
    catalog: pd.DataFrame,
    rng: np.random.Generator,
    *,
    promo_depth_lookup: dict | None = None,
    lift_coefficient: float = 6.0,
) -> pd.DataFrame:
    """Per-trip basket items → ``[trip_id, line_id, sku, qty]``.

    ``promo_depth_lookup`` {(date, sku) → depth} is the DORMANT promo
    demand-lift hook (empty in datamodel-v2; promotions disabled).
    """
    promo_depth_lookup = promo_depth_lookup or {}
    have_promo = bool(promo_depth_lookup)
    sku_pool = _build_sku_pool(catalog)
    affinity = _build_affinity_lookup(cfg)
    _assert_affinity_subcats_exist(affinity, catalog)
    combo_attach = _build_combo_attach(cfg)
    seasonality = _seasonality_boosts(cfg)

    # Precompute lookups as plain dicts — per-trip pandas .loc scalar
    # lookups are far too slow at scale (same values, no behavior change).
    aff_by_card = dict(zip(customers["card_id"], customers["affluence"].astype(float)))
    sku_sub = dict(zip(catalog["sku"], catalog["functional_subcategory"]))
    sku_group_g = dict(zip(catalog["sku"], catalog["functional_department"]))
    sku_group_q = dict(zip(catalog["sku"], catalog["functional_category"]))
    # sku→banner: the v2 SKU codes diverge per banner and don't embed the
    # banner string, so staple↔trip-banner matching needs an explicit map.
    sku_banner = dict(zip(catalog["sku"], catalog["banner_code"]))

    staple_cache: dict[tuple[str, str], list[str]] = {}
    records: list[tuple] = []

    for tr in trips.itertuples(index=False):
        trip_id, card_id, segment = tr.trip_id, tr.card_id, tr.segment
        banner, store_id = tr.banner_code, str(tr.store_id)
        ts = pd.Timestamp(tr.txn_ts)
        trip_date = ts.date()
        bucket = _pay_cycle_bucket(ts.day)
        affluence = aff_by_card[card_id]

        mission_name = _pick_mission(rng, segment, bucket, banner)
        mission = _SEGMENT_MISSIONS[segment][mission_name]
        size = _sample_basket_size(rng, segment, mission["archetype"])
        group_col = _GROUP_COL[segment]
        sku_group = sku_group_g if segment == "grocery" else sku_group_q

        # Mission group distribution (+ seasonality dept boost for grocery).
        groups = list(mission["categories"].keys())
        gw = np.array(list(mission["categories"].values()), dtype=float)
        if segment == "grocery":
            for (s, e, boost, depts) in seasonality:
                if s <= trip_date <= e:
                    for i, gname in enumerate(groups):
                        if gname in depts:
                            gw[i] *= boost

        # Staples for this card+segment.
        skey = (card_id, segment)
        if skey not in staple_cache:
            staple_cache[skey] = _derive_staples_for_card(card_id, segment, catalog)
        staples = staple_cache[skey]

        chosen_skus: set[str] = set()
        chosen_subcats: set[str] = set()
        chosen_groups: set[str] = set()
        basket: list[tuple[str, str]] = []       # (sku, group)

        # 1) Staples (banner-specific) with inclusion probability.
        for sku in staples:
            if len(basket) >= size:
                break
            if sku_banner.get(sku) != banner:
                continue
            if sku in chosen_skus or sku not in sku_sub:
                continue
            if rng.uniform() < _STAPLE_INCLUDE_PROB:
                sub = str(sku_sub[sku]); grp = str(sku_group[sku])
                chosen_skus.add(sku); chosen_subcats.add(sub); chosen_groups.add(grp)
                basket.append((sku, grp))

        # 2) Fill remaining slots.
        attempts, max_attempts = 0, size * 8
        while len(basket) < size and attempts < max_attempts:
            attempts += 1
            # Category weights (+ QSR combo-attach boost given chosen cats).
            w = gw.copy()
            if segment == "qsr" and combo_attach:
                for i, gname in enumerate(groups):
                    for anchor in chosen_groups:
                        for partner, boost in combo_attach.get(anchor, []):
                            if partner == gname:
                                w[i] *= boost
            w = w / w.sum()
            group = str(rng.choice(groups, p=w))
            pool = sku_pool.get((banner, group))
            if pool is None or len(pool["sku"]) == 0:
                continue
            skus, subs, pls = pool["sku"], pool["subcat"], pool["pl"]
            weights = np.ones(len(skus), dtype=float)
            # Grocery complementary affinity (subcategory).
            if segment == "grocery":
                for anchor_sub in chosen_subcats:
                    for partner, boost in affinity.get(anchor_sub, []):
                        weights[subs == partner] *= boost
                # National-vs-PL selection: affluence × chain PL-program
                # strength (§C5 / §A13). Realized share is measured, emergent.
                pl_mult = np.exp(_PL_AFFLUENCE_SLOPE * (1.0 - affluence))
                pl_mult *= _BANNER_PL_PROPENSITY.get(banner, 1.0)
                weights[pls] *= pl_mult
            # Dormant promo demand lift.
            if have_promo:
                for j, s in enumerate(skus):
                    depth = promo_depth_lookup.get((trip_date, str(s)))
                    if depth is not None:
                        weights[j] *= (1.0 + lift_coefficient * depth)
            weights = weights / weights.sum()
            idx = int(rng.choice(len(skus), p=weights))
            sku = str(skus[idx])
            if sku in chosen_skus:
                continue
            chosen_skus.add(sku)
            chosen_subcats.add(str(subs[idx]))
            chosen_groups.add(group)
            basket.append((sku, group))

        # 3) Emit lines.
        for line_id, (sku, grp) in enumerate(basket, start=1):
            qty_group = grp if grp != "Beverages" or segment == "grocery" else "Beverages_qsr"
            records.append((trip_id, line_id, sku, _sample_qty(rng, qty_group)))

    df = pd.DataFrame(records, columns=["trip_id", "line_id", "sku", "qty"])
    return df.sort_values(["trip_id", "line_id"], kind="mergesort").reset_index(drop=True)
