"""Stage 5 — assert the v3 drift paths are gone (SPEC §5, D25.6).

The §1 contract makes drift impossible by construction: chart values
come from the result, not the model; claims are validated; the
single source of truth is the merged comparison frame. But to lock
the design in, we assert the v3 escape hatches are physically gone:

* `src/agents/tools.py::make_chart` (model writes ``series.values``)
* `src/dashboard/chart_takeaways.py` (model-written caption layer)
* `src/agents/tools.py` itself (v3 tool surface, retired in favor of
  `lake_tools.py`)

Standalone dashboard panels keep their own `data.py` sourcing per
the SPEC — that's not an agent response making claims, so it stays
untouched.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_v3_tools_module_deleted() -> None:
    """`src/agents/tools.py` is gone — Wave 3 uses `lake_tools.py`
    instead."""
    assert not (REPO_ROOT / "src" / "agents" / "tools.py").exists()


def test_chart_takeaways_module_deleted() -> None:
    """`src/dashboard/chart_takeaways.py` is gone — D25.6 retires
    the model-written caption layer."""
    assert not (REPO_ROOT / "src" / "dashboard" / "chart_takeaways.py").exists()


def test_no_make_chart_in_lake_tools() -> None:
    """The Wave 3 tool surface has no `make_chart` tool — chart
    construction goes through `chart_build.build_chart` deterministically
    from the merged result. The four Wave 3 tools are schema_info
    (column-list introspection so the model doesn't guess) +
    query_tenant (own data) + read_lake_table (peer data, scoped) +
    emit_response (structured terminator that feeds the §1 contract)."""
    from src.agents import lake_tools as LT
    tool_names = {t["name"] for t in LT.TOOLS_SPECIALIST}
    assert "make_chart" not in tool_names
    assert tool_names == {
        "schema_info", "query_tenant", "read_lake_table", "emit_response",
    }


def test_specialist_tools_list_does_not_offer_make_chart() -> None:
    """Defense in depth: even if a specialist subclass overrode TOOLS,
    no live tool in the suite advertises `make_chart`."""
    from src.agents.specialist import Specialist
    tool_names = {t["name"] for t in Specialist.TOOLS}
    assert "make_chart" not in tool_names


def test_chart_patterns_palette_preserved() -> None:
    """SPEC §5 KEEP: `chart_patterns.py` survives as the renderer
    palette. The nine pattern functions are intact."""
    from src.dashboard import chart_patterns as CP
    # Sample the most-used patterns; the file is the source of truth.
    assert hasattr(CP, "render_time_series_vs_peers")
    assert hasattr(CP, "render_cross_merchant_comparison")
    assert hasattr(CP, "render_heatmap")
    assert hasattr(CP, "render_scatter_with_peers")
    assert hasattr(CP, "render_waterfall")
    assert hasattr(CP, "render_neighborhood_map")
    assert hasattr(CP, "render_kpi_callout")
    assert hasattr(CP, "render_table_with_drilldown")
