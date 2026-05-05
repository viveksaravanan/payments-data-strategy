"""Kroger generator wrapper.

Builds Kroger stores, loads the Kroger catalog, and calls the parameterized
basket/transaction generator. Includes ``KRG-OH-0011`` (referenced by the
store-dropout anomaly).
"""
from datetime import timedelta

import numpy as np
import pandas as pd

from . import base, catalog_kroger, parameters as P

# State -> store-id range. Numbering is contiguous across the merchant; OH gets
# 0001–0011 so that `KRG-OH-0011` (the dropout anomaly target) exists.
KROGER_STORES = [
    ("OH", "midwest", "432", 1, 11),
    ("MI", "midwest", "480", 12, 16),
    ("KY", "south",   "401", 17, 20),
    ("IN", "midwest", "462", 21, 23),
    ("TN", "south",   "372", 24, 25),
]


def build_stores(rng: np.random.Generator) -> pd.DataFrame:
    rows = []
    for state, region, zip3, lo, hi in KROGER_STORES:
        for n in range(lo, hi + 1):
            store_id = f"KRG-{state}-{n:04d}"
            zip5 = f"{zip3}{int(rng.integers(0, 100)):02d}"
            open_offset_days = int(rng.integers(365, 365 * 10))
            open_date = (P.END_DATE - timedelta(days=open_offset_days)).isoformat()
            rows.append({
                "store_id":   store_id,
                "merchant_id": "KRG",
                "store_zip5": zip5,
                "region":     region,
                "open_date":  open_date,
            })
    return pd.DataFrame(rows)


def generate(customers: pd.DataFrame, rng: np.random.Generator):
    catalog = catalog_kroger.build_catalog()
    stores = build_stores(rng)
    affinity_fn = catalog_kroger.make_apply_affinity(catalog)
    config = P.MERCHANT_CONFIGS["kroger"]
    txns, items = base.generate_merchant_transactions(
        customers=customers,
        catalog=catalog,
        stores=stores,
        config=config,
        affinity_fn=affinity_fn,
        rng=rng,
    )
    return catalog, stores, txns, items
