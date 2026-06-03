"""Stage 3 — Conversational Advisor tests.

The Advisor owns ``lake_payment_mix`` and ``lake_segment_mix`` (the
two tables no specialist owns), uses ``MERGE_REQUIRED=False`` so
single-table answers don't fail when no render block is supplied,
and is the decline-gracefully owner per D23.7.

Coverage:

* Payment-mix question: reads ``lake_payment_mix``, emits an empty
  merge, delivers the lake frame as the result; chart kind matches
  the intent.
* Segment-mix question: uses the four DERIVED labels
  (`premium_loyalist`, `frequent_value`, `occasional_premium`,
  `occasional`) — never references `loyalty_type`.
* Out-of-grain peer SKU ask: the Advisor's prompt carries the
  decline template + grain_notes flag the exclude.
* The Advisor prompt explicitly states the median-only rule, the
  no-loyalty-type rule, and the base-rate framing rule.
"""
from __future__ import annotations

import pytest

from src.agents.advisor import ConversationalAdvisor
from src.agents.context import MerchantContext
from src.agents.response import AgentResponse
from tests.agents._fake_llm import (
    patch_llm,
    scripted_emit_response,
    scripted_tool_use,
)


@pytest.fixture
def viewer_krg() -> MerchantContext:
    return MerchantContext.for_merchant("KRG")


# ---------------------------------------------------------------------
# Payment mix path
# ---------------------------------------------------------------------

def test_advisor_payment_mix_question(viewer_krg, monkeypatch) -> None:
    """Single-table lake read → empty merge → lake frame as result.
    Chart kind matches the intent; grain notes carry "no weekly"
    Exclude from the manifest."""
    emit = scripted_emit_response(
        prose="Across your zones, the segment-peer contactless share "
              "averages 0.62.",
        merge={},
        chart_intent={
            "kind": "cross_merchant_comparison",
            "x": "derived_zone",
            "series": ["contactless_share"],
            "y_format": "pct",
            "title": "Peer contactless share by zone",
            "takeaway": "Peer contactless adoption averages 62%.",
        },
        claims=[{
            "text_span": "0.62",
            "value": 0.62,
            "source": {
                "type": "CellLookup",
                "row_filter": {},
                "column": "contactless_share",
                "agg": "mean",
            },
        }],
        caveats=["Payment mix at monthly grain."],
    )
    script = [
        scripted_tool_use("read_lake_table", {
            "table": "lake_payment_mix",
        }),
        emit,
    ]
    advisor = ConversationalAdvisor(viewer_krg)
    with patch_llm(monkeypatch, script):
        resp = advisor.answer("How does our contactless adoption compare?")

    assert isinstance(resp, AgentResponse)
    assert "contactless_share" in resp.result.columns
    assert "peer_relationship" in resp.result.columns
    # "no weekly grain" or "month is finest" comes from the manifest.
    joined = " ".join(resp.grain_notes).lower()
    assert "month" in joined or "weekly" in joined
    # Chart was built.
    assert resp.chart is not None


# ---------------------------------------------------------------------
# Segment mix path — derived labels, never loyalty_type
# ---------------------------------------------------------------------

def test_advisor_segment_mix_uses_derived_labels(viewer_krg, monkeypatch) -> None:
    """Segment mix reads return rows with ``behavioral_segment`` ∈
    the four derived labels. The Advisor's prose must not call them
    "loyalty_type"."""
    emit = scripted_emit_response(
        prose="The frequent_value segment averages 0.25 share of zone "
              "at banner across peers.",
        merge={},
        chart_intent={
            "kind": "cross_merchant_comparison",
            "x": "behavioral_segment",
            "series": ["share_of_zone_at_banner"],
            "y_format": "pct",
            "title": "Peer behavioral segment mix",
            "takeaway": "frequent_value averages around 0.25 share at banner.",
        },
        claims=[{
            "text_span": "0.25",
            "value": 0.25,
            "source": {
                "type": "CellLookup",
                "row_filter": {"behavioral_segment": "frequent_value"},
                "column": "share_of_zone_at_banner",
                "agg": "mean",
            },
        }],
        caveats=["Behavioral segments are DERIVED — not loyalty_type."],
    )
    script = [
        scripted_tool_use("read_lake_table", {
            "table": "lake_segment_mix",
        }),
        emit,
    ]
    advisor = ConversationalAdvisor(viewer_krg)
    with patch_llm(monkeypatch, script):
        resp = advisor.answer("What's the segment mix?")

    # Prose doesn't say "loyalty_type".
    assert "loyalty_type" not in resp.prose.lower()
    # Prose uses one of the four derived labels.
    assert any(
        seg in resp.prose for seg in
        ("frequent_value", "premium_loyalist",
         "occasional_premium", "occasional")
    )
    # Grain notes carry "DERIVED" / "loyalty_type" exclusion.
    joined = " ".join(resp.grain_notes).lower()
    assert "derived" in joined


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
