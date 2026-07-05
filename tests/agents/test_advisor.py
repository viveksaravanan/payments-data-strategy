"""Conversational Advisor tests.

The Advisor owns **payment-mix** questions (tender / card network /
entry mode / wallet) on the Wave 3.5 line-item lake — there is no
``lake_payment_mix`` / ``lake_segment_mix`` aggregate table and no merge
step (both removed in Stage E). It uses ``MERGE_REQUIRED=False`` and is
the decline-gracefully owner (no consumer linkage / no SKU in the peer
lake).

Coverage (prompt-invariant string assertions — no live LLM here; live
behavior is exercised by the ground-truth validation harness):

* Self-tag structure: the prompt teaches the ``peer_relationship`` self /
  peer ``FILTER`` pattern and never claims own rows are absent.
* Merchant-vs-functional taxonomy discipline for category questions.
* Decline templates: peer SKU, cross-merchant cohorts, peer behavioral
  segmentation — all with the "no consumer linkage" boundary.
* Base-rate framing; no fraud vocabulary; ``MERGE_REQUIRED is False``.
"""
from __future__ import annotations

from src.agents.advisor import ConversationalAdvisor


# ---------------------------------------------------------------------
# Self-tag lake structure (guards against drift off the Wave 3.5 lake)
# ---------------------------------------------------------------------

def test_advisor_prompt_teaches_self_tag_filter_pattern() -> None:
    """The advisor must teach the own-vs-peer single-query FILTER pattern
    (own from ``peer_relationship='self'``, peer from ``'peer'``) so its
    base-rate payment comparisons are answerable — matching pricing/demand."""
    prompt = " ".join(ConversationalAdvisor.PROMPT_PATH.read_text().split())
    assert "peer_relationship = 'peer'" in prompt
    # Own rows are PRESENT tagged 'self' — the prompt must not claim they're absent.
    assert "own rows absent" not in prompt.lower()
    assert "peer_relationship = 'self'" in prompt
    # The single-query FILTER pattern that yields own AND peer in one lake query.
    assert "FILTER (WHERE peer_relationship = 'self')" in prompt
    assert "FILTER (WHERE peer_relationship = 'peer')" in prompt


def test_advisor_prompt_declares_taxonomy_rule() -> None:
    """Category questions must group OWN data on merchant_* and PEER
    comparisons on functional_* (the lake speaks functional only)."""
    prompt = ConversationalAdvisor.PROMPT_PATH.read_text()
    assert "merchant_" in prompt and "functional_" in prompt


# ---------------------------------------------------------------------
# Decline-gracefully — peer SKU
# ---------------------------------------------------------------------

def test_advisor_off_grain_sku_filter_rejected_with_decline_template() -> None:
    """The Advisor's prompt declares the peer-SKU decline template so
    the model can say it gracefully. The underlying lake_tools call
    still rejects the bad filter — this test confirms the prompt has
    the decline guidance verbatim."""
    # Normalize line breaks so split phrases match.
    prompt = " ".join(ConversationalAdvisor.PROMPT_PATH.read_text().split())
    # Wave 3.5 capability boundaries the advisor must decline gracefully:
    # peer SKU, cross-merchant cohorts/overlap, peer behavioral segments.
    assert "Peer SKU" in prompt and "not published" in prompt
    assert "Cross-merchant shopper cohorts" in prompt or "cohort" in prompt
    assert "no consumer linkage" in prompt


# ---------------------------------------------------------------------
# Prompt invariants
# ---------------------------------------------------------------------

def test_advisor_prompt_declares_base_rate_framing() -> None:
    """The Advisor MUST surface base rates next to multipliers per
    the D26.3 framing rule ("not naked multipliers")."""
    prompt = ConversationalAdvisor.PROMPT_PATH.read_text()
    assert "Base-rate framing" in prompt or "base rate" in prompt.lower()
    # The "3× store average" worked example.
    assert "3×" in prompt or "3x" in prompt or "store average" in prompt


def test_advisor_prompt_declines_peer_behavioral_segmentation() -> None:
    """Wave 3.5: the peer line-item lake has no consumer linkage, so peer
    behavioral segmentation (premium vs occasional shoppers) isn't
    available. The advisor must declare that boundary (and may still
    segment the viewer's OWN shoppers from tenant data)."""
    prompt = ConversationalAdvisor.PROMPT_PATH.read_text()
    assert "Behavioral segmentation of peers" in prompt
    assert "not available" in prompt


def test_advisor_prompt_never_mentions_fraud() -> None:
    """Even the general-purpose Advisor must stay away from fraud
    vocabulary (D20.3 — no signal in the panel)."""
    prompt = ConversationalAdvisor.PROMPT_PATH.read_text().lower()
    assert "fraud" not in prompt
    assert "tampering" not in prompt


def test_advisor_merge_required_is_false() -> None:
    """Many advisor questions are single-table — MERGE_REQUIRED must
    be False so an empty merge in the render block doesn't raise."""
    assert ConversationalAdvisor.MERGE_REQUIRED is False
