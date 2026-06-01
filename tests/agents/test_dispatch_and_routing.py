"""Stage 4 — orchestrator routing + two dispatch paths (SPEC §4,
D26.4).

Coverage:

* Keyword fallback maps known phrases to the right agent; no
  domain match → Advisor (NOT segment-default).
* LLM router (mocked) — happy path returns valid JSON →
  RoutingDecision; malformed JSON → keyword fallback; missing API
  key → keyword fallback.
* Routing set includes ``advisor``.
* Dispatch paths:
  - Pill path goes DIRECT to the named agent; orchestrator router
    NOT invoked (no Haiku call); rationale quotes the qid.
  - Free-form path goes through the orchestrator (here mocked via
    keyword fallback since no API key in tests); returns the
    AgentResponse from the chosen agent.
"""
from __future__ import annotations

import pytest

from src.agents import llm as L
from src.agents.context import MerchantContext
from src.agents.dispatch import dispatch_freeform, dispatch_pill
from src.agents.orchestrator import (
    VALID_AGENTS,
    RoutingDecision,
    _keyword_route,
    _parse_router_output,
    route,
)
from src.agents.response import AgentResponse
from tests.agents._fake_llm import (
    patch_llm,
    scripted_emit_response,
    scripted_text,
    scripted_tool_use,
)


# ---------------------------------------------------------------------
# Routing set + keyword fallback
# ---------------------------------------------------------------------

def test_valid_agents_includes_advisor() -> None:
    """The Wave 3 routing set has five members — the four specialists
    plus Advisor (D26.4)."""
    assert set(VALID_AGENTS) == {
        "pricing", "demand", "trade", "anomaly", "advisor",
    }


@pytest.mark.parametrize("question, expected_agent", [
    ("How does our dairy pricing compare to peers?", "pricing"),
    ("Why are dairy units declining this week?", "anomaly"),
    ("What categories are slowing wow?", "demand"),
    ("Where should we open a new store?", "trade"),
    ("What's our contactless share vs peers?", "advisor"),
    ("Tell me about behavioral segments in Zone 5.", "advisor"),
])
def test_keyword_route_maps_to_expected_agent(
    question, expected_agent,
) -> None:
    decision = _keyword_route(question, segment="grocer")
    assert decision.primary == expected_agent
    assert decision.via_fallback is True


def test_no_keyword_match_falls_through_to_advisor() -> None:
    """The decisive Wave 3 change: no-match → Advisor, NOT segment
    default specialist."""
    decision = _keyword_route(
        "Tell me something interesting.", segment="grocer",
    )
    assert decision.primary == "advisor"
    assert decision.via_fallback is True
    assert "advisor" in decision.rationale.lower()


def test_no_keyword_match_qsr_also_goes_to_advisor() -> None:
    """v3 routed QSR no-match → demand (sole-of-segment default).
    Wave 3 retires that — Advisor is the universal no-match
    target."""
    decision = _keyword_route(
        "Tell me something interesting.", segment="qsr",
    )
    assert decision.primary == "advisor"


def test_no_keyword_match_retail_also_goes_to_advisor() -> None:
    decision = _keyword_route(
        "Tell me something interesting.", segment="retail",
    )
    assert decision.primary == "advisor"


# ---------------------------------------------------------------------
# Router parse
# ---------------------------------------------------------------------

def test_parse_router_valid_output() -> None:
    text = '{"primary": "pricing", "rationale": "Pricing question."}'
    decision = _parse_router_output(text)
    assert decision is not None
    assert decision.primary == "pricing"


def test_parse_router_invalid_agent_returns_none() -> None:
    text = '{"primary": "not_an_agent", "rationale": "X."}'
    assert _parse_router_output(text) is None


def test_parse_router_malformed_json_returns_none() -> None:
    assert _parse_router_output("not even close to json") is None


def test_parse_router_advisor_is_valid_target() -> None:
    """Routing to advisor IS valid (D26.4 — the new no-match target)."""
    text = '{"primary": "advisor", "rationale": "General question."}'
    decision = _parse_router_output(text)
    assert decision is not None
    assert decision.primary == "advisor"


# ---------------------------------------------------------------------
# route() without an API key falls through to keyword fallback
# ---------------------------------------------------------------------

def test_route_without_api_key_uses_keyword_fallback(monkeypatch) -> None:
    """When ANTHROPIC_API_KEY is absent, route() goes straight to
    the keyword fallback path (no API call attempted)."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    ctx = MerchantContext.for_merchant("KRG")
    decision = route("How does our pricing compare?", ctx)
    assert decision.primary == "pricing"
    assert decision.via_fallback is True


# ---------------------------------------------------------------------
# Pill path — DIRECT dispatch (skips router)
# ---------------------------------------------------------------------

def test_dispatch_pill_goes_direct_to_pricing(monkeypatch) -> None:
    """Pill carries a pre-mapped agent id; orchestrator router
    should NOT be invoked. We confirm by NOT scripting a router
    response — if route() were called, the test would fail."""
    own_sql = (
        "SELECT i.category, AVG(i.unit_price) AS own_avg_price "
        "FROM transaction_items i JOIN transactions t USING (txn_id) "
        "WHERE t.banner_code = 'KRG' AND i.category = 'DAIRY' "
        "GROUP BY i.category"
    )
    emit_pricing = scripted_emit_response(
        prose="Your dairy own price averages 3.50 across zones.",
        merge={
            "on": ["category"],
            "own_value_col": "own_avg_price",
            "peer_value_col": "price_index",
            "gap_op": "difference",
        },
        chart_intent={
            "kind": "cross_merchant_comparison",
            "x": "category",
            "series": ["own_value", "peer_benchmark"],
        },
        claims=[{
            "text_span": "3.50",
            "value": 3.50,
            "source": {
                "type": "CellLookup",
                "row_filter": {"category": "DAIRY"},
                "column": "own_value",
                "agg": "mean",
            },
        }],
    )
    # No API key needed because pill skips routing.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    script = [
        scripted_tool_use("query_tenant", {"sql": own_sql}),
        scripted_tool_use("read_lake_table", {
            "table": "lake_category_metrics",
            "filters": {"category": "DAIRY", "grain": "cat_week"},
        }),
        emit_pricing,
    ]
    with patch_llm(monkeypatch, script):
        decision, resp = dispatch_pill(
            agent_id="pricing",
            merchant_id="KRG",
            question="P1 — dairy pricing pill",
            qid="P1",
        )
    assert decision.primary == "pricing"
    assert decision.via_fallback is False
    assert "P1" in decision.rationale
    assert isinstance(resp, AgentResponse)
    assert "3.50" in resp.prose


def test_dispatch_pill_advisor_target_works(monkeypatch) -> None:
    """Pills can also route directly to the Advisor for payment /
    segment pills."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    emit_advisor = scripted_emit_response(
        prose="Across your zones, peer contactless share averages 0.62.",
        merge={},
        chart_intent={
            "kind": "cross_merchant_comparison",
            "x": "derived_zone",
            "series": ["contactless_share"],
            "y_format": "pct",
        },
        claims=[{
            "text_span": "0.62",
            "value": 0.62,
            "source": {
                "type": "CellLookup",
                "row_filter": {},
                "column": "contactless_share",
                "agg": "mean",
            },
        }],
    )
    script = [
        scripted_tool_use("read_lake_table", {
            "table": "lake_payment_mix",
        }),
        emit_advisor,
    ]
    with patch_llm(monkeypatch, script):
        decision, resp = dispatch_pill(
            agent_id="advisor",
            merchant_id="KRG",
            question="A1 — contactless pill",
            qid="A1",
        )
    assert decision.primary == "advisor"
    assert isinstance(resp, AgentResponse)


# ---------------------------------------------------------------------
# Free-form path — through orchestrator
# ---------------------------------------------------------------------

def test_dispatch_freeform_routes_via_keyword_fallback(monkeypatch) -> None:
    """Without an API key, the orchestrator falls through to the
    keyword fallback. A pricing question routes to PricingSpecialist
    which runs the scripted tool sequence."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    own_sql = (
        "SELECT i.category, AVG(i.unit_price) AS own_avg_price "
        "FROM transaction_items i JOIN transactions t USING (txn_id) "
        "WHERE t.banner_code = 'KRG' AND i.category = 'DAIRY' "
        "GROUP BY i.category"
    )
    emit_pricing = scripted_emit_response(
        prose="Your dairy own price averages 3.50.",
        merge={
            "on": ["category"],
            "own_value_col": "own_avg_price",
            "peer_value_col": "price_index",
            "gap_op": "difference",
        },
        chart_intent={
            "kind": "cross_merchant_comparison",
            "x": "category",
            "series": ["own_value", "peer_benchmark"],
        },
        claims=[{
            "text_span": "3.50",
            "value": 3.50,
            "source": {
                "type": "CellLookup",
                "row_filter": {"category": "DAIRY"},
                "column": "own_value",
                "agg": "mean",
            },
        }],
    )
    script = [
        scripted_tool_use("query_tenant", {"sql": own_sql}),
        scripted_tool_use("read_lake_table", {
            "table": "lake_category_metrics",
            "filters": {"category": "DAIRY", "grain": "cat_week"},
        }),
        emit_pricing,
    ]
    with patch_llm(monkeypatch, script):
        decision, resp = dispatch_freeform(
            merchant_id="KRG",
            question="How does our pricing compare to peers?",
        )
    # Routed via keyword fallback (API key absent).
    assert decision.primary == "pricing"
    assert decision.via_fallback is True
    assert isinstance(resp, AgentResponse)
    assert "3.50" in resp.prose


def test_dispatch_freeform_ambiguous_question_to_advisor(monkeypatch) -> None:
    """Free-form ambiguous question (no keyword match) → Advisor.
    The Advisor handles it via the scripted tool flow."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    emit_advisor = scripted_emit_response(
        prose="Across zones, peer contactless share averages 0.62.",
        merge={},
        chart_intent={
            "kind": "cross_merchant_comparison",
            "x": "derived_zone",
            "series": ["contactless_share"],
            "y_format": "pct",
        },
        claims=[{
            "text_span": "0.62",
            "value": 0.62,
            "source": {
                "type": "CellLookup",
                "row_filter": {},
                "column": "contactless_share",
                "agg": "mean",
            },
        }],
    )
    script = [
        scripted_tool_use("read_lake_table", {
            "table": "lake_payment_mix",
        }),
        emit_advisor,
    ]
    with patch_llm(monkeypatch, script):
        decision, resp = dispatch_freeform(
            merchant_id="KRG",
            question="Show me payment trends.",
        )
    assert decision.primary == "advisor"
    assert isinstance(resp, AgentResponse)
