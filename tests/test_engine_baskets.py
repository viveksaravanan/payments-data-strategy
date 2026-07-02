"""Tests for src/generate/engine/baskets.py (datamodel-v2, §C5) — PILOT.

Mission-driven baskets over the static committed catalog: grocery affinity
(functional_subcategory pairs), QSR combo-attach, affluence-driven PL
selection, staples, heavy-tail size. Line items carry only sku + qty; the
taxonomy/PL resolve via the products join (the ``items_x`` fixture).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.generate.config.loader import ConfigInvariantError, load_config
from src.generate.engine.baskets import (
    _assert_affinity_subcats_exist,
    _build_affinity_lookup,
    build_basket_items,
)
from src.generate.engine.catalog import load_products
from src.generate.engine.customers import build_customers
from src.generate.engine.geography import build_stores, build_zones
from src.generate.engine.population import build_population
from src.generate.engine.trips import build_trips

CONFIG_ROOT = Path(__file__).resolve().parents[1] / "src" / "generate" / "config"
PILOT_CARDS = 6_000
GROCERS = ("KRG", "ACM", "WDX")


@pytest.fixture(scope="module")
def cfg():
    return load_config(CONFIG_ROOT)


@pytest.fixture(scope="module")
def zones(cfg):
    return build_zones(cfg)


@pytest.fixture(scope="module")
def stores(cfg):
    return build_stores(cfg, np.random.default_rng(cfg.global_["seed"]))


@pytest.fixture(scope="module")
def catalog():
    return load_products()


@pytest.fixture(scope="module")
def population(cfg):
    return build_population(cfg, np.random.default_rng(cfg.global_["seed"]), n_cards=PILOT_CARDS)


@pytest.fixture(scope="module")
def customers(cfg, population):
    return build_customers(cfg, population, np.random.default_rng(cfg.global_["seed"] + 1))


@pytest.fixture(scope="module")
def trips(cfg, population, customers, stores, zones):
    return build_trips(cfg, population, customers, stores, zones,
                       np.random.default_rng(cfg.global_["seed"] + 2))


@pytest.fixture(scope="module")
def items(cfg, trips, customers, catalog):
    return build_basket_items(cfg, trips, customers, catalog,
                              np.random.default_rng(cfg.global_["seed"] + 3))


@pytest.fixture(scope="module")
def items_x(items, catalog) -> pd.DataFrame:
    """Line items joined to products (taxonomy resolves via the sku join)."""
    p = catalog[["sku", "banner_code", "functional_department",
                 "functional_category", "functional_subcategory", "private_label"]].rename(
        columns={"functional_category": "category", "functional_subcategory": "subcategory"})
    return items.merge(p, on="sku", how="left")


# ----- catalog (precursor) ------------------------------------------

def test_catalog_per_merchant_sku_count(catalog) -> None:
    counts = catalog.groupby("banner_code").size().to_dict()
    for grocer in GROCERS:
        assert 900 <= counts[grocer] <= 1450, f"{grocer}: {counts[grocer]} SKUs"
    for qsr in ("TBL", "BKG", "CFA"):
        assert 50 <= counts[qsr] <= 95, f"{qsr}: {counts[qsr]} items"
    assert "TJX" not in counts


def test_catalog_pl_as_distinct_skus(catalog) -> None:
    g = catalog[catalog["segment"] == "grocery"]
    assert 0.10 < g["private_label"].mean() < 0.45
    # PL is a distinct SKU record, not just a flag on a shared row.
    assert g.loc[g["private_label"], "sku"].is_unique


def test_no_canonical_id_on_observable_catalog(catalog) -> None:
    assert "canonical_id" not in catalog.columns


# ----- affinity matrix (config-driven, functional_subcategory) ------

def test_affinity_matrix_is_config_driven(cfg) -> None:
    lookup = _build_affinity_lookup(cfg)
    assert lookup == {
        "Pasta":                   [("Pasta Sauce", 5.0)],
        "Pasta Sauce":             [("Pasta", 5.0)],
        "Potato & Tortilla Chips": [("Salsa & Dips", 3.5)],
        "Salsa & Dips":            [("Potato & Tortilla Chips", 3.5)],
        "Cereal":                  [("2% Reduced-Fat Milk", 3.5)],
        "2% Reduced-Fat Milk":     [("Cereal", 3.5)],
        "Sandwich Bread":          [("Butter", 2.5)],
        "Diapers & Wipes":         [("Formula & Baby Food", 2.5)],
    }


def test_affinity_subcat_guard_raises_on_typo(catalog) -> None:
    with pytest.raises(ConfigInvariantError, match="absent from"):
        _assert_affinity_subcats_exist({"NOTASUBCAT": [("ALSOBOGUS", 2.0)]}, catalog)
    _assert_affinity_subcats_exist(_build_affinity_lookup(load_config(CONFIG_ROOT)), catalog)


# ----- basket structure (minimal line schema) ----------------------

def test_required_columns(items) -> None:
    assert set(items.columns) == {"trip_id", "line_id", "sku", "qty"}


def test_every_trip_has_at_least_one_line(trips, items) -> None:
    missing = set(trips["trip_id"].unique()) - set(items["trip_id"].unique())
    assert not missing


def test_skus_unique_within_trip(items) -> None:
    assert (items.groupby("trip_id")["sku"].nunique() == items.groupby("trip_id").size()).all()


def test_qty_positive(items) -> None:
    assert (items["qty"] > 0).all()


# ----- basket size + archetype --------------------------------------

def test_grocery_basket_size_range(items, trips) -> None:
    g = set(trips[trips["segment"] == "grocery"]["trip_id"])
    sizes = items[items["trip_id"].isin(g)].groupby("trip_id").size()
    assert sizes.min() >= 1 and sizes.max() <= 35


def test_qsr_basket_size_small(items, trips) -> None:
    q = set(trips[trips["segment"] == "qsr"]["trip_id"])
    sizes = items[items["trip_id"].isin(q)].groupby("trip_id").size()
    assert sizes.max() <= 10 and sizes.median() <= 5


# ----- T11 affinity lift (functional_subcategory, via join) ---------

@pytest.mark.parametrize("anchor,partner,min_lift", [
    ("Pasta", "Pasta Sauce", 1.8),
    ("Cereal", "2% Reduced-Fat Milk", 1.8),
    ("Potato & Tortilla Chips", "Salsa & Dips", 1.8),
])
def test_T11_designed_affinity_lift(items_x, anchor, partner, min_lift) -> None:
    g = items_x[items_x["banner_code"].isin(GROCERS)]
    by_trip = g.groupby("trip_id")["subcategory"].apply(set)
    has_a = by_trip.apply(lambda s: anchor in s)
    p = by_trip.apply(lambda s: partner in s).mean()
    if has_a.sum() == 0 or p == 0:
        pytest.skip(f"insufficient {anchor}/{partner} at pilot")
    lift = by_trip[has_a].apply(lambda s: partner in s).mean() / p
    assert lift >= min_lift, f"{anchor}->{partner} lift {lift:.2f} < {min_lift}"


def test_qsr_combo_attach_materializes(items_x) -> None:
    """§B5: drink attaches to entrées, CFA >= BK >= TB ordering."""
    q = items_x[items_x["banner_code"].isin(("TBL", "BKG", "CFA"))]
    bb = q.groupby("trip_id").agg(cats=("category", set), banner=("banner_code", "first"))
    drink = {}
    for b in ("CFA", "BKG", "TBL"):
        ent = bb[(bb["banner"] == b) & bb["cats"].apply(lambda s: "Entrée" in s)]
        drink[b] = ent["cats"].apply(lambda s: "Beverages" in s).mean() if len(ent) else 0.0
    assert all(v > 0.30 for v in drink.values())
    assert drink["CFA"] >= drink["BKG"] >= drink["TBL"]


# ----- PL selection emerges by affluence ----------------------------

def test_pl_share_ordering_by_banner(items_x) -> None:
    """Realized PL unit share (measured from selection): KRG/WDX > ACM."""
    g = items_x[items_x["banner_code"].isin(GROCERS)]
    pl = {b: g[g["banner_code"] == b]["private_label"].mean() for b in GROCERS}
    assert pl["KRG"] > pl["ACM"] and pl["WDX"] > pl["ACM"]


# ----- reproducibility ----------------------------------------------

def test_reproducible_under_same_seed(cfg, trips, customers, catalog) -> None:
    a = build_basket_items(cfg, trips, customers, catalog, np.random.default_rng(cfg.global_["seed"] + 3))
    b = build_basket_items(cfg, trips, customers, catalog, np.random.default_rng(cfg.global_["seed"] + 3))
    pd.testing.assert_frame_equal(a, b)
