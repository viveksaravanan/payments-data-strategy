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
    # Phase 5.1.5: standardized to 8 across all specialists per design
    # doc §10 (1 schema + 2 tenant + 2 lake + 1 chart + 2 buffer).
    MAX_TURNS = 8
