# Agents (Wave 3)

Five user-facing agents — four domain specialists + one Conversational
Advisor. All merchant-scoped (every query inherits a viewing-merchant
context). Network Analyst from v2 has been retired. Strategy doc §10.2
specifies seven personas; the remaining two (Payment Optimization,
Segmentation) ride through the Advisor in Wave 3 by design (D26.5).

- **`orchestrator.py`** — Free-form questions land here. Haiku-based
  classifier (system prompt `prompts/orchestrator.md`) picks one of
  `pricing | demand | trade | anomaly | advisor`; keyword-fallback
  table at `orchestrator.py:64-91` runs when the API is unavailable or
  the classifier output doesn't parse. No tool loop in the orchestrator;
  it classifies and dispatches. The no-match target is the **Advisor**
  (D26.4 — replaces v3's force-routing to a segment-default specialist).
- **`pricing.py`** — Pricing & Benchmarking. `PREFERRED_PEER_METRIC =
  "price_index"`. `MAX_TURNS = 6`.
- **`demand.py`** — Demand Forecasting & Campaign Adjudication.
  `PREFERRED_PEER_METRIC = "units_index"`. `MAX_TURNS = 6`.
- **`anomaly.py`** — Anomaly Detection (operational only; **never**
  fraud/tampering per D20.3). `PREFERRED_PEER_METRIC = "wow_delta"`.
  `MAX_TURNS = 6`.
- **`trade.py`** — Trade Area Intelligence. `PREFERRED_PEER_METRIC =
  "share_of_zone"`. `MAX_TURNS = 6`.
- **`advisor.py`** — Conversational Advisor. Owns
  `lake_payment_mix` and `lake_segment_mix`; falls through here for
  ambiguous / multi-topic / definitional questions. `MAX_TURNS = 6`,
  `MERGE_REQUIRED = False` (single-source pills like payment-mix don't
  need own/peer merge).

All four specialists + the Advisor subclass `specialist.py::Specialist`
— the shared bounded tool loop, the §1.4 claims validator integration,
prose sanitization, prose-from-claims backfill, force-accept floor +
wall-clock ceiling, claim dispositions surfacing. Prompts live in
`prompts/<name>.md` and are loaded once at construction; the
`_shared_answering_rules.md` file (Rules 1–8 + 7b) is appended to every
specialist prompt at render time.

Suggested-question pills (dashboard chat panel) → `src/dashboard/agents.py::dispatch`
→ `_run_specialist(agent_id, qid, merchant_id, …)`. Free-form input →
`dispatch_orchestrated(merchant_id, question, …)` → `Orchestrator.ask`.
Both paths produce the unified `AgentResponse` dataclass from
`response.py`. The `qid` persists as a cache key only — there are no
per-qid pattern charts (D25.6 retired them).

The legacy v2 `MerchantAdvisor` was archived in Phase 1.5 to
`docs/archive/legacy_agent/`. The current Advisor (`advisor.py`) is its
Wave 3 replacement.

## Keystone modules

The §1 unified response contract (D25) is the structural wall. After the
Wave 3.5 rebuild the agent runs a **two-query flow** (`query_tenant` +
`query_lake_sql`) and emits a **structured response** (headline / evidence /
so_what); there is no merge step and charts are deferred.

- **`response.py`** — `AgentResponse` dataclass (`headline`, `evidence`,
  `so_what`, `claims`, `caveats`, plus a derived read-only `.prose`
  property that joins the text fields). Own (tenant) and peer (lake)
  arrive as **two separate frames**; claims resolve per-frame via their
  `frame` field. `merge_own_and_peer` is **kept dormant** (a plain join
  emitting `own_value` / `peer_benchmark` / `gap` for the Wave 4 dual-
  series chart builders) — it has no role in 3.5 grounding and was
  decoupled from the removed `check_magnitude_compatibility`.
- **`chart_build.py`** — `ChartIntent` schema + `build_chart`. **Dormant
  in 3.5** (`CHARTS_ENABLED = False` in `lake_tools.py`; the emit schema
  has no `chart_intent` field). Kept intact for the Wave 4 dashboard;
  still unit-tested directly. No path from a model-supplied number to a
  figure value (the D25.2 guarantee) when it returns.
- **`claims.py`** — Two-pass §1.4 validator (D25.4 / SPEC §1.4), run
  **per structured field** by `validate_structured_response` (it calls
  the single-string `validate_claims` once per non-empty field; the
  union of fields equals the old scan surface, so the guarantee holds
  field by field).
  - Pass A: each declared `Claim` recomputes from the frame via its
    `source` (`CellLookup | Derivation | ValueRef`). Within tolerance →
    pass; within band → normalize to the true cell; doesn't trace →
    strip at clause level.
  - Pass B: `scan_numerics` classifies each numeric as metric (sigil,
    decimal, adjacent modifier) or structural (counts, years, ordinals).
    Uncovered metric numeric → strip its clause.
  - Closed derivation grammar: `difference`, `ratio`, `pct_change`,
    `aggregate(sum|mean)`. No arbitrary model arithmetic.
  - `CellLookup.frame: "tenant" | "lake" | None`. Untagged claims walk
    `result → tenant → lake`. List-valued row_filter → `.isin(v)`.
  - `aggregate_column(df, column, agg)` is the single source of truth
    for multi-row mean/sum (the validator's resolve calls it).
- **`lake_tools.py`** — The tool surface. Four tools (Wave 3.5 Stage E):
  `schema_info`, `query_tenant`, `query_lake_sql`, `emit_response`. Plus
  `sanitize_prose` (XML-strip + opening-tag unwrap + internal-narration /
  question-ending → `business_fallback()`).

## Tool surface — `TOOLS_SPECIALIST`

The model sees these four tools in this order (Wave 3.5: `read_lake_table`
and `build_merge` were removed in Stage E — peer data is now raw line-item
SQL, and there is no merge step):

1. **`schema_info`** — Free, no args. Returns tenant column lists + the
   two line-item lake tables' schemas (`lake_transactions`, `lake_stores`,
   introspected from a viewer's materialized pair) + a "tips" array of
   load-bearing reminders (lake_stores-join-for-neighborhood, the k=50
   floor, COUNT(DISTINCT lake_txn_id) for transaction-level shares, and the
   taxonomy rule below). Always call first.
   - **Taxonomy rule.** `products` carries a dual taxonomy. For answers about
     the merchant's OWN data, group by `merchant_department/category/subcategory`
     (their real shelf labels). For any comparison to PEERS, group by
     `functional_department/category/subcategory` — the lake publishes the
     functional hierarchy as `department`/`category`/`subcategory`, so only
     functional labels line up across merchants. The lake never carries merchant
     labels.
2. **`query_tenant(sql)`** — Viewer-scoped SQL against `data/raw/`.
   Two-layer enforcement: `check_tenant_predicate` requires
   `WHERE banner_code = '<viewer>'` AND rejects any other 3-letter
   merchant literal; `wrap_tenant_query` CTE-shadows the tenant tables
   with viewer-filtered reads. SELECT-only. Returns a 50-row preview +
   the full frame captured in specialist state.
3. **`query_lake_sql(sql)`** — Aggregating SQL against the viewer's
   line-item peer lake (`lake_transactions` / `lake_stores` resolve to
   the viewer's materialized pair). The viewer's OWN rows are **present**
   tagged `peer_relationship = 'self'` (so the own-vs-peer gap is sortable
   in one query); any aggregate over `lake_transactions` MUST reference
   `peer_relationship` — a bare `AVG`/`SUM`/`COUNT` is rejected
   (`_check_peer_scoped`) so self can't contaminate a peer number, and the
   k=50 floor counts peer rows only (`_inject_count` FILTERs to `'peer'`).
   Enforces single aggregating SELECT (raw-row selects rejected via the
   DuckDB AST), applies the k=50 line-count floor, and surfaces the
   dropped-group `suppressed` count. Same `_df_to_payload` shape as
   `query_tenant`, so CellLookup / the §1.4 validator resolve against it
   unchanged. The result is captured as the **`lake`** frame.
4. **`emit_response(headline, evidence, so_what, claims, caveats)`** —
   Single terminator, the Wave 3.5 structured contract. `headline` is
   required; `evidence` is a list of grounded sentences; `so_what` is
   optional. Every metric numeric across the text fields needs a `claims`
   entry (`CellLookup` names `column`, `row_filter`, optional `agg`,
   `frame: "tenant" | "lake"`; multi-row without `agg` rejected at emit —
   Fix 9c). No `chart_intent` field (charts deferred to Wave 4). The text
   fields are plain text — never XML markup, never internal-error
   narration, never a question-ending (Rule 2c + `sanitize_prose`).

## Hard rules

- **Tenant isolation.** `query_tenant` enforces it via `check_tenant_predicate`
  (regex predicate check, rejects any other merchant literal) + 
  `wrap_tenant_query` (CTE-shadows tenant tables with viewer-filtered
  reads). Defense in depth. Both live in `src/lake/isolation.py`.
- **Lake identity strip (D24.1) — at build time.** The per-viewer line-item
  lake is materialized by `src/lake/build_line_items.py`: the viewer's own
  rows are **present but tagged `peer_relationship = 'self'`** (so the
  own-vs-peer gap is sortable in one query); every row carries only a
  `peer_relationship` label (`'self'` = the viewer, `'peer'` = same segment,
  `'merchant'` = different segment) with **no per-competitor identity** (the
  old `peer_a`/`peer_b` pseudonyms are gone), `banner_code` is dropped, IDs
  are generalized, time is hour-bucketed, and there is no consumer linkage.
  Self rows pass through the identical projection — no SKU, no merchant
  labels, no privileged detail. There is no query-time scope step
  (`src/lake/scope.py` was removed in Wave 3.5 Stage E). The privacy of the
  self-tag rests on two query-time guards (below): peer aggregates must
  FILTER `'peer'`, and the k-floor counts peer rows only.
- **Aggregating-only + k=50 floor + peer-scoped.** `query_lake_sql`
  (`src/lake/lake_sql.py`) enforces a **single aggregating `SELECT`** on the
  DuckDB AST (raw-row selects rejected), a **k=50 line-count floor** per group
  (`_inject_count` FILTERs the count to `peer_relationship = 'peer'`, so self
  volume can't pad a thin peer group past the floor), surfacing the
  dropped-group `suppressed` count, and a **peer-scope check**
  (`_check_peer_scoped`): any aggregate over `lake_transactions` that never
  references `peer_relationship` is rejected, so a bare `AVG`/`SUM` can't
  silently blend the viewer's own (`'self'`) rows into the peer number. The
  Wave 2 manifest grain-whitelist (`_validate_filter_keys` /
  `manifest["dimensions"]`) was removed with the aggregate lake — the peer
  surface is now raw line items queried with SQL.
- **All SQL is SELECT-only.** Regex check before any DB connection. Never
  trust the model to self-restrict.
- **`MAX_TURNS = 6`** (Stage 6.5 follow-up #6 — lowered from 10).
  After the Stage 7 trim, `WALL_CLOCK_CEILING_SEC = 90.0` is the only
  in-loop runtime bound — per-question wall-clock cap; exit to
  `_minimal_response` with `business_fallback()` if exceeded. The
  earlier `MAX_PRECONDITION_REJECTIONS = 3` force-accept floor was
  retired; a precondition raise now just retries within `MAX_TURNS` and
  the wall-clock ceiling catches any genuine runaway. Both bound the
  loop without weakening the grounding wall.
- **No untraceable number reaches the user as a stated fact.**
  Pass A (declared) + Pass B (undeclared) cover the full surface. The
  metric-vs-structural distinction is at the scanner level so "5 stores
  in Zone 3 over 12 weeks" survives without claims.
- **Anomaly is business-anomalies-only.** Never fraud / tampering /
  skimming / chargeback (D20.3). No signal in the panel.

## Failure-fallback surface (Fix 9e)

`lake_tools.business_fallback()` is the **single canonical**
business-language fallback. All paths route through it:
- `sanitize_prose` narration detector.
- Specialist all-stripped synthesizer.
- Force-accept floor caveat.
- Wall-clock ceiling exit.
- Unconverged loop exit.

`_FORBIDDEN_MECHANICS_TERMS` is the negative-space test: no path may
emit "validator", "draft", "merge spec", "retry with corrected
parameters", "system issue", "precondition", "force-accept", etc. to
the user-facing prose. Regression test
`test_no_mechanics_terms_in_assembled_response_prose` asserts the
invariant.

## Models

Specialists + router run on `claude-haiku-4-5-20251001` by default
(D25.8). `SPECIALIST_MODEL` env var (read in `llm.py:42`) overrides
specialists + Advisor to Sonnet 4.6 for targeted quality tests; the
router stays pinned Haiku. Round-7 pricing of the Haiku KRG batch:
~12 pills × ~$0.07 = ~$0.80; Sonnet would be ~3× that for the same
batch.

Most unit tests mock the client via `tests/agents/_fake_llm.py::patch_llm`
+ `scripted_tool_use` / `scripted_emit_response`. Live calls are reserved
for the preview harness and exceptional diagnostics.

## Preview harness (Stage 6.5)

`scripts/preview_agent.py` is the demo-readiness review surface (D27.2
dropped golden tests). Single-pill mode or `--batch` (iterates the
qid pill registry). Output is one stacked-section `docs/AGENT_PREVIEW.html`
per merchant carrying: the structured answer (headline / evidence /
so-what, post-validator), result table, claims with disposition badges
(`passed`/`normalized`/`stripped` color-coded), SQL surfaces (tenant +
lake + merge), routing decision, telemetry. The harness is the human-
review surface at Checkpoint 2 between Stage 6.5 and Stage 7.

## Style

- **Prompts live in `prompts/*.md`**, never as Python strings.
  Each specialist's class declares `PROMPT_PATH`; the base class reads
  it once at construction. Dynamic context (`{{viewer_id}}`,
  `{{viewer_name}}`, `{{viewer_segment}}`) is plain string replacement.
  `_shared_answering_rules.md` is appended to every specialist prompt.
- **Tools are real Python**, not LLM-described. A tool is a function
  the runner invokes when the model emits a `tool_use` block; the
  runner appends the result as a `tool_result`. No wrapper frameworks.
- **Keep the loop boring.** The hardest debugging happens when the
  loop does something clever. It shouldn't.

## Development workflow — prompt / class-attribute changes

Streamlit's hot-reload does NOT reliably pick up changes to:

- Prompt files under `prompts/*.md`.
- Class attributes (`MAX_TURNS`, `PREFERRED_PEER_METRIC`,
  `MERGE_REQUIRED`).
- Model identifiers in `llm.py`.

After editing any of these, restart Streamlit. The Wave 4 dashboard
rebuild will keep this constraint.
