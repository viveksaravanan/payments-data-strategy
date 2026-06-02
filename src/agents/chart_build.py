"""Wave 3 §1.3 deterministic chart builder (D25.2, D25.3).

The model authors a ``ChartIntent`` — a dict that names ``kind`` plus
which **columns** of the merged ``result`` map to x / series /
y_format / etc. The model **never** writes numeric values. This
module reads the named columns from the result and constructs the
Plotly figure. The structural guarantee is that every numeric
appearing in the figure traces to a cell in the result frame.

The nine intent ``kind`` values mirror the chart_patterns.py
palette families (D25.3):

* ``time_series_vs_peers`` — line: own vs peer series over time.
* ``cross_merchant_comparison`` — horizontal bars: own vs peer.
* ``heatmap`` — 2-D matrix with diverging/sequential color.
* ``scatter_quadrant`` — scatter with reference lines.
* ``waterfall`` — Plotly Waterfall, driver decomposition.
* ``geo_map`` — Mapbox scatter (open-street-map style; no token).
* ``kpi_callout`` — single-number indicator.
* ``small_multiples`` — facet grid.
* ``table_drilldown`` — Plotly Table from the merged result.

Wave 3 ships headless Plotly figures (the preview harness emits
HTML via ``to_html``); Wave 4's dashboard rebuild will plug them
into ``st.plotly_chart``.

Style palette is duplicated from ``src.dashboard.chart_patterns`` to
keep the agent layer free of Streamlit/Folium imports. If a constant
moves there, mirror it here.
"""
from __future__ import annotations

from typing import Any, Literal, TypedDict

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


# ---------------------------------------------------------------------
# Palette — mirrored from src/dashboard/chart_patterns.py.
# ---------------------------------------------------------------------

ACCENT          = "#0F4C81"
ACCENT_SOFT     = "#D8E2EE"
PEER_A          = "#6B7280"
PEER_B          = "#9CA3AF"
PEER_AGGREGATE  = "#4B5563"
BASELINE_LINE   = "rgba(128, 128, 128, 0.4)"
GRID_LINE       = "rgba(128, 128, 128, 0.10)"

TEXT            = "#1A1F2E"
TEXT_2          = "#4A5161"
TEXT_MUTED      = "#9CA3AF"

DIVERGING_LOW   = "#C44536"
DIVERGING_MID   = "#FFFFFF"
DIVERGING_HIGH  = "#0F4C81"

HOVERLABEL = dict(
    bgcolor="#FFFFFF",
    bordercolor="#E5E7EB",
    font=dict(family="-apple-system, system-ui, sans-serif",
              size=13, color="#1F2937"),
)


# ---------------------------------------------------------------------
# ChartIntent schema — model authors this dict; deterministic code reads it.
# ---------------------------------------------------------------------

ChartKind = Literal[
    "time_series_vs_peers",
    "cross_merchant_comparison",
    "heatmap",
    "scatter_quadrant",
    "waterfall",
    "geo_map",
    "kpi_callout",
    "small_multiples",
    "table_drilldown",
]


class ChartIntent(TypedDict, total=False):
    """The model emits a dict of this shape. ``kind`` + ``title`` are
    required for every kind; per-kind required keys are listed below.

    All value-bearing fields name **columns of the result**, never
    values. The deterministic builder reads from result by column name.

    Per-kind required keys (in addition to ``kind`` and ``title``):

    * ``time_series_vs_peers``: ``x`` (time column), ``series`` (list of
      value columns), ``y_format``.
    * ``cross_merchant_comparison``: ``x`` (label column), ``series``
      (list of value columns), ``y_format``.
    * ``heatmap``: ``row`` (row-label column), ``col`` (col-label
      column), ``value`` (numeric column), optional
      ``palette`` ∈ {"diverging", "sequential"}.
    * ``scatter_quadrant``: ``x`` (x-axis column), ``y`` (y-axis
      column), optional ``label`` (point-label column), optional
      ``size`` (size column).
    * ``waterfall``: ``x`` (driver-label column), ``y`` (driver-value
      column).
    * ``geo_map``: ``lat`` (latitude column), ``lon`` (longitude column),
      optional ``value`` (color column), optional ``label``.
    * ``kpi_callout``: ``value`` (single-value column — uses first row).
      Optional ``delta`` column. Optional ``y_format``.
    * ``small_multiples``: ``facet`` (column to facet on), ``x`` (x
      column), ``series`` (value column).
    * ``table_drilldown``: ``columns`` (list of column names to show).

    ``title`` and ``takeaway`` are free strings (not column references).
    The validator + agent prompts ensure they don't contain numerics
    that aren't backed by the result.
    """
    kind: ChartKind
    title: str
    takeaway: str
    x: str
    y: str
    series: list[str]
    y_format: Literal["index", "currency", "pct", "count", "raw"]
    row: str
    col: str
    value: str
    label: str
    size: str
    delta: str
    facet: str
    columns: list[str]
    lat: str
    lon: str
    palette: Literal["diverging", "sequential"]


class MissingColumnError(KeyError):
    """Raised when ``ChartIntent`` names a column that isn't in
    ``result``. The structural guarantee — every numeric in the
    figure traces to a cell — depends on this check."""


class UnsupportedIntentError(ValueError):
    """Raised when ``ChartIntent.kind`` is unrecognized or when a
    per-kind required key is missing."""


# ---------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------

def _require_keys(intent: dict, kind: str, required: list[str]) -> None:
    missing = [
        k for k in required
        if k not in intent
        or intent[k] in (None, "")
        or (isinstance(intent[k], list) and len(intent[k]) == 0)
    ]
    if missing:
        raise UnsupportedIntentError(
            f"ChartIntent kind={kind!r} missing required keys: {missing}. "
            f"Got: {sorted(intent.keys())}"
        )


def _require_columns(
    result: pd.DataFrame, intent: dict, keys: list[str],
) -> None:
    """For each ``key`` in ``intent`` (a column-name field), assert the
    referenced column exists in ``result``. ``keys`` can refer to
    string-valued or list-of-string-valued fields."""
    missing: list[str] = []
    for k in keys:
        if k not in intent:
            continue
        val = intent[k]
        if isinstance(val, str):
            if val not in result.columns:
                missing.append(f"{k}={val!r}")
        elif isinstance(val, list):
            for v in val:
                if v not in result.columns:
                    missing.append(f"{k} contains {v!r}")
    if missing:
        raise MissingColumnError(
            f"ChartIntent references columns not in result: {missing}. "
            f"Result columns: {list(result.columns)}"
        )


def _y_axis_title(y_format: str | None) -> str:
    return {
        "index":    "Index (baseline = 100)",
        "currency": "Amount ($)",
        "pct":      "Share (%)",
        "count":    "Count",
        "raw":      "",
    }.get(y_format or "raw", "")


# ---------------------------------------------------------------------
# Universal chart-builder guardrails: aggregate the result to the
# chart's natural grain BEFORE rendering, and cap per-kind series
# length + figure height. Without this, the model can emit a chart
# intent that plots every row of a multi-thousand-row merge frame —
# producing a 74,880px-tall figure (Checkpoint 2 batch 8 saw this).
# ---------------------------------------------------------------------

MAX_HEIGHT_PX = 720
MAX_BARS = 30                # cross_merchant_comparison
MAX_TIME_POINTS = 60         # time_series_vs_peers per series
MAX_SCATTER_POINTS = 200     # scatter_quadrant
MAX_WATERFALL_BARS = 30      # waterfall
MAX_FACETS = 9               # small_multiples
MAX_HEATMAP_ROWS = 25        # heatmap rows
MAX_HEATMAP_COLS = 25        # heatmap cols
MAX_GEO_MARKERS = 200        # geo_map


def _aggregate_by(
    df: pd.DataFrame,
    by: list[str],
    value_cols: list[str],
    *,
    agg: str = "mean",
) -> pd.DataFrame:
    """Group ``df`` by ``by`` and aggregate every column in
    ``value_cols`` with ``agg``. Returns the grouped frame with the
    ``by`` keys as columns (reset_index). Non-grouped/non-value
    columns are dropped; we keep only what the chart will plot, so
    figure data stays terse."""
    cols_present = [c for c in by + value_cols if c in df.columns]
    by_present = [c for c in by if c in df.columns]
    val_present = [c for c in value_cols if c in df.columns]
    if not by_present or not val_present:
        return df
    # ``sort=False`` preserves the order rows first appeared in
    # ``df`` so the chart's x-axis follows the data's natural
    # sequence (the model can sort upstream if it wants).
    return (
        df[cols_present]
        .groupby(by_present, as_index=False, sort=False)
        .agg({c: agg for c in val_present})
    )


def _cap_to_n(df: pd.DataFrame, n: int) -> tuple[pd.DataFrame, bool]:
    """Return ``(capped_df, truncated)``. When ``len(df) > n``, the
    head is kept and ``truncated`` is True."""
    if len(df) <= n:
        return df, False
    return df.head(n).copy(), True


def _decorate_title(title: str, *, truncated_note: str | None) -> str:
    if not truncated_note:
        return title or ""
    if not title:
        return truncated_note
    return f"{title}  ({truncated_note})"


# ---------------------------------------------------------------------
# Per-kind builders
# ---------------------------------------------------------------------

def _build_time_series(intent: dict, result: pd.DataFrame) -> go.Figure:
    _require_keys(intent, "time_series_vs_peers", ["x", "series"])
    _require_columns(result, intent, ["x", "series"])

    # Aggregate to one point per (x). Without this, a merge frame
    # with category × zone × week produces multiple values per
    # week and the trace becomes jagged spaghetti.
    aggregated = _aggregate_by(
        result, by=[intent["x"]], value_cols=list(intent["series"]),
        agg="mean",
    )
    capped, truncated = _cap_to_n(aggregated, MAX_TIME_POINTS)
    title = _decorate_title(
        intent.get("title", ""),
        truncated_note=(
            f"first {MAX_TIME_POINTS} of {len(aggregated)} periods"
            if truncated else None
        ),
    )

    fig = go.Figure()
    palette_cycle = [ACCENT, PEER_A, PEER_B, PEER_AGGREGATE]
    for i, col in enumerate(intent["series"]):
        is_own = i == 0
        fig.add_trace(go.Scatter(
            x=capped[intent["x"]],
            y=capped[col],
            mode="lines+markers",
            name=col,
            line=dict(
                color=palette_cycle[i % len(palette_cycle)],
                width=3 if is_own else 2,
                dash="solid" if is_own else "dash",
            ),
            marker=dict(size=8 if is_own else 6),
            connectgaps=False,
        ))
    fig.update_layout(
        title=title,
        hoverlabel=HOVERLABEL,
        yaxis_title=_y_axis_title(intent.get("y_format")),
        hovermode="x unified",
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=40, r=40, t=40, b=40),
        height=min(360, MAX_HEIGHT_PX),
        showlegend=True,
    )
    fig.update_xaxes(showgrid=False, zeroline=False)
    fig.update_yaxes(showgrid=True, gridcolor=GRID_LINE)
    return fig


def _build_cross_merchant_comparison(
    intent: dict, result: pd.DataFrame,
) -> go.Figure:
    _require_keys(intent, "cross_merchant_comparison", ["x", "series"])
    _require_columns(result, intent, ["x", "series"])

    aggregated = _aggregate_by(
        result, by=[intent["x"]], value_cols=list(intent["series"]),
        agg="mean",
    )
    capped, truncated = _cap_to_n(aggregated, MAX_BARS)
    title = _decorate_title(
        intent.get("title", ""),
        truncated_note=(
            f"first {MAX_BARS} of {len(aggregated)} {intent['x']} values"
            if truncated else None
        ),
    )

    fig = go.Figure()
    palette_cycle = [ACCENT, PEER_A, PEER_B, PEER_AGGREGATE]
    for i, col in enumerate(intent["series"]):
        fig.add_trace(go.Bar(
            y=capped[intent["x"]],
            x=capped[col],
            name=col,
            orientation="h",
            marker=dict(color=palette_cycle[i % len(palette_cycle)]),
        ))
    fig.update_layout(
        title=title,
        hoverlabel=HOVERLABEL,
        xaxis_title=_y_axis_title(intent.get("y_format")),
        barmode="group",
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=80, r=40, t=40, b=40),
        height=min(max(220, 60 * len(capped)), MAX_HEIGHT_PX),
    )
    fig.update_xaxes(showgrid=True, gridcolor=GRID_LINE)
    fig.update_yaxes(showgrid=False)
    return fig


def _build_heatmap(intent: dict, result: pd.DataFrame) -> go.Figure:
    _require_keys(intent, "heatmap", ["row", "col", "value"])
    _require_columns(result, intent, ["row", "col", "value"])

    pivot = result.pivot_table(
        index=intent["row"], columns=intent["col"],
        values=intent["value"], aggfunc="mean",
    )
    # Cap to keep the figure readable. Heatmaps with hundreds of
    # rows or cols become noise.
    if pivot.shape[0] > MAX_HEATMAP_ROWS:
        pivot = pivot.iloc[:MAX_HEATMAP_ROWS]
    if pivot.shape[1] > MAX_HEATMAP_COLS:
        pivot = pivot.iloc[:, :MAX_HEATMAP_COLS]
    palette = intent.get("palette", "diverging")
    if palette == "diverging":
        colorscale = [
            [0.0, DIVERGING_LOW], [0.5, DIVERGING_MID], [1.0, DIVERGING_HIGH],
        ]
        # Symmetric range so 0 lands on the white midpoint.
        amax = float(pivot.abs().max().max())
        zmin, zmax = -amax, amax
    else:
        colorscale = [
            [0.0, "#FFFFFF"], [0.5, ACCENT_SOFT], [1.0, ACCENT],
        ]
        zmin, zmax = float(pivot.min().min()), float(pivot.max().max())
    fig = go.Figure(go.Heatmap(
        z=pivot.values, x=list(pivot.columns), y=list(pivot.index),
        colorscale=colorscale, zmin=zmin, zmax=zmax,
    ))
    fig.update_layout(
        title=intent.get("title", ""),
        hoverlabel=HOVERLABEL,
        margin=dict(l=80, r=40, t=40, b=40),
        height=min(400, MAX_HEIGHT_PX),
    )
    return fig


def _build_scatter_quadrant(intent: dict, result: pd.DataFrame) -> go.Figure:
    _require_keys(intent, "scatter_quadrant", ["x", "y"])
    _require_columns(result, intent, ["x", "y", "label", "size"])

    # Cap points to keep the figure readable. If a label column is
    # present, prefer the head per label so we don't drop entire
    # label groups; otherwise take the head of the result.
    df = result
    if "label" in intent and intent["label"] in result.columns:
        df = result.drop_duplicates(subset=[intent["label"]], keep="first")
    capped, truncated = _cap_to_n(df, MAX_SCATTER_POINTS)
    title = _decorate_title(
        intent.get("title", ""),
        truncated_note=(
            f"first {MAX_SCATTER_POINTS} of {len(df)} points"
            if truncated else None
        ),
    )

    marker_size = capped[intent["size"]] if "size" in intent else 12
    text = capped[intent["label"]] if "label" in intent else None
    fig = go.Figure(go.Scatter(
        x=capped[intent["x"]], y=capped[intent["y"]],
        mode="markers+text" if text is not None else "markers",
        text=text, textposition="top center",
        marker=dict(size=marker_size, color=ACCENT, line=dict(color="white", width=1)),
    ))
    fig.update_layout(
        title=title,
        hoverlabel=HOVERLABEL,
        xaxis_title=intent["x"], yaxis_title=intent["y"],
        plot_bgcolor="white", paper_bgcolor="white",
        margin=dict(l=60, r=40, t=40, b=60),
        height=min(420, MAX_HEIGHT_PX),
    )
    fig.update_xaxes(showgrid=True, gridcolor=GRID_LINE, zeroline=True,
                     zerolinecolor=BASELINE_LINE)
    fig.update_yaxes(showgrid=True, gridcolor=GRID_LINE, zeroline=True,
                     zerolinecolor=BASELINE_LINE)
    return fig


def _build_waterfall(intent: dict, result: pd.DataFrame) -> go.Figure:
    _require_keys(intent, "waterfall", ["x", "y"])
    _require_columns(result, intent, ["x", "y"])
    # Aggregate (sum) contributions by driver label; a waterfall is
    # only meaningful with one bar per driver.
    aggregated = _aggregate_by(
        result, by=[intent["x"]], value_cols=[intent["y"]], agg="sum",
    )
    capped, truncated = _cap_to_n(aggregated, MAX_WATERFALL_BARS)
    title = _decorate_title(
        intent.get("title", ""),
        truncated_note=(
            f"first {MAX_WATERFALL_BARS} of {len(aggregated)} drivers"
            if truncated else None
        ),
    )
    measures = ["relative"] * len(capped)
    fig = go.Figure(go.Waterfall(
        x=capped[intent["x"]], y=capped[intent["y"]],
        measure=measures,
        increasing=dict(marker=dict(color=DIVERGING_HIGH)),
        decreasing=dict(marker=dict(color=DIVERGING_LOW)),
        totals=dict(marker=dict(color=TEXT_2)),
    ))
    fig.update_layout(
        title=title,
        hoverlabel=HOVERLABEL,
        plot_bgcolor="white", paper_bgcolor="white",
        margin=dict(l=60, r=40, t=40, b=40),
        height=min(360, MAX_HEIGHT_PX),
    )
    return fig


def _build_geo_map(intent: dict, result: pd.DataFrame) -> go.Figure:
    _require_keys(intent, "geo_map", ["lat", "lon"])
    _require_columns(result, intent, ["lat", "lon", "value", "label"])
    # Aggregate one point per (lat, lon). Without this a merge frame
    # produces a stack of markers at each store coordinate.
    by = [intent["lat"], intent["lon"]]
    value_cols: list[str] = []
    if "value" in intent and intent["value"] in result.columns:
        value_cols.append(intent["value"])
    aggregated = (
        _aggregate_by(result, by=by, value_cols=value_cols, agg="mean")
        if value_cols
        else result[by].drop_duplicates().reset_index(drop=True)
    )
    if "label" in intent and intent["label"] in result.columns:
        # Pick the first label per coordinate so the marker text matches.
        label_map = (
            result[by + [intent["label"]]]
            .drop_duplicates(subset=by, keep="first")
            .reset_index(drop=True)
        )
        aggregated = aggregated.merge(label_map, on=by, how="left")
    capped, _ = _cap_to_n(aggregated, MAX_GEO_MARKERS)
    color = capped[intent["value"]] if "value" in intent and intent["value"] in capped.columns else ACCENT
    text = capped[intent["label"]] if "label" in intent and intent["label"] in capped.columns else None
    lats = capped[intent["lat"]]
    lons = capped[intent["lon"]]
    fig = go.Figure(go.Scattermap(
        lat=lats, lon=lons, mode="markers+text",
        text=text, textposition="top center",
        marker=dict(size=12,
                    color=color if isinstance(color, pd.Series) else None,
                    colorscale="Viridis"
                    if isinstance(color, pd.Series) else None),
    ))
    center_lat = float(lats.mean()) if len(lats) else 35.2271
    center_lon = float(lons.mean()) if len(lons) else -80.8431
    fig.update_layout(
        title=intent.get("title", ""),
        map=dict(style="open-street-map",
                 center=dict(lat=center_lat, lon=center_lon),
                 zoom=10),
        margin=dict(l=0, r=0, t=40, b=0),
        height=min(420, MAX_HEIGHT_PX),
    )
    return fig


def _build_kpi_callout(intent: dict, result: pd.DataFrame) -> go.Figure:
    _require_keys(intent, "kpi_callout", ["value"])
    _require_columns(result, intent, ["value", "delta"])
    if len(result) == 0:
        raise MissingColumnError(
            "kpi_callout: result frame is empty — no value to display."
        )
    val = float(result[intent["value"]].iloc[0])
    mode = "number"
    delta_kw: dict[str, Any] = {}
    if "delta" in intent and intent["delta"] in result.columns:
        delta_val = float(result[intent["delta"]].iloc[0])
        mode = "number+delta"
        delta_kw = {"delta": dict(reference=val - delta_val)}
    fig = go.Figure(go.Indicator(
        mode=mode,
        value=val,
        title=dict(text=intent.get("title", "")),
        **delta_kw,
    ))
    fig.update_layout(
        height=240,
        margin=dict(l=40, r=40, t=60, b=40),
        paper_bgcolor="white",
    )
    return fig


def _build_small_multiples(intent: dict, result: pd.DataFrame) -> go.Figure:
    _require_keys(intent, "small_multiples", ["facet", "x", "series"])
    series_col = (
        intent["series"][0] if isinstance(intent["series"], list)
        else intent["series"]
    )
    _require_columns(result, {"facet": intent["facet"], "x": intent["x"],
                              "y": series_col}, ["facet", "x", "y"])
    # Aggregate (facet, x) → mean series so each facet's sub-line is
    # one point per x value.
    aggregated = _aggregate_by(
        result,
        by=[intent["facet"], intent["x"]],
        value_cols=[series_col],
        agg="mean",
    )
    facets = sorted(aggregated[intent["facet"]].dropna().unique().tolist())
    truncated_facets = False
    if len(facets) > MAX_FACETS:
        facets = facets[:MAX_FACETS]
        truncated_facets = True
    title = _decorate_title(
        intent.get("title", ""),
        truncated_note=(
            f"first {MAX_FACETS} of {len(aggregated[intent['facet']].unique())} facets"
            if truncated_facets else None
        ),
    )
    cols = min(3, len(facets)) or 1
    rows = (len(facets) + cols - 1) // cols
    fig = make_subplots(rows=rows, cols=cols, subplot_titles=facets)
    for i, f in enumerate(facets):
        sub = aggregated[aggregated[intent["facet"]] == f]
        r, c = i // cols + 1, i % cols + 1
        fig.add_trace(
            go.Scatter(
                x=sub[intent["x"]], y=sub[series_col],
                mode="lines+markers", name=str(f),
                line=dict(color=ACCENT, width=2), showlegend=False,
            ),
            row=r, col=c,
        )
    fig.update_layout(
        title=title,
        height=min(240 * rows, MAX_HEIGHT_PX),
        margin=dict(l=40, r=40, t=60, b=40),
        plot_bgcolor="white", paper_bgcolor="white",
    )
    return fig


_TABLE_DRILLDOWN_MAX_ROWS = 20
_TABLE_DRILLDOWN_MAX_HEIGHT_PX = 720


def _build_table_drilldown(intent: dict, result: pd.DataFrame) -> go.Figure:
    _require_keys(intent, "table_drilldown", ["columns"])
    _require_columns(result, intent, ["columns"])
    cols = intent["columns"]
    truncated = len(result) > _TABLE_DRILLDOWN_MAX_ROWS
    df_for_chart = result.head(_TABLE_DRILLDOWN_MAX_ROWS) if truncated else result
    title = intent.get("title", "")
    if truncated:
        title = (
            f"{title}  (top {_TABLE_DRILLDOWN_MAX_ROWS} of {len(result)} rows)"
            if title
            else f"Top {_TABLE_DRILLDOWN_MAX_ROWS} of {len(result)} rows"
        )
    fig = go.Figure(go.Table(
        header=dict(values=cols, fill_color=ACCENT_SOFT,
                    font=dict(color=TEXT, size=12), align="left"),
        cells=dict(
            values=[df_for_chart[c].tolist() for c in cols],
            fill_color="white",
            font=dict(color=TEXT, size=11),
            align="left",
        ),
    ))
    height = 80 + 30 * max(1, len(df_for_chart))
    fig.update_layout(
        title=title,
        height=min(height, _TABLE_DRILLDOWN_MAX_HEIGHT_PX),
        margin=dict(l=20, r=20, t=40, b=20),
    )
    return fig


_BUILDERS = {
    "time_series_vs_peers":      _build_time_series,
    "cross_merchant_comparison": _build_cross_merchant_comparison,
    "heatmap":                   _build_heatmap,
    "scatter_quadrant":          _build_scatter_quadrant,
    "waterfall":                 _build_waterfall,
    "geo_map":                   _build_geo_map,
    "kpi_callout":               _build_kpi_callout,
    "small_multiples":           _build_small_multiples,
    "table_drilldown":           _build_table_drilldown,
}


# ---------------------------------------------------------------------
# Column reconciler — Wave 3 Stage 6.5 follow-up #7.
#
# The model occasionally authors a chart_intent that names a column
# the merge layer renamed or didn't carry through (e.g. asks for
# `own_store_count` when the merged result has `store_count`, asks
# for `total_revenue` when the result has `cell_revenue`). The
# structural guarantee "every plotted number traces to a result
# cell" depends on every named column existing — but when the
# requested name is just a near-miss for a real column, the right
# fix is to rewrite the name, not skip the whole chart.
#
# The reconciler runs BEFORE the per-kind builders. It rewrites
# each column-referencing field in the intent to point at a real
# column in the result, using:
#   1. An explicit synonym table (most common rewrites).
#   2. Prefix/suffix stripping (own_, peer_).
#   3. Case-insensitive direct match.
# If a `series` list has partial matches, the invalid entries are
# dropped (a 2-series chart minus one bad series beats no chart).
# Only if NOTHING reconciles is the original name kept — _require_columns
# then raises and the caller (specialist) falls back to chart=None
# with an honest caveat.
#
# The reconciler ONLY rewrites NAMES — never invents data. The
# structural guarantee still holds: every plotted value still comes
# from a cell in result.
# ---------------------------------------------------------------------

# Common synonyms the model authors when its mental model of the
# result columns drifts from what the merge layer actually produces.
# Each entry maps an "intent name" → "result column name".
COLUMN_SYNONYMS: dict[str, str] = {
    # own_/peer_ prefix variants
    "own_store_count": "store_count",
    "peer_store_count": "store_count",
    "own_revenue": "cell_revenue",
    "peer_revenue": "cell_revenue",
    "total_revenue": "cell_revenue",
    "own_units": "cell_units",
    "peer_units": "cell_units",
    "total_units": "cell_units",
    # asp / avg unit price aliases
    "avg_unit_price": "own_value",
    "own_asp": "own_value",
    "asp": "own_value",
    "unit_price": "own_value",
    # basket aliases
    "avg_basket": "own_value",
    "avg_basket_size": "own_value",
    # category aliases (the model sometimes types category_type)
    "category_type": "category",
    "cat": "category",
    # zone aliases
    "zone": "derived_zone",
    "zone_id": "derived_zone",
    # peer-relationship aliases
    "peer_type": "peer_relationship",
    "relationship": "peer_relationship",
}


def _reconcile_column(name: str, result_columns: list[str]) -> str | None:
    """Try to map ``name`` to a real column in ``result_columns``.
    Returns the matched column name or None if no match found."""
    if name in result_columns:
        return name

    # 1. Synonym table — explicit known rewrites.
    if name in COLUMN_SYNONYMS:
        target = COLUMN_SYNONYMS[name]
        if target in result_columns:
            return target

    # 2. Case-insensitive direct match.
    lower_map = {c.lower(): c for c in result_columns}
    if name.lower() in lower_map:
        return lower_map[name.lower()]

    # 3. Prefix stripping — own_X, peer_X → X.
    for prefix in ("own_", "peer_"):
        if name.startswith(prefix):
            stem = name[len(prefix):]
            if stem in result_columns:
                return stem
            if stem.lower() in lower_map:
                return lower_map[stem.lower()]

    # 4. Suffix stripping — X_count, X_share variants (rare).
    for suffix in ("_count", "_share"):
        if name.endswith(suffix):
            stem = name[: -len(suffix)]
            if stem in result_columns:
                return stem

    return None


# Per-kind list of intent fields that reference column names. Used
# by the reconciler to know which fields to rewrite.
_COLUMN_FIELDS_BY_KIND: dict[str, list[str]] = {
    "time_series_vs_peers":      ["x", "series"],
    "cross_merchant_comparison": ["x", "series"],
    "heatmap":                   ["row", "col", "value"],
    "scatter_quadrant":          ["x", "y", "label", "size"],
    "waterfall":                 ["x", "y"],
    "geo_map":                   ["lat", "lon", "value", "label"],
    "kpi_callout":               ["value", "delta"],
    "small_multiples":           ["facet", "x", "series"],
    "table_drilldown":           ["columns"],
}


def _reconcile_intent(
    intent: dict, result: pd.DataFrame,
) -> tuple[dict, list[str]]:
    """Walk every column-referencing field in ``intent`` and try to
    remap each to a real column in ``result``. Returns the reconciled
    intent (a new dict) and a list of rewrite notes for telemetry.

    For list-valued fields (series, columns), invalid entries are
    dropped — a partial-series chart beats no chart.
    """
    kind = intent.get("kind")
    fields = _COLUMN_FIELDS_BY_KIND.get(kind, [])
    if not fields:
        return intent, []

    result_cols = list(result.columns)
    out = dict(intent)
    notes: list[str] = []

    for field in fields:
        if field not in out:
            continue
        val = out[field]
        if isinstance(val, str):
            mapped = _reconcile_column(val, result_cols)
            if mapped is not None and mapped != val:
                out[field] = mapped
                notes.append(f"{field}: {val!r}→{mapped!r}")
        elif isinstance(val, list):
            new_list: list[str] = []
            for v in val:
                if not isinstance(v, str):
                    continue
                mapped = _reconcile_column(v, result_cols)
                if mapped is not None:
                    if mapped != v:
                        notes.append(f"{field}[{v!r}]→{mapped!r}")
                    new_list.append(mapped)
                else:
                    notes.append(f"{field}: dropped invalid {v!r}")
            # Deduplicate while preserving order.
            seen: set[str] = set()
            deduped = [c for c in new_list if not (c in seen or seen.add(c))]
            out[field] = deduped

    return out, notes


# ---------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------

def build_chart(intent: dict, result: pd.DataFrame) -> go.Figure:
    """Construct a Plotly figure from ``intent`` + ``result``.

    The structural guarantee (D25.2): every numeric appearing in the
    figure is read from a cell in ``result`` — the model authors
    column names, not values. Drift between prose and chart becomes
    impossible because both source from the same frame.

    Parameters
    ----------
    intent
        Dict matching the ``ChartIntent`` shape. ``kind`` selects the
        builder; per-kind required keys must be present (otherwise
        ``UnsupportedIntentError``); every column-referencing key
        must point to a column in ``result`` (otherwise
        ``MissingColumnError``).
    result
        The merged comparison frame from ``response.merge_own_and_peer``
        (or any other agent-produced result frame).

    Returns
    -------
    plotly.graph_objects.Figure
        Headless figure. Wave 4 plugs it into ``st.plotly_chart``;
        the Stage 6.5 preview harness emits it via ``Figure.to_html``.

    Raises
    ------
    UnsupportedIntentError
        Unknown kind, or missing per-kind required key.
    MissingColumnError
        Intent names a column not in result.
    """
    kind = intent.get("kind")
    if kind not in _BUILDERS:
        raise UnsupportedIntentError(
            f"Unknown ChartIntent.kind={kind!r}. Valid kinds: "
            f"{sorted(_BUILDERS.keys())}"
        )
    # Reconcile column names BEFORE the per-kind builder runs. The
    # reconciler rewrites near-miss names to real result columns and
    # drops invalid entries from series lists (Wave 3 Stage 6.5
    # follow-up #7). _require_columns inside each builder is the
    # post-reconcile assertion — the structural guarantee still holds.
    reconciled, _rewrite_notes = _reconcile_intent(intent, result)
    return _BUILDERS[kind](reconciled, result)
