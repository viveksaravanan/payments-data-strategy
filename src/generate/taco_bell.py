"""Taco Bell generator wrapper."""
from datetime import timedelta

import numpy as np
import pandas as pd

from . import base, catalog_taco_bell, parameters as P

TBL_STORES = [
    ("CA", "west",      "900",  1,  8),
    ("TX", "south",     "750",  9, 16),
    ("NY", "northeast", "100", 17, 24),
    ("FL", "south",     "320", 25, 32),
    ("AZ", "west",      "850", 33, 40),
]


def build_stores(rng: np.random.Generator) -> pd.DataFrame:
    rows = []
    for state, region, zip3, lo, hi in TBL_STORES:
        for n in range(lo, hi + 1):
            zip5 = f"{zip3}{int(rng.integers(0, 100)):02d}"
            open_offset_days = int(rng.integers(365, 365 * 10))
            rows.append({
                "store_id":    f"TBL-{state}-{n:04d}",
                "merchant_id": "TBL",
                "store_zip5":  zip5,
                "region":      region,
                "open_date":   (P.END_DATE - timedelta(days=open_offset_days)).isoformat(),
            })
    return pd.DataFrame(rows)


def generate(customers: pd.DataFrame, rng: np.random.Generator):
    catalog = catalog_taco_bell.build_catalog()
    stores = build_stores(rng)
    affinity_fn = catalog_taco_bell.make_apply_affinity(catalog)
    config = P.MERCHANT_CONFIGS["taco_bell"]
    txns, items = base.generate_merchant_transactions(
        customers=customers,
        catalog=catalog,
        stores=stores,
        config=config,
        affinity_fn=affinity_fn,
        rng=rng,
    )
    return catalog, stores, txns, items
