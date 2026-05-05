"""Generation tests. See PLAN.md §12.

Includes the cross-merchant ``customer_pan`` invariant (DATA.md §11) and the
EBT-only-at-Kroger rule (DATA.md §3, §10).
"""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.generate import parameters as P

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"

TXN_DTYPES = {
    "txn_id":       str,
    "merchant_id":  str,
    "customer_pan": str,
    "store_id":     str,
    "txn_ts":       str,
    "payment_type": str,
    "card_network": str,
    "entry_mode":   str,
    "wallet_type":  str,
}


@pytest.fixture(scope="module")
def customers() -> pd.DataFrame:
    return pd.read_csv(RAW / "customers.csv", dtype={"customer_pan": str, "home_zip5": str})


@pytest.fixture(scope="module")
def stores() -> pd.DataFrame:
    return pd.read_csv(RAW / "stores.csv", dtype={"store_id": str, "store_zip5": str})


@pytest.fixture(scope="module")
def transactions() -> pd.DataFrame:
    return pd.read_csv(RAW / "transactions.csv", dtype=TXN_DTYPES)


@pytest.fixture(scope="module")
def items() -> pd.DataFrame:
    return pd.read_csv(RAW / "transaction_items.csv",
                       dtype={"txn_id": str, "sku": str})


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------

def test_customers_count(customers: pd.DataFrame) -> None:
    assert len(customers) == P.N_CUSTOMERS


def test_customer_pan_unique(customers: pd.DataFrame) -> None:
    assert customers["customer_pan"].is_unique


def test_customer_pan_format(customers: pd.DataFrame) -> None:
    assert customers["customer_pan"].str.fullmatch(r"\d{16}").all()


def test_customers_have_pii(customers: pd.DataFrame) -> None:
    # PII intentionally present pre-anonymization (DATA.md §1).
    assert (customers["customer_name"].str.len() > 0).all()
    assert customers["customer_email"].str.contains("@").all()
    assert customers["home_zip5"].str.fullmatch(r"\d{5}").all()


def test_customers_deterministic() -> None:
    """Same seed -> identical customer panel (byte-for-byte)."""
    from src.generate.customers import generate_customers
    a = generate_customers(np.random.default_rng(P.RANDOM_SEED))
    b = generate_customers(np.random.default_rng(P.RANDOM_SEED))
    pd.testing.assert_frame_equal(a, b)


# ---------------------------------------------------------------------------
# Volume and per-merchant presence
# ---------------------------------------------------------------------------

def test_each_merchant_has_transactions(transactions: pd.DataFrame) -> None:
    counts = transactions["merchant_id"].value_counts()
    for m in ("KRG", "TBL", "TJX"):
        assert counts.get(m, 0) > 0, f"no transactions for {m}"


def test_total_volume_in_expected_range(transactions: pd.DataFrame) -> None:
    # DATA.md §4: ~109k transactions; allow generous bounds.
    assert 70_000 < len(transactions) < 160_000


# ---------------------------------------------------------------------------
# Numeric / structural integrity
# ---------------------------------------------------------------------------

def test_no_negative_quantities_or_prices(items: pd.DataFrame) -> None:
    assert (items["qty"] > 0).all()
    assert (items["unit_price"] >= 0).all()
    assert (items["line_total"] >= -0.01).all()


def test_line_total_consistency(items: pd.DataFrame) -> None:
    diff = (items["line_total"] - (items["qty"] * items["unit_price"] - items["discount"]))
    assert diff.abs().max() < 0.05


def test_foreign_keys(transactions, customers, stores, items) -> None:
    assert transactions["customer_pan"].isin(customers["customer_pan"]).all()
    assert transactions["store_id"].isin(stores["store_id"]).all()
    assert items["txn_id"].isin(transactions["txn_id"]).all()


def test_txn_dates_in_window(transactions: pd.DataFrame) -> None:
    ts = pd.to_datetime(transactions["txn_ts"])
    start = pd.Timestamp(P.START_DATE)
    end = pd.Timestamp(P.END_DATE) + pd.Timedelta(days=1)
    assert (ts >= start).all()
    assert (ts < end).all()


# ---------------------------------------------------------------------------
# Cross-merchant invariant (DATA.md §11)
# ---------------------------------------------------------------------------

def test_cross_merchant_pan_invariant(transactions: pd.DataFrame, customers: pd.DataFrame) -> None:
    # Master panel uniqueness: a `customer_pan` maps to exactly one physical customer.
    assert customers["customer_pan"].is_unique
    # Every `customer_pan` in transactions is in the master panel.
    assert transactions["customer_pan"].isin(customers["customer_pan"]).all()
    # Real cross-merchant overlap exists, otherwise the demo's punchline doesn't land.
    n_merchants_per_pan = transactions.groupby("customer_pan")["merchant_id"].nunique()
    assert (n_merchants_per_pan >= 2).sum() > 100


# ---------------------------------------------------------------------------
# EBT rule (DATA.md §10)
# ---------------------------------------------------------------------------

def test_ebt_only_at_kroger(transactions: pd.DataFrame) -> None:
    ebt_txns = transactions[transactions["payment_type"] == "ebt"]
    assert len(ebt_txns) > 0
    assert (ebt_txns["merchant_id"] == "KRG").all()


def test_no_ebt_at_taco_bell_or_tjmaxx(transactions: pd.DataFrame) -> None:
    non_kroger = transactions[transactions["merchant_id"].isin(["TBL", "TJX"])]
    assert (non_kroger["payment_type"] != "ebt").all()
