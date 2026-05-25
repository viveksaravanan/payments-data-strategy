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
    # Phase 5.1.5: standardized to 8 across all specialists per design
    # doc §10 (1 schema + 2 tenant + 2 lake + 1 chart + 2 buffer).
    MAX_TURNS = 8
