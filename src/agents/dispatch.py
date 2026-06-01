"""Wave 3 §4 dispatch — two paths (D26.4).

Public entry points for the Wave 4 dashboard (and the Stage 6.5
preview harness):

* `dispatch_pill(agent_id, merchant_id, question, ...)` —
  **suggested-question pill goes DIRECT to its mapped specialist,
  skipping the orchestrator.** Pills carry a pre-mapped agent id
  from the question registry; routing is known so the router is
  redundant. ``qid`` is purely an identifier/cache-key.
* `dispatch_freeform(merchant_id, question, ...)` — **free-form and
  ask-AI-about-chart (Wave 4) go through the orchestrator.** The
  Haiku router classifies, falls back to keyword routing on
  failure, and the no-match target is the Advisor (D26.4).

Both paths return the same shape: ``(RoutingDecision | None,
AgentResponse)``. The pill path's routing is a synthetic
``RoutingDecision`` with ``via_fallback=False`` and ``rationale``
quoting the qid.

The v3 ``src.dashboard.agents.dispatch`` / ``dispatch_orchestrated``
return ``dict`` shapes the v3 Streamlit chat panel consumes; that's
Wave 4's concern. Wave 3 ships the structured ``AgentResponse``
return type so the preview harness can render it directly.
"""
from __future__ import annotations

from typing import Callable

from src.agents.context import MerchantContext
from src.agents.orchestrator import (
    Orchestrator,
    RoutingDecision,
    build_agent,
)
from src.agents.response import AgentResponse


def dispatch_pill(
    agent_id: str,
    merchant_id: str,
    question: str,
    *,
    qid: str | None = None,
    progress: Callable[[int, str], None] | None = None,
    on_token: Callable[[str], None] | None = None,
) -> tuple[RoutingDecision, AgentResponse]:
    """Pill path — go DIRECT to the mapped agent (skip the router).

    The qid persists only as identifier / cache key (D26.4). The
    routing decision returned is synthetic — it carries the qid in
    the rationale so the chat UI can label which pill triggered it,
    but the router did NOT run.
    """
    ctx = MerchantContext.for_merchant(merchant_id)
    agent = build_agent(agent_id, ctx)
    decision = RoutingDecision(
        primary=agent_id,
        rationale=f"Direct dispatch from suggested-question pill {qid!r}"
                  if qid else "Direct dispatch (no router invocation).",
        via_fallback=False,
        viewer_segment=None,
    )
    resp = agent.answer(question, progress=progress, on_token=on_token)
    return decision, resp


def dispatch_freeform(
    merchant_id: str,
    question: str,
    *,
    progress: Callable[[int, str], None] | None = None,
    on_token: Callable[[str], None] | None = None,
) -> tuple[RoutingDecision, AgentResponse]:
    """Free-form path — orchestrator routes through Haiku + keyword
    fallback. The no-match target is the Advisor (D26.4)."""
    ctx = MerchantContext.for_merchant(merchant_id)
    orchestrator = Orchestrator(ctx)
    return orchestrator.ask(question, progress=progress, on_token=on_token)
