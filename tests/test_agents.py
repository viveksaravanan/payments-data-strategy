"""Agent tests.

  * SQL guard rejects writes / multi-statement.
  * `query_tenant` rejects queries lacking the merchant_id predicate.
  * `query_lake` rejects tenant_* refs, rejects legacy v2 lake table
    names not in the v2.5 model, requires a reference to
    `lake_transactions` or `lake_stores`, and CTE-wraps the agent's
    SQL so the viewing merchant's data is excluded.
  * Loop terminates at MAX_TURNS when the model never emits `end_turn`.
  * Mock mode produces a non-empty answer and exercises both tenant +
    lake tools.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.agents import tools as T
from src.agents.advisor import MAX_TURNS, MerchantAdvisor


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


# ---------------------------------------------------------------------------
# Loop termination
# ---------------------------------------------------------------------------

class _AlwaysToolUseClient:
    """Stub Anthropic client whose every response is a `tool_use` for `schema_info`."""

    def __init__(self) -> None:
        self.call_count = 0
        self.messages = self  # so client.messages.create(...) works

    def create(self, **_kwargs: object) -> SimpleNamespace:
        self.call_count += 1
        block = SimpleNamespace(
            type="tool_use",
            name="schema_info",
            input={},
            id=f"tu_{self.call_count}",
        )
        return SimpleNamespace(stop_reason="tool_use", content=[block])


def test_loop_terminates_at_max_turns() -> None:
    client = _AlwaysToolUseClient()
    advisor = MerchantAdvisor("KRG", mock=False, client=client)
    resp = advisor.ask("a question that will loop forever")
    assert resp.turns == MAX_TURNS
    assert client.call_count == MAX_TURNS
    assert "MAX_TURNS" in resp.answer


# ---------------------------------------------------------------------------
# Mock mode
# ---------------------------------------------------------------------------

def test_advisor_mock_returns_answer() -> None:
    advisor = MerchantAdvisor("KRG", mock=True)
    resp = advisor.ask(
        "What are my top categories by revenue last week, and which "
        "subcategories drove each?"
    )
    assert resp.answer
    assert resp.turns == 1
    assert len(resp.sql) >= 1
    assert resp.sql[0]["tool"] == "tenant"


def test_advisor_mock_exercises_lake_view_builder() -> None:
    """The mock should include a lake call routed through the
    view-builder so the rows come back peer-pseudonymized."""
    advisor = MerchantAdvisor("KRG", mock=True)
    resp = advisor.ask("How does my dairy pricing compare to peers?")
    lake_calls = [s for s in resp.sql if s["tool"] == "lake"]
    assert len(lake_calls) >= 1, "advisor mock should call query_lake"
    # The mock's lake query references the view-builder names.
    assert "lake_transactions" in lake_calls[0]["query"]
    # Result rows must be peer-pseudonymized (no underlying merchant_id).
    rows = lake_calls[0]["rows"]["rows"]
    if rows:
        peers = {row[0] for row in rows}
        assert peers <= {"peer_a", "peer_b", "peer_c", "peer_d"}


# ---------------------------------------------------------------------------
# Tool surface
# ---------------------------------------------------------------------------

def test_advisor_tool_surface_includes_both_query_tools() -> None:
    advisor = MerchantAdvisor("KRG", mock=True)
    names = {tool["name"] for tool in advisor.tools}
    assert {"schema_info", "query_tenant", "query_lake", "chart_spec"} <= names


def test_advisor_rejects_unknown_merchant_id() -> None:
    with pytest.raises(ValueError):
        MerchantAdvisor("NOPE", mock=True)


# ---------------------------------------------------------------------------
# Integration: advisor's _dispatch threads viewing_merchant_id into query_lake.
# ---------------------------------------------------------------------------

def test_advisor_dispatch_passes_viewing_merchant_id_to_lake() -> None:
    """Construct an advisor for KRG, dispatch a synthetic query_lake
    tool call, and verify the rows came back peer-pseudonymized (which
    only happens through the v2.5 view-builder path)."""
    advisor = MerchantAdvisor("KRG", mock=True)
    result = advisor._dispatch(
        "query_lake",
        {
            "query": (
                "SELECT peer_id, COUNT(*) AS n FROM lake_transactions "
                "GROUP BY peer_id ORDER BY peer_id LIMIT 10"
            )
        },
    )
    peers = {row[0] for row in result["rows"]}
    assert peers == {"peer_a", "peer_b", "peer_c", "peer_d"}
