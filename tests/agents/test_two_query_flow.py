"""Wave 3.5 Stage C — the two-query flow end-to-end (no merge, no chart).

Drives a real specialist's dispatch + finalize over the live tenant +
line-item-lake surfaces: query_tenant (own) → query_lake_sql (peer) →
emit_response (prose + per-frame claims). Asserts the flow produces a
clean AgentResponse with NO merge/chart-skipped caveats, peer claims
resolving against the `"lake"` frame, and the §6 routing directive
rendered into the prompt. Skipped when the lake isn't built.
"""
from __future__ import annotations

import pytest

from src.agents.context import MerchantContext
from src.agents.pricing import PricingSpecialist
from src.lake.lake_sql import DATA_LAKE_ITEMS


def _built() -> bool:
    return (DATA_LAKE_ITEMS / "KRG" / "lake_transactions.parquet").exists()


needs_lake = pytest.mark.skipif(
    not _built(), reason="line-item lake not built (run `make lake-items`)"
)


# ----- §6 routing directive renders into the prompt ---------------------

def test_pricing_prompt_renders_peer_routing_directive() -> None:
    krg = PricingSpecialist(MerchantContext.for_merchant("KRG"))
    tbl = PricingSpecialist(MerchantContext.for_merchant("TBL"))
    assert "{{peer_routing}}" not in krg._system_prompt
    # Grocer (2 peers): a normal peer-comparison directive.
    assert "2 same-segment peers" in krg._system_prompt
    # QSR (0 peers): pricing declines, no cross-segment substitution.
    assert "no comparable same-segment peers" in tbl._system_prompt
    assert "not allowed" in tbl._system_prompt


# ----- the two-query flow produces a clean dual-frame response ----------

@needs_lake
def test_two_query_flow_clean_response() -> None:
    spec = PricingSpecialist(MerchantContext.for_merchant("KRG"))
    spec._reset_state()

    # 1. own dairy ASP
    spec._dispatch_tool(
        "query_tenant",
        {"sql": "SELECT i.category, AVG(i.unit_price) AS own_asp "
                "FROM transaction_items i JOIN transactions t ON i.txn_id = t.txn_id "
                "WHERE t.banner_code = 'KRG' AND i.category = 'DAIRY' GROUP BY i.category"},
    )
    # 2. peer dairy ASP
    spec._dispatch_tool(
        "query_lake_sql",
        {"sql": "SELECT category, AVG(unit_price) AS peer_asp FROM lake_transactions "
                "WHERE peer_relationship = 'peer' AND category = 'DAIRY' GROUP BY category"},
    )
    assert spec._lake_from_sql is True

    own_asp = round(float(spec._tenant_frame["own_asp"].iloc[0]), 2)
    peer_asp = round(float(spec._lake_frame["peer_asp"].iloc[0]), 2)

    # 3. emit — must NOT raise (no merge gate in the two-query flow),
    #    claims resolve per-frame.
    out = spec._dispatch_tool(
        "emit_response",
        {
            "headline": f"Your dairy ASP is ${own_asp} vs a same-segment peer "
                     f"average of ${peer_asp}.",
            "claims": [
                {"text_span": f"${own_asp}", "value": own_asp,
                 "source": {"type": "CellLookup", "row_filter": {"category": "DAIRY"},
                            "column": "own_asp", "agg": "mean", "frame": "tenant"}},
                {"text_span": f"${peer_asp}", "value": peer_asp,
                 "source": {"type": "CellLookup", "row_filter": {"category": "DAIRY"},
                            "column": "peer_asp", "agg": "mean", "frame": "lake"}},
            ],
            # No chart_intent — charts are deferred to Wave 4.
        },
    )
    assert out == {"ok": True}

    resp = spec._finalize_from_emit(converged=True, turns=3)

    # Both numbers survived → both claims resolved against their frames.
    assert f"${own_asp}" in resp.prose
    assert f"${peer_asp}" in resp.prose
    # No merge ran, no chart authored → none of those caveats fire.
    joined = " ".join(resp.caveats)
    assert "Merge spec was incomplete" not in joined
    assert "Chart skipped" not in joined
    assert resp.chart is None
    # Both surfaces logged.
    assert {s.surface for s in resp.sql} == {"tenant", "lake_sql"}
