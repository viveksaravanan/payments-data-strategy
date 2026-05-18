"""DB tests.

Validates schema applies cleanly, indexes/FKs are present, and tenant
tables are seeded with row counts that match their source CSVs. The
lake is virtual in v2.5 (computed at query time from tenant tables —
see ``tests/test_lake_views.py``); this file no longer asserts on
physical lake_* tables.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "payments.db"
SCHEMA_PATH = ROOT / "src" / "db" / "schema.sql"
RAW = ROOT / "data" / "raw"


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
        "tenant_promotions",
        "tenant_transactions", "tenant_transaction_items",
    }
    assert expected_tables == tables, (
        f"unexpected schema tables: missing={expected_tables - tables} "
        f"extra={tables - expected_tables}"
    )
    c.close()


def test_lake_materialized_tables_present(conn: sqlite3.Connection) -> None:
    """Phase 1.5 (V3_AUDIT.md Decision §1.1) inverts the v2.5 "lake is
    virtual" invariant — the lake is now materialized at seed time as
    per-viewer physical tables. This test pins the post-Phase-1.5
    shape:

      * exactly the 10 expected `lake_*_<viewer>` tables exist
        (lake_transactions_<M> + lake_stores_<M> for the 5 viewers in
        the panel),
      * each is non-empty (catches a broken materialization that
        creates the table but writes no rows),
      * nothing else matching `lake_%` slipped in — a future viewer
        added without updating this test, or a stray v2-era physical
        lake table, would fail here.
    """
    viewers = ("KRG", "ACM", "WDX", "TBL", "TJX")
    expected = {
        f"lake_{kind}_{m}"
        for m in viewers
        for kind in ("transactions", "stores")
    }
    rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name LIKE 'lake_%'"
    ).fetchall()
    names = {r[0] for r in rows}
    assert names == expected, (
        f"unexpected lake_* table shape. "
        f"extra: {sorted(names - expected)}, "
        f"missing: {sorted(expected - names)}"
    )
    for name in sorted(expected):
        count = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        assert count > 0, f"{name} materialized empty — broken seed"


def test_indexes_present(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'ix_%'"
    ).fetchall()
    names = {r[0] for r in rows}
    expected = {
        "ix_t_txn_customer", "ix_t_txn_merchant", "ix_t_txn_store",
        "ix_t_txn_ts", "ix_t_items_sku", "ix_t_items_txn",
    }
    assert expected.issubset(names)


def test_foreign_keys_declared(conn: sqlite3.Connection) -> None:
    # Each child table should have an FK row for its parent.
    fk_targets = {
        "tenant_stores":             "merchants",
        "tenant_products":           "merchants",
        "tenant_transactions":       "tenant_customers",
        "tenant_transaction_items":  "tenant_transactions",
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
    ("tenant_customers",          RAW / "customers.csv"),
    ("tenant_stores",             RAW / "stores.csv"),
    ("tenant_products",           RAW / "products.csv"),
    ("tenant_promotions",         RAW / "promotions.csv"),
    ("tenant_transactions",       RAW / "transactions.csv"),
    ("tenant_transaction_items",  RAW / "transaction_items.csv"),
])
def test_table_row_counts_match_csvs(
    conn: sqlite3.Connection, table: str, csv: Path
) -> None:
    n_csv = _csv_row_count(csv)
    n_db = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    assert n_db == n_csv, f"{table}: db={n_db} csv={n_csv}"


def test_no_pii_or_v2_demographics_in_db(conn: sqlite3.Connection) -> None:
    """Belt-and-suspenders: no PII or deferred-demographic columns
    anywhere in the schema."""
    bad = {
        "customer_name", "customer_email", "customer_pan",
        "age_band", "income_band", "is_lapser", "is_organic",
    }
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )]
    for t in tables:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info('{t}')")}
        assert not (cols & bad), f"forbidden column found in {t}: {cols & bad}"
