"""Conversational Advisor (D26.3) — the general-purpose fallback.

Routes here when no specialist fits (D26.4 — replaces v3's
force-routing-to-segment-default). Owns **payment-mix** questions
(tender / card network / entry mode / wallet) and general /
definitional / multi-topic asks. Same §1 structured contract and the
same four-tool self-tag surface as the specialists (``schema_info`` /
``query_tenant`` / ``query_lake_sql`` / ``emit_response``) — there is no
merge step and there are no aggregate tables (the Wave 2
``lake_payment_mix`` / ``lake_segment_mix`` / manifest were removed in
Wave 3.5 Stage E). What still distinguishes it:

* **Not domain-locked.** Answers across payments, catalog, geography —
  whatever a specialist didn't claim.
* **Payment mix on the line-item lake.** Own and peer payment shares
  come from one ``query_lake_sql`` over ``lake_transactions`` with a
  ``peer_relationship`` self/peer ``FILTER`` (own rows are present
  tagged ``self``); k=50 floor counts peer rows only.
* **Decline-gracefully owner.** No consumer linkage and no SKU in the
  peer lake, so peer behavioral-segmentation / cohort / SKU questions
  are declined with the nearest answerable grain offered.
* **Base-rate framing.** A multiplier is always paired with its base
  rate ("62% contactless, vs the 58% peer average — 4 points above").
* **MERGE_REQUIRED=False.** No own/peer merge step (there is none in
  Wave 3.5); own-vs-peer resolves per-frame in the single lake query.

The Advisor inherits the bounded tool loop, prompt rendering, and
finalization from ``Specialist``. The prompt is where its behavior
diverges.
"""
from __future__ import annotations

from pathlib import Path

from src.agents.specialist import Specialist


class ConversationalAdvisor(Specialist):
    AGENT_LABEL = "Conversational Advisor"
    PROMPT_PATH = Path(__file__).parent / "prompts" / "advisor.md"
    MAX_TURNS = 6
    MERGE_REQUIRED = False
    # Payment mix is the Advisor's signature peer comparison (entry-mode /
    # tender / network share vs same-segment peers).
    PREFERRED_PEER_METRIC = "payment_mix"
    PEER_ROUTING_KIND = "advisor"   # Wave 3.5 §6 — route to specialist rule, else cross-segment labeled
