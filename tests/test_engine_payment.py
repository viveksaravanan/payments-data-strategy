"""Tests for src/generate/engine/payment.py (Wave 1 Stage 4.6) —
PILOT scale.

D18: per-transaction payment fields that emerge from customer +
merchant + daypart, not independent draws:
- entry_mode conditioned on wallet_enrolled (D16) × segment × age
- wallet_at_tap gated by entry_mode=contactless AND wallet_enrolled
- connectivity_type per store/segment terminal form factor

T13 (payment mix) is the §6 acceptance: blended contactless 48-55%,
mobile wallet 16-20%, grocery debit-leaning (emergent from D16.3),
entry mode varies by store clientele.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.generate.config.loader import load_config
from src.generate.engine.customers import build_customers
from src.generate.engine.geography import build_stores, build_zones
from src.generate.engine.payment import build_payment
from src.generate.engine.population import build_population
from src.generate.engine.trips import build_trips

CONFIG_ROOT = Path(__file__).resolve().parents[1] / "src" / "generate" / "config"
PILOT_CARDS = 5_000


@pytest.fixture(scope="module")
def cfg():
    return load_config(CONFIG_ROOT)


@pytest.fixture(scope="module")
def stores(cfg) -> pd.DataFrame:
    return build_stores(cfg, np.random.default_rng(cfg.global_["seed"]))


@pytest.fixture(scope="module")
def zones(cfg) -> pd.DataFrame:
    return build_zones(cfg)


@pytest.fixture(scope="module")
def population(cfg) -> pd.DataFrame:
    return build_population(
        cfg, np.random.default_rng(cfg.global_["seed"]), n_cards=PILOT_CARDS,
    )


@pytest.fixture(scope="module")
def customers(cfg, population) -> pd.DataFrame:
    return build_customers(
        cfg, population, np.random.default_rng(cfg.global_["seed"] + 1),
    )


@pytest.fixture(scope="module")
def trips(cfg, population, customers, stores, zones) -> pd.DataFrame:
    return build_trips(
        cfg, population, customers, stores, zones,
        np.random.default_rng(cfg.global_["seed"] + 2),
    )


@pytest.fixture(scope="module")
def payments(cfg, trips, customers, stores) -> pd.DataFrame:
    return build_payment(
        cfg, trips, customers, stores,
        np.random.default_rng(cfg.global_["seed"] + 4),
    )


# ----- schema -------------------------------------------------------

def test_one_row_per_trip(trips, payments) -> None:
    assert len(payments) == len(trips)
    assert set(payments["trip_id"]) == set(trips["trip_id"])


def test_required_columns(payments) -> None:
    required = {"trip_id", "entry_mode", "wallet_at_tap",
                "wallet_provider", "connectivity_type"}
    assert required.issubset(payments.columns)


def test_entry_mode_values(payments) -> None:
    assert set(payments["entry_mode"]) <= {"contactless", "chip", "swipe", "manual"}


def test_connectivity_values(payments) -> None:
    assert set(payments["connectivity_type"]) <= {"wifi", "ethernet", "cellular"}


# ----- D18.1 entry mode by segment (T13) ----------------------------

def test_grocery_entry_mode_distribution(trips, payments) -> None:
    """D18.1 grocery baseline: contactless ~52, chip ~40, swipe ~7, manual ~1."""
    p = payments.merge(trips[["trip_id", "segment"]], on="trip_id")
    g = p[p["segment"] == "grocery"]
    shares = g["entry_mode"].value_counts(normalize=True).to_dict()
    assert shares.get("contactless", 0) == pytest.approx(0.55, abs=0.06), \
        f"grocery contactless {shares.get('contactless', 0):.3f}"
    assert 0.30 <= shares.get("chip", 0) <= 0.45
    assert 0.04 <= shares.get("swipe", 0) <= 0.10
    assert shares.get("manual", 0) <= 0.03


def test_qsr_entry_mode_distribution(trips, payments) -> None:
    """D18.1 QSR: contactless ~63, chip ~30, swipe ~5, manual ~2."""
    p = payments.merge(trips[["trip_id", "segment"]], on="trip_id")
    q = p[p["segment"] == "qsr"]
    shares = q["entry_mode"].value_counts(normalize=True).to_dict()
    assert shares.get("contactless", 0) == pytest.approx(0.65, abs=0.06)
    assert 0.20 <= shares.get("chip", 0) <= 0.35


# (off-price entry-mode test removed — segment dropped in datamodel-v2.)


# ----- entry mode emerges from wallet enrollment (D18.1 keystone) ---

def test_wallet_enrolled_skews_contactless(trips, payments, customers) -> None:
    """Per D18.1: entry mode is conditioned on wallet enrollment.
    Wallet-enrolled customers should pick contactless materially
    more than non-enrolled."""
    p = payments.merge(trips[["trip_id", "card_id"]], on="trip_id")
    p = p.merge(customers[["card_id", "wallet_enrolled"]], on="card_id")
    enrolled_contactless = (
        p[p["wallet_enrolled"]]["entry_mode"] == "contactless"
    ).mean()
    not_enrolled_contactless = (
        p[~p["wallet_enrolled"]]["entry_mode"] == "contactless"
    ).mean()
    assert enrolled_contactless > not_enrolled_contactless + 0.10, (
        f"enrolled contactless share {enrolled_contactless:.3f} should exceed "
        f"non-enrolled {not_enrolled_contactless:.3f} by ≥10pp"
    )


# ----- D18.2 wallet-at-tap ------------------------------------------

def test_wallet_at_tap_only_when_contactless_and_enrolled(
    payments, trips, customers,
) -> None:
    p = payments.merge(trips[["trip_id", "card_id"]], on="trip_id")
    p = p.merge(customers[["card_id", "wallet_enrolled"]], on="card_id")
    tapped = p[p["wallet_at_tap"]]
    assert (tapped["entry_mode"] == "contactless").all()
    assert tapped["wallet_enrolled"].all()


def test_wallet_tap_share_in_d18_band(payments) -> None:
    """D18.2 / T13: mobile wallet ~16-20% of all transactions."""
    rate = payments["wallet_at_tap"].mean()
    assert 0.13 <= rate <= 0.22, f"wallet-at-tap share {rate:.4f}"


def test_wallet_provider_set_iff_tapped(payments) -> None:
    tapped = payments[payments["wallet_at_tap"]]
    untapped = payments[~payments["wallet_at_tap"]]
    assert tapped["wallet_provider"].notna().all()
    assert untapped["wallet_provider"].isna().all()
    valid = {"apple", "google", "samsung"}
    assert set(tapped["wallet_provider"]) <= valid


# ----- D18.3 connectivity by segment --------------------------------

def test_connectivity_distribution_blended(payments) -> None:
    """D18.3 rough: wifi ~55%, ethernet ~30%, cellular ~15% blended."""
    shares = payments["connectivity_type"].value_counts(normalize=True).to_dict()
    assert shares.get("wifi", 0)     == pytest.approx(0.55, abs=0.08)
    assert shares.get("ethernet", 0) == pytest.approx(0.30, abs=0.08)
    assert shares.get("cellular", 0) == pytest.approx(0.15, abs=0.08)


def test_qsr_has_more_cellular_than_grocery(trips, payments) -> None:
    """D18.3: QSR (drive-thru + counter) higher cellular share than
    countertop-only grocery."""
    p = payments.merge(trips[["trip_id", "segment"]], on="trip_id")
    g_cell = (p[p["segment"] == "grocery"]["connectivity_type"] == "cellular").mean()
    q_cell = (p[p["segment"] == "qsr"]["connectivity_type"] == "cellular").mean()
    assert q_cell > g_cell + 0.05, \
        f"QSR cellular {q_cell:.3f} should exceed grocery {g_cell:.3f} by ≥5pp"


# ----- D7.5 grocery debit lean (emerges from customer mix) ----------

def test_grocery_emerges_debit_leaning(trips, customers) -> None:
    """D7.5 fix: grocery's tender mix emerges debit-heavier than the
    population baseline because value-zone (debit-skewed) customers
    do more grocery shopping at WDX. Test on WDX specifically — it
    should be the most debit-heavy of the three grocers."""
    by_card = customers[["card_id", "tender"]]
    g_trips = trips[trips["segment"] == "grocery"]
    g_trips = g_trips.merge(by_card, on="card_id")
    wdx_debit = (g_trips[g_trips["banner_code"] == "WDX"]["tender"] == "debit").mean()
    acm_debit = (g_trips[g_trips["banner_code"] == "ACM"]["tender"] == "debit").mean()
    assert wdx_debit > acm_debit + 0.03, \
        f"WDX debit share {wdx_debit:.3f} should exceed ACM {acm_debit:.3f}"


# ----- reproducibility ----------------------------------------------

def test_reproducible_under_same_seed(cfg, trips, customers, stores) -> None:
    rng_a = np.random.default_rng(cfg.global_["seed"] + 4)
    rng_b = np.random.default_rng(cfg.global_["seed"] + 4)
    a = build_payment(cfg, trips, customers, stores, rng_a)
    b = build_payment(cfg, trips, customers, stores, rng_b)
    pd.testing.assert_frame_equal(a, b)
