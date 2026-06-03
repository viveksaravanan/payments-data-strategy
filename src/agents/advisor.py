"""Wave 3 Stage 3 — Conversational Advisor (D26.3).

The general-purpose fallback. Routes here when no specialist fits
(D26.4 — replaces v3's force-routing-to-segment-default). Same §1
contract as the specialists; the differences are:

* **Not domain-locked.** Can reach any lake table — including the
  two no specialist owns: ``lake_payment_mix`` ("is my contactless
  share behind peers?") and ``lake_segment_mix`` ("what shoppers do
  I draw vs peers, behaviorally?").
* **Decline-gracefully owner.** Uses the manifest's ``Excludes`` to
  bound itself when a question doesn't have an answerable grain.
* **Base-rate framing.** When a metric reads as a multiplier
  ("3× store average"), the Advisor reports the base rate alongside
  ("attaches to 43% of pasta baskets, vs ~15% store average").
* **MERGE_REQUIRED=False.** Many advisor questions are single-table
  (payment mix, segment mix, or a tenant snapshot) and don't need an
  own/peer merge.

The Advisor inherits the bounded tool loop, prompt rendering, and
finalization from ``Specialist``. The prompt is the only place its
behavior diverges.
"""
from __future__ import annotations

from pathlib import Path

from src.agents.specialist import Specialist


class ConversationalAdvisor(Specialist):
    AGENT_LABEL = "Conversational Advisor"
    PROMPT_PATH = Path(__file__).parent / "prompts" / "advisor.md"
    MAX_TURNS = 6
    MERGE_REQUIRED = False
    PEER_ROUTING_KIND = "advisor"   # Wave 3.5 §6 — route to specialist rule, else cross-segment labeled
