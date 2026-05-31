"""Tests for src/generate/engine/events.py (Wave 1 Stage 4.8).

D20 — promotions + planted anomalies A1-A3. T15 (promo penetration
+ demand lift) and T16 (anomalies detectable + localized) are the
§6 acceptance signals this stage gates.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.generate.config.loader import load_config
from src.generate.engine.baskets import build_basket_items
from src.generate.engine.catalog import build_catalog
from src.generate.engine.customers import build_customers
from src.generate.engine.events import (
    apply_anomaly_filter,
    build_a2_boost_lookup,
    build_a3_basket_mult_lookup,
    build_anomaly_schedule,
    build_promo_id_lookup,
    build_promo_lookup,
    build_promo_schedule,
)
from src.generate.engine.geography import build_stores, build_zones
from src.generate.engine.population import build_population
from src.generate.engine.pricing import build_priced_items
from src.generate.engine.trips import build_trips

CONFIG_ROOT = Path(__file__).resolve().parents[1] / "src" / "generate" / "config"
PILOT_CARDS = 5_000


@pytest.fixture(scope="module")
def cfg():
    return load_config(CONFIG_ROOT)


@pytest.fixture(scope="module")
def zones(cfg) -> pd.DataFrame:
    return build_zones(cfg)


@pytest.fixture(scope="module")
def stores(cfg) -> pd.DataFrame:
    return build_stores(cfg, np.random.default_rng(cfg.global_["seed"]))


@pytest.fixture(scope="module")
def catalog(cfg) -> pd.DataFrame:
    return build_catalog(cfg, np.random.default_rng(cfg.global_["seed"] + 10))


@pytest.fixture(scope="module")
def promo_schedule(cfg, catalog) -> pd.DataFrame:
    return build_promo_schedule(
        cfg, catalog, np.random.default_rng(cfg.global_["seed"] + 20),
    )


@pytest.fixture(scope="module")
def anomaly_schedule(cfg, stores) -> pd.DataFrame:
    return build_anomaly_schedule(cfg, stores)


# ----- Promo schedule schema + coverage -----------------------------

def test_promo_schedule_schema(promo_schedule) -> None:
    required = {"promo_id", "sku", "merchant_id", "promo_type",
                "start_date", "end_date", "depth_pct"}
    assert required.issubset(promo_schedule.columns)


def test_promo_schedule_has_grocery_qsr_and_offprice(promo_schedule) -> None:
    types = set(promo_schedule["promo_type"].unique())
    assert "weekly_ad" in types
    assert "lto" in types
    assert "clearance" in types
    assert "pasta_promo" in types     # A3 backing promos


def test_promo_schedule_depths_in_band(promo_schedule) -> None:
    assert (promo_schedule["depth_pct"] >= 0.10).all()
    assert (promo_schedule["depth_pct"] <= 0.50).all()


def test_promo_schedule_windows_within_data_window(cfg, promo_schedule) -> None:
    window_start = pd.Timestamp(cfg.global_["window"]["start_date"]).date()
    window_end = pd.Timestamp(cfg.global_["window"]["end_date"]).date()
    assert (promo_schedule["start_date"] >= window_start).all()
    assert (promo_schedule["end_date"] <= window_end).all()


# ----- Anomaly schedule schema + A1/A2/A3 presence -----------------

def test_anomaly_schedule_schema(anomaly_schedule) -> None:
    required = {"anomaly_id", "type", "description",
                "start_date", "end_date", "zone_id", "banner_code",
                "store_id", "category", "subcategory", "magnitude"}
    assert required.issubset(anomaly_schedule.columns)


def test_a1_a2_a3_all_present(anomaly_schedule) -> None:
    ids = set(anomaly_schedule["anomaly_id"].unique())
    a1 = [i for i in ids if i.startswith("A1-")]
    a2 = [i for i in ids if i.startswith("A2-")]
    a3 = [i for i in ids if i.startswith("A3-")]
    assert len(a1) >= 3, f"A1 should have at least 3 zone×banner rows; got {a1}"
    assert len(a2) >= 1, f"A2 should have at least 1 entry; got {a2}"
    assert len(a3) == 3, f"A3 should have 3 banner promos; got {a3}"


def test_a1_wdx_hardest_hit(anomaly_schedule) -> None:
    """D20.2 / A1: WDX should have the highest magnitude among the
    three grocers — value banner serving the value zones."""
    a1 = anomaly_schedule[anomaly_schedule["anomaly_id"].str.startswith("A1-")]
    by_banner = a1.groupby("banner_code")["magnitude"].max().to_dict()
    assert by_banner["WDX"] > by_banner["KRG"]
    assert by_banner["WDX"] > by_banner["ACM"]


# ----- Promo lookup mechanics ---------------------------------------

def test_build_promo_lookup_keys_are_per_day(promo_schedule) -> None:
    lk = build_promo_lookup(promo_schedule)
    assert len(lk) > 0
    # Each entry is (date, sku).
    sample = next(iter(lk.keys()))
    assert isinstance(sample[0], date)
    assert isinstance(sample[1], str)


def test_promo_id_lookup_parallel_to_depth(promo_schedule) -> None:
    depth_lk = build_promo_lookup(promo_schedule)
    id_lk = build_promo_id_lookup(promo_schedule)
    assert set(depth_lk.keys()) == set(id_lk.keys())


# ----- A1 trip-filter effect ---------------------------------------

def test_a1_filter_drops_uc_wdx_trips(cfg, stores, anomaly_schedule) -> None:
    """A1 should drop ~40% of WDX trips at UC + Eastway during window."""
    # Build small population for trips that hit UC/Eastway WDX stores
    pop = build_population(
        cfg, np.random.default_rng(cfg.global_["seed"]), n_cards=2000,
    )
    cust = build_customers(
        cfg, pop, np.random.default_rng(cfg.global_["seed"] + 1),
    )
    zones = build_zones(cfg)
    trips = build_trips(
        cfg, pop, cust, stores, zones,
        np.random.default_rng(cfg.global_["seed"] + 2),
    )

    # Slice: WDX trips in UC + Eastway during A1 window
    store_to_zone = stores.set_index("store_id")["zone_id"].to_dict()
    trips_zones = np.array([store_to_zone.get(s, "") for s in trips["store_id"]])
    trip_dates = pd.to_datetime(trips["txn_ts"]).dt.date.to_numpy()
    a1_window = (trip_dates >= date(2026, 4, 19)) & (trip_dates <= date(2026, 5, 29))
    a1_wdx = (
        (trips["banner_code"] == "WDX").to_numpy()
        & np.isin(trips_zones, ["university_city", "eastway"])
        & a1_window
    )
    n_before = int(a1_wdx.sum())

    filtered = apply_anomaly_filter(
        trips, anomaly_schedule, stores,
        np.random.default_rng(99),
    )
    f_zones = np.array([store_to_zone.get(s, "") for s in filtered["store_id"]])
    f_dates = pd.to_datetime(filtered["txn_ts"]).dt.date.to_numpy()
    f_window = (f_dates >= date(2026, 4, 19)) & (f_dates <= date(2026, 5, 29))
    f_a1_wdx = (
        (filtered["banner_code"] == "WDX").to_numpy()
        & np.isin(f_zones, ["university_city", "eastway"])
        & f_window
    )
    n_after = int(f_a1_wdx.sum())

    drop_share = 1 - n_after / n_before
    print(f"\nA1 WDX UC+Eastway: {n_before} → {n_after} trips, drop {drop_share*100:.1f}% (target 40%)")
    assert 0.30 <= drop_share <= 0.50, \
        f"A1 WDX drop share {drop_share:.3f} outside 30-50% band"


def test_a1_filter_leaves_non_uc_trips_alone(
    cfg, stores, anomaly_schedule,
) -> None:
    """A1 is localized — trips at other zones should be unaffected."""
    pop = build_population(
        cfg, np.random.default_rng(cfg.global_["seed"]), n_cards=2000,
    )
    cust = build_customers(
        cfg, pop, np.random.default_rng(cfg.global_["seed"] + 1),
    )
    zones = build_zones(cfg)
    trips = build_trips(
        cfg, pop, cust, stores, zones,
        np.random.default_rng(cfg.global_["seed"] + 2),
    )

    store_to_zone = stores.set_index("store_id")["zone_id"].to_dict()
    trips_zones = np.array([store_to_zone.get(s, "") for s in trips["store_id"]])
    # Non-A1 zones (anywhere not UC + Eastway)
    non_a1 = ~np.isin(trips_zones, ["university_city", "eastway"])
    n_before = int(non_a1.sum())

    filtered = apply_anomaly_filter(
        trips, anomaly_schedule, stores,
        np.random.default_rng(99),
    )
    f_zones = np.array([store_to_zone.get(s, "") for s in filtered["store_id"]])
    non_a1_after = (~np.isin(f_zones, ["university_city", "eastway"])).sum()
    print(f"\nNon-A1 zones: {n_before} → {non_a1_after} trips (no localization breach if equal)")
    assert non_a1_after == n_before, \
        "A1 should only affect UC + Eastway; other zones unchanged"


# ----- Promo lookup integration with priced_items ------------------

def test_priced_items_apply_promo_discount(
    cfg, catalog, stores, zones, promo_schedule,
) -> None:
    """End-to-end at a 1000-card pilot: lines on promo should have
    non-zero discount; promo_id should be set for those lines."""
    pop = build_population(
        cfg, np.random.default_rng(cfg.global_["seed"]), n_cards=1000,
    )
    cust = build_customers(
        cfg, pop, np.random.default_rng(cfg.global_["seed"] + 1),
    )
    trips = build_trips(
        cfg, pop, cust, stores, zones,
        np.random.default_rng(cfg.global_["seed"] + 2),
    )
    depth_lk = build_promo_lookup(promo_schedule)
    id_lk = build_promo_id_lookup(promo_schedule)
    basket = build_basket_items(
        cfg, trips, cust, catalog,
        np.random.default_rng(cfg.global_["seed"] + 3),
        promo_depth_lookup=depth_lk,
    )
    priced = build_priced_items(
        cfg, basket, catalog, trips, stores, zones,
        np.random.default_rng(cfg.global_["seed"] + 5),
        promo_depth_lookup=depth_lk,
        promo_id_lookup=id_lk,
    )

    on_promo = priced[priced["promo_id"].notna()]
    assert (on_promo["discount"] > 0).all()
    not_on_promo = priced[priced["promo_id"].isna()]
    assert (not_on_promo["discount"] == 0).all()
    promo_share = len(on_promo) / len(priced)
    print(f"\nPromo line share blended: {promo_share*100:.1f}%")
    assert promo_share >= 0.10

    # T15 grocery-only: 25-35% of grocery units on promo
    g_trips = set(trips[trips["segment"] == "grocery"]["trip_id"])
    g_priced = priced[priced["trip_id"].isin(g_trips)]
    g_units = g_priced["qty"].sum()
    g_units_on_promo = g_priced[g_priced["promo_id"].notna()]["qty"].sum()
    g_promo_unit_share = g_units_on_promo / g_units
    print(f"T15 grocery units on promo: {g_promo_unit_share*100:.1f}%  (target 25-35%)")
    # Pilot band slightly wider than §6 strict (sampling noise at 1k cards):
    assert 0.18 <= g_promo_unit_share <= 0.42, \
        f"grocery promo unit share {g_promo_unit_share:.4f} outside pilot band"
