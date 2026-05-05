"""TJ Maxx SKU catalog (200 generic categorical SKUs).

`TJX-HOM-001` is reserved as the "Kitchen towel set" anchor for the home-goods
affinity rule.
"""
import numpy as np
import pandas as pd

from . import parameters as P

CATEGORIES = [
    ("WOM", 50, (12.00, 89.99),  "Women's Apparel",       ["Women's blouse", "Women's denim",
                                                            "Designer dress", "Athletic top",
                                                            "Athletic bottom", "Sweater"]),
    ("MEN", 35, (14.00, 79.99),  "Men's Apparel",         ["Men's polo", "Men's chinos",
                                                            "Designer button-down",
                                                            "Athletic shorts", "Sweater"]),
    ("KID", 25, (8.00, 39.99),   "Kids",                  ["Kids' jeans", "Kids' tee",
                                                            "Toddler dress", "Kids' pajamas",
                                                            "Kids' jacket"]),
    ("SHO", 25, (19.99, 99.99),  "Shoes",                 ["Athletic shoes", "Women's heels",
                                                            "Men's casual shoes",
                                                            "Kids' sneakers", "Boots"]),
    ("ACC", 20, (9.99, 199.99),  "Handbags & Accessories", ["Designer handbag", "Crossbody bag",
                                                             "Wallet", "Belt", "Sunglasses",
                                                             "Scarf"]),
    ("HOM", 30, (4.99, 49.99),   "Home Goods",            ["Kitchen towel set",  # index 1 — anchor
                                                            "Throw pillow", "Picture frame",
                                                            "Decorative vase", "Wall art",
                                                            "Candle"]),
    ("BTY", 10, (6.99, 29.99),   "Beauty",                ["Body lotion", "Hand cream",
                                                            "Hair care set", "Fragrance"]),
    ("JEW",  5, (14.99, 89.99),  "Jewelry",               ["Earrings", "Necklace", "Bracelet",
                                                            "Watch"]),
]

KITCHEN_TOWEL_SKU = "TJX-HOM-001"


def build_catalog() -> pd.DataFrame:
    rng = np.random.default_rng(P.RANDOM_SEED + 3)
    rows = []
    for cat, count, (lo, hi), label, names in CATEGORIES:
        for i in range(1, count + 1):
            sku = f"TJX-{cat}-{i:03d}"
            name = names[(i - 1) % len(names)]
            base_price = round(float(rng.uniform(lo, hi)), 2)
            rows.append({
                "sku": sku,
                "merchant_id": "TJX",
                "name": name,
                "category": cat,
                "subcategory": label,
                "is_organic": 0,
                "base_price": base_price,
            })
    df = pd.DataFrame(rows).sort_values("sku").reset_index(drop=True)
    assert len(df) == 200, f"TJ Maxx catalog size mismatch: {len(df)}"
    return df


def make_apply_affinity(catalog: pd.DataFrame):
    cat_lookup = dict(zip(catalog["sku"], catalog["category"]))
    accessories = catalog.loc[catalog["category"] == "ACC", "sku"].to_numpy()
    home_goods = catalog.loc[catalog["category"] == "HOM", "sku"].to_numpy()

    def apply(basket: list[str], rng: np.random.Generator) -> list[str]:
        cats = {cat_lookup.get(s) for s in basket}
        if "WOM" in cats and "ACC" not in cats:
            if rng.random() < 0.40 and len(accessories) > 0:
                basket.append(str(accessories[rng.integers(0, len(accessories))]))
                cats.add("ACC")
        if KITCHEN_TOWEL_SKU in basket and rng.random() < 0.50:
            others = [s for s in home_goods if s != KITCHEN_TOWEL_SKU and s not in basket]
            if others:
                basket.append(others[rng.integers(0, len(others))])
        return basket
    return apply
