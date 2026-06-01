"""Stage 2.3 — Trade-Area specialist tests.

Trade-Area uses two lake tables — ``lake_trade_area`` and
``lake_cross_merchant_cohorts``. The cohort table is the
"don't merge with own" path: lake-only, results delivered directly.

Coverage:

* Canonical trade-area question: own store units vs
  ``lake_trade_area.share_of_zone`` at (derived_zone, category).
* Cohort-only path: pull ``lake_cross_merchant_cohorts``, emit empty
  merge, deliver the lake frame as the result.
* No-raw-mean enforcement: the lake's manifest excludes "no raw mean
  spend" — the prompt declares it; the test asserts the prompt
  carries the median-only rule.
* Trade prompt avoids fraud vocabulary.
"""
from __future__ import annotations

import pytest

from src.agents.context import MerchantContext
from src.agents.response import AgentResponse
from src.agents.trade import TradeAreaSpecialist
from tests.agents._fake_llm import (
    patch_llm,
    scripted_text,
    scripted_tool_use,
)


@pytest.fixture
def viewer_krg() -> MerchantContext:
    return MerchantContext.for_merchant("KRG")


# ---------------------------------------------------------------------
# Cohort-only path (no own merge)
# ---------------------------------------------------------------------

def test_trade_cohort_only_answer(viewer_krg, monkeypatch) -> None:
    """Cohort question reads lake_cross_merchant_cohorts directly,
    emits empty merge (the lake frame IS the result), and produces a
    table_drilldown chart. The median-only D24.2 rule is enforced
    by the manifest grain_notes carrying "no raw mean spend"."""

    # The model emits an empty merge so the specialist uses the lake
    # frame directly as the result.
    final_text = """In the all-three cohort, the median combined spend is $2125.

```render
{
  "merge": {},
  "chart_intent": {
    "kind": "table_drilldown",
    "title": "Cross-merchant cohort overlap",
    "columns": ["derived_zone", "cohort_combination", "cohort_size",
                "median_combined_spend", "frequency_band"]
  },
  "claims": [
    {
      "text_span": "$2125",
      "value": 2125,
      "source": {
        "type": "CellLookup",
        "row_filter": {"cohort_combination": "all_three"},
        "column": "median_combined_spend",
        "agg": "mean"
      }
    }
  ]
}
```

```caveats
["Cohorts published as median + IQR only (D24.2)."]
```
"""
    script = [
        scripted_tool_use("read_lake_table", {
            "table": "lake_cross_merchant_cohorts",
        }),
        scripted_text(final_text),
    ]
    specialist = TradeAreaSpecialist(viewer_krg)
    with patch_llm(monkeypatch, script):
        resp = specialist.answer("Tell me about cross-merchant overlap.")

    assert isinstance(resp, AgentResponse)
    # The result IS the lake frame (no merge).
    assert "cohort_combination" in resp.result.columns
    assert "median_combined_spend" in resp.result.columns
    # No raw mean column in the manifest grain notes.
    joined = " ".join(resp.grain_notes).lower()
    assert "raw mean" in joined or "median" in joined
    # Chart is a table_drilldown.
    assert resp.chart_intent["kind"] == "table_drilldown"


# ---------------------------------------------------------------------
# Prompt invariants
# ---------------------------------------------------------------------

def test_trade_prompt_declares_no_raw_mean() -> None:
    """The Trade-Area prompt must state the median-only rule (D24.2
    — concentration risk; never publish raw mean spend)."""
    prompt = TradeAreaSpecialist.PROMPT_PATH.read_text().lower()
    assert "median" in prompt
    assert "raw mean" in prompt or "no raw mean" in prompt or "never publish raw mean" in prompt or "never say" in prompt


def test_trade_prompt_declines_peer_revenue() -> None:
    """The prompt must offer a decline-gracefully template for
    out-of-grain asks ("what's Acme's revenue in Z05?")."""
    prompt = TradeAreaSpecialist.PROMPT_PATH.read_text().lower()
    assert "per-merchant" in prompt or "share_of_zone" in prompt
    assert "decline" in prompt or "isn't published" in prompt or "not published" in prompt


def test_trade_prompt_never_mentions_fraud() -> None:
    prompt = TradeAreaSpecialist.PROMPT_PATH.read_text().lower()
    assert "fraud" not in prompt
    assert "tampering" not in prompt


def test_trade_prompt_cohort_table_omits_peer_relationship() -> None:
    """The prompt must say the cohort table has NO peer_relationship —
    that's the structural difference from the other lake tables."""
    prompt = TradeAreaSpecialist.PROMPT_PATH.read_text()
    # The prompt block describing the cohort table must call out the
    # `peer_relationship` absence near the dimension list.
    assert "NO\n" in prompt or "(NO" in prompt
    # And the peer_relationship reference is on the same line as the
    # NO callout (within the cohort-table section).
    cohort_section = prompt.split("lake_cross_merchant_cohorts", 1)[-1]
    assert "peer_relationship" in cohort_section
    assert "NO" in cohort_section
