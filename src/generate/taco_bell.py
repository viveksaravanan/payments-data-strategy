"""Taco Bell merchant data provider."""
from datetime import timedelta

import numpy as np
import pandas as pd

from . import catalog_taco_bell, metro, parameters as P, promotions
from .transactions import MerchantData


def build_stores(rng: np.random.Generator) -> pd.DataFrame:
    n = P.MERCHANT_CONFIGS["taco_bell"]["n_stores"]
    zips = metro.assign_store_zips("qsr", n, rng)
    rows = []
    for i, zip5 in enumerate(zips, start=1):
        neighborhood, region = metro.neighborhood_for(zip5)
        lat, lon = metro.store_coords(zip5, rng)
        open_offset_days = int(rng.integers(365, 365 * 10))
        rows.append({
            "store_id":     f"TBL-NC-{i:04d}",
            "merchant_id":  "TBL",
            "store_zip5":   zip5,
            "neighborhood": neighborhood,
            "metro_region": region,
            "latitude":     lat,
            "longitude":    lon,
            "open_date":    (P.END_DATE - timedelta(days=open_offset_days)).isoformat(),
        })
    return pd.DataFrame(rows)


def build(rng: np.random.Generator) -> MerchantData:
    catalog = catalog_taco_bell.build_catalog()
    stores = build_stores(rng)
    promos = promotions.generate_for_merchant(catalog, "TBL", "qsr", rng)
    affinity_fn = catalog_taco_bell.make_apply_affinity(catalog)
    return MerchantData(
        merchant_id="TBL",
        segment="qsr",
        catalog=catalog,
        stores=stores,
        promotions=promos,
        affinity_fn=affinity_fn,
    )
