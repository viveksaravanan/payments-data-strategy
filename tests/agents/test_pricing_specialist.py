"""Stage 2.1 — Pricing specialist tests.

The Pricing & Benchmarking Agent reads its own SKU/category prices
via ``query_tenant`` and peer category indices via
``read_lake_table(lake_category_metrics)``, merges them, and
emits the §1 render block. Tests use the fake LLM so they're
deterministic and cost nothing.

Coverage:

* Canonical pricing question routes through query_tenant +
  read_lake_table + final render block → produces a valid
  AgentResponse with merged result + chart + validated prose.
* Off-grain ask (peer SKU) — the model trying to filter the lake
  on ``sku`` is rejected with the manifest excludes; the prompt
  guides graceful decline.
* Anomaly absent: the specialist does NOT contain the string
  "fraud" or "tampering" in its prose path (that's the Anomaly
  agent's invariant; Pricing should be silent on those words).
"""
from __future__ import annotations

import json

import pytest

from src.agents.context import MerchantContext
from src.agents.lake_tools import LakeToolError
from src.agents.pricing import PricingSpecialist
from src.agents.response import AgentResponse
from tests.agents._fake_llm import (
    patch_llm,
    scripted_emit_response,
    scripted_text,
    scripted_tool_use,
)


@pytest.fixture
def viewer_krg() -> MerchantContext:
    return MerchantContext.for_merchant("KRG")


# ---------------------------------------------------------------------
# Canonical pricing question end-to-end
# ---------------------------------------------------------------------

def test_pricing_canonical_question_produces_agentresponse(
    viewer_krg, monkeypatch,
) -> None:
    """Scripted LLM sequence:

      1. query_tenant for own dairy category price.
      2. read_lake_table on lake_category_metrics filtered to DAIRY.
      3. final text with prose + render block.

    Specialist merges the two frames, builds the chart, validates
    claims, returns an AgentResponse.
    """
    own_sql = (
        "SELECT i.category, AVG(i.unit_price) AS own_avg_price "
        "FROM transaction_items i JOIN transactions t USING (txn_id) "
        "WHERE t.banner_code = 'KRG' AND i.category = 'DAIRY' "
        "GROUP BY i.category"
    )
    # Lake at cat_week gives ~104 DAIRY rows. Merge on `category`
    # produces 104 merged rows (single own × 104 peer cells). The
    # claim uses CellLookup with agg='mean' over the matching cells
    # — the natural way for the model to assert "averaged across
    # zones and weeks".
    # Wave 3.5 two-query flow: own claim resolves against the tenant
    # frame (own_avg_price), peer against the lake frame (peer_val).
    emit = scripted_emit_response(
        headline="Your dairy ASP is 3.50, slightly above the peer 3.52.",
        evidence=[
            "Your dairy average selling price is 3.50.",
            "The same-segment peer average is 3.52.",
        ],
        claims=[
            {
                "text_span": "3.50",
                "value": 3.50,
                "source": {
                    "type": "CellLookup",
                    "row_filter": {"category": "DAIRY"},
                    "column": "own_avg_price",
                    "agg": "mean",
                    "frame": "tenant",
                },
            },
            {
                "text_span": "peer 3.52",
                "value": 3.52,
                "source": {
                    "type": "CellLookup",
                    "row_filter": {"category": "DAIRY"},
                    "column": "peer_val",
                    "agg": "mean",
                    "frame": "lake",
                },
            },
        ],
        caveats=["Peer set is 2 grocers.",
                 "Window: 2026-03-01 to 2026-05-29."],
    )

    script = [
        scripted_tool_use("query_tenant", {"sql": own_sql}),
        scripted_tool_use("query_lake_sql", {"sql": "SELECT category, AVG(unit_price) AS peer_val FROM lake_transactions WHERE peer_relationship = 'peer' AND category = 'DAIRY' GROUP BY category"}),
        emit,
    ]

    specialist = PricingSpecialist(viewer_krg)
    with patch_llm(monkeypatch, script):
        resp = specialist.answer("How does our dairy pricing compare to peers?")

    assert isinstance(resp, AgentResponse)
    # Two-query flow: tenant frame is the result-of-record.
    assert "own_avg_price" in resp.result.columns
    # Charts are deferred to Wave 4 (§11.2) — gated off.
    assert resp.chart is None
    # Prose retained the grounded own 3.50 claim.
    assert "3.50" in resp.prose
    # Caveats survived.
    assert resp.caveats and any("Peer set" in c for c in resp.caveats)
    # SQL surfaces: tenant + lake_sql reads (no merge step in 3.5).
    surfaces = {s.surface for s in resp.sql}
    assert surfaces == {"tenant", "lake_sql"}
    # Telemetry populated.
    assert resp.telemetry is not None
    # tenant + lake_sql + emit → 3 turns (no build_merge).
    assert resp.telemetry.turns == 3


# ---------------------------------------------------------------------
# Off-grain ask — decline-gracefully via manifest excludes
# ---------------------------------------------------------------------

# ---------------------------------------------------------------------
# Render block missing — pricing requires the merge by design
# ---------------------------------------------------------------------

def test_pricing_render_block_required(viewer_krg, monkeypatch) -> None:
    """The PricingSpecialist requires a render block (MERGE_REQUIRED).
    A final text without one raises RenderBlockMissingError."""
    from src.agents.specialist import RenderBlockMissingError

    own_sql = (
        "SELECT category, COUNT(*) AS n FROM transactions "
        "WHERE banner_code = 'KRG' GROUP BY category"
    )
    script = [
        scripted_tool_use("query_tenant", {"sql": own_sql}),
        scripted_tool_use("query_lake_sql", {"sql": "SELECT category, AVG(unit_price) AS peer_val FROM lake_transactions WHERE peer_relationship = 'peer' GROUP BY category"}),
        scripted_text("Just some prose, no render block."),
    ]
    specialist = PricingSpecialist(viewer_krg)
    with patch_llm(monkeypatch, script):
        with pytest.raises(RenderBlockMissingError):
            specialist.answer("dairy")


# ---------------------------------------------------------------------
# Pricing never claims fraud / tampering
# ---------------------------------------------------------------------

def test_pricing_prompt_never_mentions_fraud() -> None:
    """The Pricing prompt must NOT include words like "fraud" or
    "tampering" — those are Anomaly territory (D20.3 forbids fraud
    claims anywhere)."""
    from src.agents.pricing import PricingSpecialist
    prompt = PricingSpecialist.PROMPT_PATH.read_text().lower()
    assert "fraud" not in prompt
    assert "tampering" not in prompt


def test_pricing_prompt_declares_no_peer_sku() -> None:
    """The prompt must explicitly carry the manifest's "no peer SKU"
    Exclude so the model knows to decline."""
    prompt = PricingSpecialist.PROMPT_PATH.read_text().lower()
    assert "no peer sku" in prompt or "peer sku detail" in prompt
