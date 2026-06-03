"""Stage 1b tests — deterministic chart builder (SPEC §1.3, D25.2/.3).

The structural guarantee: every numeric appearing in a built figure
must trace to a cell in the result. We can't easily assert "every
numeric in the figure is in the result" — Plotly figures aren't
that introspectable — but we CAN assert:

* The builder rejects intents that name columns not present in
  ``result`` (``MissingColumnError``). The model can't sneak in a
  numeric by naming a column the data doesn't have.
* The builder rejects unknown ``kind`` values (``UnsupportedIntentError``).
* Each per-kind builder fills its primary numeric channels from the
  result's named columns (verified by reading the trace data back).

These three checks structurally enforce the "values come from data,
not from the model" guarantee.
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import pytest

from src.agents.chart_build import (
    MissingColumnError,
    UnsupportedIntentError,
    build_chart,
)


# ---------------------------------------------------------------------
# Fixtures: a small merged result the per-kind builders can read.
# ---------------------------------------------------------------------

@pytest.fixture
def time_series_result() -> pd.DataFrame:
    return pd.DataFrame([
        {"period_start": "2026-04-05", "own_value": 1.05,
         "peer_benchmark": 1.00, "gap": 0.05},
        {"period_start": "2026-04-12", "own_value": 1.10,
         "peer_benchmark": 1.02, "gap": 0.08},
        {"period_start": "2026-04-19", "own_value": 1.08,
         "peer_benchmark": 1.04, "gap": 0.04},
    ])


@pytest.fixture
def comparison_result() -> pd.DataFrame:
    return pd.DataFrame([
        {"category": "DAIRY", "own_value": 1.05, "peer_benchmark": 1.00},
        {"category": "MEAT",  "own_value": 0.98, "peer_benchmark": 1.02},
        {"category": "PRODUCE", "own_value": 1.12, "peer_benchmark": 1.05},
    ])


@pytest.fixture
def heatmap_result() -> pd.DataFrame:
    return pd.DataFrame([
        {"category": "DAIRY",   "zone": "Z01", "gap": 0.05},
        {"category": "DAIRY",   "zone": "Z02", "gap": -0.03},
        {"category": "MEAT",    "zone": "Z01", "gap": 0.02},
        {"category": "MEAT",    "zone": "Z02", "gap": -0.01},
        {"category": "PRODUCE", "zone": "Z01", "gap": 0.08},
        {"category": "PRODUCE", "zone": "Z02", "gap": 0.04},
    ])


# ---------------------------------------------------------------------
# Unsupported / missing-key validation
# ---------------------------------------------------------------------

def test_unknown_kind_raises(time_series_result) -> None:
    with pytest.raises(UnsupportedIntentError) as exc:
        build_chart({"kind": "not_a_real_kind", "title": "X"},
                    time_series_result)
    assert "not_a_real_kind" in str(exc.value)


def test_missing_required_key_raises(time_series_result) -> None:
    """time_series_vs_peers needs ``x`` + ``series`` — missing them
    raises UnsupportedIntentError."""
    with pytest.raises(UnsupportedIntentError) as exc:
        build_chart(
            {"kind": "time_series_vs_peers", "title": "Trend"},
            time_series_result,
        )
    assert "x" in str(exc.value) or "series" in str(exc.value)


def test_named_column_not_in_result_raises(time_series_result) -> None:
    """The structural guarantee — every column-name in intent must
    exist in result. A series of *only* fake columns is empty
    post-reconciler (Wave 3 Stage 6.5 follow-up #7) and triggers the
    missing-required-key check from _require_keys."""
    with pytest.raises((MissingColumnError, UnsupportedIntentError)):
        build_chart(
            {"kind": "time_series_vs_peers", "title": "Trend",
             "x": "period_start", "series": ["fake_column"]},
            time_series_result,
        )


def test_list_intent_partial_match_drops_invalid_keeps_valid(
    time_series_result,
) -> None:
    """``series`` with one valid + one invalid column drops the
    invalid via the reconciler (Stage 6.5 follow-up #7). Stage 6.5
    Fix 14 then auto-adds ``peer_benchmark`` to series for
    cross-merchant chart kinds when the frame has it — so the final
    figure shows own_value (kept) + peer_benchmark (auto-added) =
    2 traces. ghost_column dropped silently."""
    fig = build_chart(
        {"kind": "time_series_vs_peers", "title": "X",
         "x": "period_start",
         "series": ["own_value", "ghost_column"]},
        time_series_result,
    )
    assert fig is not None
    # 1 own + 1 auto-added peer; ghost_column dropped.
    assert len(fig.data) == 2
    series_names = {trace.name for trace in fig.data}
    assert series_names == {"own_value", "peer_benchmark"}


# ---------------------------------------------------------------------
# Per-kind smoke + numeric-source-tracing
# ---------------------------------------------------------------------

def test_time_series_pulls_y_from_result(time_series_result) -> None:
    fig = build_chart(
        {"kind": "time_series_vs_peers", "title": "Trend",
         "x": "period_start",
         "series": ["own_value", "peer_benchmark"],
         "y_format": "index"},
        time_series_result,
    )
    assert isinstance(fig, go.Figure)
    # Trace y-values are exactly the result columns (no model-supplied
    # values sneaking in).
    own_trace = fig.data[0]
    peer_trace = fig.data[1]
    assert list(own_trace.y) == time_series_result["own_value"].tolist()
    assert list(peer_trace.y) == time_series_result["peer_benchmark"].tolist()
    assert list(own_trace.x) == time_series_result["period_start"].tolist()


def test_cross_merchant_comparison_pulls_x_from_result(comparison_result) -> None:
    fig = build_chart(
        {"kind": "cross_merchant_comparison", "title": "Pricing vs peers",
         "x": "category",
         "series": ["own_value", "peer_benchmark"],
         "y_format": "index"},
        comparison_result,
    )
    own_bar = fig.data[0]
    peer_bar = fig.data[1]
    # Horizontal bars — values on x, labels on y.
    assert list(own_bar.x) == comparison_result["own_value"].tolist()
    assert list(peer_bar.x) == comparison_result["peer_benchmark"].tolist()
    assert list(own_bar.y) == comparison_result["category"].tolist()


def test_heatmap_pulls_values_from_result(heatmap_result) -> None:
    fig = build_chart(
        {"kind": "heatmap", "title": "Gap heatmap",
         "row": "category", "col": "zone", "value": "gap",
         "palette": "diverging"},
        heatmap_result,
    )
    z = fig.data[0].z
    # The pivot table values are present in the figure trace.
    pivot = heatmap_result.pivot_table(
        index="category", columns="zone", values="gap", aggfunc="mean",
    )
    for r in range(z.shape[0]):
        for c in range(z.shape[1]):
            assert z[r][c] == pivot.values[r][c]


def test_scatter_quadrant_pulls_xy_from_result() -> None:
    df = pd.DataFrame([
        {"store_id": "S1", "x_metric": 1.0, "y_metric": 0.5, "size_metric": 10},
        {"store_id": "S2", "x_metric": 1.2, "y_metric": 0.8, "size_metric": 14},
    ])
    fig = build_chart(
        {"kind": "scatter_quadrant", "title": "Stores",
         "x": "x_metric", "y": "y_metric",
         "label": "store_id", "size": "size_metric"},
        df,
    )
    trace = fig.data[0]
    assert list(trace.x) == df["x_metric"].tolist()
    assert list(trace.y) == df["y_metric"].tolist()
    assert list(trace.text) == df["store_id"].tolist()


def test_waterfall_pulls_drivers_from_result() -> None:
    df = pd.DataFrame([
        {"driver": "Volume", "contribution": 0.10},
        {"driver": "Mix",    "contribution": -0.03},
        {"driver": "Promo",  "contribution": 0.02},
    ])
    fig = build_chart(
        {"kind": "waterfall", "title": "Gap drivers",
         "x": "driver", "y": "contribution"},
        df,
    )
    trace = fig.data[0]
    assert list(trace.x) == df["driver"].tolist()
    assert list(trace.y) == df["contribution"].tolist()


def test_kpi_callout_reads_first_row_value() -> None:
    df = pd.DataFrame([{"index_value": 1.12, "delta": 0.05}])
    fig = build_chart(
        {"kind": "kpi_callout", "title": "Price index",
         "value": "index_value", "delta": "delta"},
        df,
    )
    indicator = fig.data[0]
    # Plotly Indicator's value is the cell.
    assert indicator.value == 1.12


def test_kpi_callout_empty_result_raises() -> None:
    df = pd.DataFrame([{"index_value": 1.12}]).iloc[0:0]
    with pytest.raises(MissingColumnError):
        build_chart(
            {"kind": "kpi_callout", "title": "X", "value": "index_value"},
            df,
        )


def test_small_multiples_facets_from_column() -> None:
    df = pd.DataFrame([
        {"category": "DAIRY",   "week": "W1", "own_value": 1.05},
        {"category": "DAIRY",   "week": "W2", "own_value": 1.10},
        {"category": "MEAT",    "week": "W1", "own_value": 0.98},
        {"category": "MEAT",    "week": "W2", "own_value": 1.02},
        {"category": "PRODUCE", "week": "W1", "own_value": 1.12},
        {"category": "PRODUCE", "week": "W2", "own_value": 1.15},
    ])
    fig = build_chart(
        {"kind": "small_multiples", "title": "By category",
         "facet": "category", "x": "week", "series": ["own_value"]},
        df,
    )
    # One trace per facet (3 facets → 3 traces).
    assert len(fig.data) == 3


def test_table_drilldown_columns_from_result(comparison_result) -> None:
    fig = build_chart(
        {"kind": "table_drilldown", "title": "Comparison",
         "columns": ["category", "own_value", "peer_benchmark"]},
        comparison_result,
    )
    table = fig.data[0]
    # Header values are the requested columns.
    assert list(table.header.values) == ["category", "own_value", "peer_benchmark"]
    # Cell values are the columns from the result.
    assert list(table.cells.values[0]) == comparison_result["category"].tolist()
    assert list(table.cells.values[1]) == comparison_result["own_value"].tolist()


def test_geo_map_pulls_lat_lon_from_result() -> None:
    df = pd.DataFrame([
        {"store_id": "S1", "lat": 35.23, "lon": -80.84, "metric": 1.05},
        {"store_id": "S2", "lat": 35.20, "lon": -80.80, "metric": 0.95},
    ])
    fig = build_chart(
        {"kind": "geo_map", "title": "Stores",
         "lat": "lat", "lon": "lon", "value": "metric",
         "label": "store_id"},
        df,
    )
    trace = fig.data[0]
    assert list(trace.lat) == df["lat"].tolist()
    assert list(trace.lon) == df["lon"].tolist()


# ---------------------------------------------------------------------
# All nine kinds registered
# ---------------------------------------------------------------------

def test_cross_merchant_comparison_aggregates_and_caps_runaway_rows() -> None:
    """Regression — Checkpoint 2 v3 batch 8 produced a 74,880px bar
    chart on P1 because the model plotted 1,248 category × zone × week
    rows as separate bars. The builder now aggregates by the chart's
    `x` column AND caps to MAX_BARS bars + MAX_HEIGHT_PX height."""
    from src.agents.chart_build import MAX_BARS, MAX_HEIGHT_PX
    df = pd.DataFrame({
        "category": (["DAIRY"] * 40 + ["MEAT"] * 40 + ["PRODUCE"] * 40
                     + [f"C{i:03d}" for i in range(40)]),
        # 4 unique categories above + 40 unique => 44 distinct x values.
        "own_value":      list(range(160)),
        "peer_benchmark": [v * 1.1 for v in range(160)],
    })
    fig = build_chart(
        {"kind": "cross_merchant_comparison", "title": "Pricing",
         "x": "category",
         "series": ["own_value", "peer_benchmark"],
         "y_format": "index"},
        df,
    )
    # Each trace has at most MAX_BARS bars (post-aggregation).
    own_bar = fig.data[0]
    assert len(own_bar.x) <= MAX_BARS
    # Height capped.
    assert fig.layout.height <= MAX_HEIGHT_PX
    # The aggregation collapsed the 40 DAIRY rows to one value.
    cat_values = list(own_bar.y)
    assert "DAIRY" in cat_values
    assert cat_values.count("DAIRY") == 1


def test_time_series_aggregates_to_one_point_per_x() -> None:
    """Regression — the builder must aggregate by `x` so a merge
    frame with category × zone × week produces one point per week,
    not a jagged trace with multiple values at each week."""
    from src.agents.chart_build import MAX_TIME_POINTS, MAX_HEIGHT_PX
    df = pd.DataFrame({
        "period_start": (
            ["2026-03-01"] * 5 + ["2026-03-08"] * 5 + ["2026-03-15"] * 5
        ),
        # Same week appears 5× (across zones/categories).
        "own_value":      list(range(15)),
        "peer_benchmark": [v * 1.05 for v in range(15)],
    })
    fig = build_chart(
        {"kind": "time_series_vs_peers", "title": "Trend",
         "x": "period_start",
         "series": ["own_value", "peer_benchmark"],
         "y_format": "index"},
        df,
    )
    own_trace = fig.data[0]
    # One point per unique week (3), not 15.
    assert len(own_trace.x) == 3
    assert fig.layout.height <= MAX_HEIGHT_PX


def test_waterfall_sums_drivers_and_caps() -> None:
    """Waterfall must sum contributions per driver label and cap to
    MAX_WATERFALL_BARS so dumping 200 raw rows doesn't produce an
    unreadable chart."""
    from src.agents.chart_build import MAX_HEIGHT_PX
    df = pd.DataFrame({
        # Three drivers, each appearing twice — totals should be summed.
        "driver":       ["Vol", "Mix", "Promo", "Vol", "Mix", "Promo"],
        "contribution": [0.05, -0.02, 0.01, 0.05, -0.01, 0.01],
    })
    fig = build_chart(
        {"kind": "waterfall", "title": "Gap drivers",
         "x": "driver", "y": "contribution"},
        df,
    )
    trace = fig.data[0]
    # 3 unique drivers — one bar each, summed.
    assert len(trace.x) == 3
    # Vol summed = 0.05 + 0.05 = 0.10.
    by_label = dict(zip(list(trace.x), list(trace.y)))
    assert by_label["Vol"] == pytest.approx(0.10)
    assert by_label["Mix"] == pytest.approx(-0.03)
    assert fig.layout.height <= MAX_HEIGHT_PX


def test_scatter_quadrant_dedupes_by_label_and_caps() -> None:
    """Scatter takes the first row per label (when label column is
    given) so a duplicated-label merge doesn't render N markers per
    store. Total points capped to MAX_SCATTER_POINTS."""
    from src.agents.chart_build import MAX_HEIGHT_PX, MAX_SCATTER_POINTS
    df = pd.DataFrame({
        "store_id": ["S1", "S2", "S3", "S1", "S2"],
        "x_metric": [1.0, 1.2, 0.9, 1.0, 1.2],
        "y_metric": [0.5, 0.8, 0.6, 0.5, 0.8],
    })
    fig = build_chart(
        {"kind": "scatter_quadrant", "title": "Stores",
         "x": "x_metric", "y": "y_metric", "label": "store_id"},
        df,
    )
    trace = fig.data[0]
    # 3 unique stores; duplicates dropped.
    assert len(trace.x) == 3
    assert fig.layout.height <= MAX_HEIGHT_PX


def test_heatmap_caps_rows_and_cols() -> None:
    """Heatmap pivot is capped at MAX_HEATMAP_ROWS × MAX_HEATMAP_COLS."""
    from src.agents.chart_build import (
        MAX_HEATMAP_COLS, MAX_HEATMAP_ROWS, MAX_HEIGHT_PX,
    )
    rows = [f"R{i:03d}" for i in range(50)]
    cols = [f"C{i:03d}" for i in range(50)]
    df = pd.DataFrame([
        {"row": r, "col": c, "value": (i + j) * 0.01}
        for i, r in enumerate(rows) for j, c in enumerate(cols)
    ])
    fig = build_chart(
        {"kind": "heatmap", "title": "Big matrix",
         "row": "row", "col": "col", "value": "value"},
        df,
    )
    z = fig.data[0].z
    assert z.shape[0] <= MAX_HEATMAP_ROWS
    assert z.shape[1] <= MAX_HEATMAP_COLS
    assert fig.layout.height <= MAX_HEIGHT_PX


def test_small_multiples_caps_facets() -> None:
    """Small multiples capped at MAX_FACETS subplots."""
    from src.agents.chart_build import MAX_FACETS, MAX_HEIGHT_PX
    facets = [f"F{i:02d}" for i in range(20)]
    df = pd.DataFrame([
        {"facet": f, "x": w, "own_value": (i + w) * 0.1}
        for i, f in enumerate(facets) for w in range(4)
    ])
    fig = build_chart(
        {"kind": "small_multiples", "title": "Per facet",
         "facet": "facet", "x": "x", "series": ["own_value"]},
        df,
    )
    # One trace per facet, capped.
    assert len(fig.data) <= MAX_FACETS
    assert fig.layout.height <= MAX_HEIGHT_PX


def test_table_drilldown_caps_runaway_row_count() -> None:
    """Regression — Checkpoint 2 batch 7 produced a 21,530px-tall
    table chart because the model dumped a 700-row result into
    table_drilldown. The chart builder caps rendered rows so the
    output stays browser-friendly; the title surfaces the truncation."""
    from src.agents.chart_build import (
        _TABLE_DRILLDOWN_MAX_HEIGHT_PX,
        _TABLE_DRILLDOWN_MAX_ROWS,
    )
    big = pd.DataFrame({
        "category": [f"C{i:04d}" for i in range(700)],
        "own_value": list(range(700)),
        "peer_benchmark": [v * 1.1 for v in range(700)],
    })
    fig = build_chart(
        {"kind": "table_drilldown", "title": "Top categories",
         "columns": ["category", "own_value", "peer_benchmark"]},
        big,
    )
    # Cells contain only the first N rows.
    table = fig.data[0]
    assert len(table.cells.values[0]) == _TABLE_DRILLDOWN_MAX_ROWS
    # Height capped.
    assert fig.layout.height <= _TABLE_DRILLDOWN_MAX_HEIGHT_PX
    # Title reflects the truncation so reviewers can tell.
    assert "top" in fig.layout.title.text.lower()
    assert "700" in fig.layout.title.text


def test_table_drilldown_small_result_unchanged() -> None:
    """The cap only applies when result exceeds it — small results
    render with all rows and no title decoration."""
    small = pd.DataFrame({"x": ["a", "b", "c"], "y": [1, 2, 3]})
    fig = build_chart(
        {"kind": "table_drilldown", "title": "Three rows",
         "columns": ["x", "y"]},
        small,
    )
    assert len(fig.data[0].cells.values[0]) == 3
    assert "top" not in fig.layout.title.text.lower()


def test_all_nine_kinds_routable() -> None:
    """The D25.3 vocabulary is exactly nine kinds; the builder
    dispatch table must cover all of them so the model can't pick an
    intent that has no builder."""
    from src.agents.chart_build import _BUILDERS
    expected = {
        "time_series_vs_peers", "cross_merchant_comparison", "heatmap",
        "scatter_quadrant", "waterfall", "geo_map", "kpi_callout",
        "small_multiples", "table_drilldown",
    }
    assert set(_BUILDERS.keys()) == expected
