"""Phase 3 tests: tax math, transaction rollups, promo coverage."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.generate.tax import TAX_RATE_BY_CATEGORY

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "payments.db"

# Per-merchant promo-count targets per Layer 3f (±10% band per the
# Phase 3 reconciliation criteria).
PROMO_COUNT_TARGETS = {
    "KRG": 25,
    "ACM": 20,
    "WDX": 18,
    "TBL": 6,
    "TJX": 4,
}

TAX_EXEMPT_GROCERY = {"DAIRY", "BAKERY", "PRODUCE", "MEAT", "FROZEN", "PANTRY"}


@pytest.fixture(scope="module")
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    yield c
    c.close()


# ---------------------------------------------------------------------------
# tenant_promotions volumes per merchant (±10%).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("merchant,target", list(PROMO_COUNT_TARGETS.items()))
def test_promo_count_per_merchant(
    conn: sqlite3.Connection, merchant: str, target: int
) -> None:
    n = conn.execute(
        "SELECT COUNT(DISTINCT promo_id) FROM tenant_promotions WHERE merchant_id = ?",
        (merchant,),
    ).fetchone()[0]
    lo = int(target * 0.9)
    hi = int(target * 1.1) + 1
    assert lo <= n <= hi, (
        f"{merchant} has {n} distinct promos, target {target} ±10% "
        f"(allowed {lo}..{hi})"
    )


def test_total_promos_in_panel(conn: sqlite3.Connection) -> None:
    """Total ~73 across all merchants (Layer 3f)."""
    n = conn.execute("SELECT COUNT(DISTINCT promo_id) FROM tenant_promotions").fetchone()[0]
    expected = sum(PROMO_COUNT_TARGETS.values())  # 73
    lo = int(expected * 0.9)
    hi = int(expected * 1.1) + 1
    assert lo <= n <= hi, f"panel total promos = {n}, target {expected} ±10%"


# ---------------------------------------------------------------------------
# Promo coverage: every non-zero discount has a covering promo.
# ---------------------------------------------------------------------------

def test_no_orphan_discounts(conn: sqlite3.Connection) -> None:
    n_orphan = conn.execute(
        "SELECT COUNT(*) FROM tenant_transaction_items "
        "WHERE discount > 0 AND promo_id IS NULL"
    ).fetchone()[0]
    assert n_orphan == 0, f"{n_orphan} discounted lines have NULL promo_id"


def test_promo_id_resolves_to_promotions_row(conn: sqlite3.Connection) -> None:
    """Every non-NULL promo_id on items must exist in tenant_promotions."""
    n_dangling = conn.execute(
        """
        SELECT COUNT(*) FROM tenant_transaction_items i
        WHERE i.promo_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM tenant_promotions p
              WHERE p.promo_id = i.promo_id AND p.sku = i.sku
          )
        """
    ).fetchone()[0]
    assert n_dangling == 0, (
        f"{n_dangling} item rows have promo_id values with no matching "
        f"(promo_id, sku) row in tenant_promotions"
    )


def test_discount_within_promo_window(conn: sqlite3.Connection) -> None:
    """For every line carrying a promo_id, the txn timestamp must fall
    inside the promo's [start_date, end_date]."""
    n_off_window = conn.execute(
        """
        SELECT COUNT(*) FROM tenant_transaction_items i
        JOIN tenant_transactions t ON t.txn_id = i.txn_id
        JOIN tenant_promotions p
          ON p.promo_id = i.promo_id AND p.sku = i.sku
        WHERE i.promo_id IS NOT NULL
          AND DATE(t.txn_ts) NOT BETWEEN p.start_date AND p.end_date
        """
    ).fetchone()[0]
    assert n_off_window == 0, (
        f"{n_off_window} items reference a promo whose window doesn't "
        f"include the txn date"
    )


# ---------------------------------------------------------------------------
# Tax math.
# ---------------------------------------------------------------------------

def test_tax_exempt_grocery_categories_sum_to_zero(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        f"""
        SELECT p.category, ROUND(SUM(i.tax), 2)
        FROM tenant_transaction_items i
        JOIN tenant_products p ON p.sku = i.sku
        WHERE p.category IN ({",".join("?" * len(TAX_EXEMPT_GROCERY))})
        GROUP BY p.category
        """,
        tuple(TAX_EXEMPT_GROCERY),
    ).fetchall()
    for category, tax_sum in rows:
        assert tax_sum == 0, (
            f"Category {category} expected to be tax-exempt but sums to ${tax_sum}"
        )


def test_per_line_tax_matches_rate(conn: sqlite3.Connection) -> None:
    """`tax == round(line_total × tax_rate(category), 2)` per row.
    Sample 5,000 rows for speed; deterministic via ORDER BY."""
    rows = conn.execute(
        """
        SELECT i.txn_id, i.line_id, i.line_total, i.tax, p.category
        FROM tenant_transaction_items i
        JOIN tenant_products p ON p.sku = i.sku
        ORDER BY i.txn_id, i.line_id
        LIMIT 5000
        """
    ).fetchall()
    mismatches = []
    for txn_id, line_id, line_total, tax, category in rows:
        expected = round(line_total * TAX_RATE_BY_CATEGORY[category], 2)
        if abs(tax - expected) > 0.01:
            mismatches.append((txn_id, line_id, category, line_total, tax, expected))
    assert not mismatches, (
        f"{len(mismatches)} of 5000 sampled rows have wrong tax. "
        f"First: {mismatches[0]}"
    )


def test_non_zero_tax_categories(conn: sqlite3.Connection) -> None:
    """Non-grocery categories should have positive total tax."""
    rows = conn.execute(
        """
        SELECT p.category, ROUND(SUM(i.tax), 2)
        FROM tenant_transaction_items i
        JOIN tenant_products p ON p.sku = i.sku
        WHERE p.category IN ('BEVERAGES','SNACKS','HOUSEHOLD','PERSONAL','BABY','PET')
        GROUP BY p.category
        """
    ).fetchall()
    for category, tax_sum in rows:
        assert tax_sum > 0, f"Category {category} has zero total tax"


# ---------------------------------------------------------------------------
# Transaction rollup math.
# ---------------------------------------------------------------------------

def test_subtotal_equals_sum_of_line_totals(conn: sqlite3.Connection) -> None:
    n_bad = conn.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT t.txn_id,
                   ROUND(t.subtotal, 2) AS reported,
                   ROUND(SUM(i.line_total), 2) AS computed
            FROM tenant_transactions t
            JOIN tenant_transaction_items i ON i.txn_id = t.txn_id
            GROUP BY t.txn_id
            HAVING ABS(reported - computed) > 0.01
        )
        """
    ).fetchone()[0]
    assert n_bad == 0, f"{n_bad} transactions have subtotal != sum(line_total)"


def test_tax_total_equals_sum_of_line_taxes(conn: sqlite3.Connection) -> None:
    n_bad = conn.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT t.txn_id,
                   ROUND(t.tax_total, 2) AS reported,
                   ROUND(SUM(i.tax), 2) AS computed
            FROM tenant_transactions t
            JOIN tenant_transaction_items i ON i.txn_id = t.txn_id
            GROUP BY t.txn_id
            HAVING ABS(reported - computed) > 0.01
        )
        """
    ).fetchone()[0]
    assert n_bad == 0, f"{n_bad} transactions have tax_total != sum(tax)"


def test_txn_total_equals_subtotal_plus_tax_total(conn: sqlite3.Connection) -> None:
    n_bad = conn.execute(
        """
        SELECT COUNT(*) FROM tenant_transactions
        WHERE ROUND(subtotal + tax_total, 2) != ROUND(txn_total, 2)
        """
    ).fetchone()[0]
    assert n_bad == 0, f"{n_bad} transactions have txn_total != subtotal + tax_total"


# ---------------------------------------------------------------------------
# New per-transaction fields.
# ---------------------------------------------------------------------------

def test_terminal_id_format(conn: sqlite3.Connection) -> None:
    """terminal_id must be `<store_id>-T<NN>`."""
    n_bad = conn.execute(
        "SELECT COUNT(*) FROM tenant_transactions "
        "WHERE terminal_id NOT LIKE store_id || '-T__'"
    ).fetchone()[0]
    assert n_bad == 0, f"{n_bad} transactions have malformed terminal_id"


def test_connectivity_distribution_in_band(conn: sqlite3.Connection) -> None:
    """65% wifi / 25% cellular_4g / 8% cellular_5g / 2% ethernet, ±2pp."""
    rows = conn.execute(
        """
        SELECT connectivity_type, COUNT(*) * 1.0 / (
            SELECT COUNT(*) FROM tenant_transactions
        ) AS share
        FROM tenant_transactions
        GROUP BY connectivity_type
        """
    ).fetchall()
    target = {"wifi": 0.65, "cellular_4g": 0.25, "cellular_5g": 0.08, "ethernet": 0.02}
    actual = {row[0]: row[1] for row in rows}
    assert set(actual) == set(target), f"unexpected connectivity values: {actual}"
    for key, exp in target.items():
        assert abs(actual[key] - exp) < 0.02, (
            f"connectivity {key}: got {actual[key]:.3f}, expected {exp:.3f} ±0.02"
        )
