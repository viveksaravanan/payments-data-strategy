"""Agent tool-layer tests.

  * SQL guard rejects writes / multi-statement.
  * `query_tenant` rejects queries lacking the merchant_id predicate.
  * `query_lake` rejects tenant_* refs, rejects legacy v2 lake table
    names not in the v2.5 model, requires a reference to
    `lake_transactions` or `lake_stores`, and CTE-wraps the agent's
    SQL so the viewing merchant's data is excluded.

The legacy `MerchantAdvisor` class was archived in Phase 1.5
(Decision §1.6 in V3_AUDIT.md); its tests now live alongside the
archived class at
`docs/archive/legacy_agent/test_legacy_advisor.py.archived`.
"""
from __future__ import annotations

import pytest

from src.agents import tools as T


# ---------------------------------------------------------------------------
# SQL guard
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sql", [
    "DROP TABLE tenant_customers",
    "INSERT INTO tenant_customers VALUES ('x','12345','filler','loyalist','KRG',NULL,'credit',0,'2020-01-01')",
    "UPDATE tenant_customers SET behavioral_segment = 'stocker'",
    "DELETE FROM tenant_customers",
    "ATTACH DATABASE 'evil.db' AS e",
    "ALTER TABLE tenant_customers ADD COLUMN ssn TEXT",
    "CREATE TABLE foo (x INT)",
    "PRAGMA writable_schema = 1",
    "SELECT 1; DROP TABLE tenant_customers",  # multi-statement
    "",
    "   ",
    "EXPLAIN SELECT 1",  # not SELECT or WITH
])
def test_sql_guard_rejects_writes_and_multistatement(sql: str) -> None:
    assert T.is_safe_select(sql) is False
    with pytest.raises(ValueError):
        # The SELECT-only guard runs before viewing-merchant resolution,
        # so an unsafe query is rejected regardless of merchant context.
        T.query_lake(sql, viewing_merchant_id="KRG")


@pytest.mark.parametrize("sql", [
    "SELECT 1",
    "SELECT * FROM merchants LIMIT 5",
    "WITH a AS (SELECT 1 AS n) SELECT n FROM a",
    "  select  *  from  merchants  ",
    "SELECT * FROM merchants;",  # trailing semicolon ok
])
def test_sql_guard_accepts_safe_select(sql: str) -> None:
    assert T.is_safe_select(sql) is True


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------

def test_tenant_isolation_rejects_query_without_merchant_predicate() -> None:
    with pytest.raises(ValueError) as excinfo:
        T.query_tenant("SELECT * FROM tenant_transactions LIMIT 5",
                       current_merchant="KRG")
    assert "merchant_id" in str(excinfo.value)


def test_tenant_isolation_rejects_predicate_for_wrong_merchant() -> None:
    # Acting as KRG but query filters TBL — runner only accepts the runner's merchant_id.
    with pytest.raises(ValueError):
        T.query_tenant(
            "SELECT * FROM tenant_transactions WHERE merchant_id = 'TBL' LIMIT 5",
            current_merchant="KRG",
        )


def test_tenant_isolation_accepts_filtered_query() -> None:
    result = T.query_tenant(
        "SELECT txn_id FROM tenant_transactions WHERE merchant_id = 'KRG' LIMIT 3",
        current_merchant="KRG",
    )
    assert result["row_count"] == 3
    assert result["columns"] == ["txn_id"]


def test_tenant_isolation_accepts_double_quoted_predicate() -> None:
    sql = 'SELECT txn_id FROM tenant_transactions WHERE merchant_id = "KRG" LIMIT 1'
    assert T.has_merchant_predicate(sql, "KRG") is True


# Phase 1.5 (Decision §1.5): the CTE wrapper shadows tenant_* table
# names with per-viewer views (tenant_view_<M>_<table>). The two tests
# below verify that a SQL string which *satisfies the regex predicate
# check* but tries to broaden the result set via OR-clauses still
# returns only the viewing merchant's rows — the view is the real
# isolation boundary.

def test_tenant_isolation_blocks_or_1_eq_1_bypass() -> None:
    """`WHERE merchant_id = 'KRG' OR 1=1` satisfies the regex check but
    disables logical isolation. The per-viewer view blocks it."""
    result = T.query_tenant(
        "SELECT DISTINCT merchant_id FROM tenant_transactions "
        "WHERE merchant_id = 'KRG' OR 1=1",
        current_merchant="KRG",
    )
    seen = {row[0] for row in result["rows"]}
    assert seen == {"KRG"}, f"expected only KRG rows; got {seen}"


def test_tenant_isolation_blocks_or_other_merchant_bypass() -> None:
    """`WHERE merchant_id = 'KRG' OR merchant_id = 'ACM'` asks for
    both grocers. The per-viewer view limits the agent to KRG rows."""
    result = T.query_tenant(
        "SELECT DISTINCT merchant_id FROM tenant_transactions "
        "WHERE merchant_id = 'KRG' OR merchant_id = 'ACM'",
        current_merchant="KRG",
    )
    seen = {row[0] for row in result["rows"]}
    assert seen == {"KRG"}, f"expected only KRG rows; got {seen}"


def test_tenant_isolation_rejects_unknown_viewer() -> None:
    """`current_merchant` outside the panel raises before the DB opens."""
    with pytest.raises(ValueError):
        T.query_tenant(
            "SELECT 1 FROM tenant_transactions WHERE merchant_id = 'NOPE'",
            current_merchant="NOPE",
        )


# ---------------------------------------------------------------------------
# query_lake — Phase 5b v2.5 path (with viewing_merchant_id).
# ---------------------------------------------------------------------------

def test_query_lake_v2_5_requires_lake_view_reference() -> None:
    """A SELECT that references neither lake_transactions nor lake_stores is rejected."""
    with pytest.raises(ValueError) as excinfo:
        T.query_lake("SELECT 1", viewing_merchant_id="KRG")
    assert "lake_transactions" in str(excinfo.value)


@pytest.mark.parametrize("forbidden", [
    "SELECT * FROM lake_customers LIMIT 1",
    "SELECT * FROM lake_transaction_items LIMIT 1",
    "SELECT a.lake_txn_id FROM lake_transactions a JOIN lake_customers c USING(customer_id)",
])
def test_query_lake_v2_5_rejects_physical_lake_tables(forbidden: str) -> None:
    """References to v2 lake tables that aren't part of the v2.5
    virtual model are rejected before the DB is opened."""
    with pytest.raises(ValueError) as excinfo:
        T.query_lake(forbidden, viewing_merchant_id="KRG")
    assert "v2.5" in str(excinfo.value).lower() or "lake_transactions" in str(excinfo.value).lower()


@pytest.mark.parametrize("with_tenant_ref", [
    "SELECT t.txn_id FROM lake_transactions l, tenant_transactions t LIMIT 1",
    "SELECT * FROM tenant_products LIMIT 1",
])
def test_query_lake_v2_5_rejects_tenant_table_references(with_tenant_ref: str) -> None:
    """Lake queries cannot read tenant_* tables — those go through query_tenant."""
    with pytest.raises(ValueError) as excinfo:
        T.query_lake(with_tenant_ref, viewing_merchant_id="KRG")
    assert "tenant_" in str(excinfo.value)


def test_query_lake_v2_5_excludes_viewing_merchant() -> None:
    """A simple aggregate over the lake should report only peer rows;
    the viewing merchant's own data is excluded by the CTE wrap."""
    result = T.query_lake(
        "SELECT peer_id, COUNT(*) AS n FROM lake_transactions "
        "GROUP BY peer_id ORDER BY peer_id LIMIT 10",
        viewing_merchant_id="KRG",
    )
    peers = {row[0] for row in result["rows"]}
    assert peers == {"peer_a", "peer_b", "peer_c", "peer_d"}, (
        f"expected 4 peer labels exactly; got {peers}"
    )


def test_query_lake_v2_5_dairy_pricing_returns_peer_rows() -> None:
    """Peer DAIRY pricing query returns rows tagged with peer_a..peer_d
    (no underlying merchant_id leaks into output)."""
    result = T.query_lake(
        "SELECT peer_id, peer_segment, ROUND(AVG(unit_price), 2) AS avg_price "
        "FROM lake_transactions WHERE category = 'DAIRY' "
        "GROUP BY peer_id, peer_segment ORDER BY peer_id LIMIT 10",
        viewing_merchant_id="KRG",
    )
    assert result["columns"] == ["peer_id", "peer_segment", "avg_price"]
    peers = {row[0] for row in result["rows"]}
    # Only grocery peers carry DAIRY (peer_a, peer_b for KRG-viewer).
    assert peers <= {"peer_a", "peer_b", "peer_c", "peer_d"}
    assert "peer_a" in peers and "peer_b" in peers


def test_query_lake_v2_5_rejects_unknown_viewing_merchant() -> None:
    with pytest.raises((ValueError, KeyError)):
        T.query_lake(
            "SELECT 1 FROM lake_transactions LIMIT 1",
            viewing_merchant_id="NOPE",
        )


# Phase 1.5 (Decision §1.2): k=5 suppression is applied to lake results
# that include a count-like column. Tight cells (peer × hour-bucket ×
# card-network on a single day) produce sub-k rows reliably.

def test_query_lake_applies_k5_suppression_when_count_column_present() -> None:
    """A finely-grouped lake query produces sub-k cells; the runner
    detects the count column, drops them, and surfaces a suppression
    note."""
    result = T.query_lake(
        "SELECT peer_id, txn_hour_bucket, card_network, COUNT(*) AS n "
        "FROM lake_transactions WHERE txn_date = '2026-04-22' "
        "GROUP BY peer_id, txn_hour_bucket, card_network "
        "ORDER BY peer_id, txn_hour_bucket, card_network LIMIT 200",
        viewing_merchant_id="KRG",
    )
    # Suppression note is present (some cells were below k=5).
    assert "suppression" in result, (
        f"expected suppression note in result; got keys {list(result)}"
    )
    assert "k=5" in result["suppression"]
    # Every surviving row has n >= 5.
    n_idx = result["columns"].index("n")
    assert all(row[n_idx] >= 5 for row in result["rows"]), (
        "all surviving rows must satisfy n >= k=5"
    )


def test_query_lake_no_suppression_when_no_count_column() -> None:
    """A SELECT without a count column should NOT have a suppression
    key — the runner has nothing to filter on."""
    result = T.query_lake(
        "SELECT peer_id, category, ROUND(AVG(unit_price), 2) AS avg_price "
        "FROM lake_transactions WHERE category = 'DAIRY' "
        "GROUP BY peer_id, category ORDER BY peer_id LIMIT 20",
        viewing_merchant_id="KRG",
    )
    assert "suppression" not in result
