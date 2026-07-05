"""Demand & Assortment Agent — Phase 2C specialist.

An own-data query/pattern agent over transaction history: demand **timing**
(day-of-week + daypart curves), **velocity** (fastest/slowest movers → reorder
vs. mark-down), and **affinity** (co-purchase attach rates → bundle /
cross-merchandise). NOT a statistical forecaster — no projections, intervals, or
external signals (weather/holidays/events) exist in this synthetic data. Can also
compare unit velocity to peers via the lake when explicitly asked. Same
`Specialist` base class as Pricing. (Class name kept for import stability.)
"""
from __future__ import annotations

from pathlib import Path

from src.agents.specialist import Specialist


class DemandForecastingSpecialist(Specialist):
    AGENT_LABEL = "Demand & Assortment Agent"
    PROMPT_PATH = Path(__file__).parent / "prompts" / "demand.md"
    # Base is 6; +2 for the two-axis timing / both-ends velocity queries.
    MAX_TURNS = 8
    # Wave 3 Stage 6.5 Fix 12 — demand reads peer unit velocity (secondary flow).
    PREFERRED_PEER_METRIC = "units_index"
    PEER_ROUTING_KIND = "comparative"   # Wave 3.5 §6 — cross-segment fallback, labeled
