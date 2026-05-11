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
    # Anomaly questions need extra headroom for multi-stage drilldowns
    # (per-stage tenant comparisons + peer context + finalization).
    MAX_TURNS = 7
