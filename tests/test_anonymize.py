"""Anonymization tests. See PLAN.md §12.

Verifies the dual-path guarantees:
  * Stage 1 strips PII and produces a deterministic 16-hex `customer_id`.
  * Stage 2 produces ZIP3 + hour buckets + category-level items, and k=5
    anonymity holds on every non-NULL group.
"""
from pathlib import Path

import pandas as pd
import pytest

from src.anonymize.hash import customer_id_for
from src.generate import parameters as P

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
TENANT = ROOT / "data" / "anon" / "tenant"
LAKE = ROOT / "data" / "anon" / "lake"


@pytest.fixture(scope="module")
def tenant_customers() -> pd.DataFrame:
    return pd.read_csv(TENANT / "customers.csv",
                       dtype={"customer_id": str, "home_zip5": str})


@pytest.fixture(scope="module")
def tenant_transactions() -> pd.DataFrame:
    return pd.read_csv(TENANT / "transactions.csv",
                       dtype={"customer_id": str, "txn_id": str, "store_id": str})


@pytest.fixture(scope="module")
def tenant_items() -> pd.DataFrame:
    return pd.read_csv(TENANT / "transaction_items.csv",
                       dtype={"txn_id": str, "sku": str})


@pytest.fixture(scope="module")
def lake_customers() -> pd.DataFrame:
    return pd.read_csv(LAKE / "customers.csv",
                       dtype={"customer_id": str, "home_zip3": str})


@pytest.fixture(scope="module")
def lake_transactions() -> pd.DataFrame:
    return pd.read_csv(LAKE / "transactions.csv",
                       dtype={"customer_id": str, "txn_id": str, "store_zip3": str})


@pytest.fixture(scope="module")
def lake_items() -> pd.DataFrame:
    return pd.read_csv(LAKE / "transaction_items.csv", dtype={"txn_id": str})


# ---------------------------------------------------------------------------
# Hash helper — deterministic
# ---------------------------------------------------------------------------

def test_customer_id_for_is_deterministic() -> None:
    pan = "1234567890123456"
    a, b = customer_id_for(pan), customer_id_for(pan)
    assert a == b
    assert len(a) == 16
    assert all(ch in "0123456789abcdef" for ch in a)


def test_customer_id_for_differs_between_pans() -> None:
    assert customer_id_for("1111111111111111") != customer_id_for("2222222222222222")


# ---------------------------------------------------------------------------
# Stage 1 — Tenant
# ---------------------------------------------------------------------------

def test_tenant_customers_dropped_pii(tenant_customers: pd.DataFrame) -> None:
    assert "customer_name" not in tenant_customers.columns
    assert "customer_email" not in tenant_customers.columns
    assert "customer_pan" not in tenant_customers.columns


def test_tenant_customer_id_is_16_hex(tenant_customers: pd.DataFrame) -> None:
    assert tenant_customers["customer_id"].str.fullmatch(r"[0-9a-f]{16}").all()
    assert tenant_customers["customer_id"].is_unique


def test_tenant_keeps_full_zip_and_timestamp(
    tenant_customers: pd.DataFrame, tenant_transactions: pd.DataFrame
) -> None:
    assert tenant_customers["home_zip5"].str.fullmatch(r"\d{5}").all()
    assert tenant_transactions["txn_ts"].str.match(
        r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}"
    ).all()


def test_tenant_transactions_use_customer_id_not_pan(
    tenant_transactions: pd.DataFrame,
) -> None:
    assert "customer_pan" not in tenant_transactions.columns
    assert tenant_transactions["customer_id"].str.fullmatch(r"[0-9a-f]{16}").all()


def test_tenant_keeps_sku_level_items(tenant_items: pd.DataFrame) -> None:
    assert "sku" in tenant_items.columns
    assert tenant_items["sku"].str.contains("-").all()  # `KRG-*`/`TBL-*`/`TJX-*`


# ---------------------------------------------------------------------------
# Stage 2 — Lake
# ---------------------------------------------------------------------------

def test_lake_customers_have_zip3_or_null(lake_customers: pd.DataFrame) -> None:
    assert "home_zip5" not in lake_customers.columns
    non_null = lake_customers["home_zip3"].dropna().astype(str)
    assert non_null.str.fullmatch(r"\d{3}").all()


def test_lake_transactions_have_hour_bucket_and_zip3(
    lake_transactions: pd.DataFrame,
) -> None:
    assert "txn_hour_bucket" in lake_transactions.columns
    assert "store_zip3" in lake_transactions.columns
    assert "store_id" not in lake_transactions.columns
    assert lake_transactions["txn_hour_bucket"].str.match(
        r"\d{4}-\d{2}-\d{2} \d{2}:00:00"
    ).all()
    assert lake_transactions["store_zip3"].str.fullmatch(r"\d{3}").all()


def test_lake_items_collapsed_to_category(lake_items: pd.DataFrame) -> None:
    assert "sku_category" in lake_items.columns
    assert "sku" not in lake_items.columns
    # No SKU-level identifier leakage in the category column.
    assert not lake_items["sku_category"].astype(str).str.contains(r"-\d{3,}").any()


def test_lake_k_anonymity_holds(lake_customers: pd.DataFrame) -> None:
    not_suppressed = lake_customers.dropna(subset=["home_zip3"])
    counts = not_suppressed.groupby(
        ["age_band", "income_band", "home_zip3"]
    ).size()
    assert (counts >= P.K_ANONYMITY_THRESHOLD).all(), (
        f"k={P.K_ANONYMITY_THRESHOLD} violated: smallest non-suppressed group "
        f"has {counts.min()} customers"
    )


def test_lake_suppression_is_nonempty(lake_customers: pd.DataFrame) -> None:
    # On the default seed we expect at least some suppression — otherwise k=5
    # is never being exercised on this panel and the demo's privacy story is
    # vacuous.
    n_suppressed = int(lake_customers["home_zip3"].isna().sum())
    assert n_suppressed > 0


# ---------------------------------------------------------------------------
# Cross-merchant invariant in the lake
# ---------------------------------------------------------------------------

def test_lake_cross_merchant_join_works(lake_transactions: pd.DataFrame) -> None:
    # The same physical customer's hashed `customer_id` must appear under
    # multiple merchant_ids — that's the whole point of the lake.
    counts = lake_transactions.groupby("customer_id")["merchant_id"].nunique()
    assert (counts >= 2).sum() > 100


def test_same_pan_yields_same_id_across_merchants() -> None:
    """Round-trip: pick a customer_pan, hash with the helper, and verify the
    same value appears in multiple merchants' tenant transactions."""
    raw_customers = pd.read_csv(RAW / "customers.csv", dtype={"customer_pan": str})
    raw_txns = pd.read_csv(
        RAW / "transactions.csv", dtype={"customer_pan": str}
    )
    multi = raw_txns.groupby("customer_pan")["merchant_id"].nunique()
    sample_pan = multi[multi >= 2].index[0]
    expected_id = customer_id_for(sample_pan)
    tenant_txns = pd.read_csv(
        TENANT / "transactions.csv", dtype={"customer_id": str}
    )
    rows = tenant_txns[tenant_txns["customer_id"] == expected_id]
    assert rows["merchant_id"].nunique() >= 2
    # Sanity: this customer also exists in the master panel.
    assert raw_customers[raw_customers["customer_pan"] == sample_pan].shape[0] == 1
