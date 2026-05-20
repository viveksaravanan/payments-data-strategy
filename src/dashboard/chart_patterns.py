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
    st.markdown(f"**{title}**")
    if takeaway:
        st.markdown(
            f"<p style='color:#4B5563;font-size:13px;margin:-4px 0 8px 0;'>"
            f"{takeaway}</p>",
            unsafe_allow_html=True,
        )
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
