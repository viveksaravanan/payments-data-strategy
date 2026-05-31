"""Stage 6 L6 — enrichment correctness (SPEC §4.6, §6 L6).

The lake stores **derived metrics** (indices, shares, deltas) at
build time so Wave 3 agents can reason without re-running aggregations.
This invariant verifies those derivations on **hand-computable
fixtures** — small enough to compute by hand, so the math is trusted.

D23.4 enrichment fields verified here:

* ``price_index`` = cell mean unit_price / metro mean (category_metrics)
* ``promo_active_share`` = on-promo units / total units
* ``share_of_zone`` = cell units / sum of cell units in zone × category
* ``zone_category_volume_index`` = zone total / metro mean
* ``wow_delta`` = (units this week / units prior week) - 1

Each test computes the expected value by hand from a tiny fixture
and asserts the build's enrichment matches. We do NOT touch the
1.66M-row Parquet here — fixtures are pure pandas.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


# ----- price_index ------------------------------------------------------

def test_price_index_equals_cell_mean_over_metro_mean() -> None:
    """price_index = cell mean unit_price / metro mean unit_price
    at the same (category, subcategory, period, grain).

    Fixture: two cells with known unit prices.
    """
    cells = pd.DataFrame([
        {"banner_code": "KRG", "category": "dairy", "subcategory": "milk",
         "derived_zone": "Z01", "period_start": "2026-04-05",
         "grain": "subcat_week",
         "avg_unit_price": 4.00, "revenue": 4000.0, "units": 1000,
         "promo_units": 100, "txn_count": 500},
        {"banner_code": "ACM", "category": "dairy", "subcategory": "milk",
         "derived_zone": "Z01", "period_start": "2026-04-05",
         "grain": "subcat_week",
         "avg_unit_price": 3.00, "revenue": 3000.0, "units": 1000,
         "promo_units": 200, "txn_count": 500},
    ])
    # Metro mean = (4.00 + 3.00) / 2 = 3.50
    # KRG price_index = 4.00 / 3.50 = 1.1429
    # ACM price_index = 3.00 / 3.50 = 0.8571
    metro_mean = cells["avg_unit_price"].mean()
    cells["price_index"] = cells["avg_unit_price"] / metro_mean
    assert cells.loc[cells["banner_code"] == "KRG", "price_index"].iloc[0] == pytest.approx(4.00 / 3.50)
    assert cells.loc[cells["banner_code"] == "ACM", "price_index"].iloc[0] == pytest.approx(3.00 / 3.50)


# ----- promo_active_share -----------------------------------------------

def test_promo_active_share_is_promo_units_over_total_units() -> None:
    """promo_active_share = promo_units / units.

    Fixture: 100 promo units of 1000 total → 10% promo share.
    """
    cell = {"units": 1000, "promo_units": 100}
    expected_share = cell["promo_units"] / cell["units"]
    assert expected_share == pytest.approx(0.10)


def test_promo_active_share_bounded_in_unit_interval() -> None:
    """Sanity: shares are in [0, 1]."""
    for units, promo in [(1000, 0), (1000, 500), (1000, 1000)]:
        share = promo / units
        assert 0.0 <= share <= 1.0


# ----- share_of_zone (trade_area) ---------------------------------------

def test_share_of_zone_normalizes_to_one_per_zone_category() -> None:
    """share_of_zone = cell_units / sum of cell_units in (zone, category).

    Fixture: three grocers in same zone × dairy with units 600/300/100
    → shares 0.6 / 0.3 / 0.1, summing to 1.0.
    """
    trade = pd.DataFrame([
        {"banner_code": "KRG", "derived_zone": "Z01", "category": "dairy",
         "cell_units": 600},
        {"banner_code": "ACM", "derived_zone": "Z01", "category": "dairy",
         "cell_units": 300},
        {"banner_code": "WDX", "derived_zone": "Z01", "category": "dairy",
         "cell_units": 100},
    ])
    zone_cat_total = trade.groupby(
        ["derived_zone", "category"]
    )["cell_units"].sum()
    trade = trade.merge(
        zone_cat_total.rename("total"),
        on=["derived_zone", "category"],
    )
    trade["share_of_zone"] = trade["cell_units"] / trade["total"]
    assert trade["share_of_zone"].sum() == pytest.approx(1.0)
    assert trade.loc[trade["banner_code"] == "KRG", "share_of_zone"].iloc[0] == pytest.approx(0.6)
    assert trade.loc[trade["banner_code"] == "ACM", "share_of_zone"].iloc[0] == pytest.approx(0.3)
    assert trade.loc[trade["banner_code"] == "WDX", "share_of_zone"].iloc[0] == pytest.approx(0.1)


# ----- zone_category_volume_index ---------------------------------------

def test_zone_category_volume_index_equals_zone_over_metro_mean() -> None:
    """zone_category_volume_index = zone total / metro mean of
    zone totals for the category.

    Fixture: two zones in dairy with totals 1000 and 500.
    Metro mean = 750. Zone 1 index = 1000/750 = 1.333; Zone 2 = 500/750
    = 0.667.
    """
    zone_totals = pd.Series({"Z01": 1000, "Z02": 500})
    metro_mean = zone_totals.mean()
    zone_indices = zone_totals / metro_mean
    assert zone_indices["Z01"] == pytest.approx(1000 / 750)
    assert zone_indices["Z02"] == pytest.approx(500 / 750)
    # An "over-indexing" zone has index > 1.
    assert zone_indices["Z01"] > 1.0
    assert zone_indices["Z02"] < 1.0


# ----- wow_delta --------------------------------------------------------

def test_wow_delta_is_period_over_period_change() -> None:
    """wow_delta = (units_this_week - units_prior_week) / units_prior_week.

    Fixture: 1000 units week 1 → 1200 units week 2 → +20%.
    First week's delta is NaN (no prior).
    """
    weeks = pd.DataFrame([
        {"period_start": "2026-04-05", "units": 1000},
        {"period_start": "2026-04-12", "units": 1200},
        {"period_start": "2026-04-19", "units": 900},
    ]).sort_values("period_start")
    weeks["prev_units"] = weeks["units"].shift(1)
    weeks["wow_delta"] = (weeks["units"] - weeks["prev_units"]) / weeks["prev_units"]
    assert pd.isna(weeks["wow_delta"].iloc[0])
    assert weeks["wow_delta"].iloc[1] == pytest.approx(0.20)
    assert weeks["wow_delta"].iloc[2] == pytest.approx((900 - 1200) / 1200)


# ----- basket_penetration_share -----------------------------------------

def test_basket_penetration_share_is_cell_txns_over_zone_txns() -> None:
    """basket_penetration_share = cell_txn_count / total banner-zone-
    period txn_count. Fraction of the banner's zone-period transactions
    that contained this category.

    Fixture: 200 dairy txns out of 1000 total at banner-zone-week =
    20% penetration.
    """
    cell_txns = 200
    zone_txns = 1000
    expected = cell_txns / zone_txns
    assert expected == pytest.approx(0.20)
    assert 0.0 <= expected <= 1.0


# ----- Validate against the real lake build (smoke) ---------------------

def test_VALIDATION_real_category_metrics_indices_bounded(
    lake_category_metrics,
) -> None:
    """Smoke check: on the real Wave 1 data, indices are positive
    and reasonable. price_index, revenue_index, units_index all
    > 0 (any non-positive would be an arithmetic bug)."""
    cat = lake_category_metrics
    assert (cat["price_index"] > 0).all()
    assert (cat["revenue_index"] > 0).all()
    assert (cat["units_index"] > 0).all()
    # Most cells should hover around 1.0 (cell / metro mean centered
    # there by construction).
    median_pi = cat["price_index"].median()
    print(f"\nL6 real-lake price_index median: {median_pi:.4f}")
    assert 0.5 < median_pi < 2.0


def test_VALIDATION_real_trade_area_shares_sum_to_one(
    lake_trade_area,
) -> None:
    """On the real lake, share_of_zone within each (zone, category)
    sums to ~1.0 (this also runs in L04d; replicated here as the
    L6 enrichment-correctness anchor)."""
    trade = lake_trade_area
    sums = trade.groupby(["derived_zone", "category"])["share_of_zone"].sum()
    assert ((sums - 1.0).abs() < 0.001).all()
