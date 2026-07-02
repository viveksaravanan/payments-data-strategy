"""Tests for src/generate/engine/population.py (datamodel-v2 §C1).

Population shape: 155k cards, 3-group participation (grocery-only 36% /
QSR-only 18% / both 46%) over two segments (off-price dropped), intensity
tiers per Appendix D, cohort tag.
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
    """One full 155k population draw shared across tests."""
    return build_population(cfg, np.random.default_rng(cfg.global_["seed"]))


# ----- size & structure ---------------------------------------------

def test_population_size(cfg, pop) -> None:
    assert len(pop) == cfg.global_["population"]["target_cards"] == 155_000


def test_required_columns(pop) -> None:
    required = {
        "card_id", "participation_archetype",
        "active_grocery", "active_qsr",
        "tier_grocery", "tier_qsr",
        "trip_budget_grocery", "trip_budget_qsr",
        "cohort",
    }
    assert required.issubset(pop.columns)
    # off-price columns dropped in v2
    assert "active_off_price" not in pop.columns
    assert "trip_budget_off_price" not in pop.columns


def test_card_ids_unique(pop) -> None:
    assert pop["card_id"].is_unique


def test_card_id_format(pop) -> None:
    import re
    assert pop["card_id"].str.match(re.compile(r"^[0-9a-f]{16}$")).all()


# ----- §C1 participation matrix (3 archetypes) ----------------------

def test_participation_archetype_shares(pop) -> None:
    expected = {"grocery_only": 0.36, "qsr_only": 0.18, "both": 0.46}
    shares = pop["participation_archetype"].value_counts(normalize=True).to_dict()
    assert set(shares) == set(expected)
    for a, e in expected.items():
        assert shares[a] == pytest.approx(e, abs=0.015), f"{a}: {shares[a]:.4f} vs {e}"


def test_both_segment_share(pop) -> None:
    """The 'both' group (grocery ∩ QSR) is ~46% (§C1 cross-segment cohort)."""
    both = (pop["active_grocery"] & pop["active_qsr"]).mean()
    assert 0.44 <= both <= 0.48


def test_segment_active_counts(pop) -> None:
    """grocery-active ~82% (~127k), qsr-active ~64% (~99k) of 155k (§C1)."""
    g = int(pop["active_grocery"].sum()); q = int(pop["active_qsr"].sum())
    assert 122_000 <= g <= 132_000, f"grocery active {g}"
    assert 95_000 <= q <= 103_000, f"qsr active {q}"


# ----- Appendix D intensity tiers -----------------------------------

def test_grocery_tier_shares(pop) -> None:
    """core 20% / regular 45% / occasional 35% (of grocery-active)."""
    shares = pop[pop["active_grocery"]]["tier_grocery"].value_counts(normalize=True).to_dict()
    assert shares["core"] == pytest.approx(0.20, abs=0.03)
    assert shares["regular"] == pytest.approx(0.45, abs=0.03)
    assert shares["occasional"] == pytest.approx(0.35, abs=0.03)


def test_qsr_tier_shares(pop) -> None:
    """heavy 12% / regular 42% / occasional 46% (of qsr-active, App D)."""
    shares = pop[pop["active_qsr"]]["tier_qsr"].value_counts(normalize=True).to_dict()
    assert shares["heavy"] == pytest.approx(0.12, abs=0.03)
    assert shares["regular"] == pytest.approx(0.42, abs=0.03)
    assert shares["occasional"] == pytest.approx(0.46, abs=0.03)


# ----- trip-budget ranges (Appendix D tuned triangular) -------------

def test_grocery_trip_budgets_within_range(pop) -> None:
    by = pop[pop["active_grocery"]].groupby("tier_grocery")["trip_budget_grocery"]
    assert by.min()["core"] >= 30 and by.max()["core"] <= 46
    assert by.min()["regular"] >= 14 and by.max()["regular"] <= 24
    assert by.min()["occasional"] >= 3 and by.max()["occasional"] <= 12


def test_qsr_trip_budgets_within_range(pop) -> None:
    by = pop[pop["active_qsr"]].groupby("tier_qsr")["trip_budget_qsr"]
    assert by.min()["heavy"] >= 32 and by.max()["heavy"] <= 58
    assert by.min()["regular"] >= 14 and by.max()["regular"] <= 26
    assert by.min()["occasional"] >= 3 and by.max()["occasional"] <= 14


# ----- reconciliation: trips × cards ≈ §C9 volume -------------------

def test_qsr_trip_total_in_band(cfg, pop) -> None:
    target = cfg.global_["volume_targets"]["qsr"]
    total = int(pop["trip_budget_qsr"].sum())
    print(f"\nQSR trip budget total: {total:,} (target {target:,})")
    assert 0.90 * target <= total <= 1.10 * target


def test_grocery_trip_total_near_band(cfg, pop) -> None:
    """Grocery trip-budget total. The §C9 count target (2.67M) assumed a
    $48 basket; the model's basket runs ~$54 (high end of the §A9.5 band),
    so at the on-anchor $128M window dollars the realized TRIP COUNT lands
    ~11% under the count target. The dollar total is the primary anchor
    (Decision D), so the count band is widened to ±(12-17)% here (the
    dollar total is checked at the AUV/T3 level)."""
    target = cfg.global_["volume_targets"]["grocery"]
    total = int(pop["trip_budget_grocery"].sum())
    print(f"\ngrocery trip budget total: {total:,} (target {target:,}, widened for AOV effect)")
    assert 0.83 * target <= total <= 1.12 * target


# ----- cohort tags --------------------------------------------------

def test_cohort_categories(pop) -> None:
    assert set(pop["cohort"].unique()) == {"established", "new_in_window", "lapsing"}


def test_cohort_established_dominates(pop) -> None:
    shares = pop["cohort"].value_counts(normalize=True).to_dict()
    assert shares["established"] >= 0.70
    assert shares["new_in_window"] <= 0.20
    assert shares["lapsing"] <= 0.15


# ----- reproducibility + inactive segments --------------------------

def test_reproducible_under_same_seed(cfg) -> None:
    a = build_population(cfg, np.random.default_rng(cfg.global_["seed"]))
    b = build_population(cfg, np.random.default_rng(cfg.global_["seed"]))
    pd.testing.assert_frame_equal(a, b)


def test_inactive_segments_have_null_tier_and_zero_budget(pop) -> None:
    not_g = pop[~pop["active_grocery"]]
    assert not_g["tier_grocery"].isna().all()
    assert (not_g["trip_budget_grocery"] == 0).all()
    not_q = pop[~pop["active_qsr"]]
    assert not_q["tier_qsr"].isna().all()
    assert (not_q["trip_budget_qsr"] == 0).all()
