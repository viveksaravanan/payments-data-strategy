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
     compactly inside the KPI card header row. */
  div[class*="st-key-ask_about_"] button {
    padding: 2px 6px !important;
    min-height: 0 !important;
    font-size: 14px !important;
    line-height: 1.2 !important;
    border-color: transparent !important;
    background: transparent !important;
    opacity: 0.45;
    transition: opacity 0.15s ease, background 0.15s ease, border-color 0.15s ease;
  }
  div[class*="st-key-ask_about_"] button:hover {
    opacity: 1.0 !important;
    background: var(--accent-soft) !important;
    border-color: var(--accent-soft) !important;
  }
  div[class*="st-key-ask_about_"] button:disabled {
    opacity: 0.25 !important;
    cursor: not-allowed;
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
</style>
"""


def inject() -> None:
    """Inject the dashboard's CSS into the current Streamlit page."""
    st.markdown(_CSS, unsafe_allow_html=True)
