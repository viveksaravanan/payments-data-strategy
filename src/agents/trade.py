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
    # Phase 5.1.5: standardized to 8 across all specialists per design
    # doc §10 (1 schema + 2 tenant + 2 lake + 1 chart + 2 buffer).
    # 6 was insufficient — Trade T1 bailed mid-contract on the Phase 5.1
    # browser smoke (headline emitted, evidence/therefore/caveats lost).
    MAX_TURNS = 8
