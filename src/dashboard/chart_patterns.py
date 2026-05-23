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

import folium
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
from branca.colormap import LinearColormap
from streamlit_folium import st_folium


# Plotly needs Python-string colors (CSS variables can't reach the
# library). These mirror the values defined in styling.py — if either
# moves, both should. Phase 4 close-out can consider consolidating.
ACCENT          = "#0F4C81"   # own merchant brand color
ACCENT_SOFT     = "#D8E2EE"   # tinted brand background
PEER_A          = "#6B7280"   # medium gray
PEER_B          = "#9CA3AF"   # lighter gray
PEER_AGGREGATE  = "#4B5563"   # darker gray (for aggregate peer overlays)
BASELINE_LINE   = "rgba(128, 128, 128, 0.4)"
GRID_LINE       = "rgba(128, 128, 128, 0.10)"

# Text colors — match the v2.5 ``--text`` / ``--text-2`` / ``--text-muted``
# CSS vars so Plotly text styling stays in lock-step with the rest of
# the dashboard. Centralizing these here avoids ad-hoc hex codes
# scattered through the pattern helpers.
TEXT           = "#1A1F2E"   # primary text (cell-overlay numerals)
TEXT_2         = "#4A5161"   # secondary text (point labels, neutral)
TEXT_MUTED     = "#9CA3AF"   # muted text (footnotes, axis ticks)
TAKEAWAY_GRAY  = "#4B5563"   # takeaway subtitle color (= --text-2 family)
NEUTRAL_FILL   = "#E5E7EB"   # tailwind gray-200 — k=5 suppressed cells

# Diverging palette (Pattern 3 heatmaps, Pattern 5 waterfall, Pattern
# 2 diverging mode for D3-style fingerprints).
DIVERGING_LOW   = "#C44536"   # red — own below peer / under-indexed
DIVERGING_MID   = "#FFFFFF"   # white — on baseline
DIVERGING_HIGH  = "#0F4C81"   # blue — own above peer / over-indexed

# Sequential palette — V3_DASHBOARD_DESIGN.md Section 6.1 multi-stop
# brand-family gradient. Used by Pattern 6 maps and Pattern 3
# own-only-sequential mode. Five stops give finer resolution than the
# previous two-stop ramp.
SEQUENTIAL_STOPS = [
    [0.00, "#FFFFFF"],
    [0.20, "#D8E2EE"],
    [0.50, "#94B0CC"],
    [0.80, "#4B7BA6"],
    [1.00, "#0F4C81"],
]


# ---------------------------------------------------------------------------
# Standardized hover tooltip — applied to every Plotly figure via
# ``fig.update_layout(hoverlabel=HOVERLABEL)``. Folium tooltips get
# matching CSS in styling.py so the two libraries' tooltips read as
# one visual family.
# ---------------------------------------------------------------------------

HOVERLABEL = dict(
    bgcolor="#FFFFFF",
    bordercolor="#E5E7EB",
    font=dict(
        family="-apple-system, BlinkMacSystemFont, system-ui, sans-serif",
        size=13,
        color="#1F2937",
    ),
)


# k=5 suppression footnote — when a chart suppresses cells / rows /
# polygons because the underlying count fell below 5, the rendered
# card surfaces this short caveat below the chart.
K5_SUPPRESSION_FOOTNOTE = (
    "Some elements aggregate fewer than 5 transactions and are "
    "not shown for privacy."
)


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


# Reused footnote for cards whose computation depends on knowing
# which customer made a transaction. v3's synthetic data attaches a
# customer_id to every transaction (100% coverage); in real-world
# deployment, this slice is only loyalty-enrolled / billing-known.
CUSTOMER_COVERAGE_FOOTNOTE = (
    "Based on transactions tied to a known customer. "
    "In production, this would cover only customers with "
    "loyalty enrollment or consistent billing address."
)


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
    ask_about_this: dict | None = None,
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
        hoverlabel=HOVERLABEL,
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
    _render_card_header(title, takeaway, ask_about_this=ask_about_this)
    st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
# Pattern 1 — Time-series, own-only multi-series mode
# ---------------------------------------------------------------------------
#
# Used by TBL/TJX questions whose comparison axis is not a peer cohort
# but a within-merchant breakdown (per-daypart, per-category). Same
# Plotly machinery as ``render_time_series_vs_peers`` but takes an
# arbitrary list of own series with distinct line styles.

# Light → dark brand-family palette for own multi-series modes. Anchors
# at the existing ACCENT so the visual identity stays consistent with
# the cross-merchant charts.
_OWN_SERIES_COLORS = [
    "#0F4C81",  # ACCENT
    "#3A6FA5",
    "#6F8FB8",
    "#9CB3CC",
    "#C8D5E2",
    "#3D2EAD",  # deeper accent for the fifth series
    "#5C4FC0",
    "#8B82D6",
]
_OWN_SERIES_DASHES = [
    "solid", "dash", "dot", "dashdot",
    "longdash", "longdashdot", "solid", "dash",
]


def render_time_series_own_multi(
    data: dict,
    *,
    title: str,
    takeaway: str,
    height: int = 360,
    ask_about_this: dict | None = None,
) -> None:
    """Pattern 1, own-only multi-series mode.

    ``data`` keys:

        weeks:    list of week-start ISO dates.
        series:   list of dicts, each with
            ``name`` (str)        — legend label
            ``values`` (list)     — y-values aligned to ``weeks``
            ``color`` (str, opt.) — Plotly color (defaults to a
                                    brand-family palette by index)
            ``dash`` (str, opt.)  — Plotly dash style (defaults vary)
        y_label:  str — y-axis label (e.g., ``"Mean ticket ($)"``).
    """
    weeks = data.get("weeks") or []
    series = data.get("series") or []
    if not weeks or not series:
        _render_card_header(title, takeaway, ask_about_this=ask_about_this)
        st.caption("_No data available for this view._")
        return

    fig = go.Figure()
    for idx, s in enumerate(series):
        color = s.get("color") or _OWN_SERIES_COLORS[idx % len(_OWN_SERIES_COLORS)]
        dash  = s.get("dash")  or _OWN_SERIES_DASHES[idx % len(_OWN_SERIES_DASHES)]
        fig.add_trace(go.Scatter(
            x=weeks,
            y=s["values"],
            mode="lines+markers",
            name=s["name"],
            line=dict(color=color, dash=dash, width=2),
            marker=dict(size=5, color=color),
            connectgaps=False,
        ))

    # Hide the legend for single-series renders — the lone label
    # adds vertical clutter without disambiguating anything.
    show_legend = len(series) > 1
    fig.update_layout(
        hoverlabel=HOVERLABEL,
        xaxis_title=None,
        yaxis_title=data.get("y_label", ""),
        hovermode="x unified",
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=48, r=24, t=24, b=40),
        height=height,
        showlegend=show_legend,
        legend=dict(
            orientation="h",
            yanchor="bottom", y=1.02,
            xanchor="left",   x=0,
            font=dict(size=10),
        ),
    )
    fig.update_xaxes(showgrid=False, zeroline=False, tickfont=dict(size=10))
    fig.update_yaxes(showgrid=True, gridcolor=GRID_LINE, zeroline=False,
                     tickfont=dict(size=10))
    _render_card_header(title, takeaway, ask_about_this=ask_about_this)
    st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
# Pattern 2 — Cross-merchant comparison, single dimension
# ---------------------------------------------------------------------------

def _render_card_header(
    title: str,
    takeaway: str,
    ask_about_this: dict | None = None,
) -> None:
    """Standard title + takeaway-subtitle block above a chart.

    When ``ask_about_this`` is provided, the title and the affordance
    icon render on the same horizontal row — title-left, icon-right —
    matching Pattern 8's KPI header rhythm so every dashboard card
    has the same affordance position regardless of chart pattern.

    ``ask_about_this`` schema: ``{key, specialist, prefill}`` —
    forwarded to ``render_ask_about_this``.
    """
    if ask_about_this:
        head_l, head_r = st.columns([0.84, 0.16], gap="small")
        with head_l:
            st.markdown(f"**{title}**")
        with head_r:
            render_ask_about_this(**ask_about_this)
    else:
        st.markdown(f"**{title}**")
    if takeaway:
        st.markdown(
            f"<p style='color:{TAKEAWAY_GRAY};font-size:13px;"
            f"margin:-4px 0 8px 0;'>{takeaway}</p>",
            unsafe_allow_html=True,
        )


def _render_card_footnote(footnote: str) -> None:
    """Small muted footnote below a chart. Used for data-coverage
    caveats that apply to a specific card but aren't part of its
    headline takeaway."""
    if not footnote:
        return
    st.markdown(
        f"<p style='color:{TEXT_MUTED};font-size:12px;margin:4px 0 0 0;'>"
        f"{footnote}</p>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Sort-direction convention (Phase 4.5 (2/2))
#
# Dashboard cards are action-oriented and sort worst-first so the
# rows the merchant should act on land at the top:
#   - Card 3.2 Store performance:  deviation_pct ASC
#   - Card 4.2 Underperformers:    deviation_pct ASC
#   - Card 4.2 Top performers:     recent_revenue DESC
#
# Chat-side anomaly tables (A2, T-A1, R-A1) are investigation-oriented
# and sort by absolute magnitude so the largest swing in either
# direction surfaces first:
#   - chat A2 / T-A1 / R-A1:        abs(deviation_pct) DESC
#
# This is intentional — the dashboard surfaces "what to do" while the
# chat surfaces "what changed". Don't unify them.
# ---------------------------------------------------------------------------


def render_empty_state(message: str) -> None:
    """Render an empty-state message in place of chart content.

    Used when a chart has no data to surface (zero anomalies crossing
    threshold, no under-served neighborhoods, etc.). Maintains card
    structure (title, takeaway, affordance — those render around the
    chart helper); replaces the chart area with a centered muted
    message that matches the takeaway voice (declarative, no
    hedging).

    Callers typically invoke this after ``_render_card_header`` when
    the data is empty rather than calling the Plotly/Folium render
    path.
    """
    st.markdown(
        f"<div style='padding:32px 24px;color:{TEXT_MUTED};"
        f"font-size:13px;text-align:center;background:#FAFBFC;"
        f"border-radius:6px;'>{message}</div>",
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
    ask_about_this: dict | None = None,
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
        _render_two_panel(data, title=title, takeaway=takeaway,
                          height=height, ask_about_this=ask_about_this)
        return
    if mode == "diverging":
        _render_diverging(data, title=title, takeaway=takeaway,
                          height=height, ask_about_this=ask_about_this)
        return
    if mode == "standard":
        _render_standard(data, title=title, takeaway=takeaway,
                         own_color=own_color, height=height,
                         ask_about_this=ask_about_this)
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
                       height: int | None,
                       ask_about_this: dict | None = None) -> None:
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
        hoverlabel=HOVERLABEL,
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
    _render_card_header(title, takeaway, ask_about_this=ask_about_this)
    st.plotly_chart(fig, use_container_width=True)


def _render_diverging(data: dict, *, title: str, takeaway: str,
                       height: int | None,
                       ask_about_this: dict | None = None) -> None:
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
        hoverlabel=HOVERLABEL,
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
    _render_card_header(title, takeaway, ask_about_this=ask_about_this)
    st.plotly_chart(fig, use_container_width=True)


def _render_standard(data: dict, *, title: str, takeaway: str,
                      own_color: str, height: int | None,
                      ask_about_this: dict | None = None) -> None:
    fig = go.Figure()
    for trace in _series_traces_for_panel(data):
        fig.add_trace(trace)
    fig.add_vline(x=0, line_dash="dot", line_color=BASELINE_LINE)
    fig.update_layout(
        hoverlabel=HOVERLABEL,
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
    _render_card_header(title, takeaway, ask_about_this=ask_about_this)
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
    ask_about_this: dict | None = None,
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

    - ``"own_only_diverging"`` — diverging red-white-blue scale with
      white at parity (ratio = 1.0). Cells are ratios of recent-week
      to first-4w baseline; <1.0 = under-performing (red), >1.0 =
      over-performing (blue). Cell text overlays the ratio formatted
      as percent of baseline. Used by T-A3 (day-of-week × daypart)
      and R-A3 (day-of-week × week).
    - ``"own_only_sequential"`` — light → brand-color scale over raw
      counts. Used by Card 2.3 (hour × day-of-week traffic
      heatmap). No comparison axis — pure volume heat.
    """
    if mode == "cross_merchant_diverging":
        _render_heatmap_cross_merchant_diverging(
            data, title=title, takeaway=takeaway, height=height,
            ask_about_this=ask_about_this,
        )
        return
    if mode == "own_only_diverging":
        _render_heatmap_own_only_diverging(
            data, title=title, takeaway=takeaway, height=height,
            ask_about_this=ask_about_this,
        )
        return
    if mode == "own_only_sequential":
        _render_heatmap_own_only_sequential(
            data, title=title, takeaway=takeaway, height=height,
            ask_about_this=ask_about_this,
        )
        return
    raise NotImplementedError(
        f"render_heatmap mode {mode!r} not implemented."
    )


def _render_heatmap_cross_merchant_diverging(
    data: dict,
    *,
    title: str,
    takeaway: str,
    height: int | None,
    ask_about_this: dict | None = None,
) -> None:
    rows = data["rows"]
    cols = data["cols"]
    cells = data["cells"]
    if not rows or not cols:
        _render_card_header(title, takeaway, ask_about_this=ask_about_this)
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
        textfont=dict(size=11, color=TEXT),
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
        hoverlabel=HOVERLABEL,
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

    _render_card_header(title, takeaway, ask_about_this=ask_about_this)
    st.plotly_chart(fig, use_container_width=True)
    # Cells suppressed by k<5 enter the matrix as ``None``; if any
    # exist, surface the privacy footnote so the merchant knows the
    # blanks aren't missing data.
    if data.get("suppressed_count", 0) > 0:
        _render_card_footnote(K5_SUPPRESSION_FOOTNOTE)


def _render_heatmap_own_only_diverging(
    data: dict,
    *,
    title: str,
    takeaway: str,
    height: int | None,
    ask_about_this: dict | None = None,
) -> None:
    rows = data["rows"]
    cols = data["cols"]
    cells = data["cells"]
    if not rows or not cols:
        _render_card_header(title, takeaway, ask_about_this=ask_about_this)
        st.caption("_No data available for this view._")
        return

    # Cells are ratios (1.0 = baseline). Symmetric range AROUND 1.0
    # so red/blue saturate equally for under/over deviation.
    flat = [v for row in cells for v in row if v is not None]
    if flat:
        max_dev = max(abs(v - 1.0) for v in flat)
    else:
        max_dev = 0.2  # default 20% range if everything is None
    # Floor at 0.10 so tiny ratios don't fully saturate the color.
    max_dev = max(max_dev, 0.10)
    zmin = 1.0 - max_dev
    zmax = 1.0 + max_dev

    text = [
        [f"{v * 100:.0f}%" if v is not None else "—" for v in row]
        for row in cells
    ]

    fig = go.Figure(go.Heatmap(
        z=cells,
        x=cols,
        y=rows,
        text=text,
        texttemplate="%{text}",
        textfont=dict(size=11, color=TEXT),
        colorscale=[
            [0.0, DIVERGING_LOW],
            [0.5, DIVERGING_MID],
            [1.0, DIVERGING_HIGH],
        ],
        zmid=1.0,
        zmin=zmin,
        zmax=zmax,
        hoverongaps=False,
        hovertemplate="<b>%{y} × %{x}</b><br>%{text} of baseline<extra></extra>",
        colorbar=dict(
            tickformat=".0%",
            thickness=10,
            outlinewidth=0,
        ),
        xgap=2, ygap=2,
    ))
    fig.update_layout(
        hoverlabel=HOVERLABEL,
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=80, r=20, t=20, b=24),
        height=height or max(280, 30 * len(rows) + 80),
    )
    fig.update_xaxes(tickfont=dict(size=11), side="top")
    fig.update_yaxes(tickfont=dict(size=11), automargin=True,
                     autorange="reversed")
    _render_card_header(title, takeaway, ask_about_this=ask_about_this)
    st.plotly_chart(fig, use_container_width=True)
    if data.get("suppressed_count", 0) > 0:
        _render_card_footnote(K5_SUPPRESSION_FOOTNOTE)


def _render_heatmap_own_only_sequential(
    data: dict,
    *,
    title: str,
    takeaway: str,
    height: int | None,
    ask_about_this: dict | None = None,
) -> None:
    """Pattern 3 own-only sequential — light → brand-color scale over
    raw integer counts. No comparison axis, no diverging treatment.
    Used by Card 2.3 (hour × day-of-week heatmap)."""
    rows = data["rows"]
    cols = data["cols"]
    cells = data["cells"]
    if not rows or not cols:
        _render_card_header(title, takeaway, ask_about_this=ask_about_this)
        st.caption("_No data available for this view._")
        return

    flat = [v for row in cells for v in row if v is not None]
    vmax = max(flat) if flat else 1.0
    fig = go.Figure(go.Heatmap(
        z=cells,
        x=cols,
        y=rows,
        colorscale=SEQUENTIAL_STOPS,
        zmin=0,
        zmax=vmax,
        hoverongaps=False,
        hovertemplate="<b>%{y} · %{x}</b><br>%{z:,} txns<extra></extra>",
        colorbar=dict(
            thickness=10,
            outlinewidth=0,
            tickformat=",",
        ),
        xgap=1, ygap=1,
    ))
    fig.update_layout(
        hoverlabel=HOVERLABEL,
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=64, r=20, t=20, b=24),
        height=height or max(260, 28 * len(rows) + 80),
    )
    fig.update_xaxes(tickfont=dict(size=10), side="top")
    fig.update_yaxes(tickfont=dict(size=11), automargin=True,
                     autorange="reversed")
    _render_card_header(title, takeaway, ask_about_this=ask_about_this)
    st.plotly_chart(fig, use_container_width=True)
    if data.get("suppressed_count", 0) > 0:
        _render_card_footnote(K5_SUPPRESSION_FOOTNOTE)


# ---------------------------------------------------------------------------
# Pattern 2 — Horizontal bars, own-only mode
# ---------------------------------------------------------------------------

def render_horizontal_bars_own(
    data: dict,
    *,
    title: str,
    takeaway: str,
    height: int | None = None,
    ask_about_this: dict | None = None,
) -> None:
    """Pattern 2, own-only horizontal bars with optional reference line
    and outlier highlighting. Used by T-P3 (per-store ticket spread),
    T-D1 (own category share), R-D1, R-P3 (ticket-band split).

    ``data`` keys:

        labels: list of y-axis labels.
        values: list of x-axis values aligned to ``labels``.
        x_label, y_label:  optional axis labels.
        ref_line:          optional float — vertical reference line.
        ref_label:         optional str — annotation for ref line.
        highlight:         optional list[bool] — True rows render in a
                           darker accent shade.
        value_format:      optional Plotly tickformat (e.g., ``"$,.2f"``).
        hover_template:    optional Plotly hovertemplate string.
    """
    labels = data.get("labels") or []
    values = data.get("values") or []
    if not labels or not values or len(labels) != len(values):
        _render_card_header(title, takeaway, ask_about_this=ask_about_this)
        st.caption("_No data available for this view._")
        return

    highlight = data.get("highlight") or [False] * len(labels)
    bar_colors = [
        "#072744" if h else ACCENT  # darker accent for outliers
        for h in highlight
    ]

    hover = data.get("hover_template") or (
        "<b>%{y}</b><br>%{x}<extra></extra>"
    )
    bar_kwargs = dict(
        y=labels,
        x=values,
        orientation="h",
        marker=dict(color=bar_colors),
        hovertemplate=hover,
    )
    if data.get("customdata") is not None:
        bar_kwargs["customdata"] = data["customdata"]
    fig = go.Figure(go.Bar(**bar_kwargs))

    ref = data.get("ref_line")
    if ref is not None:
        fig.add_vline(
            x=float(ref),
            line_dash="dot",
            line_color=BASELINE_LINE,
            annotation_text=data.get("ref_label"),
            annotation_position="top",
            annotation_font_size=10,
            annotation_font_color="rgba(75, 85, 99, 0.85)",
        )

    fig.update_layout(
        hoverlabel=HOVERLABEL,
        xaxis_title=data.get("x_label"),
        yaxis_title=None,
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=80, r=24, t=20, b=32),
        height=height or max(220, 22 * len(labels) + 80),
        showlegend=False,
    )
    fig.update_xaxes(
        showgrid=True, gridcolor=GRID_LINE, zeroline=False,
        tickformat=data.get("value_format"),
        tickfont=dict(size=10),
    )
    fig.update_yaxes(showgrid=False, tickfont=dict(size=10),
                     automargin=True, autorange="reversed")
    _render_card_header(title, takeaway, ask_about_this=ask_about_this)
    st.plotly_chart(fig, use_container_width=True)


def render_horizontal_bars_grouped(
    data: dict,
    *,
    title: str,
    takeaway: str,
    height: int | None = None,
    ask_about_this: dict | None = None,
) -> None:
    """Pattern 2, multi-series grouped horizontal bars. Used by R-P3
    (ticket-band split: share of transactions + share of revenue per
    band on a common percentage axis).

    ``data`` keys:

        labels: list of y-axis labels (e.g., ticket-band names).
        series: list of dicts, each with
            ``name`` (str)      — legend label
            ``values`` (list)   — x-values aligned to ``labels``
            ``color`` (str, opt.) — defaults to a brand-family palette
        x_label: optional axis label.
        value_format: optional Plotly tickformat (e.g., ``".0%"``,
                       ``"$,.0f"``).
    """
    labels = data.get("labels") or []
    series = data.get("series") or []
    if not labels or not series:
        _render_card_header(title, takeaway, ask_about_this=ask_about_this)
        st.caption("_No data available for this view._")
        return

    fig = go.Figure()
    for idx, s in enumerate(series):
        color = s.get("color") or _OWN_SERIES_COLORS[idx % len(_OWN_SERIES_COLORS)]
        fig.add_trace(go.Bar(
            y=labels,
            x=s["values"],
            orientation="h",
            name=s["name"],
            marker=dict(color=color),
            hovertemplate=f"<b>%{{y}}</b><br>{s['name']}: %{{x}}<extra></extra>",
        ))
    fig.update_layout(
        hoverlabel=HOVERLABEL,
        barmode="group",
        xaxis_title=data.get("x_label"),
        yaxis_title=None,
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=80, r=24, t=44, b=32),
        height=height or max(220, 32 * len(labels) + 80),
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="bottom", y=1.04,
            xanchor="left",   x=0,
            font=dict(size=10),
        ),
    )
    fig.update_xaxes(
        showgrid=True, gridcolor=GRID_LINE, zeroline=False,
        tickformat=data.get("value_format"),
        tickfont=dict(size=10),
    )
    fig.update_yaxes(showgrid=False, tickfont=dict(size=10),
                     automargin=True, autorange="reversed")
    _render_card_header(title, takeaway, ask_about_this=ask_about_this)
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
    ask_about_this: dict | None = None,
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
        _render_card_header(title, takeaway, ask_about_this=ask_about_this)
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
        hoverlabel=HOVERLABEL,
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

    _render_card_header(title, takeaway, ask_about_this=ask_about_this)
    st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
# Pattern 5 — Decomposition / waterfall
# ---------------------------------------------------------------------------

def render_waterfall(
    data: dict,
    *,
    title: str,
    takeaway: str,
    mode: str = "cross_merchant",
    height: int | None = None,
    ask_about_this: dict | None = None,
) -> None:
    """Pattern 5 — waterfall decomposition with driver bars.

    Modes:

    - ``"cross_merchant"`` — own-vs-peer gap decomposed into driver
      contributions. Each driver bar shows its sign-aware pp
      contribution to the gap; a final ``"total"`` bar shows the
      cumulative gap. Used by D7.
    - ``"own_vs_own_baseline"`` — own-vs-own decomposition: recent
      week vs first-4w baseline. Drivers are Traffic, Basket, Ticket,
      Mix, Residual (no Stores — store count is held constant inside
      a single merchant across baseline and recent windows). Used by
      T-D3 and R-D3. Visually identical waterfall shape; only the
      data narrative differs (the takeaway is composed by the caller).

    ``data`` keys for ``cross_merchant`` mode:

        drivers: list of dicts, each with
            ``label`` (str)         — driver name (e.g., "Traffic")
            ``contribution`` (float) — sign-aware pp contribution
            ``own`` (float, optional) — raw own driver value (hover)
            ``peer`` (float, optional) — raw peer-mean value (hover)
        total_label: label for the cumulative bar (default "Total gap").
        y_label:     y-axis label (default "Contribution to gap (pp)").
    """
    if mode in ("cross_merchant", "own_vs_own_baseline"):
        # The renderer shape is identical for both modes — driver
        # bars then a cumulative ``total`` bar. The semantic
        # difference (vs-peer gap vs vs-baseline change) lives in
        # the caller's data shape + takeaway text.
        _render_waterfall_cross_merchant(
            data, title=title, takeaway=takeaway, height=height,
            ask_about_this=ask_about_this,
        )
        return
    raise NotImplementedError(
        f"render_waterfall mode {mode!r} not implemented."
    )


def _render_waterfall_cross_merchant(
    data: dict,
    *,
    title: str,
    takeaway: str,
    height: int | None,
    ask_about_this: dict | None = None,
) -> None:
    drivers = data.get("drivers") or []
    if not drivers:
        _render_card_header(title, takeaway, ask_about_this=ask_about_this)
        st.caption("_No data available for this view._")
        return

    total_label = data.get("total_label", "Total gap")
    y_label     = data.get("y_label", "Contribution to gap (pp)")

    # Waterfall x-axis: one entry per driver, plus a final cumulative
    # "Total gap" column. ``measure=relative`` makes each bar a delta on
    # the running total; ``measure=total`` resets the bar to absolute
    # cumulative position (so the final bar reads as the summed gap).
    x        = [d["label"] for d in drivers] + [total_label]
    y        = [float(d["contribution"]) for d in drivers] + [0.0]
    measure  = ["relative"] * len(drivers) + ["total"]
    text     = [f"{d['contribution']:+.1f}pp" for d in drivers]
    text    += [f"{sum(float(d['contribution']) for d in drivers):+.1f}pp"]

    # Per-bar hover with raw own / peer values when provided.
    customdata = [
        [d.get("own", ""), d.get("peer", "")] for d in drivers
    ] + [["", ""]]
    hover = (
        "<b>%{x}</b><br>"
        "Contribution: %{text}<br>"
        "Own: %{customdata[0]}<br>"
        "Peer: %{customdata[1]}<extra></extra>"
    )

    fig = go.Figure(go.Waterfall(
        name="Gap decomposition",
        orientation="v",
        measure=measure,
        x=x,
        y=y,
        text=text,
        textposition="outside",
        customdata=customdata,
        hovertemplate=hover,
        connector=dict(line=dict(color=BASELINE_LINE, width=1, dash="dot")),
        increasing=dict(marker=dict(color=ACCENT)),
        decreasing=dict(marker=dict(color=DIVERGING_LOW)),
        totals=dict(marker=dict(color=PEER_AGGREGATE)),
    ))
    fig.add_hline(y=0, line_dash="dot", line_color=BASELINE_LINE)

    fig.update_layout(
        hoverlabel=HOVERLABEL,
        xaxis_title=None,
        yaxis_title=y_label,
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=64, r=24, t=24, b=40),
        height=height or 380,
        showlegend=False,
    )
    fig.update_xaxes(showgrid=False, zeroline=False, tickfont=dict(size=11))
    fig.update_yaxes(showgrid=True, gridcolor=GRID_LINE, zeroline=False,
                     ticksuffix="pp", tickfont=dict(size=10))

    _render_card_header(title, takeaway, ask_about_this=ask_about_this)
    st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
# Pattern 6 — Geographic map (Folium)
# ---------------------------------------------------------------------------
#
# Synthetic neighborhood polygons built from ZIP centroids. Each
# neighborhood gets a hexagonal hull around the mean of its panel-ZIP
# centroids, padded by the max centroid-to-mean distance plus a
# fixed buffer — single-ZIP neighborhoods land on a uniform hexagon,
# multi-ZIP ones expand to cover all member ZIPs. Coords are
# duplicated from ``src/generate/metro.py``'s private centroid table
# rather than imported across the dashboard ↔ generate boundary;
# the centroid set is static and small.

CLT_CENTER = (35.227, -80.843)

_ZIP_CENTROIDS: dict[str, tuple[float, float]] = {
    "28202": (35.2271, -80.8431),  # Uptown
    "28203": (35.2096, -80.8534),  # Dilworth
    "28205": (35.2229, -80.8128),  # Plaza Midwood
    "28206": (35.2440, -80.8189),  # NoDa
    "28210": (35.1597, -80.8607),  # SouthPark south
    "28211": (35.1700, -80.8200),  # SouthPark east
    "28213": (35.3144, -80.7625),  # University City west
    "28223": (35.3056, -80.7331),  # University City east (UNCC)
    "28277": (35.0589, -80.8462),  # Ballantyne
    "28104": (35.0890, -80.7000),  # Matthews west
    "28105": (35.1170, -80.7100),  # Matthews east
    "28078": (35.4107, -80.8420),  # Huntersville
    "28134": (35.0824, -80.8923),  # Pineville
    "28025": (35.4087, -80.5790),  # Concord west
    "28027": (35.3970, -80.6800),  # Concord east
    "28115": (35.5840, -80.8100),  # Mooresville south
    "28117": (35.5800, -80.9100),  # Mooresville north
}

_NEIGHBORHOOD_ZIPS: dict[str, list[str]] = {
    "Uptown / Center City": ["28202"],
    "Plaza Midwood":        ["28205"],
    "NoDa":                 ["28206"],
    "Dilworth":             ["28203"],
    "SouthPark":            ["28210", "28211"],
    "Ballantyne":           ["28277"],
    "University City":      ["28213", "28223"],
    "Matthews":             ["28104", "28105"],
    "Huntersville":         ["28078"],
    "Pineville":            ["28134"],
    "Concord":              ["28025", "28027"],
    "Mooresville":          ["28115", "28117"],
}

# Fixed buffer added around each neighborhood's centroid radius (in
# degrees, ~1.4 km at this latitude). Small enough that adjacent
# neighborhoods don't overlap visibly; large enough that single-ZIP
# polygons read as polygons rather than dots.
_POLYGON_BUFFER_DEG = 0.013


def _hex_vertices(center: tuple[float, float], radius: float) -> list[list[float]]:
    """Six-vertex hexagon around `center` with the given radius (in
    degrees). Returned as ``[[lat, lon], ...]`` for Folium."""
    import math
    lat, lon = center
    # Latitude scale: 1 deg lat ≈ 111 km. Longitude scale at 35°N:
    # 1 deg lon ≈ 91 km. Adjust the lon radius to compensate so the
    # hexagon reads as visually circular rather than stretched
    # east-west.
    lon_scale = 111.0 / 91.0
    verts = []
    for k in range(6):
        ang = math.radians(60 * k + 30)  # offset so a flat side faces north
        verts.append([
            lat + radius * math.sin(ang),
            lon + radius * lon_scale * math.cos(ang),
        ])
    return verts


def _neighborhood_polygons() -> dict[str, list[list[float]]]:
    """Compute the hexagonal hull for every panel neighborhood. Cached
    at module load time so map renders don't recompute on every rerun."""
    out: dict[str, list[list[float]]] = {}
    for nbhd, zips in _NEIGHBORHOOD_ZIPS.items():
        centroids = [_ZIP_CENTROIDS[z] for z in zips]
        if len(centroids) == 1:
            center = centroids[0]
            radius = _POLYGON_BUFFER_DEG
        else:
            lat_c = sum(c[0] for c in centroids) / len(centroids)
            lon_c = sum(c[1] for c in centroids) / len(centroids)
            center = (lat_c, lon_c)
            # Max distance from mean to any member centroid → expand
            # to cover all ZIPs in the neighborhood.
            max_d = max(
                ((c[0] - lat_c) ** 2 + (c[1] - lon_c) ** 2) ** 0.5
                for c in centroids
            )
            radius = max_d + _POLYGON_BUFFER_DEG
        out[nbhd] = _hex_vertices(center, radius)
    return out


_POLYGON_CACHE = _neighborhood_polygons()


def neighborhood_polygon(name: str) -> list[list[float]] | None:
    """Return the precomputed hexagonal hull vertices for a panel
    neighborhood, or ``None`` if the name isn't in the panel."""
    return _POLYGON_CACHE.get(name)


def neighborhood_centroid(name: str) -> tuple[float, float] | None:
    """Mean ZIP centroid for a panel neighborhood — used to place
    aggregate peer-store markers (the lake doesn't expose per-store
    coords, so peer markers land on the neighborhood centroid with
    jitter)."""
    zips = _NEIGHBORHOOD_ZIPS.get(name)
    if not zips:
        return None
    centroids = [_ZIP_CENTROIDS[z] for z in zips]
    return (
        sum(c[0] for c in centroids) / len(centroids),
        sum(c[1] for c in centroids) / len(centroids),
    )


def render_neighborhood_map(
    data: dict,
    *,
    title: str,
    takeaway: str,
    mode: str = "diverging",
    height: int = 460,
    ask_about_this: dict | None = None,
) -> None:
    """Pattern 6 — Folium neighborhood-polygon map.

    Modes:

    - ``"diverging"`` — value-based colormap (red ↔ white ↔ blue)
      centered at 0. Used by T1 (per-neighborhood performance vs own
      panel baseline; red = under-performing, blue = over).
    - ``"sequential"`` — light → brand-color scale. Used by T2
      (customer-home density; darker = more customers from that
      neighborhood).
    - ``"score"`` — light → brand-color scale, higher = better
      opportunity. Used by T4 (expansion-opportunity score).

    ``data`` keys:

        polygons: list of dicts, each with
            ``name`` (str)     — neighborhood name
            ``value`` (float|None) — metric value driving the color
            ``tooltip`` (str)  — HTML hover content
        own_stores:  list of dicts with ``lat``, ``lon``, ``tooltip``
        peer_stores: optional list of dicts with ``lat``, ``lon``,
                     ``tooltip`` (T4 only — placed at neighborhood
                     centroids with jitter; the lake doesn't expose
                     per-store coords).
        vmin, vmax:    floats anchoring the color scale; ``vmid`` for
                       diverging mode (defaults to 0).
        value_label:   short legend label.
        map_key:       Streamlit key suffix so per-question map keys
                       stay distinct.
    """
    if mode not in ("diverging", "sequential", "score"):
        raise ValueError(f"Unknown render_neighborhood_map mode: {mode!r}")

    polygons    = data.get("polygons") or []
    own_stores  = data.get("own_stores") or []
    peer_stores = data.get("peer_stores") or []
    map_key     = data.get("map_key", "map")

    _render_card_header(title, takeaway, ask_about_this=ask_about_this)

    if not polygons:
        st.caption("_No neighborhoods with data to map._")
        return

    fmap = folium.Map(
        location=CLT_CENTER,
        zoom_start=10,
        tiles="CartoDB positron",
        control_scale=True,
    )

    values = [p["value"] for p in polygons if p.get("value") is not None]
    if not values:
        # No coloring is meaningful; render polygons in a neutral fill.
        cmap = None
    else:
        vmin = float(data.get("vmin", min(values)))
        vmax = float(data.get("vmax", max(values)))
        if mode == "diverging":
            vabs = max(abs(vmin), abs(vmax), 1.0)
            cmap = LinearColormap(
                [DIVERGING_LOW, DIVERGING_MID, DIVERGING_HIGH],
                vmin=-vabs, vmax=vabs,
            )
        else:
            # sequential / score share the canonical 5-stop brand
            # ramp defined in ``SEQUENTIAL_STOPS``. "score" mode
            # floors at 0 since opportunity scores are non-negative;
            # "sequential" mode floors at the data minimum.
            lo = 0.0 if mode == "score" else vmin
            hi = max(vmax, lo + 1e-9)
            # branca's LinearColormap takes a list of color hex codes
            # and evenly spaces them between ``vmin`` and ``vmax``.
            ramp_colors = [stop[1] for stop in SEQUENTIAL_STOPS]
            cmap = LinearColormap(ramp_colors, vmin=lo, vmax=hi)
        cmap.caption = data.get("value_label", "")

    suppressed_count = 0
    for p in polygons:
        verts = neighborhood_polygon(p["name"])
        if not verts:
            continue
        val = p.get("value")
        if val is None or cmap is None:
            # k=5 suppression / no-value polygon: light neutral fill,
            # very low opacity so it reads as "unavailable" rather
            # than "low". Tooltip carries the explanation if the
            # caller supplied one.
            fill_color = NEUTRAL_FILL
            fill_opacity = 0.20
            if val is None and p.get("suppressed"):
                suppressed_count += 1
        else:
            fill_color = cmap(val)
            fill_opacity = 0.65
        folium.Polygon(
            locations=verts,
            color=PEER_AGGREGATE,
            weight=1,
            fill=True,
            fill_color=fill_color,
            fill_opacity=fill_opacity,
            tooltip=folium.Tooltip(p.get("tooltip", p["name"]), sticky=True),
        ).add_to(fmap)

    for s in own_stores:
        folium.CircleMarker(
            location=[s["lat"], s["lon"]],
            radius=5,
            color=ACCENT,
            weight=1.2,
            fill=True,
            fill_color=ACCENT,
            fill_opacity=0.92,
            tooltip=s.get("tooltip", ""),
        ).add_to(fmap)

    for s in peer_stores:
        # Peer markers use a distinct shape (square via DivIcon would be
        # heavier; a hollow gray circle reads as peer at glance and
        # stays cheap to render).
        folium.CircleMarker(
            location=[s["lat"], s["lon"]],
            radius=4,
            color=PEER_AGGREGATE,
            weight=1.0,
            fill=True,
            fill_color=DIVERGING_MID,
            fill_opacity=0.85,
            tooltip=s.get("tooltip", ""),
        ).add_to(fmap)

    if cmap is not None:
        cmap.add_to(fmap)

    st_folium(
        fmap,
        height=height,
        use_container_width=True,
        returned_objects=[],
        key=f"map_{map_key}",
    )

    _render_card_footnote(data.get("footnote", ""))
    # Per-data-helper-reported suppression count (e.g. neighborhoods
    # whose customer count fell below k=5) overrides the local
    # ``suppressed`` flag tally. Both paths fall back to the local
    # count if not explicitly set.
    n_suppressed = data.get("suppressed_count", suppressed_count)
    if n_suppressed > 0:
        _render_card_footnote(K5_SUPPRESSION_FOOTNOTE)


# ---------------------------------------------------------------------------
# Pattern 9 — Table with drilldown
# ---------------------------------------------------------------------------

def render_table_with_drilldown(
    data: dict,
    *,
    title: str,
    takeaway: str,
    height: int | None = None,
    ask_about_this: dict | None = None,
) -> None:
    """Pattern 9 — sortable table with optional diverging-color cues on
    a ratio column. Streamlit-native ``st.dataframe`` (sortable in-place
    by clicking column headers).

    ``data`` keys:

        rows: list of dicts, one per row.
        columns: list of column dicts, each with
            ``key``    (str)  — dict key in ``rows``
            ``label``  (str)  — header displayed
            ``kind``   (str)  — one of ``"text"``, ``"int"``, ``"float"``,
                                ``"pct"``, ``"ratio"`` (for formatting)
            ``diverging`` (bool, optional) — colour cells red/white/blue
                              around zero. Only meaningful for ``"pct"``.
        empty_caption: str shown when ``rows`` is empty (otherwise a
                       generic fallback).

    Rendering uses pandas ``Styler`` for the diverging colour fill and
    ``st.dataframe`` for the sortable table. The dataframe inherits
    Streamlit's default click-to-sort behaviour without additional code.
    """
    import pandas as pd

    _render_card_header(title, takeaway, ask_about_this=ask_about_this)

    rows    = data.get("rows") or []
    columns = data.get("columns") or []
    if not rows:
        st.caption(data.get("empty_caption", "_No rows to display._"))
        return

    # Order columns explicitly so the table reads as declared.
    keys = [c["key"] for c in columns]
    df = pd.DataFrame(rows)[keys]
    df = df.rename(columns={c["key"]: c["label"] for c in columns})

    # Per-column formatter built from ``kind``.
    fmt: dict[str, str] = {}
    for c in columns:
        kind = c.get("kind", "text")
        label = c["label"]
        if kind == "int":
            fmt[label] = "{:,.0f}"
        elif kind == "float":
            fmt[label] = "{:,.1f}"
        elif kind == "pct":
            fmt[label] = "{:+.1f}%"
        elif kind == "ratio":
            fmt[label] = "{:.2f}"

    diverging_labels = [
        c["label"] for c in columns if c.get("diverging") and c.get("kind") == "pct"
    ]

    styler = df.style.format(fmt, na_rep="—")
    if diverging_labels:
        # Symmetric color range around zero so red and blue saturate
        # equally regardless of the actual data range.
        for lbl in diverging_labels:
            col = pd.to_numeric(df[lbl], errors="coerce").dropna()
            if not col.empty:
                vabs = max(abs(col.min()), abs(col.max()), 1.0)
                styler = styler.background_gradient(
                    cmap="RdBu",
                    subset=[lbl],
                    vmin=-vabs,
                    vmax=vabs,
                )

    st.dataframe(
        styler,
        use_container_width=True,
        hide_index=True,
        height=height,
    )


# ---------------------------------------------------------------------------
# Pattern 8 — KPI callout (single-number + sparkline + delta arrow)
# ---------------------------------------------------------------------------

# Maps semantic flag → ``--accent`` override for the KPI value text.
_KPI_FLAG_COLOR = {
    "alert": "var(--bad)",
    "clear": "var(--good)",
}

_DELTA_ARROW = {
    "up":   "▲",
    "down": "▼",
    "flat": "▬",
}


def _render_sparkline(values: list[float], *, color: str = ACCENT) -> None:
    """Tiny axis-less Plotly line used inside Pattern 8 cards. Keeps a
    fixed ~38 px height so all 5 KPI cards in a row stay vertically
    aligned regardless of value magnitudes."""
    if not values or len(values) < 2:
        return
    fig = go.Figure(go.Scatter(
        x=list(range(len(values))),
        y=values,
        mode="lines",
        line=dict(color=color, width=1.6, shape="spline"),
        hoverinfo="skip",
    ))
    fig.update_layout(
        hoverlabel=HOVERLABEL,
        height=38,
        margin=dict(l=0, r=0, t=2, b=0),
        showlegend=False,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(visible=False, fixedrange=True),
        yaxis=dict(visible=False, fixedrange=True),
    )
    st.plotly_chart(fig, use_container_width=True,
                     config={"displayModeBar": False})


def render_ask_about_this(
    *,
    key: str,
    specialist: str,
    prefill: str,
    label: str = "✦",
    help_text: str = "Ask the agent about this",
) -> None:
    """Per-card "Ask about this" affordance.

    Phase 4.5 final: clicking now sets only two pieces of session
    state and triggers a rerun:
        - ``chat_input_prefill`` — the templated question text. The
          chat panel's text-area widget reads this on render and uses
          it as the initial value.
        - ``chat_state``         — opens the drawer in side-panel
          mode if it was closed; leaves it alone if already open.

    The previous behaviour also set ``state.active_agent = specialist``
    so the dropdown would snap to the routed specialist. Removed —
    the orchestrator routes the question text to the appropriate
    specialist at query time, so forcing a UI specialist switch on
    affordance click felt presumptuous when the user might want to
    keep the current specialist's context. The ``specialist``
    parameter stays in the signature for backward compatibility but
    is no longer applied.

    Disabled while an agent is mid-dispatch — clicking during a stream
    would otherwise queue a second dispatch behind the running one.
    """
    state = st.session_state
    is_running = bool(state.get("agent_running", False))
    if st.button(
        label,
        key=key,
        help=help_text,
        disabled=is_running,
        use_container_width=True,
    ):
        state.chat_input_prefill = prefill
        if state.get("chat_state", "closed") == "closed":
            state.chat_state = "side"
        st.rerun()


def render_kpi_callout(
    *,
    label: str,
    value: str,
    delta_text: str | None = None,
    delta_direction: str = "flat",
    sparkline: list[float] | None = None,
    hint: str | None = None,
    flag: str | None = None,
    ask_about_this: dict | None = None,
) -> None:
    """Pattern 8 — single-number KPI callout with sparkline and delta.

    Renders inside a bordered container so callers can drop a row of
    five via ``st.columns(5)`` without additional layout glue.

    ``ask_about_this`` is an optional dict shaped
    ``{key, specialist, prefill}``. When present the affordance icon
    renders in the card's header row alongside the label.
    """
    value_color = _KPI_FLAG_COLOR.get(flag or "", "var(--text)")

    with st.container(border=True):
        # Header row: label + optional ask-about-this affordance.
        if ask_about_this:
            head_l, head_r = st.columns([0.84, 0.16], gap="small")
            with head_l:
                st.markdown(
                    f'<div class="kpi-label">{label}</div>',
                    unsafe_allow_html=True,
                )
            with head_r:
                render_ask_about_this(**ask_about_this)
        else:
            st.markdown(
                f'<div class="kpi-label">{label}</div>',
                unsafe_allow_html=True,
            )

        st.markdown(
            f'<div class="kpi-value" style="color:{value_color};">{value}</div>',
            unsafe_allow_html=True,
        )

        if delta_text:
            cls = delta_direction if delta_direction in ("up", "down", "flat") else "flat"
            arrow = _DELTA_ARROW.get(cls, "")
            st.markdown(
                f'<div class="kpi-delta {cls}">{arrow} {delta_text}</div>',
                unsafe_allow_html=True,
            )

        if sparkline:
            spark_color = (
                "#C44536" if flag == "alert" else
                "#2F855A" if flag == "clear" else
                ACCENT
            )
            _render_sparkline(sparkline, color=spark_color)

        if hint:
            st.markdown(
                f'<div class="kpi-hint">{hint}</div>',
                unsafe_allow_html=True,
            )


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
