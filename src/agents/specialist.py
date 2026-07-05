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
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from src.agents import constants
from src.agents import label_review as LR
from src.agents import lake_tools as LT
from src.agents.lake_tools import LakeToolError
from src.agents import llm as L
from src.agents.chart_build import (
    MissingColumnError,
    NonNumericChartColumnError,
    UnsupportedIntentError,
    build_chart,
)
from src.agents.claims import (
    CellLookup,
    Claim,
    Derivation,
    ValueRef,
    _apply_row_filter,
    validate_claims,
    validate_structured_response,
)
from src.agents.context import MerchantContext
from src.agents.response import (
    AgentResponse,
    MergeGrainError,
    SqlSurface,
    Telemetry,
    ViewerScopingError,
    merge_own_and_peer,
)


DEFAULT_MAX_TURNS = 6
MAX_TOKENS = 4096

# Wave 3 Stage 7 trim — only the wall-clock ceiling remains as a
# runtime bound. The earlier MAX_PRECONDITION_REJECTIONS retry-cap
# floor is retired: auto-invoke build_merge (Fix 10a) handles the
# main rejection case (missing build_merge), so the precondition
# now rarely fires; when it does (Fix 9c multi-row CellLookup
# without agg), the model retries within MAX_TURNS=6 and the
# wall-clock ceiling catches any genuine runaway.
WALL_CLOCK_CEILING_SEC = 120.0  # +30s headroom for the drill-down extra query/turn


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

    # Wave 3 Stage 6.5 Fix 12 — semantic peer_value_col preference
    # for auto-invoke build_merge. The original Fix 10a picked the
    # FIRST metric in the manifest's metrics list (txn_count for
    # lake_category_metrics), making the merged frame's
    # ``peer_benchmark`` column structurally meaningless for the
    # specialist's question (a pricing chart axis labeled
    # "peer_benchmark" plotting txn counts). Subclasses set this
    # to the semantically-right peer column for their domain;
    # falls back to the first-available-metric heuristic when
    # the preferred column is not in the lake frame.
    PREFERRED_PEER_METRIC: str | None = None

    # Wave 3.5 §6 — which peer-availability rule applies when the
    # viewer has zero same-segment peers. Subclasses set one of:
    #   "pricing"     → decline peer comparison; own-data only, no
    #                   cross-segment substitution.
    #   "comparative" → demand / trade-area / trends: fall back to the
    #                   broader merchant set, clearly labeled.
    #   "anomaly"     → cross-segment baseline labeled, or own-trend.
    #   "advisor"     → free-form: route to a specialist's rule, else
    #                   default to cross-segment labeled.
    # The directive text is computed structurally from this kind +
    # ``context.segment_peer_count`` and injected at {{peer_routing}}.
    PEER_ROUTING_KIND: str = "comparative"

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
        # Wave 3 Stage 6.5 follow-up #6 — wall-clock bound only after
        # Stage 7's retire-cap-floor trim.
        self._answer_started_at: float = 0.0

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
               .replace("{{peer_routing}}", self._peer_routing_directive())
               .replace("{{kvi_subcategories}}", constants.KVI_SUBCATEGORIES_PROMPT)
        )

    def _peer_routing_directive(self) -> str:
        """Compute the Wave 3.5 §6 peer-availability directive from this
        specialist's ``PEER_ROUTING_KIND`` + the viewer's
        ``segment_peer_count``. Structural — derived from metadata, not
        a rule the model must remember."""
        n = self.context.segment_peer_count
        seg = self.context.viewing_merchant_segment
        if n >= 1:
            plural = "peer" if n == 1 else "peers"
            return (
                f"**Peer availability:** you have **{n} same-segment "
                f"{plural}** ({seg}). Peer comparisons use the "
                f"`peer_relationship = 'peer'` rows from `query_lake_sql` "
                f"— your same-segment competitors, in real dollars."
            )
        # Zero same-segment peers (e.g. the only QSR / off-price banner).
        if self.PEER_ROUTING_KIND == "pricing":
            return (
                f"**Peer availability — NONE.** You are the only "
                f"`{seg}` merchant in the panel, so there are **no "
                f"comparable same-segment peers**. A price comparison "
                f"against a different segment is apples-to-oranges and "
                f"is **not allowed**. Answer from your OWN data "
                f"(`query_tenant`) only and state plainly that no "
                f"comparable peers are available. Do NOT pull "
                f"`peer_relationship = 'merchant'` (cross-segment) rows "
                f"as a price benchmark."
            )
        if self.PEER_ROUTING_KIND == "anomaly":
            return (
                f"**Peer availability — no same-segment peers.** You are "
                f"the only `{seg}` merchant. Use a cross-segment baseline "
                f"(`peer_relationship = 'merchant'`), clearly labeled as "
                f"the broader merchant set; or fall back to your own "
                f"trend if a cross-segment baseline is not meaningful."
            )
        # comparative (demand / trade-area / trends) + advisor default.
        return (
            f"**Peer availability — no same-segment peers.** You are the "
            f"only `{seg}` merchant, so a same-segment comparison isn't "
            f"possible. **Compare against the broader merchant set "
            f"(`peer_relationship = 'merchant'` from `query_lake_sql`) by "
            f"default** — run the peer query and report the comparison, and "
            f"you MUST label it clearly as *the broader merchant set, not "
            f"same-segment peers.* Fall back to your own data only if a "
            f"cross-segment comparison is genuinely meaningless for this "
            f"question — do NOT skip the peer query just because the peers "
            f"are a different segment."
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
            # Wall-clock ceiling — hard exit to graceful degradation.
            elapsed = time.monotonic() - self._answer_started_at
            if elapsed > WALL_CLOCK_CEILING_SEC:
                return self._minimal_response(
                    prose=LT.business_fallback(),
                    caveats=[
                        f"Per-question wall-clock ceiling reached ("
                        f"{WALL_CLOCK_CEILING_SEC:.0f}s); the agent "
                        f"returned the fetched data without converging."
                    ],
                    converged=False, turns=turn,
                )
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
            LT.business_fallback(),
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
        if name == "query_lake_sql":
            payload = LT.query_lake_sql(
                self.context.viewing_merchant_id, args["sql"],
            )
            # Register as the "lake" frame so ValueRef (frame="lake"
            # default) and CellLookup resolve against it unchanged
            # (Wave 3.5 §10.1 / Finding 9).
            self._lake_frame = payload["frame"]
            self._sql_log.append({
                "surface":   "lake_sql",
                "query":     args["sql"],
                "row_count": payload["row_count"],
                "suppressed": payload.get("suppressed", 0),
            })
            return payload
        if name == "top_movers":
            # Server-side week-over-week reducer (Phase 4). Runs the weekly
            # SQL through the guarded tenant/lake path and returns the top
            # movers as the frame — captured as the tenant or lake frame per
            # `source` so emitted claims resolve against it.
            payload = LT.top_movers(
                self.context.viewing_merchant_id,
                args["sql"],
                source=args["source"],
                dim_col=args["dim_col"],
                week_col=args["week_col"],
                value_col=args["value_col"],
                count_col=args.get("count_col"),
                top_n=int(args.get("top_n", 3)),
            )
            if args["source"] == "tenant":
                self._tenant_frame = payload["frame"]
            else:
                self._lake_frame = payload["frame"]
            self._sql_log.append({
                "surface":   f"top_movers_{args['source']}",
                "query":     args["sql"],
                "row_count": payload["row_count"],
                "suppressed": payload.get("suppressed", 0),
            })
            return payload
        if name == "emit_response":
            # Structural preconditions: data-before-emit (Fix 5), a
            # non-empty headline (Wave 3.5), and multi-row CellLookup
            # needs agg (Fix 9c). The model retries within MAX_TURNS;
            # the wall-clock ceiling catches a runaway.
            self._validate_emit_args(args)
            self._emit_args = args
            return {"ok": True}
        raise ValueError(f"Unknown tool: {name}")

    @staticmethod
    def _count_filter_matches(
        df: pd.DataFrame, row_filter: dict[str, Any],
    ) -> int | None:
        """Count rows in ``df`` matching ``row_filter``. Returns None
        if a filter column is missing (the validator will tier-3
        strip downstream, which is correct). Used by the multi-row
        CellLookup precondition (Fix 9c).

        Wave 3 Stage 6.5 Fix 11b: list-valued row_filter values are
        treated as ``.isin(v)`` (OR clause). The gate now counts
        list-valued multi-row filters correctly instead of crashing
        on ``Series == list`` and silently returning None. The
        helper and the validator's ``CellLookup._resolve_in`` use
        the same ``_apply_row_filter`` so they cannot diverge."""
        if df is None or len(df) == 0:
            return 0
        try:
            for k in row_filter:
                if k not in df.columns:
                    return None
            sub = _apply_row_filter(df, row_filter)
            return len(sub)
        except (LookupError, ValueError, TypeError):
            return None

    def _resolve_target_frame(
        self, frame_name: str | None,
    ) -> pd.DataFrame | None:
        """Pick the captured frame for a given ``frame`` field value.
        Used by the multi-row CellLookup precondition (Fix 9c) and by
        ``_finalize_from_emit`` to assemble the two-frame validator
        kwargs."""
        if frame_name == "tenant":
            return self._tenant_frame
        if frame_name == "lake":
            return self._lake_frame
        # Default: tenant if present, else lake.
        if self._tenant_frame is not None and len(self._tenant_frame) > 0:
            return self._tenant_frame
        return self._lake_frame

    # ---- emit_response preconditions ---------------------------------

    def _validate_emit_args(self, args: dict[str, Any]) -> None:
        """Enforce the structural preconditions on emit_response.

        Wave 3 Stage 6.5 Fix 9 reshape — the merge spec is no longer
        carried by ``emit_response``; it lives in the prior
        ``build_merge`` tool call. The remaining preconditions:

        * **No emit before data** (Fix 5). At least one of
          ``query_tenant`` / ``read_lake_table`` produced a
          non-empty frame.
        * **Merge must have been attempted when both frames are
          populated** (Fix 9a). The model must have called
          ``build_merge`` before ``emit_response`` so chart_intent
          and claims author against the REAL merged columns.
        * **Multi-row CellLookup requires `agg`** (Fix 9c). A claim
          whose ``row_filter`` matches multiple rows but omits
          ``agg`` is the most common cause of all-stripped
          cascades; surface a legible error so the model adds
          ``agg="sum"`` / ``agg="mean"``.

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

        # --- Wave 3.5: a structured answer needs a lead. ---
        if not (args.get("headline") or "").strip():
            raise LakeToolError(
                "emit_response rejected: `headline` is required — one "
                "sentence stating the finding that answers the question. "
                "Put supporting numbers in `evidence` and the action in "
                "`so_what`. Do not emit an empty or question-only headline."
            )

        # --- Phase 1.1: narration guard. The emitted answer must be a finished
        # statement, never mid-loop scratchpad ("let me pull…", "retrying…",
        # trailing "…"). Reject so the model re-emits a clean answer; if it can't
        # within the turn budget, the loop falls back to a canonical string. ---
        narration_fields = [args.get("headline") or ""] + list(args.get("evidence") or [])
        if any(LT.is_narration(f) for f in narration_fields):
            raise LakeToolError(
                "emit_response rejected: the `headline`/`evidence` reads as "
                "in-progress narration (e.g. 'let me…', 'retrying…', 'result "
                "was truncated…', or a trailing '…'). Emit a finished answer: a "
                "declarative headline and evidence bullets that state findings, "
                "with no mention of what you are about to do."
            )

        # --- Fix 9c: multi-row CellLookup without agg ---
        for claim in (args.get("claims") or []):
            if not isinstance(claim, dict):
                continue
            src = claim.get("source") or {}
            if src.get("type") != "CellLookup":
                continue
            if src.get("agg"):
                continue
            target_frame = self._resolve_target_frame(src.get("frame"))
            if target_frame is None or len(target_frame) == 0:
                continue
            matched = self._count_filter_matches(
                target_frame, src.get("row_filter") or {},
            )
            if matched is None or matched <= 1:
                continue
            text_span = str(claim.get("text_span", ""))[:80]
            raise LakeToolError(
                f"Claim '{text_span}': CellLookup "
                f"row_filter={src.get('row_filter')} on column "
                f"{src.get('column')!r} matches {matched} rows; you "
                f"MUST specify agg='sum' or agg='mean' to aggregate. "
                f"A naked CellLookup without agg resolves to the "
                f"first matching row only — your stated total/average "
                f"will not match and the validator will strip the "
                f"clause. Add \"agg\": \"sum\" (or \"mean\") to this "
                f"claim's source object and retry."
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
                    "Agent didn't converge within the turn budget; "
                    "see SQL surfaces and grain notes for what was "
                    "fetched.",
                ]
                return self._minimal_response(
                    prose=prose or LT.business_fallback(),
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

        # Charts held for Wave 4 (§11.2) — gated off here too. The
        # legacy fenced-render path is not exercised by the 3.5
        # two-query flow, but keep it consistent.
        chart = None
        if LT.CHARTS_ENABLED:
            try:
                chart = build_chart(chart_intent, result)
            except (MissingColumnError, UnsupportedIntentError) as exc:
                raise RenderBlockInvalidError(
                    f"chart_intent failed to build: {exc}"
                ) from exc

        report = validate_claims(prose, claims, result)

        # Legacy single-string path: the whole validated prose becomes
        # the headline. Phase 1.1 — the claims validator grounds numbers but
        # does not strip narration prose, so this text-stop branch could still
        # leak "let me pull the peer data" etc.; run it through sanitize_prose.
        headline = LT.sanitize_prose(report.prose)
        return AgentResponse(
            result=result,
            chart_intent=chart_intent,
            chart=chart,
            headline=headline,
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
        # fall back to a frame that CARRIES BOTH SIDES' columns so
        # the chart reconciler has something to remap own-side
        # references against (Wave 3 Stage 6.5 follow-up #7 — Fix 2).
        # The previous fallback (peer.copy()) silently dropped the
        # own side; chart_intent then named columns from a frame the
        # chart builder couldn't see, and every chart got skipped
        # with MissingColumnError. Carry both sides + flag the frame
        # so _finalize_from_emit can add a "merge spec missing /
        # failed" caveat.
        # Wave 3 Stage 7 trim: removed the _fallback_carry_both_sides
        # broadcast (own-scalar repeated down peer rows) that caused
        # the original all-stripped cascade pre-Fix-7. The dual-frame
        # routing on the emit_response path (Fix 9b + Fix 10a auto-
        # invoke) handles real merge failures via _merge_fail_payload;
        # this legacy _build_result path is only reachable when the
        # model emits a text turn with a fenced render block (rare
        # with Fix 9+). On failure, return own.copy() and flag
        # merge_incomplete so the caveat fires.
        if not merge_spec:
            fallback = own.copy()
            fallback.attrs["merge_incomplete"] = True
            fallback.attrs["merge_incomplete_reason"] = "empty merge spec"
            return fallback
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
            fallback = own.copy()
            fallback.attrs["merge_incomplete"] = True
            fallback.attrs["merge_incomplete_reason"] = (
                f"{type(exc).__name__}: {exc}"
            )
            return fallback

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
        the ``emit_response`` tool.

        Wave 3 Stage 6.5 Fix 9 reshape: the merge spec is no longer
        in emit_args (it ran in ``build_merge``). The result-of-record
        is:

        * ``self._merged_frame`` — clean merge path. Claims resolve
          against this single frame.
        * Dual frames (``self._merge_fail_payload``) — merge-fail
          path. Claims set ``source.frame`` to ``"tenant"`` or
          ``"lake"``; the validator receives a ``frames`` dict and
          each claim resolves against the named real frame.
        * Single-source paths (advisor on lake-only, tenant-only
          questions) — fall through to whichever frame was fetched.
        """
        args = self._emit_args or {}
        chart_intent = args.get("chart_intent") or {}
        claims_raw = args.get("claims") or []
        headline = (args.get("headline") or "").strip()
        evidence = [
            str(e).strip()
            for e in (args.get("evidence") or [])
            if str(e).strip()
        ]
        so_what = (args.get("so_what") or "").strip() or None
        caveats = list(args.get("caveats") or [])

        # Wave 3.5 — result-of-record + frames dict for the validator.
        # The two-query flow carries own (tenant) and peer (lake) as
        # SEPARATE frames; each claim resolves against its own ``frame``
        # field ("tenant" / "lake") via the frames dict, and an untagged
        # claim walks result → tenant → lake. The display
        # result-of-record is the tenant frame when present, else lake.
        frames: dict[str, pd.DataFrame] = {}
        if self._tenant_frame is not None and len(self._tenant_frame) > 0:
            frames["tenant"] = self._tenant_frame
        if self._lake_frame is not None and len(self._lake_frame) > 0:
            frames["lake"] = self._lake_frame

        if self._tenant_frame is not None and len(self._tenant_frame) > 0:
            result = self._tenant_frame
        elif self._lake_frame is not None and len(self._lake_frame) > 0:
            result = self._lake_frame
        else:
            result = pd.DataFrame()

        claims = self._parse_claims(claims_raw)

        # Strip stray Anthropic tool-use XML markers; route narration
        # leaks (including a punt-with-a-question) through the single
        # business-fallback function (Fix 9e). Per field — a rambling
        # bullet collapses to the fallback for that bullet only.
        headline = LT.sanitize_prose(headline)
        evidence = [LT.sanitize_prose(e) for e in evidence]
        so_what = LT.sanitize_prose(so_what) if so_what else None
        # Drop evidence bullets that emptied out or collapsed to the
        # canonical fallback (the fallback sentence belongs in the
        # headline degenerate path, not as a bullet).
        _fallback = LT.business_fallback()
        evidence = [e for e in evidence if e and e != _fallback]
        if so_what == _fallback:
            so_what = None

        # Wave 3.5 §2 decision 6 / §11.2 — charts are held for Wave 4.
        # The build path is kept but gated behind ``CHARTS_ENABLED``
        # (False in 3.5); the model can no longer author a chart_intent
        # (removed from the emit schema), so this branch is dead now and
        # retained only for the Wave 4 flip.
        chart = None
        chart_error: str | None = None
        if LT.CHARTS_ENABLED and chart_intent and chart_intent.get("kind"):
            try:
                chart = build_chart(chart_intent, result)
            except (MissingColumnError, UnsupportedIntentError,
                    NonNumericChartColumnError, KeyError) as exc:
                chart_error = f"{type(exc).__name__}: {exc}"
            except Exception as exc:                      # noqa: BLE001
                # Unexpected error from a per-kind builder (NaN values in
                # a Plotly indicator, etc.); same fallback path.
                chart_error = f"chart build raised: {type(exc).__name__}: {exc}"

        # Validator runs against ``result`` for Pass B; Pass A's
        # per-claim resolve honors each claim's ``frame`` field via the
        # ``frames`` dict. Runs over each structured field independently
        # (§1.4 guarantee preserved field by field).
        report = validate_structured_response(
            headline=headline, evidence=evidence, so_what=so_what,
            claims=claims, result=result, frames=frames,
        )

        # Wave 3.5 — structured degenerate handling. The old prose-from-
        # claims fragment backfill (which joined claim text_spans into a
        # disjointed run-on) is gone: with the structured contract there
        # is no single string to collapse, and each evidence bullet
        # stands alone. We only guarantee a non-empty lead.
        headline = report.headline
        evidence = report.evidence
        so_what = report.so_what
        if not headline.strip() and evidence:
            # Promote the first surviving evidence point to the lead.
            headline = evidence[0]
            evidence = evidence[1:]
        if not headline.strip():
            # Nothing survived validation. Non-answer-with-data check: if both
            # frames actually returned rows, do NOT emit the generic punt —
            # state a specific honest reason naming what was fetched.
            specific = LR.nonanswer_reason(self._tenant_frame, self._lake_frame)
            headline = specific if specific else LT.business_fallback()
            evidence = []
            so_what = None

        effective_caveats = list(caveats)
        if len(result) == 0:
            effective_caveats.append(
                "(No data was fetched before emit_response — model "
                "skipped query_tenant / query_lake_sql. Result frame "
                "is empty.)"
            )

        # Post-validation label-review layer (§ label checks): repair the labels
        # around the already-grounded numbers — direction words, share format,
        # display rounding, leaked register — and lint day-of-week SQL. It never
        # re-queries and never introduces a new number.
        headline, evidence, so_what, corrections = LR.review_prose(
            headline, evidence, so_what, claims,
        )
        effective_caveats.extend(
            LR.dayofweek_lint(self._sql_log, self.context.viewing_merchant_id)
        )

        return AgentResponse(
            result=result,
            chart_intent=chart_intent,
            chart=chart,
            headline=headline,
            evidence=evidence,
            so_what=so_what,
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
            claim_dispositions=[
                {
                    "text_span": d.claim.text_span,
                    "value": d.claim.value,
                    "status": d.status,
                    "true_value": d.true_value,
                    "reason": d.reason,
                }
                for d in report.claim_dispositions
            ],
            corrections=corrections,
        )

    def _minimal_response(
        self, *, prose: str, caveats: list[str], converged: bool, turns: int,
    ) -> AgentResponse:
        """Build an AgentResponse for the soft case (no merge required,
        no render block). Result = whichever frame was captured;
        chart_intent + claims empty. Used by Advisor on simple
        single-table answers, never by specialists with merge-required.

        The ``prose`` parameter (a single string) maps to the structured
        contract's ``headline`` — minimal answers are headline-only."""
        result = (
            self._tenant_frame if self._tenant_frame is not None
            else (self._lake_frame
                  if self._lake_frame is not None
                  else pd.DataFrame())
        )
        # Phase 1.1 — this is a leak sink for the non-emit text-stop exit: the
        # `prose` here can be raw model text. Route it through the same narration
        # sanitizer the emit path uses, so scratchpad → canonical fallback, never
        # the user's screen.
        prose = LT.sanitize_prose(prose)
        # Non-answer-with-data: if this is the generic fallback but both frames
        # actually returned rows, state a specific reason instead of the punt.
        if prose == LT.business_fallback():
            specific = LR.nonanswer_reason(self._tenant_frame, self._lake_frame)
            if specific:
                prose = specific
        return AgentResponse(
            result=result,
            chart_intent={},
            chart=None,
            headline=prose,
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
        self._answer_started_at = time.monotonic()

    @staticmethod
    def _extract_text(content_blocks: list[Any]) -> str:
        return "".join(
            b.text for b in content_blocks
            if getattr(b, "type", "") == "text"
        )


def _parse_claim_source(
    src: dict[str, Any],
) -> CellLookup | Derivation | ValueRef:
    """Parse a claim source from JSON. Supported shapes:

    * ``{"type": "CellLookup", "row_filter": {...}, "column": "..."}``
    * ``{"type": "Derivation", "op": "...", "operands": [...]
         [, "agg": "..."]}``
    * ``{"type": "ValueRef", "by": "<dim>", "value": "<dim_value>",
         "metric": "<metric_col>" [, "agg": "...", "frame": "..."]}``

    ``Derivation.operands`` is a list of CellLookup-shaped dicts.
    ``ValueRef`` (Wave 3 Stage 6.5 Fix 13a) is the address shape —
    the model names a (dimension, value, metric) triple instead of
    typing a number; the server substitutes the exact float from
    the same aggregation path that produced the surfaced
    ``aggregates`` block.
    """
    kind = src.get("type")
    if kind == "CellLookup":
        return CellLookup(
            row_filter=dict(src.get("row_filter") or {}),
            column=str(src["column"]),
            agg=src.get("agg"),
            frame=src.get("frame"),
        )
    if kind == "Derivation":
        operands = [
            CellLookup(
                row_filter=dict(o.get("row_filter") or {}),
                column=str(o["column"]),
                agg=o.get("agg"),
                frame=o.get("frame"),
            )
            for o in (src.get("operands") or [])
            if isinstance(o, dict)
        ]
        return Derivation(
            op=src["op"],
            operands=operands,
            agg=src.get("agg"),
        )
    if kind == "ValueRef":
        return ValueRef(
            by=str(src["by"]),
            value=src["value"],
            metric=str(src["metric"]),
            agg=src.get("agg") or "mean",
            frame=src.get("frame") or "lake",
        )
    raise ValueError(f"Unknown claim source type {kind!r}")
