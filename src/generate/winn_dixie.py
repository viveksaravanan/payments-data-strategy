"""Winn-Dixie merchant data provider. Charlotte stores; grocery overlay catalog."""
from datetime import timedelta

import numpy as np
import pandas as pd

from . import catalog_winn_dixie, metro, parameters as P, promotions
from .anomalies import pinned_pasta_promo
from .transactions import MerchantData

WINN_DIXIE_REQUIRED_ZIPS = ("28213",)


def build_stores(rng: np.random.Generator) -> pd.DataFrame:
    n = P.MERCHANT_CONFIGS["winn_dixie"]["n_stores"]
    zips = metro.assign_store_zips(
        "grocery", n, rng, require_zips=WINN_DIXIE_REQUIRED_ZIPS
    )
    rows = []
    for i, zip5 in enumerate(zips, start=1):
        neighborhood, region = metro.neighborhood_for(zip5)
        lat, lon = metro.store_coords(zip5, rng)
        open_offset_days = int(rng.integers(365, 365 * 10))
        rows.append({
            "store_id":     f"WDX-NC-{i:04d}",
            "merchant_id":  "WDX",
            "store_zip5":   zip5,
            "neighborhood": neighborhood,
            "metro_region": region,
            "latitude":     lat,
            "longitude":    lon,
            "open_date":    (P.END_DATE - timedelta(days=open_offset_days)).isoformat(),
        })
    return pd.DataFrame(rows)


def build(rng: np.random.Generator) -> MerchantData:
    catalog = catalog_winn_dixie.build_catalog()
    stores = build_stores(rng)
    target = promotions.PROMO_COUNT_TARGETS["WDX"]
    random_promos = promotions.generate_for_merchant(
        catalog, "WDX", "grocery", rng, n_promos=target - 1
    )
    pinned = pinned_pasta_promo(catalog, "WDX", promo_id=f"WDX-PROMO-{target:04d}")
    promos = pd.concat([random_promos, pinned], ignore_index=True) if not pinned.empty else random_promos
    affinity_fn = catalog_winn_dixie.make_apply_affinity(catalog)
    return MerchantData(
        merchant_id="WDX",
        segment="grocery",
        catalog=catalog,
        stores=stores,
        promotions=promos,
        affinity_fn=affinity_fn,
    )
