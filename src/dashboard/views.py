"""Dashboard view renderers — KPI row, map, sparkline + top SKUs,
category mix, store performance, and the Customer Insights expander.

All renderers take `(merchant_id, filters)` and write directly into the
current Streamlit container. Filter state propagates through every
query via `_filters_key()`.
"""
from __future__ import annotations

import folium
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from branca.colormap import LinearColormap
from streamlit_folium import st_folium

from . import data as D

ACCENT = "#0F4C81"
ACCENT_LIGHT = "#D8E2EE"
SURFACE = "#F7F8FA"
BORDER = "#E2E5EA"
TEXT_MUTED = "#7B8294"


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _fmt_money(n: float) -> str:
    if n >= 1_000_000:
        return f"${n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"${n / 1_000:.0f}K"
    return f"${n:.2f}"


def _fmt_int(n: int) -> str:
    return f"{int(n):,}"


def _fmt_pct(delta: float | None) -> tuple[str, str]:
    if delta is None:
        return ("—", "flat")
    pct = delta * 100
    if abs(pct) < 0.5:
        return (f"{pct:+.1f}% vs prior 30d", "flat")
    sign = "▲" if pct > 0 else "▼"
    cls = "up" if pct > 0 else "down"
    return (f"{sign} {abs(pct):.1f}% vs prior 30d", cls)


def _plotly_layout(**overrides) -> dict:
    base = {
        "font": {"family": "-apple-system, BlinkMacSystemFont, 'Segoe UI', Inter, Roboto, sans-serif",
                  "size": 12, "color": "#4A5161"},
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "rgba(0,0,0,0)",
        "margin": {"l": 40, "r": 16, "t": 16, "b": 36},
        "hoverlabel": {"bgcolor": "#1A1F2E",
                        "font": {"color": "#fff", "size": 11}},
        "xaxis": {"gridcolor": BORDER, "zerolinecolor": BORDER,
                   "linecolor": BORDER, "ticks": "outside", "tickcolor": BORDER},
        "yaxis": {"gridcolor": BORDER, "zerolinecolor": BORDER,
                   "linecolor": BORDER, "ticks": "outside", "tickcolor": BORDER},
    }
    base.update(overrides)
    return base


PLOTLY_CONFIG = {"displayModeBar": False, "responsive": True}


# ---------------------------------------------------------------------------
# KPI row
# ---------------------------------------------------------------------------

def render_kpi_row(merchant_id: str, filters: dict) -> None:
    """Compat shim — delegates to ``render_kpi_strip``."""
    render_kpi_strip(merchant_id, filters)


def render_geography_section(merchant_id: str, filters: dict) -> None:  # noqa: ARG001
    """Phase 4.4 Section 3 — Geography. Two cards in a 55/45 row:

    - Card 3.1 Neighborhood performance map (Pattern 6 diverging,
      no peer overlay since the dashboard column is own-data only).
    - Card 3.2 Store performance distribution (Pattern 9 sorted
      worst-first by recent/baseline ratio).
    """
    from . import chart_patterns as CP

    cols = st.columns([55, 45], gap="small")

    # Card 3.1 — Neighborhood performance map
    with cols[0]:
        with st.container(border=True):
            CP.render_ask_about_this(
                key=f"ask_about_geo_map_{merchant_id}",
                specialist="trade",
                prefill=(
                    "Which of my neighborhoods are over- or "
                    "under-performing, and is the issue mine or the "
                    "market's?"
                ),
            )
            chart_data = D.neighborhood_performance(merchant_id)
            nbhds = chart_data["neighborhoods"]
            if not nbhds:
                st.caption("_No store footprint to map._")
            else:
                ranked = sorted(
                    [n for n in nbhds if n["own_delta_pct"] is not None],
                    key=lambda n: n["own_delta_pct"],
                    reverse=True,
                )
                # Convert own_delta_pct (% delta vs baseline) → ratio
                # to baseline for the "X% of baseline" phrasing.
                weakest = chart_data["weakest"]
                top3 = [n["name"] for n in ranked[:3]]
                if weakest is None or weakest["own_delta_pct"] is None:
                    takeaway = "Insufficient neighborhood-level data."
                else:
                    w_ratio = 1.0 + (weakest["own_delta_pct"] / 100)
                    takeaway = (
                        f"Top 3 neighborhoods: {', '.join(top3)}. "
                        f"Weakest: {weakest['name']} at "
                        f"{w_ratio * 100:.0f}% of baseline."
                    )
                polygons = []
                for n in nbhds:
                    v = n["own_delta_pct"]
                    tip = (
                        f"<b>{n['name']}</b><br>"
                        f"Own: {n['own_n_txns']:,} txns / "
                        f"{n['own_n_stores']} stores<br>"
                        f"Δ vs baseline: "
                        f"{('—' if v is None else f'{v:+.1f}%')}"
                    )
                    polygons.append({
                        "name": n["name"], "value": v, "tooltip": tip,
                    })
                CP.render_neighborhood_map(
                    {
                        "polygons":    polygons,
                        "own_stores":  chart_data["own_markers"],
                        "peer_stores": [],
                        "value_label": "Δ vs your baseline (%)",
                        "map_key":     f"3_1_{merchant_id}",
                    },
                    title="Neighborhood performance",
                    takeaway=takeaway,
                    mode="diverging",
                    height=420,
                )

    # Card 3.2 — Store performance distribution
    with cols[1]:
        with st.container(border=True):
            CP.render_ask_about_this(
                key=f"ask_about_store_perf_{merchant_id}",
                specialist="anomaly",
                prefill=(
                    "Which stores are showing unusual traffic this week?"
                ),
            )
            # Grocers carry the peer-neighborhood column via the A2
            # helper; TBL / TJX have no same-segment peers so use the
            # leaner own-only variant. Both produce the same row
            # shape minus the peer column.
            if D.has_same_segment_peers(merchant_id):
                chart_data = D.store_anomalies(merchant_id)
            else:
                chart_data = D.store_anomalies_own_only(merchant_id)
            rows = chart_data["rows"]
            if not rows:
                st.caption("_No stores with enough baseline data._")
            else:
                # Re-sort worst-first (ratio ascending = most-negative
                # deviation_pct first). The helpers sort by absolute
                # magnitude; this card wants direction-aware ordering
                # so the worst rows surface at the top.
                rows = sorted(rows, key=lambda r: r["deviation_pct"])
                n_under = sum(
                    1 for r in rows if r["deviation_pct"] <= -15.0
                )
                n_over = sum(
                    1 for r in rows if r["deviation_pct"] >= 15.0
                )
                top_perf = max(rows, key=lambda r: r["deviation_pct"])
                takeaway = (
                    f"{n_under} of {len(rows)} stores running below "
                    f"baseline by >15%; top performer "
                    f"{top_perf['store_id']} at "
                    f"{top_perf['deviation_pct']:+.1f}% vs baseline."
                )
                columns = [
                    {"key": "store_id",      "label": "Store",        "kind": "text"},
                    {"key": "neighborhood",  "label": "Neighborhood", "kind": "text"},
                    {"key": "baseline",      "label": "Baseline/wk",  "kind": "float"},
                    {"key": "recent",        "label": "Recent",       "kind": "int"},
                    {"key": "deviation_pct", "label": "Δ vs baseline",
                     "kind": "pct", "diverging": True},
                ]
                CP.render_table_with_drilldown(
                    {
                        "rows":    rows,
                        "columns": columns,
                    },
                    title="Store performance distribution",
                    takeaway=takeaway,
                    height=360,
                )


def render_performance_section(merchant_id: str, filters: dict) -> None:  # noqa: ARG001
    """Phase 4.4 Section 2 — Performance over time. Three cards in a
    row: Revenue trajectory, Transaction trajectory, Hour × DOW
    heatmap. Each carries an "Ask about this" affordance routing per
    V3_DASHBOARD_DESIGN.md Section 5.5.
    """
    from . import chart_patterns as CP

    t = D.performance_trajectory(merchant_id)

    def _dir(pct: float) -> str:
        return "up" if pct > 0 else ("down" if pct < 0 else "flat")

    cols = st.columns(3, gap="small")

    # Card 2.1 — Revenue trajectory
    with cols[0]:
        with st.container(border=True):
            CP.render_ask_about_this(
                key=f"ask_about_rev_traj_{merchant_id}",
                specialist="anomaly",
                prefill="What's behind the revenue trajectory I'm seeing?",
            )
            rev_pct = t["revenue_30d_pct"]
            takeaway = (
                f"Revenue trending {_dir(rev_pct)} {abs(rev_pct):.1f}% over "
                f"the last 30 days; weekly trajectory is "
                f"{t['revenue_trend_shape']}."
            )
            CP.render_time_series_own_multi(
                {
                    "weeks":  t["weeks"],
                    "series": [{"name": "Revenue", "values": t["revenue"]}],
                    "y_label": "Weekly revenue ($)",
                },
                title="Revenue trajectory",
                takeaway=takeaway,
                height=300,
            )

    # Card 2.2 — Transaction trajectory (takeaway combines basket signal)
    with cols[1]:
        with st.container(border=True):
            CP.render_ask_about_this(
                key=f"ask_about_txn_traj_{merchant_id}",
                specialist="anomaly",
                prefill="What's driving the change in transaction count?",
            )
            txn_pct = t["txn_30d_pct"]
            bas_pct = t["basket_30d_pct"]
            takeaway = (
                f"Transactions {_dir(txn_pct)} {abs(txn_pct):.1f}%; "
                f"basket value {_dir(bas_pct)} {abs(bas_pct):.1f}% — your "
                f"topline is driven primarily by {t['txn_growth_driver']}."
            )
            CP.render_time_series_own_multi(
                {
                    "weeks":  t["weeks"],
                    "series": [{"name": "Transactions",
                                 "values": t["transactions"]}],
                    "y_label": "Weekly transactions",
                },
                title="Transaction trajectory",
                takeaway=takeaway,
                height=300,
            )

    # Card 2.3 — Hour × DOW heatmap (Pattern 3 own-only sequential)
    with cols[2]:
        with st.container(border=True):
            CP.render_ask_about_this(
                key=f"ask_about_hour_dow_{merchant_id}",
                specialist="trade",
                prefill="What does my hour-by-day pattern tell me about my customer base?",
            )
            h = D.hour_dow_heatmap_card(merchant_id)
            wd_we_phrase = (
                f"{h['wd_we_higher']} traffic is {h['wd_we_ratio']}% higher "
                "than the inverse"
                if h["wd_we_ratio"] > 0
                else "weekday and weekend traffic are roughly even"
            )
            takeaway = (
                f"Peak: {h['peak_dow']} {h['peak_hr']:02d}:00 "
                f"({h['peak_val']:,} txns). "
                f"Slowest: {h['slow_dow']} {h['slow_hr']:02d}:00 "
                f"({h['slow_val']:,}). {wd_we_phrase}."
            )
            CP.render_heatmap(
                h,
                title="Hour × day-of-week traffic",
                takeaway=takeaway,
                mode="own_only_sequential",
                height=300,
            )


def render_kpi_strip(merchant_id: str, filters: dict) -> None:  # noqa: ARG001
    """Phase 4.4 KPI strip: 5 Pattern 8 callouts in a row — Revenue,
    Transactions, Avg basket, Unique customers, Anomaly count. Each
    card carries an "Ask about this" affordance routing per
    V3_DASHBOARD_DESIGN.md Section 5.5.
    """
    from . import chart_patterns as CP

    k = D.kpi_strip(merchant_id)

    def _direction(pct: float) -> str:
        return "up" if pct > 0 else ("down" if pct < 0 else "flat")

    cols = st.columns(5, gap="small")

    # Card 1.1 — Revenue
    with cols[0]:
        CP.render_kpi_callout(
            label="Revenue this week",
            value=_fmt_money(k["revenue"]["value"]),
            delta_text=f"{k['revenue']['delta_pct']:+.1f}% vs prior 4w",
            delta_direction=_direction(k["revenue"]["delta_pct"]),
            sparkline=k["revenue"]["sparkline"],
            ask_about_this={
                "key":        f"ask_about_revenue_{merchant_id}",
                "specialist": "anomaly",
                "prefill":    "What's driving the change in my revenue this week?",
            },
        )

    # Card 1.2 — Transactions
    with cols[1]:
        CP.render_kpi_callout(
            label="Transactions this week",
            value=_fmt_int(k["transactions"]["value"]),
            delta_text=f"{k['transactions']['delta_pct']:+.1f}% vs prior 4w",
            delta_direction=_direction(k["transactions"]["delta_pct"]),
            sparkline=k["transactions"]["sparkline"],
            ask_about_this={
                "key":        f"ask_about_txns_{merchant_id}",
                "specialist": "anomaly",
                "prefill":    "What's driving the change in my transaction count this week?",
            },
        )

    # Card 1.3 — Avg basket (relabeled from "Avg transaction")
    with cols[2]:
        CP.render_kpi_callout(
            label="Avg basket",
            value=_fmt_money(k["avg_basket"]["value"]),
            delta_text=f"{k['avg_basket']['delta_pct']:+.1f}% vs prior 4w",
            delta_direction=_direction(k["avg_basket"]["delta_pct"]),
            sparkline=k["avg_basket"]["sparkline"],
            ask_about_this={
                "key":        f"ask_about_basket_{merchant_id}",
                "specialist": "demand",
                "prefill":    "What's changing about my average ticket?",
            },
        )

    # Card 1.4 — Unique customers
    with cols[3]:
        CP.render_kpi_callout(
            label="Unique customers",
            value=_fmt_int(k["unique_customers"]["value"]),
            delta_text=f"{k['unique_customers']['delta_pct']:+.1f}% vs prior 4w",
            delta_direction=_direction(k["unique_customers"]["delta_pct"]),
            sparkline=k["unique_customers"]["sparkline"],
            ask_about_this={
                "key":        f"ask_about_customers_{merchant_id}",
                "specialist": "demand",
                "prefill":    "Is my customer count growing or declining? Why?",
            },
        )

    # Card 1.5 — Anomaly count (NEW). Direction-aware:
    # concerning = below baseline by ≥ 15 % (alert / red);
    # notable    = above baseline by ≥ 15 % (informational, no alert).
    # Growth-mode merchants whose stores all run above baseline read
    # "All clear" + a notable count, rather than a red number.
    with cols[4]:
        a = k["anomaly"]
        concerning = a["concerning"]
        notable    = a["notable"]
        flag = "alert" if concerning > 0 else "clear"
        if concerning > 0:
            value = str(concerning)
            hint  = f"{concerning} concerning, {notable} notable"
        else:
            value = "0"
            hint  = (
                f"All clear, {notable} notable" if notable > 0
                else "All clear"
            )
        # Sparkline = trailing-12-week total flag count. Shows
        # overall anomaly volatility regardless of direction split;
        # the headline number + hint carry the direction story.
        CP.render_kpi_callout(
            label="Anomaly count",
            value=value,
            flag=flag,
            hint=hint,
            sparkline=[float(v) for v in a["trailing_total"]],
            ask_about_this={
                "key":        f"ask_about_anomaly_{merchant_id}",
                "specialist": "anomaly",
                "prefill":    "What's flagged this week?",
            },
        )


# ---------------------------------------------------------------------------
# Map — gradient color by 90-day txn volume, highlight selected stores
# ---------------------------------------------------------------------------

CLT_CENTER = [35.227, -80.843]


def render_map(merchant_id: str, filters: dict) -> None:
    """Charlotte basemap with own-merchant stores colored by 90-day
    transaction volume. Stores selected in the filter render at full
    opacity + larger radius; unselected stores are dimmed."""
    stores = D.stores_for(merchant_id).copy()
    selected = set(filters.get("stores") or [])

    fmap = folium.Map(
        location=CLT_CENTER,
        zoom_start=10,
        tiles="CartoDB positron",
        control_scale=True,
    )

    if stores.empty:
        st.markdown('<div class="panel-card" style="padding: 8px;">', unsafe_allow_html=True)
        st_folium(fmap, height=440, use_container_width=True,
                  returned_objects=[], key=f"map_{merchant_id}")
        st.markdown('</div>', unsafe_allow_html=True)
        return

    # Color scale from light to accent. Anchored to this merchant's
    # observed range so the gradient stays meaningful as we switch
    # merchants.
    vmin = float(stores["n_txns_90d"].min())
    vmax = float(stores["n_txns_90d"].max())
    cmap = LinearColormap([ACCENT_LIGHT, ACCENT], vmin=vmin, vmax=max(vmax, vmin + 1))

    # Radius scaling — base 4px → 12px depending on volume share. If a
    # filter restricts the visible set, selected stores get +3px to
    # reinforce the highlight.
    def _radius(n: float) -> float:
        share = (n - vmin) / (vmax - vmin) if vmax > vmin else 0.5
        return 4 + 8 * share

    for r in stores.itertuples():
        n = float(r.n_txns_90d)
        color = cmap(n)
        is_selected = r.store_id in selected if selected else True
        radius = _radius(n) + (3 if (selected and is_selected) else 0)
        opacity = 0.92 if is_selected else 0.20
        weight = 1.5 if is_selected else 0.5
        folium.CircleMarker(
            location=[r.latitude, r.longitude],
            radius=radius,
            color=color,
            weight=weight,
            fill=True,
            fill_color=color,
            fill_opacity=opacity,
            tooltip=(
                f"<b>{r.store_id}</b><br>"
                f"{r.neighborhood}<br>"
                f"{r.n_txns_90d:,} txns (90d)<br>"
                f"${r.revenue_90d:,.0f} revenue (90d)"
            ),
        ).add_to(fmap)

    # Map title row showing selection state
    title_html = (
        f'<div class="panel-card">'
        f'<div class="panel-title">Stores by 90-day transaction volume</div>'
        f'<div class="panel-sub">'
    )
    if selected:
        title_html += f'{len(selected)} of {len(stores)} stores selected (filtered)'
    else:
        title_html += f'All {len(stores)} stores · hover any marker for store-level metrics'
    title_html += "</div>"
    st.markdown(title_html, unsafe_allow_html=True)
    st_folium(
        fmap,
        height=420,
        use_container_width=True,
        returned_objects=[],
        key=f"map_{merchant_id}_{tuple(sorted(selected))}",
    )
    st.markdown('</div>', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Insights panel — sparkline + top SKUs
# ---------------------------------------------------------------------------

def render_insights_panel(merchant_id: str, filters: dict) -> None:
    fk = D._filters_key(filters)
    color = D.MERCHANT_COLOR[merchant_id]

    # -- Daily volume sparkline --
    df = D.daily_volume(merchant_id, fk)
    st.markdown('<div class="panel-card">'
                '<div class="panel-title">Daily transactions</div>',
                unsafe_allow_html=True)
    if df.empty:
        st.caption("No transactions in this filter.")
    else:
        fig = go.Figure(go.Scatter(
            x=df["day"], y=df["n"],
            mode="lines",
            line={"color": color, "width": 1.5},
            hovertemplate="%{x}<br>%{y:,} txns<extra></extra>",
            fill="tozeroy",
            fillcolor="rgba(15, 76, 129, 0.08)",
        ))
        fig.update_layout(**_plotly_layout(
            height=120,
            margin={"l": 24, "r": 8, "t": 4, "b": 24},
            xaxis={"showgrid": False, "linecolor": BORDER, "ticks": "",
                    "tickfont": {"size": 10, "color": TEXT_MUTED},
                    "tickformat": "%b %d", "nticks": 4},
            yaxis={"showgrid": False, "showticklabels": False,
                    "linecolor": "rgba(0,0,0,0)"},
            showlegend=False,
        ))
        st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CONFIG)
    st.markdown('</div>', unsafe_allow_html=True)

    # -- Top 5 SKUs --
    skus = D.top_skus(merchant_id, fk, n=5)
    st.markdown('<div class="panel-card">'
                '<div class="panel-title">Top 5 SKUs by revenue</div>',
                unsafe_allow_html=True)
    if skus.empty:
        st.caption("No matching SKUs in this filter.")
    else:
        fig = go.Figure(go.Bar(
            x=skus["revenue"].iloc[::-1],
            y=skus["name"].iloc[::-1],
            orientation="h",
            marker={"color": color},
            hovertemplate="<b>%{y}</b><br>$%{x:,.0f}<extra></extra>",
        ))
        fig.update_layout(**_plotly_layout(
            height=200,
            margin={"l": 8, "r": 16, "t": 4, "b": 28},
            xaxis={"showgrid": True, "tickprefix": "$", "tickformat": ",.0f",
                    "tickfont": {"size": 10}},
            yaxis={"showgrid": False, "tickfont": {"size": 11},
                    "automargin": True},
        ))
        st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CONFIG)
    st.markdown('</div>', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Detail charts — category mix + store performance
# ---------------------------------------------------------------------------

_CATEGORY_PALETTE = [
    "#0F4C81", "#3A6FA5", "#6F8FB8", "#C0563F", "#5B7B58",
    "#7B8294", "#B7791F", "#9A8BB5", "#3F8B92", "#7A4F3C",
    "#5C7A99", "#A5B07A", "#C28A7E",
]


def render_category_mix(merchant_id: str, filters: dict) -> None:
    df = D.category_mix(merchant_id, D._filters_key(filters))
    st.markdown('<div class="panel-card">'
                '<div class="panel-title">Revenue by category</div>',
                unsafe_allow_html=True)
    if df.empty:
        st.caption("No revenue in this filter.")
        st.markdown('</div>', unsafe_allow_html=True)
        return
    fig = go.Figure(go.Pie(
        labels=df["category"],
        values=df["revenue"],
        hole=0.55,
        marker={"colors": _CATEGORY_PALETTE * 3},
        hovertemplate="<b>%{label}</b><br>$%{value:,.0f}<br>%{percent}<extra></extra>",
        textinfo="none",
    ))
    fig.update_layout(**_plotly_layout(
        height=300,
        margin={"l": 8, "r": 8, "t": 16, "b": 8},
        showlegend=True,
        legend={"font": {"size": 11}, "orientation": "v",
                  "yanchor": "middle", "y": 0.5,
                  "xanchor": "left",   "x": 1.0},
    ))
    st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CONFIG)
    st.markdown('</div>', unsafe_allow_html=True)


def render_store_performance(merchant_id: str, filters: dict) -> None:
    df = D.store_performance(merchant_id, D._filters_key(filters))
    color = D.MERCHANT_COLOR[merchant_id]
    selected = set(filters.get("stores") or [])
    st.markdown('<div class="panel-card">'
                '<div class="panel-title">Stores by 90-day transactions</div>',
                unsafe_allow_html=True)
    if df.empty:
        st.caption("No stores.")
        st.markdown('</div>', unsafe_allow_html=True)
        return
    # If a store filter is active, dim unselected rows visually by using
    # a lighter color for those bars.
    bar_colors = [
        color if (not selected or s in selected) else ACCENT_LIGHT
        for s in df["store_id"]
    ]
    fig = go.Figure(go.Bar(
        x=df["n_txns"].iloc[::-1],
        y=df["store_id"].iloc[::-1],
        orientation="h",
        marker={"color": list(reversed(bar_colors))},
        hovertemplate="<b>%{y}</b><br>%{customdata}<br>%{x:,} txns<extra></extra>",
        customdata=df["neighborhood"].iloc[::-1],
    ))
    fig.update_layout(**_plotly_layout(
        height=max(220, 16 * len(df)),
        margin={"l": 8, "r": 16, "t": 4, "b": 32},
        xaxis={"showgrid": True, "tickformat": ",.0f"},
        yaxis={"showgrid": False, "tickfont": {"size": 10},
                "automargin": True},
    ))
    st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CONFIG)
    st.markdown('</div>', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Payment Intelligence — 4 charts in a row
# ---------------------------------------------------------------------------

# Fixed labels for stable legend ordering across reruns.
_PAYMENT_TYPES = ["credit", "debit"]
_PAYMENT_TYPE_COLORS = {"credit": ACCENT, "debit": "#6F8FB8"}

_NETWORKS = ["visa", "mc", "amex", "discover"]
_NETWORK_COLORS = {"visa": ACCENT, "mc": "#C0563F", "amex": "#5B7B58", "discover": "#B7791F"}

_ENTRY_MODES = ["chip", "contactless", "swipe", "manual"]
_ENTRY_MODE_COLORS = {
    "chip":        ACCENT,
    "contactless": "#3A6FA5",
    "swipe":       "#9A8BB5",
    "manual":      "#C0563F",
}

_WALLET_COLORS = {"apple": "#1A1F2E", "google": "#0F4C81", "samsung": "#3A6FA5"}


def render_payment_intelligence(merchant_id: str, filters: dict) -> None:
    """Four charts in a single row exposing payment fields unique to a
    Verifone-grade panel: method mix, card network, entry mode trend,
    wallet adoption."""
    fk = D._filters_key(filters)

    st.markdown(
        '<div style="font-size:13px;letter-spacing:0.06em;text-transform:uppercase;'
        'color:var(--accent);font-weight:600;margin:18px 0 6px;">Payment intelligence</div>'
        '<div style="font-size:12.5px;color:var(--text-muted);margin:0 0 10px;">'
        'Verifone captures card-network, entry-mode, and wallet fields on every '
        'transaction — most merchants do not see these in standard POS reports.'
        '</div>',
        unsafe_allow_html=True,
    )

    c1, c2, c3, c4 = st.columns(4, gap="small")
    with c1:
        _render_payment_method(D.payment_method_mix(merchant_id, fk))
    with c2:
        _render_card_network(D.card_network_mix(merchant_id, fk))
    with c3:
        _render_entry_mode_trend(D.entry_mode_trend(merchant_id, fk))
    with c4:
        _render_wallet_adoption(D.wallet_adoption(merchant_id, fk))


def _render_payment_method(df: pd.DataFrame) -> None:
    st.markdown('<div class="panel-card">'
                '<div class="panel-title">Payment method</div>'
                '<div class="panel-sub">credit vs debit</div>',
                unsafe_allow_html=True)
    if df.empty:
        st.caption("No data in this filter.")
        st.markdown('</div>', unsafe_allow_html=True)
        return
    colors = [_PAYMENT_TYPE_COLORS.get(lbl, ACCENT) for lbl in df["label"]]
    fig = go.Figure(go.Pie(
        labels=df["label"], values=df["n"], hole=0.55,
        marker={"colors": colors},
        textinfo="label+percent",
        textfont={"size": 11, "color": "#fff"},
        hovertemplate="<b>%{label}</b><br>%{value:,} txns<br>%{percent}<extra></extra>",
    ))
    fig.update_layout(**_plotly_layout(
        height=220, margin={"l": 8, "r": 8, "t": 6, "b": 6},
        showlegend=False,
    ))
    st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CONFIG)
    st.markdown('</div>', unsafe_allow_html=True)


def _render_card_network(df: pd.DataFrame) -> None:
    st.markdown('<div class="panel-card">'
                '<div class="panel-title">Card network</div>'
                '<div class="panel-sub">Visa · Mastercard · Amex · Discover</div>',
                unsafe_allow_html=True)
    if df.empty:
        st.caption("No data in this filter.")
        st.markdown('</div>', unsafe_allow_html=True)
        return
    colors = [_NETWORK_COLORS.get(lbl, ACCENT) for lbl in df["label"]]
    fig = go.Figure(go.Pie(
        labels=df["label"], values=df["n"], hole=0.55,
        marker={"colors": colors},
        textinfo="label+percent",
        textfont={"size": 11, "color": "#fff"},
        hovertemplate="<b>%{label}</b><br>%{value:,} txns<br>%{percent}<extra></extra>",
    ))
    fig.update_layout(**_plotly_layout(
        height=220, margin={"l": 8, "r": 8, "t": 6, "b": 6},
        showlegend=False,
    ))
    st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CONFIG)
    st.markdown('</div>', unsafe_allow_html=True)


def _render_entry_mode_trend(df: pd.DataFrame) -> None:
    st.markdown('<div class="panel-card">'
                '<div class="panel-title">Entry mode over time</div>'
                '<div class="panel-sub">stacked daily share</div>',
                unsafe_allow_html=True)
    if df.empty:
        st.caption("No data in this filter.")
        st.markdown('</div>', unsafe_allow_html=True)
        return
    modes = [m for m in _ENTRY_MODES if m in df.columns]
    fig = go.Figure()
    for m in modes:
        fig.add_trace(go.Scatter(
            x=df["day"], y=df[m],
            mode="lines",
            name=m,
            stackgroup="entry",
            line={"width": 0.5, "color": _ENTRY_MODE_COLORS.get(m, ACCENT)},
            fillcolor=_ENTRY_MODE_COLORS.get(m, ACCENT),
            hovertemplate="<b>%{x|%b %d}</b><br>" + m + ": %{y:,}<extra></extra>",
        ))
    fig.update_layout(**_plotly_layout(
        height=220, margin={"l": 28, "r": 8, "t": 6, "b": 32},
        showlegend=True,
        legend={"orientation": "h", "y": -0.28, "x": 0,
                 "xanchor": "left", "font": {"size": 10}},
        xaxis={"tickformat": "%b %d", "tickfont": {"size": 10},
                "linecolor": BORDER, "gridcolor": BORDER, "ticks": "outside"},
        yaxis={"tickfont": {"size": 10}, "linecolor": BORDER,
                "gridcolor": BORDER, "ticks": "outside", "rangemode": "tozero"},
    ))
    st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CONFIG)
    st.markdown('</div>', unsafe_allow_html=True)


def _render_wallet_adoption(w: dict) -> None:
    pct = w.get("wallet_pct", 0.0)
    total = w.get("contactless_total", 0)
    breakdown = w.get("wallet_breakdown", pd.DataFrame())
    st.markdown('<div class="panel-card">'
                '<div class="panel-title">Mobile wallet adoption</div>'
                '<div class="panel-sub">share of contactless txns</div>',
                unsafe_allow_html=True)
    if not total:
        st.caption("No contactless transactions in this filter.")
        st.markdown('</div>', unsafe_allow_html=True)
        return
    # Headline metric: percentage with a wallet
    st.markdown(
        f'<div style="font-size:30px;font-weight:600;color:var(--accent);'
        f'letter-spacing:-0.02em;font-variant-numeric:tabular-nums;line-height:1.05;">'
        f'{pct:.1f}%</div>'
        f'<div style="font-size:11px;color:var(--text-muted);letter-spacing:0.05em;'
        f'text-transform:uppercase;margin:2px 0 12px;">'
        f'of {total:,} contactless transactions</div>',
        unsafe_allow_html=True,
    )
    # Apple / Google / Samsung breakdown bar
    if not breakdown.empty:
        colors = [_WALLET_COLORS.get(w, ACCENT) for w in breakdown["wallet"]]
        fig = go.Figure(go.Bar(
            x=breakdown["n"].iloc[::-1],
            y=breakdown["wallet"].iloc[::-1],
            orientation="h",
            marker={"color": list(reversed(colors))},
            hovertemplate="<b>%{y}</b><br>%{x:,} txns<extra></extra>",
        ))
        fig.update_layout(**_plotly_layout(
            height=120, margin={"l": 8, "r": 16, "t": 4, "b": 24},
            xaxis={"tickformat": ",.0f", "tickfont": {"size": 10}},
            yaxis={"tickfont": {"size": 11}, "automargin": True},
        ))
        st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CONFIG)
    st.markdown('</div>', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Time Patterns — hour × day-of-week heatmap
# ---------------------------------------------------------------------------

# SQLite's strftime('%w', …) yields 0=Sunday … 6=Saturday. The heatmap
# is read top-to-bottom as Monday → Sunday for executive convention.
_DOW_ORDER       = [1, 2, 3, 4, 5, 6, 0]   # Mon, Tue, …, Sun
_DOW_LABELS      = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def render_time_patterns(merchant_id: str, filters: dict) -> None:
    """24×7 heatmap showing when the merchant is busiest."""
    grid = D.hour_dow_heatmap(merchant_id, D._filters_key(filters))
    st.markdown(
        '<div style="font-size:13px;letter-spacing:0.06em;text-transform:uppercase;'
        'color:var(--accent);font-weight:600;margin:14px 0 6px;">Time patterns</div>'
        '<div style="font-size:12.5px;color:var(--text-muted);margin:0 0 10px;">'
        'Verifone captures full timestamps on every transaction, enabling '
        'operational planning at hour-by-day granularity.'
        '</div>',
        unsafe_allow_html=True,
    )
    st.markdown('<div class="panel-card">'
                '<div class="panel-title">Transactions by hour of day &times; day of week</div>',
                unsafe_allow_html=True)
    if grid.empty or grid.values.sum() == 0:
        st.caption("No data in this filter.")
        st.markdown('</div>', unsafe_allow_html=True)
        return
    # Reorder rows to Mon → Sun
    z = grid.reindex(index=_DOW_ORDER).values
    fig = go.Figure(go.Heatmap(
        z=z,
        x=[f"{h:02d}" for h in range(24)],
        y=_DOW_LABELS,
        colorscale=[[0, "#FFFFFF"], [0.5, ACCENT_LIGHT], [1, ACCENT]],
        hovertemplate="<b>%{y} %{x}:00</b><br>%{z:,} txns<extra></extra>",
        colorbar={"thickness": 12, "outlinewidth": 0,
                   "tickfont": {"size": 10, "color": TEXT_MUTED}},
    ))
    fig.update_layout(**_plotly_layout(
        height=260,
        margin={"l": 36, "r": 24, "t": 4, "b": 32},
        xaxis={"side": "bottom", "tickfont": {"size": 10},
                "showgrid": False, "linecolor": BORDER},
        yaxis={"tickfont": {"size": 11}, "showgrid": False, "linecolor": BORDER,
                "autorange": "reversed"},
    ))
    st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CONFIG)
    st.markdown('</div>', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Customer Engagement — observable metrics only
# ---------------------------------------------------------------------------

def render_customer_engagement(merchant_id: str, filters: dict) -> None:
    """Replaces the prior Customer Insights expander. All metrics are
    derivable from observable transaction data (no synthetic-construct
    affinity types or behavioral segments)."""
    eng = D.customer_engagement(merchant_id, D._filters_key(filters))

    with st.expander("Customer Engagement", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            _render_txn_freq(eng.get("txn_freq", pd.DataFrame()))
        with c2:
            _render_recency(eng.get("recency", {}))
        c3, c4 = st.columns(2)
        with c3:
            _render_revenue_concentration(eng.get("revenue_concentration", {}))
        with c4:
            _render_promo_redemption(eng.get("top_promos", pd.DataFrame()))


def _ci_card_open(title: str, sub: str = "") -> None:
    sub_html = f'<div class="panel-sub">{sub}</div>' if sub else ""
    st.markdown(
        f'<div class="panel-card" style="height: 100%;">'
        f'<div class="panel-title">{title}</div>{sub_html}',
        unsafe_allow_html=True,
    )


def _ci_card_close() -> None:
    st.markdown("</div>", unsafe_allow_html=True)


def _hbar(df: pd.DataFrame, x_col: str, y_col: str, color: str = ACCENT,
          height: int = 220, x_fmt: str = ",.0f", x_prefix: str = "",
          x_suffix: str = "") -> None:
    fig = go.Figure(go.Bar(
        x=df[x_col].iloc[::-1],
        y=df[y_col].iloc[::-1],
        orientation="h",
        marker={"color": color},
        hovertemplate=f"<b>%{{y}}</b><br>{x_prefix}%{{x:{x_fmt}}}{x_suffix}<extra></extra>",
    ))
    fig.update_layout(**_plotly_layout(
        height=height,
        margin={"l": 8, "r": 16, "t": 4, "b": 28},
        xaxis={"showgrid": True, "tickprefix": x_prefix, "tickformat": x_fmt,
                "ticksuffix": x_suffix, "tickfont": {"size": 10}},
        yaxis={"showgrid": False, "tickfont": {"size": 11}, "automargin": True},
    ))
    st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CONFIG)


def _render_txn_freq(df: pd.DataFrame) -> None:
    _ci_card_open("Transactions per customer",
                   "How concentrated is engagement?")
    if df.empty:
        st.caption("No data in this filter.")
    else:
        df = df.rename(columns={"bucket": "Transactions"})
        _hbar(df, "n_customers", "Transactions", color=ACCENT)
    _ci_card_close()


def _render_recency(rec: dict) -> None:
    _ci_card_open("Customer recency",
                   "Active in last 30 days vs lapsed")
    total  = rec.get("total_customers", 0)
    active = rec.get("active_30d", 0)
    lapsed = rec.get("lapsed_30d", 0)
    active_pct = rec.get("active_pct", 0.0)
    lapsed_pct = 100.0 - active_pct if total else 0.0
    html = (
        '<div style="display:flex;justify-content:space-around;gap:12px;padding:18px 4px 8px;">'
        '  <div style="text-align:center;">'
        f'    <div style="font-size:26px;font-weight:600;color:var(--accent);font-variant-numeric:tabular-nums;">{active:,}</div>'
        f'    <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.06em;color:var(--text-muted);">Active (last 30d)</div>'
        f'    <div style="font-size:12px;color:var(--text-2);margin-top:2px;">{active_pct:.1f}%</div>'
        '  </div>'
        '  <div style="text-align:center;">'
        f'    <div style="font-size:26px;font-weight:600;color:var(--text-muted);font-variant-numeric:tabular-nums;">{lapsed:,}</div>'
        f'    <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.06em;color:var(--text-muted);">Lapsed (30d+)</div>'
        f'    <div style="font-size:12px;color:var(--text-2);margin-top:2px;">{lapsed_pct:.1f}%</div>'
        '  </div>'
        '</div>'
        f'<div style="font-size:11px;color:var(--text-muted);text-align:center;margin-top:4px;">'
        f'Total customers: {total:,}</div>'
    )
    st.markdown(html, unsafe_allow_html=True)
    _ci_card_close()


def _render_revenue_concentration(rc: dict) -> None:
    _ci_card_open("Revenue concentration",
                   "Share of revenue from top customers")
    top10 = rc.get("pct_top10", 0.0) or 0.0
    top20 = rc.get("pct_top20", 0.0) or 0.0
    top50 = rc.get("pct_top50", 0.0) or 0.0
    df = pd.DataFrame({
        "Cohort":     ["Top 10%", "Top 20%", "Top 50%"],
        "Rev share":  [top10, top20, top50],
    })
    _hbar(df, "Rev share", "Cohort", color="#3A6FA5",
           x_fmt=".1f", x_suffix="%")
    _ci_card_close()


def _render_promo_redemption(df: pd.DataFrame) -> None:
    _ci_card_open("Top promos by redemption rate",
                   "% of customers who redeemed each promo")
    if df.empty:
        st.caption("No promo redemptions in this filter.")
    else:
        df = df.rename(columns={"promo_name": "Promo"})
        _hbar(df, "redemption_rate_pct", "Promo",
               color="#5B7B58", x_fmt=".1f", x_suffix="%")
    _ci_card_close()
