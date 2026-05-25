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
