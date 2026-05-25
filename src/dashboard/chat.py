"""Chat panel — agent selector, suggested questions, per-merchant
chat history, free-form input.

Layout (top to bottom):
  1. Specialist-agent selector.
  2. Selected agent's description + 4 suggested-question buttons.
  3. "Clear chat history" button (only clears the current merchant).
  4. Scrollable chat history container (fixed height).
     Each turn renders as a `st.chat_message` user/assistant bubble.
     A pending click or free-form submission renders its streaming
     narration INSIDE this container (in the assistant bubble) so all
     agent activity stays visually inside the chat window.
  5. Free-form `st.chat_input` at the bottom — routes through the
     orchestrator on submit.

Per-merchant isolation: every read / write / clear goes through
`st.session_state.chat_messages_by_merchant[merchant_id]`. Switching the
merchant dropdown changes which bucket is displayed; no message ever
crosses between merchants. This is the UI-layer analog of the
`MerchantContext` binding on the agent side.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Callable

import streamlit as st

from . import agents as A
from . import chart_patterns as CP
from . import chart_takeaways as CT
from . import data as D
from . import questions as Q


# ---------------------------------------------------------------------------
# Telemetry footer — rendered INSIDE the chat panel so it participates
# in the panel's flex layout (was previously in app.py and added
# height below the panel).
# ---------------------------------------------------------------------------

def _render_telemetry_inline() -> None:
    """One-line cost / token telemetry below the chat input. Only
    rendered when at least one LLM call has been made this session."""
    try:
        from src.agents import llm as _llm
        totals = _llm.session_totals()
    except Exception:  # noqa: BLE001
        totals = None
    if not (totals and totals.get("calls", 0) > 0):
        return
    st.markdown(
        f'<div style="font-size:10px;color:var(--text-muted);'
        f'margin:4px 0 0 0;letter-spacing:0.02em;line-height:1.3;'
        f'text-align:right;">'
        f'{totals["calls"]} calls · '
        f'{totals["input_tokens"]:,} in / '
        f'{totals["output_tokens"]:,} out · '
        f'${totals["cost_usd"]:.4f}'
        f'</div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Per-merchant chat history (session state)
# ---------------------------------------------------------------------------

_HISTORY_KEY = "chat_messages_by_merchant"


def _ensure_history(merchant_id: str) -> list[dict]:
    state = st.session_state
    state.setdefault(_HISTORY_KEY, {})
    state[_HISTORY_KEY].setdefault(merchant_id, [])
    return state[_HISTORY_KEY][merchant_id]


def reset_history(merchant_id: str) -> None:
    """Empty ONLY the named merchant's history. Other merchants are
    preserved. Used by the explicit "Clear chat history" button."""
    state = st.session_state
    state.setdefault(_HISTORY_KEY, {})
    state[_HISTORY_KEY][merchant_id] = []


def _push(
    merchant_id: str,
    question: str,
    response: dict,
    *,
    qid: str | None = None,
) -> None:
    """Append a turn to the merchant's chat history.

    `qid` (when set) lets the history-replay path re-render the
    pattern chart associated with the question — kept as a separate
    field rather than mixed into `response` so it survives the
    `**response` spread without conflict.
    """
    _ensure_history(merchant_id).append({
        "ts":       datetime.now(),
        "question": question,
        "qid":      qid,
        **response,
    })


# ---------------------------------------------------------------------------
# Question → chart renderer registry
#
# Each entry maps a suggested-question ID to a function that fetches the
# question's data and renders the appropriate chart pattern. Called from
# both the live-turn path (after the agent's prose lands) and the
# history-replay path (on every rerun).
#
# Phase 4.1 wires A1 only. Phase 4.2 grows the registry as more
# questions land.
# ---------------------------------------------------------------------------

def _render_a1(merchant_id: str, filters: dict | None = None) -> None:
    """A1: University City weekly transaction trajectory (Pattern 1)."""
    chart_data = D.uc_decline_trajectory(merchant_id, filters=filters)
    if not chart_data.get("weeks"):
        st.caption("_No University City data available for this merchant._")
        return
    takeaway = CT.compute_takeaway("A1", merchant_id, filters=filters)

    CP.render_time_series_vs_peers(
        chart_data,
        title="University City weekly transactions",
        takeaway=takeaway,
        show_peers=chart_data.get("has_peers", True),
    )


def _render_p1(merchant_id: str, filters: dict | None = None) -> None:
    """P1: category × peer pricing heatmap (Pattern 3 cross-merchant diverging)."""
    chart_data = D.category_peer_pricing_gaps(merchant_id, filters=filters)
    if not chart_data["rows"]:
        st.caption("_No pricing data available for this merchant._")
        return

    # Takeaway centralized in chart_takeaways.compute_takeaway("P1", ...)
    # — see Phase 5.1.9 commit message for the architectural rationale.
    takeaway = CT.compute_takeaway("P1", merchant_id, filters=filters)

    CP.render_heatmap(
        chart_data,
        title="Your prices vs peer grocers, by category",
        takeaway=takeaway,
        mode="cross_merchant_diverging",
    )


def _render_p2(merchant_id: str, filters: dict | None = None) -> None:
    """P2: staple-tier vs non-food-tier pricing positioning (Pattern 2)."""
    chart_data = D.staple_vs_nonfood_pricing(merchant_id, filters=filters)
    if not chart_data["panel_a_data"]["categories"] and \
       not chart_data["panel_b_data"]["categories"]:
        st.caption("_No pricing data available for this merchant._")
        return
    takeaway = CT.compute_takeaway("P2", merchant_id, filters=filters)
    CP.render_cross_merchant_comparison(
        chart_data,
        title="Pricing positioning: staples vs non-food",
        takeaway=takeaway,
        mode="two_panel",
    )


def _render_p3(merchant_id: str, filters: dict | None = None) -> None:
    """P3: pricing-leverage scatter — volume × peer-gap quadrants (Pattern 4)."""
    chart_data = D.category_pricing_leverage(merchant_id, filters=filters)
    if not chart_data["points"]:
        st.caption("_No pricing data available for this merchant._")
        return
    takeaway = CT.compute_takeaway("P3", merchant_id, filters=filters)
    CP.render_scatter_with_peers(
        chart_data,
        title="Pricing leverage by category",
        takeaway=takeaway,
    )


def _render_d4(merchant_id: str, filters: dict | None = None) -> None:
    """D4: own share vs peer share — basket-mix scatter with parity line (Pattern 4)."""
    chart_data = D.category_share_vs_peer_share(merchant_id, filters=filters)
    if not chart_data["points"]:
        st.caption("_No basket-mix data available for this merchant._")
        return
    takeaway = CT.compute_takeaway("D4", merchant_id, filters=filters)
    CP.render_scatter_with_peers(
        chart_data,
        title="Your category mix vs peer-average",
        takeaway=takeaway,
    )


def _render_d7(merchant_id: str, filters: dict | None = None) -> None:
    """D7: per-peer revenue gap decomposition (Pattern 5 waterfall × 2).

    Renders one waterfall per same-segment peer (peer_a + peer_b)
    stacked vertically. Each waterfall has six driver bars (Stores,
    Traffic/store, Basket, Ticket, Mix, Residual) — Stores isolated as
    the strategic / slow lever, the next three as per-store
    operational drivers. The takeaway names Stores explicitly and
    then the dominant per-store driver (or a joint pair when two
    per-store drivers sit within 2pp of each other — KRG↔WDX hits
    this zone).
    """
    chart_data = D.revenue_gap_decomposition(merchant_id, filters=filters)
    if not chart_data.get("has_peers"):
        st.caption("_No peer data available for revenue-gap decomposition._")
        return

    for peer_label in ("peer_a", "peer_b"):
        decomp = chart_data["per_peer"].get(peer_label)
        if not decomp:
            continue

        gap_pct   = decomp["total_gap_pct"]
        stores_pp = decomp["stores_pp"]
        dom_name  = decomp["dominant_per_store"]
        dom_pp    = decomp["dominant_pp"]
        tied      = decomp.get("tied_with") or []

        if not tied:
            per_store_phrase = (
                f"{dom_name.lower()} dominates at {dom_pp:+.1f}pp"
            )
        else:
            # Joint pair (or triple): name leader + each tied driver.
            both     = [(dom_name, dom_pp)] + tied
            names    = " + ".join(n.lower() for n, _ in both)
            magnitudes = " / ".join(f"{pp:+.1f}pp" for _, pp in both)
            per_store_phrase = (
                f"{names} together drive the per-store gap ({magnitudes})"
            )

        takeaway = (
            f"Stores contribute {stores_pp:+.1f}pp; "
            f"among per-store drivers, {per_store_phrase}."
        )

        peer_name = CP.peer_display(peer_label)
        title = f"vs {peer_name} ({gap_pct:+.1f}pp gap)"
        CP.render_waterfall(
            decomp,
            title=title,
            takeaway=takeaway,
            mode="cross_merchant",
        )


def _render_d3(merchant_id: str, filters: dict | None = None) -> None:
    """D3: basket-mix fingerprint vs peer-average (Pattern 2 diverging)."""
    chart_data = D.basket_mix_vs_peers(merchant_id, filters=filters)
    if not chart_data["categories"]:
        st.caption("_No basket-mix data available for this merchant._")
        return
    takeaway = CT.compute_takeaway("D3", merchant_id, filters=filters)
    CP.render_cross_merchant_comparison(
        chart_data,
        title="Your basket mix vs peer-average",
        takeaway=takeaway,
        mode="diverging",
    )


def _render_t_p1(merchant_id: str, filters: dict | None = None) -> None:
    """T-P1 (TBL): mean ticket trend per daypart (Pattern 1 own-multi)."""
    chart_data = D.tbl_daypart_ticket_trends(merchant_id, filters=filters)
    if not chart_data["series"]:
        st.caption("_No daypart-trend data available._")
        return
    takeaway = CT.compute_takeaway("T-P1", merchant_id, filters=filters)
    CP.render_time_series_own_multi(
        chart_data,
        title="Average ticket by daypart, weekly",
        takeaway=takeaway,
    )


def _render_t_p2(merchant_id: str, filters: dict | None = None) -> None:
    """T-P2 (TBL): per-category unit price trends (Pattern 1 own-multi)."""
    chart_data = D.category_unit_price_trends(merchant_id, top_n=6, filters=filters)
    if not chart_data["series"]:
        st.caption("_No category-price data available._")
        return
    takeaway = CT.compute_takeaway("T-P2", merchant_id, filters=filters)
    CP.render_time_series_own_multi(
        chart_data,
        title="Mean unit price by category, weekly",
        takeaway=takeaway,
    )


def _render_t_p3(merchant_id: str, filters: dict | None = None) -> None:
    """T-P3 (TBL): per-store mean-ticket distribution (Pattern 2 own-bars)."""
    chart_data = D.per_store_mean_ticket(merchant_id, filters=filters)
    if not chart_data["labels"]:
        st.caption("_No store ticket data available._")
        return
    takeaway = CT.compute_takeaway("T-P3", merchant_id, filters=filters)
    CP.render_horizontal_bars_own(
        chart_data,
        title="Mean ticket by store",
        takeaway=takeaway,
    )


def _render_t_a1(merchant_id: str, filters: dict | None = None) -> None:
    """T-A1 (TBL): per-store recent-vs-baseline traffic anomalies (Pattern 9)."""
    chart_data = D.store_anomalies_own_only(merchant_id, filters=filters)
    rows = chart_data["rows"]
    if not rows:
        st.caption("_No stores with enough baseline data to evaluate._")
        return
    takeaway = CT.compute_takeaway("T-A1", merchant_id, filters=filters)
    CP.render_table_with_drilldown(
        {
            "rows": rows,
            "columns": [
                {"key": "store_id",      "label": "Store",        "kind": "text"},
                {"key": "neighborhood",  "label": "Neighborhood", "kind": "text"},
                {"key": "baseline",      "label": "Baseline (txns/wk)", "kind": "float"},
                {"key": "recent",        "label": "Recent (txns)", "kind": "int"},
                {"key": "deviation_pct", "label": "Δ vs baseline", "kind": "pct", "diverging": True},
            ],
        },
        title="Per-store traffic vs your first-4-week baseline",
        takeaway=takeaway,
    )


def _render_t_a2(merchant_id: str, filters: dict | None = None) -> None:
    """T-A2 (TBL): per-SKU recent-vs-baseline volume anomalies (Pattern 9)."""
    chart_data = D.sku_anomalies(merchant_id, top_n=25, filters=filters)
    rows = chart_data["rows"]
    if not rows:
        st.caption("_No SKU-volume data in the recent window._")
        return
    takeaway = CT.compute_takeaway("T-A2", merchant_id, filters=filters)
    CP.render_table_with_drilldown(
        {
            "rows": rows,
            "columns": [
                {"key": "sku_name",      "label": "Menu item",     "kind": "text"},
                {"key": "category",      "label": "Category",      "kind": "text"},
                {"key": "baseline",      "label": "Baseline (lines/wk)", "kind": "float"},
                {"key": "recent",        "label": "Recent (lines)", "kind": "int"},
                {"key": "deviation_pct", "label": "Δ vs baseline", "kind": "pct", "diverging": True},
            ],
        },
        title="Top menu-item volume deviations vs your first-4-week baseline",
        takeaway=takeaway,
    )


def _render_t_a3(merchant_id: str, filters: dict | None = None) -> None:
    """T-A3 (TBL): day-of-week × daypart ratio heatmap (Pattern 3 own-only)."""
    chart_data = D.day_daypart_heatmap(merchant_id, filters=filters)
    takeaway = CT.compute_takeaway("T-A3", merchant_id, filters=filters)
    CP.render_heatmap(
        chart_data,
        title="Day × daypart recent-week vs first-4-week baseline",
        takeaway=takeaway,
        mode="own_only_diverging",
    )


def _render_t_d1(merchant_id: str, filters: dict | None = None) -> None:
    """T-D1 (TBL): menu-category share bars (Pattern 2 own-bars)."""
    chart_data = D.category_share_own(merchant_id, top_n=8, filters=filters)
    if not chart_data["labels"]:
        st.caption("_No category-share data available._")
        return
    takeaway = CT.compute_takeaway("T-D1", merchant_id, filters=filters)
    CP.render_horizontal_bars_own(
        chart_data,
        title="Category share of revenue",
        takeaway=takeaway,
    )


def _render_t_d2(merchant_id: str, filters: dict | None = None) -> None:
    """T-D2 (TBL): category share trajectory (Pattern 1 own-multi)."""
    chart_data = D.category_share_trajectory(merchant_id, top_n=6, filters=filters)
    if not chart_data["series"]:
        st.caption("_No category-share trajectory data available._")
        return
    takeaway = CT.compute_takeaway("T-D2", merchant_id, filters=filters)
    CP.render_time_series_own_multi(
        chart_data,
        title="Category share of revenue, weekly",
        takeaway=takeaway,
    )


def _render_t_d3(merchant_id: str, filters: dict | None = None) -> None:
    """T-D3 (TBL): revenue-change decomposition (Pattern 5 own-vs-own)."""
    chart_data = D.revenue_change_decomposition_own(merchant_id, filters=filters)
    if not chart_data.get("has_data"):
        st.caption("_Insufficient data to decompose this week's change._")
        return
    takeaway = CT.compute_takeaway("T-D3", merchant_id, filters=filters)
    CP.render_waterfall(
        chart_data,
        title="Revenue change drivers vs first-4-week baseline",
        takeaway=takeaway,
        mode="own_vs_own_baseline",
    )


def _render_r_p1(merchant_id: str, filters: dict | None = None) -> None:
    """R-P1 (TJX): per-category unit-price trends (Pattern 1 own-multi).

    Same data shape as T-P2 — reusing ``category_unit_price_trends``.
    The merchant-phrased takeaway changes to frame it as a ticket
    trend (R-P1) rather than a unit-price drift (T-P2).
    """
    chart_data = D.category_unit_price_trends(merchant_id, top_n=6, filters=filters)
    if not chart_data["series"]:
        st.caption("_No category-ticket data available._")
        return
    takeaway = CT.compute_takeaway("R-P1", merchant_id, filters=filters)
    CP.render_time_series_own_multi(
        chart_data,
        title="Mean ticket by category, weekly",
        takeaway=takeaway,
    )


def _render_r_p2(merchant_id: str, filters: dict | None = None) -> None:
    """R-P2 (TJX): per-category price spread table (Pattern 9)."""
    chart_data = D.category_price_spread(merchant_id, filters=filters)
    rows = chart_data["rows"]
    if not rows:
        st.caption("_No price-spread data available._")
        return
    takeaway = CT.compute_takeaway("R-P2", merchant_id, filters=filters)
    CP.render_table_with_drilldown(
        {
            "rows": rows,
            "columns": [
                {"key": "category",     "label": "Category",  "kind": "text"},
                {"key": "min_price",    "label": "Min",       "kind": "float"},
                {"key": "median_price", "label": "Median",    "kind": "float"},
                {"key": "max_price",    "label": "Max",       "kind": "float"},
                {"key": "spread_ratio", "label": "Max / Min", "kind": "ratio"},
                {"key": "n_lines",      "label": "Lines",     "kind": "int"},
            ],
        },
        title="Per-category price spread",
        takeaway=takeaway,
    )


def _render_r_p3(merchant_id: str, filters: dict | None = None) -> None:
    """R-P3 (TJX): ticket-band split (Pattern 2 grouped bars)."""
    chart_data = D.ticket_band_distribution(merchant_id, filters=filters)
    if not chart_data["labels"]:
        st.caption("_No ticket-band data available._")
        return
    takeaway = CT.compute_takeaway("R-P3", merchant_id, filters=filters)
    CP.render_horizontal_bars_grouped(
        chart_data,
        title="Transactions and revenue by ticket band",
        takeaway=takeaway,
    )


def _render_r_a1(merchant_id: str, filters: dict | None = None) -> None:
    """R-A1 (TJX): per-store recent-vs-baseline anomalies (Pattern 9).

    Same helper as T-A1; the merchant-phrased takeaway matches the
    grocer-style A2 wording for consistency.
    """
    chart_data = D.store_anomalies_own_only(merchant_id, filters=filters)
    rows = chart_data["rows"]
    if not rows:
        st.caption("_No stores with enough baseline data to evaluate._")
        return
    takeaway = CT.compute_takeaway("R-A1", merchant_id, filters=filters)
    CP.render_table_with_drilldown(
        {
            "rows": rows,
            "columns": [
                {"key": "store_id",      "label": "Store",         "kind": "text"},
                {"key": "neighborhood",  "label": "Neighborhood",  "kind": "text"},
                {"key": "baseline",      "label": "Baseline (txns/wk)", "kind": "float"},
                {"key": "recent",        "label": "Recent (txns)", "kind": "int"},
                {"key": "deviation_pct", "label": "Δ vs baseline", "kind": "pct", "diverging": True},
            ],
        },
        title="Per-store traffic vs your first-4-week baseline",
        takeaway=takeaway,
    )


def _render_r_a2(merchant_id: str, filters: dict | None = None) -> None:
    """R-A2 (TJX): per-category volume anomalies (Pattern 9, no peer col).

    Reuses ``category_anomalies``; for TJX the peer column comes back
    None for every row (no same-segment peers in the lake), so the
    column is omitted entirely from the table.
    """
    chart_data = D.category_anomalies(merchant_id, filters=filters)
    rows = chart_data["rows"]
    if not rows:
        st.caption("_No category-volume data in the recent window._")
        return
    takeaway = CT.compute_takeaway("R-A2", merchant_id, filters=filters)
    CP.render_table_with_drilldown(
        {
            "rows": rows,
            "columns": [
                {"key": "category",      "label": "Category", "kind": "text"},
                {"key": "baseline",      "label": "Baseline (lines/wk)", "kind": "float"},
                {"key": "recent",        "label": "Recent (lines)", "kind": "int"},
                {"key": "deviation_pct", "label": "Δ vs baseline", "kind": "pct", "diverging": True},
            ],
        },
        title="Per-category recent-week volume vs your first-4-week baseline",
        takeaway=takeaway,
    )


def _render_r_a3(merchant_id: str, filters: dict | None = None) -> None:
    """R-A3 (TJX): day × week ratio heatmap (Pattern 3 own-only)."""
    chart_data = D.day_week_heatmap(merchant_id, filters=filters)
    takeaway = CT.compute_takeaway("R-A3", merchant_id, filters=filters)
    CP.render_heatmap(
        chart_data,
        title="Day-of-week recent vs first-4-week baseline",
        takeaway=takeaway,
        mode="own_only_diverging",
    )


def _render_r_d1(merchant_id: str, filters: dict | None = None) -> None:
    """R-D1 (TJX): category share bars (Pattern 2 own-bars).

    Shares ``category_share_own`` with T-D1; only the merchant-phrased
    title changes."""
    chart_data = D.category_share_own(merchant_id, top_n=8, filters=filters)
    if not chart_data["labels"]:
        st.caption("_No category-share data available._")
        return
    takeaway = CT.compute_takeaway("R-D1", merchant_id, filters=filters)
    CP.render_horizontal_bars_own(
        chart_data,
        title="Category share of revenue",
        takeaway=takeaway,
    )


def _render_r_d2(merchant_id: str, filters: dict | None = None) -> None:
    """R-D2 (TJX): category share trajectory (Pattern 1 own-multi)."""
    chart_data = D.category_share_trajectory(merchant_id, top_n=6, filters=filters)
    if not chart_data["series"]:
        st.caption("_No category-share trajectory data available._")
        return
    takeaway = CT.compute_takeaway("R-D2", merchant_id, filters=filters)
    CP.render_time_series_own_multi(
        chart_data,
        title="Category share of revenue, weekly",
        takeaway=takeaway,
    )


def _render_r_d3(merchant_id: str, filters: dict | None = None) -> None:
    """R-D3 (TJX): revenue-change decomposition (Pattern 5 own-vs-own)."""
    chart_data = D.revenue_change_decomposition_own(merchant_id, filters=filters)
    if not chart_data.get("has_data"):
        st.caption("_Insufficient data to decompose this week's change._")
        return
    takeaway = CT.compute_takeaway("R-D3", merchant_id, filters=filters)
    CP.render_waterfall(
        chart_data,
        title="Revenue change drivers vs first-4-week baseline",
        takeaway=takeaway,
        mode="own_vs_own_baseline",
    )


def _render_a2(merchant_id: str, filters: dict | None = None) -> None:
    """A2: per-store recent-vs-baseline traffic table (Pattern 9)."""
    chart_data = D.store_anomalies(merchant_id, filters=filters)
    rows = chart_data["rows"]
    if not rows:
        st.caption("_No stores with enough baseline data to evaluate._")
        return

    takeaway = CT.compute_takeaway("A2", merchant_id, filters=filters)

    CP.render_table_with_drilldown(
        {
            "rows": rows,
            "columns": [
                {"key": "store_id",           "label": "Store",         "kind": "text"},
                {"key": "neighborhood",       "label": "Neighborhood",  "kind": "text"},
                {"key": "baseline",           "label": "Baseline (txns/wk)", "kind": "float"},
                {"key": "recent",             "label": "Recent (txns)", "kind": "int"},
                {"key": "deviation_pct",      "label": "Δ vs baseline", "kind": "pct", "diverging": True},
                {"key": "peer_deviation_pct", "label": "Peer-nbhd Δ",   "kind": "pct", "diverging": True},
            ],
            "empty_caption": "_No stores with enough baseline data to evaluate._",
        },
        title="Per-store traffic vs your first-4-week baseline",
        takeaway=takeaway,
    )


def _render_a3(merchant_id: str, filters: dict | None = None) -> None:
    """A3: per-category recent-vs-baseline volume table (Pattern 9)."""
    chart_data = D.category_anomalies(merchant_id, filters=filters)
    rows = chart_data["rows"]
    if not rows:
        st.caption("_No category-volume data in the recent window._")
        return

    takeaway = CT.compute_takeaway("A3", merchant_id, filters=filters)

    CP.render_table_with_drilldown(
        {
            "rows": rows,
            "columns": [
                {"key": "category",           "label": "Category",      "kind": "text"},
                {"key": "baseline",           "label": "Baseline (lines/wk)", "kind": "float"},
                {"key": "recent",             "label": "Recent (lines)", "kind": "int"},
                {"key": "deviation_pct",      "label": "Δ vs baseline", "kind": "pct", "diverging": True},
                {"key": "peer_deviation_pct", "label": "Peer Δ",         "kind": "pct", "diverging": True},
            ],
            "empty_caption": "_No category-volume data in the recent window._",
        },
        title="Per-category recent-week volume vs your first-4-week baseline",
        takeaway=takeaway,
    )


def _render_t1(merchant_id: str, filters: dict | None = None) -> None:
    """T1: per-neighborhood performance map (Pattern 6 diverging)."""
    chart_data = D.neighborhood_performance(merchant_id, filters=filters)
    nbhds = chart_data["neighborhoods"]
    if not nbhds:
        st.caption("_No store footprint to map._")
        return

    takeaway = CT.compute_takeaway("T1", merchant_id, filters=filters)

    polygons = []
    for n in nbhds:
        v = n["own_delta_pct"]
        tip = (
            f"<b>{n['name']}</b><br>"
            f"Own: {n['own_n_txns']:,} txns / {n['own_n_stores']} stores<br>"
            f"Δ vs baseline: "
            f"{('—' if v is None else f'{v:+.1f}%')}<br>"
            f"Peer signal: {n['peer_signal']}"
        )
        polygons.append({"name": n["name"], "value": v, "tooltip": tip})

    CP.render_neighborhood_map(
        {
            "polygons":     polygons,
            "own_stores":   chart_data["own_markers"],
            "peer_stores":  [],
            "value_label":  "Δ vs your baseline (%)",
            "map_key":      f"t1_{merchant_id}",
        },
        title="Per-neighborhood performance vs your baseline",
        takeaway=takeaway,
        mode="diverging",
    )


def _render_t2(merchant_id: str, filters: dict | None = None) -> None:
    """T2: customer-home density map (Pattern 6 sequential)."""
    chart_data = D.customer_home_density(merchant_id, filters=filters)
    nbhds = chart_data["neighborhoods"]
    if not nbhds:
        st.caption("_No customer-home data to map._")
        return

    takeaway = CT.compute_takeaway("T2", merchant_id, filters=filters)

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
            "map_key":     f"t2_{merchant_id}",
            "footnote":    chart_data.get("footnote", ""),
        },
        title="Where your customers live (vs where your stores sit)",
        takeaway=takeaway,
        mode="sequential",
    )


def _render_t4(merchant_id: str, filters: dict | None = None) -> None:
    """T4: expansion-opportunity map (Pattern 6 score)."""
    chart_data = D.expansion_opportunity(merchant_id, filters=filters)
    nbhds = chart_data["neighborhoods"]
    if not nbhds:
        st.caption("_No customer-activity data to score._")
        return

    takeaway = CT.compute_takeaway("T4", merchant_id, filters=filters)

    polygons = []
    for n in nbhds:
        tip = (
            f"<b>{n['name']}</b><br>"
            f"Score: {n['score']:.1f}<br>"
            f"Customer activity: {n['n_txns']:,} txns<br>"
            f"Own stores: {n['own_n_stores']} · Peer stores: {n['peer_n_stores']}"
        )
        polygons.append({
            "name":    n["name"],
            "value":   n["score"],
            "tooltip": tip,
        })

    CP.render_neighborhood_map(
        {
            "polygons":    polygons,
            "own_stores":  chart_data["own_markers"],
            "peer_stores": chart_data["peer_markers"],
            "value_label": "Expansion score",
            "map_key":     f"t4_{merchant_id}",
            "footnote":    chart_data.get("footnote", ""),
        },
        title="Neighborhood expansion opportunities",
        takeaway=takeaway,
        mode="score",
    )


QUESTION_RENDERERS: dict[str, Callable[..., None]] = {
    "A1": _render_a1,
    "A2": _render_a2,
    "A3": _render_a3,
    "P1": _render_p1,
    "P2": _render_p2,
    "P3": _render_p3,
    "D3": _render_d3,
    "D4": _render_d4,
    "D7": _render_d7,
    "T1": _render_t1,
    "T2": _render_t2,
    "T4": _render_t4,
    # TBL (4.3a)
    "T-P1": _render_t_p1,
    "T-P2": _render_t_p2,
    "T-P3": _render_t_p3,
    "T-A1": _render_t_a1,
    "T-A2": _render_t_a2,
    "T-A3": _render_t_a3,
    "T-D1": _render_t_d1,
    "T-D2": _render_t_d2,
    "T-D3": _render_t_d3,
    # TJX (4.3b)
    "R-P1": _render_r_p1,
    "R-P2": _render_r_p2,
    "R-P3": _render_r_p3,
    "R-A1": _render_r_a1,
    "R-A2": _render_r_a2,
    "R-A3": _render_r_a3,
    "R-D1": _render_r_d1,
    "R-D2": _render_r_d2,
    "R-D3": _render_r_d3,
}


def _render_question_chart(qid: str | None, merchant_id: str) -> None:
    """Render the chart associated with `qid`, if a renderer is wired.

    Pulls the current per-merchant filter state from session_state and
    passes it through so chat-replayed charts honor the dashboard's
    active filters. Wrapped in try/except so a chart-render failure
    doesn't break the surrounding chat-message bubble.
    """
    if not qid:
        return
    renderer = QUESTION_RENDERERS.get(qid)
    if renderer is None:
        return
    filters = st.session_state.get("filters_by_merchant", {}).get(merchant_id)
    try:
        renderer(merchant_id, filters=filters)
    except Exception as exc:  # noqa: BLE001 — chart errors are non-fatal
        st.caption(f"_(chart render failed: {type(exc).__name__})_")


# ---------------------------------------------------------------------------
# Agent-response render helper — escape characters Streamlit's markdown
# parser would otherwise interpret as LaTeX math delimiters. Apply ONLY
# to agent (assistant) prose. Never to user input.
# ---------------------------------------------------------------------------

def _escape_dollars(text: str) -> str:
    """Escape `$` so Streamlit's markdown parser doesn't read it as a
    LaTeX math delimiter. Defined as a top-level helper so callers can
    pre-escape strings BEFORE substituting them into f-strings —
    Python 3.11 doesn't allow backslashes inside f-string expression
    parts."""
    return text.replace("$", "\\$")


def render_agent_response(text: str) -> None:
    """Render an agent's prose. Escapes `$` so Streamlit doesn't treat
    dollar amounts as LaTeX math delimiters (causing italic-serif bleed
    across runs of text). No-op on empty input."""
    if not text:
        return
    st.markdown(_escape_dollars(text))


# Defensive client-side stripper: removes ```caveats ... ``` fenced
# blocks from agent prose. The specialist's upstream regex
# (specialist._CAVEATS_RE) is strict — anchored to end-of-string,
# requires a newline before the closing fence, and only matches the
# literal `caveats` label. When the model drifts (trailing whitespace,
# same-line close, alternate language label, fence appearing mid-prose
# rather than at the very end) the parser misses and the fence leaks
# into the prose, rendering as a JSON-style code block in the bubble.
# We apply two passes:
#   1. Strip every ```caveats ... ``` fence anywhere in the text.
#   2. Strip any trailing fenced code block whose body looks like a
#      JSON list — covers the case where the model uses no language
#      label or writes ```json instead of ```caveats.
_CAVEATS_LABELED_RE = re.compile(
    r"\n?```caveats\b.*?```\s*",
    re.DOTALL | re.IGNORECASE,
)
_TRAILING_JSON_FENCE_RE = re.compile(
    r"\n?```[A-Za-z0-9_-]*\s*\n?\s*\[[^`]*?\]\s*\n?```\s*\Z",
    re.DOTALL,
)


def _strip_caveats_tail(text: str) -> str:
    if not text:
        return text
    cleaned = _CAVEATS_LABELED_RE.sub("", text)
    cleaned = _TRAILING_JSON_FENCE_RE.sub("", cleaned)
    return cleaned.rstrip()


def _streaming_cut_index(text: str) -> int:
    """Return the index at which the streaming display should be
    truncated to hide the trailing caveats / JSON-array fenced block.
    Returns -1 if no cut needed."""
    lower = text.lower()
    candidates = [
        lower.find("```caveats"),
        lower.find("```json"),
    ]
    candidates = [c for c in candidates if c != -1]
    # Also cut at any bare ``` that begins a trailing JSON-array fence
    # — we infer "trailing JSON fence" by looking for ```\n[ near the
    # end of the streamed text. Cheap heuristic: find the last ```
    # before end and check if the next non-whitespace char is '['.
    last_fence = lower.rfind("```")
    if last_fence != -1:
        after = text[last_fence + 3:].lstrip()
        if after.startswith("["):
            candidates.append(last_fence)
    return min(candidates) if candidates else -1


def _render_caveats(caveats: "list[str] | None") -> None:
    if not caveats:
        return
    lines = "\n".join(f"- {_escape_dollars(str(c))}" for c in caveats)
    # Blank line between the italic label and the bullet list — without
    # it, some markdown renderers glue the bullets to the previous
    # paragraph instead of starting a list. Wrap in <small> via plain
    # markdown so the block reads as a quiet footnote, not a header.
    st.markdown(f"*Caveats:*\n\n{lines}")


def _render_chart(chart) -> None:
    if chart is None:
        return
    st.plotly_chart(chart, use_container_width=True)


def _render_table(tbl) -> None:
    if tbl is None or tbl.empty:
        return
    st.dataframe(tbl, use_container_width=True, hide_index=True)


def _render_history_entry(entry: dict, merchant_id: str) -> None:
    """Render a single completed turn as a user + assistant bubble pair.

    If `entry["qid"]` names a question with a registered chart
    renderer, that pattern's chart is rendered inside the assistant
    bubble after the prose + agent-provided chart/table — keeping
    the bubble cohesive across replay.
    """
    with st.chat_message("user"):
        # User input — render literally, no $-escape
        st.markdown(entry.get("question", ""))
    with st.chat_message("assistant"):
        st.caption(entry.get("agent", "Agent"))
        prose = _strip_caveats_tail(entry.get("prose", ""))
        if prose:
            st.markdown(_escape_dollars(prose))
        _render_caveats(entry.get("caveats"))
        _render_chart(entry.get("chart"))
        _render_table(entry.get("table"))
        if not entry.get("error"):
            _render_question_chart(entry.get("qid"), merchant_id)


def _render_live_turn(
    question: str,
    agent_label: str,
    runner: "Callable[[Callable[[int, str], None], Callable[[str], None]], dict]",
    *,
    qid: str | None = None,
    merchant_id: str | None = None,
) -> dict:
    """Render a user+assistant bubble pair with live narration streaming
    into the assistant bubble. Returns the agent's final response dict.

    The streaming placeholder receives both per-turn progress messages
    and streamed final-answer tokens. When the runner returns, the
    placeholder is replaced with the fully-formatted response (caption,
    prose, table, caveats).

    Streaming tokens are $-escaped to match `render_agent_response`.
    """
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        placeholder = st.empty()
        streamed: list[str] = []

        def on_progress(turn: int, msg: str) -> None:
            if not streamed:
                placeholder.markdown(
                    f"_:hourglass_flowing_sand: **{agent_label}** — {msg}_"
                )

        def on_token(text: str) -> None:
            streamed.append(text)
            full = "".join(streamed)
            # Truncate before any trailing caveats / JSON-array fence so
            # the placeholder shows only clean prose during the stream.
            cut = _streaming_cut_index(full)
            visible = full if cut == -1 else full[:cut].rstrip()
            placeholder.markdown(_escape_dollars(visible))

        response = runner(on_progress, on_token)
        clean_prose = _strip_caveats_tail(response.get("prose", ""))
        # Overwrite the placeholder with the final state (caption + prose)
        # in one container call rather than emptying + re-adding widgets
        # — keeps the bubble layout deterministic.
        with placeholder.container():
            st.caption(response.get("agent", agent_label))
            if clean_prose:
                st.markdown(_escape_dollars(clean_prose))
        _render_caveats(response.get("caveats"))
        _render_chart(response.get("chart"))
        _render_table(response.get("table"))
        if qid and merchant_id and not response.get("error"):
            _render_question_chart(qid, merchant_id)
    return response


# ---------------------------------------------------------------------------
# Main render
# ---------------------------------------------------------------------------

def render_chat_panel(merchant_id: str) -> None:
    state = st.session_state
    state.setdefault("active_agent", "pricing")
    state.setdefault("chat_state", "closed")
    state.setdefault("agent_running", False)
    state.setdefault("pending_dispatch", None)
    # Phase 4.5 follow-up: the affordance prefill is now injected
    # directly into the text-input widget (no separate confirm-to-send
    # card). ``chat_input_prefill`` carries the text from the
    # ask-about-this click to the next rerun's text-area initial value.
    state.setdefault("chat_input_prefill", "")
    history = _ensure_history(merchant_id)

    # When an agent is mid-dispatch (i.e. we're inside the run that will
    # process state.pending_dispatch below), disable every control that
    # could re-trigger a Streamlit rerun — clicks on disabled HTML
    # buttons are blocked browser-side, so the streaming `_render_live_turn`
    # below can't be aborted mid-stream.
    is_running = bool(state.agent_running)
    expanded = state.chat_state == "expanded"

    # -- Expand toggle on the middle-left edge of the panel. Rendered
    # into a Streamlit container with a unique key so CSS can position
    # it ``position: absolute; left: -16px; top: 50%`` (half-outside
    # the panel's left border). The container itself sits inside the
    # panel overlay's DOM tree but visually overhangs to the left. --
    with st.container(key="chat_expand_edge"):
        chevron = "›" if expanded else "‹"
        if st.button(
            chevron,
            key=f"expand_edge_btn_{merchant_id}",
            help=("Collapse chat" if expanded else "Expand chat"),
            disabled=is_running,
            type="secondary",
            use_container_width=True,
        ):
            state.chat_state = "side" if expanded else "expanded"
            st.rerun()

    # -- Header row: avatar + title (left), Clear+X cluster (right).
    # The two action buttons live in a single nested column pair so
    # Clear sits immediately adjacent to X; the cluster is wrapped
    # in a keyed container so CSS can pin the whole group flush to
    # the right edge of the header regardless of how the outer
    # column proportionally resizes when the panel transitions
    # 40 vw → 90 vw. --
    h1, h_actions = st.columns([1, 0.30], gap="small")
    with h1:
        # Avatar (purple circle) + title — canonical sparkles SVG in
        # #534AB7 on #EEEDFE soft fill. The subtitle line below the
        # title (was "{merchant} · {specialist}") was removed in this
        # commit — the merchant context is established by the
        # dashboard header, and the active specialist is visible in
        # the selectbox just below this row, so the subtitle was
        # duplicative.
        st.markdown(
            """
            <div style="display:flex;align-items:center;gap:10px;
                 margin:4px 0 6px 0;">
              <div style="width:36px;height:36px;border-radius:50%;
                   background:#EEEDFE;display:flex;align-items:center;
                   justify-content:center;flex-shrink:0;">
                <svg width="20" height="20" viewBox="0 0 24 24"
                     fill="#534AB7" xmlns="http://www.w3.org/2000/svg">
                  <path d="M12 0 L13.5 8.5 L22 10 L13.5 11.5 L12 20
                           L10.5 11.5 L2 10 L10.5 8.5 Z"/>
                  <path d="M19 14 L19.7 16.3 L22 17 L19.7 17.7
                           L19 20 L18.3 17.7 L16 17 L18.3 16.3 Z"/>
                </svg>
              </div>
              <div style="font-size:24px;font-weight:600;
                   color:var(--text);">Ask the data</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with h_actions:
        with st.container(key="chat_header_actions"):
            ha, hb = st.columns([0.78, 0.22], gap="small")
            with ha:
                if st.button(
                    "Clear",
                    key=f"clear_btn_{merchant_id}",
                    help="Wait for current response…" if is_running else "Clear chat history",
                    use_container_width=True,
                    disabled=is_running,
                    type="secondary",
                ):
                    reset_history(merchant_id)
                    st.rerun()
            with hb:
                if st.button(
                    "✕",
                    key=f"close_btn_{merchant_id}",
                    help="Close chat panel",
                    use_container_width=True,
                    disabled=is_running,
                    type="secondary",
                ):
                    state.chat_state = "closed"
                    st.rerun()

    # -- Agent selector — back to ``st.selectbox`` (the radio aesthetic
    # was generic Streamlit default; the dropdown is cleaner). The
    # 4-item list makes the type-ahead filter a non-issue in practice
    # — the committed value is always one of the four specialists. --
    agent_ids = ["demand", "pricing", "anomaly", "trade"]
    agent_labels = {a: A.AGENT_LABELS[a] for a in agent_ids}
    chosen = st.selectbox(
        "Specialist agent",
        options=agent_ids,
        format_func=lambda a: agent_labels[a],
        index=agent_ids.index(state.active_agent),
        key=f"agent_select_{merchant_id}",
        disabled=is_running,
        label_visibility="collapsed",
    )
    if chosen != state.active_agent and not is_running:
        state.active_agent = chosen
        # Do NOT reset chat history on agent switch.

    agent_label = A.AGENT_LABELS[state.active_agent]

    # Suggested questions — always shown. The previous
    # auto-collapse logic (``suggestions_open_by_merchant``,
    # ``last_active_agent_by_merchant``, ``▾ Hide`` / ``▸ Show``
    # toggles) was removed: it was broken and added complexity
    # without enough payoff. The pills stay visible at all times.
    clicked: tuple[str, str] | None = None
    for q in Q.questions_for(merchant_id, state.active_agent):
        qid, qtext = q["id"], q["text"]
        if st.button(
            qtext,
            key=f"q_{merchant_id}_{state.active_agent}_{qid}",
            use_container_width=True,
            disabled=is_running,
            type="secondary",
        ):
            clicked = (qid, qtext)

    if clicked is not None and not is_running:
        qid, qtext = clicked
        state.pending_dispatch = {
            "kind":     "question",
            "qid":      qid,
            "qtext":    qtext,
            "agent_id": state.active_agent,
            "agent_label": agent_label,
        }
        state.agent_running = True
        st.rerun()

    # Phase 4.5 polish: removed the two ``st.markdown("---")``
    # dividers that previously bracketed the chat-history container.
    # Saved ~34 px and let the history flex grow into the freed
    # space. The ``--- `` between description and suggestions stays
    # (it's the only visual break between control area and content).

    # -- Reserve scrollable chat history container. Base height
    # bumped 220 → 380 px as a fallback floor when the CSS flex
    # override (``height: 100% !important``) doesn't win the
    # cascade. With the flex override active the container fills
    # whatever room the panel allocates; without it, 380 still
    # gives the history a substantial vertical share. --
    chat_box = st.container(height=442, border=True, key="chat_history")

    # -- Free-form input — simple text area + Send button to the
    # right. The whole row is pinned to the bottom of the chat
    # panel by the flex layout in styling.py (the chat-history
    # element-container has ``flex: 1 1 auto`` so it grows to
    # fill all space above this row). The text-area is CSS-locked
    # to a fixed height so it doesn't move as the user types
    # (overflow-y: auto handles longer text). --
    prefill = state.chat_input_prefill or ""
    # Re-key the textarea per (merchant_id, prefill-hash) so a fresh
    # prefill from an affordance click forces the widget to remount
    # with the new ``value=`` — without the re-key, Streamlit keeps
    # the prior textarea content and ignores the value kwarg.
    input_key = f"chat_input_{merchant_id}_{hash(prefill) & 0xFFFF:04x}"
    with st.container(key="chat_input_row"):
        col_input, col_send = st.columns([1, 0.18], gap="small")
        with col_input:
            free_q = st.text_area(
                "Ask anything…",
                value=prefill,
                key=input_key,
                height=68,
                disabled=is_running,
                label_visibility="collapsed",
                placeholder="Ask any question about your data…",
            )
        with col_send:
            send_clicked = st.button(
                "Send",
                key=f"chat_send_{merchant_id}",
                type="primary",
                disabled=is_running,
                use_container_width=True,
            )
        if send_clicked:
            if free_q and free_q.strip():
                state.pending_dispatch = {
                    "kind":     "free",
                    "question": free_q.strip(),
                }
                state.chat_input_prefill = ""
                state.agent_running = True
                st.rerun()

    # -- Fill the chat container --
    with chat_box:
        # Phase 4.5 final: "No questions yet" placeholder removed.
        # The input area's placeholder copy already invites typing;
        # a duplicate empty-state message added noise. An empty
        # ``chat_box`` reads as an open canvas waiting for the first
        # turn.
        pending = state.pending_dispatch
        for entry in history:
            _render_history_entry(entry, merchant_id)

        if pending is not None:
            try:
                if pending["kind"] == "question":
                    qid       = pending["qid"]
                    qtext     = pending["qtext"]
                    agent_id  = pending["agent_id"]
                    label     = pending["agent_label"]
                    response = _render_live_turn(
                        qtext,
                        label,
                        lambda progress, on_token: A.dispatch(
                            agent_id, qid, merchant_id,
                            progress=progress, on_token=on_token,
                        ),
                        qid=qid,
                        merchant_id=merchant_id,
                    )
                    _push(merchant_id, qtext, response, qid=qid)
                else:  # "free"
                    question = pending["question"]
                    response = _render_live_turn(
                        question,
                        "Conversational Advisor",
                        lambda progress, on_token: A.dispatch_orchestrated(
                            merchant_id, question,
                            progress=progress, on_token=on_token,
                        ),
                    )
                    _push(merchant_id, question, response)
            finally:
                state.pending_dispatch = None
                state.agent_running = False
            st.rerun()

    # -- Session telemetry — rendered INSIDE the chat panel so it
    # participates in the panel's flex layout (was previously in
    # app.py, adding height below the panel). Compact 8 px single
    # line; only renders when at least one LLM call has been
    # made. --
    _render_telemetry_inline()
