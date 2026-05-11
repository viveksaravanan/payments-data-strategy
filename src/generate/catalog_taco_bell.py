"""Taco Bell SKU catalog (60 SKUs).

`type` column drives the affinity rule: any entree → drink @ 0.70; combo →
cinnamon twists @ 0.35.
"""
import numpy as np
import pandas as pd

from . import parameters as P

# (type, count, base price range, example names)
TYPES = [
    ("TACO",   8,  (1.29, 4.49),  ["Crunchy Taco", "Soft Taco", "Doritos Locos Taco",
                                    "Crunchy Taco Supreme", "Soft Taco Supreme",
                                    "Spicy Potato Soft Taco", "Steak Soft Taco",
                                    "Chalupa-style Taco"]),
    ("BURR",  10,  (2.29, 6.49),  ["Bean Burrito", "Beefy 5-Layer Burrito", "Burrito Supreme",
                                    "Chicken Chipotle Melt", "Cheesy Bean & Rice",
                                    "Quesarito", "Grilled Cheese Burrito",
                                    "Crunchwrap Burrito", "Black Bean Burrito",
                                    "Steak Burrito"]),
    ("SPEC",   8,  (3.49, 7.99),  ["Crunchwrap Supreme", "Mexican Pizza", "Quesadilla",
                                    "Cheesy Gordita Crunch", "Chalupa Supreme",
                                    "Power Menu Bowl", "Chicken Chalupa", "Steak Chalupa"]),
    ("COMBO",  6,  (5.99, 11.99), ["$5 Cravings Box", "Build Your Own Cravings Box",
                                    "Deluxe Cravings Box", "Big Box",
                                    "Crunchwrap Combo", "Chalupa Combo"]),
    ("SIDE",   6,  (1.49, 4.99),  ["Cinnamon Twists", "Chips & Cheese", "Nachos",
                                    "Black Beans & Rice", "Cheesy Roll-Up",
                                    "Cinnabon Delights (4 ct)"]),
    ("DRINK", 12,  (1.99, 3.49),  ["Baja Blast Lg", "Baja Blast Md", "Coke Lg",
                                    "Coke Md", "Diet Coke Lg", "Diet Coke Md",
                                    "Sprite Md", "Sprite Lg", "Iced Tea Lg",
                                    "Iced Coffee", "Bottled Water", "Lemonade Md"]),
    ("BFAST", 10,  (2.49, 5.99),  ["Breakfast Crunchwrap", "Breakfast Burrito",
                                    "Hash Brown", "Cinnabon Delights (2 ct)",
                                    "Cinnabon Delights (4 ct, am)", "Bacon Breakfast Burrito",
                                    "Sausage Breakfast Burrito", "Steak Breakfast Burrito",
                                    "Breakfast Quesadilla", "Breakfast Combo"]),
]


def build_catalog() -> pd.DataFrame:
    rng = np.random.default_rng(P.RANDOM_SEED + 2)

    rows = []
    for ttype, count, (lo, hi), names in TYPES:
        for i in range(1, count + 1):
            sku = f"TBL-{ttype}-{i:02d}"
            name = names[i - 1] if i - 1 < len(names) else f"{ttype.title()} item {i}"
            base_price = round(float(rng.uniform(lo, hi)), 2)
            rows.append({
                "sku": sku,
                "merchant_id": "TBL",
                "name": name,
                "category": ttype,
                "subcategory": ttype.title(),
                "base_price": base_price,
                "type": ttype,
            })

    df = pd.DataFrame(rows).sort_values("sku").reset_index(drop=True)
    assert len(df) == 60, f"Taco Bell catalog size mismatch: {len(df)}"
    return df


ENTREE_TYPES = {"TACO", "BURR", "SPEC", "COMBO"}


def make_apply_affinity(catalog: pd.DataFrame):
    type_lookup = dict(zip(catalog["sku"], catalog["type"]))
    drinks = catalog.loc[catalog["type"] == "DRINK", "sku"].to_numpy()
    twists = catalog.loc[catalog["name"].str.contains("Cinnamon", na=False), "sku"].to_numpy()

    def apply(basket: list[str], rng: np.random.Generator) -> list[str]:
        types = {type_lookup.get(s) for s in basket}
        if types & ENTREE_TYPES and "DRINK" not in types:
            if rng.random() < 0.70 and len(drinks) > 0:
                basket.append(str(drinks[rng.integers(0, len(drinks))]))
        if "COMBO" in types and rng.random() < 0.35 and len(twists) > 0:
            twist = str(twists[rng.integers(0, len(twists))])
            if twist not in basket:
                basket.append(twist)
        return basket
    return apply
