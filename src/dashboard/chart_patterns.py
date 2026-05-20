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
            name="peer_a",
            line=dict(color=PEER_A, dash="dash", width=2),
            marker=dict(size=6, color=PEER_A),
            connectgaps=False,
        ))
    if show_peers and data.get("peer_b") is not None:
        fig.add_trace(go.Scatter(
            x=data["weeks"],
            y=data["peer_b"],
            mode="lines+markers",
            name="peer_b",
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
            name="peer_a",
            marker=dict(color=PEER_A),
        ))
    if panel.get("peer_b_gaps") is not None or panel.get("peer_b") is not None:
        vals = panel.get("peer_b_gaps") or panel.get("peer_b")
        traces.append(go.Bar(
            y=cats, x=vals, orientation="h",
            name="peer_b",
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
