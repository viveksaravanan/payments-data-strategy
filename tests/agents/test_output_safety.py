"""Phase 1 — output-safety layer.

The invariant: the only things that reach the user are a validated emit_response
payload or a canonical fallback — never raw model scratchpad. Plus direction/label
correctness, revenue→sales, and headline hygiene. All deterministic + network-free
(the label layer runs on strings; the leak test uses the fake LLM).
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.agents import label_review as LR
from src.agents import lake_tools as LT
from src.agents.advisor import ConversationalAdvisor
from src.agents.context import MerchantContext
from src.agents.lake_tools import LakeToolError
from src.agents.pricing import PricingSpecialist
from tests.agents._fake_llm import patch_llm, scripted_text


# ----- 1.1 narration never reaches the user ----------------------------------

def test_is_narration_flags_scratchpad_not_findings() -> None:
    for s in ["Let me compute the week-over-week changes now.",
              "Result was truncated — pulling the last two weeks.",
              "No anomalies found yet — checking store trends.",
              "Placeholder - retrying query",
              "Now pulling the peer data…"]:
        assert LT.is_narration(s), s
    for s in ["Kroger prices below same-segment peers in Beef.",
              "You're closed Sundays, so there's no Sunday business to compare.",
              "Set {kind: indicator} as a placeholder marker — not XML."]:
        assert not LT.is_narration(s), s


def test_emit_guard_rejects_narration_headline() -> None:
    """A narration headline in emit_response is rejected (→ model retries)."""
    spec = PricingSpecialist(MerchantContext.for_merchant("KRG"))
    spec._reset_state()
    spec._tenant_frame = pd.DataFrame({"category": ["Milk"], "own_asp": [3.45]})
    with pytest.raises(LakeToolError, match="narration"):
        spec._validate_emit_args(
            {"headline": "Let me pull the peer data now.", "claims": []})
    # a real finding is accepted by the guard
    spec._validate_emit_args(
        {"headline": "You price below peers in milk.", "claims": []})


def test_textstop_scratchpad_falls_back_not_leaked(monkeypatch) -> None:
    """If the model stops with a narration TEXT block instead of emit_response,
    the user gets the canonical fallback — never the scratchpad (Exit B)."""
    adv = ConversationalAdvisor(MerchantContext.for_merchant("KRG"))
    with patch_llm(monkeypatch, [scripted_text("Let me pull the peer payment data now…")]):
        resp = adv.answer("What's my payment mix versus peers?")
    assert LT.business_fallback() in resp.prose
    assert "Let me pull" not in resp.prose


def test_textstop_clean_answer_preserved(monkeypatch) -> None:
    """A clean text-stop answer is NOT clobbered by the sanitizer."""
    adv = ConversationalAdvisor(MerchantContext.for_merchant("KRG"))
    with patch_llm(monkeypatch, [scripted_text("Your customers split about evenly "
                                               "between credit and debit.")]):
        resp = adv.answer("What's my payment mix?")
    assert "credit and debit" in resp.prose
    assert LT.business_fallback() not in resp.prose


# ----- 1.2 direction / magnitude ---------------------------------------------

def _run(hl, ev, claims):
    h, e, s, c = LR.review_prose(hl, ev, None, claims)
    return h, e, [x["check"] for x in c]


def test_direction_arithmetic_form_flips_and_drops_qualifier() -> None:
    # "peer 247,000 ... below your own 211,000" — 247K is ABOVE 211K.
    _, ev, checks = _run(
        "Store comparison.",
        ["Peer stores logged 247,000, slightly below your own 211,000."], [])
    assert "above" in ev[0] and "below" not in ev[0]
    assert "slightly" not in ev[0]
    assert "direction" in checks and "magnitude" in checks


def test_direction_no_false_flip_when_correct() -> None:
    _, ev, checks = _run(
        "Pricing.",
        ["You charge $8.16 in Beef versus the peer average of $10.42, below peers."], [])
    assert "below" in ev[0] and "direction" not in checks


def test_direction_claim_indexed_multi_category_headline() -> None:
    from types import SimpleNamespace as NS
    def cl(v, k, f):
        return NS(value=v, source=NS(row_filter={"category": k}, frame=f), text_span="")
    claims = [cl(6.92, "Personal Care", "tenant"), cl(5.65, "Personal Care", "lake"),
              cl(4.1, "Salty Snacks", "tenant"), cl(3.6, "Salty Snacks", "lake")]
    h, _, checks = _run("Acme prices below peers in Personal Care and Salty Snacks.", [], claims)
    assert "above" in h and "direction" in checks


# ----- 1.3 revenue → sales ---------------------------------------------------

def test_revenue_relabeled_to_sales_in_all_fields() -> None:
    h, e, s, c = LR.review_prose(
        "Your Beef revenue leads.", ["Peer revenue was $1.2M."],
        "Grow revenue by pricing up.", [])
    assert "revenue" not in (h + " " + " ".join(e) + " " + s).lower()
    assert h.count("sales") and e[0].count("sales") and "sales" in s


def test_revenue_backstop_leaves_identifiers_alone() -> None:
    h, _, _, _ = LR.review_prose("Your own_revenue column is fine.", [], None, [])
    assert "own_revenue" in h


# ----- 1.4 headline hygiene --------------------------------------------------

def test_bare_list_headline_promotes_evidence() -> None:
    h, ev, checks = _run(
        "NoDa, Dilworth, Center City.",
        ["Your NoDa sales lead the metro at $2.1M.", "Dilworth follows."], [])
    assert h == "Your NoDa sales lead the metro at $2.1M." and "headline" in checks


def test_fragment_headline_repaired() -> None:
    h, _, checks = _run(
        "Winn-Dixie shows standout strength. while Dilworth lags.",
        ["Eastway is your densest catchment."], [])
    assert h == "Eastway is your densest catchment." and "headline" in checks


def test_good_headline_untouched() -> None:
    h, _, checks = _run("Matthews is your biggest missed opportunity.", ["x."], [])
    assert h == "Matthews is your biggest missed opportunity." and "headline" not in checks
