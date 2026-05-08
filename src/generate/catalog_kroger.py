"""Kroger SKU catalog — loaded from per-category JSON files.

The catalog source-of-truth lives in `data/catalogs/kroger/<category>.json` —
twelve files, each a JSON array of SKU specs:

    {"name": ..., "subcategory": ..., "base_price": ..., "is_organic": bool,
     "ebt_eligible": bool}

This module loads the JSONs, assigns SKU codes of the form `KRG-<CATEGORY>-NNNN`
(within-category 1-based sequence), and produces the `tenant_products` row
shape. The SKU **prefix** format is stable (KRG-PRODUCE, KRG-DAIRY, ...) but
within-category numbering depends on the JSON order, so anchor SKUs for the
affinity rules and the planted avocado-price anomaly are resolved by **name
lookup** at run time, not by hard-coded SKU code.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

# `parameters` is imported lazily inside functions to avoid a circular import
# when this module is loaded as part of the package.

# Order matters — JSON_FILES sets the iteration order so the SKU sequence is
# deterministic. Use UPPERCASE category names (matches the rest of the schema).
CATALOG_DIR = Path(__file__).resolve().parents[2] / "data" / "catalogs" / "kroger"
JSON_FILES: list[tuple[str, Path]] = [
    ("PRODUCE",   CATALOG_DIR / "produce.json"),
    ("DAIRY",     CATALOG_DIR / "dairy.json"),
    ("BAKERY",    CATALOG_DIR / "bakery.json"),
    ("MEAT",      CATALOG_DIR / "meat.json"),
    ("FROZEN",    CATALOG_DIR / "frozen.json"),
    ("PANTRY",    CATALOG_DIR / "pantry.json"),
    ("SNACKS",    CATALOG_DIR / "snacks.json"),
    ("BEVERAGES", CATALOG_DIR / "beverages.json"),
    ("HOUSEHOLD", CATALOG_DIR / "household.json"),
    ("PERSONAL",  CATALOG_DIR / "personal.json"),
    ("BABY",      CATALOG_DIR / "baby.json"),
    ("PET",       CATALOG_DIR / "pet.json"),
]


def _load_json_items(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"Missing catalog file: {path}")
    with path.open() as f:
        return json.load(f)


def build_catalog() -> pd.DataFrame:
    """Build the Kroger catalog from the JSON files.

    Deterministic — same files in, same DataFrame out. No rng dependence.
    """
    rows: list[dict] = []
    expected_total = 0
    for category, path in JSON_FILES:
        items = _load_json_items(path)
        expected_total += len(items)
        for i, item in enumerate(items, start=1):
            sku = f"KRG-{category}-{i:04d}"
            rows.append({
                "sku": sku,
                "merchant_id": "KRG",
                "name": item["name"],
                "category": category,
                "subcategory": item["subcategory"],
                "is_organic": int(bool(item["is_organic"])),
                "base_price": float(item["base_price"]),
                "ebt_eligible": int(bool(item["ebt_eligible"])),
            })

    df = pd.DataFrame(rows).sort_values("sku").reset_index(drop=True)
    assert len(df) == expected_total, (
        f"Kroger catalog row count mismatch: built {len(df)}, expected {expected_total}"
    )
    return df


# ---------------------------------------------------------------------------
# Affinity rules — name-based anchors
# ---------------------------------------------------------------------------
# Each entry is (anchor_name_substring, companion_name_substring, P(companion|anchor)).
# Anchors are resolved against the loaded catalog at make_apply_affinity time;
# any unmatched anchor raises immediately so a JSON refactor can't silently
# disable an affinity rule.
KROGER_AFFINITY_BY_NAME: list[tuple[str, str, float]] = [
    ("Diapers size 3 Pampers",  "Infant formula Similac Advance", 0.45),
    ("Spaghetti (1 lb box)",    "Marinara sauce traditional",     0.55),
    ("Tortilla wraps",          "80/20 ground beef",              0.40),
    ("Tortilla wraps",          "Sharp cheddar shredded",         0.45),
    ("Whole milk (gallon)",     "Cheerios cereal (18 oz)",        0.30),
    ("Folgers ground coffee",   "Half and half (quart)",          0.40),
]


def _resolve_name(catalog: pd.DataFrame, substring: str) -> str:
    matches = catalog[catalog["name"].str.contains(substring, case=False, na=False, regex=False)]
    if matches.empty:
        raise ValueError(
            f"Affinity anchor not found in Kroger catalog: {substring!r}. "
            f"Check data/catalogs/kroger/*.json for an item whose name contains it."
        )
    return str(matches.iloc[0]["sku"])


def make_apply_affinity(catalog: pd.DataFrame):
    """Resolve substring anchors to specific SKU codes once, return a closure
    that applies the affinity rules to a single basket."""
    resolved: list[tuple[str, str, float]] = [
        (_resolve_name(catalog, anchor), _resolve_name(catalog, companion), prob)
        for anchor, companion, prob in KROGER_AFFINITY_BY_NAME
    ]

    def apply(basket: list[str], rng: np.random.Generator) -> list[str]:
        present = set(basket)
        for anchor_sku, companion_sku, prob in resolved:
            if anchor_sku in present and companion_sku not in present and rng.random() < prob:
                basket.append(companion_sku)
                present.add(companion_sku)
        return basket

    return apply
