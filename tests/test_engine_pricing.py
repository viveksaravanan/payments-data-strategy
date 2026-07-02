"""Tests for src/generate/engine/pricing.py (datamodel-v2: FLAT shelf-price).

Pricing is now flat: realized unit_price = the catalog's baked shelf_price
(the v1 transaction-time modifier chain is gone — positioning/KVI/specialty/
PL are baked into shelf_price by scripts/build_catalog.py). The cross-banner
differentiation properties (no banner cheapest on everything, KVI-tight /
specialty-wide, PL discount) now live in the committed catalog and are
tested there.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.generate.config.loader import load_config
from src.generate.engine.catalog import load_products
from src.generate.engine.pricing import build_priced_items

CONFIG_ROOT = Path(__file__).resolve().parents[1] / "src" / "generate" / "config"
GROCERS = ("KRG", "ACM", "WDX")
# functional_subcategory KVI / specialty examples (vocab kvi/specialty flags).
KVI_SUBS = {"2% Reduced-Fat Milk", "Whole Milk", "Grade A Eggs", "Butter",
            "Bananas & Everyday Fruit", "Everyday Vegetables", "Ground Beef",
            "Chicken", "Potato & Tortilla Chips", "Soda", "Sandwich Bread"}
SPECIALTY_SUBS = {"Plant-Based Milk", "Specialty Cheese", "Organic & Specialty Fruit",
                  "Organic Vegetables", "Beef Steaks", "Fish & Shellfish", "Coffee",
                  "Ice Cream", "Nuts & Trail Mix"}


@pytest.fixture(scope="module")
def cfg():
    return load_config(CONFIG_ROOT)


@pytest.fixture(scope="module")
def catalog() -> pd.DataFrame:
    return load_products()


# ----- catalog carries a baked shelf_price -------------------------

def test_catalog_has_shelf_price(catalog) -> None:
    assert "shelf_price" in catalog.columns
    assert (catalog["shelf_price"] > 0).all()
    assert "canonical_id" not in catalog.columns   # hidden (data/eval)


# ----- flat pricing: unit_price == shelf_price ---------------------

def _synthetic(cfg, catalog):
    """One grocery + one QSR line, minimal basket schema (sku+qty)."""
    milk = catalog[catalog["functional_subcategory"] == "2% Reduced-Fat Milk"].iloc[0]
    qsr = catalog[catalog["segment"] == "qsr"].iloc[0]
    start = pd.Timestamp(cfg.global_["window"]["start_date"]) + pd.Timedelta(days=30)
    items = pd.DataFrame([
        {"trip_id": "T-1", "line_id": 1, "sku": milk["sku"], "qty": 2},
        {"trip_id": "T-2", "line_id": 1, "sku": qsr["sku"], "qty": 1},
    ])
    trips = pd.DataFrame([
        {"trip_id": "T-1", "banner_code": milk["banner_code"], "txn_ts": start},
        {"trip_id": "T-2", "banner_code": qsr["banner_code"], "txn_ts": start},
    ])
    return items, trips, {milk["sku"]: milk["shelf_price"], qsr["sku"]: qsr["shelf_price"]}


def test_flat_pricing_equals_shelf_price(cfg, catalog) -> None:
    items, trips, shelf = _synthetic(cfg, catalog)
    priced = build_priced_items(cfg, items, catalog, trips)
    for r in priced.itertuples(index=False):
        assert round(r.unit_price, 2) == round(shelf[r.sku], 2)


def test_line_total_equals_unit_price_times_qty(cfg, catalog) -> None:
    items, trips, _ = _synthetic(cfg, catalog)
    priced = build_priced_items(cfg, items, catalog, trips)
    assert np.allclose(priced["line_total"], priced["unit_price"] * priced["qty"], atol=0.01)


def test_priced_items_schema_minimal(cfg, catalog) -> None:
    """v2 line schema: no canonical_id / category / subcategory."""
    items, trips, _ = _synthetic(cfg, catalog)
    priced = build_priced_items(cfg, items, catalog, trips)
    assert set(priced.columns) == {
        "trip_id", "line_id", "sku", "qty", "unit_price",
        "discount", "promo_id", "line_total",
    }
    assert (priced["discount"] == 0).all()          # promos dormant
    assert priced["promo_id"].isna().all()


def test_priced_items_reproducible(cfg, catalog) -> None:
    items, trips, _ = _synthetic(cfg, catalog)
    a = build_priced_items(cfg, items, catalog, trips)
    b = build_priced_items(cfg, items, catalog, trips)
    pd.testing.assert_frame_equal(a, b)


# ----- T14 properties baked into shelf_price -----------------------

def test_T14_no_banner_cheapest_majority(catalog) -> None:
    g = catalog[catalog["segment"] == "grocery"]
    med = g.groupby(["functional_subcategory", "banner_code"])["shelf_price"].median().reset_index()
    cnt = med.groupby("functional_subcategory")["banner_code"].nunique()
    shared = cnt[cnt >= 2].index
    med = med[med["functional_subcategory"].isin(shared)]
    cheapest = med.loc[med.groupby("functional_subcategory")["shelf_price"].idxmin(), "banner_code"]
    shares = cheapest.value_counts(normalize=True).to_dict()
    print(f"\nT14 cheapest shares: " + ", ".join(f"{b} {shares.get(b,0)*100:.0f}%" for b in GROCERS))
    assert all(s <= 0.70 for s in shares.values())


def test_T14_private_label_gap(catalog) -> None:
    g = catalog[catalog["segment"] == "grocery"]
    by = g.groupby(["banner_code", "functional_subcategory", "private_label"])["shelf_price"].mean()
    by = by.unstack("private_label").dropna()
    by.columns = ["nb", "pl"]
    gap = (1 - by["pl"] / by["nb"]).mean()
    print(f"\nT14 PL gap: {gap*100:.1f}%")
    assert 0.08 <= gap <= 0.35


def test_T14_kvi_tighter_than_specialty(catalog) -> None:
    """KVI subcats have tighter cross-banner spread than specialty
    (KVI-dampener / specialty-amplifier baked into shelf_price)."""
    g = catalog[catalog["segment"] == "grocery"]
    med = g.groupby(["functional_subcategory", "banner_code"])["shelf_price"].median()
    def spread(subs):
        vals = []
        for sub in subs:
            s = med.loc[sub] if sub in med.index.get_level_values(0) else None
            if s is not None and len(s) >= 2 and s.min() > 0:
                vals.append((s.max() - s.min()) / s.min())
        return float(np.median(vals)) if vals else float("nan")
    kvi = spread(KVI_SUBS); spec = spread(SPECIALTY_SUBS)
    print(f"\nT14 spread: KVI {kvi:.3f} < specialty {spec:.3f}")
    assert kvi < spec
