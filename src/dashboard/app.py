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
# Phase 4.5 follow-up: chat panel is now a fixed-position overlay
# drawer with three states. ``chat_expanded`` from earlier phases is
# subsumed by this state machine — kept for backward compat but
# treated as deprecated.
state.setdefault("chat_state", "closed")  # "closed" | "side" | "expanded"
state.setdefault("chat_expanded", False)


# Phase 4.5 polish: ``_render_telemetry_footer`` moved into chat.py
# as ``_render_telemetry_inline``. It now renders at the very end of
# ``render_chat_panel`` so it lives INSIDE the panel's flex layout
# instead of adding height below the panel.


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
    isolation boundary.

    What DOES clear on switch: the affordance prefill text. A prefill
    set from a card on the previous merchant ("Why is University City
    declining? Are peers seeing the same drop?") doesn't make sense
    after switching to a different merchant — drop it so the textarea
    renders empty on the new viewer.
    """
    st.session_state.chat_input_prefill = ""


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

# Phase 4.5 follow-up: dashboard always renders at full width. The chat
# panel is a fixed-position overlay drawer (closed / side / expanded)
# rather than a permanent right-rail column.

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
# Layout — dashboard always full-width; chat is an overlay drawer
# ---------------------------------------------------------------------------

# Drive the body-level class that controls drawer width.
styling.apply_chat_state_class(state.chat_state)

views.render_kpi_strip(mid, filters)
views.render_performance_section(mid, filters)
views.render_geography_section(mid, filters)
views.render_catalog_section(mid, filters)
views.render_customers_section(mid, filters)

# Backdrop renders in both side and expanded modes — a light gray
# scrim covers the dashboard area to the left of the panel so the
# panel reads as the primary focus. The scrim is purely decorative
# since Streamlit's iframe sandbox blocks click-outside JS from
# updating session_state reliably; use the X / collapse buttons
# to dismiss.
if state.chat_state in ("side", "expanded"):
    st.markdown('<div class="chat-backdrop"></div>', unsafe_allow_html=True)

# Edge tab: a small floating button on the right edge, only shown
# when the panel is closed. Clicking opens the side-mode drawer.
if state.chat_state == "closed":
    with st.container(key="chat_edge_tab"):
        if st.button("✦", key="chat_edge_open", help="Open the chat panel"):
            state.chat_state = "side"
            st.rerun()

# Chat panel: rendered into a container whose key the CSS targets
# with ``position: fixed`` to overlay the dashboard. The container
# always renders; CSS hides it via ``body.chat-closed`` so the
# Streamlit widget state inside (input, history, etc.) survives state
# transitions without re-instantiating.
with st.container(key="chat_panel_overlay"):
    # Telemetry now renders inside ``render_chat_panel`` so it
    # participates in the panel's flex layout.
    chat.render_chat_panel(mid)
