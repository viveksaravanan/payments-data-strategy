"""Catalog — per-merchant SKU lists (subset of Layer 7 / D19).

Strict D11 layer order puts catalog at L7 (Stage 4.7), but baskets at
L5 need to reference SKUs. D19.5 / D17.5 reconcile this: the catalog's
*structure* (SKU list with category, subcategory, private_label,
canonical_id) is logically L5; the catalog's *pricing* is L7. Stage
4.5 (this module) produces the structure; Stage 4.7 extends the
table with anchored prices, per-merchant strategy modifiers, and
the time-drift / promo state.

Per-merchant assortment differentiation (D19.4 — premium specialty
tail, value PL depth) is also deferred to Stage 4.7. Wave 1 Stage
4.5 ships a full-assortment baseline so the basket layer can test
its mission-driven affinities without an additional confounder.

canonical_id ties the same product across merchants for cross-
merchant comparison (e.g. 2% Milk 1gal at KRG, ACM, WDX share a
canonical_id). Format: ``<SEG>-<CATEGORY>-<SUBCATEGORY>-<NNN>``.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.generate.config.loader import Config


# ----- Taxonomies ---------------------------------------------------
# Per-segment category → subcategory → SKUs-per-subcategory count.

_GROCERY_TAXONOMY: dict[str, dict[str, int]] = {
    "DAIRY":      {"MILK": 22, "CHEESE": 30, "YOGURT": 24, "BUTTER": 10, "EGGS": 8},
    "BAKERY":     {"BREAD": 22, "ROLLS": 16, "PASTRIES": 20, "BAGELS": 14},
    "PRODUCE":    {"FRUIT": 30, "VEGETABLE": 35, "SALAD": 12, "HERBS": 10},
    "MEAT":       {"BEEF": 22, "POULTRY": 18, "PORK": 16, "SEAFOOD": 18, "DELI": 14},
    "FROZEN":     {"FROZEN_MEAL": 26, "ICE_CREAM": 24, "FROZEN_VEG": 18, "FROZEN_PIZZA": 20},
    "PANTRY":     {"PASTA": 22, "SAUCE": 24, "CANNED": 28, "RICE": 14, "CEREAL": 28,
                   "COFFEE": 18, "SOUP": 22},
    "SNACKS":     {"CHIPS": 28, "SALSA": 16, "COOKIES": 24, "CRACKERS": 18,
                   "CANDY": 28, "NUTS": 16},
    "BEVERAGES":  {"SODA": 30, "WATER": 16, "JUICE": 22, "TEA": 18, "ENERGY": 14},
    "HOUSEHOLD":  {"CLEANING": 24, "PAPER": 14, "LAUNDRY": 20, "STORAGE": 14},
    "PERSONAL":   {"TOOTHPASTE": 18, "SOAP": 18, "SHAMPOO": 24, "DEODORANT": 14},
    "BABY":       {"DIAPERS": 14, "WIPES": 10, "FORMULA": 12, "BABY_FOOD": 18},
    "PET":        {"PET_FOOD": 24, "PET_TREATS": 18, "PET_SUPPLIES": 18},
}

_QSR_TAXONOMY: dict[str, dict[str, int]] = {
    "TACO":  {"CRUNCHY": 4, "SOFT": 4, "SPECIALTY": 4},
    "BURR":  {"BEAN": 3, "BEEF": 4, "CHICKEN": 4, "SPECIALTY": 4},
    "COMBO": {"LUNCH_COMBO": 4, "DINNER_COMBO": 4},
    "SIDE":  {"NACHO": 3, "FRIES": 2, "CINNAMON": 2},
    "DRINK": {"SODA": 6, "COFFEE": 3, "SWEET_TEA": 2, "WATER": 2},
    "BFAST": {"BREAKFAST_BURR": 4, "CRUNCHWRAP": 2, "EGG_DISH": 3},
}

_OFF_PRICE_TAXONOMY: dict[str, dict[str, int]] = {
    "WOM": {"TOP": 24, "BOTTOM": 18, "DRESS": 14, "OUTERWEAR": 8},
    "MEN": {"TOP": 16, "BOTTOM": 14, "OUTERWEAR": 6},
    "KID": {"TOP": 12, "BOTTOM": 10, "OUTERWEAR": 6},
    "HOM": {"DECOR": 14, "KITCHEN": 12, "BATH": 8, "BEDDING": 10},
    "ACC": {"BAG": 10, "JEWELRY": 8, "BELT": 4},
    "SHO": {"WOMEN": 12, "MEN": 8, "KID": 6},
    "BTY": {"FRAGRANCE": 6, "MAKEUP": 8, "SKINCARE": 6},
}


# Default private-label share by segment (D19.4 — Wave 1 Stage 4.5
# uses a uniform share across merchants; per-merchant PL depth lands
# at 4.7).
_PRIVATE_LABEL_SHARE = {
    "grocery":   0.25,
    "qsr":       0.0,       # QSR menu is the menu; no PL concept
    "off_price": 0.10,      # mostly branded
}


# Anchor base prices per (segment, category, subcategory). Mid-of-range
# value; per-SKU spread within the subcategory comes from the position
# index plus per-SKU competitive index (Stage 4.7 pricing). Real-world
# 2025 anchors per D19.1.

_ANCHOR_PRICE_MID: dict[tuple[str, str, str], float] = {
    # Grocery (per-unit prices, mid-range)
    ("grocery", "DAIRY",     "MILK"):       3.49,
    ("grocery", "DAIRY",     "CHEESE"):     4.99,
    ("grocery", "DAIRY",     "YOGURT"):     2.49,
    ("grocery", "DAIRY",     "BUTTER"):     4.49,
    ("grocery", "DAIRY",     "EGGS"):       3.79,
    ("grocery", "BAKERY",    "BREAD"):      3.49,
    ("grocery", "BAKERY",    "ROLLS"):      3.79,
    ("grocery", "BAKERY",    "PASTRIES"):   4.49,
    ("grocery", "BAKERY",    "BAGELS"):     3.99,
    ("grocery", "PRODUCE",   "FRUIT"):      2.99,
    ("grocery", "PRODUCE",   "VEGETABLE"):  2.49,
    ("grocery", "PRODUCE",   "SALAD"):      4.49,
    ("grocery", "PRODUCE",   "HERBS"):      2.79,
    ("grocery", "MEAT",      "BEEF"):       7.49,
    ("grocery", "MEAT",      "POULTRY"):    5.99,
    ("grocery", "MEAT",      "PORK"):       5.49,
    ("grocery", "MEAT",      "SEAFOOD"):    9.99,
    ("grocery", "MEAT",      "DELI"):       7.99,
    ("grocery", "FROZEN",    "FROZEN_MEAL"): 4.49,
    ("grocery", "FROZEN",    "ICE_CREAM"):   5.49,
    ("grocery", "FROZEN",    "FROZEN_VEG"):  2.99,
    ("grocery", "FROZEN",    "FROZEN_PIZZA"): 6.49,
    ("grocery", "PANTRY",    "PASTA"):      2.49,
    ("grocery", "PANTRY",    "SAUCE"):      3.99,
    ("grocery", "PANTRY",    "CANNED"):     1.99,
    ("grocery", "PANTRY",    "RICE"):       3.99,
    ("grocery", "PANTRY",    "CEREAL"):     4.49,
    ("grocery", "PANTRY",    "COFFEE"):     9.99,
    ("grocery", "PANTRY",    "SOUP"):       2.49,
    ("grocery", "SNACKS",    "CHIPS"):      4.49,
    ("grocery", "SNACKS",    "SALSA"):      3.99,
    ("grocery", "SNACKS",    "COOKIES"):    3.99,
    ("grocery", "SNACKS",    "CRACKERS"):   3.49,
    ("grocery", "SNACKS",    "CANDY"):      2.49,
    ("grocery", "SNACKS",    "NUTS"):       7.49,
    ("grocery", "BEVERAGES", "SODA"):       4.99,
    ("grocery", "BEVERAGES", "WATER"):      4.49,
    ("grocery", "BEVERAGES", "JUICE"):      4.49,
    ("grocery", "BEVERAGES", "TEA"):        3.49,
    ("grocery", "BEVERAGES", "ENERGY"):     2.49,
    ("grocery", "HOUSEHOLD", "CLEANING"):   5.49,
    ("grocery", "HOUSEHOLD", "PAPER"):      11.99,
    ("grocery", "HOUSEHOLD", "LAUNDRY"):    12.99,
    ("grocery", "HOUSEHOLD", "STORAGE"):    6.99,
    ("grocery", "PERSONAL",  "TOOTHPASTE"): 3.99,
    ("grocery", "PERSONAL",  "SOAP"):       5.49,
    ("grocery", "PERSONAL",  "SHAMPOO"):    7.99,
    ("grocery", "PERSONAL",  "DEODORANT"):  4.99,
    ("grocery", "BABY",      "DIAPERS"):    24.99,
    ("grocery", "BABY",      "WIPES"):      6.99,
    ("grocery", "BABY",      "FORMULA"):    29.99,
    ("grocery", "BABY",      "BABY_FOOD"):  2.49,
    ("grocery", "PET",       "PET_FOOD"):   17.99,
    ("grocery", "PET",       "PET_TREATS"): 6.49,
    ("grocery", "PET",       "PET_SUPPLIES"): 12.99,

    # QSR (per-item menu prices)
    ("qsr", "TACO",  "CRUNCHY"):    1.99,
    ("qsr", "TACO",  "SOFT"):       2.19,
    ("qsr", "TACO",  "SPECIALTY"):  3.49,
    ("qsr", "BURR",  "BEAN"):       2.49,
    ("qsr", "BURR",  "BEEF"):       3.49,
    ("qsr", "BURR",  "CHICKEN"):    3.99,
    ("qsr", "BURR",  "SPECIALTY"):  5.49,
    ("qsr", "COMBO", "LUNCH_COMBO"):  7.99,
    ("qsr", "COMBO", "DINNER_COMBO"): 8.99,
    ("qsr", "SIDE",  "NACHO"):      2.99,
    ("qsr", "SIDE",  "FRIES"):      2.49,
    ("qsr", "SIDE",  "CINNAMON"):   2.79,
    ("qsr", "DRINK", "SODA"):       2.49,
    ("qsr", "DRINK", "COFFEE"):     2.79,
    ("qsr", "DRINK", "SWEET_TEA"):  2.49,
    ("qsr", "DRINK", "WATER"):      1.99,
    ("qsr", "BFAST", "BREAKFAST_BURR"): 3.99,
    ("qsr", "BFAST", "CRUNCHWRAP"): 4.49,
    ("qsr", "BFAST", "EGG_DISH"):   3.49,

    # Off-price retail (per-item prices)
    ("off_price", "WOM", "TOP"):       17.99,
    ("off_price", "WOM", "BOTTOM"):    24.99,
    ("off_price", "WOM", "DRESS"):     34.99,
    ("off_price", "WOM", "OUTERWEAR"): 59.99,
    ("off_price", "MEN", "TOP"):       19.99,
    ("off_price", "MEN", "BOTTOM"):    29.99,
    ("off_price", "MEN", "OUTERWEAR"): 54.99,
    ("off_price", "KID", "TOP"):       12.99,
    ("off_price", "KID", "BOTTOM"):    14.99,
    ("off_price", "KID", "OUTERWEAR"): 24.99,
    ("off_price", "HOM", "DECOR"):     19.99,
    ("off_price", "HOM", "KITCHEN"):   17.99,
    ("off_price", "HOM", "BATH"):      14.99,
    ("off_price", "HOM", "BEDDING"):   39.99,
    ("off_price", "ACC", "BAG"):       34.99,
    ("off_price", "ACC", "JEWELRY"):   17.99,
    ("off_price", "ACC", "BELT"):      14.99,
    ("off_price", "SHO", "WOMEN"):     34.99,
    ("off_price", "SHO", "MEN"):       39.99,
    ("off_price", "SHO", "KID"):       19.99,
    ("off_price", "BTY", "FRAGRANCE"): 19.99,
    ("off_price", "BTY", "MAKEUP"):    14.99,
    ("off_price", "BTY", "SKINCARE"):  17.99,
}


def _base_price_for_canonical(segment: str, category: str, subcategory: str,
                              position: int, n_in_subcat: int) -> float:
    """Per-SKU base price. Anchor mid for the (cat, subcat) ± a
    position-driven spread so SKUs within a subcategory vary
    realistically without per-SKU noise (deterministic).

    Spread: ±20% around the anchor, distributed evenly by position.
    """
    anchor = _ANCHOR_PRICE_MID.get((segment, category, subcategory))
    if anchor is None:
        anchor = 5.00  # fallback
    if n_in_subcat <= 1:
        return round(anchor, 2)
    # Linear ramp from -20% to +20% by position 1..n.
    t = (position - 1) / max(1, n_in_subcat - 1)
    spread_factor = 0.80 + 0.40 * t
    return round(anchor * spread_factor, 2)


_SEGMENT_BANNERS = {
    "grocery":   ("ACM", "KRG", "WDX"),
    "qsr":       ("TBL",),
    "off_price": ("TJX",),
}

_SEGMENT_TAXONOMY = {
    "grocery":   _GROCERY_TAXONOMY,
    "qsr":       _QSR_TAXONOMY,
    "off_price": _OFF_PRICE_TAXONOMY,
}


def _canonical_skus_for_segment(segment: str) -> list[dict]:
    """One canonical SKU per (segment, category, subcategory, position).
    Shared across all banners in that segment (cross-merchant matching).
    """
    rows: list[dict] = []
    for category, subcats in _SEGMENT_TAXONOMY[segment].items():
        for subcat, count in subcats.items():
            for i in range(1, count + 1):
                canonical_id = f"{segment[:1].upper()}-{category}-{subcat}-{i:03d}"
                rows.append({
                    "canonical_id": canonical_id,
                    "segment":      segment,
                    "category":     category,
                    "subcategory":  subcat,
                    "position":     i,
                })
    return rows


def _mark_private_label(
    canonical_rows: list[dict],
    segment: str,
    rng: np.random.Generator,
) -> list[bool]:
    """Designate ~``share`` of the canonical SKUs as private label.
    Deterministic from the rng draw order."""
    share = _PRIVATE_LABEL_SHARE[segment]
    flags = rng.uniform(size=len(canonical_rows)) < share
    return flags.tolist()


def build_catalog(cfg: Config, rng: np.random.Generator) -> pd.DataFrame:
    """Emit the per-merchant catalog table.

    Returns one row per (merchant, canonical SKU) pair. For Wave 1
    Stage 4.5 every merchant in a segment carries every canonical
    SKU; per-merchant assortment differentiation (D19.4) lands at
    Stage 4.7.

    Columns: ``sku, merchant_id, banner_code, segment, category,
    subcategory, canonical_id, private_label, base_price``.
    base_price added at Stage 4.7 — the shared "anchor" the
    pricing layer modulates per merchant + zone + time + line.
    """
    rows: list[dict] = []
    for segment in ("grocery", "qsr", "off_price"):
        canonicals = _canonical_skus_for_segment(segment)
        pl_flags = _mark_private_label(canonicals, segment, rng)
        # Count SKUs per (cat, subcat) to drive the position-based price spread.
        subcat_counts: dict[tuple[str, str], int] = {}
        for canon in canonicals:
            key = (canon["category"], canon["subcategory"])
            subcat_counts[key] = subcat_counts.get(key, 0) + 1

        for banner in _SEGMENT_BANNERS[segment]:
            for canon, pl in zip(canonicals, pl_flags):
                n_in_subcat = subcat_counts[(canon["category"], canon["subcategory"])]
                base_price = _base_price_for_canonical(
                    segment, canon["category"], canon["subcategory"],
                    canon["position"], n_in_subcat,
                )
                rows.append({
                    "sku":          f"{banner}-{canon['canonical_id']}",
                    "merchant_id":  banner,
                    "banner_code":  banner,
                    "segment":      segment,
                    "category":     canon["category"],
                    "subcategory":  canon["subcategory"],
                    "canonical_id": canon["canonical_id"],
                    "private_label": bool(pl),
                    "base_price":   base_price,
                })

    df = pd.DataFrame(rows)
    return df.sort_values(["banner_code", "sku"], kind="mergesort").reset_index(drop=True)
