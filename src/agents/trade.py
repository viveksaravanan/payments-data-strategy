"""Trade Area Intelligence Agent — Phase 2C specialist.

Store catchment, peer neighborhood density, underserved markets,
per-store performance variance, new-store siting. Same `Specialist`
base class as Pricing.
"""
from __future__ import annotations

from pathlib import Path

from src.agents.specialist import Specialist


class TradeAreaSpecialist(Specialist):
    AGENT_LABEL = "Trade Area Intelligence Agent"
    PROMPT_PATH = Path(__file__).parent / "prompts" / "trade.md"
    # Trade questions often need own-store + lake density + neighborhood
    # join + finalize = 4-5 tool calls. The 2D.1 cap of 5 starved the
    # finalization turn (7/80 partials); reverted to 6.
    MAX_TURNS = 6
