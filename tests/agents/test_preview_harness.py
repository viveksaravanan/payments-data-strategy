"""Stage 6.5 tests — preview harness (SPEC §6.5).

The preview harness is the human-review surface for routing/decline
quality. Tests pin the structure (HTML rendered; pill mode resolves
qid → agent + text; embedded chart values trace to the merged
result frame) without needing a real LLM.
"""
from __future__ import annotations

import importlib
import re

import pandas as pd
import plotly.graph_objects as go
import pytest

from src.agents.chart_build import build_chart
from src.agents.response import AgentResponse, SqlSurface, Telemetry


def _make_minimal_response() -> AgentResponse:
    """A bare-bones AgentResponse we can hand to the renderers."""
    result = pd.DataFrame([
        {"category": "DAIRY", "own_value": 1.062,
         "peer_benchmark": 1.00, "gap": 0.062},
    ])
    intent = {
        "kind": "cross_merchant_comparison",
        "x": "category",
        "series": ["own_value", "peer_benchmark"],
        "y_format": "index",
        "title": "Dairy", "takeaway": "ok",
    }
    chart = build_chart(intent, result)
    return AgentResponse(
        result=result,
        chart_intent=intent,
        chart=chart,
        headline="Your dairy price index is 1.062 above peers.",
        claims=[],
        caveats=["Demo fixture."],
        sql=[SqlSurface(surface="lake", query="<read>", row_count=1)],
        grain_notes=["no peer SKU"],
        telemetry=Telemetry(
            model="claude-haiku-4-5-20251001",
            input_tokens=12, output_tokens=10, cost_usd=0.0001, turns=3,
        ),
    )


# ---------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------

def test_chart_renders_to_interactive_html() -> None:
    """The chart embeds as self-contained Plotly HTML (CDN-loaded
    plotly.js); this is the format the dashboard will plug in."""
    preview = importlib.import_module("scripts.preview_agent")
    resp = _make_minimal_response()
    html = preview._render_chart_html(resp)
    # The embedded HTML must reference Plotly to be interactive.
    assert "plotly" in html.lower()
    # The chart's structural source-of-truth — column names from the
    # result — appears in the HTML (Plotly serializes axis labels +
    # legend names from the trace data, which we built from result
    # columns by name).
    assert "own_value" in html or "DAIRY" in html


def test_chart_html_omits_model_supplied_numbers() -> None:
    """Defense in depth: even though the harness's prose contains
    "1.062" (it came from the model), the chart HTML's numeric
    payload is sourced exclusively from the result frame. We don't
    assert the absence of every model-supplied number (Plotly's
    embedded JSON makes that brittle), but we DO assert that the
    chart's data points equal the result's column values."""
    resp = _make_minimal_response()
    # Plotly figure's first trace y-values come from result["own_value"].
    own_bar = resp.chart.data[0]
    expected = resp.result["own_value"].tolist()
    assert list(own_bar.x) == expected


def test_page_wrap_includes_merchant_label() -> None:
    preview = importlib.import_module("scripts.preview_agent")
    page = preview._wrap_page("<p>hi</p>", merchant="KRG")
    assert "<!DOCTYPE html>" in page
    assert "KRG" in page
    # Generation timestamp present.
    assert "Generated" in page


def test_render_one_includes_all_sections() -> None:
    preview = importlib.import_module("scripts.preview_agent")
    from src.agents.orchestrator import RoutingDecision
    decision = RoutingDecision(
        primary="pricing",
        rationale="Pill P1 → PricingSpecialist.",
        via_fallback=False,
    )
    section = preview._render_one(
        label="P1 — Dairy pricing",
        question="How does dairy pricing compare?",
        routing=decision,
        resp=_make_minimal_response(),
    )
    # All structural pieces of the contract are surfaced.
    assert "P1 — Dairy pricing" in section
    assert "Answer" in section            # structured answer section
    assert "Chart" not in section         # charts deferred to Wave 4
    assert "Merged result" in section
    assert "Claims" in section
    assert "SQL surfaces" in section
    assert "Grain notes" in section
    assert "Caveats" in section
    assert "Telemetry" in section


# ---------------------------------------------------------------------
# qid → agent mapping
# ---------------------------------------------------------------------

@pytest.mark.parametrize("qid, expected", [
    ("P1", "pricing"),
    ("P3", "pricing"),
    ("D1", "demand"),
    ("D7", "demand"),
    ("A1", "anomaly"),
    ("A3", "anomaly"),
    ("T1", "trade"),
])
def test_agent_for_qid_maps_prefix(qid, expected) -> None:
    preview = importlib.import_module("scripts.preview_agent")
    assert preview._agent_for_qid(qid) == expected


def test_agent_for_qid_unknown_prefix_raises() -> None:
    preview = importlib.import_module("scripts.preview_agent")
    with pytest.raises(ValueError):
        preview._agent_for_qid("Z9")


# ---------------------------------------------------------------------
# Pill iteration
# ---------------------------------------------------------------------

def test_iter_all_pills_for_grocer_covers_all_specialists() -> None:
    preview = importlib.import_module("scripts.preview_agent")
    pills = list(preview._iter_all_pills("KRG"))
    qids = {q for q, _, _ in pills}
    agents = {a for _, _, a in pills}
    # Grocer registry covers all four specialists.
    assert agents == {"pricing", "demand", "trade", "anomaly"}
    # The well-known pills are present (P1, A1, D3/D4/D7, T*).
    assert "P1" in qids
    assert "A1" in qids


def test_render_empty_result_table_shows_empty_hint() -> None:
    preview = importlib.import_module("scripts.preview_agent")
    html = preview._render_result_table(pd.DataFrame())
    assert "empty" in html.lower()


def test_render_no_claims_shows_hint() -> None:
    preview = importlib.import_module("scripts.preview_agent")
    resp = _make_minimal_response()
    resp.claims = []
    assert "no claims" in preview._render_claims(resp).lower()
