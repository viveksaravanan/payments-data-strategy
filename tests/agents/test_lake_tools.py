"""Stage 2 tests — the lake tool surface specialists call (SPEC §2).

Two tools:

* ``query_tenant(viewer, sql)`` — viewer-scoped DuckDB reads against
  ``data/raw/``; rejects cross-merchant SQL via the Wave 2 isolation
  guards.
* ``read_lake_table(viewer, table, filters)`` — scoped reads from
  ``data/lake/``; off-grain filters rejected with the manifest
  Excludes; viewer rows dropped; identity stripped.

Plus the fenced-block parser the specialist uses to extract the
model's render + caveats from its final response.
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.agents.lake_tools import (
    LakeToolError,
    parse_caveats_block,
    parse_render_block,
    query_tenant,
    strip_render_and_caveats_blocks,
)


# ---------------------------------------------------------------------
# query_tenant
# ---------------------------------------------------------------------

def test_query_tenant_runs_viewer_scoped_select() -> None:
    """A SELECT with a viewer-scoping predicate is allowed and
    returns rows."""
    sql = (
        "SELECT COUNT(*) AS n FROM transactions WHERE banner_code = 'KRG'"
    )
    payload = query_tenant("KRG", sql)
    assert "rows" in payload
    assert payload["columns"] == ["n"]
    assert payload["rows"][0][0] > 0


def test_query_tenant_rejects_cross_merchant_sql() -> None:
    """A query asking for ACM while viewer=KRG is rejected (Wave 2
    isolation predicate check)."""
    sql = (
        "SELECT COUNT(*) FROM transactions WHERE banner_code = 'ACM'"
    )
    with pytest.raises(LakeToolError):
        query_tenant("KRG", sql)


def test_query_tenant_rejects_unscoped_query() -> None:
    """A query with no viewer-scoping predicate is rejected."""
    sql = "SELECT COUNT(*) FROM transactions"
    with pytest.raises(LakeToolError):
        query_tenant("KRG", sql)


def test_query_tenant_truncates_to_llm_budget() -> None:
    """Payload caps rows at LLM_ROW_BUDGET and flags truncation; the
    full frame is still in the payload for the specialist."""
    sql = (
        "SELECT * FROM transactions WHERE banner_code = 'KRG' LIMIT 200"
    )
    payload = query_tenant("KRG", sql)
    assert payload["row_count"] >= 50
    assert len(payload["rows"]) <= 50
    assert payload["truncated"] is True
    assert isinstance(payload["frame"], pd.DataFrame)


# ---------------------------------------------------------------------
# Fenced-block parsing
# ---------------------------------------------------------------------

def test_parse_render_block_extracts_json() -> None:
    text = """Here is my answer.

```render
{
  "merge": {"on": ["category"], "own_value_col": "own_price",
            "peer_value_col": "price_index", "gap_op": "ratio"},
  "chart_intent": {"kind": "cross_merchant_comparison",
                   "x": "category",
                   "series": ["own_value", "peer_benchmark"],
                   "title": "Pricing"},
  "claims": []
}
```
"""
    block = parse_render_block(text)
    assert block is not None
    assert block["chart_intent"]["kind"] == "cross_merchant_comparison"
    assert block["merge"]["gap_op"] == "ratio"


def test_parse_render_block_returns_none_when_absent() -> None:
    assert parse_render_block("Just prose, no render block.") is None


def test_parse_render_block_returns_none_when_malformed() -> None:
    text = "```render\n{not valid json}\n```"
    assert parse_render_block(text) is None


def test_parse_caveats_block_extracts_list() -> None:
    text = """Some prose.

```caveats
["Peer set is 2 grocers.", "Window is March 1 — May 29."]
```
"""
    cav = parse_caveats_block(text)
    assert cav == [
        "Peer set is 2 grocers.",
        "Window is March 1 — May 29.",
    ]


def test_strip_render_and_caveats_blocks() -> None:
    text = """Your dairy price index is 1.062 above peers.

```render
{"merge": {}, "chart_intent": {}, "claims": []}
```

```caveats
["One caveat."]
```
"""
    cleaned = strip_render_and_caveats_blocks(text)
    assert "render" not in cleaned.lower()
    assert "caveats" not in cleaned.lower()
    assert "1.062" in cleaned
