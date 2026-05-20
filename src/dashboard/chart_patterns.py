"""Chart pattern rendering helpers for the v3 dashboard and agent
chat surfaces.

Each pattern function takes pattern-specific data + metadata (axis
labels, takeaway template, interactivity hooks) and renders directly
to Streamlit (typically via ``st.plotly_chart`` or ``st_folium``).

The 9 patterns documented in ``chart_patterns.md``:

1. Time-series-vs-peers (line chart with peer overlays)
2. Cross-merchant comparison (horizontal bars)
3. Cross-merchant heatmap (diverging or sequential)
4. Scatter with peer context
5. Decomposition / waterfall
6. Geographic map (Folium)
7. Small-multiples
8. Single-number callout / KPI
9. Table-with-drilldown

Phase 4.1 implements Pattern 1 only. Subsequent phases add the rest.
"""
from __future__ import annotations

import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st


# Plotly needs Python-string colors (CSS variables can't reach the
# library). These mirror the values defined in styling.py — if either
# moves, both should. Phase 4 close-out can consider consolidating.
ACCENT          = "#0F4C81"   # own merchant brand color
PEER_A          = "#6B7280"   # medium gray
PEER_B          = "#9CA3AF"   # lighter gray
PEER_AGGREGATE  = "#4B5563"   # darker gray (for aggregate peer overlays)
BASELINE_LINE   = "rgba(128, 128, 128, 0.4)"
GRID_LINE       = "rgba(128, 128, 128, 0.10)"

# Diverging palette (Pattern 3 heatmaps, Pattern 5 waterfall, Pattern
# 2 diverging mode for D3-style fingerprints).
DIVERGING_LOW   = "#C44536"   # red — own below peer / under-indexed
DIVERGING_MID   = "#FFFFFF"   # white — on baseline
DIVERGING_HIGH  = "#0F4C81"   # blue — own above peer / over-indexed


# ---------------------------------------------------------------------------
# Peer display labels
#
# Internal data keys keep the lake's raw `peer_a` / `peer_b` identifiers
# (consistent with lake_transactions.peer_id values from Phase 1.5).
# User-facing strings — legends, axis ticks, hover tooltips, takeaway
# subtitles — abstract these to "Peer A" / "Peer B". The demo framing
# is "you vs the market", not "you vs ACM specifically"; abstracted
# labels keep the cross-merchant story working even if the merchant
# selector hides which grocer is which peer.
# ---------------------------------------------------------------------------

PEER_DISPLAY = {
    "peer_a":         "Peer A",
    "peer_b":         "Peer B",
    "peer_aggregate": "Peers (avg)",
}


def peer_display(label: str) -> str:
    """Convert an internal peer id (``peer_a``) to its user-facing
    display form (``Peer A``). Unknown labels fall through unchanged."""
    return PEER_DISPLAY.get(label, label)


# ---------------------------------------------------------------------------
# Pattern 1 — Time-series-vs-peers
# ---------------------------------------------------------------------------

def render_time_series_vs_peers(
    data: dict,
    *,
    title: str,
    takeaway: str,
    own_color: str = ACCENT,
    show_peers: bool = True,
    own_label: str = "You",
    height: int = 360,
) -> None:
    """Pattern 1 — line chart with own merchant + optional peer overlays.

    Args:
        data: dict with keys
            - ``weeks``: list of week-starting date strings
            - ``own``: list of metric values, same length as ``weeks``
            - ``peer_a`` (optional): list of values for peer_a
            - ``peer_b`` (optional): list of values for peer_b
          Values are typically normalized to baseline = 100. ``None``
          entries in a series are passed to Plotly as NaN; the line
          breaks across the gap rather than interpolating.
        title: chart title (rendered above the takeaway).
        takeaway: pre-computed takeaway sentence (rendered as subtitle).
        own_color: brand color for own merchant line.
        show_peers: when False, render own series only (no peer lines).
        own_label: legend label for the own merchant series.
        height: chart pixel height. Default 360 fits the chat panel
                without scrolling.

    Renders directly via ``st.plotly_chart``. No return value.
    """
    if not data.get("weeks"):
        st.caption("_No data available for this view._")
        return

    fig = go.Figure()

    if show_peers and data.get("peer_a") is not None:
        fig.add_trace(go.Scatter(
            x=data["weeks"],
            y=data["peer_a"],
            mode="lines+markers",
            name=peer_display("peer_a"),
            line=dict(color=PEER_A, dash="dash", width=2),
            marker=dict(size=6, color=PEER_A),
            connectgaps=False,
        ))
    if show_peers and data.get("peer_b") is not None:
        fig.add_trace(go.Scatter(
            x=data["weeks"],
            y=data["peer_b"],
            mode="lines+markers",
            name=peer_display("peer_b"),
            line=dict(color=PEER_B, dash="dot", width=2),
            marker=dict(size=6, color=PEER_B),
            connectgaps=False,
        ))

    # Own merchant last so it z-renders on top of peers.
    fig.add_trace(go.Scatter(
        x=data["weeks"],
        y=data["own"],
        mode="lines+markers",
        name=own_label,
        line=dict(color=own_color, width=3),
        marker=dict(size=8, color=own_color),
        connectgaps=False,
    ))

    # Baseline reference line at index = 100 (only meaningful when
    # the chart is normalized). Subtle, behind the data.
    fig.add_hline(
        y=100,
        line_dash="dot",
        line_color=BASELINE_LINE,
        annotation_text="Baseline",
        annotation_position="right",
        annotation_font_size=10,
        annotation_font_color="rgba(128, 128, 128, 0.7)",
    )

    fig.update_layout(
        xaxis_title=None,
        yaxis_title="Index (baseline = 100)",
        hovermode="x unified",
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=40, r=40, t=24, b=40),
        height=height,
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="bottom", y=1.02,
            xanchor="left",   x=0,
        ),
    )
    fig.update_xaxes(showgrid=False, zeroline=False, tickfont=dict(size=10))
    fig.update_yaxes(
        showgrid=True,
        gridcolor=GRID_LINE,
        zeroline=False,
        tickfont=dict(size=10),
    )

    # Title + takeaway above the chart. The chart helper renders both
    # so callers don't have to coordinate spacing.
    _render_card_header(title, takeaway)
    st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
# Pattern 2 — Cross-merchant comparison, single dimension
# ---------------------------------------------------------------------------

def _render_card_header(title: str, takeaway: str) -> None:
    """Standard title + takeaway-subtitle block above a chart."""
    st.markdown(f"**{title}**")
    if takeaway:
        st.markdown(
            f"<p style='color:#4B5563;font-size:13px;margin:-4px 0 8px 0;'>"
            f"{takeaway}</p>",
            unsafe_allow_html=True,
        )


def render_cross_merchant_comparison(
    data: dict,
    *,
    title: str,
    takeaway: str,
    mode: str = "standard",
    own_color: str = ACCENT,
    height: int | None = None,
) -> None:
    """Pattern 2 — horizontal-bar comparison across a single dimension.

    Modes:

    - ``"standard"`` — grouped horizontal bars, one group per category,
      bars per merchant series (own + optional peer_a / peer_b).
      ``data`` keys: ``categories`` (list[str]), ``own`` (list[float],
      optional), ``peer_a`` / ``peer_b`` (optional lists).
    - ``"diverging"`` — single series of bars extending positive or
      negative from a zero baseline. Positive = ACCENT (own
      over-indexed), negative = DIVERGING_LOW (own under-indexed).
      ``data`` keys: ``categories``, ``deltas`` (list[float]).
    - ``"two_panel"`` — two stacked subplots, each in standard mode.
      Vertical layout chosen over side-by-side because the chat
      panel is 35 % of viewport — side-by-side panels would compress
      labels. ``data`` keys: ``panel_a_title``, ``panel_a_data``,
      ``panel_b_title``, ``panel_b_data``.
    """
    if mode == "two_panel":
        _render_two_panel(data, title=title, takeaway=takeaway, height=height)
        return
    if mode == "diverging":
        _render_diverging(data, title=title, takeaway=takeaway, height=height)
        return
    if mode == "standard":
        _render_standard(data, title=title, takeaway=takeaway,
                         own_color=own_color, height=height)
        return
    raise ValueError(f"Unknown render_cross_merchant_comparison mode: {mode!r}")


def _series_traces_for_panel(panel: dict) -> list[go.Bar]:
    """Build the ordered list of go.Bar traces for a single panel.

    Order: peer_a (background) → peer_b → own (front) so own renders
    visually on top when bars overlap.
    """
    cats = panel["categories"]
    traces: list[go.Bar] = []
    if panel.get("peer_a_gaps") is not None or panel.get("peer_a") is not None:
        vals = panel.get("peer_a_gaps") or panel.get("peer_a")
        traces.append(go.Bar(
            y=cats, x=vals, orientation="h",
            name=peer_display("peer_a"),
            marker=dict(color=PEER_A),
        ))
    if panel.get("peer_b_gaps") is not None or panel.get("peer_b") is not None:
        vals = panel.get("peer_b_gaps") or panel.get("peer_b")
        traces.append(go.Bar(
            y=cats, x=vals, orientation="h",
            name=peer_display("peer_b"),
            marker=dict(color=PEER_B),
        ))
    if panel.get("own") is not None:
        traces.append(go.Bar(
            y=cats, x=panel["own"], orientation="h",
            name="You",
            marker=dict(color=ACCENT),
        ))
    return traces


def _render_two_panel(data: dict, *, title: str, takeaway: str,
                       height: int | None) -> None:
    pa = data["panel_a_data"]
    pb = data["panel_b_data"]
    # Row-height proportional to category count so each bar has roughly
    # the same vertical pitch.
    n_a, n_b = len(pa["categories"]), len(pb["categories"])
    row_heights = [max(n_a, 1), max(n_b, 1)]
    total = sum(row_heights)
    fig = make_subplots(
        rows=2, cols=1,
        subplot_titles=(data["panel_a_title"], data["panel_b_title"]),
        row_heights=[h / total for h in row_heights],
        vertical_spacing=0.12,
    )
    # Panel A traces (legend only on this panel to avoid duplicates).
    for trace in _series_traces_for_panel(pa):
        trace.showlegend = True
        trace.legendgroup = trace.name
        fig.add_trace(trace, row=1, col=1)
    # Panel B traces share legendgroup so the legend stays unified.
    for trace in _series_traces_for_panel(pb):
        trace.showlegend = False
        trace.legendgroup = trace.name
        fig.add_trace(trace, row=2, col=1)
    # Vertical reference line at 0 on both panels.
    fig.add_vline(x=0, line_dash="dot", line_color=BASELINE_LINE, row=1, col=1)
    fig.add_vline(x=0, line_dash="dot", line_color=BASELINE_LINE, row=2, col=1)
    fig.update_layout(
        barmode="group",
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=80, r=24, t=44, b=32),
        height=height or (60 + 22 * total + 60),
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="bottom", y=1.04,
            xanchor="left",   x=0,
        ),
    )
    fig.update_xaxes(showgrid=True, gridcolor=GRID_LINE,
                     ticksuffix="%", tickfont=dict(size=10))
    fig.update_yaxes(showgrid=False, tickfont=dict(size=11), automargin=True)
    _render_card_header(title, takeaway)
    st.plotly_chart(fig, use_container_width=True)


def _render_diverging(data: dict, *, title: str, takeaway: str,
                       height: int | None) -> None:
    cats = data["categories"]
    deltas = data["deltas"]
    colors = [
        DIVERGING_HIGH if d is not None and d > 0
        else DIVERGING_LOW if d is not None and d < 0
        else GRID_LINE
        for d in deltas
    ]
    fig = go.Figure(go.Bar(
        y=cats, x=deltas, orientation="h",
        marker=dict(color=colors),
        hovertemplate="<b>%{y}</b>: %{x:+.1f}pp<extra></extra>",
    ))
    fig.add_vline(x=0, line_dash="dot", line_color=BASELINE_LINE)
    fig.update_layout(
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=80, r=24, t=12, b=32),
        height=height or max(220, 26 * len(cats) + 80),
        showlegend=False,
    )
    fig.update_xaxes(showgrid=True, gridcolor=GRID_LINE,
                     ticksuffix="pp", tickfont=dict(size=10))
    fig.update_yaxes(showgrid=False, tickfont=dict(size=11),
                     automargin=True, autorange="reversed")
    _render_card_header(title, takeaway)
    st.plotly_chart(fig, use_container_width=True)


def _render_standard(data: dict, *, title: str, takeaway: str,
                      own_color: str, height: int | None) -> None:
    fig = go.Figure()
    for trace in _series_traces_for_panel(data):
        fig.add_trace(trace)
    fig.add_vline(x=0, line_dash="dot", line_color=BASELINE_LINE)
    fig.update_layout(
        barmode="group",
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=80, r=24, t=12, b=32),
        height=height or max(220, 26 * len(data["categories"]) + 80),
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.04,
                     xanchor="left", x=0),
    )
    fig.update_xaxes(showgrid=True, gridcolor=GRID_LINE, tickfont=dict(size=10))
    fig.update_yaxes(showgrid=False, tickfont=dict(size=11),
                     automargin=True, autorange="reversed")
    _render_card_header(title, takeaway)
    st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
# Pattern 3 — Cross-merchant heatmap
# ---------------------------------------------------------------------------

def render_heatmap(
    data: dict,
    *,
    title: str,
    takeaway: str,
    mode: str = "cross_merchant_diverging",
    height: int | None = None,
) -> None:
    """Pattern 3 — heatmap with three modes.

    Modes:

    - ``"cross_merchant_diverging"`` — diverging red-white-blue
      color scale, symmetric around zero. Cells are typically
      percentage gaps (own vs peer). Red = own below peer, blue =
      own above peer. Cell text overlays the gap value.
      ``data`` keys: ``rows`` (list[str]), ``cols`` (list[str]),
      ``cells`` (2D list[float|None] aligned to rows × cols).
      ``None`` cells render transparent (k=5 suppression).

    - ``"own_only_diverging"`` — Phase 4.3 work (T-A3, R-A3). Not
      yet implemented.
    - ``"own_only_sequential"`` — Phase 4.4 work (Card 2.3). Not
      yet implemented.
    """
    if mode == "cross_merchant_diverging":
        _render_heatmap_cross_merchant_diverging(
            data, title=title, takeaway=takeaway, height=height,
        )
        return
    raise NotImplementedError(
        f"render_heatmap mode {mode!r} not implemented "
        f"(Phase 4.2b implements cross_merchant_diverging only)."
    )


def _render_heatmap_cross_merchant_diverging(
    data: dict,
    *,
    title: str,
    takeaway: str,
    height: int | None,
) -> None:
    rows = data["rows"]
    cols = data["cols"]
    cells = data["cells"]
    if not rows or not cols:
        _render_card_header(title, takeaway)
        st.caption("_No data available for this view._")
        return

    # Plotly heatmap treats None as missing → cell renders blank.
    # Text overlay uses an em-dash for suppressed cells.
    text = [
        [f"{v:+.1f}%" if v is not None else "—" for v in row]
        for row in cells
    ]

    # Symmetric color range around zero so red and blue have equal
    # saturation magnitude regardless of the actual data range.
    flat = [v for row in cells for v in row if v is not None]
    abs_max = max((abs(v) for v in flat), default=1.0)
    # Floor at 1.0 so very small gaps don't show as fully saturated.
    abs_max = max(abs_max, 1.0)

    fig = go.Figure(go.Heatmap(
        z=cells,
        x=[peer_display(c) for c in cols],
        y=rows,
        text=text,
        texttemplate="%{text}",
        textfont=dict(size=11, color="#1A1F2E"),
        colorscale=[
            [0.0, DIVERGING_LOW],
            [0.5, DIVERGING_MID],
            [1.0, DIVERGING_HIGH],
        ],
        zmid=0,
        zmin=-abs_max,
        zmax=abs_max,
        hoverongaps=False,
        hovertemplate="<b>%{y}</b> vs %{x}<br>%{text}<extra></extra>",
        colorbar=dict(
            ticksuffix="%",
            tickformat=".0f",
            thickness=10,
            outlinewidth=0,
        ),
        xgap=2, ygap=2,  # subtle gridlines between cells
    ))
    fig.update_layout(
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=80, r=20, t=20, b=24),
        height=height or max(280, 30 * len(rows) + 80),
    )
    fig.update_xaxes(tickfont=dict(size=11), side="top")
    # autorange="reversed" so the first row in `rows` appears at the
    # top of the heatmap (default Plotly puts y=0 at the bottom).
    fig.update_yaxes(tickfont=dict(size=11), automargin=True,
                     autorange="reversed")

    _render_card_header(title, takeaway)
    st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
# Pattern 4 — Scatter with peer context
# ---------------------------------------------------------------------------

def render_scatter_with_peers(
    data: dict,
    *,
    title: str,
    takeaway: str,
    height: int | None = None,
) -> None:
    """Pattern 4 — scatter plot with optional reference lines.

    Each ``point`` becomes a bubble whose marker area is proportional
    to ``size``. Used by P3 (volume × pricing-gap quadrants) and D4
    (own share vs peer share, with 45° parity line).

    ``data`` keys:

        points: list of dicts, each with
            ``label`` (str), ``x`` (float), ``y`` (float),
            ``size`` (float)
        x_label, y_label:        axis labels
        x_zero_line (bool):      draw a dotted vertical reference at x=0
        y_baseline (float|None): draw a dotted horizontal reference here
        show_45_degree_line:     draw a dashed y=x parity line
    """
    points = data.get("points") or []
    if not points:
        _render_card_header(title, takeaway)
        st.caption("_No data available for this view._")
        return

    import math
    sizes = [float(p["size"]) for p in points]
    s_min, s_max = min(sizes), max(sizes)

    def _to_marker_size(s: float) -> float:
        # Square-root scaling for area-proportional markers, capped to a
        # 6–34 px range so the smallest bubbles stay readable and the
        # largest don't dwarf the chart.
        if s_max <= s_min:
            return 14.0
        return 6.0 + 28.0 * math.sqrt(max(0.0, (s - s_min) / (s_max - s_min)))

    xs = [float(p["x"]) for p in points]
    ys = [float(p["y"]) for p in points]
    labels = [str(p["label"]) for p in points]
    marker_sizes = [_to_marker_size(s) for s in sizes]

    # Reference lines drawn before the main trace so they sit behind
    # the bubbles. Plotly handles z-order in trace-add order.
    fig = go.Figure()

    if data.get("show_45_degree_line"):
        # Span both axes so the line covers the full visible data range.
        lo = min(min(xs), min(ys))
        hi = max(max(xs), max(ys))
        pad = (hi - lo) * 0.08 if hi > lo else 1.0
        fig.add_trace(go.Scatter(
            x=[lo - pad, hi + pad],
            y=[lo - pad, hi + pad],
            mode="lines",
            line=dict(color=BASELINE_LINE, dash="dash", width=1.5),
            name="parity",
            showlegend=False,
            hoverinfo="skip",
        ))

    fig.add_trace(go.Scatter(
        x=xs, y=ys,
        mode="markers+text",
        marker=dict(
            size=marker_sizes,
            color=ACCENT,
            opacity=0.55,
            line=dict(width=1, color=ACCENT),
        ),
        text=labels,
        textposition="top center",
        textfont=dict(size=10, color="#4A5161"),
        customdata=[[s] for s in sizes],
        hovertemplate=(
            "<b>%{text}</b><br>"
            f"{data['x_label']}: %{{x:.2f}}<br>"
            f"{data['y_label']}: %{{y:.2f}}<br>"
            "Size: %{customdata[0]:,.0f}<extra></extra>"
        ),
    ))

    if data.get("x_zero_line"):
        fig.add_vline(x=0, line_dash="dot", line_color=BASELINE_LINE)
    y_baseline = data.get("y_baseline")
    if y_baseline is not None:
        fig.add_hline(y=y_baseline, line_dash="dot", line_color=BASELINE_LINE)

    fig.update_layout(
        xaxis_title=data["x_label"],
        yaxis_title=data["y_label"],
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=64, r=24, t=12, b=48),
        height=height or 380,
        showlegend=False,
        hovermode="closest",
    )
    fig.update_xaxes(showgrid=True, gridcolor=GRID_LINE, zeroline=False,
                     tickfont=dict(size=10))
    fig.update_yaxes(showgrid=True, gridcolor=GRID_LINE, zeroline=False,
                     tickfont=dict(size=10))

    _render_card_header(title, takeaway)
    st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
# Takeaway-subtitle templating
# ---------------------------------------------------------------------------

def format_takeaway(template: str, values: dict) -> str:
    """Format a takeaway-subtitle template with computed values.

    Templates use Python ``str.format`` ``{placeholder}`` syntax.
    Missing placeholders raise ``KeyError`` intentionally — a silent
    fallback to "unknown" would produce nonsense subtitles. Callers
    should pre-build the values dict with every placeholder the
    template references.

    Example::

        template = (
            "Your UC transactions dropped {own_pct_drop}% from "
            "baseline by week of {trough_week}; peers also declined "
            "({peer_a_pct_drop}% and {peer_b_pct_drop}%). "
            "The pattern is {market_signal}."
        )
        values = {
            "own_pct_drop":    46,
            "trough_week":     "Apr 27",
            "peer_a_pct_drop": 31,
            "peer_b_pct_drop": 33,
            "market_signal":   "market-wide",
        }
        format_takeaway(template, values)
        # → "Your UC transactions dropped 46% from baseline by ..."
    """
    return template.format(**values)
