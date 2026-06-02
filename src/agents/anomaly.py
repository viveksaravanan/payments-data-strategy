"""Anomaly Detection Agent — Phase 2B specialist.

Operational anomalies only (no fraud). The prompt enumerates the three
planted anomalies in the panel so the LLM walks through them
consistently for demos. Same `Specialist` base class as Pricing.
"""
from __future__ import annotations

from pathlib import Path

from src.agents.specialist import Specialist


class AnomalyDetectionSpecialist(Specialist):
    AGENT_LABEL = "Anomaly Detection Agent"
    PROMPT_PATH = Path(__file__).parent / "prompts" / "anomaly.md"
    # Phase 5.1.5: standardized to 8 across all specialists.
    # Phase 5.1.9: bumped to 10 to accommodate the analytical
    # reconciliation step chart-takeaway injection adds.
    MAX_TURNS = 6
