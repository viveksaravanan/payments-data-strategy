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
    # Demand typically converges in 2-3 turns, but the `campaign_attr`
    # path needs multi-step promo lookups. 6 turns gives finalization
    # headroom without inviting exploration.
    MAX_TURNS = 6
