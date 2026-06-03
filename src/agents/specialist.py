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


DEFAULT_MAX_TURNS = 6
MAX_TOKENS = 4096

# Wave 3 Stage 7 trim — only the wall-clock ceiling remains as a
# runtime bound. The earlier MAX_PRECONDITION_REJECTIONS retry-cap
# floor is retired: auto-invoke build_merge (Fix 10a) handles the
# main rejection case (missing build_merge), so the precondition
# now rarely fires; when it does (Fix 9c multi-row CellLookup
# without agg), the model retries within MAX_TURNS=6 and the
# wall-clock ceiling catches any genuine runaway.
WALL_CLOCK_CEILING_SEC = 90.0


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
        # Wave 3 Stage 6.5 Fix 9 — merge becomes its own tool turn.
        # ``_merged_frame`` is the result-of-record after a clean
        # build_merge. ``_merge_fail_payload`` carries both real
        # frames when the merge fails — claims author against named
        # frames in that path.
        self._merged_frame: pd.DataFrame | None = None
        self._merge_fail_payload: tuple[pd.DataFrame, pd.DataFrame] | None = None
        self._merge_attempted: bool = False
        # Wave 3.5 — the lake frame came from `query_lake_sql` (the new
        # two-query flow) rather than `read_lake_table`. In that flow
        # there is no merge: the agent runs query_tenant + query_lake_sql
        # and compares; claims resolve per-frame. This flag routes emit
        # around the build_merge auto-invoke + the Fix 9a merge gate.
        self._lake_from_sql: bool = False

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
            f"possible. You MAY compare against the broader merchant set "
            f"(`peer_relationship = 'merchant'` from `query_lake_sql`), "
            f"but you MUST label it clearly as *the broader merchant "
            f"set, not same-segment peers.*"
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
            self._lake_from_sql = True
            self._lake_manifest = None
            self._sql_log.append({
                "surface":   "lake_sql",
                "query":     args["sql"],
                "row_count": payload["row_count"],
                "suppressed": payload.get("suppressed", 0),
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
        if name == "build_merge":
            return self._dispatch_build_merge(args)
        if name == "emit_response":
            # Wave 3 Stage 6.5 Fix 10a — auto-invoke build_merge
            # server-side when the model has fetched both frames but
            # skipped the explicit build_merge tool call. The Fix 9a
            # precondition still fires AS the gate, but auto-invoke
            # ensures the merge has actually been ATTEMPTED before
            # the gate runs (so it doesn't reject), AND that the
            # merged frame OR the dual-frame payload is set so
            # _finalize_from_emit reaches the peer data. Without
            # this, Haiku skips build_merge, gets rejected 3×,
            # force-accepts, and _finalize_from_emit silently picks
            # tenant-only — dropping the lake side.
            if (
                self.MERGE_REQUIRED
                and not self._lake_from_sql  # Wave 3.5 two-query flow: no merge
                and self._tenant_frame is not None
                and len(self._tenant_frame) > 0
                and self._lake_frame is not None
                and len(self._lake_frame) > 0
                and self._merged_frame is None
                and self._merge_fail_payload is None
                and not self._merge_attempted
            ):
                self._auto_invoke_build_merge()
            # Structural preconditions (Wave 3 Stage 6.5 follow-ups
            # #5 / #6 / #9). After Fix 10a, the auto-invoke runs
            # first; the precondition mainly catches genuinely
            # broken authoring (multi-row CellLookup without agg —
            # Fix 9c).
            # Wave 3 Stage 7 trim: precondition raises are no longer
            # caught with a retry-cap floor; the model retries within
            # MAX_TURNS=6 and wall-clock ceiling catches runaway.
            self._validate_emit_args(args)
            self._emit_args = args
            return {"ok": True}
        raise ValueError(f"Unknown tool: {name}")

    # ---- build_merge -------------------------------------------------

    def _dispatch_build_merge(
        self, args: dict[str, Any],
    ) -> dict[str, Any]:
        """Run ``merge_own_and_peer`` server-side against the captured
        tenant + lake frames and return the REAL merged frame's
        columns/dtypes/preview to the model (Wave 3 Stage 6.5 Fix 9a).

        On success: stores the merged frame as ``self._merged_frame``
        (the result-of-record); subsequent ``emit_response`` authors
        against these real names.

        On failure (mismatched/missing join keys, grain
        incompatibility): returns ``merge_failed=True`` with BOTH
        real frames unmerged (Fix 9b) — does NOT broadcast a scalar
        across rows (that was the cause of the all-stripped
        cascade). The model then authors side-by-side prose + claims
        with ``frame: 'tenant' | 'lake'``.
        """
        tenant_ready = (
            self._tenant_frame is not None
            and len(self._tenant_frame) > 0
        )
        lake_ready = (
            self._lake_frame is not None
            and len(self._lake_frame) > 0
        )
        if not (tenant_ready and lake_ready):
            raise LakeToolError(
                "build_merge rejected: requires non-empty results "
                "from BOTH query_tenant AND read_lake_table. "
                f"tenant_rows={len(self._tenant_frame) if self._tenant_frame is not None else 0}; "
                f"lake_rows={len(self._lake_frame) if self._lake_frame is not None else 0}. "
                "Call the missing tool(s) first."
            )

        self._merge_attempted = True
        on = list(args.get("on") or [])
        own_value_col = args.get("own_value_col") or ""
        peer_value_col = args.get("peer_value_col") or ""
        gap_op = args.get("gap_op", "difference")

        try:
            merged = merge_own_and_peer(
                own_df=self._tenant_frame,
                peer_df=self._lake_frame,
                on=on,
                own_value_col=own_value_col,
                peer_value_col=peer_value_col,
                gap_op=gap_op,
                viewer=self.context.viewing_merchant_id,
            )
        except (KeyError, MergeGrainError, ViewerScopingError) as exc:
            # Merge-fail dual-frame path (Fix 9b). Return BOTH real
            # frames so the model authors per-frame claims, with
            # business-language guidance.
            self._merge_fail_payload = (
                self._tenant_frame, self._lake_frame,
            )
            self._sql_log.append({
                "surface":   "merge",
                "query":     f"build_merge(on={on!r}, own={own_value_col!r}, peer={peer_value_col!r}, gap_op={gap_op!r}) → FAILED",
                "row_count": 0,
            })
            return {
                "merge_failed": True,
                "reason": f"{type(exc).__name__}: {exc}",
                "tenant": self._df_summary(self._tenant_frame),
                "lake":   self._df_summary(self._lake_frame),
                "guidance": (
                    "The merge could not run. Compose your answer "
                    "side-by-side: state one fact from the tenant "
                    "frame (your own data, e.g. $/unit) AND one fact "
                    "from the lake frame (peer benchmark, often a "
                    "unitless index). Tie them together in prose; do "
                    "not invent a synthetic gap column. EACH claim "
                    "must set source.frame = 'tenant' or 'lake' so it "
                    "resolves against the right source. For the "
                    "chart, set chart_intent.source = 'tenant' or "
                    "'lake' to plot from one real frame."
                ),
            }

        if len(merged) == 0:
            # Empty merge — not a fatal error structurally, but the
            # downstream chart + claims would all return empty. Treat
            # as a merge-fail so the model authors per-frame.
            self._merge_fail_payload = (
                self._tenant_frame, self._lake_frame,
            )
            self._sql_log.append({
                "surface":   "merge",
                "query":     f"build_merge(on={on!r}, own={own_value_col!r}, peer={peer_value_col!r}, gap_op={gap_op!r}) → 0 rows",
                "row_count": 0,
            })
            return {
                "merge_failed": True,
                "reason": (
                    f"Inner merge on {on} produced 0 rows. Typical "
                    "cause: a value-domain mismatch on a join key "
                    "(period_start dtype/casing, category casing, "
                    "etc.). Check the actual values in each frame."
                ),
                "tenant": self._df_summary(self._tenant_frame),
                "lake":   self._df_summary(self._lake_frame),
                "guidance": (
                    "Inspect the join-key values in both frames. If "
                    "they genuinely don't align, answer side-by-side "
                    "(set source.frame on each claim and "
                    "chart_intent.source on the chart)."
                ),
            }

        self._merged_frame = merged
        self._sql_log.append({
            "surface":   "merge",
            "query":     f"build_merge(on={on!r}, own={own_value_col!r}, peer={peer_value_col!r}, gap_op={gap_op!r})",
            "row_count": len(merged),
        })
        return {
            "merge_failed": False,
            "row_count": len(merged),
            **self._df_summary(merged),
            "gap_is_directional": bool(merged.attrs.get("gap_is_directional", False)),
            "magnitude_diagnostic": merged.attrs.get("magnitude_diagnostic", {}),
            "guidance": (
                "Author your chart_intent and claims against these "
                "EXACT column names. The merged frame is the "
                "result-of-record; chart_intent.source defaults to "
                "'merged' and claim sources default to the merged "
                "frame — you don't need to set 'frame' explicitly "
                "after a clean merge."
            ),
        }

    def _auto_invoke_build_merge(self) -> None:
        """Wave 3 Stage 6.5 Fix 10a — server-side merge fallback when
        the model skipped the explicit ``build_merge`` tool call.

        Derives the merge spec from:
        * ``on`` = (tenant columns) ∩ (lake columns) ∩ manifest
          dimensions for the lake table the model read. **Dimension
          keys only** — never join on a metric column, even if the
          tenant SQL happens to compute a column whose name collides
          with a lake metric (e.g. P3's tenant-computed
          ``promo_active_share``).
        * ``own_value_col`` = first non-dimension numeric column in
          the tenant frame.
        * ``peer_value_col`` = first manifest metric that exists in
          the lake frame.

        On empty dimension intersection (T1/T4: neighborhood vs
        Z-code label spaces), routes to the dual-frame path without
        forcing a join. On merge failure or 0-row merge result, same
        — dual-frame path is the structural answer.

        Sets ``self._merge_attempted = True`` either way so the Fix
        9a precondition gate in ``_validate_emit_args`` doesn't fire.
        """
        manifest = self._lake_manifest or {}
        manifest_dims: set[str] = set(manifest.get("dimensions", []))
        manifest_metrics: list[str] = list(manifest.get("metrics", []))
        tenant = self._tenant_frame
        lake = self._lake_frame
        assert tenant is not None and lake is not None  # caller guarantees

        tenant_cols = set(tenant.columns)
        lake_cols = set(lake.columns)
        # Dimension-only intersection. Manifest is the source of
        # truth: a column shared by both frames is only a join key
        # if the manifest declares it a dimension.
        shared_dims = sorted(tenant_cols & lake_cols & manifest_dims)

        if not shared_dims:
            # Empty intersection → dual-frame path. Don't force a
            # meaningless join.
            self._merge_fail_payload = (tenant, lake)
            self._merge_attempted = True
            self._sql_log.append({
                "surface":   "merge",
                "query":     "auto-build_merge(no shared dimension key → dual-frame)",
                "row_count": 0,
            })
            return

        # Pick own_value_col: first non-dimension numeric column in
        # tenant. Skip columns the manifest also names as dimensions
        # (those are join keys, not values).
        own_value_col: str | None = None
        for col in tenant.columns:
            if col in manifest_dims:
                continue
            try:
                if pd.api.types.is_numeric_dtype(tenant[col]):
                    own_value_col = col
                    break
            except Exception:                                # noqa: BLE001
                continue

        # Pick peer_value_col. Wave 3 Stage 6.5 Fix 12: prefer the
        # subclass's PREFERRED_PEER_METRIC (pricing → price_index,
        # demand → units_index, etc.) so the merged frame's
        # peer_benchmark column is semantically meaningful for the
        # specialist's question. Fall back to first-available
        # manifest metric only when the preferred column isn't in
        # the lake frame (e.g. lake_payment_mix doesn't have
        # price_index — Advisor on payment_mix falls back).
        peer_value_col: str | None = None
        if (
            self.PREFERRED_PEER_METRIC
            and self.PREFERRED_PEER_METRIC in lake_cols
            and self.PREFERRED_PEER_METRIC in manifest_metrics
        ):
            peer_value_col = self.PREFERRED_PEER_METRIC
        else:
            for metric in manifest_metrics:
                if metric in lake_cols:
                    peer_value_col = metric
                    break

        if own_value_col is None or peer_value_col is None:
            # Can't derive a defensible spec → dual-frame.
            self._merge_fail_payload = (tenant, lake)
            self._merge_attempted = True
            self._sql_log.append({
                "surface":   "merge",
                "query":     (
                    f"auto-build_merge(no derivable value columns; "
                    f"own={own_value_col!r} peer={peer_value_col!r} "
                    f"→ dual-frame)"
                ),
                "row_count": 0,
            })
            return

        try:
            merged = merge_own_and_peer(
                own_df=tenant,
                peer_df=lake,
                on=shared_dims,
                own_value_col=own_value_col,
                peer_value_col=peer_value_col,
                gap_op="difference",
                viewer=self.context.viewing_merchant_id,
            )
        except (KeyError, MergeGrainError, ViewerScopingError) as exc:
            self._merge_fail_payload = (tenant, lake)
            self._merge_attempted = True
            self._sql_log.append({
                "surface":   "merge",
                "query":     (
                    f"auto-build_merge(on={shared_dims}, own={own_value_col!r}, "
                    f"peer={peer_value_col!r}) → {type(exc).__name__}; dual-frame"
                ),
                "row_count": 0,
            })
            return

        if len(merged) == 0:
            self._merge_fail_payload = (tenant, lake)
            self._merge_attempted = True
            self._sql_log.append({
                "surface":   "merge",
                "query":     (
                    f"auto-build_merge(on={shared_dims}, own={own_value_col!r}, "
                    f"peer={peer_value_col!r}) → 0 rows; dual-frame"
                ),
                "row_count": 0,
            })
            return

        self._merged_frame = merged
        self._merge_attempted = True
        self._sql_log.append({
            "surface":   "merge",
            "query":     (
                f"auto-build_merge(on={shared_dims}, own={own_value_col!r}, "
                f"peer={peer_value_col!r})"
            ),
            "row_count": len(merged),
        })

    @staticmethod
    def _df_summary(df: pd.DataFrame) -> dict[str, Any]:
        """Return a model-readable summary of a frame: columns +
        dtypes + a 50-row preview. Same shape as query_tenant /
        read_lake_table payloads, so the model can read it the same
        way."""
        if df is None or len(df) == 0:
            return {
                "rows":      [],
                "columns":   list(df.columns) if df is not None else [],
                "dtypes":    {},
                "row_count": 0,
                "truncated": False,
            }
        head = df.head(50)
        return {
            "rows":      head.values.tolist(),
            "columns":   list(df.columns),
            "dtypes":    {c: str(df[c].dtype) for c in df.columns},
            "row_count": len(df),
            "truncated": len(df) > 50,
        }

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
        ``_finalize_from_emit`` to assemble the dual-frame validator
        kwargs."""
        if frame_name == "tenant":
            return self._tenant_frame
        if frame_name == "lake":
            return self._lake_frame
        if frame_name == "merged":
            return self._merged_frame
        # Default: merged frame if present, else tenant, else lake.
        if self._merged_frame is not None:
            return self._merged_frame
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

        # --- Fix 9a: when both frames are populated, build_merge
        #     must have run (clean merge or merge-fail) before emit. ---
        if (
            self.MERGE_REQUIRED
            and not self._lake_from_sql  # Wave 3.5 two-query flow: no merge gate
            and tenant_ready and lake_ready
            and not self._merge_attempted
        ):
            raise LakeToolError(
                "emit_response rejected: both tenant and lake frames "
                "are populated; call build_merge(on=..., "
                "own_value_col=..., peer_value_col=..., gap_op=...) "
                "FIRST. The server returns the real merged frame's "
                "columns + dtypes so you can author chart_intent and "
                "claims against actual names, not guesses. Tenant "
                f"columns: {list(self._tenant_frame.columns)}. Lake "
                f"columns: {list(self._lake_frame.columns)}."
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
        prose = (args.get("prose") or "").strip()
        caveats = list(args.get("caveats") or [])

        # Pick the result-of-record + the frames dict for the
        # validator. Three paths:
        #   1. Clean merge → ``_merged_frame`` is the single result;
        #      frames dict carries it under "merged" (plus tenant/lake
        #      for any claims that explicitly named one).
        #   2. Merge-fail dual-frame → no single result; result
        #      defaults to whichever frame chart_intent.source names
        #      (or tenant by default).
        #   3. Single-source → result is the captured frame.
        merge_failed = self._merge_fail_payload is not None
        frames: dict[str, pd.DataFrame] = {}
        if self._tenant_frame is not None and len(self._tenant_frame) > 0:
            frames["tenant"] = self._tenant_frame
        if self._lake_frame is not None and len(self._lake_frame) > 0:
            frames["lake"] = self._lake_frame
        if self._merged_frame is not None:
            frames["merged"] = self._merged_frame

        chart_source = chart_intent.get("source")
        if self._merged_frame is not None and not merge_failed:
            result = self._merged_frame
            if chart_source is None:
                chart_source = "merged"
        elif merge_failed:
            # Dual-frame path; pick the chart's source from
            # chart_intent.source (default "tenant"). Claims resolve
            # against their own ``source.frame`` via the frames dict.
            if chart_source not in ("tenant", "lake"):
                chart_source = "tenant"
            result = frames.get(chart_source, self._tenant_frame
                                if self._tenant_frame is not None
                                else pd.DataFrame())
        else:
            # Wave 3 Stage 6.5 Fix 10b — when both frames are
            # populated but neither merged nor merge_fail_payload
            # is set (rare with Fix 10a in place — would only
            # happen if _auto_invoke_build_merge raised an
            # unexpected exception, or if MERGE_REQUIRED=False
            # and the model emitted directly), synthesize the
            # dual-frame payload on the spot. Never silently drop
            # the lake frame.
            both_populated = (
                self._tenant_frame is not None
                and len(self._tenant_frame) > 0
                and self._lake_frame is not None
                and len(self._lake_frame) > 0
            )
            if both_populated:
                self._merge_fail_payload = (
                    self._tenant_frame, self._lake_frame,
                )
                merge_failed = True
                if chart_source not in ("tenant", "lake"):
                    chart_source = "tenant"
                result = frames.get(
                    chart_source,
                    self._tenant_frame,
                )
            elif self._tenant_frame is not None and len(self._tenant_frame) > 0:
                result = self._tenant_frame
                if chart_source is None:
                    chart_source = "tenant"
            elif self._lake_frame is not None and len(self._lake_frame) > 0:
                result = self._lake_frame
                if chart_source is None:
                    chart_source = "lake"
            else:
                result = pd.DataFrame()

        claims = self._parse_claims(claims_raw)

        # Strip stray Anthropic tool-use XML markers; route narration
        # leaks through the single business-fallback function
        # (Fix 9e).
        prose = LT.sanitize_prose(prose)

        # Build the chart gracefully — if chart_intent is malformed
        # (model omitted per-kind required fields, named a column
        # that isn't in the result, etc.), set chart=None and surface
        # the reason in caveats.
        chart = None
        chart_error: str | None = None
        # Wave 3.5 §2.6 — charts are held for Wave 4. When the model
        # omits chart_intent (the 3.5 prose-only contract), skip chart
        # building entirely: no chart, and NO "chart skipped" caveat
        # (it isn't malformed — it's intentionally absent). A populated
        # chart_intent (legacy read_lake_table flow) still builds.
        if chart_intent and chart_intent.get("kind"):
            try:
                chart = build_chart(chart_intent, result)
            except (MissingColumnError, UnsupportedIntentError,
                    NonNumericChartColumnError, KeyError) as exc:
                chart_error = f"{type(exc).__name__}: {exc}"
            except Exception as exc:                      # noqa: BLE001
                # Unexpected error from a per-kind builder (NaN values in
                # a Plotly indicator, etc.); same fallback path.
                chart_error = f"chart build raised: {type(exc).__name__}: {exc}"

        # Validator runs against ``result`` (the chart's substrate)
        # for Pass B; Pass A's per-claim resolve honors each claim's
        # ``frame`` field via the ``frames`` dict.
        report = validate_claims(prose, claims, result, frames=frames)

        # Wave 3 Stage 6.5 follow-up #8 round-4 — prose-from-claims
        # backfill. When the validated prose is empty/very short but
        # the model authored substantive claims (typical force-accept
        # path: the model spent its turn-budget on the merge spec and
        # never wrote the user-facing paragraph), synthesize a prose
        # paragraph from the passing claims' text_spans. The text_spans
        # were authored by the model and describe real result cells
        # (failed claims are flagged with status="stripped"; we use
        # only passing/normalized ones). The synthesis is safe by
        # construction — each clause is already a passing claim.
        passing_text_spans = [
            d.claim.text_span
            for d in report.claim_dispositions
            if d.status in ("passed", "normalized")
            and d.claim.text_span.strip()
        ]
        if len(report.prose.strip()) < 40 and passing_text_spans:
            # Build sentences by joining text_spans with periods; the
            # model's text_spans are typically complete clauses, so
            # this reads as a coherent paragraph.
            synth = ". ".join(
                ts.rstrip(".") for ts in passing_text_spans
            ) + "."
            report.prose = synth
        elif (
            len(report.prose.strip()) < 40
            and (report.claim_dispositions or len(result) > 0)
        ):
            # Wave 3 Stage 6.5 Fix 9e — single business-language
            # fallback. All paths that need a "couldn't substantiate"
            # fallback route through ``business_fallback()`` so a
            # future regression can't reintroduce mechanics-talk.
            # Shape information (row count + columns) lives in
            # ``effective_caveats`` below, not in prose.
            report.prose = LT.business_fallback()

        effective_caveats = list(caveats)
        if chart_error:
            effective_caveats.append(
                f"(Chart skipped — model's chart_intent was malformed. "
                f"Reason: {chart_error})"
            )
        if len(result) == 0:
            effective_caveats.append(
                "(No data was fetched before emit_response — model "
                "skipped query_tenant / query_lake_sql. Result frame "
                "is empty.)"
            )
        # Side-by-side caveat when own_value and peer_benchmark are
        # in different units (merge layer nullified the gap; this
        # surfaces it honestly to the user).
        if result.attrs.get("gap_is_directional"):
            diag = result.attrs.get("magnitude_diagnostic", {})
            own_med = diag.get("own_median_abs", float("nan"))
            peer_med = diag.get("peer_median_abs", float("nan"))
            effective_caveats.append(
                "Side-by-side comparison only: own_value and "
                f"peer_benchmark are in different units "
                f"(medians ≈ {own_med:.3g} vs {peer_med:.3g}); "
                "the gap column is intentionally null. Use the two "
                "columns directionally rather than subtractively."
            )
        if result.attrs.get("merge_incomplete"):
            reason = result.attrs.get("merge_incomplete_reason", "")
            effective_caveats.append(
                f"Merge spec was incomplete ({reason}); own-side "
                "columns were carried alongside the peer frame as a "
                "best-effort side-by-side. Use the comparison "
                "directionally rather than as a precise own−peer gap."
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
        self._answer_started_at = time.monotonic()
        self._merged_frame = None
        self._merge_fail_payload = None
        self._merge_attempted = False
        self._lake_from_sql = False

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
