"""Stage 4.4 tests — ``lake_trade_area`` (D23.3.4)."""
from __future__ import annotations

import pandas as pd
import pytest

from src.lake.build import K_MIN, build_lake_trade_area


@pytest.fixture(scope="module")
def trade_area() -> pd.DataFrame:
    return build_lake_trade_area()


def test_required_columns(trade_area) -> None:
    required = {
        "banner_code", "derived_zone", "category",
        "store_count", "cell_units", "cell_revenue",
        "share_of_zone", "zone_category_volume_index", "txn_count",
    }
    assert required.issubset(trade_area.columns)


def test_no_identity_or_peer_columns(trade_area) -> None:
    forbidden = {"customer_token", "sku", "store_id",
                 "peer_relationship", "subcategory"}
    assert not (forbidden & set(trade_area.columns))


def test_every_cell_meets_k_floor(trade_area) -> None:
    print(
        f"\nL04d cells: {len(trade_area):,}  | "
        f"min txn_count: {int(trade_area['txn_count'].min())}  | "
        f"max: {int(trade_area['txn_count'].max())}"
    )
    assert (trade_area["txn_count"] >= K_MIN).all()


def test_share_of_zone_sums_to_1_per_zone_category(trade_area) -> None:
    """Across all merchants in a (zone, category), shares sum to 1.0."""
    total = trade_area.groupby(
        ["derived_zone", "category"]
    )["share_of_zone"].sum()
    # Allow tiny tolerance.
    assert ((total - 1.0).abs() < 0.001).all()


def test_share_of_zone_in_unit_interval(trade_area) -> None:
    assert (trade_area["share_of_zone"] >= 0).all()
    assert (trade_area["share_of_zone"] <= 1).all()


def test_zone_category_volume_index_positive(trade_area) -> None:
    assert (trade_area["zone_category_volume_index"] > 0).all()


def test_store_count_positive(trade_area) -> None:
    assert (trade_area["store_count"] > 0).all()


def test_all_five_merchants_present(trade_area) -> None:
    """At least somewhere — some merchants don't appear in some zones."""
    assert set(trade_area["banner_code"].unique()) == {
        "KRG", "ACM", "WDX", "TBL", "TJX",
    }
