"""Pricing & Benchmarking Agent — Wave 3 specialist.

Thin subclass of ``Specialist``. The base class drives the bounded
tool loop (``query_tenant`` + ``read_lake_table`` from
``src.agents.lake_tools``), parses the model's final render block,
merges own + peer frames, builds the chart deterministically, and
runs the §1.4 claims validator. The Pricing prompt at
``prompts/pricing.md`` provides the persona + grain discipline.

Surface: ``lake_category_metrics`` — `price_index`,
`promo_active_share`, `basket_penetration_share`. Excludes carried
into ``grain_notes``: no peer SKU, no peer store_id, no daily peer
grain (week is finest temporal).
"""
from __future__ import annotations

from pathlib import Path

from src.agents.specialist import Specialist


class PricingSpecialist(Specialist):
    AGENT_LABEL = "Pricing & Benchmarking Agent"
    PROMPT_PATH = Path(__file__).parent / "prompts" / "pricing.md"
    MAX_TURNS = 6   # Wave 3 Stage 6.5 follow-up #6 — converging pills finish in 3-5
    # Wave 3 Stage 6.5 Fix 12 — pricing reads peer prices.
    PREFERRED_PEER_METRIC = "price_index"
    PEER_ROUTING_KIND = "pricing"   # Wave 3.5 §6 — decline when 0 same-segment peers
