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
    # Phase 5.1.5: standardized to 8 across all specialists.
    # Phase 5.1.9: bumped to 10 to accommodate the analytical
    # reconciliation step chart-takeaway injection adds.
    MAX_TURNS = 6
    # Wave 3 Stage 6.5 Fix 12 — demand reads peer unit velocity.
    PREFERRED_PEER_METRIC = "units_index"
