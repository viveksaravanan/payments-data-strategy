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

# Generic descriptions per docs/archive/V3_DASHBOARD_DESIGN.md Section 4 — written
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
# Inner LLM runner — invoked by both dispatch() and dispatch_orchestrated()
# fallback paths. Raises on failure; callers translate to error responses.
#
# Phase 5.5.2 reverted the chart-takeaway pre-injection layer that
# Phase 5.1.9 introduced. The injection worked for some qids but
# failed unpredictably across the 30-qid surface (3 iterations couldn't
# stabilize convergence above 8/12). The agent now operates without
# chart context; ``chart_takeaways.py`` stays for UI captions only.
# See V3_AUDIT.md "Phase 5.5.2 — Reverted chart-takeaway injection"
# for the rationale and the v4 unified-compute plan.
# ---------------------------------------------------------------------------

def _response_to_dict(resp, agent_label: str) -> dict:
    """Map the Wave 3.5 structured ``AgentResponse`` onto the dict the
    chat renderer consumes. The response is structured (headline /
    evidence / so_what); ``prose`` is the derived joined view (back-compat);
    ``table`` is the result frame; ``chart`` is always None (charts
    deferred to Wave 4.1)."""
    import pandas as pd  # noqa: PLC0415

    result = getattr(resp, "result", None)
    table = result if isinstance(result, pd.DataFrame) and len(result) > 0 else None
    tel = getattr(resp, "telemetry", None)
    return {
        "agent":    agent_label,
        "headline": getattr(resp, "headline", "") or "",
        "evidence": list(getattr(resp, "evidence", []) or []),
        "so_what":  getattr(resp, "so_what", None),
        "prose":    getattr(resp, "prose", "") or "",
        "caveats":  list(getattr(resp, "caveats", []) or []),
        "table":    table,
        "chart":    None,
        "telemetry": {
            "model":         tel.model,
            "input_tokens":  tel.input_tokens,
            "output_tokens": tel.output_tokens,
            "cost_usd":      tel.cost_usd,
            "turns":         tel.turns,
        } if tel is not None else None,
        "error":    False,
    }


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

    resp = spec.answer(question, progress=progress, on_token=on_token)
    return _response_to_dict(resp, AGENT_LABELS.get(specialist_id, "Agent"))


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
    response. No mock fallback (per docs/archive/V3_DASHBOARD_DESIGN.md §7.3).
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
        resp = orch.ask(raw_question, progress=progress, on_token=on_token)
        return _response_to_dict(resp, "Conversational Advisor")
    except Exception:  # noqa: BLE001
        return _orchestrator_error_response()
