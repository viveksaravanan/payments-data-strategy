"""Tests for src/generate/engine/customers.py (Wave 1 Stage 4.3).

D16: durable per-card state — home zone, affluence draw, banner
loyalty (D16.1), and single card identity (D16.3). Preference
vector + staple SKUs (D16.2) land at Stage 4.5 (baskets) where
the catalog exists.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.generate.config.loader import load_config
from src.generate.engine.customers import build_customers
from src.generate.engine.population import build_population

CONFIG_ROOT = Path(__file__).resolve().parents[1] / "src" / "generate" / "config"


@pytest.fixture(scope="module")
def cfg():
    return load_config(CONFIG_ROOT)


@pytest.fixture(scope="module")
def population(cfg) -> pd.DataFrame:
    rng = np.random.default_rng(cfg.global_["seed"])
    return build_population(cfg, rng)


@pytest.fixture(scope="module")
def customers(cfg, population) -> pd.DataFrame:
    """One customer draw, shared across tests for speed."""
    rng = np.random.default_rng(cfg.global_["seed"] + 1)
    return build_customers(cfg, population, rng)


# ----- size & schema ------------------------------------------------

def test_one_row_per_card(cfg, customers, population) -> None:
    assert len(customers) == len(population)
    assert set(customers["card_id"]) == set(population["card_id"])


def test_required_columns(customers) -> None:
    required = {
        "card_id",
        "home_zone", "affluence",
        "loyalty_type", "primary_banner",
        "tender", "network", "wallet_enrolled", "wallet_provider",
    }
    assert required.issubset(customers.columns)


# ----- home zone (D13.3) --------------------------------------------

def test_home_zone_values_are_valid(cfg, customers) -> None:
    valid = {z["id"] for z in cfg.zones}
    assert set(customers["home_zone"]) <= valid


def test_home_zone_distribution_matches_residential_weights(cfg, customers) -> None:
    shares = customers["home_zone"].value_counts(normalize=True).to_dict()
    for z in cfg.zones:
        expected = z["residential_weight"]
        actual = shares.get(z["id"], 0)
        assert actual == pytest.approx(expected, abs=0.015), \
            f"zone {z['id']}: expected ≈{expected}, got {actual:.4f}"


# ----- affluence (D13.3: conditioned on zone profile with spread) ---

def test_affluence_centered_on_zone_mean(cfg, customers) -> None:
    """Per-zone affluence median ≈ zone profile affluence (within
    sampling tolerance)."""
    by_zone = customers.groupby("home_zone")["affluence"].median()
    for z in cfg.zones:
        expected = z["affluence"]
        got = by_zone[z["id"]]
        assert abs(got - expected) <= 0.10, \
            f"zone {z['id']}: median affluence {got:.3f} vs expected {expected:.3f}"


def test_affluence_has_within_zone_spread(customers) -> None:
    """D13.3: affluence is a skew with within-zone variance, not a
    determinant — no zone is monolithic."""
    by_zone_std = customers.groupby("home_zone")["affluence"].std()
    assert (by_zone_std > 0.05).all(), \
        "every zone must have within-zone affluence variance"


# ----- loyalty (D16.1) ----------------------------------------------

def test_loyalty_type_values(customers) -> None:
    valid = {"loyalist", "splitter", "three_chain", "lapsed_light"}
    assert set(customers["loyalty_type"]) <= valid


def test_loyalty_type_shares(customers) -> None:
    """D16.1: loyalist 55%, splitter 30%, three-chain 12%, lapsed 3%."""
    shares = customers["loyalty_type"].value_counts(normalize=True).to_dict()
    assert shares["loyalist"]     == pytest.approx(0.55, abs=0.02)
    assert shares["splitter"]     == pytest.approx(0.30, abs=0.02)
    assert shares["three_chain"]  == pytest.approx(0.12, abs=0.02)
    assert shares["lapsed_light"] == pytest.approx(0.03, abs=0.015)


def test_primary_banner_is_a_grocery_banner(cfg, customers, population) -> None:
    """Loyalty is grocery-only per D16.1 — the loyalty_type is set
    for all cards but the primary_banner is the grocer (or null if
    the card isn't grocery-active)."""
    grocery_active = customers["card_id"].isin(
        population[population["active_grocery"]]["card_id"]
    )
    grocers = {"KRG", "ACM", "WDX"}
    active = customers[grocery_active]
    assert active["primary_banner"].notna().all()
    assert set(active["primary_banner"]) <= grocers
    inactive = customers[~grocery_active]
    assert inactive["primary_banner"].isna().all()


# ----- card identity (D16.3) ----------------------------------------

def test_tender_values(customers) -> None:
    assert set(customers["tender"]) <= {"credit", "debit"}


def test_network_values(customers) -> None:
    assert set(customers["network"]) <= {"visa", "mc", "amex", "discover"}


def test_network_conditional_on_tender(customers) -> None:
    """D16.3 / Fed 2025 Diary anchors:
    - debit ≈ visa 60 / mc 38 / other 2
    - credit ≈ visa 50 / mc 25 / amex 13-19 / discover 5-6."""
    debit = customers[customers["tender"] == "debit"]
    d_shares = debit["network"].value_counts(normalize=True).to_dict()
    assert d_shares.get("visa", 0) == pytest.approx(0.60, abs=0.03)
    assert d_shares.get("mc",   0) == pytest.approx(0.38, abs=0.03)
    assert d_shares.get("amex", 0) + d_shares.get("discover", 0) <= 0.05

    credit = customers[customers["tender"] == "credit"]
    c_shares = credit["network"].value_counts(normalize=True).to_dict()
    assert c_shares.get("visa", 0)     == pytest.approx(0.50, abs=0.03)
    assert c_shares.get("mc",   0)     == pytest.approx(0.25, abs=0.03)
    assert 0.10 <= c_shares.get("amex", 0)     <= 0.22
    assert 0.03 <= c_shares.get("discover", 0) <= 0.09


def test_wallet_enrollment_rate(customers) -> None:
    """D16.3 anchor: ~45% mobile-wallet enrollment."""
    rate = customers["wallet_enrolled"].mean()
    assert 0.40 <= rate <= 0.50


def test_wallet_provider_values_only_when_enrolled(customers) -> None:
    """Provider set iff wallet_enrolled is True."""
    enrolled = customers[customers["wallet_enrolled"]]
    not_enrolled = customers[~customers["wallet_enrolled"]]
    assert enrolled["wallet_provider"].notna().all()
    assert not_enrolled["wallet_provider"].isna().all()
    valid = {"apple", "google", "samsung"}
    assert set(enrolled["wallet_provider"]) <= valid


def test_wallet_provider_shares(customers) -> None:
    """D16.3 / D18.2: Apple ~55, Google ~30, Samsung ~15."""
    enrolled = customers[customers["wallet_enrolled"]]
    shares = enrolled["wallet_provider"].value_counts(normalize=True).to_dict()
    assert shares.get("apple",   0) == pytest.approx(0.55, abs=0.04)
    assert shares.get("google",  0) == pytest.approx(0.30, abs=0.04)
    assert shares.get("samsung", 0) == pytest.approx(0.15, abs=0.04)


# ----- D7.5 emergent grocery debit lean -----------------------------

def test_value_zone_skews_debit(customers) -> None:
    """D7.5 / D16.3 'emergent debit lean for grocery':
    value zones (lower affluence) should produce a debit-heavier
    card pool than affluent zones. The per-merchant payment mix
    falls out of this at transaction time (Layer 6); here we just
    confirm the zone-conditional skew."""
    # Aggregate by zone affluence: split at affluence=1.0.
    by_card_aff = customers[["affluence", "tender"]]
    value_pool = by_card_aff[by_card_aff["affluence"] < 0.95]
    affluent_pool = by_card_aff[by_card_aff["affluence"] > 1.20]
    value_debit = (value_pool["tender"] == "debit").mean()
    affluent_debit = (affluent_pool["tender"] == "debit").mean()
    assert value_debit > affluent_debit + 0.05, (
        f"value-zone debit share {value_debit:.3f} should exceed "
        f"affluent-zone debit share {affluent_debit:.3f} by ≥5pp"
    )


# ----- reproducibility ----------------------------------------------

def test_reproducible_under_same_seed(cfg, population) -> None:
    rng_a = np.random.default_rng(cfg.global_["seed"] + 1)
    rng_b = np.random.default_rng(cfg.global_["seed"] + 1)
    a = build_customers(cfg, population, rng_a)
    b = build_customers(cfg, population, rng_b)
    pd.testing.assert_frame_equal(a, b)
