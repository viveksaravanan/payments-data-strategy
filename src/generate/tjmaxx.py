"""TJ Maxx generator wrapper."""
from datetime import timedelta

import numpy as np
import pandas as pd

from . import base, catalog_tjmaxx, parameters as P

TJX_STORES = [
    ("NY", "northeast", "100",  1,  3),
    ("MA", "northeast", "020",  4,  6),
    ("TX", "south",     "750",  7,  9),
    ("CA", "west",      "900", 10, 12),
    ("FL", "south",     "320", 13, 15),
]


def build_stores(rng: np.random.Generator) -> pd.DataFrame:
    rows = []
    for state, region, zip3, lo, hi in TJX_STORES:
        for n in range(lo, hi + 1):
            zip5 = f"{zip3}{int(rng.integers(0, 100)):02d}"
            open_offset_days = int(rng.integers(365, 365 * 10))
            rows.append({
                "store_id":    f"TJX-{state}-{n:04d}",
                "merchant_id": "TJX",
                "store_zip5":  zip5,
                "region":      region,
                "open_date":   (P.END_DATE - timedelta(days=open_offset_days)).isoformat(),
            })
    return pd.DataFrame(rows)


def generate(customers: pd.DataFrame, rng: np.random.Generator):
    catalog = catalog_tjmaxx.build_catalog()
    stores = build_stores(rng)
    affinity_fn = catalog_tjmaxx.make_apply_affinity(catalog)
    config = P.MERCHANT_CONFIGS["tjmaxx"]
    txns, items = base.generate_merchant_transactions(
        customers=customers,
        catalog=catalog,
        stores=stores,
        config=config,
        affinity_fn=affinity_fn,
        rng=rng,
    )
    return catalog, stores, txns, items
