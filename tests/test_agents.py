"""Agent tests. See PLAN.md §12.

  * SQL guard rejects writes / multi-statement.
  * `query_tenant` rejects queries lacking the merchant_id predicate.
  * Loop terminates at MAX_TURNS when the model never emits `end_turn`.
  * Mock mode produces non-empty answers for both agents.
  * Network Analyst does not have access to `query_tenant`.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.agents import tools as T
from src.agents.advisor import MAX_TURNS, MerchantAdvisor
from src.agents.analyst import NetworkAnalyst


# ---------------------------------------------------------------------------
# SQL guard
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sql", [
    "DROP TABLE tenant_customers",
    "INSERT INTO tenant_customers VALUES ('x','y','z','12345','2020-01-01','credit',0)",
    "UPDATE tenant_customers SET age_band = '99'",
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
        T.query_lake(sql)


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
    # Some SQL dialects accept double-quoted strings; SQLite accepts them as identifiers
    # by default, but our regex permits either quote style. Accept the predicate; the DB
    # will then evaluate it.
    sql = 'SELECT txn_id FROM tenant_transactions WHERE merchant_id = "KRG" LIMIT 1'
    # has_merchant_predicate is just the predicate check; full execution may or may not
    # succeed depending on SQLite quote-handling. We only assert the predicate check passes.
    assert T.has_merchant_predicate(sql, "KRG") is True


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
    resp = advisor.ask("What are my top categories by revenue last week, and which subcategories drove each?")
    assert resp.answer
    assert resp.turns == 1
    assert len(resp.sql) >= 1
    assert resp.sql[0]["tool"] == "tenant"


def test_analyst_mock_returns_answer() -> None:
    analyst = NetworkAnalyst(mock=True)
    resp = analyst.ask("How do customers split across the panel?")
    assert resp.answer
    assert resp.turns == 1
    assert len(resp.sql) >= 1
    assert resp.sql[0]["tool"] == "lake"


# ---------------------------------------------------------------------------
# Tool surface — analyst has no tenant access
# ---------------------------------------------------------------------------

def test_analyst_tool_surface_excludes_query_tenant() -> None:
    analyst = NetworkAnalyst(mock=True)
    names = {tool["name"] for tool in analyst.tools}
    assert "query_tenant" not in names
    assert "query_lake" in names
    assert "schema_info" in names


def test_advisor_tool_surface_includes_both_query_tools() -> None:
    advisor = MerchantAdvisor("KRG", mock=True)
    names = {tool["name"] for tool in advisor.tools}
    assert {"schema_info", "query_tenant", "query_lake", "chart_spec"} <= names


def test_advisor_rejects_unknown_merchant_id() -> None:
    with pytest.raises(ValueError):
        MerchantAdvisor("NOPE", mock=True)
