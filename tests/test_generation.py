"""Generation tests. See PLAN.md §12.

Includes the cross-merchant ``customer_pan`` invariant (DATA.md §11) and the
EBT-only-at-Kroger rule (DATA.md §3, §10).
"""
from datetime import timedelta
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


# ---------------------------------------------------------------------------
# v2 regression tests — realistic catalog and planted anomalies
# ---------------------------------------------------------------------------

import sqlite3  # noqa: E402

ROOT_DIR = Path(__file__).resolve().parents[1]
_DB_PATH = ROOT_DIR / "data" / "payments.db"


def _conn() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True)


def test_no_placeholder_kroger_names() -> None:
    """After the JSON-catalog rewrite, no Kroger product should have a
    placeholder name like 'Pet item 0042' or 'Bakery item 0010'."""
    with _conn() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM tenant_products "
            "WHERE merchant_id='KRG' AND (name LIKE '%item%' OR name LIKE '%Item%')"
        ).fetchone()[0]
    assert n == 0, f"{n} Kroger products still have placeholder names containing 'item'"


def test_planted_anomalies_present() -> None:
    """All three planted anomalies should be detectable in the regenerated DB.
    Lookups are by name / query, not by hard-coded SKU or store ID, so the
    test stays valid as catalog details shift."""
    with _conn() as conn:
        # (a) Avocado price spike: max unit_price >> 4x min for that SKU's lines.
        sku_row = conn.execute(
            "SELECT sku FROM tenant_products "
            "WHERE merchant_id='KRG' AND name='Avocados (4-pack)'"
        ).fetchone()
        assert sku_row is not None, "Avocados (4-pack) SKU not found in tenant_products"
        avocado_sku = sku_row[0]
        prices = conn.execute(
            "SELECT MAX(unit_price), MIN(unit_price) "
            "FROM tenant_transaction_items WHERE sku = ?",
            (avocado_sku,),
        ).fetchone()
        assert prices[1] > 0, "Avocado SKU has no transactions"
        ratio = prices[0] / prices[1]
        assert ratio > 4, (
            f"Anomaly 1 missing: avocado max/min unit_price ratio = {ratio:.2f}, "
            f"expected > 4 (5x spike day)"
        )

        # (b) Largest 7-day-over-7-day decline: query for the worst-performing
        # Kroger store, no hard-coded ID. Window: last 7 days vs prior 7 days
        # of the data window (which ends 2026-05-05).
        end = P.END_DATE
        last7_start = (end - timedelta(days=6)).isoformat()
        prior7_start = (end - timedelta(days=13)).isoformat()
        worst = conn.execute(
            f"""
            WITH last7 AS (
                SELECT store_id, COUNT(*) AS n FROM tenant_transactions
                WHERE merchant_id='KRG' AND txn_ts >= '{last7_start}'
                GROUP BY store_id
            ),
            prior7 AS (
                SELECT store_id, COUNT(*) AS n FROM tenant_transactions
                WHERE merchant_id='KRG'
                  AND txn_ts >= '{prior7_start}' AND txn_ts < '{last7_start}'
                GROUP BY store_id
            )
            SELECT s.store_id,
                   COALESCE(p.n, 0) AS prior_n,
                   COALESCE(l.n, 0) AS last_n,
                   CAST(COALESCE(l.n, 0) AS REAL) / NULLIF(COALESCE(p.n, 0), 0) AS ratio
            FROM tenant_stores s
            LEFT JOIN last7  l USING(store_id)
            LEFT JOIN prior7 p USING(store_id)
            WHERE s.merchant_id='KRG' AND COALESCE(p.n, 0) > 50
            ORDER BY ratio ASC
            LIMIT 1
            """
        ).fetchone()
        assert worst is not None, "No Kroger store has enough prior-week traffic to compare"
        assert worst[3] is not None and worst[3] <= 0.75, (
            f"Anomaly 2 missing: worst Kroger store ({worst[0]}) had ratio "
            f"{worst[3]:.2f} (last={worst[2]}, prior={worst[1]}); expected <= 0.75"
        )

        # (c) Baby cohort surge: customers buying BABY items in last 21 days
        # who never bought BABY before that.
        cutoff = (end - timedelta(days=20)).isoformat()
        new_buyers = conn.execute(
            f"""
            SELECT COUNT(DISTINCT t.customer_id)
            FROM tenant_transactions t
            JOIN tenant_transaction_items i ON i.txn_id = t.txn_id
            JOIN tenant_products p ON p.sku = i.sku
            WHERE t.merchant_id='KRG'
              AND p.category='BABY'
              AND t.txn_ts >= '{cutoff}'
              AND t.customer_id NOT IN (
                  SELECT DISTINCT t2.customer_id
                  FROM tenant_transactions t2
                  JOIN tenant_transaction_items i2 ON i2.txn_id = t2.txn_id
                  JOIN tenant_products p2 ON p2.sku = i2.sku
                  WHERE t2.merchant_id='KRG'
                    AND p2.category='BABY'
                    AND t2.txn_ts < '{cutoff}'
              )
            """
        ).fetchone()[0]
        assert new_buyers >= 30, (
            f"Anomaly 3 missing: only {new_buyers} new baby buyers in last 21 days; "
            f"expected >= 30"
        )


def test_kroger_category_distribution_realistic() -> None:
    """Top 4 categories at Kroger by 90-day revenue should come from the
    'staples' set (MEAT, PANTRY, PRODUCE, DAIRY, BEVERAGES, FROZEN). The
    test fails if PET, BABY, or PERSONAL appears in the top 4 — those would
    indicate uniform-by-SKU sampling has been re-introduced."""
    allowed = {"MEAT", "PANTRY", "PRODUCE", "DAIRY", "BEVERAGES", "FROZEN"}
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT p.category
            FROM tenant_transaction_items i
            JOIN tenant_products p ON p.sku = i.sku
            JOIN tenant_transactions t ON t.txn_id = i.txn_id
            WHERE t.merchant_id='KRG'
            GROUP BY p.category
            ORDER BY SUM(i.line_total) DESC
            LIMIT 4
            """
        ).fetchall()
    top4 = {r[0] for r in rows}
    leaks = top4 - allowed
    assert not leaks, f"Top 4 Kroger categories include {leaks}, expected subset of {allowed}"
