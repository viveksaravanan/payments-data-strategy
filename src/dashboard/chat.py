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

from . import agents as A
from . import data as D
from . import questions as Q


# ---------------------------------------------------------------------------
# Telemetry footer — rendered INSIDE the chat panel so it participates
# in the panel's flex layout (was previously in app.py and added
# height below the panel).
# ---------------------------------------------------------------------------

def _render_telemetry_inline() -> None:
    """One-line cost / token telemetry below the chat input. Only
    rendered when at least one LLM call has been made this session."""
    try:
        from src.agents import llm as _llm
        totals = _llm.session_totals()
    except Exception:  # noqa: BLE001
        totals = None
    if not (totals and totals.get("calls", 0) > 0):
        return
    st.markdown(
        f'<div style="font-size:10px;color:var(--text-muted);'
        f'margin:4px 0 0 0;letter-spacing:0.02em;line-height:1.3;'
        f'text-align:right;">'
        f'{totals["calls"]} calls · '
        f'{totals["input_tokens"]:,} in / '
        f'{totals["output_tokens"]:,} out · '
        f'${totals["cost_usd"]:.4f}'
        f'</div>',
        unsafe_allow_html=True,
    )


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


def _push(
    merchant_id: str,
    question: str,
    response: dict,
    *,
    qid: str | None = None,
) -> None:
    """Append a turn to the merchant's chat history.

    `qid` (when set) lets the history-replay path re-render the
    pattern chart associated with the question — kept as a separate
    field rather than mixed into `response` so it survives the
    `**response` spread without conflict.
    """
    _ensure_history(merchant_id).append({
        "ts":       datetime.now(),
        "question": question,
        "qid":      qid,
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


def _render_history_entry(entry: dict, merchant_id: str) -> None:
    """Render a single completed turn as a user + assistant bubble pair.

    If `entry["qid"]` names a question with a registered chart
    renderer, that pattern's chart is rendered inside the assistant
    bubble after the prose + agent-provided chart/table — keeping
    the bubble cohesive across replay.
    """
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
    *,
    qid: str | None = None,
    merchant_id: str | None = None,
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
    state.setdefault("chat_state", "closed")
    state.setdefault("agent_running", False)
    state.setdefault("pending_dispatch", None)
    # Phase 4.5 follow-up: the affordance prefill is now injected
    # directly into the text-input widget (no separate confirm-to-send
    # card). ``chat_input_prefill`` carries the text from the
    # ask-about-this click to the next rerun's text-area initial value.
    state.setdefault("chat_input_prefill", "")
    history = _ensure_history(merchant_id)

    # When an agent is mid-dispatch (i.e. we're inside the run that will
    # process state.pending_dispatch below), disable every control that
    # could re-trigger a Streamlit rerun — clicks on disabled HTML
    # buttons are blocked browser-side, so the streaming `_render_live_turn`
    # below can't be aborted mid-stream.
    is_running = bool(state.agent_running)
    expanded = state.chat_state == "expanded"

    # -- Expand toggle on the middle-left edge of the panel. Rendered
    # into a Streamlit container with a unique key so CSS can position
    # it ``position: absolute; left: -16px; top: 50%`` (half-outside
    # the panel's left border). The container itself sits inside the
    # panel overlay's DOM tree but visually overhangs to the left. --
    with st.container(key="chat_expand_edge"):
        chevron = "›" if expanded else "‹"
        if st.button(
            chevron,
            key=f"expand_edge_btn_{merchant_id}",
            help=("Collapse chat" if expanded else "Expand chat"),
            disabled=is_running,
            type="secondary",
            use_container_width=True,
        ):
            state.chat_state = "side" if expanded else "expanded"
            st.rerun()

    # -- Header row: avatar + title (left), Clear+X cluster (right).
    # The two action buttons live in a single nested column pair so
    # Clear sits immediately adjacent to X; the cluster is wrapped
    # in a keyed container so CSS can pin the whole group flush to
    # the right edge of the header regardless of how the outer
    # column proportionally resizes when the panel transitions
    # 40 vw → 90 vw. --
    h1, h_actions = st.columns([1, 0.30], gap="small")
    with h1:
        # Avatar (purple circle) + title — canonical sparkles SVG in
        # #534AB7 on #EEEDFE soft fill. The subtitle line below the
        # title (was "{merchant} · {specialist}") was removed in this
        # commit — the merchant context is established by the
        # dashboard header, and the active specialist is visible in
        # the selectbox just below this row, so the subtitle was
        # duplicative.
        st.markdown(
            """
            <div style="display:flex;align-items:center;gap:10px;
                 margin:4px 0 6px 0;">
              <div style="width:36px;height:36px;border-radius:50%;
                   background:#EEEDFE;display:flex;align-items:center;
                   justify-content:center;flex-shrink:0;">
                <svg width="20" height="20" viewBox="0 0 24 24"
                     fill="#534AB7" xmlns="http://www.w3.org/2000/svg">
                  <path d="M12 0 L13.5 8.5 L22 10 L13.5 11.5 L12 20
                           L10.5 11.5 L2 10 L10.5 8.5 Z"/>
                  <path d="M19 14 L19.7 16.3 L22 17 L19.7 17.7
                           L19 20 L18.3 17.7 L16 17 L18.3 16.3 Z"/>
                </svg>
              </div>
              <div style="font-size:24px;font-weight:600;
                   color:var(--text);">Ask the data</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with h_actions:
        with st.container(key="chat_header_actions"):
            ha, hb = st.columns([0.78, 0.22], gap="small")
            with ha:
                if st.button(
                    "Clear",
                    key=f"clear_btn_{merchant_id}",
                    help="Wait for current response…" if is_running else "Clear chat history",
                    use_container_width=True,
                    disabled=is_running,
                    type="secondary",
                ):
                    reset_history(merchant_id)
                    st.rerun()
            with hb:
                if st.button(
                    "✕",
                    key=f"close_btn_{merchant_id}",
                    help="Close chat panel",
                    use_container_width=True,
                    disabled=is_running,
                    type="secondary",
                ):
                    state.chat_state = "closed"
                    st.rerun()

    # -- Agent selector — back to ``st.selectbox`` (the radio aesthetic
    # was generic Streamlit default; the dropdown is cleaner). The
    # 4-item list makes the type-ahead filter a non-issue in practice
    # — the committed value is always one of the four specialists. --
    agent_ids = ["demand", "pricing", "anomaly", "trade"]
    agent_labels = {a: A.AGENT_LABELS[a] for a in agent_ids}
    chosen = st.selectbox(
        "Specialist agent",
        options=agent_ids,
        format_func=lambda a: agent_labels[a],
        index=agent_ids.index(state.active_agent),
        key=f"agent_select_{merchant_id}",
        disabled=is_running,
        label_visibility="collapsed",
    )
    if chosen != state.active_agent and not is_running:
        state.active_agent = chosen
        # Do NOT reset chat history on agent switch.

    agent_label = A.AGENT_LABELS[state.active_agent]

    # Suggested questions — always shown. The previous
    # auto-collapse logic (``suggestions_open_by_merchant``,
    # ``last_active_agent_by_merchant``, ``▾ Hide`` / ``▸ Show``
    # toggles) was removed: it was broken and added complexity
    # without enough payoff. The pills stay visible at all times.
    clicked: tuple[str, str] | None = None
    for q in Q.questions_for(merchant_id, state.active_agent):
        qid, qtext = q["id"], q["text"]
        if st.button(
            qtext,
            key=f"q_{merchant_id}_{state.active_agent}_{qid}",
            use_container_width=True,
            disabled=is_running,
            type="secondary",
        ):
            clicked = (qid, qtext)

    if clicked is not None and not is_running:
        qid, qtext = clicked
        state.pending_dispatch = {
            "kind":     "question",
            "qid":      qid,
            "qtext":    qtext,
            "agent_id": state.active_agent,
            "agent_label": agent_label,
        }
        state.agent_running = True
        st.rerun()

    # Phase 4.5 polish: removed the two ``st.markdown("---")``
    # dividers that previously bracketed the chat-history container.
    # Saved ~34 px and let the history flex grow into the freed
    # space. The ``--- `` between description and suggestions stays
    # (it's the only visual break between control area and content).

    # -- Reserve scrollable chat history container. Base height
    # bumped 220 → 380 px as a fallback floor when the CSS flex
    # override (``height: 100% !important``) doesn't win the
    # cascade. With the flex override active the container fills
    # whatever room the panel allocates; without it, 380 still
    # gives the history a substantial vertical share. --
    chat_box = st.container(height=442, border=True, key="chat_history")

    # -- Free-form input — simple text area + Send button to the
    # right. The whole row is pinned to the bottom of the chat
    # panel by the flex layout in styling.py (the chat-history
    # element-container has ``flex: 1 1 auto`` so it grows to
    # fill all space above this row). The text-area is CSS-locked
    # to a fixed height so it doesn't move as the user types
    # (overflow-y: auto handles longer text). --
    prefill = state.chat_input_prefill or ""
    # Re-key the textarea per (merchant_id, prefill-hash) so a fresh
    # prefill from an affordance click forces the widget to remount
    # with the new ``value=``. The form wrapper below
    # (``clear_on_submit=True``) handles the typed-text-after-send
    # case — Streamlit clears every widget inside the form on submit
    # whether the user clicked Send or pressed Cmd/Ctrl+Enter.
    input_key = f"chat_input_{merchant_id}_{hash(prefill) & 0xFFFF:04x}"
    with st.container(key="chat_input_row"):
        with st.form(
            key=f"chat_form_{merchant_id}",
            clear_on_submit=True,
            border=False,
        ):
            col_input, col_send = st.columns([1, 0.18], gap="small")
            with col_input:
                free_q = st.text_area(
                    "Ask anything…",
                    value=prefill,
                    key=input_key,
                    height=68,
                    disabled=is_running,
                    label_visibility="collapsed",
                    placeholder="Ask any question about your data…",
                )
            with col_send:
                send_clicked = st.form_submit_button(
                    "Send",
                    type="primary",
                    disabled=is_running,
                    use_container_width=True,
                )
            if send_clicked and free_q and free_q.strip():
                state.pending_dispatch = {
                    "kind":     "free",
                    "question": free_q.strip(),
                }
                state.chat_input_prefill = ""
                state.agent_running = True
                st.rerun()

    # -- Fill the chat container --
    with chat_box:
        # Phase 4.5 final: "No questions yet" placeholder removed.
        # The input area's placeholder copy already invites typing;
        # a duplicate empty-state message added noise. An empty
        # ``chat_box`` reads as an open canvas waiting for the first
        # turn.
        pending = state.pending_dispatch
        for entry in history:
            _render_history_entry(entry, merchant_id)

        if pending is not None:
            try:
                if pending["kind"] == "question":
                    qid       = pending["qid"]
                    qtext     = pending["qtext"]
                    agent_id  = pending["agent_id"]
                    label     = pending["agent_label"]
                    response = _render_live_turn(
                        qtext,
                        label,
                        lambda progress, on_token: A.dispatch(
                            agent_id, qid, merchant_id,
                            progress=progress, on_token=on_token,
                        ),
                        qid=qid,
                        merchant_id=merchant_id,
                    )
                    _push(merchant_id, qtext, response, qid=qid)
                else:  # "free"
                    question = pending["question"]
                    response = _render_live_turn(
                        question,
                        "Conversational Advisor",
                        lambda progress, on_token: A.dispatch_orchestrated(
                            merchant_id, question,
                            progress=progress, on_token=on_token,
                        ),
                    )
                    _push(merchant_id, question, response)
            finally:
                state.pending_dispatch = None
                state.agent_running = False
            st.rerun()

    # -- Session telemetry — rendered INSIDE the chat panel so it
    # participates in the panel's flex layout (was previously in
    # app.py, adding height below the panel). Compact 8 px single
    # line; only renders when at least one LLM call has been
    # made. --
    _render_telemetry_inline()
