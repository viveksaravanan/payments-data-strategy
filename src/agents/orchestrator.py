"""Wave 3 orchestrator (D26.4) — Haiku-routed dispatch.

Free-form questions and "ask AI about this chart" (Wave 4) flow
through here. Suggested-question pills go DIRECT to their mapped
specialist via ``src.dashboard.agents.dispatch`` — they don't pass
through routing because their qid pre-resolves the agent (D26.4 /
SPEC §4).

Differences from v3:

* Routing set adds ``advisor`` as the fifth target.
* "No match" target is the Advisor, NOT a segment-default
  specialist (replaces v3's force-routing).
* Returns ``AgentResponse`` directly (the Wave 3 §1 contract) with
  the routing decision attached via ``routing`` metadata, instead
  of the v3 ``SpecialistResponse``/``OrchestratorResponse`` shapes.

The router is always Haiku (D25.8). The specialist runs on
``SPECIALIST_MODEL`` (Haiku default, Sonnet-switchable).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from src.agents import llm as L
from src.agents.context import MerchantContext
from src.agents.response import AgentResponse


VALID_AGENTS = ("pricing", "demand", "trade", "anomaly", "advisor")

PROMPT_PATH = Path(__file__).parent / "prompts" / "orchestrator.md"
_PROMPT_TEMPLATE = PROMPT_PATH.read_text()


@dataclass
class RoutingDecision:
    primary: str
    rationale: str
    via_fallback: bool = False
    viewer_segment: str | None = None
    router_in_tokens: int = 0
    router_out_tokens: int = 0
    router_cost_usd: float = 0.0
    router_elapsed_sec: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary": self.primary,
            "rationale": self.rationale,
            "via_fallback": self.via_fallback,
            "viewer_segment": self.viewer_segment,
        }


# ---------------------------------------------------------------------
# Keyword fallback
# ---------------------------------------------------------------------

_KEYWORD_RULES: list[tuple[tuple[str, ...], str]] = [
    # Advisor — payment + segment + general. Listed first so a
    # payment-related question with generic comparison phrasing
    # ("contactless vs peers") routes here rather than to Pricing.
    (("payment", "contactless", "chip", "swipe", "wallet", "apple pay",
      "google pay", "card network", "visa", "mastercard", "amex",
      "behavioral segment", "behavioral", "shopper type", "loyalist",
      "frequent value", "premium loyalist"), "advisor"),
    # Pricing — drop overly-broad "vs peer(s)" from v3; keep tight
    # price-specific phrases.
    (("price", "pricing", "expensive", "cheap", "above market",
      "below market", "price index", "category share",
      "promo penetration"), "pricing"),
    # Anomaly
    (("anomaly", "unusual", "weird", "spike", "decline", "drop",
      "fell", "outlier", "why is", "why are", "what's wrong"),
     "anomaly"),
    # Demand
    (("slow", "slowing", "slow-mover", "slow mover", "forecast",
      "campaign", "promo", "demand", "uplift", "lapsed", "former",
      "ice cream", "clear inventory", "week-over-week", "wow",
      "growth"), "demand"),
    # Trade
    (("location", "neighborhood", "area", "expansion", "open a new store",
      "open new", "where should", "trade area", "underserved",
      "catchment", "store performance", "cohort", "overlap",
      "cross-merchant"), "trade"),
]


def _keyword_route(question: str, segment: str | None) -> RoutingDecision:
    """Keyword-based routing fallback. Falls through to Advisor
    when no domain keyword matches (replaces v3's segment-conditional
    force-default)."""
    q = question.lower()
    for keywords, agent in _KEYWORD_RULES:
        for kw in keywords:
            if kw in q:
                return RoutingDecision(
                    primary=agent,
                    rationale=f"Keyword fallback matched '{kw}'.",
                    via_fallback=True,
                    viewer_segment=segment,
                )
    return RoutingDecision(
        primary="advisor",
        rationale="No specialist keyword matched; routing to Advisor.",
        via_fallback=True,
        viewer_segment=segment,
    )


# ---------------------------------------------------------------------
# Segment label normalization
# ---------------------------------------------------------------------

_GROCER_MERCHANTS = {"KRG", "ACM", "WDX"}
_QSR_MERCHANTS = {"TBL", "BKG", "CFA"}   # datamodel-v2: off-price dropped


def _segment_for_merchant(merchant_id: str) -> str:
    if merchant_id in _GROCER_MERCHANTS:
        return "grocer"
    if merchant_id in _QSR_MERCHANTS:
        return "qsr"
    return "unknown"


def _render_router_prompt(ctx: MerchantContext) -> str:
    routing_segment = _segment_for_merchant(ctx.viewing_merchant_id)
    return (
        _PROMPT_TEMPLATE
        .replace("{{viewer_id}}", ctx.viewing_merchant_id)
        .replace("{{viewer_name}}", ctx.viewing_merchant_name)
        .replace("{{viewer_segment}}", routing_segment)
    )


# ---------------------------------------------------------------------
# Router output parsing
# ---------------------------------------------------------------------

_JSON_RE = re.compile(r"\{[^{}]*\}", re.DOTALL)


def _parse_router_output(text: str) -> RoutingDecision | None:
    m = _JSON_RE.search(text)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    primary = obj.get("primary")
    if primary not in VALID_AGENTS:
        return None
    return RoutingDecision(
        primary=primary,
        rationale=str(obj.get("rationale") or "").strip()[:200]
                  or "Routed by classifier.",
        via_fallback=False,
    )


# ---------------------------------------------------------------------
# Routing entry point
# ---------------------------------------------------------------------

def route(question: str, ctx: MerchantContext) -> RoutingDecision:
    """Always-Haiku router (D25.8). On API failure or unparseable
    output, falls through to ``_keyword_route``. Final no-match
    target is the Advisor (D26.4)."""
    segment = _segment_for_merchant(ctx.viewing_merchant_id)
    if not L.is_available():
        return _keyword_route(question, segment=segment)
    try:
        import time
        client = L._client()           # noqa: SLF001 — reuse cached client
        t0 = time.monotonic()
        resp = client.messages.create(
            model=L.MODEL_ROUTER,
            system=_render_router_prompt(ctx),
            messages=[{"role": "user", "content": question}],
            max_tokens=200,
            temperature=0,   # deterministic routing (parity with the specialists' temp=0)
        )
        elapsed = time.monotonic() - t0
        usage = getattr(resp, "usage", None)
        tel = L.CallTelemetry(
            model=L.MODEL_ROUTER,
            input_tokens=getattr(usage, "input_tokens", 0) if usage else 0,
            output_tokens=getattr(usage, "output_tokens", 0) if usage else 0,
            elapsed_sec=elapsed,
        )
        L._record(tel)                 # noqa: SLF001
        text = "".join(
            b.text for b in resp.content
            if getattr(b, "type", "") == "text"
        )
        decision = _parse_router_output(text)
        if decision is None:
            return _keyword_route(question, segment=segment)
        decision.viewer_segment = segment
        decision.router_in_tokens = tel.input_tokens
        decision.router_out_tokens = tel.output_tokens
        decision.router_cost_usd = tel.cost_usd
        decision.router_elapsed_sec = tel.elapsed_sec
        return decision
    except Exception:                  # noqa: BLE001 — never fail routing
        return _keyword_route(question, segment=segment)


# ---------------------------------------------------------------------
# Specialist factory
# ---------------------------------------------------------------------

def build_agent(agent_id: str, ctx: MerchantContext):
    if agent_id == "pricing":
        from src.agents.pricing import PricingSpecialist
        return PricingSpecialist(ctx)
    if agent_id == "demand":
        from src.agents.demand import DemandForecastingSpecialist
        return DemandForecastingSpecialist(ctx)
    if agent_id == "trade":
        from src.agents.trade import TradeAreaSpecialist
        return TradeAreaSpecialist(ctx)
    if agent_id == "anomaly":
        from src.agents.anomaly import AnomalyDetectionSpecialist
        return AnomalyDetectionSpecialist(ctx)
    if agent_id == "advisor":
        from src.agents.advisor import ConversationalAdvisor
        return ConversationalAdvisor(ctx)
    raise ValueError(f"Unknown agent: {agent_id!r}")


# ---------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------

class Orchestrator:
    """Free-form-question dispatcher. Routes to one of the five
    agents (4 specialists + Advisor) and returns the agent's
    ``AgentResponse`` with routing metadata attached."""

    def __init__(self, context: MerchantContext) -> None:
        self.context = context

    def ask(
        self,
        question: str,
        *,
        progress: Callable[[int, str], None] | None = None,
        on_token: Callable[[str], None] | None = None,
    ) -> tuple[RoutingDecision, AgentResponse]:
        if progress is not None:
            progress(0, "Picking a specialist…")
        decision = route(question, self.context)
        # Anomaly Detection + Trade Area specialists are deferred — their
        # questions are handled by the Conversational Advisor. The backend
        # agents stay intact; the orchestrator simply never dispatches to
        # them. Remove this remap to re-enable them.
        if decision.primary in ("anomaly", "trade"):
            decision.primary = "advisor"
        agent = build_agent(decision.primary, self.context)
        resp = agent.answer(question, progress=progress, on_token=on_token)
        return decision, resp
