"""Layer 1 — Geography & Zones (D13).

Reads metro + merchant configs; emits two DataFrames:

* ``build_zones(cfg)`` — the 8 zones as a DataFrame with the
  D13.1 profile columns and centroid lat/long.
* ``build_stores(cfg, rng)`` — 29 stores placed per the D13.2
  matrix, each at its zone centroid ± a jitter (±0.02° per
  v3 baseline), with stable store_id ``<banner>-NC-<NNNN>``.

The frames are the input to Layer 2 (population — home-zone
weights) and Layer 4 (trips — gravity geometry). Stage 4.4 uses
the centroids and segment-level distance-decay β to compute
store-choice probabilities; Stage 4.1 just defines where everything
sits.

Reproducibility (T18): a single seeded RNG is threaded in; iteration
order over merchants and zones is the sorted config order, so two
runs against the same seed produce identical store frames.
"""
from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd

from src.generate.config.loader import Config


# Jitter envelope around the zone centroid for store coordinates
# (D13.4 / v3 baseline). Small enough that stores stay in their
# named neighborhood, large enough to differentiate two stores in
# the same zone for the gravity model.
JITTER_DEG = 0.02

# Per-merchant store lifetime range in days: 1y – 10y before the
# data window starts. Matches the v3 baseline so legacy AUV math
# carries over.
OPEN_DATE_MIN_DAYS = 365
OPEN_DATE_MAX_DAYS = 365 * 10


def _density_to_metro_region(density: str) -> str:
    """Categorical bucket: dense → urban_core, mid → inner_suburbs,
    sparse → outer_suburbs. Matches the v3 taxonomy."""
    d = (density or "").lower()
    if d == "high":
        return "urban_core"
    if d in ("medium", "med-high"):
        return "inner_suburbs"
    if d in ("low", "low-med"):
        return "outer_suburbs"
    raise ValueError(f"unknown zone density {density!r}")


def build_zones(cfg: Config) -> pd.DataFrame:
    """Emit the 8-zone reference table.

    Column names mirror the YAML keys, except the centroid is
    flattened to ``centroid_lat`` / ``centroid_long`` for the join
    with the stores frame.
    """
    rows: list[dict] = []
    for z in cfg.zones:
        rows.append({
            "zone_id":            z["id"],
            "name":               z["name"],
            "archetype":          z["archetype"],
            "affluence":          z["affluence"],
            "residential_weight": z["residential_weight"],
            "density":            z["density"],
            "household_skew":     z["household_skew"],
            "age_skew":           z["age_skew"],
            "centroid_lat":       z["centroid"]["lat"],
            "centroid_long":      z["centroid"]["long"],
        })
    return pd.DataFrame(rows)


def _store_jitter(rng: np.random.Generator) -> tuple[float, float]:
    """Symmetric jitter in degrees around the zone centroid."""
    return (
        float(rng.uniform(-JITTER_DEG, JITTER_DEG)),
        float(rng.uniform(-JITTER_DEG, JITTER_DEG)),
    )


def build_stores(cfg: Config, rng: np.random.Generator) -> pd.DataFrame:
    """Place all merchants' stores per the D13.2 placement matrix.

    Iteration order is sorted by merchant name (for reproducibility)
    and within a merchant by sorted zone id, then by seat index within
    the zone. The store_id sequence is per-banner, zero-padded to four
    digits.
    """
    zones_df = build_zones(cfg).set_index("zone_id")
    window_start = pd.Timestamp(cfg.global_["window"]["start_date"])

    rows: list[dict] = []
    for merchant_name in sorted(cfg.merchants):
        m = cfg.merchants[merchant_name]
        banner = m["banner_code"]
        merchant_id = banner  # kept as banner_code for now (v3 used 3-char codes too)
        bias = m["zone_placement_bias"] or {}
        seat = 0
        for zone_id in sorted(bias):
            count = int(bias[zone_id])
            if zone_id not in zones_df.index:
                # Should be caught by config invariants, but guard anyway.
                raise KeyError(f"merchant {merchant_name} places stores in unknown zone {zone_id!r}")
            z = zones_df.loc[zone_id]
            for _ in range(count):
                seat += 1
                lat_jit, long_jit = _store_jitter(rng)
                open_days_back = int(rng.integers(OPEN_DATE_MIN_DAYS, OPEN_DATE_MAX_DAYS + 1))
                open_date = (window_start - pd.Timedelta(days=open_days_back)).date()
                rows.append({
                    "store_id":     f"{banner}-NC-{seat:04d}",
                    "merchant_id":  merchant_id,
                    "banner_code":  banner,
                    "zone_id":      zone_id,
                    "neighborhood": z["name"],
                    "metro_region": _density_to_metro_region(z["density"]),
                    "latitude":     float(z["centroid_lat"]) + lat_jit,
                    "longitude":    float(z["centroid_long"]) + long_jit,
                    "open_date":    open_date,
                })

    df = pd.DataFrame(rows)
    # Stable on-disk order: by banner then seat. Already the iteration
    # order above, but make it explicit so downstream sorts agree.
    df = df.sort_values(["banner_code", "store_id"], kind="mergesort").reset_index(drop=True)
    return df


def home_zone_weights(cfg: Config) -> dict[str, float]:
    """Zone-id → residential weight, for Layer 2/3 home-zone draw."""
    return {z["id"]: float(z["residential_weight"]) for z in cfg.zones}


def euclidean_degree_distance(
    a_lat: float | Iterable[float],
    a_long: float | Iterable[float],
    b_lat: float | Iterable[float],
    b_long: float | Iterable[float],
) -> float | np.ndarray:
    """Simple Euclidean distance in degrees. The Charlotte metro is
    small enough (~30 mi diameter) that the longitude-foreshortening
    error is below the noise floor of our store-choice model. Used by
    Layer 4b (Stage 4.4) for ``P(s) ∝ A_s / (d + d₀)^β``.
    """
    a_lat = np.asarray(a_lat, dtype=float)
    a_long = np.asarray(a_long, dtype=float)
    b_lat = np.asarray(b_lat, dtype=float)
    b_long = np.asarray(b_long, dtype=float)
    return np.sqrt((a_lat - b_lat) ** 2 + (a_long - b_long) ** 2)
