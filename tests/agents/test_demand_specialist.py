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
    own_sql = (
        "SELECT i.category, SUM(i.qty) AS own_units "
        "FROM transaction_items i JOIN transactions t USING (txn_id) "
        "WHERE t.banner_code = 'KRG' AND i.category = 'DAIRY' "
        "GROUP BY i.category"
    )
    emit = scripted_emit_response(
        prose="Your dairy own units total ground vs the peer "
              "units_index average of 0.93.",
        merge={
            "on": ["category"],
            "own_value_col": "own_units",
            "peer_value_col": "units_index",
            "gap_op": "difference",
        },
        chart_intent={
            "kind": "cross_merchant_comparison",
            "x": "category",
            "series": ["own_value", "peer_benchmark"],
            "y_format": "index",
            "title": "Dairy demand vs peer baseline",
            "takeaway": "Peer units index averages just below 1.0.",
        },
        claims=[{
            "text_span": "0.93",
            "value": 0.93,
            "source": {
                "type": "CellLookup",
                "row_filter": {"category": "DAIRY"},
                "column": "peer_benchmark",
                "agg": "mean",
            },
        }],
        caveats=["Window: 2026-03-01 to 2026-05-29.",
                 "Peer set is 2 grocers (segment_peer)."],
    )
    script = [
        scripted_tool_use("query_tenant", {"sql": own_sql}),
        scripted_tool_use("read_lake_table", {
            "table": "lake_category_metrics",
            "filters": {"category": "DAIRY", "grain": "cat_week"},
        }),
        emit,
    ]
    specialist = DemandForecastingSpecialist(viewer_krg)
    with patch_llm(monkeypatch, script):
        resp = specialist.answer("How are dairy units trending vs peers?")

    assert isinstance(resp, AgentResponse)
    assert "own_value" in resp.result.columns
    assert "peer_benchmark" in resp.result.columns
    # Chart built; prose retains 1.00.
    assert resp.chart is not None
    # The validator may normalize 0.93 to 0.9258 (the true mean, at
    # faithful precision) — either survives.
    assert "0.93" in resp.prose or "0.9258" in resp.prose
    # Grain notes carry "no daily" exclude.
    joined = " ".join(resp.grain_notes).lower()
    assert "daily" in joined or "week" in joined


def test_demand_off_grain_daily_filter_rejected(viewer_krg, monkeypatch) -> None:
    """Daily-grain peer asks aren't published. The lake_tools.filter
    validator catches `txn_date` (a v3-era field name no longer in the
    manifest dimensions) and rejects with the manifest excludes."""
    from src.agents import lake_tools as LT
    from src.agents.lake_tools import LakeToolError

    with pytest.raises(LakeToolError) as exc:
        LT.read_lake_table(
            "KRG", "lake_category_metrics",
            filters={"txn_date": "2026-05-15"},
        )
    msg = str(exc.value).lower()
    # The Excludes ("no daily grain") surfaces in the rejection.
    assert "txn_date" in str(exc.value)
    assert "daily" in msg or "week" in msg


def test_demand_prompt_never_mentions_fraud() -> None:
    """D20.3 — even the demand prompt must not contain fraud
    vocabulary."""
    prompt = DemandForecastingSpecialist.PROMPT_PATH.read_text().lower()
    assert "fraud" not in prompt
    assert "tampering" not in prompt


def test_demand_prompt_declares_no_daily_grain() -> None:
    """The prompt must explicitly state the "no daily peer grain"
    Exclude so the model knows to decline daily asks."""
    prompt = DemandForecastingSpecialist.PROMPT_PATH.read_text().lower()
    assert "no daily" in prompt or "week is the finest" in prompt
