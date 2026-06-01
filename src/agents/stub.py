"""Wave 3 Stage 1d — stub agent that exercises the §1 contract end-to-end.

NOT a real specialist. The stub returns a hand-built ``AgentResponse``
constructed from a fixture dairy result; its purpose is to demonstrate
the §1 keystone — AgentResponse + merge + chart builder + claims
validator — working on all five disposition tiers without involving
the LLM. Used by:

* The pause-and-report demo at Checkpoint 1 (the human-review surface
  before Stage 2 builds real specialists on top).
* The Stage 6.5 preview harness's "contract works without an LLM"
  smoke test.

The stub embeds five kinds of numeric content in its prose:

1. A clean declared claim → passes verbatim.
2. A near-tolerance declared claim → normalized to true value.
3. A fabricated declared claim (3.00 vs true 1.062) → clause
   stripped.
4. A fabricated undeclared metric numeric (50%) → caught by Pass B,
   clause stripped.
5. A structural integer ("5 stores in Zone 5") → survives (the
   metric/structural scanner distinction).

Plus a chart built from the same merged result so the chart-vs-prose
single-source-of-truth guarantee is visible too.
"""
from __future__ import annotations

import pandas as pd

from src.agents.chart_build import build_chart
from src.agents.claims import (
    CellLookup,
    Claim,
    ValidationReport,
    validate_claims,
)
from src.agents.response import (
    AgentResponse,
    SqlSurface,
    Telemetry,
    merge_own_and_peer,
)


def _fixture_own_dairy() -> pd.DataFrame:
    """Synthetic own-tenant dairy frame for the stub. KRG's category-
    level dairy spend in Zone 5 over the demo window."""
    return pd.DataFrame([
        {"banner_code": "KRG", "category": "DAIRY", "derived_zone": "Z05",
         "period_start": "2026-04-05", "own_avg_price": 1.062},
    ])


def _fixture_peer_dairy() -> pd.DataFrame:
    """Synthetic lake-side peer dairy frame, already scope_for_viewer'd
    (no banner_code; carries peer_relationship instead)."""
    return pd.DataFrame([
        {"category": "DAIRY", "derived_zone": "Z05",
         "period_start": "2026-04-05",
         "peer_relationship": "segment_peer",
         "price_index": 1.00, "txn_count": 600},
    ])


def _stub_prose() -> str:
    """The hand-crafted prose carrying all five disposition tiers."""
    return (
        # 1. Clean declared claim — exact match to true cell.
        "Your dairy price index in Zone 5 is 1.062, just above peers. "
        # 2. Within tolerance — claim says ≈1.06, true is 1.062.
        "Across the 12 weeks of the window, the gap holds at ≈1.06. "
        # 3. Fabricated DECLARED claim — declared 3.00, true is 1.062.
        "Your produce index is 3.00, dramatically above peers. "
        # 4. Fabricated UNDECLARED metric — 50% has no backing claim.
        "Promo penetration is 50%, far above the metro average. "
        # 5. Structural integer — "5 stores" must survive.
        "Your 5 stores in Zone 5 carry these dynamics."
    )


def _stub_claims() -> list[Claim]:
    """Two declared claims: the clean one (1.062) and the near-tolerance
    one (≈1.06). The fabricated 3.00 is ALSO declared (so it's
    caught by Pass A's tolerance check); the fabricated 50% is NOT
    declared (so it's caught by Pass B's scan)."""
    return [
        Claim(
            text_span="1.062",
            value=1.062,
            source=CellLookup(
                {"category": "DAIRY", "derived_zone": "Z05"},
                "own_value",
            ),
        ),
        Claim(
            text_span="≈1.06",
            value=1.06,
            source=CellLookup(
                {"category": "DAIRY", "derived_zone": "Z05"},
                "own_value",
            ),
        ),
        Claim(
            text_span="3.00",
            value=3.00,
            source=CellLookup(
                {"category": "DAIRY", "derived_zone": "Z05"},
                "own_value",
            ),
        ),
    ]


def run_stub() -> tuple[AgentResponse, ValidationReport]:
    """Build the AgentResponse + run the validator on it. Returns both
    so the demo can show the pre-validation prose + the per-claim
    disposition + the cleaned prose side-by-side."""
    own = _fixture_own_dairy()
    peer = _fixture_peer_dairy()

    result = merge_own_and_peer(
        own, peer,
        on=["category", "derived_zone", "period_start"],
        own_value_col="own_avg_price",
        peer_value_col="price_index",
        viewer="KRG",
    )

    chart_intent = {
        "kind": "cross_merchant_comparison",
        "title": "Dairy price index — Zone 5",
        "takeaway": "You sit just above peers in dairy.",
        "x": "category",
        "series": ["own_value", "peer_benchmark"],
        "y_format": "index",
    }
    chart = build_chart(chart_intent, result)

    prose = _stub_prose()
    claims = _stub_claims()
    report = validate_claims(prose, claims, result)

    response = AgentResponse(
        result=result,
        chart_intent=chart_intent,
        chart=chart,
        prose=report.prose,
        claims=claims,
        caveats=["Demo fixture, not real Wave 1/2 data."],
        sql=[
            SqlSurface(surface="tenant", query="<stub own SELECT>",
                       row_count=len(own)),
            SqlSurface(surface="lake",
                       query="<stub lake read of category_metrics>",
                       row_count=len(peer)),
        ],
        grain_notes=[
            "no peer SKU (own side reaches SKU via the tenant surface)",
            "no daily grain (week is finest temporal)",
        ],
        telemetry=Telemetry(
            model="stub", input_tokens=0, output_tokens=0, cost_usd=0.0,
            turns=0, converged=True,
        ),
    )
    return response, report


def format_demo_report(
    response: AgentResponse, report: ValidationReport,
) -> str:
    """Human-readable summary of the contract working — used by the
    pause-and-report checkpoint to surface what happened.

    Surfaces, for each of the five embedded numerics:
    * The original prose span.
    * Whether it traced (pass / normalized / stripped).
    * The cleaned prose (with the fabricated clauses excised).
    * The chart shape (kind + columns it pulled from result).
    """
    lines: list[str] = []
    lines.append("=" * 70)
    lines.append("Wave 3 Stage 1 — Contract demo (stub agent)")
    lines.append("=" * 70)
    lines.append("")
    lines.append("Original prose:")
    lines.append(f"  {report.original_prose}")
    lines.append("")
    lines.append("Per-claim disposition (Pass A):")
    for d in report.claim_dispositions:
        lines.append(
            f"  text_span={d.claim.text_span!r:15}  "
            f"declared={d.claim.value!r:8}  "
            f"true={d.true_value!r:8}  "
            f"status={d.status}"
            + (f"  reason={d.reason}" if d.reason else "")
        )
    lines.append("")
    lines.append(f"Undeclared-numeric strips (Pass B): "
                 f"{len(report.undeclared_strips)}")
    for u in report.undeclared_strips:
        lines.append(
            f"  text={u['text']!r:8}  value={u['value']!r:8}  "
            f"span={u['span']}"
        )
    lines.append("")
    lines.append("Cleaned prose (delivered to user):")
    lines.append(f"  {report.prose}")
    lines.append("")
    lines.append("Chart shape:")
    lines.append(
        f"  kind={response.chart_intent['kind']}  "
        f"x={response.chart_intent['x']!r}  "
        f"series={response.chart_intent['series']!r}"
    )
    lines.append(
        f"  result columns: {list(response.result.columns)}"
    )
    lines.append(
        f"  result rows: {len(response.result)} "
        f"(values pulled FROM result, never from model)"
    )
    lines.append("")
    lines.append(
        f"Caveats: {response.caveats}\n"
        f"Grain notes: {response.grain_notes}\n"
        f"SQL surfaces: {[(s.surface, s.row_count) for s in response.sql]}"
    )
    lines.append("=" * 70)
    return "\n".join(lines)
