"""Kroger SKU catalog (~1,500 SKUs across 12 categories).

Anchor SKUs are placed at fixed indices so affinity rules and the price-spike
anomaly (`KRG-PRODUCE-0042`) reference stable IDs.
"""
import numpy as np
import pandas as pd

from . import parameters as P

# Category -> (count, base price range, default subcategory pool, ebt_eligible_default)
CATEGORIES = [
    ("PRODUCE",    120, (0.69, 12.99), ["Fresh Produce"], 1),
    ("DAIRY",      140, (1.99, 9.99),  ["Milk & Cream", "Cheese", "Yogurt", "Eggs"], 1),
    ("BAKERY",      80, (1.99, 8.99),  ["Bread", "Tortillas", "Buns", "Pastry"], 1),
    ("MEAT",       150, (3.99, 24.99), ["Beef", "Chicken", "Pork", "Seafood"], 1),
    ("FROZEN",     140, (2.49, 12.99), ["Frozen Meals", "Ice Cream", "Frozen Veg"], 1),
    ("PANTRY",     220, (1.49, 14.99), ["Pasta & Sauce", "Cereal", "Canned", "Baking", "Oils"], 1),
    ("SNACKS",     140, (1.99, 7.99),  ["Chips", "Cookies", "Bars"], 1),
    ("BEVERAGES",  160, (1.49, 14.99), ["Coffee", "Soda", "Juice", "Water"], 1),
    ("HOUSEHOLD",  120, (2.99, 19.99), ["Cleaning", "Paper Goods", "Laundry"], 0),
    ("PERSONAL",   100, (2.49, 14.99), ["Hygiene", "Hair Care"], 0),
    ("BABY",        80, (3.99, 39.99), ["Diapers", "Formula", "Baby Food"], 1),
    ("PET",         50, (4.99, 49.99), ["Dog", "Cat"], 0),
]

# Anchor SKUs — fixed slots for affinity rules and the planted price-spike anomaly.
ANCHORS = {
    "KRG-PRODUCE-0042":   ("Avocados (4-pack)",       "PRODUCE",   "Fresh Produce", 4.99,  1),
    "KRG-DAIRY-0001":     ("Whole milk (gallon)",      "DAIRY",     "Milk & Cream",  3.99,  1),
    "KRG-DAIRY-0010":     ("Half & half (quart)",      "DAIRY",     "Milk & Cream",  3.49,  1),
    "KRG-DAIRY-0050":     ("Shredded cheddar (8 oz)",  "DAIRY",     "Cheese",        3.99,  1),
    "KRG-PANTRY-0001":    ("Spaghetti (1 lb box)",     "PANTRY",    "Pasta & Sauce", 1.99,  1),
    "KRG-PANTRY-0002":    ("Marinara sauce (24 oz)",   "PANTRY",    "Pasta & Sauce", 3.49,  1),
    "KRG-PANTRY-0050":    ("Cheerios cereal (18 oz)",  "PANTRY",    "Cereal",        4.99,  1),
    "KRG-MEAT-0001":      ("80/20 ground beef (lb)",   "MEAT",      "Beef",          5.99,  1),
    "KRG-BAKERY-0010":    ("Tortillas flour (10 ct)",  "BAKERY",    "Tortillas",     2.99,  1),
    "KRG-BABY-0001":      ("Diapers size 3 (144 ct)",  "BABY",      "Diapers",      34.99,  1),
    "KRG-BABY-0010":      ("Infant formula (23 oz)",   "BABY",      "Formula",      28.99,  1),
    "KRG-BEVERAGES-0001": ("Ground coffee (30 oz)",    "BEVERAGES", "Coffee",       12.99,  1),
}


def build_catalog() -> pd.DataFrame:
    """Build the Kroger catalog deterministically (no rng — fixed pricing model)."""
    # Use a local rng for any catalog jitter, but seed from a constant so the catalog
    # is identical across runs without requiring callers to pass an rng.
    rng = np.random.default_rng(P.RANDOM_SEED + 1)

    rows = []
    for cat, count, (lo, hi), subs, ebt_default in CATEGORIES:
        # Last 30% of bakery flagged as prepared/hot — ineligible for EBT.
        for i in range(1, count + 1):
            sku = f"KRG-{cat}-{i:04d}"
            if sku in ANCHORS:
                continue  # filled in below
            subcategory = subs[(i - 1) % len(subs)]
            base_price = round(float(rng.uniform(lo, hi)), 2)
            ebt_eligible = ebt_default
            if cat == "BAKERY" and i > int(count * 0.7):
                ebt_eligible = 0
            is_organic = int(rng.random() < P.MERCHANT_CONFIGS["kroger"]["organic_share"]
                             and cat in {"PRODUCE", "DAIRY", "MEAT"})
            rows.append({
                "sku": sku,
                "merchant_id": "KRG",
                "name": f"{cat.title()} item {i:04d}",
                "category": cat,
                "subcategory": subcategory,
                "is_organic": is_organic,
                "base_price": base_price,
                "ebt_eligible": ebt_eligible,
            })

    for sku, (name, cat, subcat, price, ebt) in ANCHORS.items():
        rows.append({
            "sku": sku,
            "merchant_id": "KRG",
            "name": name,
            "category": cat,
            "subcategory": subcat,
            "is_organic": 0,
            "base_price": price,
            "ebt_eligible": ebt,
        })

    df = pd.DataFrame(rows).sort_values("sku").reset_index(drop=True)
    assert len(df) == 1500, f"Kroger catalog size mismatch: {len(df)}"
    return df


# Affinity rules: (anchor_sku, companion_sku, P(companion | anchor))
KROGER_AFFINITY = [
    ("KRG-BABY-0001",      "KRG-BABY-0010",   0.45),  # Diapers + Infant formula
    ("KRG-PANTRY-0001",    "KRG-PANTRY-0002", 0.55),  # Spaghetti + Marinara
    ("KRG-BAKERY-0010",    "KRG-MEAT-0001",   0.40),  # Tortillas + Ground beef
    ("KRG-BAKERY-0010",    "KRG-DAIRY-0050",  0.45),  # Tortillas + Shredded cheddar
    ("KRG-DAIRY-0001",     "KRG-PANTRY-0050", 0.30),  # Whole milk + Cereal
    ("KRG-BEVERAGES-0001", "KRG-DAIRY-0010",  0.40),  # Coffee + Half & half
]


def make_apply_affinity(catalog: pd.DataFrame):
    rules = KROGER_AFFINITY

    def apply(basket: list[str], rng: np.random.Generator) -> list[str]:
        present = set(basket)
        for anchor, companion, prob in rules:
            if anchor in present and companion not in present and rng.random() < prob:
                basket.append(companion)
                present.add(companion)
        return basket
    return apply
