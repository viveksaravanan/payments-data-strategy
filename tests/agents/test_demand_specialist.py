"""Stage 2.2 — Demand specialist tests.

Same pattern as Pricing: scripted LLM, viewer-scoped tenant query,
lake read of ``lake_category_metrics`` (this time for
``units_index`` / ``wow_delta``), merge, chart, claims validation.

Coverage:

* Canonical demand question: weekly own units vs peer units_index.
* Off-grain daily peer ask declined: filter with a `period_start`
  that's not in the week-bucketed lake is fine (returns 0 rows),
  but a filter on a daily-resolution key is structurally not
  present — exercises that the lake's manifest excludes "no daily
  grain" is surfaced to the agent.
* Demand prompt avoids fraud vocabulary (D20.3).
"""
from __future__ import annotations

import pytest

from src.agents.context import MerchantContext
from src.agents.demand import DemandForecastingSpecialist
from src.agents.response import AgentResponse
from tests.agents._fake_llm import (
    patch_llm,
    scripted_emit_response,
    scripted_tool_use,
)


@pytest.fixture
def viewer_krg() -> MerchantContext:
    return MerchantContext.for_merchant("KRG")


def test_demand_canonical_question_units_index(viewer_krg, monkeypatch) -> None:
    """Demand question: KRG own units vs peer units_index for dairy
    over the window. Single-row tenant aggregate; many-row lake
    aggregated to mean via CellLookup.agg."""
    # Use AVG(qty) per line (~1.2) so the merge magnitude check
    # passes against peer units_index (~0.85). The Fix-2 guard
    # rejects raw SUM(qty) ~435k vs an index ~1 as nonsensical.
    own_sql = (
        "SELECT p.functional_category AS category, AVG(i.qty) AS own_avg_qty "
        "FROM transaction_items i JOIN transactions t USING (txn_id) "
        "JOIN products p ON i.sku = p.sku "
        "WHERE t.banner_code = 'KRG' AND p.functional_category = 'Milk' "
        "GROUP BY p.functional_category"
    )
    # Wave 3.5 two-query flow: own claim against the tenant frame
    # (own_avg_qty), peer against the lake frame (peer_units).
    emit = scripted_emit_response(
        headline="Your dairy per-line units run at 1.24.",
        evidence=[
            "Your dairy average units per line is 1.24.",
        ],
        claims=[
            {
                "text_span": "1.24",
                "value": 1.24,
                "source": {
                    "type": "CellLookup",
                    "row_filter": {"category": "Milk"},
                    "column": "own_avg_qty",
                    "agg": "mean",
                    "frame": "tenant",
                },
            },
        ],
        caveats=["Window: 2026-03-01 to 2026-05-29.",
                 "Peer set is 2 grocers (segment_peer)."],
    )
    script = [
        scripted_tool_use("query_tenant", {"sql": own_sql}),
        scripted_tool_use("query_lake_sql", {"sql": "SELECT category, AVG(qty) AS peer_units FROM lake_transactions WHERE peer_relationship = 'peer' AND category = 'Milk' GROUP BY category"}),
        emit,
    ]
    specialist = DemandForecastingSpecialist(viewer_krg)
    with patch_llm(monkeypatch, script):
        resp = specialist.answer("How are dairy units trending vs peers?")

    assert isinstance(resp, AgentResponse)
    assert "own_avg_qty" in resp.result.columns
    # Charts deferred to Wave 4.
    assert resp.chart is None
    # The own 1.24 claim survives (normalized to the true mean is fine).
    assert "1.24" in resp.prose or "1.23" in resp.prose
    # Two surfaces logged.
    assert {s.surface for s in resp.sql} == {"tenant", "lake_sql"}


def test_demand_prompt_never_mentions_fraud() -> None:
    """D20.3 — even the demand prompt must not contain fraud
    vocabulary."""
    prompt = DemandForecastingSpecialist.PROMPT_PATH.read_text().lower()
    assert "fraud" not in prompt
    assert "tampering" not in prompt


def test_demand_prompt_uses_query_lake_sql_flow() -> None:
    """Wave 3.5: peer data comes from query_lake_sql (line-item lake).
    The prompt must teach that flow and the partial-week guard (the old
    fixed-grain "no daily" Exclude no longer applies — txn_date is daily)."""
    prompt = DemandForecastingSpecialist.PROMPT_PATH.read_text().lower()
    assert "query_lake_sql" in prompt
    assert "read_lake_table" not in prompt
    assert "partial" in prompt  # partial-week guard retained
