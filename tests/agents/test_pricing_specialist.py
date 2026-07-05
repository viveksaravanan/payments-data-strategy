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
        "SELECT p.functional_category AS category, AVG(i.unit_price) AS own_avg_price "
        "FROM transaction_items i JOIN transactions t USING (txn_id) "
        "JOIN products p ON i.sku = p.sku "
        "WHERE t.banner_code = 'KRG' AND p.functional_category = 'Milk' "
        "GROUP BY p.functional_category"
    )
    # datamodel-v2 two-query flow: category/taxonomy resolve via the
    # products join (line items carry only sku). Own claim resolves
    # against the tenant frame (own_avg_price), peer against the lake
    # frame (peer_val). KRG own milk ASP ≈ $3.45; peer ≈ $3.66.
    emit = scripted_emit_response(
        headline="Your milk ASP is 3.45, below the peer 3.66.",
        evidence=[
            "Your milk average selling price is 3.45.",
            "The same-segment peer average is 3.66.",
        ],
        claims=[
            {
                "text_span": "3.45",
                "value": 3.45,
                "source": {
                    "type": "CellLookup",
                    "row_filter": {"category": "Milk"},
                    "column": "own_avg_price",
                    "agg": "mean",
                    "frame": "tenant",
                },
            },
            {
                "text_span": "peer 3.66",
                "value": 3.66,
                "source": {
                    "type": "CellLookup",
                    "row_filter": {"category": "Milk"},
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
        scripted_tool_use("query_lake_sql", {"sql": "SELECT category, AVG(unit_price) AS peer_val FROM lake_transactions WHERE peer_relationship = 'peer' AND category = 'Milk' GROUP BY category"}),
        emit,
    ]

    specialist = PricingSpecialist(viewer_krg)
    with patch_llm(monkeypatch, script):
        resp = specialist.answer("How does our milk pricing compare to peers?")

    assert isinstance(resp, AgentResponse)
    # Two-query flow: tenant frame is the result-of-record.
    assert "own_avg_price" in resp.result.columns
    # Charts are deferred to Wave 4 (§11.2) — gated off.
    assert resp.chart is None
    # Prose retained the grounded own milk-price claim. The validator
    # normalizes the scripted 3.45 to the true own_avg_price (scale-dependent:
    # ~3.45 at pilot, 3.4496 at full 155k), so assert the value READ FROM the
    # response result frame reaches the prose — robust at any data scale.
    _own = float(resp.result["own_avg_price"].iloc[0])
    assert f"{_own:.4f}" in resp.prose or f"{_own:.2f}" in resp.prose
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
        "SELECT segment, COUNT(*) AS n FROM transactions "
        "WHERE banner_code = 'KRG' GROUP BY segment"
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


# ---------------------------------------------------------------------
# Reasoning gates — the rendered prompt must carry them (prompt-contract)
# ---------------------------------------------------------------------

def test_pricing_prompt_carries_both_gates_and_query_shape(viewer_krg) -> None:
    """The pricing contract is prompt-borne: the rendered system prompt
    must carry Gate A (mix), Gate B (KVI/direction), the both-directions
    requirement, and the sortable own-vs-peer gap query shape (the self-tag
    architecture). Guards against a silent prompt regression."""
    prompt = PricingSpecialist(viewer_krg)._system_prompt
    low = prompt.lower()
    # Gate A — mix control via subcategory grain.
    assert "gate a" in low and "mix" in low and "assortment" in low
    # Gate B — KVI / traffic-driver.
    assert "gate b" in low
    assert "known-value" in low or "kvi" in low
    assert "traffic" in low          # traffic-driver framing
    # Both directions — ASC for a below question, DESC for an above one.
    assert "order by gap asc" in low
    assert "desc" in low
    assert "furthest below" in low and "above" in low
    # The sortable gap query: own via FILTER self, peer via FILTER peer, in
    # ONE lake query — ranked, not eyeballed across two frames.
    assert "filter (where peer_relationship = 'self')" in low
    assert "filter (where peer_relationship = 'peer')" in low
    assert "functional_subcategory" in low
    # emit is terminal, never a placeholder checkpoint.
    assert "placeholder" in low and "last action" in low
    # Elasticity honesty on sizing.
    assert "elasticity" in low


def test_pricing_prompt_injects_full_kvi_list(viewer_krg) -> None:
    """Every KVI subcategory from the single-source constant must appear
    verbatim in the rendered prompt — so the prompt and the (future)
    server-side classifier can never drift."""
    from src.agents import constants
    prompt = PricingSpecialist(viewer_krg)._system_prompt
    assert constants.KVI_SUBCATEGORIES_PROMPT in prompt
    for sub in constants.KVI_SUBCATEGORIES:
        assert sub in prompt, f"KVI subcategory {sub!r} missing from prompt"


# ---------------------------------------------------------------------
# Query shape — CASE-split + list row_filter grounds end-to-end
# ---------------------------------------------------------------------

def _built() -> bool:
    from src.lake.lake_sql import DATA_LAKE_ITEMS
    return (DATA_LAKE_ITEMS / "KRG" / "lake_transactions.parquet").exists()


needs_lake = pytest.mark.skipif(
    not _built(), reason="line-item lake not built (run `make lake-items`)"
)


@needs_lake
def test_pricing_subcategory_query_grounds_both_directions(
    viewer_krg, monkeypatch,
) -> None:
    """The taught query shape must actually execute and ground: one
    subcategory-grain query per side (category + subcategory), a KVI-aware
    emit that features BOTH directions (steak below, bananas at parity), a
    steak-share claim that totals beef via a category-only row_filter, and
    a pct_change gap that renders as a scaled percent (…% in the tens,
    never a sub-1% artifact from the pre-fix normalizer bug)."""
    import re

    own_sql = (
        "SELECT p.functional_category AS category, p.functional_subcategory AS subcategory, "
        "AVG(i.unit_price) AS own_asp, SUM(i.qty) AS own_units "
        "FROM transaction_items i JOIN transactions t ON i.txn_id = t.txn_id "
        "JOIN products p ON i.sku = p.sku WHERE t.banner_code = 'KRG' "
        "GROUP BY 1, 2 ORDER BY own_units DESC"
    )
    peer_sql = (
        "SELECT category, subcategory, AVG(unit_price) AS peer_asp, SUM(qty) AS peer_units "
        "FROM lake_transactions WHERE peer_relationship = 'peer' "
        "GROUP BY category, subcategory ORDER BY peer_units DESC"
    )
    B = {"category": "Beef", "subcategory": "Beef Steaks"}
    G = {"category": "Beef", "subcategory": "Ground Beef"}
    N = {"category": "Fresh Fruit", "subcategory": "Bananas & Everyday Fruit"}
    emit = scripted_emit_response(
        headline="Your beef gap versus peers is real and sits in steak, not a mix artifact.",
        evidence=[
            "Your beef steaks run 11.26 per unit versus the peer average of 15.39 — about 27% below peers.",
            "Ground beef is near parity at 5.45 versus 5.78, so the gap is a steak story.",
            "You are under-indexed on steak: 46% of your beef units are steak.",
            "On known-value items you're right at peers — bananas are 2.46 versus 2.44.",
        ],
        so_what=(
            "Beef steaks are worth testing a modest increase while watching units; "
            "leave ground beef and bananas, known-value items you keep matched to peers."
        ),
        claims=[
            {"text_span": "11.26 per unit", "value": 11.257,
             "source": {"type": "CellLookup", "row_filter": B,
                        "column": "own_asp", "agg": "mean", "frame": "tenant"}},
            {"text_span": "peer average of 15.39", "value": 15.386,
             "source": {"type": "CellLookup", "row_filter": B,
                        "column": "peer_asp", "agg": "mean", "frame": "lake"}},
            {"text_span": "about 27% below peers", "value": -0.268,
             "source": {"type": "Derivation", "op": "pct_change", "operands": [
                 {"type": "CellLookup", "row_filter": B,
                  "column": "own_asp", "agg": "mean", "frame": "tenant"},
                 {"type": "CellLookup", "row_filter": B,
                  "column": "peer_asp", "agg": "mean", "frame": "lake"}]}},
            {"text_span": "5.45 versus 5.78", "value": 5.454,
             "source": {"type": "CellLookup", "row_filter": G,
                        "column": "own_asp", "agg": "mean", "frame": "tenant"}},
            {"text_span": "46% of your beef units are steak", "value": 0.465,
             "source": {"type": "Derivation", "op": "ratio", "operands": [
                 {"type": "CellLookup", "row_filter": B,
                  "column": "own_units", "agg": "sum", "frame": "tenant"},
                 {"type": "CellLookup", "row_filter": {"category": "Beef"},
                  "column": "own_units", "agg": "sum", "frame": "tenant"}]}},
            {"text_span": "bananas are 2.46 versus 2.44", "value": 2.456,
             "source": {"type": "CellLookup", "row_filter": N,
                        "column": "own_asp", "agg": "mean", "frame": "tenant"}},
        ],
        caveats=[
            "Category ASP blends assortment; comparing at subcategory isolates price from mix.",
            "Realized gain depends on price elasticity, which this data doesn't measure.",
        ],
    )

    script = [
        scripted_tool_use("query_tenant", {"sql": own_sql}),
        scripted_tool_use("query_lake_sql", {"sql": peer_sql}),
        emit,
    ]
    specialist = PricingSpecialist(viewer_krg)
    with patch_llm(monkeypatch, script):
        resp = specialist.answer("Where should I reprice versus peers?")

    assert isinstance(resp, AgentResponse)
    prose = resp.prose
    # Both directions survived grounding: a beef (below) number and the
    # bananas (at-parity, above) number both reach the prose.
    assert re.search(r"11\.2\d", prose), "steak own price missing"
    assert re.search(r"2\.4\d", prose), "bananas own price missing (other-direction KVI dropped)"
    # The pct_change gap renders as a scaled percent in the tens — the
    # _format_normalized fix. Never a sub-1% artifact.
    assert re.search(r"\b2\d(\.\d+)?%", prose), f"expected a scaled ~20s% gap, got: {prose!r}"
    assert not re.search(r"\b0\.\d+%", prose), f"sub-1% normalizer artifact leaked: {prose!r}"
    # so_what carried the KVI-aware action.
    assert resp.so_what and "ground beef" in resp.so_what.lower()
    # Two-query flow, no chart.
    assert {s.surface for s in resp.sql} == {"tenant", "lake_sql"}
    assert resp.chart is None
