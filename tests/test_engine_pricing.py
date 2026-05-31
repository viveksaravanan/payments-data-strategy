"""Tests for src/generate/engine/pricing.py (Wave 1 Stage 4.7).

D19 pricing — anchor × strategy × zone × time × noise. T14 in §6:
- No banner cheapest on >70% of comparable canonical SKUs.
- Private-label gap ~25%+.
- KVI cross-banner spread tight (<~10%).
- Specialty spread wider.

Most tests use synthetic mini-frames to exercise the pricing logic
directly (fast). Only the AOV check uses the pilot fixture chain
(slow first run for trips + baskets).
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
from src.generate.engine.pricing import (
    _PL_FACTOR,
    _effective_category_mult,
    _per_sku_competitive_index,
    build_priced_items,
)
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


# ----- catalog has base_price now ----------------------------------

def test_catalog_has_base_price(catalog) -> None:
    assert "base_price" in catalog.columns
    assert (catalog["base_price"] > 0).all()


def test_canonical_id_base_price_consistent_across_grocers(catalog) -> None:
    """Same canonical_id has the same base_price at every grocer
    (per-merchant strategy modulates at Stage 4.7, base is shared)."""
    g = catalog[catalog["segment"] == "grocery"]
    by_canon = g.groupby("canonical_id")["base_price"].nunique()
    assert (by_canon == 1).mean() > 0.99


# ----- T14 — pricing strategy emerges in catalog "rack" prices -----

def _catalog_rack_prices(catalog: pd.DataFrame) -> pd.DataFrame:
    """Compute the per-(banner, canonical) rack price using catalog
    strategy alone (base × per-merchant category × PL × competitive
    index). Excludes zone/time/noise so we measure the pure strategy
    signal — what a price-comparison shopper would see on the
    shelf-tag without per-store/per-day variance."""
    rows = []
    for r in catalog.itertuples(index=False):
        cat_mult = _effective_category_mult(r.banner_code, r.category, r.subcategory)
        pl_mult = _PL_FACTOR.get(r.banner_code, 1.0) if r.private_label else 1.0
        comp_idx = _per_sku_competitive_index(r.banner_code, r.canonical_id)
        rows.append({
            "banner_code": r.banner_code,
            "canonical_id": r.canonical_id,
            "category": r.category,
            "subcategory": r.subcategory,
            "private_label": r.private_label,
            "base_price": r.base_price,
            "rack_price": round(r.base_price * cat_mult * pl_mult * comp_idx, 2),
        })
    return pd.DataFrame(rows)


def test_T14_no_banner_cheapest_on_more_than_70pct(catalog) -> None:
    """T14: no single banner cheapest on >70% of comparable SKUs.
    Comparison is per canonical_id within grocery (the 3 grocers).
    Tested at the catalog rack-price level so noise doesn't muddy
    the strategy signal."""
    rack = _catalog_rack_prices(catalog)
    g = rack[rack["banner_code"].isin(["KRG", "ACM", "WDX"])]
    # For each canonical_id, find the banner with the lowest rack price.
    cheapest = g.loc[g.groupby("canonical_id")["rack_price"].idxmin()]
    shares = cheapest["banner_code"].value_counts(normalize=True).to_dict()
    print(f"\nT14 cheapest-banner shares: {shares}")
    assert all(s <= 0.70 for s in shares.values()), \
        f"banner dominates cheapest: {shares}"


def test_T14_private_label_gap_about_25pct(catalog) -> None:
    """T14: PL gap ~25%+ at the population level. Measured per
    (banner, subcategory): PL price vs national-brand price for SKUs
    in the same subcategory."""
    rack = _catalog_rack_prices(catalog)
    g = rack[rack["banner_code"].isin(["KRG", "ACM", "WDX"])]
    # Per (banner, subcategory): mean PL price / mean NB price.
    pivot = g.groupby(["banner_code", "subcategory", "private_label"])["rack_price"].mean()
    pivot = pivot.unstack("private_label")
    pivot.columns = ["nb_mean", "pl_mean"]
    pivot = pivot.dropna()
    pivot["gap"] = 1 - pivot["pl_mean"] / pivot["nb_mean"]
    blended = pivot["gap"].mean()
    print(f"\nT14 PL gap (blended across banners): {blended:.4f}")
    assert 0.18 <= blended <= 0.35, f"PL gap {blended:.4f} outside band"


def test_T14_KVI_spread_tight(catalog) -> None:
    """T14: KVI cross-banner spread tight (<~10%). KVI = known-value
    items: staples shoppers price-check. Use MILK, EGGS, BREAD,
    BUTTER as canonical KVIs."""
    rack = _catalog_rack_prices(catalog)
    g = rack[rack["banner_code"].isin(["KRG", "ACM", "WDX"])]
    kvi_subs = {"MILK", "EGGS", "BREAD", "BUTTER"}
    kvi = g[g["subcategory"].isin(kvi_subs)]
    # For each canonical, compute spread = (max - min) / min across banners.
    spreads = kvi.groupby("canonical_id")["rack_price"].agg(
        lambda x: (x.max() - x.min()) / x.min() if x.min() > 0 else 0
    )
    median_spread = spreads.median()
    print(f"\nT14 KVI median cross-banner spread: {median_spread:.4f}")
    assert median_spread <= 0.15, f"KVI spread {median_spread:.4f} too wide"


def test_T14_specialty_spread_wider_than_KVI(catalog) -> None:
    """T14: specialty subcategories should show wider cross-banner
    spread than KVI. Specialty = items where strategy differentiates
    (e.g. premium organic/specialty vs value-PL)."""
    rack = _catalog_rack_prices(catalog)
    g = rack[rack["banner_code"].isin(["KRG", "ACM", "WDX"])]
    kvi_subs = {"MILK", "EGGS", "BREAD", "BUTTER"}
    specialty_subs = {"DELI", "SEAFOOD", "PASTRIES", "FORMULA", "COFFEE"}
    kvi = g[g["subcategory"].isin(kvi_subs)]
    spec = g[g["subcategory"].isin(specialty_subs)]
    kvi_spreads = kvi.groupby("canonical_id")["rack_price"].agg(
        lambda x: (x.max() - x.min()) / x.min() if x.min() > 0 else 0
    ).median()
    spec_spreads = spec.groupby("canonical_id")["rack_price"].agg(
        lambda x: (x.max() - x.min()) / x.min() if x.min() > 0 else 0
    ).median()
    print(f"\nT14 KVI vs specialty median spreads: KVI={kvi_spreads:.4f} vs specialty={spec_spreads:.4f}")
    assert spec_spreads > kvi_spreads, \
        f"specialty {spec_spreads:.4f} should exceed KVI {kvi_spreads:.4f}"


# ----- pricing logic — synthetic mini-input -----------------------

def _synthetic_inputs(cfg, catalog, stores) -> tuple:
    """Build a tiny synthetic item frame to exercise pricing logic
    in isolation."""
    # Pick one SKU per grocer for the same canonical_id (e.g., a milk).
    canonical = catalog[catalog["subcategory"] == "MILK"]["canonical_id"].iloc[0]
    same_canonical = catalog[catalog["canonical_id"] == canonical]
    # Use first store of each banner.
    rows = []
    trip_rows = []
    window_mid = pd.Timestamp(cfg.global_["window"]["start_date"]) + pd.Timedelta(days=45)
    for i, (_, sku_row) in enumerate(same_canonical.iterrows()):
        banner = sku_row["banner_code"]
        store_id = stores[stores["banner_code"] == banner]["store_id"].iloc[0]
        trip_id = f"T-SYNTH-{i:03d}"
        rows.append({
            "trip_id": trip_id, "line_id": 1, "sku": sku_row["sku"],
            "canonical_id": sku_row["canonical_id"],
            "category": sku_row["category"], "subcategory": sku_row["subcategory"],
            "qty": 1,
        })
        trip_rows.append({
            "trip_id": trip_id, "card_id": "synthetic",
            "segment": sku_row["segment"], "banner_code": banner,
            "store_id": store_id, "txn_ts": window_mid,
        })
    return pd.DataFrame(rows), pd.DataFrame(trip_rows)


def test_synthetic_per_merchant_strategy_visible(cfg, catalog, stores, zones) -> None:
    """At the same store-zone + same date + same canonical SKU, the
    per-merchant strategy is visible: WDX should be below ACM
    consistently (value vs premium). KRG vs ACM ordering can flip
    on a single SKU because the per-SKU competitive index (±5%)
    operates at the SKU level — that's the deliberate "idiosyncratic
    lever" of D19.2 that smears per-banner ordering. The systematic
    ordering only emerges across many SKUs (tested in T14).
    """
    items, trips = _synthetic_inputs(cfg, catalog, stores)
    rng = np.random.default_rng(0)
    priced = build_priced_items(cfg, items, catalog, trips, stores, zones, rng)
    by_banner = priced.merge(trips[["trip_id", "banner_code"]], on="trip_id")
    prices = by_banner.groupby("banner_code")["unit_price"].first().to_dict()
    print(f"\nSynthetic milk price by banner: {prices}")
    assert prices["WDX"] < prices["ACM"], \
        f"WDX {prices['WDX']:.2f} should be cheaper than ACM {prices['ACM']:.2f}"


def test_priced_items_schema(cfg, catalog, stores, zones) -> None:
    items, trips = _synthetic_inputs(cfg, catalog, stores)
    rng = np.random.default_rng(0)
    priced = build_priced_items(cfg, items, catalog, trips, stores, zones, rng)
    required = {"trip_id", "line_id", "sku", "canonical_id",
                "category", "subcategory", "qty", "unit_price", "line_total"}
    assert required.issubset(priced.columns)
    assert (priced["unit_price"] > 0).all()
    assert (priced["line_total"] > 0).all()


def test_priced_items_line_total_equals_unit_price_times_qty(
    cfg, catalog, stores, zones,
) -> None:
    items, trips = _synthetic_inputs(cfg, catalog, stores)
    items["qty"] = [3, 2, 1]
    rng = np.random.default_rng(0)
    priced = build_priced_items(cfg, items, catalog, trips, stores, zones, rng)
    assert np.allclose(
        priced["line_total"], priced["unit_price"] * priced["qty"],
        atol=0.01,
    )


def test_priced_items_reproducible(cfg, catalog, stores, zones) -> None:
    items, trips = _synthetic_inputs(cfg, catalog, stores)
    rng_a = np.random.default_rng(0)
    rng_b = np.random.default_rng(0)
    a = build_priced_items(cfg, items, catalog, trips, stores, zones, rng_a)
    b = build_priced_items(cfg, items, catalog, trips, stores, zones, rng_b)
    pd.testing.assert_frame_equal(a, b)


# ----- AOV — T2 — needs the pilot fixture chain --------------------

@pytest.fixture(scope="module")
def pilot_priced_items(cfg, zones, stores, catalog) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Slow pilot fixture: full chain → priced items.
    Returns (priced_items, trips) for AOV computation."""
    pop = build_population(cfg, np.random.default_rng(cfg.global_["seed"]),
                           n_cards=PILOT_CARDS)
    cust = build_customers(cfg, pop, np.random.default_rng(cfg.global_["seed"] + 1))
    trips = build_trips(
        cfg, pop, cust, stores, zones,
        np.random.default_rng(cfg.global_["seed"] + 2),
    )
    basket = build_basket_items(
        cfg, trips, cust, catalog,
        np.random.default_rng(cfg.global_["seed"] + 3),
    )
    priced = build_priced_items(
        cfg, basket, catalog, trips, stores, zones,
        np.random.default_rng(cfg.global_["seed"] + 5),
    )
    return priced, trips


def test_T2_grocery_aov_in_band(pilot_priced_items) -> None:
    """T2 in §6: grocery AOV $48-62 blended."""
    priced, trips = pilot_priced_items
    by_trip = priced.groupby("trip_id")["line_total"].sum()
    g_trips = trips[trips["segment"] == "grocery"]["trip_id"]
    g_aov = by_trip.loc[by_trip.index.intersection(g_trips)].mean()
    print(f"\nT2 grocery AOV: ${g_aov:.2f}")
    assert 40 <= g_aov <= 70, f"grocery AOV ${g_aov:.2f} outside pilot band"


def test_T2_qsr_aov_in_band(pilot_priced_items) -> None:
    """T2 in §6: QSR AOV $9-12."""
    priced, trips = pilot_priced_items
    by_trip = priced.groupby("trip_id")["line_total"].sum()
    q_trips = trips[trips["segment"] == "qsr"]["trip_id"]
    q_aov = by_trip.loc[by_trip.index.intersection(q_trips)].mean()
    print(f"\nT2 QSR AOV: ${q_aov:.2f}")
    assert 7 <= q_aov <= 15, f"QSR AOV ${q_aov:.2f} outside pilot band"


def test_T2_off_price_aov_in_band(pilot_priced_items) -> None:
    """T2 in §6: off-price AOV $30-50."""
    priced, trips = pilot_priced_items
    by_trip = priced.groupby("trip_id")["line_total"].sum()
    op_trips = trips[trips["segment"] == "off_price"]["trip_id"]
    op_aov = by_trip.loc[by_trip.index.intersection(op_trips)].mean()
    print(f"\nT2 off-price AOV: ${op_aov:.2f}")
    assert 25 <= op_aov <= 60, f"off-price AOV ${op_aov:.2f} outside pilot band"
