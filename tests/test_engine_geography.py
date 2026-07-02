"""Tests for src/generate/engine/geography.py (Wave 1 Stage 4.1).

D13: place 29 stores in 8 zones per the D13.2 matrix, with centroid
± jitter; emit the zones table; supply distance + helpers consumed
by Layer 4b store resolution (D15b / D13.4).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.generate.config.loader import load_config
from src.generate.engine.geography import build_stores, build_zones

CONFIG_ROOT = Path(__file__).resolve().parents[1] / "src" / "generate" / "config"


@pytest.fixture(scope="module")
def cfg():
    return load_config(CONFIG_ROOT)


@pytest.fixture
def rng() -> np.random.Generator:
    return np.random.default_rng(42)


# ----- zones --------------------------------------------------------

def test_build_zones_emits_eight_rows(cfg) -> None:
    z = build_zones(cfg)
    assert isinstance(z, pd.DataFrame)
    assert len(z) == 8


def test_build_zones_required_columns(cfg) -> None:
    z = build_zones(cfg)
    required = {
        "zone_id", "name", "archetype",
        "affluence", "residential_weight",
        "density", "household_skew", "age_skew",
        "centroid_lat", "centroid_long",
    }
    assert required.issubset(z.columns)


def test_build_zones_residential_weights_sum_to_one(cfg) -> None:
    z = build_zones(cfg)
    assert z["residential_weight"].sum() == pytest.approx(1.0, abs=1e-9)


# ----- stores -------------------------------------------------------

def test_build_stores_total_count_38(cfg, rng) -> None:
    s = build_stores(cfg, rng)
    assert len(s) == 38


def test_build_stores_per_merchant_counts(cfg, rng) -> None:
    s = build_stores(cfg, rng)
    counts = s.groupby("banner_code").size().to_dict()
    assert counts == {"KRG": 6, "ACM": 5, "WDX": 4, "TBL": 9, "BKG": 8, "CFA": 6}


def test_build_stores_per_zone_counts_match_placement(cfg, rng) -> None:
    """§A11/§B1 placement grids — per-zone totals across all merchants."""
    s = build_stores(cfg, rng)
    expected = {
        "center_city": 4,       # KRG ACM TBL BKG
        "dilworth": 3,          # KRG ACM CFA
        "ballantyne": 4,        # KRG ACM BKG CFA
        "noda": 5,              # KRG ACM TBL BKG CFA
        "university_city": 6,   # KRG WDX TBL2 BKG CFA
        "eastway": 4,           # WDX TBL2 BKG
        "matthews": 9,          # KRG ACM WDX TBL2 BKG2 CFA2
        "cabarrus_edge": 3,     # WDX TBL BKG
    }
    got = s.groupby("zone_id").size().to_dict()
    assert got == expected


def test_build_stores_required_columns(cfg, rng) -> None:
    s = build_stores(cfg, rng)
    required = {
        "store_id", "merchant_id", "banner_code",
        "zone_id", "neighborhood", "metro_region",
        "latitude", "longitude", "open_date",
    }
    assert required.issubset(s.columns)


def test_store_id_format(cfg, rng) -> None:
    """`<banner>-NC-<NNNN>` zero-padded 4 digits."""
    s = build_stores(cfg, rng)
    import re
    pattern = re.compile(r"^(KRG|ACM|WDX|TBL|BKG|CFA)-NC-\d{4}$")
    assert all(pattern.match(sid) for sid in s["store_id"]), \
        f"bad store_id formats: {[sid for sid in s['store_id'] if not pattern.match(sid)]}"


def test_store_ids_are_unique(cfg, rng) -> None:
    s = build_stores(cfg, rng)
    assert s["store_id"].is_unique


def test_store_centroids_within_zone_jitter(cfg, rng) -> None:
    """D13.4 jitter ±0.02° (carried from v3 baseline)."""
    s = build_stores(cfg, rng)
    z = build_zones(cfg)
    merged = s.merge(z, on="zone_id", suffixes=("", "_z"))
    lat_offset = (merged["latitude"] - merged["centroid_lat"]).abs()
    long_offset = (merged["longitude"] - merged["centroid_long"]).abs()
    assert (lat_offset <= 0.02 + 1e-9).all()
    assert (long_offset <= 0.02 + 1e-9).all()


def test_reproducible_under_same_seed(cfg) -> None:
    """T18 prerequisite at the geography layer."""
    rng_a = np.random.default_rng(42)
    rng_b = np.random.default_rng(42)
    a = build_stores(cfg, rng_a)
    b = build_stores(cfg, rng_b)
    pd.testing.assert_frame_equal(a, b)


def test_open_date_before_window_start(cfg, rng) -> None:
    """Stores opened before the data window."""
    s = build_stores(cfg, rng)
    window_start = cfg.global_["window"]["start_date"]
    assert (pd.to_datetime(s["open_date"]) <= pd.Timestamp(window_start)).all()


def test_metro_region_derived_from_density(cfg, rng) -> None:
    """High density → urban_core; medium/med-high → inner_suburbs;
    low/low-med → outer_suburbs. Matches the v3 categorization."""
    s = build_stores(cfg, rng)
    by_zone = s.groupby("zone_id")["metro_region"].first().to_dict()
    # center_city density=high → urban_core
    assert by_zone["center_city"] == "urban_core"
    # ballantyne density=low → outer_suburbs
    assert by_zone["ballantyne"] == "outer_suburbs"
    # noda density=med-high → inner_suburbs
    assert by_zone["noda"] == "inner_suburbs"
    # dilworth density=medium → inner_suburbs
    assert by_zone["dilworth"] == "inner_suburbs"
