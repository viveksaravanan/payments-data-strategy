"""Stage 4.2 tests — ``lake_payment_mix`` (D23.3.2).

Schema, k≥50 floor, shares-sum-to-1 per cell, mobile-wallet provider
split present, no per-customer rows.
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.lake.build import K_MIN, build_lake_payment_mix


@pytest.fixture(scope="module")
def payment_mix() -> pd.DataFrame:
    return build_lake_payment_mix()


# ----- Schema -------------------------------------------------------------

def test_required_columns(payment_mix) -> None:
    required = {
        "banner_code", "derived_zone", "month_start", "txn_count",
        "contactless_share", "chip_share", "swipe_share", "manual_share",
        "credit_share", "debit_share",
        "visa_share", "mc_share", "amex_share", "discover_share",
        "wallet_share",
        "apple_share_within_wallet", "google_share_within_wallet",
        "samsung_share_within_wallet",
        "wifi_share", "ethernet_share", "cellular_share",
    }
    assert required.issubset(payment_mix.columns)


def test_no_peer_relationship_or_identity_columns(payment_mix) -> None:
    forbidden = {"peer_relationship", "customer_token", "card_id", "sku", "store_id"}
    assert not (forbidden & set(payment_mix.columns))


def test_all_five_merchants_present(payment_mix) -> None:
    assert set(payment_mix["banner_code"].unique()) == {
        "KRG", "ACM", "WDX", "TBL", "TJX",
    }


# ----- k ≥ 50 floor -------------------------------------------------------

def test_every_cell_meets_k_floor(payment_mix) -> None:
    print(
        f"\nL04b cells: {len(payment_mix):,}  | "
        f"min txn_count: {int(payment_mix['txn_count'].min())}  | "
        f"max: {int(payment_mix['txn_count'].max())}"
    )
    assert (payment_mix["txn_count"] >= K_MIN).all()


# ----- Shares sum to 1.0 per cell ----------------------------------------

def test_entry_mode_shares_sum_to_1(payment_mix) -> None:
    total = (
        payment_mix["contactless_share"]
        + payment_mix["chip_share"]
        + payment_mix["swipe_share"]
        + payment_mix["manual_share"]
    )
    # Floating-point tolerance.
    assert ((total - 1.0).abs() < 1e-9).all()


def test_tender_shares_sum_to_1(payment_mix) -> None:
    total = payment_mix["credit_share"] + payment_mix["debit_share"]
    assert ((total - 1.0).abs() < 1e-9).all()


def test_network_shares_sum_to_1(payment_mix) -> None:
    total = (
        payment_mix["visa_share"] + payment_mix["mc_share"]
        + payment_mix["amex_share"] + payment_mix["discover_share"]
    )
    # Some networks may be absent in a cell; total may be < 1 if any
    # txn has missing network. In Wave 1 every txn has a network, so
    # total should = 1.
    assert ((total - 1.0).abs() < 1e-9).all()


def test_connectivity_shares_sum_to_1(payment_mix) -> None:
    total = (
        payment_mix["wifi_share"] + payment_mix["ethernet_share"]
        + payment_mix["cellular_share"]
    )
    assert ((total - 1.0).abs() < 1e-9).all()


def test_wallet_provider_split_present(payment_mix) -> None:
    """When wallet_share > 0 (which it always is at full scale),
    the within-wallet provider split is published."""
    cells_with_wallet = payment_mix[payment_mix["wallet_share"] > 0]
    assert len(cells_with_wallet) > 0
    provider_total = (
        cells_with_wallet["apple_share_within_wallet"]
        + cells_with_wallet["google_share_within_wallet"]
        + cells_with_wallet["samsung_share_within_wallet"]
    )
    # ~1.0 when there's wallet usage; floating-point tolerance.
    assert ((provider_total - 1.0).abs() < 1e-9).all()


# ----- Shares in [0, 1] ----------------------------------------------------

@pytest.mark.parametrize("col", [
    "contactless_share", "chip_share", "swipe_share", "manual_share",
    "credit_share", "debit_share",
    "visa_share", "mc_share", "amex_share", "discover_share",
    "wallet_share",
    "apple_share_within_wallet", "google_share_within_wallet",
    "samsung_share_within_wallet",
    "wifi_share", "ethernet_share", "cellular_share",
])
def test_share_columns_in_unit_interval(payment_mix, col: str) -> None:
    s = payment_mix[col]
    assert (s >= 0).all() and (s <= 1).all()


# ----- Realism: known Wave 1 magnitudes carry through --------------------

def test_blended_contactless_share_in_expected_band(payment_mix) -> None:
    """Wave 1 DQ Report (full scale): blended contactless 53.5% (band
    48-55%). Weighted by txn_count, the lake aggregate should be close."""
    pop_weighted = (
        (payment_mix["contactless_share"] * payment_mix["txn_count"]).sum()
        / payment_mix["txn_count"].sum()
    )
    print(f"\nL04b lake blended contactless: {pop_weighted*100:.1f}%  (Wave 1 53.5%)")
    assert 0.48 <= pop_weighted <= 0.58


def test_blended_wallet_share_in_expected_band(payment_mix) -> None:
    """Wave 1 DQ Report: wallet-at-tap 16.7% (band 16-20%)."""
    pop_weighted = (
        (payment_mix["wallet_share"] * payment_mix["txn_count"]).sum()
        / payment_mix["txn_count"].sum()
    )
    print(f"L04b lake blended wallet-at-tap: {pop_weighted*100:.1f}%  (Wave 1 16.7%)")
    assert 0.13 <= pop_weighted <= 0.22
