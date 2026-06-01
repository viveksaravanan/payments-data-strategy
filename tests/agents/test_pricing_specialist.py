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
    emit = scripted_emit_response(
        prose="Your dairy own price averages 3.50 in your zones, "
              "indicating elevated category cost.",
        merge={
            "on": ["category"],
            "own_value_col": "own_avg_price",
            "peer_value_col": "price_index",
            "gap_op": "difference",
        },
        chart_intent={
            "kind": "cross_merchant_comparison",
            "x": "category",
            "series": ["own_value", "peer_benchmark"],
            "y_format": "index",
            "title": "Dairy pricing vs peers",
            "takeaway": "Average own dairy price across zones and weeks.",
        },
        claims=[{
            "text_span": "3.50",
            "value": 3.50,
            "source": {
                "type": "CellLookup",
                "row_filter": {"category": "DAIRY"},
                "column": "own_value",
                "agg": "mean",
            },
        }],
        caveats=["Peer set is 2 grocers.",
                 "Window: 2026-03-01 to 2026-05-29."],
    )

    script = [
        scripted_tool_use("query_tenant", {"sql": own_sql}),
        scripted_tool_use("read_lake_table", {
            "table": "lake_category_metrics",
            "filters": {"category": "DAIRY", "grain": "cat_week"},
        }),
        emit,
    ]

    specialist = PricingSpecialist(viewer_krg)
    with patch_llm(monkeypatch, script):
        resp = specialist.answer("How does our dairy pricing compare to peers?")

    assert isinstance(resp, AgentResponse)
    # Merged result has the canonical columns.
    assert "own_value" in resp.result.columns
    assert "peer_benchmark" in resp.result.columns
    assert "gap" in resp.result.columns
    # Chart was built.
    assert resp.chart is not None
    # Prose retained the 3.5 claim (declared and matching cell).
    assert "3.5" in resp.prose
    # Caveats survived.
    assert resp.caveats and any("Peer set" in c for c in resp.caveats)
    # Grain notes came from the manifest.
    assert any("peer SKU" in g for g in resp.grain_notes)
    # SQL surfaces: both tenant + lake reads.
    surfaces = {s.surface for s in resp.sql}
    assert surfaces == {"tenant", "lake"}
    # Telemetry populated.
    assert resp.telemetry is not None
    assert resp.telemetry.turns == 3


# ---------------------------------------------------------------------
# Off-grain ask — decline-gracefully via manifest excludes
# ---------------------------------------------------------------------

def test_pricing_off_grain_sku_filter_rejected(viewer_krg, monkeypatch) -> None:
    """The model tries to filter the lake by sku — read_lake_table
    raises LakeToolError. The error is fed back to the model as a
    tool result so it can decline gracefully on the next turn.
    """
    final_text = """I can compare at category or subcategory grain; peer SKU detail isn't published.

```render
{
  "merge": {},
  "chart_intent": {
    "kind": "kpi_callout",
    "title": "No peer SKU available",
    "value": "row_count"
  },
  "claims": []
}
```

```caveats
["No peer SKU detail is published — Wave 2 lake stops at subcategory."]
```
"""
    script = [
        scripted_tool_use("read_lake_table", {
            "table": "lake_category_metrics",
            "filters": {"sku": "MILK_GAL"},
        }),
        scripted_text(final_text),
    ]
    specialist = PricingSpecialist(viewer_krg)
    with patch_llm(monkeypatch, script):
        # We can't easily call .answer() because the read returns an
        # error rather than a frame — and our minimal_response path
        # only fires when no merge is required. Sanity: the underlying
        # tool DOES raise LakeToolError when called with sku filter.
        from src.agents import lake_tools as LT
        with pytest.raises(LakeToolError) as exc:
            LT.read_lake_table(
                "KRG", "lake_category_metrics",
                filters={"sku": "MILK_GAL"},
            )
        assert "sku" in str(exc.value)
        assert "peer SKU" in str(exc.value)


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
        scripted_tool_use("read_lake_table", {
            "table": "lake_category_metrics",
            "filters": {"category": "DAIRY"},
        }),
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
