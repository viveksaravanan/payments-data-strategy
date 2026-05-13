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


def _render_history_entry(entry: dict) -> None:
    """Render a single completed turn as a user + assistant bubble pair."""
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
    return response


# ---------------------------------------------------------------------------
# Main render
# ---------------------------------------------------------------------------

def render_chat_panel(merchant_id: str) -> None:
    state = st.session_state
    state.setdefault("active_agent", "pricing")
    state.setdefault("chat_expanded", False)
    history = _ensure_history(merchant_id)
    questions_by_agent = P.questions_for(merchant_id)

    # -- Header row: title (left), expand toggle + clear-history (right) --
    # The expand and clear buttons share narrow columns so they read as
    # compact icon affordances. Keys use the merchant_id suffix; CSS in
    # styling.py targets `st-key-expand_btn_*` / `st-key-clear_btn_*` to
    # apply the smaller padding + accent / danger hover tints.
    h1, h2, h3 = st.columns([0.7, 0.15, 0.15], gap="small")
    with h1:
        st.markdown(
            '<div style="font-size:13px;letter-spacing:0.06em;'
            'text-transform:uppercase;color:var(--accent);font-weight:600;'
            'margin:6px 0 0;">Specialist agents</div>',
            unsafe_allow_html=True,
        )
    with h2:
        expanded = state.chat_expanded
        toggle_icon = "⤡" if expanded else "⤢"
        toggle_help = "Collapse chat" if expanded else "Expand chat"
        if st.button(
            toggle_icon,
            key=f"expand_btn_{merchant_id}",
            help=toggle_help,
            use_container_width=True,
        ):
            state.chat_expanded = not expanded
            st.rerun()
    with h3:
        if st.button(
            "🗑",
            key=f"clear_btn_{merchant_id}",
            help="Clear chat history",
            use_container_width=True,
        ):
            reset_history(merchant_id)
            st.rerun()

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
