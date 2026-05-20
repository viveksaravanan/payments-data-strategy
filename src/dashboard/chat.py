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
from . import chart_patterns as CP
from . import data as D
from . import questions as Q


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
# Question → chart renderer registry
#
# Each entry maps a suggested-question ID to a function that fetches the
# question's data and renders the appropriate chart pattern. Called from
# both the live-turn path (after the agent's prose lands) and the
# history-replay path (on every rerun).
#
# Phase 4.1 wires A1 only. Phase 4.2 grows the registry as more
# questions land.
# ---------------------------------------------------------------------------

def _render_a1(merchant_id: str) -> None:
    """A1: University City weekly transaction trajectory (Pattern 1)."""
    chart_data = D.uc_decline_trajectory(merchant_id)
    if not chart_data.get("weeks"):
        st.caption("_No University City data available for this merchant._")
        return

    # If the viewer has no UC stores at all, render the peer overlays
    # only and a different takeaway. Avoids the "you dropped 0%" gibberish.
    if not chart_data.get("trough_week"):
        takeaway = (
            "You have no University City stores in the panel — "
            "showing peer trajectories only."
        )
    else:
        takeaway = CP.format_takeaway(
            "Your UC transactions dropped {own_pct_drop}% from baseline "
            "by week of {trough_week}; peers also declined "
            "({peer_a_pct_drop}% and {peer_b_pct_drop}%). "
            "The pattern is {market_signal}.",
            chart_data,
        )

    CP.render_time_series_vs_peers(
        chart_data,
        title="University City weekly transactions",
        takeaway=takeaway,
        show_peers=chart_data.get("has_peers", True),
    )


def _render_p1(merchant_id: str) -> None:
    """P1: category × peer pricing heatmap (Pattern 3 cross-merchant diverging)."""
    chart_data = D.category_peer_pricing_gaps(merchant_id)
    if not chart_data["rows"]:
        st.caption("_No pricing data available for this merchant._")
        return

    # The takeaway varies based on whether the matrix spans zero:
    #   - mixed (positive + negative cells): "above X in Y; below Z in W"
    #   - all-positive: "above peers across the board; widest in Y"
    #   - all-negative: "below peers across the board; widest in Y"
    #   - near-parity: "at or near peer levels"
    PARITY = 0.5
    above = chart_data["max_above"]  # (value, category, peer)
    below = chart_data["max_below"]
    if above and below and above[0] > PARITY and below[0] < -PARITY:
        takeaway = (
            f"You're priced {above[0]:.1f}% above {CP.peer_display(above[2])} "
            f"in {above[1]}; {abs(below[0]):.1f}% below "
            f"{CP.peer_display(below[2])} in {below[1]}."
        )
    elif above and above[0] > PARITY:
        takeaway = (
            f"You're priced above peers across categories; "
            f"widest gap: +{above[0]:.1f}% in {above[1]} "
            f"(vs {CP.peer_display(above[2])})."
        )
    elif below and below[0] < -PARITY:
        takeaway = (
            f"You're priced below peers across categories; "
            f"widest gap: {below[0]:.1f}% in {below[1]} "
            f"(vs {CP.peer_display(below[2])})."
        )
    else:
        takeaway = "Your prices are at or near peer levels across categories."

    CP.render_heatmap(
        chart_data,
        title="Your prices vs peer grocers, by category",
        takeaway=takeaway,
        mode="cross_merchant_diverging",
    )


def _render_p2(merchant_id: str) -> None:
    """P2: staple-tier vs non-food-tier pricing positioning (Pattern 2)."""
    chart_data = D.staple_vs_nonfood_pricing(merchant_id)
    if not chart_data["panel_a_data"]["categories"] and \
       not chart_data["panel_b_data"]["categories"]:
        st.caption("_No pricing data available for this merchant._")
        return
    takeaway = CP.format_takeaway(
        "Your staple tier averages {staple_pct:+.1f}% vs Peer A; "
        "non-food tier averages {nonfood_pct:+.1f}%. "
        "Your pricing strategy is {tier_signal} across tiers.",
        chart_data,
    )
    CP.render_cross_merchant_comparison(
        chart_data,
        title="Pricing positioning: staples vs non-food",
        takeaway=takeaway,
        mode="two_panel",
    )


def _render_p3(merchant_id: str) -> None:
    """P3: pricing-leverage scatter — volume × peer-gap quadrants (Pattern 4)."""
    chart_data = D.category_pricing_leverage(merchant_id)
    if not chart_data["points"]:
        st.caption("_No pricing data available for this merchant._")
        return
    above = chart_data["above_peer_names"]
    if above:
        names = ", ".join(above)
        takeaway = (
            f"Your largest priced-above-peers categories are {names}; "
            f"{chart_data['top_volume_category']} is the highest-volume "
            "opportunity."
        )
    else:
        takeaway = (
            "You're at or below peer pricing on every category; "
            f"{chart_data['top_volume_category']} is your largest "
            "category by volume."
        )
    CP.render_scatter_with_peers(
        chart_data,
        title="Pricing leverage by category",
        takeaway=takeaway,
    )


def _render_d4(merchant_id: str) -> None:
    """D4: own share vs peer share — basket-mix scatter with parity line (Pattern 4)."""
    chart_data = D.category_share_vs_peer_share(merchant_id)
    if not chart_data["points"]:
        st.caption("_No basket-mix data available for this merchant._")
        return
    takeaway = (
        f"{chart_data['over_category']} overperforms peers by "
        f"{chart_data['over_pp']:+.1f}pp share; "
        f"{chart_data['under_category']} underperforms by "
        f"{chart_data['under_pp']:+.1f}pp."
    )
    CP.render_scatter_with_peers(
        chart_data,
        title="Your category mix vs peer-average",
        takeaway=takeaway,
    )


def _render_d3(merchant_id: str) -> None:
    """D3: basket-mix fingerprint vs peer-average (Pattern 2 diverging)."""
    chart_data = D.basket_mix_vs_peers(merchant_id)
    if not chart_data["categories"]:
        st.caption("_No basket-mix data available for this merchant._")
        return
    takeaway = CP.format_takeaway(
        "You're over-indexed on {top_category} (+{top_pp:.1f}pp vs "
        "peer-average); under-indexed on {bottom_category} "
        "({bottom_pp:.1f}pp).",
        chart_data,
    )
    CP.render_cross_merchant_comparison(
        chart_data,
        title="Your basket mix vs peer-average",
        takeaway=takeaway,
        mode="diverging",
    )


QUESTION_RENDERERS: dict[str, Callable[[str], None]] = {
    "A1": _render_a1,
    "P1": _render_p1,
    "P2": _render_p2,
    "P3": _render_p3,
    "D3": _render_d3,
    "D4": _render_d4,
}


def _render_question_chart(qid: str | None, merchant_id: str) -> None:
    """Render the chart associated with `qid`, if a renderer is wired.

    Wrapped in try/except so a chart-render failure doesn't break the
    surrounding chat-message bubble.
    """
    if not qid:
        return
    renderer = QUESTION_RENDERERS.get(qid)
    if renderer is None:
        return
    try:
        renderer(merchant_id)
    except Exception as exc:  # noqa: BLE001 — chart errors are non-fatal
        st.caption(f"_(chart render failed: {type(exc).__name__})_")


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
        if not entry.get("error"):
            _render_question_chart(entry.get("qid"), merchant_id)


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
        if qid and merchant_id and not response.get("error"):
            _render_question_chart(qid, merchant_id)
    return response


# ---------------------------------------------------------------------------
# Main render
# ---------------------------------------------------------------------------

def render_chat_panel(merchant_id: str) -> None:
    state = st.session_state
    state.setdefault("active_agent", "pricing")
    state.setdefault("chat_expanded", False)
    state.setdefault("agent_running", False)
    state.setdefault("pending_dispatch", None)
    # Phase 4.1: Ask-about-this affordance pre-fills the chat with a
    # context-aware question. Streamlit's st.chat_input doesn't accept
    # a value= argument, so the prefill is rendered as a "confirm to
    # send" card above the input rather than injected into the field.
    state.setdefault("chat_input_prefill", None)
    history = _ensure_history(merchant_id)

    # When an agent is mid-dispatch (i.e. we're inside the run that will
    # process state.pending_dispatch below), disable every control that
    # could re-trigger a Streamlit rerun — clicks on disabled HTML
    # buttons are blocked browser-side, so the streaming `_render_live_turn`
    # below can't be aborted mid-stream.
    is_running = bool(state.agent_running)

    # -- Header row: title (left), expand toggle + clear-history (right) --
    # The expand and clear buttons share narrow columns so they read as
    # compact icon affordances. Keys use the merchant_id suffix; CSS in
    # styling.py targets `st-key-expand_btn_*` / `st-key-clear_btn_*` to
    # apply the smaller padding + accent / danger hover tints, plus the
    # explicit disabled fade.
    h1, h2, h3 = st.columns([0.7, 0.15, 0.15], gap="small")
    with h1:
        st.markdown("#### Ask the data")
    with h2:
        expanded = state.chat_expanded
        toggle_icon = "⤡" if expanded else "⤢"
        if is_running:
            toggle_help = "Wait for current response…"
        else:
            toggle_help = "Collapse chat" if expanded else "Expand chat"
        if st.button(
            toggle_icon,
            key=f"expand_btn_{merchant_id}",
            help=toggle_help,
            use_container_width=True,
            disabled=is_running,
            type="secondary",
        ):
            state.chat_expanded = not expanded
            # Tell the next run to scroll the parent window to the top
            # — without this the user lands wherever the document was
            # scrolled when they clicked the icon, which is often the
            # bottom of the prior layout.
            state.scroll_to_top_pending = True
            st.rerun()
    with h3:
        if st.button(
            "🗑",
            key=f"clear_btn_{merchant_id}",
            help="Wait for current response…" if is_running else "Clear chat history",
            use_container_width=True,
            disabled=is_running,
            type="secondary",
        ):
            reset_history(merchant_id)
            st.rerun()

    # -- Agent selector (disabled during a run so an agent switch can't
    # abort the in-flight dispatch). label_visibility="collapsed" drops
    # the "Specialist agent" sub-label — the dropdown content is
    # self-explanatory. The label string is kept for screen readers. --
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

    # -- Description --
    st.caption(A.AGENT_DESCRIPTIONS[state.active_agent])
    agent_label = A.AGENT_LABELS[state.active_agent]

    st.markdown("---")

    # Suggested questions: clicking enqueues a pending dispatch but
    # does NOT run the agent in this run. Streamlit's auto-rerun on
    # button-click fires, and the next run renders everything with
    # is_running=True before the dispatch executes inside the chat
    # container — so the user can't disrupt mid-stream.
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

    # ----- DEBUG: Phase 4.1 test affordance -----
    # Remove in Phase 4.4 when real card-side "Ask about this" buttons
    # land in the dashboard column. Lets us exercise the pre-fill +
    # specialist-snap plumbing without dashboard cards.
    if st.button(
        "🧪 [DEBUG] Test Ask-about-this → A1",
        key=f"debug_ask_about_{merchant_id}",
        disabled=is_running,
        use_container_width=True,
    ):
        state.chat_input_prefill = (
            "What's driving the transaction drop at my "
            "University City stores?"
        )
        state.active_agent = "anomaly"
        st.rerun()
    # ----- end DEBUG -----

    st.markdown("---")

    # -- Reserve scrollable chat history container --
    chat_box = st.container(height=700, border=True)

    st.markdown("---")

    # -- Pending-question card (confirm-to-send for the Ask-about-this
    # affordance). Streamlit's st.chat_input has no value= parameter,
    # so we surface the templated question as a small card with Send /
    # Cancel buttons. The chat_input below stays as the free-form input.
    prefill = state.chat_input_prefill
    if prefill is not None:
        with st.container(border=True):
            st.caption("Confirm to send:")
            st.markdown(prefill)
            cs, cc = st.columns([1, 1])
            with cs:
                if st.button(
                    "Send",
                    key=f"prefill_send_{merchant_id}",
                    type="primary",
                    disabled=is_running,
                    use_container_width=True,
                ):
                    state.pending_dispatch = {
                        "kind":     "free",
                        "question": prefill,
                    }
                    state.chat_input_prefill = None
                    state.agent_running = True
                    st.rerun()
            with cc:
                if st.button(
                    "Cancel",
                    key=f"prefill_cancel_{merchant_id}",
                    disabled=is_running,
                    use_container_width=True,
                ):
                    state.chat_input_prefill = None
                    st.rerun()

    # -- Free-form input at the bottom (rendered before container fill
    # so it appears visually below the container) --
    free_q = st.chat_input(
        "Ask anything…",
        key=f"chat_input_{merchant_id}",
        disabled=is_running,
    )
    if free_q and free_q.strip() and not is_running:
        state.pending_dispatch = {
            "kind":     "free",
            "question": free_q.strip(),
        }
        state.agent_running = True
        st.rerun()

    # -- Fill the chat container --
    with chat_box:
        pending = state.pending_dispatch
        if not history and pending is None:
            st.caption(
                "No questions yet. Pick a suggestion above, "
                "or type one in below."
            )
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
