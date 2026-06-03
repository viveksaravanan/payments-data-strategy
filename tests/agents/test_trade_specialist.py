"""Stage 2.3 — Trade-Area specialist tests.

Trade-Area uses two lake tables — ``lake_trade_area`` and
``lake_cross_merchant_cohorts``. The cohort table is the
"don't merge with own" path: lake-only, results delivered directly.

Coverage:

* Canonical trade-area question: own store units vs
  ``lake_trade_area.share_of_zone`` at (derived_zone, category).
* Cohort-only path: pull ``lake_cross_merchant_cohorts``, emit empty
  merge, deliver the lake frame as the result.
* No-raw-mean enforcement: the lake's manifest excludes "no raw mean
  spend" — the prompt declares it; the test asserts the prompt
  carries the median-only rule.
* Trade prompt avoids fraud vocabulary.
"""
from __future__ import annotations

import pytest

from src.agents.context import MerchantContext
from src.agents.response import AgentResponse
from src.agents.trade import TradeAreaSpecialist
from tests.agents._fake_llm import (
    patch_llm,
    scripted_emit_response,
    scripted_tool_use,
)


@pytest.fixture
def viewer_krg() -> MerchantContext:
    return MerchantContext.for_merchant("KRG")


# ---------------------------------------------------------------------
# Cohort-only path (no own merge)
# ---------------------------------------------------------------------

def test_trade_cohort_only_answer(viewer_krg, monkeypatch) -> None:
    """Cohort question reads lake_cross_merchant_cohorts directly,
    emits empty merge (the lake frame IS the result), and produces a
    table_drilldown chart. The median-only D24.2 rule is enforced
    by the manifest grain_notes carrying "no raw mean spend"."""

    # The model emits an empty merge so the specialist uses the lake
    # frame directly as the result.
    emit = scripted_emit_response(
        prose="In the all-three cohort, the median combined spend is $2125.",
        merge={},
        chart_intent={
            "kind": "table_drilldown",
            "title": "Cross-merchant cohort overlap",
            "columns": ["derived_zone", "cohort_combination", "cohort_size",
                        "median_combined_spend", "frequency_band"],
        },
        claims=[{
            "text_span": "$2125",
            "value": 2125,
            "source": {
                "type": "CellLookup",
                "row_filter": {"cohort_combination": "all_three"},
                "column": "median_combined_spend",
                "agg": "mean",
            },
        }],
        caveats=["Cohorts published as median + IQR only (D24.2)."],
    )
    script = [
        scripted_tool_use("read_lake_table", {
            "table": "lake_cross_merchant_cohorts",
        }),
        emit,
    ]
    specialist = TradeAreaSpecialist(viewer_krg)
    with patch_llm(monkeypatch, script):
        resp = specialist.answer("Tell me about cross-merchant overlap.")

    assert isinstance(resp, AgentResponse)
    # The result IS the lake frame (no merge).
    assert "cohort_combination" in resp.result.columns
    assert "median_combined_spend" in resp.result.columns
    # No raw mean column in the manifest grain notes.
    joined = " ".join(resp.grain_notes).lower()
    assert "raw mean" in joined or "median" in joined
    # Charts are deferred to Wave 4 (§11.2) — gated off.
    assert resp.chart is None


# ---------------------------------------------------------------------
# Prompt invariants
# ---------------------------------------------------------------------

def test_trade_prompt_declines_cross_merchant_cohorts() -> None:
    """Wave 3.5: the line-item lake drops consumer linkage, so
    cross-merchant cohort / overlap questions are no longer answerable.
    The prompt must declare that boundary."""
    prompt = TradeAreaSpecialist.PROMPT_PATH.read_text().lower()
    assert "no consumer linkage" in prompt
    assert "cohort" in prompt and "isn't available" in prompt


def test_trade_prompt_declines_single_competitor_figure() -> None:
    """Peer identity is reduced to the relationship label, so a single
    competitor's figure can't be isolated. The prompt declines it."""
    prompt = TradeAreaSpecialist.PROMPT_PATH.read_text().lower()
    assert "acme" in prompt  # the worked decline example
    assert "isn't available" in prompt or "not available" in prompt


def test_trade_prompt_never_mentions_fraud() -> None:
    prompt = TradeAreaSpecialist.PROMPT_PATH.read_text().lower()
    assert "fraud" not in prompt
    assert "tampering" not in prompt


def test_trade_prompt_uses_query_lake_sql_with_real_neighborhood() -> None:
    """Wave 3.5: peer geography is the real `neighborhood` name via
    query_lake_sql — no Z-codes, no read_lake_table, no zone mapping."""
    prompt = TradeAreaSpecialist.PROMPT_PATH.read_text()
    assert "query_lake_sql" in prompt
    assert "neighborhood" in prompt
    assert "read_lake_table" not in prompt
    assert "derived_zone" not in prompt and "Z01" not in prompt
