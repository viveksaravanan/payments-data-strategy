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

## Wave 3 keystone modules

The §1 unified response contract (D25) is the structural wall. Four
modules implement it; each has a strict guarantee.

- **`response.py`** — `AgentResponse` dataclass + `merge_own_and_peer`.
  Owns the dual-path merge: tenant frame + lake frame → comparison
  frame at matching grain with canonical `own_value` / `peer_benchmark`
  / `gap` columns. Viewer-scoping check rejects own frames carrying
  rows for other merchants; identity check rejects peer frames still
  carrying `banner_code` / `merchant_id` / `merchant` / `name`. Date
  dtype coercion handles the lake's `date32 → object` vs DuckDB's
  `datetime64[us]`. Magnitude check NaNs the `gap` column when own and
  peer are in incompatible units (e.g. raw $ vs unitless index) and
  sets `result.attrs["gap_is_directional"]`.
- **`chart_build.py`** — `ChartIntent` schema + `build_chart`. Nine
  chart kinds (`time_series_vs_peers`, `cross_merchant_comparison`,
  `heatmap`, `scatter_quadrant`, `waterfall`, `geo_map`, `kpi_callout`,
  `small_multiples`, `table_drilldown`). The intent dict names result
  columns; the builder reads values from the frame. **No path from a
  model-supplied number to a figure value.** The chart-intent
  reconciler (after the Stage 7 trim) only drops invalid entries from
  list-valued fields (a partial chart beats no chart) and auto-adds
  `peer_benchmark` to series for cross-merchant kinds (Fix 14); the
  near-miss synonym/case-insensitive/prefix-strip remap layer was
  retired once ValueRef (Fix 13) and build_merge auto-invoke (Fix 10a)
  removed the source of the drift. Numeric-axis guard (Stage 6.5
  follow-up #9d) raises
  `NonNumericChartColumnError` if a value-axis column is datetime /
  object / categorical instead of crashing with `TypeError`.
- **`claims.py`** — Two-pass §1.4 validator (D25.4 / SPEC §1.4).
  - Pass A: each declared `Claim` recomputes from `result` via its
    `source` (`CellLookup | Derivation`). Within tolerance → pass; within
    tolerance band → normalize to true cell; doesn't trace → strip at
    clause level.
  - Pass B: `scan_numerics` tokenizes prose and classifies each
    numeric as metric (sigil, decimal, adjacent modifier) or
    structural (entity counts, years, ordinals). Only metric tokens
    require coverage by a passing Pass-A claim's `text_span`.
    Uncovered metric numeric → strip its clause.
  - Closed derivation grammar: `difference`, `ratio`, `pct_change`,
    `aggregate(sum|mean)`. No arbitrary model arithmetic.
  - `CellLookup.frame: "tenant" | "lake" | "merged" | None` (Fix 9b).
    Untagged claims walk the `frames` dict (`result → merged → tenant →
    lake`) to find the first frame where the column + filter resolve
    (Fix 10c). List-valued row_filter entries → `.isin(v)` IN-clause
    (Fix 11b).
  - `aggregate_column(df, column, agg)` is the single source of truth
    for multi-row mean/sum. Both the validator's resolve and
    `lake_tools._compute_lake_aggregates` call it. Byte-identical
    floats by construction — a model that copies a surfaced aggregate
    value resolves to `passed`, not `normalized` (Fix 11a).
- **`lake_tools.py`** — The tool surface. Five tools:
  `schema_info`, `query_tenant`, `read_lake_table`, `build_merge`,
  `emit_response`. Plus `sanitize_prose` (XML-strip + opening-tag
  unwrap + internal-narration → `business_fallback()`).

## Tool surface — `TOOLS_SPECIALIST`

The model sees these five tools in this order:

1. **`schema_info`** — Free, no args. Returns tenant column lists +
   the five lake table manifests (dimensions, metrics, excludes,
   k_floor, ladder) + a "tips" array carrying load-bearing reminders
   (canonical week-boundary SQL, comparable-units guidance, etc.).
   Always call first. Without it, the model guesses column names and
   burns turns failing.
2. **`query_tenant(sql)`** — Viewer-scoped SQL against `data/raw/`.
   Two-layer enforcement: `check_tenant_predicate` requires
   `WHERE banner_code = '<viewer>'` AND rejects any other 3-letter
   merchant literal; `wrap_tenant_query` CTE-shadows the tenant tables
   with viewer-filtered reads. SELECT-only — semicolons, DDL, DML all
   rejected before any DB connection opens. Returns a payload with a
   50-row preview + the full frame captured in specialist state.
3. **`read_lake_table(table, filters)`** — Reads `data/lake/<table>.parquet`
   for one of the five Wave 2 tables. Filter keys whitelisted against
   `manifest["dimensions"]` — off-grain filters (e.g. `sku` on
   `lake_category_metrics`) get rejected with the relevant Excludes
   quoted. `scope_for_viewer` strips viewer rows + adds
   `peer_relationship` + drops `banner_code` (D24.1).
   `assert_no_identity_leak` safety check before returning. Payload
   includes:
   - `rows` + `columns` + `row_count` (50-row preview).
   - `manifest` (dimensions / metrics / excludes / k_floor / ladder).
   - `aggregates` (Fix 11a) — per-single-dimension means of every
     numeric manifest metric: `aggregates.by_<dim>.<value>.<metric>`.
     The model copies values verbatim into `claim.value` instead of
     guessing.
   - `zero_rows_diagnostic` when filters returned 0 rows — lists
     `available_values_per_filter` so the model retries with a
     corrected filter rather than concluding "the dataset isn't
     populated".
4. **`build_merge(on, own_value_col, peer_value_col, gap_op)`** —
   Server-side merge that returns the **real** merged frame's columns
   + dtypes + 50-row preview (Fix 9a). The model authors `chart_intent`
   and `claims` against names it has actually seen. **Auto-invoked**
   when both tenant + lake frames are populated and the model skips
   the explicit call (Fix 10a) — auto-spec derives `on` as
   `(tenant_cols) ∩ (lake_cols) ∩ manifest_dimensions` (dimension keys
   only; never join on a metric), `own_value_col` as first non-dim
   numeric in tenant, `peer_value_col` from each specialist's
   `PREFERRED_PEER_METRIC` (Fix 12). Empty intersection → dual-frame
   path (`_merge_fail_payload` set, both real frames preserved). On
   `KeyError` / `MergeGrainError` / `ViewerScopingError` / 0-row merge
   → same dual-frame path.
5. **`emit_response(prose, chart_intent, claims, caveats)`** — Single
   terminator. No `merge` field (the merge ran in build_merge). Each
   `CellLookup` claim names `column`, `row_filter`, optional `agg`,
   optional `frame`. Multi-row `row_filter` without `agg` is rejected
   at emit precondition time (Fix 9c) so the model retries with
   `agg="mean"` rather than silently stripping at validation. The
   `prose` field is plain text — never XML tool-use markup, never
   internal-error narration (Rule 2c + `sanitize_prose` backstop).

## Hard rules

- **Tenant isolation.** `query_tenant` enforces it via `check_tenant_predicate`
  (regex predicate check, rejects any other merchant literal) + 
  `wrap_tenant_query` (CTE-shadows tenant tables with viewer-filtered
  reads). Defense in depth. Both live in `src/lake/isolation.py`.
- **Lake identity strip (D24.1).** `scope_for_viewer` in
  `src/lake/scope.py` drops viewer rows, adds `peer_relationship`
  (`segment_peer` | `cross_segment`), drops `banner_code`.
  `assert_no_identity_leak` is the safety net.
- **Manifest grain whitelist.** `_validate_filter_keys` in `lake_tools.py`
  rejects any filter not in `manifest["dimensions"]`. The Excludes list
  reaches the model on rejection so it can decline gracefully.
- **All SQL is SELECT-only.** Regex check before any DB connection. Never
  trust the model to self-restrict.
- **`MAX_TURNS = 6`** (Stage 6.5 follow-up #6 — lowered from 10).
  After the Stage 7 trim, `WALL_CLOCK_CEILING_SEC = 90.0` is the only
  in-loop runtime bound — per-question wall-clock cap; exit to
  `_minimal_response` with `business_fallback()` if exceeded. The
  earlier `MAX_PRECONDITION_REJECTIONS = 3` force-accept floor was
  retired: build_merge auto-invoke (Fix 10a) absorbs the main
  rejection case, so a precondition raise now just retries within
  `MAX_TURNS` and the wall-clock ceiling catches any genuine runaway.
  Both bound the loop without weakening the grounding wall.
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
+ `scripted_tool_use` / `scripted_emit_response` / `scripted_build_merge`.
Live calls are reserved for the preview harness and exceptional
diagnostics.

## Preview harness (Stage 6.5)

`scripts/preview_agent.py` is the demo-readiness review surface (D27.2
dropped golden tests). Single-pill mode or `--batch` (iterates the
qid pill registry). Output is one stacked-section `docs/AGENT_PREVIEW.html`
per merchant carrying: prose (post-validator), interactive Plotly
chart, merged result table, claims with disposition badges
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
