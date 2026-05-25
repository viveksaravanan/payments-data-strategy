"""Specialist dispatch + orchestration for the chat panel.

Two entry points:

- `dispatch(specialist_id, question_id, merchant_id, ...)` — runs a
  suggested-question pill through the named specialist (pricing /
  anomaly / demand / trade). Cached per Streamlit session.
- `dispatch_orchestrated(merchant_id, raw_question, ...)` — runs a
  free-form question through the Haiku-router orchestrator, which
  dispatches to one of the four specialists. Never cached.

No mock fallback. When the LLM is unreachable (no API key, API
error, timeout) both entry points return an honest error response
of the same shape as a successful response so `chat.py`'s rendering
code continues to work unchanged. The chat shows
ERROR_PROSE in the assistant bubble.

The dispatch function signatures are preserved byte-identical from
the v2.5 `placeholders.py` so `chat.py`'s call sites only need
import-path updates.
"""
from __future__ import annotations

from typing import Callable


# ---------------------------------------------------------------------------
# Specialist labels and one-line descriptions
# ---------------------------------------------------------------------------

AGENT_LABELS = {
    "demand":  "Demand Forecasting Agent",
    "pricing": "Pricing & Benchmarking Agent",
    "anomaly": "Anomaly Detection Agent",
    "trade":   "Trade Area Intelligence Agent",
}

# Generic descriptions per V3_DASHBOARD_DESIGN.md Section 4 — written
# to be true for any viewer (grocer, QSR, retail).
AGENT_DESCRIPTIONS = {
    "pricing": "Benchmarks your pricing across categories and SKUs, surfaces where you're aligned or out of position.",
    "anomaly": "Detects unusual patterns in your stores, categories, and traffic — flags what changed and helps you investigate.",
    "demand":  "Analyzes your basket composition, category momentum, and revenue drivers.",
    "trade":   "Maps your trade area, customer geography, and expansion opportunities.",
}


# ---------------------------------------------------------------------------
# Error response shape
# ---------------------------------------------------------------------------

ERROR_PROSE = "Couldn't reach the agent. Please try again in a moment."


def _error_response(specialist_id: str) -> dict:
    """Honest error response for the chat — shape matches a successful
    response so the rendering code in `chat.py` consumes it uniformly."""
    return {
        "agent":   AGENT_LABELS.get(specialist_id, "Agent"),
        "prose":   ERROR_PROSE,
        "caveats": None,
        "table":   None,
        "chart":   None,
        "error":   True,
    }


def _orchestrator_error_response() -> dict:
    return {
        "agent":   "Conversational Advisor",
        "prose":   ERROR_PROSE,
        "caveats": None,
        "table":   None,
        "chart":   None,
        "error":   True,
    }


# ---------------------------------------------------------------------------
# Session-state cache for suggested-question dispatch
#
# Cached per Streamlit session. Lifetime ends on browser refresh. Scope
# is suggested-question buttons only; free-form orchestrator calls are
# never cached.
# ---------------------------------------------------------------------------

_CacheKey = tuple[str, str, str]


def _cache_get(key: _CacheKey) -> "dict | None":
    try:
        import streamlit as st  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return None
    try:
        bucket: dict[_CacheKey, dict] = st.session_state.setdefault("llm_cache", {})
    except Exception:  # noqa: BLE001 — no Streamlit runtime (tests)
        return None
    return bucket.get(key)


def _cache_set(key: _CacheKey, value: dict) -> None:
    try:
        import streamlit as st  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return
    try:
        bucket: dict[_CacheKey, dict] = st.session_state.setdefault("llm_cache", {})
    except Exception:  # noqa: BLE001
        return
    # Don't cache error responses — retry the LLM on the next click
    # rather than freezing a "couldn't reach the agent" message in.
    if value.get("error"):
        return
    bucket[key] = value


# ---------------------------------------------------------------------------
# Question-text lookup for suggested-question dispatch
# ---------------------------------------------------------------------------

def _question_text_for(specialist_id: str, question_id: str, merchant_id: str) -> str:
    """Look up the question text from the v3 registry. Falls back to the
    raw question_id if no entry matches (treats it as free-form text)."""
    from src.dashboard.questions import questions_for
    try:
        for q in questions_for(merchant_id, specialist_id):
            if q["id"] == question_id:
                return q["text"]
    except (KeyError, ValueError):
        pass
    return question_id


# ---------------------------------------------------------------------------
# Chart-takeaway pre-injection (Phase 5.1.9)
#
# For suggested-question dispatches (i.e. dispatches with a known
# qid), we pre-compute the chart's authoritative takeaway by calling
# the same data helper the chart renderer will use. The takeaway is
# then injected into the specialist's question as ground truth.
# This collapses two analytical windows (agent SQL vs chart helper)
# into one canonical view — the agent's prose cannot drift from the
# chart caption beneath it.
#
# For free-form orchestrated dispatch (no qid), there is no chart
# and no takeaway; the specialist proceeds normally.
# ---------------------------------------------------------------------------

def _compute_chart_takeaway(
    qid: "str | None",
    merchant_id: str,
) -> "str | None":
    """For a known qid, return the chart's authoritative takeaway
    string (same string the chart caption will display). Returns
    ``None`` for unknown qids, qids with no chart helper, or chart
    helpers that returned no data — in all those cases the dispatch
    path proceeds without injection."""
    if not qid:
        return None
    from src.dashboard import chart_takeaways as CT
    return CT.compute_takeaway(qid, merchant_id)


# ---------------------------------------------------------------------------
# Inner LLM runner — invoked by both dispatch() and dispatch_orchestrated()
# fallback paths. Raises on failure; callers translate to error responses.
# ---------------------------------------------------------------------------

def _run_specialist(
    specialist_id: str,
    question_id: "str | None",
    merchant_id: str,
    *,
    progress: "Callable[[int, str], None] | None" = None,
    on_token: "Callable[[str], None] | None" = None,
    raw_question: "str | None" = None,
) -> dict:
    """Run a specialist's LLM specialist. Raises on failure.

    `raw_question` overrides the registry lookup — used by free-form
    dispatch where the user typed an arbitrary question.
    """
    from src.agents.context import MerchantContext

    question = raw_question or _question_text_for(specialist_id, question_id, merchant_id)
    ctx = MerchantContext.for_merchant(merchant_id)

    # Phase 5.1.9: pre-compute the chart's authoritative takeaway and
    # inject as ground truth into the specialist's question. When the
    # chart renders below the agent's response, its mechanically-
    # computed caption is THIS string — so the agent's prose has the
    # same source of truth the user sees on the chart.
    takeaway = _compute_chart_takeaway(question_id, merchant_id)
    if takeaway:
        question = (
            f"{question}\n\n"
            f"**Authoritative takeaway from the chart that will render "
            f"below your response:**\n"
            f'"{takeaway}"\n\n'
            f"Your prose MUST be consistent with this takeaway in "
            f"direction and magnitude. If your tool results suggest a "
            f"different analytical window or answer, RE-QUERY using "
            f"the same window the takeaway uses (typically first week "
            f"vs last week in a trajectory, not first-half-mean vs "
            f"second-half-mean) before responding."
        )

    if specialist_id == "pricing":
        from src.agents.pricing import PricingSpecialist
        spec = PricingSpecialist(ctx)
    elif specialist_id == "anomaly":
        from src.agents.anomaly import AnomalyDetectionSpecialist
        spec = AnomalyDetectionSpecialist(ctx)
    elif specialist_id == "demand":
        from src.agents.demand import DemandForecastingSpecialist
        spec = DemandForecastingSpecialist(ctx)
    elif specialist_id == "trade":
        from src.agents.trade import TradeAreaSpecialist
        spec = TradeAreaSpecialist(ctx)
    else:
        raise NotImplementedError(
            f"No specialist wired for specialist_id={specialist_id!r}"
        )

    return spec.answer(question, progress=progress, on_token=on_token).to_dict()


# ---------------------------------------------------------------------------
# Dispatch entry points
# ---------------------------------------------------------------------------

def dispatch(
    agent_id: str,
    question_id: str,
    merchant_id: str,
    *,
    progress: "Callable[[int, str], None] | None" = None,
    on_token: "Callable[[str], None] | None" = None,
) -> dict:
    """Suggested-question entry point. Cached per session.

    On LLM unavailability or failure, returns an honest error
    response. No mock fallback (per V3_DASHBOARD_DESIGN.md §7.3).
    """
    key: _CacheKey = (agent_id, question_id, merchant_id)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    from src.agents import llm

    if not llm.is_available():
        return _error_response(agent_id)

    try:
        resp = _run_specialist(
            agent_id, question_id, merchant_id,
            progress=progress, on_token=on_token,
        )
    except Exception:  # noqa: BLE001 — translated to an honest error
        return _error_response(agent_id)

    _cache_set(key, resp)
    return resp


def dispatch_orchestrated(
    merchant_id: str,
    raw_question: str,
    *,
    progress: "Callable[[int, str], None] | None" = None,
    on_token: "Callable[[str], None] | None" = None,
) -> dict:
    """Free-form entry point through the Haiku-router orchestrator.

    The orchestrator classifies intent via a Haiku router and
    dispatches to the chosen specialist. On any failure (LLM
    unavailable, router error, specialist error) returns an honest
    error response. Never cached.
    """
    from src.agents import llm

    if not llm.is_available():
        return _orchestrator_error_response()

    try:
        from src.agents.context import MerchantContext
        from src.agents.orchestrator import Orchestrator
        ctx = MerchantContext.for_merchant(merchant_id)
        orch = Orchestrator(ctx)
        return orch.ask(raw_question, progress=progress, on_token=on_token).to_dict()
    except Exception:  # noqa: BLE001
        return _orchestrator_error_response()
