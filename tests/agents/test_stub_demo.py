"""Stage 1d tests — stub agent demonstrates §1 contract end-to-end.

The stub is the human-review surface for Checkpoint 1. These tests
pin that the demo produces the expected disposition matrix:

1. Clean declared claim (1.062) → passes verbatim.
2. Near-tolerance claim (≈1.06) → normalized to 1.062 in cleaned prose.
3. Fabricated declared claim (3.00) → clause stripped.
4. Fabricated undeclared metric (50%) → clause stripped via Pass B scan.
5. Structural integer "5 stores in Zone 5" → survives.

Plus the chart is built deterministically from the merged result.
"""
from __future__ import annotations

import plotly.graph_objects as go

from src.agents.stub import format_demo_report, run_stub


def test_stub_runs_end_to_end() -> None:
    response, report = run_stub()
    # AgentResponse shape is well-formed.
    assert response.result is not None
    assert len(response.result) > 0
    assert response.chart_intent["kind"] == "cross_merchant_comparison"
    assert isinstance(response.chart, go.Figure)
    assert response.prose
    assert response.claims


def test_stub_clean_claim_passes_verbatim() -> None:
    """Tier 1: 1.062 matches the cell exactly → passes verbatim."""
    _, report = run_stub()
    clean = next(d for d in report.claim_dispositions
                 if d.claim.text_span == "1.062")
    assert clean.status == "passed"
    assert clean.true_value == 1.062
    assert "1.062" in report.prose


def test_stub_near_tolerance_normalizes() -> None:
    """Tier 2: ≈1.06 (model rounding) → normalized to 1.062 (true)
    in cleaned prose."""
    _, report = run_stub()
    norm = next(d for d in report.claim_dispositions
                if d.claim.text_span == "≈1.06")
    assert norm.status == "normalized"
    assert norm.true_value == 1.062
    # Normalized prose contains the true value (1.062), not the
    # model's rounded version (≈1.06).
    assert "1.062" in report.prose


def test_stub_fabricated_declared_strips_clause() -> None:
    """Tier 3a: 3.00 (declared) doesn't match true 1.062 → clause
    stripped. The "produce index is 3.00" sentence is gone."""
    _, report = run_stub()
    fab = next(d for d in report.claim_dispositions
               if d.claim.text_span == "3.00")
    assert fab.status == "stripped"
    assert "3.00" not in report.prose
    assert "produce index" not in report.prose.lower()


def test_stub_fabricated_undeclared_strips_clause() -> None:
    """Tier 3b: 50% has no backing claim → Pass B catches it,
    clause stripped. The "Promo penetration is 50%" sentence is
    gone."""
    _, report = run_stub()
    # Pass B logged the strip.
    assert any(u["text"].endswith("%") and u["value"] == 50.0
               for u in report.undeclared_strips)
    assert "50%" not in report.prose
    assert "Promo penetration" not in report.prose


def test_stub_structural_integer_survives() -> None:
    """The boundary case the user pinned: "5 stores in Zone 5" must
    survive Pass B without a backing claim — metric/structural
    classifier exempts it."""
    _, report = run_stub()
    assert "5 stores" in report.prose
    assert "Zone 5" in report.prose


def test_stub_chart_values_come_from_result() -> None:
    """The chart's bar x-values come from the result, not from the
    model's prose — the deterministic builder pulls them by column
    name."""
    response, _ = run_stub()
    own_bar = response.chart.data[0]
    # Bar x-values come from result["own_value"], not from any
    # numeric literal in the model's prose.
    expected = response.result["own_value"].tolist()
    assert list(own_bar.x) == expected


def test_stub_response_is_returned_not_rejected() -> None:
    """Strict guarantee, graceful handling: even with two failed
    claims (declared 3.00 + undeclared 50%) and one normalization,
    the response is returned non-empty — never hard-rejected."""
    response, report = run_stub()
    assert report.has_any_strip   # something was stripped
    assert response.prose         # but the response stands
    assert len(response.prose) > 0


def test_stub_demo_report_summarizes_dispositions() -> None:
    """The human-readable demo report includes every disposition
    tier so a reviewer can verify Pass A + Pass B + structural-
    survival in one glance."""
    response, report = run_stub()
    text = format_demo_report(response, report)
    # Each tier surfaced.
    assert "passed" in text         # tier 1
    assert "normalized" in text     # tier 2
    assert "stripped" in text       # tier 3a + 3b
    # Undeclared 50% scan logged.
    assert "Pass B" in text or "Undeclared" in text
    assert "5 stores" in text or "Zone 5" in text  # the structural
    # Chart shape referenced.
    assert "cross_merchant_comparison" in text


def test_stub_has_grain_notes_and_sql() -> None:
    """The contract carries grain_notes (from the manifest) and SQL
    surfaces — even the stub demonstrates the shape."""
    response, _ = run_stub()
    assert any("peer SKU" in note for note in response.grain_notes)
    assert {s.surface for s in response.sql} == {"tenant", "lake"}
