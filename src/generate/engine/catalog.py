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
    subcategory, canonical_id, private_label``. Pricing fields
    (base_price, etc.) are added at Stage 4.7.
    """
    rows: list[dict] = []
    for segment in ("grocery", "qsr", "off_price"):
        canonicals = _canonical_skus_for_segment(segment)
        pl_flags = _mark_private_label(canonicals, segment, rng)
        for banner in _SEGMENT_BANNERS[segment]:
            for canon, pl in zip(canonicals, pl_flags):
                rows.append({
                    "sku":          f"{banner}-{canon['canonical_id']}",
                    "merchant_id":  banner,
                    "banner_code":  banner,
                    "segment":      segment,
                    "category":     canon["category"],
                    "subcategory":  canon["subcategory"],
                    "canonical_id": canon["canonical_id"],
                    "private_label": bool(pl),
                })

    df = pd.DataFrame(rows)
    return df.sort_values(["banner_code", "sku"], kind="mergesort").reset_index(drop=True)
