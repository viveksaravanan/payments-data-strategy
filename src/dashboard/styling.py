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
  .block-container { padding-top: 1.6rem; padding-bottom: 2rem; max-width: 1500px; }
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
    /* Phase 4.5 polish: 28 × 28 (up from 22) with the canonical
       sparkles SVG rendered as a CSS background-image. The button's
       text label (✦ glyph) is hidden via ``font-size: 0`` — the
       glyph remains in the DOM for screen-reader fallback but the
       SVG bg-image is what's painted. This unifies the icon
       rendering across edge tab + 15 card affordances + panel
       header (panel header uses inline SVG because st.markdown
       supports it). Light purple soft hover, AI-identity color
       #534AB7 throughout. */
    width: 28px !important;
    height: 28px !important;
    max-width: 28px !important;
    min-height: 0 !important;
    padding: 0 !important;
    margin-left: auto !important;
    border-radius: 50% !important;
    border-color: transparent !important;
    background-color: transparent !important;
    background-image: url("data:image/svg+xml;utf8,%3Csvg width='16' height='16' viewBox='0 0 24 24' fill='%23534AB7' xmlns='http://www.w3.org/2000/svg'%3E%3Cpath d='M12 0 L13.5 8.5 L22 10 L13.5 11.5 L12 20 L10.5 11.5 L2 10 L10.5 8.5 Z'/%3E%3Cpath d='M19 14 L19.7 16.3 L22 17 L19.7 17.7 L19 20 L18.3 17.7 L16 17 L18.3 16.3 Z'/%3E%3C/svg%3E") !important;
    background-repeat: no-repeat !important;
    background-position: center !important;
    background-size: 16px 16px !important;
    font-size: 0 !important;  /* hide the ✦ fallback glyph */
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
  div[class*="st-key-chat_panel_overlay"] {
    position: fixed !important;
    top: 56px;
    right: 0;
    bottom: 0;
    width: 40vw;
    max-width: 720px;
    background: #FFFFFF;
    border-left: 1px solid var(--border);
    box-shadow: -8px 0 24px rgba(15, 31, 46, 0.10);
    z-index: 998;
    /* overflow-y: auto so panel content can scroll if it exceeds
       viewport height — the prior ``overflow: visible`` setting
       was pushing the text-input area off the bottom of the viewport
       on shorter displays. The middle-left expand chevron is
       positioned via ``position: fixed`` (relative to the viewport)
       so it isn't clipped by this overflow. */
    overflow-y: auto !important;
    overflow-x: hidden !important;
    padding: 16px 20px 16px 20px;
    transition: width 0.3s ease, max-width 0.3s ease;
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
    right: 0;
    top: 50%;
    transform: translateY(-50%);
    z-index: 997;
    width: 56px !important;
  }
  div[class*="st-key-chat_edge_tab"] button {
    /* Same canonical sparkles SVG as the affordance buttons +
       panel-header avatar — unified visual identity. The ✦ glyph
       in the button label is hidden via font-size: 0. */
    width: 48px !important;
    height: 72px !important;
    padding: 0 !important;
    border-radius: 8px 0 0 8px !important;
    background-color: #FFFFFF !important;
    background-image: url("data:image/svg+xml;utf8,%3Csvg width='24' height='24' viewBox='0 0 24 24' fill='%23534AB7' xmlns='http://www.w3.org/2000/svg'%3E%3Cpath d='M12 0 L13.5 8.5 L22 10 L13.5 11.5 L12 20 L10.5 11.5 L2 10 L10.5 8.5 Z'/%3E%3Cpath d='M19 14 L19.7 16.3 L22 17 L19.7 17.7 L19 20 L18.3 17.7 L16 17 L18.3 16.3 Z'/%3E%3C/svg%3E") !important;
    background-repeat: no-repeat !important;
    background-position: center !important;
    background-size: 24px 24px !important;
    border: 1px solid var(--border) !important;
    border-right: none !important;
    box-shadow: -3px 0 8px rgba(15, 31, 46, 0.10) !important;
    font-size: 0 !important;
    line-height: 1 !important;
    color: transparent !important;
    transition: background-color 0.15s ease;
  }
  div[class*="st-key-chat_edge_tab"] button:hover {
    background-color: #EEEDFE !important;
  }

  /* Phase 4.5 polish — chat panel viewport sizing.
     The chat-history container ("chat_history") inside the panel had
     a fixed 320px height; allow it to grow to fill the available
     space inside the panel, so the panel reads as one continuous
     pane fitting the viewport. */
  div[class*="st-key-chat_history"] {
    height: auto !important;
    max-height: calc(100vh - 380px) !important;
  }

  /* Middle-left expand button — round chevron anchored to the
     viewport (``position: fixed``) at the panel's left edge.
     Anchored via ``right`` so it tracks the panel width as it
     transitions between side (40 vw) and expanded (90 vw).
     Using fixed positioning instead of absolute keeps the chevron
     visible regardless of the panel's overflow-y scrolling. */
  div[class*="st-key-chat_expand_edge"] {
    position: fixed !important;
    right: calc(40vw - 22px);  /* center on the panel's left edge */
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
  /* Phase 4.5 polish: bigger 44 × 44 chevron (was 32 × 32). */
  div[class*="st-key-chat_expand_edge"] button {
    width: 44px !important;
    height: 44px !important;
    min-height: 0 !important;
    padding: 0 !important;
    border-radius: 50% !important;
    background: #FFFFFF !important;
    border: 1px solid var(--border) !important;
    box-shadow: -2px 2px 8px rgba(15, 31, 46, 0.15) !important;
    font-size: 20px !important;
    line-height: 1 !important;
    color: #534AB7 !important;
  }
  div[class*="st-key-chat_expand_edge"] button:hover {
    background: #EEEDFE !important;
    color: #534AB7 !important;
  }

  /* Chat panel header buttons:
     - "Clear chat" is a small text pill (no icon)
     - ✕ close button has no border / square background — bare glyph
       with a hover-only soft purple background. */
  /* Phase 4.5 polish: fixed 96 px width so the pill doesn't stretch
     when the panel transitions from 40 vw → 90 vw. The wrapper rule
     right-aligns the constrained-width button within its column. */
  div[class*="st-key-clear_btn_"] {
    display: flex !important;
    justify-content: flex-end !important;
  }
  div[class*="st-key-clear_btn_"] button {
    width: 96px !important;
    max-width: 96px !important;
    flex: 0 0 96px !important;
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
  div[class*="st-key-close_btn_"] button:hover {
    background: #EEEDFE !important;
    color: #534AB7 !important;
  }

  /* Backdrop — dimmed click-catcher behind the panel when expanded.
     Implemented as a markdown div with ``position: fixed`` that sits
     between the dashboard (z-index: 0) and the panel (z-index: 998). */
  .chat-backdrop {
    position: fixed;
    top: 0; right: 0; bottom: 0; left: 0;
    background: rgba(15, 31, 46, 0.45);
    z-index: 997;
    pointer-events: none;  /* purely visual — click-to-close not wired in this commit */
  }

  /* Phase 4.5 final — Variant B chat input.
     The wrapper container (``chat_input_row``) is styled as one
     rounded white pill with a 1 px border + subtle shadow. The
     ``st.text_area`` inside is restyled to be borderless / shadowless
     so it reads as the pill's interior. The "Send" button (a single
     ``›`` glyph) is positioned absolutely on the right side of the
     pill as a 32 × 32 purple circle. */
  div[class*="st-key-chat_input_row"] {
    position: relative !important;
    background: #FFFFFF !important;
    border: 1px solid var(--border) !important;
    border-radius: 24px !important;
    padding: 4px 48px 4px 16px !important;  /* right padding = arrow + gutter */
    margin-top: 8px !important;
    box-shadow: 0 1px 3px rgba(15, 31, 46, 0.04);
    transition: border-color 0.15s ease, box-shadow 0.15s ease;
  }
  div[class*="st-key-chat_input_row"]:focus-within {
    border-color: #534AB7 !important;
    box-shadow: 0 0 0 3px rgba(83, 74, 183, 0.12);
  }
  /* Textarea inside the pill — borderless, transparent bg, no
     resize handle, no scrollbar at rest. */
  div[class*="st-key-chat_input_row"] textarea {
    border: none !important;
    outline: none !important;
    box-shadow: none !important;
    background: transparent !important;
    padding: 8px 4px !important;
    font-size: 14px !important;
    line-height: 1.4 !important;
    resize: none !important;
    min-height: 0 !important;
  }
  div[class*="st-key-chat_input_row"] textarea:focus {
    border: none !important;
    outline: none !important;
    box-shadow: none !important;
  }
  /* Send button — small purple circle on the right edge of the
     pill, vertically centered. */
  div[class*="st-key-chat_input_row"] div[class*="st-key-chat_send_"] {
    position: absolute !important;
    right: 8px !important;
    top: 50% !important;
    transform: translateY(-50%) !important;
    width: 32px !important;
    z-index: 5;
  }
  div[class*="st-key-chat_input_row"] div[class*="st-key-chat_send_"] button {
    width: 32px !important;
    height: 32px !important;
    min-height: 0 !important;
    padding: 0 !important;
    border-radius: 50% !important;
    background: #534AB7 !important;
    border: none !important;
    color: #FFFFFF !important;
    font-size: 20px !important;
    line-height: 1 !important;
    font-weight: 600 !important;
    box-shadow: 0 1px 3px rgba(83, 74, 183, 0.30);
    transition: background 0.15s ease, transform 0.1s ease;
  }
  div[class*="st-key-chat_input_row"] div[class*="st-key-chat_send_"] button:hover {
    background: #443B9F !important;
    transform: translateY(-1px);
  }
  div[class*="st-key-chat_input_row"] div[class*="st-key-chat_send_"] button:disabled {
    background: var(--text-muted) !important;
    cursor: not-allowed;
    transform: none;
  }
  /* Suppress the Streamlit textarea label area + counter inside the
     pill — ``label_visibility="collapsed"`` hides the label text
     but Streamlit still reserves a small spacer; flatten it. */
  div[class*="st-key-chat_input_row"] [data-testid="stWidgetLabel"] {
    display: none !important;
  }

  /* Mobile responsiveness — drawer becomes a bottom sheet at narrow
     viewports. */
  @media (max-width: 768px) {
    div[class*="st-key-chat_panel_overlay"] {
      top: auto !important;
      bottom: 0 !important;
      left: 0 !important;
      right: 0 !important;
      width: 100vw !important;
      max-width: none !important;
      height: 75vh !important;
      border-left: none !important;
      border-top: 1px solid var(--border) !important;
      box-shadow: 0 -8px 24px rgba(15, 31, 46, 0.10) !important;
    }
    body.chat-expanded div[class*="st-key-chat_panel_overlay"] {
      height: 95vh !important;
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
    transitions fire smoothly on state changes. It also installs a
    one-time global ``keydown`` listener that wires Enter-to-submit
    on the chat panel's text-area (Shift+Enter still inserts a
    newline; the listener is guarded by a sentinel flag so reruns
    don't stack handlers).
    """
    import streamlit.components.v1 as _components
    cls = f"chat-{chat_state}"
    _components.html(
        f"""
        <script>
          const doc = window.parent.document;
          doc.body.classList.remove('chat-closed', 'chat-side', 'chat-expanded');
          doc.body.classList.add({cls!r});

          // One-time Enter-to-submit binding on the chat-panel
          // textarea. The sentinel on ``window.parent`` keeps this
          // idempotent across Streamlit reruns — each rerun re-runs
          // this script but the conditional ensures only the first
          // run attaches the listener.
          if (!window.parent.__chatEnterHandlerInstalled) {{
            window.parent.__chatEnterHandlerInstalled = true;
            doc.addEventListener('keydown', function(e) {{
              if (!e.target || e.target.tagName !== 'TEXTAREA') return;
              if (e.key !== 'Enter' || e.shiftKey) return;
              // Only fire on the chat panel's text-area (key starts
              // with ``chat_input_``), not other textareas on the
              // page that might exist in future cards.
              const wrapper = e.target.closest('[class*="st-key-chat_input_"]');
              if (!wrapper) return;
              e.preventDefault();
              const sendBtn = doc.querySelector(
                '[class*="st-key-chat_send_"] button'
              );
              if (sendBtn) sendBtn.click();
            }});
          }}
        </script>
        """,
        height=0,
    )
