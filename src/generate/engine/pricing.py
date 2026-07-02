"""Layer 7 — Pricing (datamodel-v2: FLAT shelf-price model, §C7).

Realized line price = the catalog's baked ``shelf_price``. The v1
transaction-time modifier chain (per-merchant category multiplier,
KVI-dampener / specialty-amplifier, PL factor, per-SKU competitive
index, zone effect, time drift, line noise) is **gone** — those
deterministic positioning levers are now baked into ``shelf_price`` by
the catalog authoring script (``scripts/build_catalog.py``). Pricing is
therefore fully deterministic and cross-banner differentiation
("no banner cheapest on everything", KVI-tight / specialty-wide, PL
discount) still holds — it's just carried by the shelf price.

The promo-discount hook is kept **dormant** (promotions disabled in
datamodel-v2): when a ``promo_depth_lookup`` is supplied a line's price
drops by the depth and ``promo_id`` is set; with the default empty
lookup every line is ``discount=0.0, promo_id=None``.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.generate.config.loader import Config


def build_priced_items(
    cfg: Config,
    basket_items: pd.DataFrame,
    catalog: pd.DataFrame,
    trips: pd.DataFrame,
    rng: np.random.Generator | None = None,
    *,
    promo_depth_lookup: dict | None = None,
    promo_id_lookup: dict | None = None,
) -> pd.DataFrame:
    """Price each basket line at the catalog ``shelf_price`` (flat).

    Returns one row per line with the datamodel-v2 line schema
    ``[trip_id, line_id, sku, qty, unit_price, discount, promo_id,
    line_total]`` — NO canonical_id / category / subcategory / name
    (those resolve via a join to ``products`` on ``sku``; the line stays
    minimal per §A12).
    """
    promo_depth_lookup = promo_depth_lookup or {}
    promo_id_lookup = promo_id_lookup or {}

    shelf = dict(zip(catalog["sku"], catalog["shelf_price"].astype(float)))
    items = basket_items[["trip_id", "line_id", "sku", "qty"]].copy()
    n = len(items)
    sku_arr = items["sku"].to_numpy()
    qty_arr = items["qty"].to_numpy()

    shelf_price = np.array([shelf.get(str(s), np.nan) for s in sku_arr], dtype=float)

    discount = np.zeros(n, dtype=float)
    promo_id = np.empty(n, dtype=object)      # all None (promos dormant)
    if promo_depth_lookup:                     # dormant path (empty by default)
        trip_dates = pd.to_datetime(
            items.merge(trips[["trip_id", "txn_ts"]], on="trip_id")["txn_ts"]
        ).dt.date.to_numpy()
        for i in range(n):
            depth = promo_depth_lookup.get((trip_dates[i], str(sku_arr[i])))
            if depth is not None:
                discount[i] = round(shelf_price[i] * depth, 2)
                promo_id[i] = promo_id_lookup.get((trip_dates[i], str(sku_arr[i])))

    unit_price = np.round(shelf_price - discount, 2)
    line_total = np.round(unit_price * qty_arr, 2)

    items["unit_price"] = unit_price
    items["discount"] = discount
    items["promo_id"] = promo_id
    items["line_total"] = line_total
    return items.sort_values(["trip_id", "line_id"], kind="mergesort").reset_index(drop=True)
