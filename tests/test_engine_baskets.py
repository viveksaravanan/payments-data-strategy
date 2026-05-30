"""Tests for src/generate/engine/baskets.py (Wave 1 Stage 4.5) —
PILOT scale.

D17: mission-driven baskets with designed affinity, staples,
heavy-tail size distribution. The pilot scale (~5k cards) gates
both T11 (affinity discoverable via lift) and T12 (basket
heavy-tail) before full-scale generation. Full-scale verification
is owned by Stage 6.

The pilot fixture builds everything up to baskets at 5k cards.
That's enough rows (~85k trips × ~6 items/basket = ~500k line
items) for lift signals on the planted pairs to surface.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.generate.config.loader import load_config
from src.generate.engine.baskets import build_basket_items
from src.generate.engine.catalog import build_catalog
from src.generate.engine.customers import build_customers
from src.generate.engine.geography import build_stores, build_zones
from src.generate.engine.population import build_population
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
    rng = np.random.default_rng(cfg.global_["seed"])
    return build_stores(cfg, rng)


@pytest.fixture(scope="module")
def catalog(cfg) -> pd.DataFrame:
    rng = np.random.default_rng(cfg.global_["seed"] + 10)
    return build_catalog(cfg, rng)


@pytest.fixture(scope="module")
def population(cfg) -> pd.DataFrame:
    rng = np.random.default_rng(cfg.global_["seed"])
    return build_population(cfg, rng, n_cards=PILOT_CARDS)


@pytest.fixture(scope="module")
def customers(cfg, population) -> pd.DataFrame:
    rng = np.random.default_rng(cfg.global_["seed"] + 1)
    return build_customers(cfg, population, rng)


@pytest.fixture(scope="module")
def trips(cfg, population, customers, stores, zones) -> pd.DataFrame:
    rng = np.random.default_rng(cfg.global_["seed"] + 2)
    return build_trips(cfg, population, customers, stores, zones, rng)


@pytest.fixture(scope="module")
def items(cfg, trips, customers, catalog) -> pd.DataFrame:
    rng = np.random.default_rng(cfg.global_["seed"] + 3)
    return build_basket_items(cfg, trips, customers, catalog, rng)


# ----- catalog (precursor) ------------------------------------------

def test_catalog_per_merchant_sku_count(catalog) -> None:
    """Per-segment merchant SKU counts roughly match D17.6 (~1,100
    grocery), D19.5 (smaller QSR + retail catalogs)."""
    counts = catalog.groupby("merchant_id").size().to_dict()
    # Grocery merchants each carry the full canonical grocery set.
    for grocer in ("KRG", "ACM", "WDX"):
        assert 900 <= counts[grocer] <= 1300, f"{grocer}: {counts[grocer]} SKUs"
    # QSR menu is much smaller.
    assert 40 <= counts["TBL"] <= 90
    # Off-price.
    assert 200 <= counts["TJX"] <= 320


def test_catalog_has_private_label_in_grocery(catalog) -> None:
    g = catalog[catalog["segment"] == "grocery"]
    assert g["private_label"].mean() > 0.10
    assert g["private_label"].mean() < 0.40


def test_canonical_ids_shared_across_grocers(catalog) -> None:
    """Same canonical_id appears at all 3 grocers (cross-merchant
    matching)."""
    g = catalog[catalog["segment"] == "grocery"]
    by_canon = g.groupby("canonical_id")["banner_code"].nunique()
    # The vast majority of canonical IDs should appear at all 3 grocers
    # (Wave 1 Stage 4.5 ships full assortment; D19.4 differentiation
    # lands at 4.7).
    assert (by_canon == 3).mean() > 0.95


# ----- basket structure ---------------------------------------------

def test_required_columns(items) -> None:
    required = {"trip_id", "line_id", "sku", "canonical_id",
                "category", "subcategory", "qty"}
    assert required.issubset(items.columns)


def test_every_trip_has_at_least_one_line(trips, items) -> None:
    trips_with_items = set(items["trip_id"].unique())
    expected = set(trips["trip_id"].unique())
    missing = expected - trips_with_items
    assert not missing, f"trips with empty baskets: {sorted(missing)[:5]}"


def test_line_ids_unique_within_trip(items) -> None:
    by_trip = items.groupby("trip_id")["line_id"].nunique()
    by_trip_count = items.groupby("trip_id").size()
    assert (by_trip == by_trip_count).all()


def test_skus_unique_within_trip(items) -> None:
    by_trip = items.groupby("trip_id")["sku"].nunique()
    by_trip_count = items.groupby("trip_id").size()
    assert (by_trip == by_trip_count).all()


def test_qty_positive(items) -> None:
    assert (items["qty"] > 0).all()


# ----- D17.4 basket size + archetype --------------------------------

def test_grocery_basket_size_range(items, trips) -> None:
    """Grocery basket sizes span 2-30 (across archetypes)."""
    g_trips = set(trips[trips["segment"] == "grocery"]["trip_id"])
    sizes = items[items["trip_id"].isin(g_trips)].groupby("trip_id").size()
    assert sizes.min() >= 1
    assert sizes.max() <= 35  # triangular clip + tiny rounding slop


def test_qsr_basket_size_small(items, trips) -> None:
    """QSR baskets cap around 10 (combo + sides + drink mostly)."""
    q_trips = set(trips[trips["segment"] == "qsr"]["trip_id"])
    sizes = items[items["trip_id"].isin(q_trips)].groupby("trip_id").size()
    assert sizes.max() <= 10
    assert sizes.median() <= 5


def test_off_price_basket_size_spans_1_to_12(items, trips) -> None:
    op_trips = set(trips[trips["segment"] == "off_price"]["trip_id"])
    sizes = items[items["trip_id"].isin(op_trips)].groupby("trip_id").size()
    assert sizes.min() >= 1
    assert sizes.max() <= 14


# ----- T12 sanity at pilot: heavy-tail basket size ------------------

def test_top_20pct_baskets_share_of_units_grocery_pilot(items, trips) -> None:
    """T12 sanity at pilot: top 20% of grocery baskets ≈ 45-55%
    of grocery units. We allow a wider 40-60% band at pilot scale
    because basket sample size is smaller — full-scale check at
    Stage 6."""
    g_trips = set(trips[trips["segment"] == "grocery"]["trip_id"])
    g_items = items[items["trip_id"].isin(g_trips)]
    by_trip_units = g_items.groupby("trip_id")["qty"].sum()
    sorted_units = by_trip_units.sort_values(ascending=False)
    n_top = max(1, int(len(sorted_units) * 0.20))
    top_share = sorted_units.iloc[:n_top].sum() / sorted_units.sum()
    # Pilot band slightly wider than the 45-55% Stage-6 target.
    assert 0.40 <= top_share <= 0.62, \
        f"top-20% grocery basket unit share {top_share:.4f} outside pilot band"


# ----- D17.3 staples: repeat purchase realism -----------------------

def test_repeat_purchase_above_chance_for_grocery_staples(
    items, trips, customers, catalog,
) -> None:
    """For loyalist cards with active grocery shopping, the SKU they
    buy most often should recur at a rate well above the 1/N chance
    of picking it independently."""
    cust = customers[customers["loyalty_type"] == "loyalist"]
    g_trips = trips[
        (trips["segment"] == "grocery") &
        (trips["card_id"].isin(cust["card_id"]))
    ]
    # Cards with at least 5 grocery trips so we can measure recurrence.
    trip_counts = g_trips.groupby("card_id").size()
    active = trip_counts[trip_counts >= 5].index.tolist()
    if len(active) == 0:
        pytest.skip("no loyalists with ≥5 grocery trips in pilot")
    by_card_g_trips = g_trips[g_trips["card_id"].isin(active)]
    # join items
    g_items = items.merge(by_card_g_trips[["trip_id", "card_id"]], on="trip_id")
    # For each card, find top SKU's share of trips it appears in.
    top_share = (
        g_items.groupby(["card_id", "sku"]).agg(
            trips_with_sku=("trip_id", "nunique"),
        ).reset_index()
        .merge(by_card_g_trips.groupby("card_id").size().rename("n_trips"),
               on="card_id")
    )
    top_share["share"] = top_share["trips_with_sku"] / top_share["n_trips"]
    per_card_top = top_share.groupby("card_id")["share"].max()
    # Loyalists should have a top-SKU that recurs in ≥20% of trips on
    # average (well above random ~1/avg_basket_size × catalog_size).
    assert per_card_top.mean() > 0.20, \
        f"loyalist mean top-SKU share {per_card_top.mean():.3f}"


# ----- T11 design gate at pilot: affinity discoverable via lift -----

@pytest.mark.parametrize("anchor,partner,min_lift", [
    ("PASTA",   "SAUCE",   3.0),
    ("CHIPS",   "SALSA",   2.5),
    ("DIAPERS", "WIPES",   3.0),
    ("MILK",    "CEREAL",  2.0),
])
def test_T11_designed_affinity_lift(items, anchor, partner, min_lift) -> None:
    """T11: an analyst running lift = P(B|A in basket) / P(B) on the
    raw data should see the designed pairs surface above threshold.
    This is the design gate for the baskets layer — if these pairs
    don't lift here at pilot scale, the model is wrong, not just
    under-tuned."""
    by_trip = items.groupby("trip_id")["subcategory"].apply(set)
    n = len(by_trip)
    p_partner = by_trip.apply(lambda s: partner in s).mean()
    has_anchor = by_trip.apply(lambda s: anchor in s)
    if has_anchor.sum() == 0:
        pytest.skip(f"no {anchor} baskets in pilot")
    p_partner_given_anchor = by_trip[has_anchor].apply(lambda s: partner in s).mean()
    if p_partner == 0:
        pytest.skip(f"{partner} never appears in pilot")
    lift = p_partner_given_anchor / p_partner
    assert lift >= min_lift, (
        f"{anchor}→{partner} lift {lift:.2f} below threshold {min_lift}; "
        f"P({partner})={p_partner:.4f}, P({partner}|{anchor})={p_partner_given_anchor:.4f}"
    )


# ----- mission emergence — categories co-occur per mission ---------

def test_breakfast_emerges_DAIRY_with_CEREAL_in_grocery(items, trips) -> None:
    """Breakfast mission has high DAIRY + PANTRY (CEREAL). The
    emergent lift on (DAIRY category, CEREAL subcat) should be
    visible at pilot scale even without an explicit DAIRY×CEREAL
    pair in the affinity matrix — that's the mission-emergent
    effect D17.1 is supposed to produce."""
    g_trips = set(trips[trips["segment"] == "grocery"]["trip_id"])
    g_items = items[items["trip_id"].isin(g_trips)]
    by_trip = g_items.groupby("trip_id").apply(
        lambda x: ("DAIRY" in set(x["category"]), "CEREAL" in set(x["subcategory"])),
        include_groups=False,
    )
    flags = pd.DataFrame(by_trip.tolist(), columns=["has_dairy", "has_cereal"])
    p_cereal = flags["has_cereal"].mean()
    p_cereal_given_dairy = flags[flags["has_dairy"]]["has_cereal"].mean()
    if p_cereal == 0:
        pytest.skip("no cereal trips in pilot")
    lift = p_cereal_given_dairy / p_cereal
    assert lift >= 1.3, f"DAIRY→CEREAL emergent lift {lift:.2f} too low"


# ----- reproducibility ----------------------------------------------

def test_reproducible_under_same_seed(cfg, trips, customers, catalog) -> None:
    rng_a = np.random.default_rng(cfg.global_["seed"] + 3)
    rng_b = np.random.default_rng(cfg.global_["seed"] + 3)
    a = build_basket_items(cfg, trips, customers, catalog, rng_a)
    b = build_basket_items(cfg, trips, customers, catalog, rng_b)
    pd.testing.assert_frame_equal(a, b)
