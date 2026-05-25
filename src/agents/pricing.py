"""Pricing & Benchmarking Agent — Phase 2A specialist.

Subclass of `Specialist` with the pricing persona and prompt. The tool
list is the default `TOOLS_SPECIALIST` (schema_info, query_tenant,
query_lake, make_chart). The model is Sonnet 4.6 per Phase 2 decision.
"""
from __future__ import annotations

from pathlib import Path

from src.agents.specialist import Specialist


class PricingSpecialist(Specialist):
    AGENT_LABEL = "Pricing & Benchmarking Agent"
    PROMPT_PATH = Path(__file__).parent / "prompts" / "pricing.md"
    # Phase 5.1.5: standardized to 8 across all specialists per design
    # doc §10 (1 schema + 2 tenant + 2 lake + 1 chart + 2 buffer).
    MAX_TURNS = 8
