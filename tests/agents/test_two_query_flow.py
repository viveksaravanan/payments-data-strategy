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
from src.agents.demand import DemandForecastingSpecialist
from src.agents.pricing import PricingSpecialist
from src.lake.lake_sql import DATA_LAKE_ITEMS
from src.lake.observable_guard import DATA_RAW


def _built() -> bool:
    return (DATA_LAKE_ITEMS / "KRG" / "lake_transactions.parquet").exists()


needs_lake = pytest.mark.skipif(
    not _built(), reason="line-item lake not built (run `make lake-items`)"
)

needs_raw = pytest.mark.skipif(
    not (DATA_RAW / "transactions.parquet").exists(),
    reason="data/raw not built (run `make seed`)",
)


# ----- §6 routing directive renders into the prompt ---------------------

def test_pricing_prompt_renders_peer_routing_directive() -> None:
    krg = PricingSpecialist(MerchantContext.for_merchant("KRG"))
    tbl = PricingSpecialist(MerchantContext.for_merchant("TBL"))
    assert "{{peer_routing}}" not in krg._system_prompt
    # datamodel-v2: BOTH segments now have 2 same-segment peers
    # (grocery KRG/ACM/WDX; QSR TBL/BKG/CFA) — the peer-comparison
    # directive renders for both, no 0-peer decline.
    assert "2 same-segment peers" in krg._system_prompt
    assert "2 same-segment peers" in tbl._system_prompt


# ----- the two-query flow produces a clean dual-frame response ----------

@needs_lake
def test_two_query_flow_clean_response() -> None:
    spec = PricingSpecialist(MerchantContext.for_merchant("KRG"))
    spec._reset_state()

    # 1. own milk ASP (category resolves via the products join)
    spec._dispatch_tool(
        "query_tenant",
        {"sql": "SELECT p.functional_category AS category, AVG(i.unit_price) AS own_asp "
                "FROM transaction_items i JOIN transactions t ON i.txn_id = t.txn_id "
                "JOIN products p ON i.sku = p.sku "
                "WHERE t.banner_code = 'KRG' AND p.functional_category = 'Milk' "
                "GROUP BY p.functional_category"},
    )
    # 2. peer milk ASP
    spec._dispatch_tool(
        "query_lake_sql",
        {"sql": "SELECT category, AVG(unit_price) AS peer_asp FROM lake_transactions "
                "WHERE peer_relationship = 'peer' AND category = 'Milk' GROUP BY category"},
    )

    # Exact (unrounded) cell values → the claim value matches the
    # recomputed mean exactly, so the validator passes it without
    # normalizing the 2-dp display span.
    own_exact = float(spec._tenant_frame["own_asp"].iloc[0])
    peer_exact = float(spec._lake_frame["peer_asp"].iloc[0])
    own_asp = round(own_exact, 2)
    peer_asp = round(peer_exact, 2)

    # 3. emit — must NOT raise (no merge gate in the two-query flow),
    #    claims resolve per-frame.
    out = spec._dispatch_tool(
        "emit_response",
        {
            "headline": f"Your milk ASP is ${own_asp} vs a same-segment peer "
                     f"average of ${peer_asp}.",
            "claims": [
                {"text_span": f"${own_asp}", "value": own_exact,
                 "source": {"type": "CellLookup", "row_filter": {"category": "Milk"},
                            "column": "own_asp", "agg": "mean", "frame": "tenant"}},
                {"text_span": f"${peer_asp}", "value": peer_exact,
                 "source": {"type": "CellLookup", "row_filter": {"category": "Milk"},
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


# ----- Bug B: a second query_tenant must not hide the first's claims -----

@needs_raw
def test_second_tenant_query_does_not_strip_first_frame_claim() -> None:
    """Velocity runs a fast (DESC) query then a slow (ASC) query. The second
    used to overwrite the singular `_tenant_frame`, so the (correct) fast-mover
    claim resolved against the slow frame → matched 0 rows → stripped. Claims
    now resolve against the UNION of captured tenant frames, so the fast claim
    survives."""
    spec = DemandForecastingSpecialist(MerchantContext.for_merchant("KRG"))
    spec._reset_state()

    vel = ("SELECT p.merchant_category AS category, SUM(i.qty) AS units "
           "FROM transaction_items i JOIN transactions t ON i.txn_id = t.txn_id "
           "JOIN products p ON i.sku = p.sku WHERE t.banner_code = 'KRG' GROUP BY 1 ")
    # 1. FAST end — full ranking DESC; capture the top mover.
    spec._dispatch_tool("query_tenant", {"sql": vel + "ORDER BY units DESC"})
    fast_cat = str(spec._tenant_frame["category"].iloc[0])
    fast_units = float(spec._tenant_frame["units"].iloc[0])
    # 2. SLOW end — bottom 3 ASC; overwrites the singular frame.
    spec._dispatch_tool("query_tenant", {"sql": vel + "ORDER BY units ASC LIMIT 3"})
    # Precondition for the regression: the fast category is gone from the
    # last (singular) frame — so without the union fix the claim would strip.
    assert fast_cat not in set(spec._tenant_frame["category"])

    out = spec._dispatch_tool("emit_response", {
        "headline": f"{fast_cat} is your fastest mover at about {int(fast_units)} units.",
        "claims": [{
            "text_span": f"about {int(fast_units)} units", "value": fast_units,
            "source": {"type": "CellLookup", "row_filter": {"category": fast_cat},
                       "column": "units", "agg": "sum", "frame": "tenant"},
        }],
    })
    assert out == {"ok": True}

    resp = spec._finalize_from_emit(converged=True, turns=3)
    # The fast-mover units survived — the earlier frame's cell resolved.
    assert f"about {int(fast_units)} units" in resp.prose
