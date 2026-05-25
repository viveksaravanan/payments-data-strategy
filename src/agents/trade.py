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
    # Phase 5.1.5: standardized to 8 across all specialists (6 was
    # insufficient — Trade T1 bailed mid-contract during the 5.1 smoke).
    # Phase 5.1.9: bumped to 10 to accommodate the analytical
    # reconciliation step chart-takeaway injection adds.
    MAX_TURNS = 10
