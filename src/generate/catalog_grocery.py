"""Grocery SKU catalog — base + per-grocer overlay.

Replaces the v2 ``catalog_kroger.py`` with a model that scales to multiple
grocers:

  - ``data/catalogs/base_grocery_catalog.json`` — canonical SKU universe.
  - ``data/catalogs/overlays/{kroger,acme,winn_dixie}.json`` — per-grocer
    inclusion list and per-tier multipliers.

For a given merchant, ``build_catalog(merchant_key, rng)`` joins the base
catalog with the overlay, applies the tier multipliers, and adds ±2%
per-SKU noise. Output shape matches ``tenant_products``: ``sku``,
``merchant_id``, ``name``, ``category``, ``subcategory``, ``base_price``.

SKU codes are merchant-prefixed: ``<MERCHANT_ID>-<CATEGORY>-<NNNN>`` where
``NNNN`` comes from the canonical_sku (so the same canonical product gets
the same numeric suffix across grocers — useful when scanning the data).

The shared affinity rules (anchor → companion) live in this module too.
They're keyed off canonical product names so they apply to whichever
grocers carry both anchor and companion.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

CATALOG_DIR = Path(__file__).resolve().parents[2] / "data" / "catalogs"
BASE_PATH = CATALOG_DIR / "base_grocery_catalog.json"
OVERLAY_DIR = CATALOG_DIR / "overlays"

OVERLAY_KEYS: dict[str, str] = {
    "kroger":     "kroger.json",
    "acme":       "acme.json",
    "winn_dixie": "winn_dixie.json",
}


def _load_base() -> list[dict]:
    with BASE_PATH.open() as f:
        return json.load(f)


def _load_overlay(merchant_key: str) -> dict:
    if merchant_key not in OVERLAY_KEYS:
        raise ValueError(
            f"Unknown grocer overlay {merchant_key!r}; expected one of "
            f"{sorted(OVERLAY_KEYS)}."
        )
    with (OVERLAY_DIR / OVERLAY_KEYS[merchant_key]).open() as f:
        return json.load(f)


def build_catalog(
    merchant_key: str, rng: np.random.Generator | None = None
) -> pd.DataFrame:
    """Build the per-grocer catalog DataFrame.

    Parameters
    ----------
    merchant_key
        ``"kroger"`` / ``"acme"`` / ``"winn_dixie"``.
    rng
        Optional generator for the per-SKU ±2% price noise. If omitted,
        a deterministic seed derived from the merchant_id is used so the
        catalog is reproducible across runs.
    """
    base = _load_base()
    overlay = _load_overlay(merchant_key)
    merchant_id = overlay["merchant_id"]
    tight_mult = float(overlay["tight_multiplier"])
    loose_mult = float(overlay["loose_multiplier"])
    tight_set = set(overlay["tight_categories"])
    loose_set = set(overlay["loose_categories"])
    included = set(overlay["included_canonical_skus"])

    if rng is None:
        # Stable per-merchant seed; matches across runs without coupling
        # to the global RANDOM_SEED.
        seed_hash = sum(b * 31 ** i for i, b in enumerate(merchant_id.encode()))
        rng = np.random.default_rng(seed_hash & 0xFFFFFFFF)

    rows: list[dict] = []
    for canonical in base:
        if canonical["canonical_sku"] not in included:
            continue
        category = canonical["category"]
        if category in tight_set:
            mult = tight_mult
        elif category in loose_set:
            mult = loose_mult
        else:
            raise ValueError(
                f"Category {category!r} not in tight or loose tier — "
                f"check overlay metadata."
            )
        # Per-SKU ±2% noise (V2_5_DATA_DESIGN.md Phase 1 Layer 3c).
        noise = 1.0 + (rng.random() * 2 - 1) * 0.02
        price = round(canonical["base_price"] * mult * noise, 2)
        # Merchant-prefixed SKU code, preserving the canonical SKU's
        # category and within-category index.
        sku = f"{merchant_id}-{canonical['canonical_sku']}"
        rows.append({
            "sku":         sku,
            "merchant_id": merchant_id,
            "name":        canonical["name"],
            "category":    category,
            "subcategory": canonical["subcategory"],
            "base_price":  price,
        })

    df = pd.DataFrame(rows).sort_values("sku").reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# Shared affinity rules (anchor → companion).
# Keyed off canonical product names so they apply to any grocer that
# carries both products. Phase 1 had these only at Kroger; in Phase 2
# they apply across KRG/ACM/WDX consistently.
# ---------------------------------------------------------------------------

GROCERY_AFFINITY_BY_NAME: list[tuple[str, str, float]] = [
    ("Diapers size 3 Pampers",  "Infant formula Similac Advance", 0.45),
    ("Spaghetti (1 lb box)",    "Marinara sauce traditional",     0.55),
    ("Tortilla wraps",          "80/20 ground beef",              0.40),
    ("Tortilla wraps",          "Sharp cheddar shredded",         0.45),
    ("Whole milk (gallon)",     "Cheerios cereal (18 oz)",        0.30),
    ("Folgers ground coffee",   "Half and half (quart)",          0.40),
]


def _resolve_name(catalog: pd.DataFrame, substring: str) -> str | None:
    """Return the SKU whose name contains `substring`, or None if missing.

    Returns None (not raises) so an affinity rule whose anchor or
    companion isn't carried by this grocer is silently dropped — without
    hiding the case where neither grocer carries it (caller logs).
    """
    matches = catalog[
        catalog["name"].str.contains(substring, case=False, na=False, regex=False)
    ]
    if matches.empty:
        return None
    return str(matches.iloc[0]["sku"])


def make_apply_affinity(catalog: pd.DataFrame):
    """Resolve substring anchors to specific SKU codes once and return a
    closure that applies the surviving affinity rules to a single basket.
    """
    resolved: list[tuple[str, str, float]] = []
    for anchor_substring, companion_substring, prob in GROCERY_AFFINITY_BY_NAME:
        anchor_sku = _resolve_name(catalog, anchor_substring)
        companion_sku = _resolve_name(catalog, companion_substring)
        if anchor_sku is None or companion_sku is None:
            # Rule is silently inactive for this grocer — companion may
            # not be carried. That's expected for narrower overlays.
            continue
        resolved.append((anchor_sku, companion_sku, prob))

    def apply(basket: list[str], rng: np.random.Generator) -> list[str]:
        present = set(basket)
        for anchor_sku, companion_sku, prob in resolved:
            if (anchor_sku in present
                    and companion_sku not in present
                    and rng.random() < prob):
                basket.append(companion_sku)
                present.add(companion_sku)
        return basket

    return apply
