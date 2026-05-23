"""Merchant dashboard — executive-grade BI dashboard with a persistent
specialist-agent chat panel.

Layout:
  - Top: brand + merchant selectbox + filter row (date range, stores,
    categories).
  - Left col (70%):
      KPI row (4 cards; Active customers is clickable) →
      Map + insights panel (60/40 split) →
      Category mix + store performance (2-up) →
      Customer Insights expander.
  - Right col (30%):
      4 specialist-agent suggestion cards + chronological chat history.
      No free-form input in Phase 1.

Phase 2 will replace `placeholders.dispatch` with real LLM agent calls
(plus a free-form text input) without changing the rest of the layout.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # tolerated even though Phase 1 doesn't call any LLM APIs

import streamlit as st
import streamlit.components.v1 as components

from src.dashboard import chat, data as D, styling, views

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "payments.db"


# ---------------------------------------------------------------------------
# Page setup
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Payments Data Strategy — Merchant Dashboard",
    layout="wide",
    initial_sidebar_state="collapsed",
)
styling.inject()

# Scroll the parent window back to the top whenever the chat-expand
# state was just toggled. Streamlit strips <script> tags from
# st.markdown, so this has to go through components.html — which paints
# a zero-height iframe whose JS can reach the parent via window.parent.
if st.session_state.pop("scroll_to_top_pending", False):
    components.html(
        "<script>window.parent.scrollTo({top: 0, left: 0, behavior: 'instant'});</script>",
        height=0,
    )

if not DB_PATH.exists() or DB_PATH.stat().st_size == 0:
    st.error("Database not found or empty. Run `make seed` first.")
    st.stop()


# ---------------------------------------------------------------------------
# Session-state initialization
# ---------------------------------------------------------------------------

state = st.session_state
state.setdefault("merchant_id", "KRG")
state.setdefault("filters_by_merchant", {})
state.setdefault("chat_messages_by_merchant", {})
state.setdefault("active_agent", "pricing")
state.setdefault("chat_expanded", False)


def _render_telemetry_footer() -> None:
    """Cost / token telemetry block — rendered below the chat panel in
    both layout modes (split and full-width)."""
    try:
        from src.agents import llm as _llm
        totals = _llm.session_totals()
    except Exception:  # noqa: BLE001
        totals = None
    if not (totals and totals.get("calls", 0) > 0):
        return
    st.markdown(
        '<hr style="margin: 14px 0 6px; border-color: var(--border);"/>'
        '<div style="font-size:11px;color:var(--text-muted);'
        'letter-spacing:0.05em;text-transform:uppercase;font-weight:600;'
        'margin:0 0 4px;">Session telemetry</div>'
        f'<div style="font-size:12px;color:var(--text-2);">'
        f'{totals["calls"]} LLM calls · '
        f'{totals["input_tokens"]:,} in · '
        f'{totals["output_tokens"]:,} out · '
        f'~${totals["cost_usd"]:.4f}'
        '</div>',
        unsafe_allow_html=True,
    )


def _default_filters(merchant_id: str) -> dict:
    return {
        "date_start": D.PANEL_START,
        "date_end":   D.PANEL_END,
        "stores":     [],
        "categories": [],
    }


def _filters() -> dict:
    """Return (and lazy-initialize) the filter dict for the active merchant."""
    mid = state.merchant_id
    if mid not in state.filters_by_merchant:
        state.filters_by_merchant[mid] = _default_filters(mid)
    return state.filters_by_merchant[mid]


def _on_merchant_change() -> None:
    """Called by the merchant selectbox. Per-merchant state (filters,
    chat history) is keyed by merchant_id and preserved across switches —
    no clearing here. The dict-by-merchant in session_state IS the
    isolation boundary."""
    return


# ---------------------------------------------------------------------------
# Header — title + merchant selectbox + filter row
# ---------------------------------------------------------------------------

header_col1, header_col2 = st.columns([5, 2])
with header_col1:
    st.markdown(
        '<h1 style="margin-top: -4px;">Merchant dashboard</h1>'
        '<div style="font-size:13px;color:var(--text-muted);margin-top:-4px;">'
        'Cross-merchant analytics on a synthetic 10,000-customer Charlotte panel.'
        '</div>',
        unsafe_allow_html=True,
    )
with header_col2:
    merchant_labels = [f"{D.MERCHANT_NAME[m]}" for m in
                        ("KRG", "ACM", "WDX", "TBL", "TJX")]
    merchant_ids = ["KRG", "ACM", "WDX", "TBL", "TJX"]
    chosen = st.selectbox(
        "Acting as",
        options=merchant_ids,
        format_func=lambda mid: D.MERCHANT_NAME[mid],
        index=merchant_ids.index(state.merchant_id),
        key="merchant_select",
    )
    if chosen != state.merchant_id:
        state.merchant_id = chosen
        _on_merchant_change()
        st.rerun()


filters = _filters()
mid = state.merchant_id

# Filter row + dashboard views are skipped entirely when the chat panel
# is expanded — the chat is then the only thing on the page.
if not state.chat_expanded:
    f_col1, f_col2, f_col3 = st.columns([1.2, 1.6, 1.6])
    with f_col1:
        date_range = st.date_input(
            "Date range",
            value=(filters["date_start"], filters["date_end"]),
            min_value=D.PANEL_START,
            max_value=D.PANEL_END,
            key=f"date_{mid}",
        )
        if isinstance(date_range, tuple) and len(date_range) == 2:
            filters["date_start"], filters["date_end"] = date_range
    with f_col2:
        own_stores_df = D.stores_for(mid)
        chosen_stores = st.multiselect(
            f"Stores ({len(own_stores_df)})",
            options=own_stores_df["store_id"].tolist(),
            default=filters["stores"] if filters["stores"] else [],
            placeholder="All stores",
            key=f"stores_{mid}",
        )
        filters["stores"] = chosen_stores
    with f_col3:
        cats = D.categories_for(mid)
        chosen_cats = st.multiselect(
            f"Categories ({len(cats)})",
            options=cats,
            default=filters["categories"] if filters["categories"] else [],
            placeholder="All categories",
            key=f"cats_{mid}",
        )
        filters["categories"] = chosen_cats

    st.markdown("<hr style='margin: 8px 0 12px;'/>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Layout — split (dashboard + chat) or full-width chat when expanded
# ---------------------------------------------------------------------------

if state.chat_expanded:
    # Full-width chat: dashboard column entirely hidden. Bubbles, charts,
    # tables, suggestions, and chat_input naturally render at full
    # viewport width.
    chat.render_chat_panel(mid)
    _render_telemetry_footer()
else:
    dash_col, chat_col = st.columns([65, 35], gap="medium")

    with dash_col:
        views.render_kpi_strip(mid, filters)
        views.render_performance_section(mid, filters)

        map_col, ins_col = st.columns([6, 4], gap="small")
        with map_col:
            views.render_map(mid, filters)
        with ins_col:
            views.render_insights_panel(mid, filters)

        cat_col, store_col = st.columns([1, 1], gap="small")
        with cat_col:
            views.render_category_mix(mid, filters)
        with store_col:
            views.render_store_performance(mid, filters)

        views.render_payment_intelligence(mid, filters)
        views.render_time_patterns(mid, filters)
        views.render_customer_engagement(mid, filters)

    with chat_col:
        chat.render_chat_panel(mid)
        _render_telemetry_footer()
