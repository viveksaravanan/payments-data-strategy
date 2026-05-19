"""Charlotte-metro geography helpers.

Source of truth for the neighborhood/region/ZIP map is
`parameters.V2_5_NEIGHBORHOODS` (Layer 2 of `V2_5_DATA_DESIGN.md`). This
module adds the runtime helpers: ZIP centroid coordinates, ±0.02° jitter,
and per-grocer store-count distribution across neighborhoods.
"""
from __future__ import annotations

import numpy as np

from . import parameters as P

# Approximate centroids for each Charlotte ZIP5 in the panel. Values are
# rough lat/long centroids; jitter is applied per store. Used only by the
# generator — not consumed by tests beyond range checks.
_ZIP_CENTROIDS: dict[str, tuple[float, float]] = {
    "28202": (35.2271, -80.8431),  # Uptown
    "28203": (35.2096, -80.8534),  # Dilworth
    "28205": (35.2229, -80.8128),  # Plaza Midwood
    "28206": (35.2440, -80.8189),  # NoDa
    "28210": (35.1597, -80.8607),  # SouthPark south
    "28211": (35.1700, -80.8200),  # SouthPark east
    "28213": (35.3144, -80.7625),  # University City west
    "28223": (35.3056, -80.7331),  # University City east (UNCC)
    "28277": (35.0589, -80.8462),  # Ballantyne
    "28104": (35.0890, -80.7000),  # Matthews west
    "28105": (35.1170, -80.7100),  # Matthews east
    "28078": (35.4107, -80.8420),  # Huntersville
    "28134": (35.0824, -80.8923),  # Pineville
    "28025": (35.4087, -80.5790),  # Concord west
    "28027": (35.3970, -80.6800),  # Concord east
    "28115": (35.5840, -80.8100),  # Mooresville south
    "28117": (35.5800, -80.9100),  # Mooresville north
}

JITTER_DEG = 0.02


def neighborhood_for(zip5: str) -> tuple[str, str]:
    """Return (neighborhood, metro_region) for a Charlotte panel ZIP5."""
    return P.V2_5_ZIP5_TO_NEIGHBORHOOD[zip5]


def store_coords(zip5: str, rng: np.random.Generator) -> tuple[float, float]:
    """Sample a (lat, long) within ±JITTER_DEG of the ZIP's centroid."""
    if zip5 not in _ZIP_CENTROIDS:
        raise KeyError(f"No centroid for ZIP {zip5}; not in Charlotte panel.")
    lat0, lon0 = _ZIP_CENTROIDS[zip5]
    dlat = float(rng.uniform(-JITTER_DEG, JITTER_DEG))
    dlon = float(rng.uniform(-JITTER_DEG, JITTER_DEG))
    return round(lat0 + dlat, 5), round(lon0 + dlon, 5)


# Per-merchant ZIP allocation. Each merchant places its `n_stores` across
# the metro by repeatedly drawing from a weighted ZIP list. Weights bias
# urban-core ZIPs for grocers (denser routes), QSR distribution towards
# arterial outer suburbs, and TJ Maxx towards inner suburbs.
#
# The exact distribution is approximate; what matters for the v2.5 demo
# is that each neighborhood (especially University City and Plaza
# Midwood, both Phase-6 anomaly targets) ends up with at least one store
# of each grocer.

# Region-tier base weights per segment.
_TIER_WEIGHTS = {
    "grocery":          {"urban_core": 1.2, "inner_suburbs": 1.4, "outer_suburbs": 1.0},
    "qsr":              {"urban_core": 1.0, "inner_suburbs": 1.2, "outer_suburbs": 1.4},
    "off_price_retail": {"urban_core": 0.6, "inner_suburbs": 1.6, "outer_suburbs": 1.0},
}


def _tier_weighted_zip_pool(
    segment: str, merchant_id: str | None = None
) -> tuple[list[str], np.ndarray]:
    weights = _TIER_WEIGHTS[segment]
    nbhd_bias = (
        P.MERCHANT_NEIGHBORHOOD_BIAS.get(merchant_id, {})
        if merchant_id is not None else {}
    )
    zips: list[str] = []
    probs: list[float] = []
    for neighborhood, (region, zip_list) in P.V2_5_NEIGHBORHOODS.items():
        bias = nbhd_bias.get(neighborhood, 1.0)
        w = (weights[region] * bias) / max(len(zip_list), 1)
        for z in zip_list:
            zips.append(z)
            probs.append(w)
    arr = np.asarray(probs, dtype=float)
    arr = arr / arr.sum()
    return zips, arr


def assign_store_zips(
    segment: str,
    n_stores: int,
    rng: np.random.Generator,
    *,
    require_zips: tuple[str, ...] = (),
    merchant_id: str | None = None,
) -> list[str]:
    """Pick `n_stores` ZIPs for a merchant in `segment`.

    `require_zips`: ZIPs that MUST receive at least one store (used to
    guarantee the shared comparison footprint and Phase 6 anomaly
    targets).

    `merchant_id`: if provided, `MERCHANT_NEIGHBORHOOD_BIAS` is applied
    to the per-neighborhood probabilities (Pass 2). None preserves the
    segment-only behavior.
    """
    zips, probs = _tier_weighted_zip_pool(segment, merchant_id=merchant_id)
    if n_stores < len(require_zips):
        raise ValueError(
            f"Cannot place {len(require_zips)} required ZIPs into "
            f"{n_stores} stores."
        )

    out: list[str] = list(require_zips)
    remaining = n_stores - len(require_zips)
    if remaining > 0:
        sampled = rng.choice(zips, size=remaining, p=probs, replace=True)
        out.extend(sampled.tolist())

    rng.shuffle(out)
    return out
