"""Per-merchant promotion generation.

Produces ``tenant_promotions`` rows: ``promo_id``, ``merchant_id``,
``sku``, ``start_date``, ``end_date``, ``discount_pct``, ``promo_name``,
``promo_type``. Each promo covers multiple SKUs → one row per
(promo_id, sku).

Targets per `V2_5_DATA_DESIGN.md` Phase 1 Layer 3f:

  | Merchant   | Promos / 90 days |
  |------------|------------------|
  | Kroger     | ~25              |
  | Acme       | ~20              |
  | Winn-Dixie | ~18              |
  | Taco Bell  | ~6               |
  | TJ Maxx    | ~4               |

Counts include the three pinned pasta-promo rows (Kroger Apr 15–21,
Acme Apr 19–25, Winn-Dixie Apr 22–28) injected by the per-grocer
``build()`` from ``src/generate/anomalies/acme_pasta_promo.py``; this
module produces ``target - 1`` random promos per grocer and the
anomalies module supplies the final pinned promo so totals stay at
the design target.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable

import numpy as np
import pandas as pd

from . import parameters as P

# Per-segment promo-type mix. Grocery emphasizes weekly-ad cadence; QSR
# is mostly LTOs (limited-time menu items); off-price retail leans on
# clearance.
PROMO_TYPE_MIX: dict[str, dict[str, float]] = {
    "grocery":          {"weekly_ad": 0.55, "holiday": 0.15, "lto": 0.20, "clearance": 0.10},
    "qsr":              {"lto": 0.65, "holiday": 0.20, "weekly_ad": 0.15, "clearance": 0.00},
    "off_price_retail": {"clearance": 0.55, "lto": 0.30, "holiday": 0.15, "weekly_ad": 0.00},
}

# Inclusive-day duration ranges per promo type.
DURATION_DAYS: dict[str, tuple[int, int]] = {
    "weekly_ad": (7, 7),
    "holiday":   (5, 7),
    "lto":       (14, 21),
    "clearance": (21, 30),
}

# Number of SKUs covered by a single promo. Phase 4 retuned to hit the
# design's "Discount line items: ~15% grocery / ~5% QSR / ~3% retail
# (±3pp)" target after the per-line 85% application probability is
# applied. Per-segment overrides apply because catalogs differ wildly
# in size (TBL ~60 SKUs vs KRG ~1,100).
SKUS_PER_PROMO: dict[str, tuple[int, int]] = {
    "weekly_ad": (70, 140),
    "holiday":   (100, 180),
    "lto":       (8, 18),
    "clearance": (25, 50),
}

# Per-segment override for SKU coverage. Capped to a fraction of the
# catalog so that QSR (60 SKUs) and TJ Maxx (200 SKUs) don't end up
# with promos that effectively cover the whole catalog.
SKUS_PER_PROMO_BY_SEGMENT: dict[str, dict[str, tuple[int, int]]] = {
    "grocery": SKUS_PER_PROMO,
    "qsr": {
        "weekly_ad": (3, 8),
        "holiday":   (4, 8),
        "lto":       (3, 6),
        "clearance": (3, 5),
    },
    "off_price_retail": {
        "weekly_ad": (8, 18),
        "holiday":   (10, 20),
        "lto":       (4, 10),
        "clearance": (8, 18),
    },
}

# Discount depth (fraction off list price).
DISCOUNT_DEPTH: dict[str, tuple[float, float]] = {
    "weekly_ad": (0.10, 0.25),
    "holiday":   (0.15, 0.30),
    "lto":       (0.10, 0.20),
    "clearance": (0.20, 0.40),
}

# Per-merchant promo-count targets. Each grocer's `build()` reserves
# one slot for the pinned pasta promo (Phase 6 anomaly); totals stay
# at these targets.
PROMO_COUNT_TARGETS: dict[str, int] = {
    "KRG": 25,
    "ACM": 20,
    "WDX": 18,
    "TBL": 6,
    "TJX": 4,
}


def _name_for(promo_type: str, merchant_id: str, idx: int) -> str:
    """A short human-readable campaign label for grouping by promo_name."""
    if promo_type == "weekly_ad":
        return f"{merchant_id} Weekly Ad #{idx + 1}"
    if promo_type == "holiday":
        return f"{merchant_id} Spring Event #{idx + 1}"
    if promo_type == "lto":
        return f"{merchant_id} Limited-Time Offer #{idx + 1}"
    if promo_type == "clearance":
        return f"{merchant_id} Clearance #{idx + 1}"
    return f"{merchant_id} Promotion #{idx + 1}"


def _sample_promo_type(rng: np.random.Generator, segment: str) -> str:
    mix = PROMO_TYPE_MIX[segment]
    keys = [k for k, p in mix.items() if p > 0]
    probs = np.asarray([mix[k] for k in keys], dtype=float)
    probs = probs / probs.sum()
    return str(rng.choice(keys, p=probs))


def _sample_window(
    rng: np.random.Generator, promo_type: str, start: date, end: date
) -> tuple[date, date]:
    dur_lo, dur_hi = DURATION_DAYS[promo_type]
    duration = int(rng.integers(dur_lo, dur_hi + 1))
    days_in_window = (end - start).days + 1
    max_offset = max(0, days_in_window - duration)
    offset = int(rng.integers(0, max_offset + 1))
    s = start + timedelta(days=offset)
    e = s + timedelta(days=duration - 1)
    if e > end:
        e = end
    return s, e


def generate_for_merchant(
    catalog: pd.DataFrame,
    merchant_id: str,
    segment: str,
    rng: np.random.Generator,
    n_promos: int | None = None,
    *,
    window_start: date | None = None,
    window_end: date | None = None,
) -> pd.DataFrame:
    """Build the tenant_promotions DataFrame for a single merchant.

    `catalog` is the merchant's tenant_products DataFrame (must include
    ``sku``). `segment` controls the promo-type mix. `n_promos` defaults
    to the per-merchant target. The output has one row per (promo, sku).
    """
    if segment not in PROMO_TYPE_MIX:
        raise ValueError(f"Unknown segment {segment!r}")
    if n_promos is None:
        n_promos = PROMO_COUNT_TARGETS[merchant_id]
    if window_start is None:
        window_start = P.START_DATE
    if window_end is None:
        window_end = P.END_DATE

    sku_pool = catalog["sku"].to_numpy()
    sku_count_table = SKUS_PER_PROMO_BY_SEGMENT.get(segment, SKUS_PER_PROMO)
    rows: list[dict] = []
    for i in range(n_promos):
        promo_type = _sample_promo_type(rng, segment)
        start, end = _sample_window(rng, promo_type, window_start, window_end)
        n_lo, n_hi = sku_count_table[promo_type]
        n_skus = min(int(rng.integers(n_lo, n_hi + 1)), len(sku_pool))
        chosen = rng.choice(sku_pool, size=n_skus, replace=False)
        d_lo, d_hi = DISCOUNT_DEPTH[promo_type]
        discount_pct = round(float(rng.uniform(d_lo, d_hi)), 2)
        promo_id = f"{merchant_id}-PROMO-{i + 1:04d}"
        promo_name = _name_for(promo_type, merchant_id, i)
        for sku in chosen:
            rows.append({
                "promo_id":     promo_id,
                "merchant_id":  merchant_id,
                "sku":          str(sku),
                "start_date":   start.isoformat(),
                "end_date":     end.isoformat(),
                "discount_pct": discount_pct,
                "promo_name":   promo_name,
                "promo_type":   promo_type,
            })
    return pd.DataFrame(rows)


def build_promo_lookup(
    promotions: pd.DataFrame,
    days_in_window: int,
    window_start: date,
) -> dict[int, dict[str, tuple[str, float]]]:
    """Pre-compute (day_offset, sku) → (promo_id, discount_pct) lookup.

    Returned as a dict-of-dicts keyed by day offset from `window_start`.
    Used by `base.py` to resolve a line item's promo in O(1).
    """
    out: dict[int, dict[str, tuple[str, float]]] = {}
    if promotions is None or promotions.empty:
        return out
    for row in promotions.itertuples(index=False):
        s = date.fromisoformat(row.start_date)
        e = date.fromisoformat(row.end_date)
        sku = str(row.sku)
        promo_id = str(row.promo_id)
        discount_pct = float(row.discount_pct)
        d = s
        while d <= e:
            offset = (d - window_start).days
            if 0 <= offset < days_in_window:
                # If multiple promos overlap on the same SKU/day, the
                # last one wins. The pinned pasta promos are appended
                # last by `build()`, so they take precedence on
                # overlaps with random promos.
                out.setdefault(offset, {})[sku] = (promo_id, discount_pct)
            d += timedelta(days=1)
    return out
