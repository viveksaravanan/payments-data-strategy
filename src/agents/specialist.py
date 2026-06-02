"""Wave 3 specialist base class.

A specialist:

* Owns a viewer (``MerchantContext``).
* Owns a prompt template (Markdown, persona + scope + tool guidance).
* Runs a bounded tool loop using ``src.agents.lake_tools`` —
  ``query_tenant`` and ``read_lake_table``.
* Parses the model's final turn for a fenced ``render`` block
  (merge spec + chart_intent + claims) and a fenced ``caveats``
  block. The render block is the structured §1 contract emission.
* Merges the captured tenant + lake frames into one comparison
  result (``response.merge_own_and_peer``).
* Builds the chart deterministically (``chart_build.build_chart``).
* Validates the prose against the result + claims
  (``claims.validate_claims``) — strict guarantee, graceful handling.
* Returns ``AgentResponse`` (D25.1).

Subclasses (Pricing, Demand, Trade-Area, Anomaly) override:

* ``AGENT_LABEL`` — display name for the chat panel.
* ``PROMPT_PATH`` — persona prompt.
* Optionally ``MAX_TURNS``.

Everything else lives in this base.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from src.agents import lake_tools as LT
from src.agents import llm as L
from src.agents.chart_build import (
    MissingColumnError,
    UnsupportedIntentError,
    build_chart,
)
from src.agents.claims import (
    CellLookup,
    Claim,
    Derivation,
    validate_claims,
)
from src.agents.context import MerchantContext
from src.agents.response import (
    AgentResponse,
    MergeGrainError,
    MergeUnitMismatchError,
    SqlSurface,
    Telemetry,
    ViewerScopingError,
    check_magnitude_compatibility,
    merge_own_and_peer,
)


DEFAULT_MAX_TURNS = 10
MAX_TOKENS = 4096


PROGRESS_MESSAGES = [
    "Looking up your data…",
    "Comparing with peer data…",
    "Building the comparison…",
    "Finalizing analysis…",
]


# ---------------------------------------------------------------------
# Public errors
# ---------------------------------------------------------------------

class RenderBlockMissingError(ValueError):
    """The model's final assistant turn did not include a parseable
    ``render`` fenced block. Without it the specialist can't build
    the §1 AgentResponse — bug in the prompt or model output."""


class RenderBlockInvalidError(ValueError):
    """The render block was present but its shape doesn't match the
    expected schema (missing merge / chart_intent / claims)."""


# ---------------------------------------------------------------------
# Specialist
# ---------------------------------------------------------------------

class Specialist:
    """Wave 3 specialist base class. Subclasses set the four class
    attributes; the loop, parsing, merging, chart, and validation
    are common."""

    AGENT_LABEL: str = ""
    PROMPT_PATH: Path | None = None
    TOOLS: list[dict[str, Any]] = LT.TOOLS_SPECIALIST
    MODEL: str = L.MODEL_SPECIALIST
    MAX_TURNS: int = DEFAULT_MAX_TURNS

    # Subclasses override these to tell the merge step what to merge
    # on. ``MERGE_DEFAULT`` is used when the model's render block
    # doesn't supply its own (typically only when no own-side query
    # ran, e.g. Advisor on payment_mix).
    MERGE_REQUIRED: bool = True

    def __init__(self, context: MerchantContext) -> None:
        if not self.AGENT_LABEL or self.PROMPT_PATH is None:
            raise NotImplementedError(
                f"{type(self).__name__} must set AGENT_LABEL + PROMPT_PATH."
            )
        self.context = context
        self._system_prompt = self._render_prompt()

        # Per-call state
        self._sql_log:      list[dict[str, Any]] = []
        self._tenant_frame: pd.DataFrame | None = None
        self._lake_frame:   pd.DataFrame | None = None
        self._lake_manifest: dict[str, Any] | None = None
        self._emit_args:    dict[str, Any] | None = None
        self._in_tokens:    int = 0
        self._out_tokens:   int = 0
        self._cost_usd:     float = 0.0

    # ---- Prompt rendering -------------------------------------------

    _SHARED_RULES_PATH = (
        Path(__file__).parent / "prompts" / "_shared_answering_rules.md"
    )

    def _render_prompt(self) -> str:
        raw = self.PROMPT_PATH.read_text()
        # Inject the shared answering rules at the end of every
        # specialist prompt so the four Checkpoint 2 v3 failure
        # modes (wrong-filter, defer-to-clarification, ungrounded
        # prose, mislabel) get the same treatment regardless of
        # which specialist the dispatch resolves to.
        if self._SHARED_RULES_PATH.exists():
            raw = raw.rstrip() + "\n\n---\n\n" + self._SHARED_RULES_PATH.read_text()
        return (
            raw.replace("{{viewer_id}}", self.context.viewing_merchant_id)
               .replace("{{viewer_name}}", self.context.viewing_merchant_name)
               .replace(
                   "{{viewer_segment}}",
                   self.context.viewing_merchant_segment,
               )
        )

    # ---- Public ------------------------------------------------------

    def answer(
        self,
        question: str,
        *,
        progress: Callable[[int, str], None] | None = None,
        on_token: Callable[[str], None] | None = None,  # noqa: ARG002 — see comment below
    ) -> AgentResponse:
        """Run the bounded tool loop and produce an AgentResponse.

        ``on_token`` is intentionally unwired pending Wave 4 — the
        streaming surface (``L.call_with_tools_streaming``) exists
        but the live path uses ``L.call_with_tools`` with
        ``tool_choice="any"`` / pinned ``emit_response``, where the
        delivered prose comes from a tool call's args (not a
        streamed text block). When the Wave 4 dashboard adds a
        streaming chat panel, wire ``on_token`` to the streaming
        call site there.
        """
        self._reset_state()

        messages: list[dict[str, Any]] = [
            {"role": "user", "content": question}
        ]
        final_text = ""

        for turn in range(self.MAX_TURNS):
            if progress is not None:
                progress(
                    turn,
                    PROGRESS_MESSAGES[
                        min(turn, len(PROGRESS_MESSAGES) - 1)
                    ],
                )

            # tool_choice strategy:
            #  - Early turns: "any" — force a tool call but let the
            #    model pick which one (typically query_tenant /
            #    read_lake_table to gather data).
            #  - Final 2 turns OR once enough data has been gathered
            #    (both tenant + lake frames non-empty, or one is
            #    non-empty and the model has been at it for several
            #    turns): pin to emit_response so the model can't keep
            #    re-querying. Haiku's default behavior is to keep
            #    refining tenant SQL forever without ever finalizing;
            #    pinning ensures convergence.
            #
            # NOTE — "captured" means a populated frame. An empty
            # result (e.g. lake filter returned 0 rows + diagnostic)
            # does NOT count as captured because the model needs to
            # retry with a corrected filter before finalizing.
            tenant_ready = (
                self._tenant_frame is not None
                and len(self._tenant_frame) > 0
            )
            lake_ready = (
                self._lake_frame is not None
                and len(self._lake_frame) > 0
            )
            ready_to_emit = (
                # Both frames populated OR
                (tenant_ready and lake_ready)
                # One populated + enough exploration done OR
                or (turn >= 3 and (tenant_ready or lake_ready))
                # Hard wall: last 2 turns of the budget.
                or turn >= self.MAX_TURNS - 2
            )
            tool_choice = (
                {"type": "tool", "name": "emit_response"}
                if ready_to_emit
                else {"type": "any"}
            )
            resp, tel = L.call_with_tools(
                model=self.MODEL,
                system=self._system_prompt,
                tools=self.TOOLS,
                messages=messages,
                max_tokens=MAX_TOKENS,
                tool_choice=tool_choice,
            )
            self._in_tokens  += tel.input_tokens
            self._out_tokens += tel.output_tokens
            self._cost_usd   += tel.cost_usd
            messages.append({"role": "assistant", "content": resp.content})

            if resp.stop_reason != "tool_use":
                final_text = self._extract_text(resp.content)
                return self._finalize(
                    final_text, converged=True, turns=turn + 1,
                )

            tool_results: list[dict[str, Any]] = []
            for block in resp.content:
                if getattr(block, "type", "") != "tool_use":
                    continue
                try:
                    result = self._dispatch_tool(
                        block.name, dict(block.input or {})
                    )
                    is_error = False
                except Exception as exc:               # noqa: BLE001
                    result = {"error": f"{type(exc).__name__}: {exc}"}
                    is_error = True
                # Strip the full frame before sending to the LLM
                # (the specialist already captured it in state).
                payload_for_llm = (
                    {k: v for k, v in result.items() if k != "frame"}
                    if isinstance(result, dict) else result
                )
                tool_results.append({
                    "type":        "tool_result",
                    "tool_use_id": block.id,
                    "content":     json.dumps(payload_for_llm, default=str),
                    "is_error":    is_error,
                })

            # If the model called emit_response, it has produced the
            # structured final response — terminate the loop.
            if self._emit_args is not None:
                return self._finalize_from_emit(
                    converged=True, turns=turn + 1,
                )
            messages.append({"role": "user", "content": tool_results})

        # Loop exhausted without final text.
        return self._finalize(
            "(I couldn't converge on an answer in "
            f"{self.MAX_TURNS} turns.)",
            converged=False, turns=self.MAX_TURNS,
        )

    # ---- Tool dispatch ----------------------------------------------

    def _dispatch_tool(
        self, name: str, args: dict[str, Any],
    ) -> dict[str, Any]:
        if name == "schema_info":
            return LT.schema_info()
        if name == "query_tenant":
            payload = LT.query_tenant(
                self.context.viewing_merchant_id, args["sql"],
            )
            self._tenant_frame = payload["frame"]
            self._sql_log.append({
                "surface": "tenant",
                "query":   args["sql"],
                "row_count": payload["row_count"],
            })
            return payload
        if name == "read_lake_table":
            payload = LT.read_lake_table(
                self.context.viewing_merchant_id,
                args["table"],
                args.get("filters") or {},
            )
            self._lake_frame = payload["frame"]
            self._lake_manifest = payload["manifest"]
            self._sql_log.append({
                "surface":   "lake",
                "query":     f"read_lake_table({args['table']!r}, "
                             f"filters={args.get('filters') or {}})",
                "row_count": payload["row_count"],
            })
            return payload
        if name == "emit_response":
            # Three structural preconditions enforced before the
            # emit is accepted (Wave 3 Stage 6.5 follow-up #5,
            # Fixes 1+2+5). A failure here raises ``LakeToolError``
            # which the loop's exception envelope converts to a
            # tool_result with is_error=True — the model sees the
            # diagnostic and can retry with corrected args. We do
            # NOT set ``_emit_args`` on rejection, so the loop
            # continues.
            self._validate_emit_args(args)
            self._emit_args = args
            return {"ok": True}
        raise ValueError(f"Unknown tool: {name}")

    # ---- emit_response preconditions ---------------------------------

    def _validate_emit_args(self, args: dict[str, Any]) -> None:
        """Enforce the three structural preconditions on emit_response:

        Fix 5 — **no emit before data**. At least one of
        ``query_tenant`` / ``read_lake_table`` must have returned a
        non-empty frame this session.

        Fix 1 — **merge spec must be runnable when both frames are
        present** (for specialists with ``MERGE_REQUIRED=True``). An
        empty merge spec when both frames are populated, or a merge
        spec that fails to run, surfaces as a tool error rather than
        silently falling back to the lake frame (the previous
        behavior, which produced misleading "chart_intent malformed"
        caveats downstream — the intent was fine, the merge didn't
        run).

        Fix 2 — **unit/magnitude guard**. After a successful merge,
        if ``own_value`` and ``peer_benchmark`` differ in order of
        magnitude beyond a threshold (raw $ vs unitless index, etc.),
        surface a tool error with column-shape diagnostics. The
        claim-level §1.4 validator catches misclaimed values; this
        catches the structural pairing bug upstream.

        Raises ``LakeToolError`` on any failed precondition (caught
        by the loop in ``answer()``).
        """
        from src.agents.lake_tools import LakeToolError

        tenant_ready = (
            self._tenant_frame is not None
            and len(self._tenant_frame) > 0
        )
        lake_ready = (
            self._lake_frame is not None
            and len(self._lake_frame) > 0
        )

        # --- Fix 5: no emit before data ---
        if not tenant_ready and not lake_ready:
            raise LakeToolError(
                "emit_response rejected: you must fetch data via "
                "query_tenant or read_lake_table (with a populated "
                "result) before emitting a response. An empty-result "
                "emit produces a degraded section with no data to "
                "validate prose claims against. Call schema_info if "
                "you need column names, then a data tool, then "
                "emit_response."
            )

        # --- Fix 1: merge spec must be runnable when both frames present ---
        merge_spec = args.get("merge") or {}
        if self.MERGE_REQUIRED and tenant_ready and lake_ready:
            if not merge_spec:
                raise LakeToolError(
                    "emit_response rejected: both tenant and lake "
                    "frames are populated but `merge` is empty. The "
                    "comparison frame can't be built without "
                    "merge.on (join keys present in BOTH frames), "
                    "merge.own_value_col (from the tenant result), "
                    "and merge.peer_value_col (from the lake "
                    "result). Tenant columns: "
                    f"{list(self._tenant_frame.columns)}. Lake "
                    f"columns: {list(self._lake_frame.columns)}."
                )
            try:
                test_merged = merge_own_and_peer(
                    own_df=self._tenant_frame,
                    peer_df=self._lake_frame,
                    on=list(merge_spec.get("on") or []),
                    own_value_col=merge_spec.get("own_value_col") or "",
                    peer_value_col=merge_spec.get("peer_value_col") or "",
                    gap_op=merge_spec.get("gap_op", "difference"),
                    viewer=self.context.viewing_merchant_id,
                )
            except (KeyError, MergeGrainError, ViewerScopingError) as exc:
                raise LakeToolError(
                    "emit_response rejected: merge spec failed to "
                    f"run. Reason: {type(exc).__name__}: {exc}. "
                    "Pick join keys present in BOTH frames; pick "
                    "value columns that exist in their respective "
                    "frames. Tenant columns: "
                    f"{list(self._tenant_frame.columns)}. Lake "
                    f"columns: {list(self._lake_frame.columns)}."
                ) from exc

            if len(test_merged) == 0:
                raise LakeToolError(
                    "emit_response rejected: the merge ran but "
                    "produced 0 rows. Your join keys "
                    f"({merge_spec.get('on')}) don't match between "
                    "the two frames — typically a value-domain "
                    "issue (e.g. lake period_start is Monday-of-week "
                    "but your tenant SQL produced Saturday-of-week, "
                    "or category casing differs). Check the actual "
                    "values in each frame and retry."
                )

            # --- Fix 2: magnitude / unit compatibility ---
            ok, diag = check_magnitude_compatibility(test_merged)
            if not ok:
                own_col = merge_spec.get("own_value_col")
                peer_col = merge_spec.get("peer_value_col")
                own_med = diag["own_median_abs"]
                peer_med = diag["peer_median_abs"]
                ratio = diag["ratio"]
                raise LakeToolError(
                    "emit_response rejected: merge produced a "
                    "magnitude-mismatched comparison. "
                    f"own_value_col={own_col!r} median ≈ {own_med:.4g}; "
                    f"peer_value_col={peer_col!r} median ≈ {peer_med:.4g}; "
                    f"ratio ≈ {ratio:.1f}× (threshold 100). The two "
                    "columns are not subtractable in units — one "
                    "looks like raw $/count and the other like an "
                    "index/share. Pick columns with comparable units "
                    "(e.g. own units SUM(qty) vs peer units_index "
                    "— both are 'units' shape — NOT own SUM(line_total) "
                    "vs peer revenue_index)."
                )

    # ---- Finalize ----------------------------------------------------

    def _finalize(
        self, text: str, *, converged: bool, turns: int,
    ) -> AgentResponse:
        """Parse the model's final response into an AgentResponse.

        Steps:
        1. Parse fenced render block: ``{merge, chart_intent,
           claims}``. ``RenderBlockMissingError`` if absent.
        2. Parse fenced caveats block (optional, default []).
        3. Merge tenant + lake frames per the model's merge spec.
           If only one source was queried (e.g. Advisor on a single
           lake table), use that frame as ``result`` directly.
        4. Build the chart from the chart_intent + result.
        5. Validate prose against claims + result; ``cleaned_prose``
           replaces the raw text in the response.
        """
        render = LT.parse_render_block(text)
        caveats = LT.parse_caveats_block(text)
        prose = LT.strip_render_and_caveats_blocks(text)

        if render is None:
            # Loop-exhaustion case (Wave 3 Stage 6.5 follow-up #5):
            # the model never produced a valid emit_response within
            # MAX_TURNS (typically because the structural
            # preconditions kept rejecting its merge spec). Produce
            # a minimal response with the synthetic text + an
            # "unconverged" caveat rather than raising a hard error
            # — the user still sees what the agent attempted, plus
            # any data it did manage to fetch.
            if not converged:
                exhausted_caveats = list(caveats) + [
                    "(Agent did not converge within the turn budget — "
                    "the merge/emit preconditions kept rejecting "
                    "the model's args. See SQL surfaces and grain "
                    "notes for what was attempted.)"
                ]
                return self._minimal_response(
                    prose=prose or "(no prose produced)",
                    caveats=exhausted_caveats,
                    converged=False,
                    turns=turns,
                )
            if self.MERGE_REQUIRED:
                raise RenderBlockMissingError(
                    "Model's final response did not include a parseable "
                    "`render` fenced block. Expected JSON with keys "
                    "{merge, chart_intent, claims}."
                )
            # Soft case: produce a minimal response with just prose.
            return self._minimal_response(
                prose=prose, caveats=caveats, converged=converged,
                turns=turns,
            )

        result = self._build_result(render.get("merge") or {})
        chart_intent = render.get("chart_intent") or {}
        claims = self._parse_claims(render.get("claims") or [])

        try:
            chart = build_chart(chart_intent, result)
        except (MissingColumnError, UnsupportedIntentError) as exc:
            raise RenderBlockInvalidError(
                f"chart_intent failed to build: {exc}"
            ) from exc

        report = validate_claims(prose, claims, result)

        return AgentResponse(
            result=result,
            chart_intent=chart_intent,
            chart=chart,
            prose=report.prose,
            claims=claims,
            caveats=caveats,
            sql=[
                SqlSurface(
                    surface=s["surface"], query=s["query"],
                    row_count=s["row_count"],
                ) for s in self._sql_log
            ],
            grain_notes=list((self._lake_manifest or {}).get("excludes", [])),
            telemetry=Telemetry(
                model=self.MODEL,
                input_tokens=self._in_tokens,
                output_tokens=self._out_tokens,
                cost_usd=self._cost_usd,
                turns=turns,
                converged=converged,
            ),
        )

    def _build_result(self, merge_spec: dict[str, Any]) -> pd.DataFrame:
        """Run ``merge_own_and_peer`` per the model's merge spec, or
        return one of the captured frames if no merge applies. When
        the model emits a render block without fetching any data, an
        empty result is returned (caller will surface a "no data
        fetched" caveat instead of hard-failing)."""
        own, peer = self._tenant_frame, self._lake_frame
        if own is None and peer is None:
            # The model jumped straight to emit_response without
            # gathering data — surface an empty frame and let
            # _finalize_from_emit append a caveat. Better than
            # raising, which produced a "Failed: ..." section in
            # the preview with no inspectable state.
            return pd.DataFrame()
        if own is not None and peer is None:
            return own.copy()
        if own is None and peer is not None:
            return peer.copy()
        # Both present — merge if the spec is non-empty, otherwise
        # fall back to the lake frame (the more common case when a
        # specialist queried both but the model didn't author a
        # merge).
        if not merge_spec:
            return peer.copy()
        try:
            return merge_own_and_peer(
                own_df=own,
                peer_df=peer,
                on=list(merge_spec.get("on") or []),
                own_value_col=merge_spec["own_value_col"],
                peer_value_col=merge_spec["peer_value_col"],
                gap_op=merge_spec.get("gap_op", "difference"),
                viewer=self.context.viewing_merchant_id,
            )
        except (KeyError, MergeGrainError, ViewerScopingError) as exc:
            # Same graceful-degradation rationale: surface what we
            # have rather than hard-failing the response.
            return peer.copy()

    @staticmethod
    def _parse_claims(claims_json: list[Any]) -> list[Claim]:
        """Parse the model's JSON list of claims into typed Claim
        objects. Tolerates extra keys; raises on missing required keys."""
        out: list[Claim] = []
        for c in claims_json:
            if not isinstance(c, dict):
                continue
            try:
                source = _parse_claim_source(c.get("source") or {})
                out.append(Claim(
                    text_span=str(c["text_span"]),
                    value=float(c["value"]),
                    source=source,
                ))
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def _finalize_from_emit(
        self, *, converged: bool, turns: int,
    ) -> AgentResponse:
        """Build the AgentResponse from the args the model passed to
        the ``emit_response`` tool. This is the Wave 3 normal exit
        path; the legacy fenced-block parser in ``_finalize`` is the
        fallback for soft cases (Advisor) that don't require
        emit_response."""
        args = self._emit_args or {}
        merge_spec = args.get("merge") or {}
        chart_intent = args.get("chart_intent") or {}
        claims_raw = args.get("claims") or []
        prose = (args.get("prose") or "").strip()
        caveats = list(args.get("caveats") or [])

        result = self._build_result(merge_spec)
        claims = self._parse_claims(claims_raw)

        # Strip stray Anthropic tool-use XML markers the model
        # sometimes double-encodes inside the `prose` string field
        # (Wave 3 Stage 6.5 follow-up #5 — A3 batch-7 emission blob).
        prose = LT.sanitize_prose(prose)

        # Build the chart gracefully — if chart_intent is malformed
        # (model omitted per-kind required fields, named a column
        # that isn't in the result, etc.), set chart=None and surface
        # the reason in caveats. The prose + claims + result still
        # get the §1.4 treatment and reach the user. This trades a
        # missing chart for a delivered prose answer; the alternative
        # was raising and emitting nothing.
        chart = None
        chart_error: str | None = None
        try:
            chart = build_chart(chart_intent, result)
        except (MissingColumnError, UnsupportedIntentError, KeyError) as exc:
            chart_error = f"{type(exc).__name__}: {exc}"
        except Exception as exc:                          # noqa: BLE001
            # Unexpected error from a per-kind builder (NaN values in
            # a Plotly indicator, etc.); same fallback path.
            chart_error = f"chart build raised: {type(exc).__name__}: {exc}"

        report = validate_claims(prose, claims, result)

        effective_caveats = list(caveats)
        if chart_error:
            effective_caveats.append(
                f"(Chart skipped — model's chart_intent was malformed. "
                f"Reason: {chart_error})"
            )
        if len(result) == 0:
            effective_caveats.append(
                "(No data was fetched before emit_response — model "
                "skipped query_tenant / read_lake_table. Result frame "
                "is empty.)"
            )

        return AgentResponse(
            result=result,
            chart_intent=chart_intent,
            chart=chart,
            prose=report.prose,
            claims=claims,
            caveats=effective_caveats,
            sql=[
                SqlSurface(
                    surface=s["surface"], query=s["query"],
                    row_count=s["row_count"],
                ) for s in self._sql_log
            ],
            grain_notes=list(
                (self._lake_manifest or {}).get("excludes", []),
            ),
            telemetry=Telemetry(
                model=self.MODEL,
                input_tokens=self._in_tokens,
                output_tokens=self._out_tokens,
                cost_usd=self._cost_usd,
                turns=turns,
                converged=converged,
            ),
        )

    def _minimal_response(
        self, *, prose: str, caveats: list[str], converged: bool, turns: int,
    ) -> AgentResponse:
        """Build an AgentResponse for the soft case (no merge required,
        no render block). Result = whichever frame was captured;
        chart_intent + claims empty. Used by Advisor on simple
        single-table answers, never by specialists with merge-required."""
        result = (
            self._tenant_frame if self._tenant_frame is not None
            else (self._lake_frame
                  if self._lake_frame is not None
                  else pd.DataFrame())
        )
        return AgentResponse(
            result=result,
            chart_intent={},
            chart=None,
            prose=prose,
            claims=[],
            caveats=caveats,
            sql=[
                SqlSurface(
                    surface=s["surface"], query=s["query"],
                    row_count=s["row_count"],
                ) for s in self._sql_log
            ],
            grain_notes=list((self._lake_manifest or {}).get("excludes", [])),
            telemetry=Telemetry(
                model=self.MODEL,
                input_tokens=self._in_tokens,
                output_tokens=self._out_tokens,
                cost_usd=self._cost_usd,
                turns=turns,
                converged=converged,
            ),
        )

    # ---- Helpers -----------------------------------------------------

    def _reset_state(self) -> None:
        self._sql_log = []
        self._tenant_frame = None
        self._lake_frame = None
        self._lake_manifest = None
        self._emit_args = None
        self._in_tokens = 0
        self._out_tokens = 0
        self._cost_usd = 0.0

    @staticmethod
    def _extract_text(content_blocks: list[Any]) -> str:
        return "".join(
            b.text for b in content_blocks
            if getattr(b, "type", "") == "text"
        )


def _parse_claim_source(src: dict[str, Any]) -> CellLookup | Derivation:
    """Parse a claim source from JSON. Supported shapes:

    * ``{"type": "CellLookup", "row_filter": {...}, "column": "..."}``
    * ``{"type": "Derivation", "op": "...", "operands": [...]
         [, "agg": "..."]}``

    ``Derivation.operands`` is a list of CellLookup-shaped dicts.
    """
    kind = src.get("type")
    if kind == "CellLookup":
        return CellLookup(
            row_filter=dict(src.get("row_filter") or {}),
            column=str(src["column"]),
            agg=src.get("agg"),
        )
    if kind == "Derivation":
        operands = [
            CellLookup(
                row_filter=dict(o.get("row_filter") or {}),
                column=str(o["column"]),
                agg=o.get("agg"),
            )
            for o in (src.get("operands") or [])
            if isinstance(o, dict)
        ]
        return Derivation(
            op=src["op"],
            operands=operands,
            agg=src.get("agg"),
        )
    raise ValueError(f"Unknown claim source type {kind!r}")
