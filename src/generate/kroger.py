"""Kroger merchant data provider.

Phase 4: per-merchant modules expose `build(rng)` returning a
`MerchantData` bundle. Transaction generation is unified in
`src/generate/transactions.py`.

Kroger forces stores in 28205 (Plaza Midwood) and 28213 (University City)
so the Phase 6 anomaly anchors have local foot traffic. Phase 1.6 Pass 2
extends the required-zip set to enforce a 5-neighborhood shared
comparison footprint across all three grocers (≥2 stores each in
Dilworth, SouthPark, University City, Ballantyne, Plaza Midwood).
"""
from datetime import timedelta

import numpy as np
import pandas as pd

from . import catalog_grocery, metro, parameters as P, promotions
from .anomalies import pinned_pasta_promo
from .transactions import MerchantData

# Shared 5-neighborhood comparison footprint (≥2 stores each):
# Dilworth (28203 ×2), SouthPark (28210 + 28211), University City
# (28213 + 28223), Ballantyne (28277 ×2), Plaza Midwood (28205 ×2).
SHARED_FOOTPRINT_ZIPS = (
    "28203", "28203",
    "28210", "28211",
    "28213", "28223",
    "28277", "28277",
    "28205", "28205",
)

KROGER_REQUIRED_ZIPS = SHARED_FOOTPRINT_ZIPS


def build_stores(rng: np.random.Generator) -> pd.DataFrame:
    n = P.MERCHANT_CONFIGS["kroger"]["n_stores"]
    zips = metro.assign_store_zips(
        "grocery", n, rng,
        require_zips=KROGER_REQUIRED_ZIPS,
        merchant_id="KRG",
    )
    rows = []
    for i, zip5 in enumerate(zips, start=1):
        neighborhood, region = metro.neighborhood_for(zip5)
        lat, lon = metro.store_coords(zip5, rng)
        open_offset_days = int(rng.integers(365, 365 * 10))
        rows.append({
            "store_id":     f"KRG-NC-{i:04d}",
            "merchant_id":  "KRG",
            "store_zip5":   zip5,
            "neighborhood": neighborhood,
            "metro_region": region,
            "latitude":     lat,
            "longitude":    lon,
            "open_date":    (P.END_DATE - timedelta(days=open_offset_days)).isoformat(),
        })
    return pd.DataFrame(rows)


def build(rng: np.random.Generator) -> MerchantData:
    catalog = catalog_grocery.build_catalog("kroger")
    stores = build_stores(rng)
    # Reserve one slot for the pinned pasta promo (Phase 6 anomaly).
    target = P.MERCHANT_CONFIGS["kroger"].get("n_promos") or promotions.PROMO_COUNT_TARGETS["KRG"]
    random_promos = promotions.generate_for_merchant(
        catalog, "KRG", "grocery", rng, n_promos=target - 1
    )
    pinned = pinned_pasta_promo(catalog, "KRG", promo_id=f"KRG-PROMO-{target:04d}")
    promos = pd.concat([random_promos, pinned], ignore_index=True) if not pinned.empty else random_promos
    affinity_fn = catalog_grocery.make_apply_affinity(catalog)
    return MerchantData(
        merchant_id="KRG",
        segment="grocery",
        catalog=catalog,
        stores=stores,
        promotions=promos,
        affinity_fn=affinity_fn,
    )
