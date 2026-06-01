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
    missing = [k for k in required if k not in intent or intent[k] in (None, "")]
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
# Per-kind builders
# ---------------------------------------------------------------------

def _build_time_series(intent: dict, result: pd.DataFrame) -> go.Figure:
    _require_keys(intent, "time_series_vs_peers", ["x", "series"])
    _require_columns(result, intent, ["x", "series"])

    fig = go.Figure()
    palette_cycle = [ACCENT, PEER_A, PEER_B, PEER_AGGREGATE]
    for i, col in enumerate(intent["series"]):
        is_own = i == 0
        fig.add_trace(go.Scatter(
            x=result[intent["x"]],
            y=result[col],
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
        title=intent.get("title", ""),
        hoverlabel=HOVERLABEL,
        yaxis_title=_y_axis_title(intent.get("y_format")),
        hovermode="x unified",
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=40, r=40, t=40, b=40),
        height=360,
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

    fig = go.Figure()
    palette_cycle = [ACCENT, PEER_A, PEER_B, PEER_AGGREGATE]
    for i, col in enumerate(intent["series"]):
        fig.add_trace(go.Bar(
            y=result[intent["x"]],
            x=result[col],
            name=col,
            orientation="h",
            marker=dict(color=palette_cycle[i % len(palette_cycle)]),
        ))
    fig.update_layout(
        title=intent.get("title", ""),
        hoverlabel=HOVERLABEL,
        xaxis_title=_y_axis_title(intent.get("y_format")),
        barmode="group",
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=80, r=40, t=40, b=40),
        height=max(220, 60 * len(result)),
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
        height=400,
    )
    return fig


def _build_scatter_quadrant(intent: dict, result: pd.DataFrame) -> go.Figure:
    _require_keys(intent, "scatter_quadrant", ["x", "y"])
    _require_columns(result, intent, ["x", "y", "label", "size"])

    marker_size = result[intent["size"]] if "size" in intent else 12
    text = result[intent["label"]] if "label" in intent else None
    fig = go.Figure(go.Scatter(
        x=result[intent["x"]], y=result[intent["y"]],
        mode="markers+text" if text is not None else "markers",
        text=text, textposition="top center",
        marker=dict(size=marker_size, color=ACCENT, line=dict(color="white", width=1)),
    ))
    fig.update_layout(
        title=intent.get("title", ""),
        hoverlabel=HOVERLABEL,
        xaxis_title=intent["x"], yaxis_title=intent["y"],
        plot_bgcolor="white", paper_bgcolor="white",
        margin=dict(l=60, r=40, t=40, b=60), height=420,
    )
    fig.update_xaxes(showgrid=True, gridcolor=GRID_LINE, zeroline=True,
                     zerolinecolor=BASELINE_LINE)
    fig.update_yaxes(showgrid=True, gridcolor=GRID_LINE, zeroline=True,
                     zerolinecolor=BASELINE_LINE)
    return fig


def _build_waterfall(intent: dict, result: pd.DataFrame) -> go.Figure:
    _require_keys(intent, "waterfall", ["x", "y"])
    _require_columns(result, intent, ["x", "y"])
    measures = ["relative"] * len(result)
    fig = go.Figure(go.Waterfall(
        x=result[intent["x"]], y=result[intent["y"]],
        measure=measures,
        increasing=dict(marker=dict(color=DIVERGING_HIGH)),
        decreasing=dict(marker=dict(color=DIVERGING_LOW)),
        totals=dict(marker=dict(color=TEXT_2)),
    ))
    fig.update_layout(
        title=intent.get("title", ""),
        hoverlabel=HOVERLABEL,
        plot_bgcolor="white", paper_bgcolor="white",
        margin=dict(l=60, r=40, t=40, b=40), height=360,
    )
    return fig


def _build_geo_map(intent: dict, result: pd.DataFrame) -> go.Figure:
    _require_keys(intent, "geo_map", ["lat", "lon"])
    _require_columns(result, intent, ["lat", "lon", "value", "label"])
    color = result[intent["value"]] if "value" in intent else ACCENT
    text = result[intent["label"]] if "label" in intent else None
    lats = result[intent["lat"]]
    lons = result[intent["lon"]]
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
        height=420,
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
    facets = sorted(result[intent["facet"]].dropna().unique().tolist())
    cols = min(3, len(facets)) or 1
    rows = (len(facets) + cols - 1) // cols
    fig = make_subplots(rows=rows, cols=cols, subplot_titles=facets)
    for i, f in enumerate(facets):
        sub = result[result[intent["facet"]] == f]
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
        title=intent.get("title", ""),
        height=240 * rows,
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
    return _BUILDERS[kind](intent, result)
