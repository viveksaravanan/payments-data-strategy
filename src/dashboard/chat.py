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


def _render_history_entry(entry: dict) -> None:
    """Render a single completed turn as a user + assistant bubble pair."""
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
                    # Caveats are agent-authored prose too — pre-escape
                    # $ then substitute into the bullet template. Pre-escape
                    # (not inline in the f-string) for Python 3.11
                    # compatibility — see _escape_dollars.
                    safe_c = _escape_dollars(str(c))
                    st.markdown(f"- {safe_c}")


def _render_live_turn(
    question: str,
    agent_label: str,
    runner: "Callable[[Callable[[int, str], None], Callable[[str], None]], dict]",
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
            placeholder.markdown(_escape_dollars("".join(streamed)))

        response = runner(on_progress, on_token)
        placeholder.empty()
        st.caption(response.get("agent", agent_label))
        render_agent_response(response.get("prose", ""))
        tbl = response.get("table")
        if tbl is not None and not tbl.empty:
            st.dataframe(
                tbl, use_container_width=True, hide_index=True,
            )
        if response.get("caveats"):
            with st.expander("Caveats"):
                for c in response["caveats"]:
                    safe_c = _escape_dollars(str(c))
                    st.markdown(f"- {safe_c}")
    return response


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

    # Collect a button click without acting on it — we'll handle it
    # below, INSIDE the chat container, so the streaming bubble appears
    # in the chat window rather than below the buttons.
    pending_click: tuple[str, str] | None = None
    for qid, qtext in questions_by_agent[state.active_agent]:
        if st.button(
            qtext,
            key=f"q_{merchant_id}_{state.active_agent}_{qid}",
            use_container_width=True,
        ):
            pending_click = (qid, qtext)

    # -- Clear chat history button --
    if st.button(
        "Clear chat history",
        key=f"clear_{merchant_id}",
        use_container_width=True,
    ):
        reset_history(merchant_id)
        st.rerun()

    # -- Reserve scrollable chat history container --
    chat_box = st.container(height=700, border=True)

    # -- Free-form input at the bottom (rendered before container fill
    # so it appears visually below the container) --
    free_q = st.chat_input(
        "Ask anything…",
        key=f"chat_input_{merchant_id}",
    )

    # -- Fill the chat container --
    with chat_box:
        if not history and not pending_click and not (free_q and free_q.strip()):
            st.caption(
                "No questions yet. Pick a suggestion above, "
                "or type one in below."
            )
        for entry in history:
            _render_history_entry(entry)

        if pending_click is not None:
            qid, qtext = pending_click
            active_agent = state.active_agent
            response = _render_live_turn(
                qtext,
                agent_label,
                lambda progress, on_token: P.dispatch(
                    active_agent, qid, merchant_id,
                    progress=progress, on_token=on_token,
                ),
            )
            _push(merchant_id, qtext, response)
            st.rerun()
        elif free_q and free_q.strip():
            question = free_q.strip()
            response = _render_live_turn(
                question,
                "Conversational Advisor",
                lambda progress, on_token: P.dispatch_orchestrated(
                    merchant_id, question,
                    progress=progress, on_token=on_token,
                ),
            )
            _push(merchant_id, question, response)
            st.rerun()
