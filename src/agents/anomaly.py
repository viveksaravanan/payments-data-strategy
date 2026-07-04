"""Anomaly Detection Agent — Phase 2B specialist.

Operational anomalies only (no fraud). The prompt enumerates the three
planted anomalies in the panel so the LLM walks through them
consistently for demos. Same `Specialist` base class as Pricing.
"""
from __future__ import annotations

from pathlib import Path

from src.agents import lake_tools as LT
from src.agents.specialist import Specialist


class AnomalyDetectionSpecialist(Specialist):
    AGENT_LABEL = "Anomaly Detection Agent"
    PROMPT_PATH = Path(__file__).parent / "prompts" / "anomaly.md"
    # Anomaly gets the extra `top_movers` tool (Phase 4) so it never diffs a
    # truncated weekly pivot in-head.
    TOOLS = LT.TOOLS_ANOMALY
    # Base is 6; +2 for the drill-down (movers → flagged subcategory → emit).
    MAX_TURNS = 8
    # Wave 3 Stage 6.5 Fix 12 — anomalies key off week-over-week
    # divergence between own + peer.
    PREFERRED_PEER_METRIC = "wow_delta"
    PEER_ROUTING_KIND = "anomaly"   # Wave 3.5 §6 — cross-segment baseline labeled, or own-trend
