"""CSS injection for the merchant dashboard.

Matches `docs/report.html`: accent #0F4C81, surface #F7F8FA, system fonts,
1.5px borders, 6px corners. One module = one concern (styling only).
"""
from __future__ import annotations

import streamlit as st


_CSS = """
<style>
  :root {
    --accent:       #0F4C81;
    --accent-soft:  #D8E2EE;
    --surface:      #F7F8FA;
    --border:       #E2E5EA;
    --text:         #1A1F2E;
    --text-2:       #4A5161;
    --text-muted:   #7B8294;
    --anomaly:      #C44536;
    --c-krg:        #0F4C81;
    --c-acm:        #3A6FA5;
    --c-wdx:        #6F8FB8;
    --c-tbl:        #C0563F;
    --c-tjx:        #5B7B58;
    --good:         #2F855A;
    --bad:          #C44536;
    /* Phase 4.5 — peer / diverging palette additions (mirror the
       chart_patterns.py constants of the same name). */
    --peer-a:           #6B7280;
    --peer-b:           #9CA3AF;
    --peer-aggregate:   #4B5563;
    --diverging-low:    #C44536;
    --diverging-mid:    #FFFFFF;
    --diverging-high:   #0F4C81;
  }

  /* Streamlit overrides — system fonts, tighter spacing for a dashboard look. */
  html, body, [data-testid="stAppViewContainer"] {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Inter, Roboto, sans-serif !important;
    color: var(--text);
  }
  .block-container { padding-top: 2.2rem; padding-bottom: 2rem; max-width: 1500px; }
  h1, h2, h3 { letter-spacing: -0.01em; color: var(--text); }
  h1 { font-size: 26px !important; font-weight: 600 !important; margin: 0 0 4px !important; }
  h2 { font-size: 18px !important; font-weight: 600 !important; }
  h3 { font-size: 15px !important; font-weight: 600 !important; }

  /* KPI cards — match the report's `.stat-row` rhythm */
  .kpi-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 18px; margin: 8px 0 16px; }
  .kpi {
    background: #fff;
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 22px 22px;
    position: relative;
    box-shadow: 0 1px 3px rgba(15, 31, 46, 0.04), 0 1px 2px rgba(15, 31, 46, 0.03);
    height: 100%;
    margin-bottom: 18px;
  }
  .kpi.clickable { cursor: pointer; transition: border-color 0.15s ease, box-shadow 0.15s ease; }
  .kpi.clickable:hover { border-color: var(--accent); box-shadow: 0 4px 10px rgba(15, 76, 129, 0.10); }
  .kpi .num {
    font-size: 28px;
    font-weight: 600;
    letter-spacing: -0.02em;
    color: var(--text);
    font-variant-numeric: tabular-nums;
    line-height: 1.1;
  }
  .kpi .label {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--text-muted);
    margin-top: 6px;
    font-weight: 600;
  }
  .kpi .delta { font-size: 12px; font-variant-numeric: tabular-nums; margin-top: 6px; }
  .kpi .delta.up   { color: var(--good); }
  .kpi .delta.down { color: var(--bad); }
  .kpi .delta.flat { color: var(--text-muted); }
  .kpi .hint {
    position: absolute;
    top: 12px; right: 14px;
    font-size: 10px;
    color: var(--accent);
    text-transform: uppercase;
    letter-spacing: 0.06em;
    font-weight: 600;
  }

  /* Phase 4.4 KPI callout primitives — rendered inside an
     st.container(border=True), so the bordered shell comes from
     Streamlit; the typography rules below match the v2.5 .kpi rhythm. */
  .kpi-label {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--text-muted);
    font-weight: 600;
    margin: 0 0 6px 0;
  }
  .kpi-value {
    font-size: 28px;
    font-weight: 600;
    letter-spacing: -0.02em;
    color: var(--text);
    font-variant-numeric: tabular-nums;
    line-height: 1.1;
    margin: 0 0 4px 0;
  }
  .kpi-delta { font-size: 12px; font-variant-numeric: tabular-nums; margin: 4px 0 4px 0; }
  .kpi-delta.up   { color: var(--good); }
  .kpi-delta.down { color: var(--bad); }
  .kpi-delta.flat { color: var(--text-muted); }
  .kpi-hint {
    font-size: 11px;
    color: var(--text-muted);
    margin-top: 4px;
  }

  /* "Ask about this" affordance — small icon-style button. Targets
     Streamlit's per-widget st-key-* class so the button renders
     compactly inside the KPI card header row.

     Phase 4.5 fix-up: the button is constrained to a fixed 28 × 28 px
     square pushed to the right edge of its column, so the hover
     background paints a small circle centered exactly on the icon
     instead of a wide pill that drifts left of the icon. Without
     ``max-width`` + ``margin-left: auto`` the button stretched to
     fill ``use_container_width=True``, making the hover background
     wider than the icon. */
  div[class*="st-key-ask_about_"] button {
    /* Phase 4.5 final: 32 × 32 (up from 28) with 20 × 20 SVG inside
       — bigger, more discoverable, matches the panel-header avatar
       size. Canonical sparkles SVG rendered as a CSS background-
       image (Streamlit button labels can't carry inline SVG). The
       ✦ glyph in the button label is hidden via ``font-size: 0`` —
       remains in the DOM for screen-reader fallback. */
    width: 32px !important;
    height: 32px !important;
    max-width: 32px !important;
    min-height: 0 !important;
    padding: 0 !important;
    margin-left: auto !important;
    border-radius: 50% !important;
    border-color: transparent !important;
    background-color: transparent !important;
    background-image: url("data:image/svg+xml;utf8,%3Csvg width='20' height='20' viewBox='0 0 24 24' fill='%23534AB7' xmlns='http://www.w3.org/2000/svg'%3E%3Cpath d='M12 0 L13.5 8.5 L22 10 L13.5 11.5 L12 20 L10.5 11.5 L2 10 L10.5 8.5 Z'/%3E%3Cpath d='M19 14 L19.7 16.3 L22 17 L19.7 17.7 L19 20 L18.3 17.7 L16 17 L18.3 16.3 Z'/%3E%3C/svg%3E") !important;
    background-repeat: no-repeat !important;
    background-position: center !important;
    background-size: 20px 20px !important;
    font-size: 0 !important;
    line-height: 1 !important;
    color: transparent !important;
    opacity: 0.65;
    transition: opacity 0.15s ease, background-color 0.15s ease, border-color 0.15s ease;
  }
  div[class*="st-key-ask_about_"] button:hover {
    opacity: 1.0 !important;
    background-color: #EEEDFE !important;
    border-color: #EEEDFE !important;
  }
  div[class*="st-key-ask_about_"] button:disabled {
    opacity: 0.25 !important;
    cursor: not-allowed;
  }
  /* Pull the affordance button's parent column flush to the right
     within the card header so the icon sits cleanly at the top-right
     corner, matching the design doc's Section 5.1 placement. */
  div[class*="st-key-ask_about_"] {
    display: flex !important;
    justify-content: flex-end !important;
    align-items: center !important;
  }

  /* Generic card primitive */
  .panel-card {
    background: #fff;
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 16px 18px;
    margin-bottom: 12px;
  }
  .panel-card .panel-title {
    font-size: 13.5px;
    font-weight: 600;
    color: var(--text);
    margin: 0 0 8px;
  }
  .panel-card .panel-sub {
    font-size: 12px;
    color: var(--text-muted);
    margin: 0 0 8px;
  }

  /* Chat panel — agent suggestion cards */
  .agent-card {
    background: #fff;
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 14px 16px;
    margin-bottom: 12px;
  }
  .agent-card .agent-name {
    font-size: 13px;
    font-weight: 600;
    color: var(--accent);
    margin: 0 0 2px;
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .agent-card .agent-name .dot {
    width: 8px; height: 8px;
    border-radius: 50%;
    background: var(--accent);
    display: inline-block;
  }
  .agent-card .agent-desc {
    font-size: 12px;
    color: var(--text-muted);
    margin: 0 0 10px;
    line-height: 1.45;
  }
  /* Style the Streamlit buttons inside agent cards as compact pills */
  .agent-card div.stButton > button {
    width: 100%;
    text-align: left;
    background: var(--surface);
    border: 1px solid var(--border);
    color: var(--text);
    font-size: 12.5px;
    font-weight: 400;
    padding: 6px 10px;
    border-radius: 6px;
    margin: 3px 0;
    line-height: 1.35;
    white-space: normal;
    min-height: 0;
    height: auto;
  }
  .agent-card div.stButton > button:hover {
    background: #fff;
    border-color: var(--accent);
    color: var(--accent);
  }

  /* Chat history entries */
  .chat-entry {
    background: var(--surface);
    border-left: 3px solid var(--accent);
    border-radius: 0 6px 6px 0;
    padding: 12px 14px;
    margin: 10px 0;
  }
  .chat-entry .chat-head {
    font-size: 11px;
    color: var(--text-muted);
    margin-bottom: 6px;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    font-weight: 600;
  }
  .chat-entry .chat-head .agent-name { color: var(--accent); }
  .chat-entry .chat-q {
    font-size: 13px;
    font-weight: 600;
    color: var(--text);
    margin: 0 0 6px;
  }
  .chat-entry .chat-body {
    font-size: 13px;
    line-height: 1.55;
    color: var(--text-2);
  }
  .chat-entry .chat-body p { margin: 0 0 8px; }
  .chat-entry .chat-body strong { color: var(--text); }

  /* Heatmap toggle row */
  .map-toggles {
    display: flex; gap: 14px;
    padding: 6px 0 4px;
    font-size: 12px; color: var(--text-2);
  }

  /* Caption tints for the "Customer Insights from Your Own Data" subtitle */
  .insights-subtitle {
    font-size: 13px;
    color: var(--text-muted);
    font-style: italic;
    margin: 0 0 16px;
  }

  /* Section dividers / spacing */
  hr { border-color: var(--border); margin: 8px 0; }

  /* Chat header action buttons (expand toggle + clear history).
     Streamlit emits a per-widget class `st-key-<key>` on each widget's
     wrapper when `key=` is set; we match it with [class*="..."] so the
     same rule covers the per-merchant suffix (e.g. st-key-clear_btn_KRG).
     Both buttons default to a quiet, neutral look; on hover, expand
     picks up the accent color and clear picks up the danger/anomaly
     color so the destructive action is visually distinguished. */
  [class*="st-key-expand_btn_"] button,
  [class*="st-key-clear_btn_"] button {
    padding: 4px 0 !important;
    min-height: 0 !important;
    font-size: 14px !important;
    line-height: 1.2 !important;
    color: var(--text-muted) !important;
    background: transparent !important;
    border-color: var(--border) !important;
  }
  [class*="st-key-expand_btn_"] button:hover {
    color: var(--accent) !important;
    border-color: var(--accent) !important;
    background: rgba(15, 76, 129, 0.06) !important;
  }
  [class*="st-key-clear_btn_"] button:hover {
    color: var(--anomaly) !important;
    border-color: var(--anomaly) !important;
    background: rgba(196, 69, 54, 0.06) !important;
  }
  /* Explicit disabled state — Streamlit's default disabled styling is
     subtle, so we hard-fade the icon to make it obvious that the
     button is non-interactive during an agent dispatch. */
  [class*="st-key-expand_btn_"] button:disabled,
  [class*="st-key-clear_btn_"] button:disabled {
    color: var(--text-muted) !important;
    opacity: 0.35 !important;
    cursor: not-allowed !important;
    background: transparent !important;
    border-color: var(--border) !important;
  }

  /* Phase 4.5 — Folium tooltip styling. Mirrors chart_patterns.py
     HOVERLABEL so Plotly and Folium tooltips read as one visual
     family (white bg, light-gray border, system font, gray-800
     text). Targets Leaflet's per-element tooltip class — st_folium
     renders the underlying Leaflet map directly so this selector
     reaches the embedded iframe via Leaflet's standard CSS. */
  .leaflet-tooltip {
    background-color: #FFFFFF !important;
    border: 1px solid #E5E7EB !important;
    border-radius: 4px !important;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.10) !important;
    color: #1F2937 !important;
    font-family: -apple-system, BlinkMacSystemFont, system-ui, sans-serif !important;
    font-size: 13px !important;
    padding: 6px 10px !important;
  }
  .leaflet-tooltip-top:before,
  .leaflet-tooltip-bottom:before,
  .leaflet-tooltip-left:before,
  .leaflet-tooltip-right:before {
    border-top-color: #E5E7EB !important;
  }

  /* -----------------------------------------------------------------
     Phase 4.5 follow-up — Chat panel overlay drawer.

     The chat panel is rendered into a Streamlit container with a
     stable key suffix (``chat_panel_overlay``). The CSS below
     promotes that container into a position:fixed right-side drawer
     so the dashboard column can use the full viewport width.

     Three states live in ``state.chat_state``:
       - "closed"   → drawer hidden; edge tab visible on right edge
       - "side"     → drawer at 40 vw
       - "expanded" → drawer at 90 vw with dim backdrop

     Width is varied by a body-level class (``body.chat-side`` /
     ``body.chat-expanded``) so a single CSS selector responds to the
     state change. ``transition: width 0.3s ease`` makes the resize
     smooth between side ↔ expanded.

     Click-outside-to-close: NOT implemented in this commit — Streamlit
     reruns interrupt JS bridges. Use the explicit X button in the
     panel header.
  ----------------------------------------------------------------- */
  /* Shared panel width — drives the panel, the backdrop's right edge, and
     the collapse handle so all three align to ONE vertical line. */
  body { --chat-w: clamp(360px, 40vw, 480px); }
  body.chat-expanded { --chat-w: 90vw; }

  div[class*="st-key-chat_panel_overlay"] {
    position: fixed !important;
    /* The keyed container IS the stVerticalBlock — make it the flex column
       directly (current Streamlit wraps each widget in a stLayoutWrapper,
       so the old ``> stVerticalBlock`` selector no longer matched). */
    display: flex !important;
    flex-direction: column !important;
    gap: 6px !important;
    top: 56px;
    right: 0;
    bottom: 0;
    width: var(--chat-w);
    max-width: none;
    background: #FFFFFF;
    border-left: 1px solid var(--border);
    box-shadow: -8px 0 24px rgba(15, 31, 46, 0.10);
    z-index: 998;
    /* Phase 4.5 final: ``overflow: hidden`` (was ``auto``). The panel
       itself does NOT scroll — only the chat-history container
       inside it scrolls internally. Layout below makes the panel a
       flex column so the history fills the remaining space after
       the fixed-size header / selectbox / suggestions / input row.
       Phase 4.5 polish: top padding trimmed 16 → 4 px so the panel
       header sits flush at the top with minimal gap above. */
    overflow: hidden !important;
    padding: 4px 20px 12px 20px;
    transition: width 0.3s ease, max-width 0.3s ease;
  }
  /* The panel's direct children (current Streamlit) are stLayoutWrapper /
     stElementContainer rows. Pin them all at natural height EXCEPT the
     chat-history wrapper, which grows to fill the remaining space — so the
     header + input + cost footer stay visible and only history scrolls. */
  div[class*="st-key-chat_panel_overlay"] > div[data-testid="stLayoutWrapper"],
  div[class*="st-key-chat_panel_overlay"] > div[data-testid="stElementContainer"] {
    flex: 0 0 auto !important;
    min-height: 0 !important;
  }
  /* The chat-history wrapper grows + becomes the only scroll zone.
     ``height: auto`` overrides the inline ``st.container(height=…)`` px so
     flex controls its size. */
  div[class*="st-key-chat_panel_overlay"] >
    div[data-testid="stLayoutWrapper"]:has(> div[class*="st-key-chat_history"]) {
    flex: 1 1 auto !important;
    min-height: 0 !important;
    height: auto !important;
    overflow: hidden !important;
  }
  div[class*="st-key-chat_history"] {
    height: 100% !important;
    max-height: none !important;
    overflow-y: auto !important;
  }
  /* Compact spacing on the fixed-size header / selectbox / suggested-
     question rows so the chat-history flex-grow has room to breathe. */
  div[class*="st-key-chat_panel_overlay"] hr {
    margin: 4px 0 !important;
  }
  body.chat-expanded div[class*="st-key-chat_panel_overlay"] {
    width: 90vw;
    max-width: none;
  }
  body.chat-closed div[class*="st-key-chat_panel_overlay"] {
    display: none !important;
  }

  /* Edge tab — small floating button on the right edge of the
     viewport, shown only when ``state.chat_state == "closed"``. Click
     opens the drawer in side mode. */
  div[class*="st-key-chat_edge_tab"] {
    position: fixed !important;
    right: 8px;
    /* Pinned top-right, sitting just below Streamlit's top toolbar (the
       kebab menu at top:0) so the two never overlap. Was top:50% (mid-
       height of the right edge, easy to miss). */
    top: 64px;
    transform: none;
    z-index: 997;
    width: 70px !important;
  }
  div[class*="st-key-chat_edge_tab"] button {
    /* Same canonical sparkles SVG as the affordance buttons +
       panel-header avatar — unified visual identity. Phase 4.5
       final: SVG bumped 24 → 28 px for better visibility. */
    width: 64px !important;
    height: 88px !important;
    padding: 46px 2px 8px 2px !important;
    border-radius: 8px 0 0 8px !important;
    background-color: #FFFFFF !important;
    background-image: url("data:image/svg+xml;utf8,%3Csvg width='28' height='28' viewBox='0 0 24 24' fill='%23534AB7' xmlns='http://www.w3.org/2000/svg'%3E%3Cpath d='M12 0 L13.5 8.5 L22 10 L13.5 11.5 L12 20 L10.5 11.5 L2 10 L10.5 8.5 Z'/%3E%3Cpath d='M19 14 L19.7 16.3 L22 17 L19.7 17.7 L19 20 L18.3 17.7 L16 17 L18.3 16.3 Z'/%3E%3C/svg%3E") !important;
    background-repeat: no-repeat !important;
    background-position: center 14px !important;   /* sparkle near the top */
    background-size: 26px 26px !important;
    border: 1px solid var(--border) !important;
    border-right: none !important;
    box-shadow: -3px 0 8px rgba(15, 31, 46, 0.10) !important;
    /* "AI agents" label shown beneath the sparkle (padding-top pushes it
       below the SVG; was font-size:0 to hide the ✦ glyph). */
    font-size: 6px !important;
    font-weight: 600 !important;
    line-height: 1.1 !important;
    color: #534AB7 !important;
    white-space: nowrap !important;
    text-align: center !important;
    transition: background-color 0.15s ease;
  }
  div[class*="st-key-chat_edge_tab"] button:hover {
    background-color: #EEEDFE !important;
  }

  /* Chat-history container — fills its 60% flex parent. The
     element-container above provides the height; the inner
     container just stretches and scrolls. */
  div[class*="st-key-chat_history"] {
    height: 100% !important;
    max-height: none !important;
  }

  /* Middle-left expand button — round chevron anchored to the
     viewport (``position: fixed``) at the panel's left edge.
     Anchored via ``right`` so it tracks the panel width as it
     transitions between side (40 vw) and expanded (90 vw).
     Using fixed positioning instead of absolute keeps the chevron
     visible regardless of the panel's overflow-y scrolling. */
  div[class*="st-key-chat_expand_edge"] {
    position: fixed !important;
    right: calc(var(--chat-w) - 22px);  /* center on the panel's left edge */
    top: 50% !important;
    transform: translateY(-50%) !important;
    width: 44px !important;
    z-index: 1000;
    transition: right 0.3s ease;
  }
  body.chat-expanded div[class*="st-key-chat_expand_edge"] {
    right: calc(90vw - 22px);
  }
  body.chat-closed div[class*="st-key-chat_expand_edge"] {
    display: none !important;
  }
  /* 44 × 44 button with a 22 px SVG chevron painted as a
     background-image (Streamlit ``st.button`` labels can't carry
     inline SVG, so the unicode ``‹``/``›`` glyph in the label is
     hidden via ``font-size: 0`` and ``color: transparent`` and
     used only as a screen-reader fallback). The SVG sits at ~50 %
     of the button diameter for clear visibility. The
     ``body.chat-expanded`` override below swaps to the
     right-pointing variant when the panel is in expanded mode. */
  div[class*="st-key-chat_expand_edge"] button {
    width: 44px !important;
    height: 44px !important;
    min-height: 0 !important;
    padding: 0 !important;
    border-radius: 50% !important;
    background-color: #FFFFFF !important;
    background-image: url("data:image/svg+xml;utf8,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23534AB7' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolyline points='15 18 9 12 15 6'/%3E%3C/svg%3E") !important;
    background-repeat: no-repeat !important;
    background-position: center !important;
    background-size: 22px 22px !important;
    border: 1px solid var(--border) !important;
    box-shadow: -2px 2px 8px rgba(15, 31, 46, 0.15) !important;
    font-size: 0 !important;
    line-height: 1 !important;
    color: transparent !important;
  }
  body.chat-expanded div[class*="st-key-chat_expand_edge"] button {
    background-image: url("data:image/svg+xml;utf8,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23534AB7' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolyline points='9 18 15 12 9 6'/%3E%3C/svg%3E") !important;
  }
  div[class*="st-key-chat_expand_edge"] button:hover {
    background-color: #EEEDFE !important;
  }

  /* Chat panel header buttons:
     - "Clear chat" is a small text pill (no icon)
     - ✕ close button has no border / square background — bare glyph
       with a hover-only soft purple background. */
  /* Phase 4.5 polish: fixed 96 px width so the pill doesn't stretch
     when the panel transitions from 40 vw → 90 vw. The wrapper rule
     right-aligns the constrained-width button within its column. */
  /* Header action cluster — Clear + ✕ live in nested ``st.columns``
     inside a ``chat_header_actions`` container. Pin the cluster
     flush to the right edge of the header so the buttons sit
     immediately adjacent to each other and don't drift when the
     panel transitions 40 vw → 90 vw (the outer column expands
     but the cluster stays right-anchored with a fixed total
     width). */
  div[class*="st-key-chat_header_actions"] {
    width: 120px !important;          /* 88 Clear + 4 gap + 28 ✕ — no slack, so ✕ is flush right */
    max-width: 120px !important;
    margin-left: auto !important;
  }
  /* Breathing room between the "Ask the data" header and the agent
     selector (was crowding the title). */
  div[class*="st-key-agent_select"] {
    margin-top: 12px !important;
  }
  div[class*="st-key-chat_header_actions"] div[data-testid="stHorizontalBlock"] {
    gap: 4px !important;
    align-items: center !important;
    justify-content: flex-end !important;
  }
  div[class*="st-key-clear_btn_"] {
    display: flex !important;
    justify-content: flex-end !important;
  }
  div[class*="st-key-clear_btn_"] button {
    width: 88px !important;
    max-width: 88px !important;
    flex: 0 0 88px !important;
    font-size: 12px !important;
    padding: 6px 12px !important;
    min-height: 0 !important;
    border-radius: 14px !important;
    background: transparent !important;
    border: 1px solid var(--border) !important;
    color: var(--text-2) !important;
    transition: background 0.15s ease, color 0.15s ease;
  }
  div[class*="st-key-clear_btn_"] button:hover {
    background: #EEEDFE !important;
    border-color: #EEEDFE !important;
    color: #534AB7 !important;
  }
  div[class*="st-key-close_btn_"] button {
    width: 28px !important;
    height: 28px !important;
    min-height: 0 !important;
    padding: 0 !important;
    margin-left: auto !important;
    border-radius: 50% !important;
    background: transparent !important;
    border: none !important;
    font-size: 16px !important;
    line-height: 1 !important;
    color: var(--text-muted) !important;
    box-shadow: none !important;
    transition: background 0.15s ease, color 0.15s ease;
  }
  div[class*="st-key-chat_edge_tab"] button p {
    font-size: 13.5px !important;
    line-height: 1.1 !important;
    margin: 0 !important;
    text-align: center !important;
    color: #534AB7 !important;
}
  div[class*="st-key-close_btn_"] button:hover {
    background: #EEEDFE !important;
    color: #534AB7 !important;
  }

  /* Backdrop scrim — covers the dashboard area to the LEFT of the
     panel so the panel reads as the primary focus. Clean light
     gray (no dim/dark feel). Sits below the dashboard header
     (top: 56 px) and stops at the panel's left edge. Slightly
     stronger in expanded mode to keep the modal-style focus
     that 90 vw deserves.

     Implemented as a markdown div with ``position: fixed`` that
     sits between the dashboard (z-index: 0) and the panel
     (z-index: 998). Click-to-dismiss: it's a real st.button (key
     ``chat_backdrop``) overlaid as a full-area scrim; its right edge tracks
     ``--chat-w`` so it ends exactly at the panel's left border. */
  div[class*="st-key-chat_backdrop"] {
    position: fixed !important;
    top: 56px;
    right: var(--chat-w);
    bottom: 0;
    left: 0;
    background: rgba(241, 239, 232, 0.55);
    z-index: 997;
    transition: right 0.3s ease, background-color 0.3s ease;
  }
  body.chat-expanded div[class*="st-key-chat_backdrop"] {
    background: rgba(241, 239, 232, 0.75);
  }
  /* The button fills the scrim and is fully transparent — the whole dim
     area is the click target. */
  div[class*="st-key-chat_backdrop"] button {
    width: 100% !important;
    height: 100% !important;
    min-height: 0 !important;
    padding: 0 !important;
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    color: transparent !important;
    cursor: pointer !important;
  }

  /* Simple chat input row — plain text area + Send button at
     the right. No rounded pill chrome (the prior "Variant B"
     wrapper styling was removed at the user's request: "keep
     this simple"). The row sits at the bottom of the panel
     because the chat-history element-container above has
     ``flex: 1 1 60%`` and absorbs all the space above. */
  div[class*="st-key-chat_input_row"] {
    margin-top: 6px !important;
    flex-shrink: 0 !important;
  }
  /* Pin the cost/telemetry footer at the bottom next to the input row so the
     cost line is never squeezed or clipped on short viewports. */
  div[class*="st-key-chat_telemetry"] {
    flex-shrink: 0 !important;
  }
  div[class*="st-key-chat_input_row"] div[data-testid="stHorizontalBlock"] {
    align-items: stretch !important;
  }
  div[class*="st-key-chat_input_row"] [data-testid="stWidgetLabel"] {
    display: none !important;
  }
  /* Lock the textarea height so it doesn't grow as the user
     types — overflow-y handles longer text. Keeps the input
     visually pinned to the bottom of the panel. */
  div[class*="st-key-chat_input_row"] textarea {
    height: 45px !important;
    min-height: 45px !important;
    max-height:45px !important;
    font-size: 16px !important;
    line-height: 1.4 !important;
    resize: none !important;
    overflow-y: auto !important;
  }
  /* Send button — full-height purple pill matching the textarea.
     Selectors target ``stFormSubmitButton`` (Phase 5.5.x rework wraps
     the input + button in ``st.form(clear_on_submit=True)`` so the
     submit button is no longer a keyed ``st.button``). */
  div[class*="st-key-chat_input_row"] [data-testid="stFormSubmitButton"] button {
    height: 45px !important;
    min-height: 45px !important;
    padding: 0 !important;
    border-radius: 8px !important;
    background-color: #534AB7 !important;
    color: #FFFFFF !important;
    border: none !important;
    font-size: 16px !important;
    font-weight: 600 !important;
    box-shadow: 0 1px 3px rgba(83, 74, 183, 0.30);
    transition: background-color 0.15s ease;
  }
  div[class*="st-key-chat_input_row"] [data-testid="stFormSubmitButton"] button:hover {
    background-color: #443B9F !important;
    color: #FFFFFF !important;
  }
  div[class*="st-key-chat_input_row"] [data-testid="stFormSubmitButton"] button:disabled {
    background-color: var(--text-muted) !important;
    cursor: not-allowed;
  }

  /* Suggested-question pills — always visible (the auto-collapse
     toggle was removed). The pill-to-pill spacing override
     targets the ``element-container`` layer that Streamlit's
     ``stVerticalBlock`` flex gap actually spaces — setting
     margin on the inner ``st-key-q_*`` wrapper doesn't work
     because flex gap is applied one DOM layer up. Adjust the
     ``margin-bottom`` value in the rule below to widen/narrow
     pill-to-pill spacing. */
  div[class*="st-key-chat_panel_overlay"] div[data-testid="stVerticalBlock"] >
    div[data-testid="element-container"]:has(> div[class*="st-key-q_"]) {
    margin-top: 0px !important;
    margin-bottom: 0 !important;
  }
  div[class*="st-key-chat_panel_overlay"] div[class*="st-key-q_"] button {
    font-size: 9px !important;
    padding: 2px 6px !important;
    min-height: 0 !important;
    border-radius: 5px !important;
    line-height: 1.2 !important;
    text-align: center !important;
    justify-content: flex-start !important;
    background: var(--surface) !important;
    border: 1px solid var(--border) !important;
    color: var(--text-2) !important;
    transition: background 0.15s ease, border-color 0.15s ease;
  }
  div[class*="st-key-chat_panel_overlay"] div[class*="st-key-q_"] button:hover {
    background: #EEEDFE !important;
    border-color: #EEEDFE !important;
  }

  /* Mobile responsiveness — drawer becomes a bottom sheet at narrow
     viewports. */
  @media (max-width: 768px) {
    body { --chat-w: 100vw; }
    div[class*="st-key-chat_panel_overlay"] {
      top: auto !important;
      bottom: 0 !important;
      left: 0 !important;
      right: 0 !important;
      width: 100vw !important;
      max-width: none !important;
      height: 75dvh !important;
      border-left: none !important;
      border-top: 1px solid var(--border) !important;
      box-shadow: 0 -8px 24px rgba(15, 31, 46, 0.10) !important;
    }
    body.chat-expanded div[class*="st-key-chat_panel_overlay"] {
      height: 95dvh !important;
    }
    div[class*="st-key-chat_edge_tab"] {
      right: 16px !important;
      top: auto !important;
      bottom: 16px !important;
      transform: none !important;
    }
    div[class*="st-key-chat_edge_tab"] button {
      width: 56px !important;
      height: 56px !important;
      border-radius: 50% !important;
      border: 1px solid var(--border) !important;
      box-shadow: 0 4px 12px rgba(15, 31, 46, 0.15) !important;
    }
  }
</style>
"""


def inject() -> None:
    """Inject the dashboard's CSS into the current Streamlit page."""
    st.markdown(_CSS, unsafe_allow_html=True)


def apply_chat_state_class(chat_state: str) -> None:
    """Toggle a body-level class to drive the chat-drawer width.

    The chat panel CSS responds to ``body.chat-closed`` /
    ``body.chat-side`` / ``body.chat-expanded`` — this helper writes
    the current state to the iframe's ``document.body`` so the CSS
    transitions fire smoothly on state changes.

    Phase 4.5 polish: the prior Enter-to-submit keydown handler is
    removed. Enter now inserts a newline (the textarea's default
    behaviour); only the Send button submits. Removes a JS surface
    area that fought Streamlit's iframe re-mounting and produced
    occasional double-submit flickers on rerun.
    """
    import streamlit.components.v1 as _components
    cls = f"chat-{chat_state}"
    _components.html(
        f"""
        <script>
          const doc = window.parent.document;
          doc.body.classList.remove('chat-closed', 'chat-side', 'chat-expanded');
          doc.body.classList.add({cls!r});
        </script>
        """,
        height=0,
    )
