"""Tests for src/generate/engine/trips.py (Wave 1 Stage 4.4).

D15 (temporal) + D15b (store resolution). Places each card's
per-segment trip budget into dated, timed transactions at specific
stores. Cohort active windows (D15.5) respected; gravity model
(D13.4) drives store choice; loyalty × gravity (D16.1 + D13.4)
drives banner choice for grocery.

The trips fixture is built at full ~100k-card scale so per-segment
volume + daypart distributions check meaningfully. First run takes
~30-60s due to the 1.67M-row trip set; subsequent tests are
instant against the cached fixture.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.generate.config.loader import load_config
from src.generate.engine.customers import build_customers
from src.generate.engine.geography import build_stores, build_zones
from src.generate.engine.population import build_population
from src.generate.engine.trips import build_trips

CONFIG_ROOT = Path(__file__).resolve().parents[1] / "src" / "generate" / "config"


@pytest.fixture(scope="module")
def cfg():
    return load_config(CONFIG_ROOT)


@pytest.fixture(scope="module")
def zones(cfg) -> pd.DataFrame:
    return build_zones(cfg)


@pytest.fixture(scope="module")
def stores(cfg) -> pd.DataFrame:
    rng = np.random.default_rng(cfg.global_["seed"])
    return build_stores(cfg, rng)


@pytest.fixture(scope="module")
def population(cfg) -> pd.DataFrame:
    rng = np.random.default_rng(cfg.global_["seed"])
    return build_population(cfg, rng)


@pytest.fixture(scope="module")
def customers(cfg, population) -> pd.DataFrame:
    rng = np.random.default_rng(cfg.global_["seed"] + 1)
    return build_customers(cfg, population, rng)


@pytest.fixture(scope="module")
def trips(cfg, population, customers, stores, zones) -> pd.DataFrame:
    rng = np.random.default_rng(cfg.global_["seed"] + 2)
    return build_trips(cfg, population, customers, stores, zones, rng)


# ----- schema -------------------------------------------------------

def test_required_columns(trips) -> None:
    required = {
        "trip_id", "card_id", "segment",
        "banner_code", "store_id", "txn_ts",
    }
    assert required.issubset(trips.columns)


def test_trip_id_unique(trips) -> None:
    assert trips["trip_id"].is_unique


# ----- per-card trip budgets respected ------------------------------

def test_per_card_trip_counts_match_budget(trips, population) -> None:
    """The sum of trips per card per segment must equal the
    population trip budget (no skipped trips, no duplicates)."""
    by_card_segment = trips.groupby(["card_id", "segment"]).size().unstack(fill_value=0)
    pop_indexed = population.set_index("card_id")

    if "grocery" in by_card_segment.columns:
        assert (by_card_segment["grocery"] == pop_indexed.loc[
            by_card_segment.index, "trip_budget_grocery"
        ]).all()
    if "qsr" in by_card_segment.columns:
        assert (by_card_segment["qsr"] == pop_indexed.loc[
            by_card_segment.index, "trip_budget_qsr"
        ]).all()
    if "off_price" in by_card_segment.columns:
        assert (by_card_segment["off_price"] == pop_indexed.loc[
            by_card_segment.index, "trip_budget_off_price"
        ]).all()


def test_total_trips_match_population_budgets(trips, population) -> None:
    expected = (
        int(population["trip_budget_grocery"].sum())
        + int(population["trip_budget_qsr"].sum())
        + int(population["trip_budget_off_price"].sum())
    )
    assert len(trips) == expected


# ----- timing (D15) -------------------------------------------------

def test_all_dates_within_window(cfg, trips) -> None:
    start = pd.Timestamp(cfg.global_["window"]["start_date"])
    end = pd.Timestamp(cfg.global_["window"]["end_date"]) + pd.Timedelta(days=1)
    ts = pd.to_datetime(trips["txn_ts"])
    assert (ts >= start).all()
    assert (ts < end).all()


def test_grocery_weekend_weekday_ratio(trips) -> None:
    """T4 in §6: grocery weekend/weekday ratio 1.2-1.35."""
    g = trips[trips["segment"] == "grocery"]
    dow = pd.to_datetime(g["txn_ts"]).dt.dayofweek
    # Mon-Thu=weekday, Fri-Sun=weekend? D15.2 has Fri at 1.05 and Sun=1.25.
    # Use Sat-Sun as weekend (most common definition); Mon-Fri as weekday.
    weekend = ((dow == 5) | (dow == 6)).sum()
    weekday = ((dow >= 0) & (dow <= 4)).sum()
    ratio = (weekend / 2) / (weekday / 5)  # per-day average
    assert 1.15 <= ratio <= 1.40, f"grocery weekend/weekday ratio {ratio:.3f}"


def test_grocery_sun_at_least_sat_at_least_fri(trips) -> None:
    """T4 in §6: Sun ≥ Sat ≥ Fri (within sampling tolerance)."""
    g = trips[trips["segment"] == "grocery"]
    dow_counts = pd.to_datetime(g["txn_ts"]).dt.dayofweek.value_counts()
    sun = dow_counts.get(6, 0)
    sat = dow_counts.get(5, 0)
    fri = dow_counts.get(4, 0)
    # Allow 2% slop for sampling noise.
    assert sun * 1.02 >= sat
    assert sat * 1.02 >= fri


def test_taco_bell_late_night_share(trips) -> None:
    """T5 in §6: Taco Bell late-night (9pm+) share 17-21%."""
    tbl = trips[trips["banner_code"] == "TBL"]
    hour = pd.to_datetime(tbl["txn_ts"]).dt.hour
    late_night = ((hour >= 21) | (hour < 3)).mean()
    assert 0.17 <= late_night <= 0.22, f"TBL late-night share {late_night:.4f}"


def test_pay_cycle_lift_visible(trips) -> None:
    """T6 in §6: early-month (1-10) + mid-month (15-17) > mid-late
    month average. Tests on grocery where the lift is strongest."""
    g = trips[trips["segment"] == "grocery"]
    day = pd.to_datetime(g["txn_ts"]).dt.day
    early = ((day >= 1) & (day <= 10)).sum() / 10
    mid = ((day >= 15) & (day <= 17)).sum() / 3
    flat = ((day >= 20) & (day <= 28)).sum() / 9
    assert early > flat * 1.05, f"early-month lift: {early:.0f} vs flat {flat:.0f}"
    assert mid > flat * 1.05, f"mid-month lift: {mid:.0f} vs flat {flat:.0f}"


# ----- cohort active window (D15.5) ---------------------------------

def test_established_cards_span_full_window(cfg, trips, customers, population) -> None:
    """Established cards (cohort) transact across the full 90 days."""
    start = pd.Timestamp(cfg.global_["window"]["start_date"])
    end = pd.Timestamp(cfg.global_["window"]["end_date"])
    est_ids = population[population["cohort"] == "established"]["card_id"]
    est_trips = trips[trips["card_id"].isin(est_ids)]
    ts = pd.to_datetime(est_trips["txn_ts"])
    span = (ts.max() - ts.min()).days
    assert span >= 85  # near-full window


def test_new_in_window_cards_concentrated_later(trips, population) -> None:
    """New_in_window cards have median trip date later than
    established cards."""
    new_ids = population[population["cohort"] == "new_in_window"]["card_id"]
    est_ids = population[population["cohort"] == "established"]["card_id"]
    new_med = pd.to_datetime(trips[trips["card_id"].isin(new_ids)]["txn_ts"]).median()
    est_med = pd.to_datetime(trips[trips["card_id"].isin(est_ids)]["txn_ts"]).median()
    assert new_med > est_med, \
        f"new_in_window median {new_med} should be later than established median {est_med}"


def test_lapsing_cards_concentrated_earlier(trips, population) -> None:
    """Lapsing cards' median trip date should be before established."""
    lap_ids = population[population["cohort"] == "lapsing"]["card_id"]
    est_ids = population[population["cohort"] == "established"]["card_id"]
    if len(lap_ids) == 0:
        pytest.skip("no lapsing cards in pilot")
    lap_med = pd.to_datetime(trips[trips["card_id"].isin(lap_ids)]["txn_ts"]).median()
    est_med = pd.to_datetime(trips[trips["card_id"].isin(est_ids)]["txn_ts"]).median()
    assert lap_med < est_med, \
        f"lapsing median {lap_med} should be earlier than established median {est_med}"


# ----- store choice (D15b / D13.4) ----------------------------------

def test_every_store_has_trips(trips, stores) -> None:
    """No orphan stores. Gravity should distribute traffic everywhere."""
    store_ids_with_trips = set(trips["store_id"].unique())
    expected = set(stores["store_id"])
    missing = expected - store_ids_with_trips
    assert not missing, f"stores with zero trips: {sorted(missing)}"


def test_grocery_loyalist_primary_banner_share(trips, customers, population) -> None:
    """D16.1: loyalist primary banner share ~88% of their grocery trips."""
    loyalists = customers[customers["loyalty_type"] == "loyalist"]
    loyalist_g_trips = trips[
        (trips["segment"] == "grocery")
        & (trips["card_id"].isin(loyalists["card_id"]))
    ]
    primary_by_card = customers.set_index("card_id")["primary_banner"]
    is_primary = loyalist_g_trips["banner_code"].values == \
                 primary_by_card.loc[loyalist_g_trips["card_id"]].values
    share = is_primary.mean()
    # D16.1 says 88% but gravity composition can pull it down somewhat
    # depending on home-zone distance from the primary banner's stores.
    assert 0.80 <= share <= 0.93, f"loyalist primary share {share:.4f}"


def test_three_chain_primary_banner_share(trips, customers, population) -> None:
    """D16.1: three-chain primary share ~45% (much lower spread)."""
    three_chain = customers[customers["loyalty_type"] == "three_chain"]
    tc_g_trips = trips[
        (trips["segment"] == "grocery")
        & (trips["card_id"].isin(three_chain["card_id"]))
    ]
    if len(tc_g_trips) == 0:
        pytest.skip("no three_chain grocery trips")
    primary_by_card = customers.set_index("card_id")["primary_banner"]
    is_primary = tc_g_trips["banner_code"].values == \
                 primary_by_card.loc[tc_g_trips["card_id"]].values
    share = is_primary.mean()
    assert 0.38 <= share <= 0.55, f"three_chain primary share {share:.4f}"


def test_qsr_trips_only_at_tbl(trips) -> None:
    qsr = trips[trips["segment"] == "qsr"]
    assert (qsr["banner_code"] == "TBL").all()


def test_off_price_trips_only_at_tjx(trips) -> None:
    op = trips[trips["segment"] == "off_price"]
    assert (op["banner_code"] == "TJX").all()


# ----- reproducibility ----------------------------------------------

def test_reproducible_under_same_seed(cfg, population, customers, stores, zones) -> None:
    rng_a = np.random.default_rng(cfg.global_["seed"] + 2)
    rng_b = np.random.default_rng(cfg.global_["seed"] + 2)
    a = build_trips(cfg, population, customers, stores, zones, rng_a)
    b = build_trips(cfg, population, customers, stores, zones, rng_b)
    pd.testing.assert_frame_equal(a, b)
