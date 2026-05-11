"""One-off generator for the v2.5 grocery catalog files.

Reads the existing Kroger per-category JSONs in `data/catalogs/kroger/`
and produces:

  - data/catalogs/base_grocery_catalog.json — the canonical SKU universe
    (~1,112 entries) shared by Kroger, Acme, and Winn-Dixie.
  - data/catalogs/overlays/{kroger,acme,winn_dixie}.json — each lists
    the canonical SKUs the grocer carries and the per-tier price
    multipliers used at catalog-build time.

Determinism: the per-grocer SKU subsetting uses a fixed numpy seed so
re-running this script produces the same output. Re-run only when the
canonical universe or per-grocer SKU shares change deliberately.

    uv run python scripts/build_v2_5_catalogs.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
CATALOGS = ROOT / "data" / "catalogs"
KROGER_JSONS = CATALOGS / "kroger"
OVERLAYS = CATALOGS / "overlays"

# Category-iteration order (matches the previous catalog_kroger.JSON_FILES
# ordering so canonical SKU codes stay stable).
CATEGORY_ORDER = [
    "PRODUCE", "DAIRY", "BAKERY", "MEAT", "FROZEN", "PANTRY",
    "SNACKS", "BEVERAGES", "HOUSEHOLD", "PERSONAL", "BABY", "PET",
]

# Two-tier categorization (V2_5_DATA_DESIGN.md Phase 1, Layer 3c).
TIGHT_CATEGORIES = {"DAIRY", "BAKERY", "PRODUCE", "MEAT", "PANTRY", "SNACKS",
                    "BEVERAGES", "FROZEN"}
LOOSE_CATEGORIES = {"HOUSEHOLD", "PERSONAL", "BABY", "PET"}

# Per-grocer overlay parameters.
#   `keep_fraction_per_category`: fraction of canonical SKUs the grocer
#       carries within each category. 1.0 = carries all; 0.8 = drops 20%.
#   `tight_multiplier` / `loose_multiplier`: per-tier price multipliers
#       applied at catalog-build time. Per-SKU ±2% noise is applied on top
#       in `catalog_grocery.py`.
GROCERS = {
    "kroger": {
        "merchant_id": "KRG",
        "keep_fraction": 1.00,        # baseline: carries the full canonical universe
        "tight_multiplier": 1.00,
        "loose_multiplier": 1.00,
    },
    "acme": {
        "merchant_id": "ACM",
        "keep_fraction": 0.90,        # ~1,000 SKUs out of ~1,112
        "tight_multiplier": 1.03,
        "loose_multiplier": 1.07,
    },
    "winn_dixie": {
        "merchant_id": "WDX",
        "keep_fraction": 0.79,        # ~880 SKUs
        "tight_multiplier": 0.97,
        "loose_multiplier": 0.93,
    },
}

OVERLAY_SEED = 1492  # used for deterministic SKU subsetting per grocer


def _load_kroger_categories() -> dict[str, list[dict]]:
    """Return {CATEGORY: [item, ...]} preserving JSON-file order."""
    out: dict[str, list[dict]] = {}
    for cat in CATEGORY_ORDER:
        path = KROGER_JSONS / f"{cat.lower()}.json"
        with path.open() as f:
            out[cat] = json.load(f)
    return out


def _build_base_catalog(by_category: dict[str, list[dict]]) -> list[dict]:
    """Flatten into a list of canonical SKU records."""
    rows: list[dict] = []
    for cat in CATEGORY_ORDER:
        for i, item in enumerate(by_category[cat], start=1):
            rows.append({
                "canonical_sku": f"{cat}-{i:04d}",
                "name": item["name"],
                "category": cat,
                "subcategory": item["subcategory"],
                "base_price": float(item["base_price"]),
            })
    return rows


def _build_overlay(
    base: list[dict], grocer_key: str, params: dict, rng: np.random.Generator,
) -> dict:
    """Pick a stable subset of the canonical SKUs for this grocer.

    Subsetting is per-category so each category retains the same fraction.
    For Kroger (keep_fraction=1.0) the subset is the full canonical list.
    """
    keep = params["keep_fraction"]
    by_cat: dict[str, list[str]] = {}
    for sku in base:
        by_cat.setdefault(sku["category"], []).append(sku["canonical_sku"])

    included: list[str] = []
    for cat in CATEGORY_ORDER:
        canonical = by_cat[cat]
        if keep >= 0.999:
            included.extend(canonical)
            continue
        n_keep = max(1, int(round(len(canonical) * keep)))
        chosen = rng.choice(len(canonical), size=n_keep, replace=False)
        included.extend(sorted(canonical[i] for i in chosen.tolist()))

    return {
        "merchant_id":       params["merchant_id"],
        "keep_fraction":     keep,
        "tight_multiplier":  params["tight_multiplier"],
        "loose_multiplier":  params["loose_multiplier"],
        "tight_categories":  sorted(TIGHT_CATEGORIES),
        "loose_categories":  sorted(LOOSE_CATEGORIES),
        "included_canonical_skus": sorted(included),
    }


def main() -> None:
    OVERLAYS.mkdir(parents=True, exist_ok=True)
    by_category = _load_kroger_categories()
    base = _build_base_catalog(by_category)

    base_path = CATALOGS / "base_grocery_catalog.json"
    base_path.write_text(json.dumps(base, indent=2))
    print(f"wrote {base_path}: {len(base):,} canonical SKUs")

    rng = np.random.default_rng(OVERLAY_SEED)
    for grocer_key, params in GROCERS.items():
        # Each grocer gets its own substream so adding/removing a grocer
        # doesn't shift the others' SKU selections.
        sub_rng = np.random.default_rng(rng.integers(0, 2**31 - 1))
        overlay = _build_overlay(base, grocer_key, params, sub_rng)
        out = OVERLAYS / f"{grocer_key}.json"
        out.write_text(json.dumps(overlay, indent=2))
        print(
            f"wrote {out}: {len(overlay['included_canonical_skus']):,} SKUs "
            f"(tight×{overlay['tight_multiplier']:.2f}, "
            f"loose×{overlay['loose_multiplier']:.2f})"
        )


if __name__ == "__main__":
    main()
