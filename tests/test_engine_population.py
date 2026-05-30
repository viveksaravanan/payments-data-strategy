"""Tests for src/generate/engine/population.py (Wave 1 Stage 4.2).

D14: assign ~100k cards an activity profile — which segments each
touches and how active in each (per-segment trip budget). Implements
D14.3 intensity tiers, the D14.4 participation matrix (32% multi-
merchant, 6% all-three), and the D14.7 cohort tag.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.generate.config.loader import load_config
from src.generate.engine.population import build_population

CONFIG_ROOT = Path(__file__).resolve().parents[1] / "src" / "generate" / "config"


@pytest.fixture(scope="module")
def cfg():
    return load_config(CONFIG_ROOT)


@pytest.fixture(scope="module")
def pop(cfg) -> pd.DataFrame:
    """One population draw shared across tests for speed (~100k rows)."""
    rng = np.random.default_rng(cfg.global_["seed"])
    return build_population(cfg, rng)


# ----- size & structure ---------------------------------------------

def test_population_size(cfg, pop) -> None:
    target = cfg.global_["population"]["target_cards"]
    assert len(pop) == target


def test_required_columns(pop) -> None:
    required = {
        "card_id",
        "active_grocery", "active_qsr", "active_off_price",
        "tier_grocery", "tier_qsr", "tier_off_price",
        "trip_budget_grocery", "trip_budget_qsr", "trip_budget_off_price",
        "cohort",
        "participation_archetype",
    }
    assert required.issubset(pop.columns)


def test_card_ids_unique(pop) -> None:
    assert pop["card_id"].is_unique


def test_card_id_format(pop) -> None:
    """16-char hex per the v3 baseline / D16.3 single-token spec."""
    import re
    pattern = re.compile(r"^[0-9a-f]{16}$")
    assert pop["card_id"].str.match(pattern).all()


# ----- D14.4 participation matrix -----------------------------------

def test_participation_archetype_shares(pop) -> None:
    """D14.4: 7 archetypes summing to 100% with the stated shares."""
    expected = {
        "grocery_only":      0.37,
        "retail_only":       0.18,
        "qsr_only":          0.13,
        "grocery_qsr":       0.13,
        "grocery_retail":    0.09,
        "grocery_qsr_retail": 0.06,
        "qsr_retail":        0.04,
    }
    shares = pop["participation_archetype"].value_counts(normalize=True).to_dict()
    for archetype, expected_share in expected.items():
        assert archetype in shares, f"missing archetype {archetype!r}"
        # ±1.5 percentage points tolerance for sampling noise at 100k.
        assert shares[archetype] == pytest.approx(expected_share, abs=0.015), \
            f"{archetype}: expected {expected_share}, got {shares[archetype]:.4f}"


def test_multi_merchant_share_near_32_pct(pop) -> None:
    """D6/D14.4 design intent: ~25-30% multi-merchant; matrix lands at ~32%."""
    n_segments = (
        pop["active_grocery"].astype(int)
        + pop["active_qsr"].astype(int)
        + pop["active_off_price"].astype(int)
    )
    multi_share = (n_segments >= 2).mean()
    assert 0.28 <= multi_share <= 0.34, f"multi-merchant share {multi_share:.4f}"


def test_all_three_share_near_6_pct(pop) -> None:
    n_segments = (
        pop["active_grocery"].astype(int)
        + pop["active_qsr"].astype(int)
        + pop["active_off_price"].astype(int)
    )
    all_three_share = (n_segments == 3).mean()
    assert 0.04 <= all_three_share <= 0.08, f"all-three share {all_three_share:.4f}"


# ----- D14.2 per-segment active counts ------------------------------

def test_grocery_active_count(pop) -> None:
    """D14.2: ~65k grocery-active cards."""
    n = int(pop["active_grocery"].sum())
    assert 62_000 <= n <= 68_000, f"grocery active: {n}"


def test_qsr_active_count(pop) -> None:
    """D14.2: ~36k qsr-active cards."""
    n = int(pop["active_qsr"].sum())
    assert 34_000 <= n <= 38_000, f"qsr active: {n}"


def test_off_price_active_count(pop) -> None:
    """D14.2: ~37k off-price-active cards."""
    n = int(pop["active_off_price"].sum())
    assert 35_000 <= n <= 39_000, f"off-price active: {n}"


# ----- D14.3 intensity tiers ----------------------------------------

def test_grocery_tier_shares(pop) -> None:
    """D14.3 grocery: core 20%, regular 45%, occasional 35% (of grocery-active)."""
    grocery = pop[pop["active_grocery"]]
    shares = grocery["tier_grocery"].value_counts(normalize=True).to_dict()
    assert shares["core"] == pytest.approx(0.20, abs=0.03)
    assert shares["regular"] == pytest.approx(0.45, abs=0.03)
    assert shares["occasional"] == pytest.approx(0.35, abs=0.03)


def test_qsr_tier_shares(pop) -> None:
    """QSR tier shares: per AskUserQuestion in this session, D14.3's
    15/35/50 reconciles to ~11.85 mean trips/card × 36k active = 427k,
    overshooting T1's ±10% band on the 365k target. Resolution: lower
    heavy share to 10% (heavy is the brand-loyalist minority); means
    and ranges stay ratified. New shares 10/40/50 give weighted mean
    ~10.4 → 374k, comfortably in band."""
    qsr = pop[pop["active_qsr"]]
    shares = qsr["tier_qsr"].value_counts(normalize=True).to_dict()
    assert shares["heavy"] == pytest.approx(0.10, abs=0.03)
    assert shares["regular"] == pytest.approx(0.40, abs=0.03)
    assert shares["occasional"] == pytest.approx(0.50, abs=0.03)


def test_off_price_tier_shares(pop) -> None:
    """D14.3 off-price: enthusiast 12%, regular 33%, occasional 55%."""
    retail = pop[pop["active_off_price"]]
    shares = retail["tier_off_price"].value_counts(normalize=True).to_dict()
    assert shares["enthusiast"] == pytest.approx(0.12, abs=0.03)
    assert shares["regular"] == pytest.approx(0.33, abs=0.03)
    assert shares["occasional"] == pytest.approx(0.55, abs=0.03)


# ----- D14.3 trip-budget ranges -------------------------------------

def test_grocery_trip_budgets_within_range(pop) -> None:
    g = pop[pop["active_grocery"]]
    # core 28-40, regular 14-22, occasional 3-10. Allow inclusive bounds.
    by_tier = g.groupby("tier_grocery")["trip_budget_grocery"]
    assert by_tier.min()["core"] >= 28
    assert by_tier.max()["core"] <= 40
    assert by_tier.min()["regular"] >= 14
    assert by_tier.max()["regular"] <= 22
    assert by_tier.min()["occasional"] >= 3
    assert by_tier.max()["occasional"] <= 10


def test_qsr_trip_budgets_within_range(pop) -> None:
    q = pop[pop["active_qsr"]]
    by_tier = q.groupby("tier_qsr")["trip_budget_qsr"]
    assert by_tier.min()["heavy"] >= 30
    assert by_tier.max()["heavy"] <= 55
    assert by_tier.min()["regular"] >= 8
    assert by_tier.max()["regular"] <= 15
    assert by_tier.min()["occasional"] >= 2
    assert by_tier.max()["occasional"] <= 6


def test_off_price_trip_budgets_within_range(pop) -> None:
    """Off-price tier hi bounds widened from D14.3's 20/8/3 to
    24/10/4 per AskUserQuestion in this session — D14.3's stated μ
    can't be hit by triangular(lo, μ, hi) at the tighter bounds.
    Shares and means remain ratified; the hi widening lets the
    distribution actually reach μ."""
    r = pop[pop["active_off_price"]]
    by_tier = r.groupby("tier_off_price")["trip_budget_off_price"]
    assert by_tier.min()["enthusiast"] >= 10
    assert by_tier.max()["enthusiast"] <= 24
    assert by_tier.min()["regular"] >= 4
    assert by_tier.max()["regular"] <= 10
    assert by_tier.min()["occasional"] >= 1
    assert by_tier.max()["occasional"] <= 4


# ----- D14.1 reconciliation: trips × cards ≈ D5 volume targets ------

def test_grocery_trip_total_in_d5_band(cfg, pop) -> None:
    target = cfg.global_["volume_targets"]["grocery"]
    tol = cfg.global_["volume_targets"]["tolerance_pct"] / 100
    total = int(pop["trip_budget_grocery"].sum())
    assert target * (1 - tol) <= total <= target * (1 + tol), \
        f"grocery trip total {total} vs target {target} (±{tol*100:.0f}%)"


def test_qsr_trip_total_in_d5_band(cfg, pop) -> None:
    target = cfg.global_["volume_targets"]["qsr"]
    tol = cfg.global_["volume_targets"]["tolerance_pct"] / 100
    total = int(pop["trip_budget_qsr"].sum())
    assert target * (1 - tol) <= total <= target * (1 + tol), \
        f"qsr trip total {total} vs target {target} (±{tol*100:.0f}%)"


def test_off_price_trip_total_in_d5_band(cfg, pop) -> None:
    target = cfg.global_["volume_targets"]["off_price"]
    tol = cfg.global_["volume_targets"]["tolerance_pct"] / 100
    total = int(pop["trip_budget_off_price"].sum())
    assert target * (1 - tol) <= total <= target * (1 + tol), \
        f"off-price trip total {total} vs target {target} (±{tol*100:.0f}%)"


# ----- D14.7 cohort tags --------------------------------------------

def test_cohort_categories(pop) -> None:
    values = set(pop["cohort"].unique())
    assert values == {"established", "new_in_window", "lapsing"}


def test_cohort_established_dominates(pop) -> None:
    """Most cards transact across the whole window; new/lapsing are
    smaller tails. Not a ratified D14 number, but the test pins the
    behavior so accidental swings get noticed."""
    shares = pop["cohort"].value_counts(normalize=True).to_dict()
    assert shares["established"] >= 0.70
    assert shares["new_in_window"] <= 0.20
    assert shares["lapsing"] <= 0.15


# ----- reproducibility ----------------------------------------------

def test_reproducible_under_same_seed(cfg) -> None:
    rng_a = np.random.default_rng(cfg.global_["seed"])
    rng_b = np.random.default_rng(cfg.global_["seed"])
    a = build_population(cfg, rng_a)
    b = build_population(cfg, rng_b)
    pd.testing.assert_frame_equal(a, b)


# ----- inactive segments have null tier + 0 budget -------------------

def test_inactive_segments_have_null_tier_and_zero_budget(pop) -> None:
    """A card not in a segment must have null tier and 0 trip budget
    for that segment — keeps downstream layers from accidentally
    consuming garbage values."""
    not_g = pop[~pop["active_grocery"]]
    assert not_g["tier_grocery"].isna().all()
    assert (not_g["trip_budget_grocery"] == 0).all()

    not_q = pop[~pop["active_qsr"]]
    assert not_q["tier_qsr"].isna().all()
    assert (not_q["trip_budget_qsr"] == 0).all()

    not_r = pop[~pop["active_off_price"]]
    assert not_r["tier_off_price"].isna().all()
    assert (not_r["trip_budget_off_price"] == 0).all()
