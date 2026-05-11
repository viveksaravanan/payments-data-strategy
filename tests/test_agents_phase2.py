"""Phase 2 agent tests.

Two layers:
  - **Always-on (no API key needed)** — import-checks, MerchantContext
    semantics, and audit helpers. These run in CI.
  - **LLM matrix (opt-in, requires ANTHROPIC_API_KEY)** — the
    privacy-critical 5-viewer × 4-pricing-question matrix from the
    Phase 2 plan §10. Marked `@pytest.mark.llm`; skipped by default.

Run the LLM matrix with `pytest -m llm`.
"""
from __future__ import annotations

import os
import re

import pandas as pd
import pytest

from src.agents.context import MerchantContext
from src.dashboard import placeholders as P

# Anthropic SDK is only required for the @llm-marked tests.
_HAS_KEY = bool(os.environ.get("ANTHROPIC_API_KEY"))

# Names that should never appear in any response unless the viewer is
# that merchant.
REAL_NAMES = ["Kroger", "Acme", "Winn-Dixie", "Taco Bell", "TJ Maxx"]

NAME_BY_ID = {
    "KRG": "Kroger", "ACM": "Acme", "WDX": "Winn-Dixie",
    "TBL": "Taco Bell", "TJX": "TJ Maxx",
}

PRICING_QUESTIONS_BY_SEGMENT = {
    "grocery":         ["dairy_vs_peers", "above_market", "below_market", "produce_share"],
    "qsr":             ["price_check_qsr", "above_market_qsr", "below_market_qsr", "category_share"],
    "off_price_retail": ["price_check_tjx", "above_market_tjx", "below_market_tjx", "category_share_tjx"],
}

SEGMENT_BY_ID = {
    "KRG": "grocery", "ACM": "grocery", "WDX": "grocery",
    "TBL": "qsr", "TJX": "off_price_retail",
}


# ---------------------------------------------------------------------------
# Always-on
# ---------------------------------------------------------------------------

def test_merchant_context_resolves_all_merchants():
    """MerchantContext.for_merchant() works for every panel merchant."""
    for mid, name in NAME_BY_ID.items():
        ctx = MerchantContext.for_merchant(mid)
        assert ctx.viewing_merchant_id == mid
        assert ctx.viewing_merchant_name == name
        assert ctx.viewing_merchant_segment == SEGMENT_BY_ID[mid]


def test_merchant_context_rejects_unknown_id():
    with pytest.raises(ValueError):
        MerchantContext.for_merchant("FOO")


def test_pricing_specialist_imports_cleanly():
    """The specialist module imports without requiring an API key."""
    from src.agents.pricing import PricingSpecialist
    assert PricingSpecialist.AGENT_LABEL == "Pricing & Benchmarking Agent"
    assert PricingSpecialist.PROMPT_PATH.exists()


def test_phase2bc_specialists_import_and_have_prompts():
    """Phase 2B/C: anomaly + demand + trade specialists are wired."""
    from src.agents.anomaly import AnomalyDetectionSpecialist
    from src.agents.demand  import DemandForecastingSpecialist
    from src.agents.trade   import TradeAreaSpecialist
    for cls, label in [
        (AnomalyDetectionSpecialist,   "Anomaly Detection Agent"),
        (DemandForecastingSpecialist,  "Demand Forecasting Agent"),
        (TradeAreaSpecialist,          "Trade Area Intelligence Agent"),
    ]:
        assert cls.AGENT_LABEL == label
        assert cls.PROMPT_PATH.exists()


def test_no_peer_rule_present_in_every_specialist_prompt():
    """Every specialist prompt must carry the exact no-peer phrasing
    so TJX (no same-segment peers) doesn't hit MAX_TURNS retrying."""
    from src.agents.anomaly import AnomalyDetectionSpecialist
    from src.agents.demand  import DemandForecastingSpecialist
    from src.agents.pricing import PricingSpecialist
    from src.agents.trade   import TradeAreaSpecialist
    expected = "No segment peers available for this response."
    for cls in (PricingSpecialist, AnomalyDetectionSpecialist,
                DemandForecastingSpecialist, TradeAreaSpecialist):
        body = cls.PROMPT_PATH.read_text()
        assert expected in body, f"missing no-peer phrasing in {cls.__name__}"


def test_orchestrator_keyword_fallback_routes_correctly():
    """When the LLM router is unavailable, keyword fallback should
    route to the right specialist for each domain."""
    from src.agents.orchestrator import _keyword_route
    cases = [
        ("How am I priced on dairy vs peers?",        "pricing"),
        ("Anything unusual recently?",                "anomaly"),
        ("Why are my University City stores declining?", "anomaly"),
        ("What slow-mover SKUs should I clear?",      "demand"),
        ("Where should I open a new store?",          "trade"),
    ]
    for q, expected in cases:
        d = _keyword_route(q)
        assert d.primary == expected, f"q={q!r}: got {d.primary}, want {expected}"
        assert d.via_fallback is True


def test_orchestrator_router_output_parser_rejects_garbage():
    """The router output parser should reject non-JSON and invalid
    specialist names rather than silently dispatching to nowhere."""
    from src.agents.orchestrator import _parse_router_output
    assert _parse_router_output("definitely not JSON") is None
    assert _parse_router_output('{"primary": "WAT"}') is None
    assert _parse_router_output('{"primary": "pricing"}') is not None


def test_orchestrator_prompt_lists_all_four_specialists():
    """The router prompt must reference all four specialist ids so the
    LLM has a complete option set."""
    from src.agents.orchestrator import PROMPT_PATH
    body = PROMPT_PATH.read_text()
    for spec in ("pricing", "anomaly", "demand", "trade"):
        assert f"`{spec}`" in body, f"missing specialist {spec!r} in router prompt"


def test_llm_module_is_available_check():
    """`llm.is_available()` reflects env state; doesn't raise."""
    from src.agents import llm
    assert llm.is_available() == bool(os.environ.get("ANTHROPIC_API_KEY"))


def test_dispatch_falls_back_to_hardcoded_when_llm_unavailable(monkeypatch):
    """With ANTHROPIC_API_KEY unset, the dispatch path returns a
    hardcoded response with NO fallback label (mock mode is silent)."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # Force is_available to recompute by reimporting? Just monkeypatch.
    from src.agents import llm
    monkeypatch.setattr(llm, "is_available", lambda: False)
    resp = P.dispatch("pricing", "dairy_vs_peers", "KRG")
    assert "agent" in resp and "prose" in resp
    # No fallback flag because LLM was never attempted
    assert not resp.get("fallback", False)


def test_label_fallback_prepends_clear_failure_label():
    """Smoke-check the labeled-fallback helper."""
    raw = {"agent": "X", "prose": "hardcoded prose", "table": None, "chart": None}
    labeled = P._label_fallback(raw, "TimeoutError: 60s exceeded")
    assert labeled["fallback"] is True
    assert "LLM call failed" in labeled["prose"]
    assert "hardcoded prose" in labeled["prose"]
    # Hardcoded prose still intact — only prefixed
    assert labeled["prose"].endswith("hardcoded prose")


# ---------------------------------------------------------------------------
# Audit helpers (used by both always-on + llm-marked tests)
# ---------------------------------------------------------------------------

def _name_leak_audit(response: dict, viewing_merchant_id: str) -> list[str]:
    """Return the list of real merchant names found in the response
    that don't belong to the viewer. Empty list = no leak."""
    own_name = NAME_BY_ID[viewing_merchant_id]
    haystack = _response_text(response)
    leaked = []
    for name in REAL_NAMES:
        if name == own_name:
            continue
        if re.search(rf"\b{re.escape(name)}\b", haystack):
            leaked.append(name)
    return leaked


def _response_text(response: dict) -> str:
    """Concatenate every textual surface of a response (prose, table,
    caveats) for name-leak / sql audit scanning."""
    parts = [response.get("prose") or ""]
    parts.extend(response.get("caveats") or [])
    tbl = response.get("table")
    if isinstance(tbl, pd.DataFrame) and not tbl.empty:
        parts.append(tbl.to_csv(index=False))
    return "\n".join(parts)


def _sql_audit(response: dict, viewing_merchant_id: str) -> dict:
    """Return audit findings on the SQL log. Tenant queries must
    contain WHERE merchant_id = '<viewer>'. Lake queries must not
    contain a tenant_* reference."""
    findings: dict = {"tenant_predicate_missing": [], "lake_tenant_leak": []}
    for entry in response.get("sql", []):
        q = entry.get("query", "")
        if entry.get("tool") == "tenant":
            pat = rf"merchant_id\s*=\s*['\"]{re.escape(viewing_merchant_id)}['\"]"
            if not re.search(pat, q, re.IGNORECASE):
                findings["tenant_predicate_missing"].append(q)
        elif entry.get("tool") == "lake":
            if re.search(r"\btenant_\w+\b", q, re.IGNORECASE):
                findings["lake_tenant_leak"].append(q)
    return findings


# ---------------------------------------------------------------------------
# LLM matrix — opt-in
# ---------------------------------------------------------------------------

PRICING_MATRIX = [
    (mid, qid)
    for mid in NAME_BY_ID
    for qid in PRICING_QUESTIONS_BY_SEGMENT[SEGMENT_BY_ID[mid]]
]


@pytest.mark.llm
@pytest.mark.skipif(not _HAS_KEY, reason="ANTHROPIC_API_KEY not set")
@pytest.mark.parametrize(("merchant_id", "question_id"), PRICING_MATRIX)
def test_pricing_matrix_no_name_leak_and_sql_scoped(merchant_id, question_id):
    """The full Phase 2A peer-isolation matrix: 5 viewers × 4 pricing
    questions = 20 LLM calls. For each: no peer real-name leak; tenant
    SQL scoped to viewer; lake SQL doesn't touch tenant_*."""
    resp = P.dispatch("pricing", question_id, merchant_id)
    assert resp.get("agent"), "response missing agent label"
    assert resp.get("prose"), "response missing prose"

    leaks = _name_leak_audit(resp, merchant_id)
    assert not leaks, (
        f"viewer={merchant_id} q={question_id}: real merchant names leaked: {leaks}"
    )

    findings = _sql_audit(resp, merchant_id)
    assert not findings["tenant_predicate_missing"], (
        f"viewer={merchant_id} q={question_id}: tenant queries missing predicate: "
        f"{findings['tenant_predicate_missing']}"
    )
    assert not findings["lake_tenant_leak"], (
        f"viewer={merchant_id} q={question_id}: lake queries reference tenant_*: "
        f"{findings['lake_tenant_leak']}"
    )


@pytest.mark.llm
@pytest.mark.skipif(not _HAS_KEY, reason="ANTHROPIC_API_KEY not set")
def test_cross_viewer_correctness_different_peer_mappings():
    """KRG's peer_a is Acme; ACM's peer_a is Kroger. Asking the same
    pricing question for two viewers should yield merchant-specific
    'own' values and merchant-specific peer mappings.

    This test confirms the merchant-context isolation at the response
    level. The SQL audit (in the matrix above) confirms it at the
    query level.
    """
    krg = P.dispatch("pricing", "dairy_vs_peers", "KRG")
    acm = P.dispatch("pricing", "dairy_vs_peers", "ACM")

    # Both succeed
    assert krg.get("prose") and acm.get("prose")

    # Neither response should leak the other's real name
    assert not _name_leak_audit(krg, "KRG"), "KRG response leaks peer names"
    assert not _name_leak_audit(acm, "ACM"), "ACM response leaks peer names"

    # The two responses should NOT be byte-identical — even if both
    # mention "peer_a", the underlying numbers should differ because
    # peer_a means Acme for KRG and Kroger for ACM.
    assert krg["prose"] != acm["prose"], (
        "KRG and ACM produced identical prose — peer mapping isn't differentiating viewers"
    )
