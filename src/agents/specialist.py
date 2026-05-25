"""Base class for Phase 2 LLM-backed specialist agents.

A specialist owns:
  - A `MerchantContext` (viewer binding).
  - A prompt template (Markdown, persona / scope / tool guidance).
  - A tool list (Anthropic SDK input_schema format).
  - A bounded tool loop (hard cap = MAX_TURNS, mirrors the existing
    MerchantAdvisor pattern).
  - State accumulators: SQL log, last query result table, chart figure,
    cost telemetry.

Subclasses (PricingSpecialist, AnomalySpecialist, etc.) override:
  - AGENT_LABEL (display name for the chat panel).
  - PROMPT_PATH (path to the persona prompt).
  - Optionally TOOLS (default = T.TOOLS_SPECIALIST).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from src.agents import llm as L
from src.agents import tools as T
from src.agents.context import MerchantContext

# Default turn cap. Bumped from 8 to 10 in Phase 5.1.9 because
# chart-takeaway injection adds an analytical reconciliation
# workflow: agent's initial query may use a different window than
# the chart, requires re-query to match, plus the normal
# query → chart → respond sequence. Net: 10 gives reasonable
# headroom for the common case.
DEFAULT_MAX_TURNS = 10
# Output budget per turn. Bumped 2048 → 4096 in Phase 5.1.10 so
# the trailing caveats fence never gets truncated mid-stream when
# the chart-takeaway reconciliation context inflates the response.
# Haiku output is cheap (~$5/M); allocating doesn't cost — we only
# pay for tokens actually generated.
MAX_TOKENS = 4096

# Phase 2A.5: per-turn progress narration. The Specialist fires
# progress(turn_idx, message) at the start of each turn so the
# dashboard can show forward motion in the spinner.
PROGRESS_MESSAGES = [
    "Looking up your data…",
    "Comparing with peer data…",
    "Building the comparison…",
    "Finalizing analysis…",
]

# Caveats block: the prompt instructs the agent to append a fenced
# JSON list at the very end. The renderer parses it out into a
# structured `caveats` field so the dashboard can render it muted.
_CAVEATS_RE = re.compile(
    r"```caveats\s*\n(.*?)\n```\s*$",
    re.DOTALL | re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------

@dataclass
class SpecialistResponse:
    agent:         str
    prose:         str
    table:         pd.DataFrame | None = None
    chart:         Any | None = None      # go.Figure if present
    caveats:       list[str] = field(default_factory=list)
    sql:           list[dict[str, Any]] = field(default_factory=list)
    converged:     bool = True
    turns:         int = 0
    input_tokens:  int = 0
    output_tokens: int = 0
    cost_usd:      float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Shape the dashboard's chat panel expects from `dispatch()`."""
        return {
            "agent":   self.agent,
            "prose":   self.prose,
            "table":   self.table,
            "chart":   self.chart,
            "caveats": list(self.caveats),
            "sql":     list(self.sql),
            "telemetry": {
                "input_tokens":  self.input_tokens,
                "output_tokens": self.output_tokens,
                "cost_usd":      self.cost_usd,
                "turns":         self.turns,
                "converged":     self.converged,
            },
        }


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class Specialist:
    """Subclass-with-class-vars pattern. Override the four class
    attributes; the loop is the same."""

    AGENT_LABEL: str = ""
    PROMPT_PATH: Path | None = None
    TOOLS: list[dict[str, Any]] = T.TOOLS_SPECIALIST
    MODEL: str = L.MODEL_SPECIALIST
    MAX_TURNS: int = DEFAULT_MAX_TURNS

    def __init__(self, context: MerchantContext) -> None:
        if not self.AGENT_LABEL or self.PROMPT_PATH is None:
            raise NotImplementedError(
                f"{type(self).__name__} must set AGENT_LABEL and PROMPT_PATH."
            )
        self.context = context
        self._system_prompt = self._render_prompt()

        # Per-call state. Reset on every answer().
        self._sql_log:    list[dict[str, Any]] = []
        self._last_table: dict[str, Any] | None = None
        self._chart:      Any | None = None
        self._in_tokens:  int = 0
        self._out_tokens: int = 0
        self._cost_usd:   float = 0.0

    # ---- Prompt rendering ----------------------------------------------

    def _render_prompt(self) -> str:
        raw = self.PROMPT_PATH.read_text()
        return (
            raw
            .replace("{{viewer_id}}",      self.context.viewing_merchant_id)
            .replace("{{viewer_name}}",    self.context.viewing_merchant_name)
            .replace("{{viewer_segment}}", self.context.viewing_merchant_segment)
        )

    # ---- Public ---------------------------------------------------------

    def answer(
        self,
        question: str,
        *,
        progress: "Callable[[int, str], None] | None" = None,
        on_token: "Callable[[str], None] | None" = None,
    ) -> SpecialistResponse:
        """Run the bounded tool loop and return a structured response.

        Optional callbacks (Phase 2A.5):
          - `progress(turn_index, message)` fires at the start of each
            turn so the dashboard can update its spinner with forward
            motion.
          - `on_token(text)` fires for each text delta when streaming.
            When provided, the LLM call uses Anthropic's streaming API
            and emits text deltas as they arrive (typically only the
            final non-tool-use turn produces meaningful streamed text).
        """
        # Reset per-call state
        self._sql_log    = []
        self._last_table = None
        self._chart      = None
        self._in_tokens  = 0
        self._out_tokens = 0
        self._cost_usd   = 0.0

        messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
        use_streaming = on_token is not None

        for turn in range(self.MAX_TURNS):
            if progress is not None:
                progress(turn, PROGRESS_MESSAGES[min(turn, len(PROGRESS_MESSAGES) - 1)])

            if use_streaming:
                resp, tel = L.call_with_tools_streaming(
                    model=self.MODEL,
                    system=self._system_prompt,
                    tools=self.TOOLS,
                    messages=messages,
                    max_tokens=MAX_TOKENS,
                    on_text_delta=on_token,
                )
            else:
                resp, tel = L.call_with_tools(
                    model=self.MODEL,
                    system=self._system_prompt,
                    tools=self.TOOLS,
                    messages=messages,
                    max_tokens=MAX_TOKENS,
                )
            self._in_tokens  += tel.input_tokens
            self._out_tokens += tel.output_tokens
            self._cost_usd   += tel.cost_usd

            messages.append({"role": "assistant", "content": resp.content})

            if resp.stop_reason != "tool_use":
                text = self._extract_text(resp.content)
                return self._finalize(text, converged=True, turns=turn + 1)

            # Process all tool_use blocks in this turn before going back
            # to the model. The model can mix text + multiple tool_use
            # blocks in the same response; we acknowledge them all in
            # one user message.
            tool_results: list[dict[str, Any]] = []
            for block in resp.content:
                if getattr(block, "type", "") != "tool_use":
                    continue
                try:
                    result = self._dispatch_tool(
                        block.name, dict(block.input or {}),
                    )
                    is_error = False
                except Exception as exc:  # noqa: BLE001
                    result = {"error": f"{type(exc).__name__}: {exc}"}
                    is_error = True
                # Phase 2A.5: trim the payload returned to the LLM —
                # cap rows, round floats, drop empty columns. The
                # specialist's `_last_table` keeps the full-precision
                # version for rendering.
                payload_for_llm = (
                    T.trim_for_llm(result)
                    if (not is_error and isinstance(result, dict)
                         and "rows" in result)
                    else result
                )
                tool_results.append({
                    "type":        "tool_result",
                    "tool_use_id": block.id,
                    "content":     json.dumps(payload_for_llm, default=str),
                    "is_error":    is_error,
                })
            messages.append({"role": "user", "content": tool_results})

        # Loop exhausted without a final answer.
        partial = (
            "(I couldn't converge on a full answer in "
            f"{self.MAX_TURNS} turns. Showing the best-effort partial — see "
            "the SQL below for what I queried.)"
        )
        return self._finalize(partial, converged=False, turns=self.MAX_TURNS)

    # ---- Internals ------------------------------------------------------

    def _dispatch_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        ctx = self.context
        if name == "schema_info":
            return ctx.schema_info()

        if name == "query_tenant":
            res = ctx.query_tenant(args["query"])
            self._sql_log.append(
                {"tool": "tenant", "query": args["query"], "row_count": res.get("row_count", 0)}
            )
            self._last_table = res
            return res

        if name == "query_lake":
            res = ctx.query_lake(args["query"])
            self._sql_log.append(
                {"tool": "lake", "query": args["query"], "row_count": res.get("row_count", 0)}
            )
            self._last_table = res
            return res

        if name == "make_chart":
            fig = ctx.make_chart(args)
            self._chart = fig
            return T.make_chart_ack(args)

        raise ValueError(f"Unknown tool: {name}")

    @staticmethod
    def _extract_text(content_blocks: list[Any]) -> str:
        return "".join(
            b.text for b in content_blocks
            if getattr(b, "type", "") == "text"
        )

    def _finalize(
        self, text: str, *, converged: bool, turns: int,
    ) -> SpecialistResponse:
        prose, caveats = self._split_caveats(text)
        table_df: pd.DataFrame | None = None
        if self._last_table and self._last_table.get("rows"):
            table_df = pd.DataFrame(
                self._last_table["rows"],
                columns=self._last_table["columns"],
            )
        return SpecialistResponse(
            agent=self.AGENT_LABEL,
            prose=prose.strip(),
            table=table_df,
            chart=self._chart,
            caveats=caveats,
            sql=list(self._sql_log),
            converged=converged,
            turns=turns,
            input_tokens=self._in_tokens,
            output_tokens=self._out_tokens,
            cost_usd=self._cost_usd,
        )

    @staticmethod
    def _split_caveats(text: str) -> tuple[str, list[str]]:
        """Pull a trailing ```caveats\\n[...]``` JSON-list block out of
        the prose. Returns (prose_without_block, caveats_list). If the
        block is missing or malformed, returns (text, [])."""
        m = _CAVEATS_RE.search(text)
        if not m:
            return text, []
        body = m.group(1).strip()
        try:
            caveats = json.loads(body)
            if not isinstance(caveats, list):
                return text, []
            caveats = [str(c).strip() for c in caveats if c]
        except Exception:  # noqa: BLE001
            return text, []
        prose = text[: m.start()].rstrip()
        return prose, caveats
