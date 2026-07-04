"""Demand Forecasting & Campaign Adjudication Agent — Phase 2C specialist.

Slow-mover analysis, lapsed-buyer cohort identification, projected
promo uplift, and campaign attribution. Folds in the flagship
slowing-ice-cream scenario. Same `Specialist` base class as Pricing.
"""
from __future__ import annotations

from pathlib import Path

from src.agents.specialist import Specialist


class DemandForecastingSpecialist(Specialist):
    AGENT_LABEL = "Demand Forecasting Agent"
    PROMPT_PATH = Path(__file__).parent / "prompts" / "demand.md"
    # Base is 6; +2 for the drill-down (top-line → subcategory → emit).
    MAX_TURNS = 8
    # Wave 3 Stage 6.5 Fix 12 — demand reads peer unit velocity.
    PREFERRED_PEER_METRIC = "units_index"
    PEER_ROUTING_KIND = "comparative"   # Wave 3.5 §6 — cross-segment fallback, labeled
