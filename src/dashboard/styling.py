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
  .kpi-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 8px 0 16px; }
  .kpi {
    background: #fff;
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 18px 20px;
    position: relative;
  }
  .kpi.clickable { cursor: pointer; transition: border-color 0.15s ease, box-shadow 0.15s ease; }
  .kpi.clickable:hover { border-color: var(--accent); box-shadow: 0 2px 6px rgba(15, 76, 129, 0.08); }
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
</style>
"""


def inject() -> None:
    """Inject the dashboard's CSS into the current Streamlit page."""
    st.markdown(_CSS, unsafe_allow_html=True)
