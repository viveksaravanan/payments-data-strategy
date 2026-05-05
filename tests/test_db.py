"""DB tests. See PLAN.md §12.

Validates schema applies cleanly, indexes/FKs are present, both layers seeded
with matching row counts, and the cross-merchant lake query (count of
customers active at >=2 merchants) returns a non-zero result.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "payments.db"
SCHEMA_PATH = ROOT / "src" / "db" / "schema.sql"
TENANT = ROOT / "data" / "anon" / "tenant"
LAKE = ROOT / "data" / "anon" / "lake"


@pytest.fixture(scope="module")
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    c.execute("PRAGMA foreign_keys = ON")
    yield c
    c.close()


def _csv_row_count(path: Path) -> int:
    with path.open() as f:
        return sum(1 for _ in f) - 1  # minus header


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def test_schema_applies_cleanly(tmp_path: Path) -> None:
    db = tmp_path / "smoke.db"
    c = sqlite3.connect(db)
    c.executescript(SCHEMA_PATH.read_text())
    tables = {r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    expected_tables = {
        "merchants",
        "tenant_customers", "tenant_stores", "tenant_products",
        "tenant_transactions", "tenant_transaction_items",
        "lake_customers", "lake_transactions", "lake_transaction_items",
    }
    assert expected_tables.issubset(tables)
    c.close()


def test_indexes_present(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'ix_%'"
    ).fetchall()
    names = {r[0] for r in rows}
    expected = {
        "ix_t_txn_customer", "ix_t_txn_merchant", "ix_t_txn_store",
        "ix_t_txn_ts", "ix_t_items_sku", "ix_t_items_txn",
        "ix_l_txn_customer", "ix_l_txn_merchant", "ix_l_txn_ts",
        "ix_l_items_txn",
    }
    assert expected.issubset(names)


def test_foreign_keys_declared(conn: sqlite3.Connection) -> None:
    # Each child table should have an FK row for its parent.
    fk_targets = {
        "tenant_stores":             "merchants",
        "tenant_products":           "merchants",
        "tenant_transactions":       "tenant_customers",
        "tenant_transaction_items":  "tenant_transactions",
        "lake_transactions":         "lake_customers",
        "lake_transaction_items":    "lake_transactions",
    }
    for child, parent in fk_targets.items():
        fks = conn.execute(f"PRAGMA foreign_key_list('{child}')").fetchall()
        assert any(fk[2] == parent for fk in fks), (
            f"{child} missing FK -> {parent} (got {fks})"
        )


def test_foreign_key_enforcement_active(conn: sqlite3.Connection) -> None:
    on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert on == 1


# ---------------------------------------------------------------------------
# Row counts
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("table,csv", [
    ("tenant_customers",          TENANT / "customers.csv"),
    ("tenant_stores",             TENANT / "stores.csv"),
    ("tenant_products",           TENANT / "products.csv"),
    ("tenant_transactions",       TENANT / "transactions.csv"),
    ("tenant_transaction_items",  TENANT / "transaction_items.csv"),
    ("lake_customers",            LAKE / "customers.csv"),
    ("lake_transactions",         LAKE / "transactions.csv"),
    ("lake_transaction_items",    LAKE / "transaction_items.csv"),
])
def test_table_row_counts_match_csvs(
    conn: sqlite3.Connection, table: str, csv: Path
) -> None:
    n_csv = _csv_row_count(csv)
    n_db = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    assert n_db == n_csv, f"{table}: db={n_db} csv={n_csv}"


# ---------------------------------------------------------------------------
# Cross-merchant lake query — the demo's punchline
# ---------------------------------------------------------------------------

def test_cross_merchant_lake_query_returns_nonzero(conn: sqlite3.Connection) -> None:
    n = conn.execute("""
        SELECT COUNT(*) FROM (
            SELECT customer_id
            FROM lake_transactions
            GROUP BY customer_id
            HAVING COUNT(DISTINCT merchant_id) >= 2
        )
    """).fetchone()[0]
    assert n > 0


def test_no_pii_in_db(conn: sqlite3.Connection) -> None:
    """Belt-and-suspenders: no `customer_name`/`customer_email`/`customer_pan`
    column anywhere in the schema."""
    bad = {"customer_name", "customer_email", "customer_pan"}
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )]
    for t in tables:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info('{t}')")}
        assert not (cols & bad), f"PII column found in {t}: {cols & bad}"
