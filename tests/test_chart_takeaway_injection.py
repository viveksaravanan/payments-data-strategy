"""Phase 5.1.9 — chart-takeaway pre-injection tests.

The architectural fix for the chart-vs-prose contradiction
discovered in Phase 5.1.8: the dispatcher pre-computes the chart's
authoritative takeaway and injects it into the specialist's
question as ground truth. These tests cover the injection
contract; they do NOT call the LLM.

Three tests:
  * Known qid → takeaway string returned
  * Unknown qid → ``None``
  * ``_run_specialist`` injects the takeaway into the question
    passed to the specialist's ``answer()`` call
"""
from __future__ import annotations

from unittest.mock import patch

from src.dashboard.agents import _compute_chart_takeaway, _run_specialist
from src.dashboard.chart_takeaways import (
    compute_takeaway,
    is_pattern_fallback,
    PATTERN_FALLBACK_MARKER,
    _PATTERN_FALLBACKS,
    _REGISTRY,
)


def test_compute_chart_takeaway_for_known_qid():
    """T-P2 has a chart helper that produces a takeaway. The
    string must contain at least the top or next-largest category
    surfaced by ``category_unit_price_trends`` (BURR or BFAST in
    the current TBL data, per the failing case from Phase 5.1.8)."""
    takeaway = _compute_chart_takeaway("T-P2", "TBL")
    assert takeaway is not None, (
        "T-P2 takeaway must not be None for TBL — chart helper "
        "should always have data in the panel window."
    )
    assert "BURR" in takeaway or "BFAST" in takeaway, (
        f"Expected BURR or BFAST in T-P2 takeaway; got: {takeaway!r}"
    )


def test_compute_chart_takeaway_for_unknown_qid():
    """Unknown qids return None; the dispatch path proceeds without
    chart-takeaway injection (specialist behaves as before)."""
    assert _compute_chart_takeaway("FAKE_QID", "KRG") is None
    # ``None`` qid also short-circuits (free-form orchestrated
    # dispatch path).
    assert _compute_chart_takeaway(None, "KRG") is None


def test_run_specialist_injects_takeaway_for_known_qid():
    """When a known qid is dispatched, the question passed into
    the specialist's ``answer()`` carries the chart takeaway as
    ground truth. We mock ``answer`` to capture the argument
    without making a live LLM call."""

    captured: dict = {}

    class _StubSpecialist:
        def __init__(self, ctx):
            self.ctx = ctx
        def answer(self, question, *, progress=None, on_token=None):
            captured["question"] = question
            class _Resp:
                def to_dict(self_inner):
                    return {
                        "agent":   "stub",
                        "prose":   "",
                        "caveats": [],
                        "table":   None,
                        "chart":   None,
                    }
            return _Resp()

    with patch(
        "src.agents.pricing.PricingSpecialist", _StubSpecialist,
    ):
        _run_specialist("pricing", "T-P2", "TBL")

    assert "question" in captured, "specialist.answer() must have been called"
    q = captured["question"]
    assert "Authoritative takeaway from the chart" in q, (
        f"Expected injected takeaway header in question; got: {q!r}"
    )
    # The TBL T-P2 takeaway names BURR or BFAST as the top shift.
    assert "BURR" in q or "BFAST" in q, (
        f"Expected injected takeaway to reference BURR or BFAST; got: {q!r}"
    )


# ---------------------------------------------------------------------------
# Phase 5.2 — coverage extension + pattern-type fallback
# ---------------------------------------------------------------------------

def test_compute_takeaway_pattern_fallback_for_d7():
    """D7 renders two separate waterfalls (one per peer) — no single
    sentence summarizes both. The pattern-fallback path kicks in and
    returns a brief chart-shape descriptor instead of ``None``."""
    result = compute_takeaway("D7", "KRG")
    assert result is not None, (
        "D7 should fall back to a pattern-type descriptor, not None."
    )
    assert is_pattern_fallback(result), (
        f"Expected D7 result to be detected as a pattern fallback; "
        f"got: {result!r}"
    )
    assert PATTERN_FALLBACK_MARKER in result
    assert "waterfall" in result.lower()


def test_takeaway_coverage_matches_question_renderers():
    """Every qid in chat.py's QUESTION_RENDERERS should have either a
    full takeaway handler in ``_REGISTRY`` OR a pattern fallback in
    ``_PATTERN_FALLBACKS``. Coverage regression canary."""
    from src.dashboard.chat import QUESTION_RENDERERS

    covered_full     = set(_REGISTRY.keys())
    covered_fallback = set(_PATTERN_FALLBACKS.keys())
    covered_total    = covered_full | covered_fallback
    registered       = set(QUESTION_RENDERERS.keys())
    uncovered        = registered - covered_total

    assert not uncovered, (
        f"qids registered in QUESTION_RENDERERS but missing from "
        f"chart_takeaways coverage: {sorted(uncovered)}"
    )


def test_is_pattern_fallback_discriminates():
    """``is_pattern_fallback`` should return True for fallback strings
    and False for full takeaways and ``None``."""
    # Full takeaway (no marker)
    assert not is_pattern_fallback("BURR prices are down 0.9% over 90 days.")
    # Pattern fallback (marker present)
    assert is_pattern_fallback(
        "The chart that will render below your response is a heatmap..."
    )
    # None input
    assert not is_pattern_fallback(None)
