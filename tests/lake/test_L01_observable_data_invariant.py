"""L1 — Observable-data invariant test (Wave 2 §1 / D23.1).

Three mechanisms, each independent:

(a) The accessor refuses forbidden tables (``customers``, ``zones``)
    and forbidden columns within allowed tables (notably
    ``stores.zone_id``, the §1-violation-in-disguise case).
(b) The accessor permits legitimate reads and projects the column
    set correctly.
(c) A static AST scan of every .py file under ``src/lake/`` asserts no
    direct ``pd.read_parquet`` / ``pq.read_table`` call bypasses the
    accessor. Only ``observable_guard.py`` itself is allowed to call
    the underlying readers.
"""
from __future__ import annotations

import ast
from pathlib import Path

import duckdb
import pytest

from src.lake.observable_guard import (
    ALLOWED_COLUMNS,
    ALLOWED_TABLES,
    FORBIDDEN_TABLES,
    ForbiddenColumnError,
    load_table,
    read_parquet_relation,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
LAKE_DIR = REPO_ROOT / "src" / "lake"


# ----- (a) Accessor rejects forbidden tables ------------------------------

def test_customers_table_forbidden() -> None:
    with pytest.raises(ForbiddenColumnError, match="customers"):
        load_table("customers")


def test_zones_table_forbidden() -> None:
    with pytest.raises(ForbiddenColumnError, match="zones"):
        load_table("zones")


def test_unknown_table_forbidden() -> None:
    with pytest.raises(ForbiddenColumnError, match="not in the allowed source set"):
        load_table("not_a_real_table")


# ----- (a) Accessor rejects forbidden columns within allowed tables -------

def test_stores_zone_id_forbidden() -> None:
    """The §1-violation-in-disguise case: stores.zone_id is the planted
    label. Reading it and relabeling 'derived' is exactly the failure
    mode the plan explicitly guards against."""
    with pytest.raises(ForbiddenColumnError, match="zone_id"):
        load_table("stores", columns=["store_id", "zone_id"])


def test_forbidden_column_error_lists_allowed_set() -> None:
    """Error message must surface the allowed set so the caller can
    self-correct, not just say 'forbidden'."""
    with pytest.raises(ForbiddenColumnError) as exc_info:
        load_table("stores", columns=["zone_id"])
    msg = str(exc_info.value)
    # Names the offending column + names a few legitimate alternatives.
    assert "zone_id" in msg
    assert "latitude" in msg
    assert "longitude" in msg


# ----- (b) Allowed reads succeed --------------------------------------------

def test_stores_lat_long_allowed() -> None:
    """The legitimate coordinate-based path Stage 3 zone derivation
    will use."""
    df = load_table("stores", columns=["store_id", "latitude", "longitude"])
    assert set(df.columns) == {"store_id", "latitude", "longitude"}
    assert len(df) == 29  # Wave 1 footprint per D5/D13.2 + AskUserQuestion resolution


def test_allowed_full_table_load_returns_all_allowed_columns() -> None:
    """No-columns load returns exactly the whitelist for that table."""
    df = load_table("transactions")
    assert set(df.columns) == ALLOWED_COLUMNS["transactions"]


def test_subset_of_allowed_columns_works() -> None:
    df = load_table("transaction_items", columns=["txn_id", "sku", "qty"])
    assert set(df.columns) == {"txn_id", "sku", "qty"}


def test_promotions_load_works() -> None:
    df = load_table("promotions")
    assert len(df) > 0
    assert "promo_id" in df.columns


def test_products_load_works() -> None:
    df = load_table("products")
    assert "canonical_id" in df.columns
    assert "private_label" in df.columns


# ----- (c) DuckDB relation reader applies the same whitelist --------------

def test_relation_reader_rejects_forbidden_table() -> None:
    with pytest.raises(ForbiddenColumnError):
        read_parquet_relation("customers")


def test_relation_reader_rejects_forbidden_column() -> None:
    with pytest.raises(ForbiddenColumnError, match="zone_id"):
        read_parquet_relation("stores", columns=["zone_id"])


def test_relation_reader_returns_duckdb_relation() -> None:
    """Relation, not DataFrame — columnar aggregation downstream
    (Wave 1 Stage 2 storage convention)."""
    rel = read_parquet_relation("stores", columns=["store_id", "latitude"])
    assert isinstance(rel, duckdb.DuckDBPyRelation)
    n = rel.aggregate("count(*) AS n").fetchone()[0]
    assert n == 29


# ----- (d) Static scan: no direct Parquet read bypasses the accessor -----

class _ParquetCallFinder(ast.NodeVisitor):
    """Detect direct ``read_parquet`` / ``read_table`` calls (would
    bypass the accessor's column whitelist)."""

    def __init__(self) -> None:
        self.findings: list[tuple[int, str]] = []

    def visit_Call(self, node: ast.Call) -> None:
        attr_name: str | None = None
        if isinstance(node.func, ast.Attribute):
            attr_name = node.func.attr
        elif isinstance(node.func, ast.Name):
            attr_name = node.func.id
        if attr_name in {"read_parquet", "read_table"}:
            self.findings.append((node.lineno, attr_name))
        self.generic_visit(node)


# Files explicitly allowed to call the underlying Parquet readers.
_ACCESSOR_FILES: set[str] = {"observable_guard.py"}

# Wave 2 lake files are scanned. Wave 1 legacy v2.5 SQLite files
# (views.py, peer_mapping.py) are retired in Stage 7 — exclude them
# from the scan so they don't false-trigger. They use sqlite3, not
# Parquet, so they wouldn't trigger anyway; explicit allow-list keeps
# the test contract clear.
_LEGACY_V25_FILES: set[str] = {"views.py", "peer_mapping.py"}


def test_no_direct_parquet_read_in_lake_source() -> None:
    """Static scan of src/lake/*.py. Any file other than the accessor
    must NOT directly call ``pd.read_parquet`` / ``pq.read_table`` —
    that would bypass the column whitelist and the §1 invariant."""
    bypasses: list[str] = []
    for path in sorted(LAKE_DIR.glob("*.py")):
        if path.name in _ACCESSOR_FILES or path.name in _LEGACY_V25_FILES:
            continue
        src = path.read_text()
        try:
            tree = ast.parse(src)
        except SyntaxError as e:
            pytest.fail(f"{path.relative_to(REPO_ROOT)} failed to parse: {e}")
        finder = _ParquetCallFinder()
        finder.visit(tree)
        for lineno, attr in finder.findings:
            bypasses.append(
                f"{path.relative_to(REPO_ROOT)}:{lineno} calls {attr}() "
                f"directly — must route through observable_guard"
            )
    assert not bypasses, (
        "Direct Parquet reads in src/lake/ bypass observable_guard:\n  "
        + "\n  ".join(bypasses)
    )
