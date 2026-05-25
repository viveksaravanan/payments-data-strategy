"""Conversational Business Advisor orchestrator.

Refactor of v2's `advisor.py`. Single responsibility: take a free-form
business question, classify it via a Haiku-based router, and dispatch
to the chosen specialist. Returns the specialist's response with the
routing decision attached so the dashboard can surface it.

Design:
  - No tool loop here. The router is one Haiku call returning a JSON
    classification; the specialist owns the tool loop.
  - On router failure (malformed JSON, transient error), fall back to
    a keyword-based router so we always return a specialist response.
  - When `secondary` is set, we currently dispatch the primary only and
    surface the secondary as a hint in the prose. Multi-specialist
    synthesis is deferred to Phase 2D.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from src.agents import llm as L
from src.agents.context import MerchantContext
from src.agents.specialist import SpecialistResponse

VALID_SPECIALISTS = ("pricing", "anomaly", "demand", "trade")

PROMPT_PATH = Path(__file__).parent / "prompts" / "orchestrator.md"
_PROMPT_TEMPLATE = PROMPT_PATH.read_text()

# Phase 5.4 — segment-conditional routing.
#
# The orchestrator's "ambiguous default" specialist depends on the
# viewer's segment:
#   grocer → anomaly  (decline-investigation is the grocer demo arc)
#   qsr    → demand   (growth tracking is TBL's arc; no same-segment peers)
#   retail → pricing  (pricing-positioning is TJX's arc)
#   unknown → demand  (original segment-blind default)
#
# Specialist prompts already see ``{{viewer_segment}}`` as the raw
# business label ("grocery"/"qsr"/"off_price_retail"). The routing
# helpers below normalize to "grocer"/"qsr"/"retail" for the
# prompt + fallback rules so the language stays user-friendly.
_GROCER_MERCHANTS = {"KRG", "ACM", "WDX"}
_QSR_MERCHANTS    = {"TBL"}
_RETAIL_MERCHANTS = {"TJX"}

_SEGMENT_DEFAULT_SPECIALIST = {
    "grocer":  "anomaly",
    "qsr":     "demand",
    "retail":  "pricing",
    "unknown": "demand",
}


def _segment_for_merchant(merchant_id: str) -> str:
    """Map merchant ID to a routing-friendly segment label.

    Returns one of ``'grocer'``, ``'qsr'``, ``'retail'``, or
    ``'unknown'`` — distinct from MerchantContext's raw business
    label (``"grocery"`` / ``"qsr"`` / ``"off_price_retail"``)."""
    if merchant_id in _GROCER_MERCHANTS:
        return "grocer"
    if merchant_id in _QSR_MERCHANTS:
        return "qsr"
    if merchant_id in _RETAIL_MERCHANTS:
        return "retail"
    return "unknown"


# Lazy specialist import — done in `_build_specialist` to keep the
# orchestrator usable in tests that don't need every specialist class.


@dataclass
class RoutingDecision:
    primary:   str
    secondary: str | None
    rationale: str
    via_fallback: bool = False  # True iff the keyword fallback was used
    viewer_segment: str | None = None  # 'grocer'|'qsr'|'retail'|'unknown'|None

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary":        self.primary,
            "secondary":      self.secondary,
            "rationale":      self.rationale,
            "via_fallback":   self.via_fallback,
            "viewer_segment": self.viewer_segment,
        }


@dataclass
class OrchestratorResponse:
    """The shape `chat.py` and `placeholders.py` consume."""
    routing:       RoutingDecision
    specialist:    SpecialistResponse
    router_in_tokens:  int = 0
    router_out_tokens: int = 0
    router_cost_usd:   float = 0.0
    router_elapsed_sec: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        sd = self.specialist.to_dict()
        # Prepend a routing line to the prose so the demo audience
        # sees which specialist picked up the question.
        spec_label = sd.get("agent", "Specialist")
        prefix = (
            f"_Routed to the **{spec_label}** "
            f"({self.routing.rationale.rstrip('.')}).\\_\n\n"
        )
        sd = dict(sd)
        sd["prose"] = prefix + (sd.get("prose") or "")
        sd["routing"] = self.routing.to_dict()
        # Telemetry — fold the router hop into the totals so the
        # dashboard footer reflects the true cost of a free-form ask.
        tel = sd.get("telemetry") or {}
        tel = dict(tel)
        tel["input_tokens"]  = tel.get("input_tokens", 0)  + self.router_in_tokens
        tel["output_tokens"] = tel.get("output_tokens", 0) + self.router_out_tokens
        tel["cost_usd"]      = round(
            float(tel.get("cost_usd", 0.0)) + self.router_cost_usd, 6,
        )
        tel["router_elapsed_sec"] = round(self.router_elapsed_sec, 2)
        sd["telemetry"] = tel
        return sd


# ---------------------------------------------------------------------------
# Keyword fallback (used when the LLM router fails)
# ---------------------------------------------------------------------------

_KEYWORD_RULES: list[tuple[tuple[str, ...], str]] = [
    # Pricing
    (("price", "pricing", "expensive", "cheap", "above market",
      "below market", "compare", "vs peer", "vs peers", "share trends",
      "category share"), "pricing"),
    # Anomaly
    (("anomaly", "unusual", "weird", "spike", "decline", "drop",
      "fell", "outlier", "why is", "why are", "what's wrong"), "anomaly"),
    # Demand
    (("slow", "slowing", "slow-mover", "slow mover", "forecast",
      "campaign", "promo", "demand", "uplift", "lapsed", "former",
      "ice cream", "clear inventory"), "demand"),
    # Trade
    (("location", "neighborhood", "area", "peers cluster", "expansion",
      "open a new store", "open new", "where should", "trade area",
      "underserved", "velocity", "catchment", "store performance"), "trade"),
]


def _keyword_route(question: str, segment: str | None = None) -> RoutingDecision:
    """Keyword-based routing fallback used when the LLM router fails.

    Explicit domain keywords match first (segment-blind — an anomaly
    question is an anomaly question regardless of viewer segment).
    If no keyword matches, fall back to the segment-conditional
    default (Phase 5.4): grocer→anomaly, qsr→demand, retail→pricing.
    ``segment=None`` preserves the original segment-blind default
    ("demand") for callers that don't know the viewer segment."""
    q = question.lower()
    for keywords, agent in _KEYWORD_RULES:
        for kw in keywords:
            if kw in q:
                return RoutingDecision(
                    primary=agent,
                    secondary=None,
                    rationale=f"Keyword fallback matched '{kw}'.",
                    via_fallback=True,
                    viewer_segment=segment,
                )
    # No explicit keyword match — use segment-conditional default.
    default_specialist = _SEGMENT_DEFAULT_SPECIALIST.get(
        segment or "unknown", "demand",
    )
    rationale = (
        f"No domain match; defaulted to {default_specialist} "
        f"({segment} ambiguous default)."
        if segment in _SEGMENT_DEFAULT_SPECIALIST and segment != "unknown"
        else f"No domain match; defaulted to {default_specialist}."
    )
    return RoutingDecision(
        primary=default_specialist,
        secondary=None,
        rationale=rationale,
        via_fallback=True,
        viewer_segment=segment,
    )


# ---------------------------------------------------------------------------
# LLM router
# ---------------------------------------------------------------------------

_JSON_RE = re.compile(r"\{[^{}]*\}", re.DOTALL)


def _parse_router_output(text: str) -> RoutingDecision | None:
    """Pull the JSON object out of the router's text response.
    Returns None if the response isn't parseable as a routing decision."""
    m = _JSON_RE.search(text)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    primary = obj.get("primary")
    if primary not in VALID_SPECIALISTS:
        return None
    secondary = obj.get("secondary")
    if secondary not in (None, *VALID_SPECIALISTS):
        secondary = None
    return RoutingDecision(
        primary=primary,
        secondary=secondary,
        rationale=str(obj.get("rationale") or "").strip()[:200] or "Routed by classifier.",
        via_fallback=False,
    )


def _render_router_prompt(ctx: MerchantContext) -> str:
    # Phase 5.4: substitute the routing-normalized segment label
    # ('grocer'/'qsr'/'retail'/'unknown') so the prompt's
    # segment-conditional rules can key on a consistent vocabulary.
    routing_segment = _segment_for_merchant(ctx.viewing_merchant_id)
    return (
        _PROMPT_TEMPLATE
        .replace("{{viewer_id}}",      ctx.viewing_merchant_id)
        .replace("{{viewer_name}}",    ctx.viewing_merchant_name)
        .replace("{{viewer_segment}}", routing_segment)
    )


def route(question: str, ctx: MerchantContext) -> RoutingDecision:
    """LLM router with keyword fallback. Cheap — one Haiku call, no tools."""
    segment = _segment_for_merchant(ctx.viewing_merchant_id)
    if not L.is_available():
        return _keyword_route(question, segment=segment)
    try:
        import anthropic  # noqa: F401, PLC0415  — import smoke-check
        client = L._client()  # noqa: SLF001 — reuse the cached client
        import time
        t0 = time.monotonic()
        resp = client.messages.create(
            model=L.MODEL_ROUTER,
            system=_render_router_prompt(ctx),
            messages=[{"role": "user", "content": question}],
            max_tokens=200,
        )
        elapsed = time.monotonic() - t0
        usage = getattr(resp, "usage", None)
        tel = L.CallTelemetry(
            model=L.MODEL_ROUTER,
            input_tokens=getattr(usage, "input_tokens", 0)  if usage else 0,
            output_tokens=getattr(usage, "output_tokens", 0) if usage else 0,
            elapsed_sec=elapsed,
        )
        L._record(tel)  # noqa: SLF001
        text = "".join(
            b.text for b in resp.content
            if getattr(b, "type", "") == "text"
        )
        decision = _parse_router_output(text)
        if decision is None:
            return _keyword_route(question, segment=segment)
        decision.viewer_segment = segment
        decision._tel = tel  # type: ignore[attr-defined] — attach for caller
        return decision
    except Exception:  # noqa: BLE001 — never fail a routing decision
        return _keyword_route(question, segment=segment)


# ---------------------------------------------------------------------------
# Specialist factory
# ---------------------------------------------------------------------------

def _build_specialist(agent_id: str, ctx: MerchantContext):
    if agent_id == "pricing":
        from src.agents.pricing import PricingSpecialist
        return PricingSpecialist(ctx)
    if agent_id == "anomaly":
        from src.agents.anomaly import AnomalyDetectionSpecialist
        return AnomalyDetectionSpecialist(ctx)
    if agent_id == "demand":
        from src.agents.demand import DemandForecastingSpecialist
        return DemandForecastingSpecialist(ctx)
    if agent_id == "trade":
        from src.agents.trade import TradeAreaSpecialist
        return TradeAreaSpecialist(ctx)
    raise ValueError(f"Unknown specialist: {agent_id!r}")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

class Orchestrator:
    """Stateless — one instance per viewer is fine, but the public
    `ask` method takes the question fresh each call. No conversation
    state is carried across calls (specialists are also stateless)."""

    def __init__(self, context: MerchantContext) -> None:
        self.context = context

    def ask(
        self,
        question: str,
        *,
        progress: "Callable[[int, str], None] | None" = None,
        on_token: "Callable[[str], None] | None" = None,
    ) -> OrchestratorResponse:
        # Surface the router hop as turn 0 in the progress narration.
        if progress is not None:
            progress(0, "Picking a specialist…")
        decision = route(question, self.context)
        router_tel = getattr(decision, "_tel", None)

        spec = _build_specialist(decision.primary, self.context)
        spec_resp = spec.answer(question, progress=progress, on_token=on_token)

        # If a secondary domain was flagged, append a single sentence
        # to the prose noting the cross-domain angle.
        if decision.secondary and decision.secondary != decision.primary:
            sec_label = {
                "pricing": "Pricing & Benchmarking",
                "anomaly": "Anomaly Detection",
                "demand":  "Demand Forecasting",
                "trade":   "Trade Area Intelligence",
            }[decision.secondary]
            hint = (
                f"\n\n_This question also has a {sec_label} angle — ask the "
                f"{sec_label} specialist directly for that view._"
            )
            spec_resp.prose = (spec_resp.prose or "") + hint

        return OrchestratorResponse(
            routing=decision,
            specialist=spec_resp,
            router_in_tokens=(router_tel.input_tokens   if router_tel else 0),
            router_out_tokens=(router_tel.output_tokens if router_tel else 0),
            router_cost_usd=(router_tel.cost_usd       if router_tel else 0.0),
            router_elapsed_sec=(router_tel.elapsed_sec if router_tel else 0.0),
        )
