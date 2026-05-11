"""Chat panel — agent dropdown + suggested questions + free-form input
+ chronological chat history.

Layout (top to bottom):
  1. Specialist-agent dropdown (Demand / Pricing / Anomaly / Trade).
  2. Selected agent's description.
  3. Selected agent's 4 suggested questions as clickable buttons.
  4. Free-form input (text area + Ask button). Keyword-routes to a
     specialist; Phase 1 returns the routed agent's general overview
     handler prefixed with "Based on your question, the X Agent
     suggests..." Phase 2 will replace this with real LLM orchestration.
  5. Chat history (newest first). History persists per-merchant and is
     reset by app.py when the merchant changes.
"""
from __future__ import annotations

from datetime import datetime
from html import escape as h
from typing import Callable

import streamlit as st

from . import data as D
from . import placeholders as P


# ---------------------------------------------------------------------------
# History state
# ---------------------------------------------------------------------------

def _ensure_history(merchant_id: str) -> list[dict]:
    state = st.session_state
    state.setdefault("chat_history", {})
    state.chat_history.setdefault(merchant_id, [])
    return state.chat_history[merchant_id]


def reset_history(merchant_id: str) -> None:
    state = st.session_state
    state.setdefault("chat_history", {})
    state.chat_history[merchant_id] = []


def _push(merchant_id: str, question: str, response: dict) -> None:
    _ensure_history(merchant_id).append({
        "ts":       datetime.now(),
        "question": question,
        **response,
    })


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def render_chat_panel(merchant_id: str) -> None:
    state = st.session_state
    state.setdefault("active_agent", "pricing")
    _ensure_history(merchant_id)
    questions_by_agent = P.questions_for(merchant_id)

    # -- Agent dropdown --
    agent_labels = {
        "demand":  P.AGENT_LABELS["demand"],
        "pricing": P.AGENT_LABELS["pricing"],
        "anomaly": P.AGENT_LABELS["anomaly"],
        "trade":   P.AGENT_LABELS["trade"],
    }
    agent_ids = list(agent_labels.keys())
    chosen = st.selectbox(
        "Specialist agent",
        options=agent_ids,
        format_func=lambda a: agent_labels[a],
        index=agent_ids.index(state.active_agent),
        key=f"agent_select_{merchant_id}",
    )
    if chosen != state.active_agent:
        state.active_agent = chosen
        # Note: do NOT reset chat history on agent switch (per spec).

    # -- Description + 4 suggested-question buttons --
    desc = P.AGENT_DESCRIPTIONS[state.active_agent]
    st.markdown(
        f'<div class="agent-card" style="margin-top:6px;">'
        f'  <div class="agent-desc" style="margin-bottom:8px;">{h(desc)}</div>',
        unsafe_allow_html=True,
    )
    agent_label = P.AGENT_LABELS[state.active_agent]
    for qid, qtext in questions_by_agent[state.active_agent]:
        if st.button(
            qtext,
            key=f"q_{merchant_id}_{state.active_agent}_{qid}",
            use_container_width=True,
        ):
            response = _run_with_live_narration(
                agent_label,
                lambda progress, on_token: P.dispatch(
                    state.active_agent, qid, merchant_id,
                    progress=progress, on_token=on_token,
                ),
            )
            _push(merchant_id, qtext, response)
            st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

    # -- Free-form input --
    st.markdown(
        '<div style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;'
        'color:var(--text-muted);font-weight:600;margin:14px 0 4px;">Ask anything</div>',
        unsafe_allow_html=True,
    )
    with st.form(f"freeform_{merchant_id}", clear_on_submit=True):
        free_q = st.text_area(
            label="Free-form question",
            label_visibility="collapsed",
            placeholder="Type a question — a specialist will pick it up",
            key=f"freeform_text_{merchant_id}",
            height=68,
        )
        submitted = st.form_submit_button("Ask", type="primary")
    if submitted and free_q.strip():
        _handle_free_form(merchant_id, free_q.strip())
        st.rerun()

    # -- Chat history (newest first) --
    history = _ensure_history(merchant_id)
    st.markdown("---")
    if not history:
        st.caption("No questions yet. Pick a suggestion above, or type one in.")
        return
    st.markdown(
        '<div style="font-size:11px;letter-spacing:0.06em;text-transform:uppercase;'
        f'color:var(--text-muted);font-weight:600;margin:8px 0 4px;">'
        f'Chat history ({len(history)})</div>',
        unsafe_allow_html=True,
    )
    for entry in reversed(history):
        _render_chat_entry(entry)


def _handle_free_form(merchant_id: str, question: str) -> None:
    """Route a free-form question through the LLM orchestrator. The
    orchestrator classifies intent via a Haiku router and dispatches
    to the chosen specialist. On any failure (LLM unavailable, router
    error) the orchestrator itself falls back to keyword routing +
    a hardcoded handler — we don't need to handle that here."""
    response = _run_with_live_narration(
        "Conversational Advisor",
        lambda progress, on_token: P.dispatch_orchestrated(
            merchant_id, question,
            progress=progress, on_token=on_token,
        ),
    )
    _push(merchant_id, question, response)


# ---------------------------------------------------------------------------
# Live narration: progress + streaming text in a single placeholder
# ---------------------------------------------------------------------------

def _run_with_live_narration(
    agent_label: str,
    runner: "Callable[[Callable[[int, str], None], Callable[[str], None]], dict]",
) -> dict:
    """Render a single `st.empty()` placeholder that updates first
    with per-turn progress messages, then with streamed final-answer
    text. Returns the runner's response.

    The placeholder is cleared on return — the chat history view
    re-renders the final response with full formatting (table, chart).
    Cache hits skip the callbacks entirely (no Streamlit calls) so
    they return without rendering progress.
    """
    placeholder = st.empty()
    streamed: list[str] = []
    last_progress: list[str] = []

    def on_progress(turn: int, msg: str) -> None:
        last_progress.append(msg)
        # If streaming hasn't started yet, show progress. Once tokens
        # arrive, the stream view takes over and progress is suppressed.
        if not streamed:
            placeholder.markdown(
                f"_:hourglass_flowing_sand: **{agent_label}** — {msg}_"
            )

    def on_token(text: str) -> None:
        streamed.append(text)
        placeholder.markdown("".join(streamed))

    try:
        response = runner(on_progress, on_token)
    finally:
        placeholder.empty()
    return response


def _render_chat_entry(entry: dict) -> None:
    ts: datetime = entry["ts"]
    when = ts.strftime("%H:%M")
    agent = entry.get("agent", "Agent")
    question = entry.get("question", "")
    prose = entry.get("prose", "")
    table = entry.get("table")
    tel = entry.get("telemetry") or {}

    # Per-question telemetry as a hover tooltip on the timestamp. Shows
    # cost, tokens, turns. Surfaced inline so the demo audience can see
    # per-question cost without leaving the chat panel.
    tel_hover = ""
    if tel:
        cost = float(tel.get("cost_usd", 0.0))
        in_tok = int(tel.get("input_tokens", 0))
        out_tok = int(tel.get("output_tokens", 0))
        turns  = int(tel.get("turns", 0))
        tel_hover = (
            f" title=\"~${cost:.4f} · {in_tok:,} in / {out_tok:,} out tokens · "
            f"{turns} turn(s)\""
        )

    head = (
        f'<div class="chat-head">'
        f'  <span class="agent-name">{h(agent)}</span> &middot; '
        f'<span style="cursor: help; text-decoration: underline dotted;"{tel_hover}>{when}</span>'
        f'</div>'
        f'<div class="chat-q">{h(question)}</div>'
    )
    st.markdown(
        f'<div class="chat-entry">{head}<div class="chat-body">',
        unsafe_allow_html=True,
    )
    if prose:
        st.markdown(prose)
    if table is not None and not table.empty:
        st.dataframe(table, use_container_width=True, hide_index=True)
    st.markdown("</div></div>", unsafe_allow_html=True)
