"""Per-line tax rates by category.

Source: `V2_5_DATA_DESIGN.md` Phase 1 Layer 3e — five-tier tax model.

The lookup is explicit (rather than rule-based) because the actual
catalog category vocabulary is wider than the design doc's example list:
Taco Bell uses TACO/BURR/SPEC/COMBO/SIDE/DRINK/BREAKFAST (the design
gives MAIN/SIDE/DRINK/COMBO as exemplars), and TJ Maxx uses
WOM/MEN/KID/SHO/ACC/HOM/BEA/JEW (design gives APPAREL/HOME/ACCESSORY).
All QSR food and all retail apparel are taxable at 7% per the design's
"non-food / prepared" and "retail" rates.
"""
from __future__ import annotations

# Phase 1 Layer 3e tax-rate-by-category. Tax-exempt grocery staples
# (DAIRY, BAKERY basic, PRODUCE, MEAT, FROZEN, PANTRY) are 0%; the
# 4%-tier (BEVERAGES, SNACKS) reflects "discretionary food" treatment;
# 7% covers everything else (prepared/non-food grocery, QSR food,
# retail apparel/home/accessory).
TAX_RATE_BY_CATEGORY: dict[str, float] = {
    # --- Tax-exempt grocery (Layer 3e tier 1) -----------------------
    "DAIRY":     0.00,
    "BAKERY":    0.00,
    "PRODUCE":   0.00,
    "MEAT":      0.00,
    "FROZEN":    0.00,
    "PANTRY":    0.00,
    # --- Discretionary grocery (Layer 3e tier 2) --------------------
    "BEVERAGES": 0.04,
    "SNACKS":    0.04,
    # --- Non-food grocery (Layer 3e tier 3) -------------------------
    "HOUSEHOLD": 0.07,
    "PERSONAL":  0.07,
    "BABY":      0.07,
    "PET":       0.07,
    # --- QSR food (Layer 3e tier 4) — Taco Bell vocabulary ----------
    "TACO":      0.07,
    "BURR":      0.07,
    "SPEC":      0.07,
    "COMBO":     0.07,
    "SIDE":      0.07,
    "DRINK":     0.07,
    "BFAST":     0.07,
    # --- Retail (Layer 3e tier 5) — TJ Maxx vocabulary --------------
    "WOM":       0.07,
    "MEN":       0.07,
    "KID":       0.07,
    "SHO":       0.07,
    "ACC":       0.07,
    "HOM":       0.07,
    "BTY":       0.07,
    "JEW":       0.07,
}


def tax_rate(category: str) -> float:
    """Return the tax rate for `category`. Raises if the category is
    unknown — better to fail loudly than silently apply a wrong rate."""
    try:
        return TAX_RATE_BY_CATEGORY[category]
    except KeyError as exc:
        raise KeyError(
            f"No tax rate registered for category {category!r}. "
            f"Add it to src/generate/tax.py."
        ) from exc
