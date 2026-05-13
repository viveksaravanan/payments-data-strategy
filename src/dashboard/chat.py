"""Chat panel — agent selector, suggested questions, per-merchant
chat history, free-form input.

Layout (top to bottom):
  1. Specialist-agent selector.
  2. Selected agent's description + 4 suggested-question buttons.
  3. "Clear chat history" button (only clears the current merchant).
  4. Scrollable chat history container (fixed height).
     Each turn renders as a `st.chat_message` user/assistant bubble.
  5. Free-form `st.chat_input` at the bottom — routes through the
     orchestrator on submit.

Per-merchant isolation: every read / write / clear goes through
`st.session_state.chat_messages_by_merchant[merchant_id]`. Switching the
merchant dropdown changes which bucket is displayed; no message ever
crosses between merchants. This is the UI-layer analog of the
`MerchantContext` binding on the agent side.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable

import streamlit as st

from . import data as D  # noqa: F401  — kept for parity with other modules
from . import placeholders as P


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


def _push(merchant_id: str, question: str, response: dict) -> None:
    _ensure_history(merchant_id).append({
        "ts":       datetime.now(),
        "question": question,
        **response,
    })


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


# ---------------------------------------------------------------------------
# Main render
# ---------------------------------------------------------------------------

def render_chat_panel(merchant_id: str) -> None:
    state = st.session_state
    state.setdefault("active_agent", "pricing")
    history = _ensure_history(merchant_id)
    questions_by_agent = P.questions_for(merchant_id)

    # -- Agent selector --
    agent_ids = ["demand", "pricing", "anomaly", "trade"]
    agent_labels = {a: P.AGENT_LABELS[a] for a in agent_ids}
    chosen = st.selectbox(
        "Specialist agent",
        options=agent_ids,
        format_func=lambda a: agent_labels[a],
        index=agent_ids.index(state.active_agent),
        key=f"agent_select_{merchant_id}",
    )
    if chosen != state.active_agent:
        state.active_agent = chosen
        # Do NOT reset chat history on agent switch.

    # -- Description + 4 suggested-question buttons --
    st.caption(P.AGENT_DESCRIPTIONS[state.active_agent])
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

    # -- Clear chat history button --
    if st.button(
        "Clear chat history",
        key=f"clear_{merchant_id}",
        use_container_width=True,
    ):
        reset_history(merchant_id)
        st.rerun()

    # -- Scrollable chat history container --
    chat_box = st.container(height=480, border=True)
    with chat_box:
        if not history:
            st.caption(
                "No questions yet. Pick a suggestion above, "
                "or type one in below."
            )
        else:
            for entry in history:
                with st.chat_message("user"):
                    # User input — render literally, no $-escape
                    st.markdown(entry.get("question", ""))
                with st.chat_message("assistant"):
                    agent_name = entry.get("agent", "Agent")
                    st.caption(agent_name)
                    render_agent_response(entry.get("prose", ""))
                    tbl = entry.get("table")
                    if tbl is not None and not tbl.empty:
                        st.dataframe(
                            tbl, use_container_width=True, hide_index=True,
                        )
                    if entry.get("caveats"):
                        with st.expander("Caveats"):
                            for c in entry["caveats"]:
                                # Caveats are agent-authored prose too —
                                # pre-escape $ then substitute into the
                                # bullet template. Pre-escape (not inline
                                # in the f-string) for Python 3.11
                                # compatibility — see _escape_dollars.
                                safe_c = _escape_dollars(str(c))
                                st.markdown(f"- {safe_c}")

    # -- Free-form input at the bottom --
    free_q = st.chat_input(
        "Ask anything…",
        key=f"chat_input_{merchant_id}",
    )
    if free_q and free_q.strip():
        _handle_free_form(merchant_id, free_q.strip())
        st.rerun()


# ---------------------------------------------------------------------------
# Free-form path — routes via the LLM orchestrator
# ---------------------------------------------------------------------------

def _handle_free_form(merchant_id: str, question: str) -> None:
    """Free-form questions go through the orchestrator; the orchestrator's
    own routing prefix (e.g. "Routed to the Pricing & Benchmarking Agent…")
    will be embedded in the response prose."""
    response = _run_with_live_narration(
        "Conversational Advisor",
        lambda progress, on_token: P.dispatch_orchestrated(
            merchant_id, question,
            progress=progress, on_token=on_token,
        ),
    )
    _push(merchant_id, question, response)


# ---------------------------------------------------------------------------
# Live narration — progress + streaming tokens
# ---------------------------------------------------------------------------

def _run_with_live_narration(
    agent_label: str,
    runner: "Callable[[Callable[[int, str], None], Callable[[str], None]], dict]",
) -> dict:
    """Render a single placeholder that updates first with per-turn
    progress messages, then with streamed final-answer text.

    The placeholder is cleared on return — the chat history view
    re-renders the final response with full formatting (table, chart,
    caveats). Cache hits skip the callbacks entirely (no Streamlit
    calls) so they return without rendering progress.

    Streaming tokens are $-escaped to match `render_agent_response`.
    """
    placeholder = st.empty()
    streamed: list[str] = []

    def on_progress(turn: int, msg: str) -> None:
        if not streamed:
            placeholder.markdown(
                f"_:hourglass_flowing_sand: **{agent_label}** — {msg}_"
            )

    def on_token(text: str) -> None:
        streamed.append(text)
        # Escape $ in streamed tokens for the same reason
        # `render_agent_response` does — keep dollar amounts from
        # triggering LaTeX-math rendering mid-stream.
        placeholder.markdown(_escape_dollars("".join(streamed)))

    try:
        response = runner(on_progress, on_token)
    finally:
        placeholder.empty()
    return response
