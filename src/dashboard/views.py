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
# KPI row — Phase 4.5 (2/2) removed the v2.5 ``render_kpi_row`` compat
# shim. v3 callers use ``render_kpi_strip`` directly (Phase 4.4a).
# ---------------------------------------------------------------------------


def render_customers_section(merchant_id: str, filters: dict) -> None:
    """Phase 4.4 Section 5 — Customers. Three cards in an equal-width row:

    - Card 5.1 New vs returning (Pattern 2 own-bars, with trend delta).
    - Card 5.2 Transactions per customer (Pattern 2 own-bars, 5
      visit-count buckets with cohort revenue share in the takeaway).
    - Card 5.3 Customer home geography (Pattern 6 sequential mode,
      reuses ``customer_home_density`` from 4.2e, includes the
      customer-coverage footnote).
    """
    from . import chart_patterns as CP

    cols = st.columns(3, gap="small")

    # Card 5.1 — New vs returning
    with cols[0]:
        with st.container(border=True):
            ask_5_1 = {
                "key":        f"ask_about_new_ret_{merchant_id}",
                "specialist": "demand",
                "prefill":    (
                    "What's the composition of my customer base this "
                    "week — are new customers growing or my base growing?"
                ),
            }
            nvr = D.new_vs_returning(merchant_id, filters=filters)
            if nvr["total_count"] == 0:
                st.caption("_No customer activity in the recent week._")
            else:
                direction = (
                    "up" if nvr["new_pct_delta_pp"] > 0
                    else "down" if nvr["new_pct_delta_pp"] < 0
                    else "flat"
                )
                takeaway = (
                    f"{nvr['new_pct']:.0f}% of this week's customers "
                    f"are new; {nvr['returning_pct']:.0f}% are returning. "
                    f"New-customer share is {direction} "
                    f"{abs(nvr['new_pct_delta_pp']):.1f}pp vs prior 4 weeks."
                )
                CP.render_horizontal_bars_own(
                    {
                        "labels": ["Returning", "New"],
                        "values": [nvr["returning_count"], nvr["new_count"]],
                        "x_label": "Customers",
                        "value_format": ",.0f",
                    },
                    title="New vs returning customers this week",
                    takeaway=takeaway,
                    height=260,
                    ask_about_this=ask_5_1,
                )

    # Card 5.2 — Transactions per customer
    with cols[1]:
        with st.container(border=True):
            ask_5_2 = {
                "key":        f"ask_about_freq_{merchant_id}",
                "specialist": "demand",
                "prefill":    (
                    "What does my customer frequency distribution "
                    "tell me about loyalty?"
                ),
            }
            freq = D.transactions_per_customer(merchant_id, filters=filters)
            if not freq["labels"]:
                st.caption("_No customer-frequency data._")
            else:
                if freq["top_cohort"] == "11+":
                    cohort_phrase = "Your top cohort (11+ visits in 90 days)"
                else:
                    cohort_phrase = f"Your top cohort ({freq['top_cohort']} visits in 90 days)"
                takeaway = (
                    f"{cohort_phrase} is {freq['top_cohort_cust_pct']:.0f}% of "
                    f"your customers but generates "
                    f"{freq['top_cohort_rev_pct']:.0f}% of revenue. "
                    f"{freq['n_one_visit']:,} customer"
                    f"{'s' if freq['n_one_visit'] != 1 else ''} "
                    "shopped just once."
                )
                CP.render_horizontal_bars_own(
                    freq,
                    title="Transactions per customer (90d)",
                    takeaway=takeaway,
                    height=260,
                    ask_about_this=ask_5_2,
                )

    # Card 5.3 — Customer home geography
    with cols[2]:
        with st.container(border=True):
            ask_5_3 = {
                "key":        f"ask_about_home_geo_{merchant_id}",
                "specialist": "trade",
                "prefill":    "Where do my customers live relative to my stores?",
            }
            chart_data = D.customer_home_density(merchant_id, filters=filters)
            nbhds = chart_data["neighborhoods"]
            if not nbhds:
                st.caption("_No customer-home data to map._")
            else:
                # Pct living in served neighborhoods = 100 - pct_underserved.
                pct_served = round(100 - chart_data["pct_underserved"], 1)
                densest = chart_data["densest_underserved"]
                if densest is not None:
                    takeaway = (
                        f"{pct_served:.0f}% of your customers live in "
                        "neighborhoods where you have at least one store. "
                        f"Densest customer area without a nearby store: "
                        f"{densest['name']} ({densest['n_customers']:,} customers)."
                    )
                else:
                    takeaway = (
                        f"{pct_served:.0f}% of your customers live in "
                        "neighborhoods where you have at least one store; "
                        "no under-served neighborhoods in the panel."
                    )
                polygons = []
                for n in nbhds:
                    tip = (
                        f"<b>{n['name']}</b><br>"
                        f"{n['n_customers']:,} home customers<br>"
                        f"{n['own_n_stores']} own store(s)"
                        + ("<br><i>under-served</i>" if n["is_underserved"] else "")
                    )
                    polygons.append({
                        "name":    n["name"],
                        "value":   n["n_customers"],
                        "tooltip": tip,
                    })
                CP.render_neighborhood_map(
                    {
                        "polygons":    polygons,
                        "own_stores":  chart_data["own_markers"],
                        "peer_stores": [],
                        "value_label": "Customers (count)",
                        "map_key":     f"5_3_{merchant_id}",
                        "footnote":    chart_data.get("footnote", ""),
                    },
                    title="Customer home geography",
                    takeaway=takeaway,
                    mode="sequential",
                    height=300,
                    ask_about_this=ask_5_3,
                )


def render_catalog_section(merchant_id: str, filters: dict) -> None:
    """Phase 4.4 Section 4 — Catalog. Two cards in a 40/60 row:

    - Card 4.1 Category mix (Pattern 2 own-bars, top 8 + "Other"
      roll-up). Replaces v2.5's category-mix donut.
    - Card 4.2 SKU performance (Pattern 9 with a Top/Bottom toggle).
      The toggle persists per-merchant in session state so switching
      merchants doesn't reset the view.
    """
    from . import chart_patterns as CP

    cols = st.columns([40, 60], gap="small")
    state = st.session_state
    state.setdefault("sku_view_by_merchant", {})

    # Card 4.1 — Category mix
    with cols[0]:
        with st.container(border=True):
            # Per-viewer affordance routing for Card 4.1 — Demand
            # specialist for all three segments but different pre-fill.
            prefill_by_segment = {
                "KRG": ("Where am I over- or under-indexed in my basket "
                         "mix vs peers?"),
                "ACM": ("Where am I over- or under-indexed in my basket "
                         "mix vs peers?"),
                "WDX": ("Where am I over- or under-indexed in my basket "
                         "mix vs peers?"),
                "TBL": ("What does my menu mix look like? Where am I "
                         "most concentrated?"),
                "TJX": ("What does my category mix look like? Where am I "
                         "concentrated?"),
            }
            ask_4_1 = {
                "key":        f"ask_about_cat_mix_{merchant_id}",
                "specialist": "demand",
                "prefill":    prefill_by_segment.get(merchant_id, prefill_by_segment["KRG"]),
            }
            chart_data = D.category_share_own(merchant_id, top_n=8, filters=filters)
            if not chart_data["labels"]:
                st.caption("_No category-share data available._")
            else:
                names = chart_data["top3_names"]
                # Count "long tail" = remaining bars beyond top-3 (the
                # final "Other" bar collapses any below top-8 into one,
                # but the long-tail story should reflect everything past
                # the named top-3).
                n_long_tail = max(0, len(chart_data["labels"]) - 3)
                takeaway = (
                    f"Top 3 categories ({', '.join(names)}) account for "
                    f"{chart_data['top3_pct']:.1f}% of revenue. "
                    f"{n_long_tail} categor"
                    f"{'ies' if n_long_tail != 1 else 'y'} make up the "
                    "long tail."
                )
                CP.render_horizontal_bars_own(
                    chart_data,
                    title="Category mix",
                    takeaway=takeaway,
                    height=360,
                    ask_about_this=ask_4_1,
                )

    # Card 4.2 — SKU performance with top/bottom toggle
    with cols[1]:
        with st.container(border=True):
            current_view = state.sku_view_by_merchant.get(merchant_id, "top")
            # Toggle widget — persisted per merchant so a viewer switch
            # doesn't reset another merchant's chosen view.
            view = st.radio(
                "Show",
                options=["Top performers", "Underperformers"],
                index=0 if current_view == "top" else 1,
                horizontal=True,
                key=f"sku_view_{merchant_id}",
                label_visibility="collapsed",
            )
            view_key = "top" if view == "Top performers" else "bottom"
            state.sku_view_by_merchant[merchant_id] = view_key

            # Affordance routing depends on view + segment.
            if view_key == "top":
                ask_specialist = "demand"
                ask_prefill = (
                    "Tell me more about my top-performing SKUs and "
                    "what's driving them"
                )
            else:
                ask_specialist = "anomaly"
                # Pre-fill matches the segment's anomaly-SKU question.
                ask_prefill = {
                    "KRG": "Which SKUs or categories are spiking or dropping unusually?",
                    "ACM": "Which SKUs or categories are spiking or dropping unusually?",
                    "WDX": "Which SKUs or categories are spiking or dropping unusually?",
                    "TBL": "Are any menu items spiking or dropping unusually?",
                    "TJX": "Are any categories spiking or dropping unusually?",
                }.get(merchant_id, "Which SKUs are spiking or dropping unusually?")
            ask_4_2 = {
                "key":        f"ask_about_sku_{merchant_id}",
                "specialist": ask_specialist,
                "prefill":    ask_prefill,
            }

            chart_data = D.sku_performance(merchant_id, filters=filters)
            all_rows = chart_data["rows"]
            if not all_rows:
                st.caption("_No SKU data in the recent window._")
            else:
                if view_key == "top":
                    rows = sorted(
                        all_rows,
                        key=lambda r: r["recent_revenue"],
                        reverse=True,
                    )[:20]
                    n_decliners = sum(
                        1 for r in all_rows
                        if r["deviation_pct"] is not None
                        and r["deviation_pct"] <= -15.0
                    )
                    total_rev = sum(r["recent_revenue"] for r in all_rows) or 1.0
                    top10_rev = sum(r["recent_revenue"] for r in rows[:10])
                    top_sku = rows[0]
                    takeaway = (
                        f"Top SKU: {top_sku['sku_name']} "
                        f"(${top_sku['recent_revenue']:,.0f} this week). "
                        f"Top 10 SKUs account for "
                        f"{top10_rev / total_rev * 100:.0f}% of revenue."
                    )
                else:
                    # Underperformers — sort by deviation_pct asc.
                    # SKUs with no baseline (deviation_pct is None)
                    # can't be classified as decliners; drop them.
                    rows = sorted(
                        [r for r in all_rows if r["deviation_pct"] is not None],
                        key=lambda r: r["deviation_pct"],
                    )[:20]
                    n_decliners = sum(
                        1 for r in all_rows
                        if r["deviation_pct"] is not None
                        and r["deviation_pct"] <= -15.0
                    )
                    if not rows:
                        # Underperformers view with no SKU below the
                        # -15 % floor: swap the table for an empty-
                        # state block so the merchant sees a clear
                        # "nothing to act on" rather than an empty
                        # grid.
                        CP._render_card_header(
                            "SKU performance",
                            "No SKU-level decliners in the recent week.",
                            ask_about_this=ask_4_2,
                        )
                        CP.render_empty_state(
                            "Every SKU is within 15% of its first-4-week "
                            "baseline. No underperformers to investigate."
                        )
                        return
                    worst = rows[0]
                    takeaway = (
                        f"{n_decliners} SKUs declining >15% vs "
                        f"baseline. Largest drop: {worst['sku_name']} "
                        f"at {worst['deviation_pct']:+.1f}%."
                    )
                CP.render_table_with_drilldown(
                    {
                        "rows": rows,
                        "columns": [
                            {"key": "sku_name",       "label": "SKU",       "kind": "text"},
                            {"key": "category",       "label": "Category",  "kind": "text"},
                            {"key": "baseline_lines", "label": "Baseline/wk", "kind": "float"},
                            {"key": "recent_lines",   "label": "Recent (lines)", "kind": "int"},
                            {"key": "deviation_pct",  "label": "Δ vs baseline",
                             "kind": "pct", "diverging": True},
                            {"key": "recent_revenue", "label": "Revenue ($)", "kind": "float"},
                        ],
                    },
                    title="SKU performance",
                    takeaway=takeaway,
                    height=330,
                    ask_about_this=ask_4_2,
                )


def render_geography_section(merchant_id: str, filters: dict) -> None:
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
            ask_3_1 = {
                "key":        f"ask_about_geo_map_{merchant_id}",
                "specialist": "trade",
                "prefill":    (
                    "Which of my neighborhoods are over- or "
                    "under-performing, and is the issue mine or the "
                    "market's?"
                ),
            }
            chart_data = D.neighborhood_performance(merchant_id, filters=filters)
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
                    ask_about_this=ask_3_1,
                )

    # Card 3.2 — Store performance distribution
    with cols[1]:
        with st.container(border=True):
            ask_3_2 = {
                "key":        f"ask_about_store_perf_{merchant_id}",
                "specialist": "anomaly",
                "prefill":    (
                    "Which stores are showing unusual traffic this week?"
                ),
            }
            # Grocers carry the peer-neighborhood column via the A2
            # helper; TBL / TJX have no same-segment peers so use the
            # leaner own-only variant. Both produce the same row
            # shape minus the peer column.
            if D.has_same_segment_peers(merchant_id):
                chart_data = D.store_anomalies(merchant_id, filters=filters)
            else:
                chart_data = D.store_anomalies_own_only(merchant_id, filters=filters)
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
                    ask_about_this=ask_3_2,
                )


def render_performance_section(merchant_id: str, filters: dict) -> None:
    """Phase 4.4 Section 2 — Performance over time. Three cards in a
    row: Revenue trajectory, Transaction trajectory, Hour × DOW
    heatmap. Each carries an "Ask about this" affordance routing per
    V3_DASHBOARD_DESIGN.md Section 5.5.
    """
    from . import chart_patterns as CP

    t = D.performance_trajectory(merchant_id, filters=filters)

    def _dir(pct: float) -> str:
        return "up" if pct > 0 else ("down" if pct < 0 else "flat")

    cols = st.columns(3, gap="small")

    # Card 2.1 — Revenue trajectory
    with cols[0]:
        with st.container(border=True):
            ask_2_1 = {
                "key":        f"ask_about_rev_traj_{merchant_id}",
                "specialist": "anomaly",
                "prefill":    "What's behind the revenue trajectory I'm seeing?",
            }
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
                ask_about_this=ask_2_1,
            )

    # Card 2.2 — Transaction trajectory (takeaway combines basket signal)
    with cols[1]:
        with st.container(border=True):
            ask_2_2 = {
                "key":        f"ask_about_txn_traj_{merchant_id}",
                "specialist": "anomaly",
                "prefill":    "What's driving the change in transaction count?",
            }
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
                ask_about_this=ask_2_2,
            )

    # Card 2.3 — Hour × DOW heatmap (Pattern 3 own-only sequential)
    with cols[2]:
        with st.container(border=True):
            ask_2_3 = {
                "key":        f"ask_about_hour_dow_{merchant_id}",
                "specialist": "trade",
                "prefill":    "What does my hour-by-day pattern tell me about my customer base?",
            }
            h = D.hour_dow_heatmap_card(merchant_id, filters=filters)
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
                ask_about_this=ask_2_3,
            )


def render_kpi_strip(merchant_id: str, filters: dict) -> None:
    """Phase 4.4 KPI strip: 5 Pattern 8 callouts in a row — Revenue,
    Transactions, Avg basket, Unique customers, Anomaly count. Each
    card carries an "Ask about this" affordance routing per
    V3_DASHBOARD_DESIGN.md Section 5.5.
    """
    from . import chart_patterns as CP

    k = D.kpi_strip(merchant_id, filters=filters)

    def _direction(pct: float | None) -> str:
        if pct is None:
            return "flat"
        return "up" if pct > 0 else ("down" if pct < 0 else "flat")

    def _delta_text(pct: float | None) -> str:
        if pct is None:
            return "— (window too narrow)"
        return f"{pct:+.1f}% vs prior 4w"

    cols = st.columns(5, gap="small")

    # Card 1.1 — Revenue
    with cols[0]:
        CP.render_kpi_callout(
            label="Revenue this week",
            value=_fmt_money(k["revenue"]["value"]),
            delta_text=_delta_text(k["revenue"]["delta_pct"]),
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
            delta_text=_delta_text(k["transactions"]["delta_pct"]),
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
            delta_text=_delta_text(k["avg_basket"]["delta_pct"]),
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
            delta_text=_delta_text(k["unique_customers"]["delta_pct"]),
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



# Phase 4.4e removed the v2.5 dashboard carry-overs:
#   render_map, render_insights_panel        → obsoleted by Section 3 (Card 3.1)
#   render_time_patterns                      → obsoleted by Card 2.3 in Section 2
#   render_customer_engagement (+ 4 sub-helpers and the _ci_*/_hbar
#       primitives they used) → obsoleted by Section 5
