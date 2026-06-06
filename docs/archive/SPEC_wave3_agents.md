# SPEC — Wave 3: Agents (v4)

**Hand-off spec for Claude Code. Execute closely and independently.**
**Authority:** `DECISIONS.md` D25–D27 is the source of truth — D25 (unified response contract), D26 (roster + surface mapping), D27 (lightweight golden tests). Where this SPEC and DECISIONS disagree, DECISIONS wins — pause and flag.

**Prerequisite — SATISFIED.** Wave 2 is committed on `v4`, the lake is materialized at `data/lake/`, and `docs/LAKE_REPORT.md` publishes the real grain manifest (full-scale, 1.66M txns). This SPEC codes against **that real manifest** — the actual column names, grains, and `Excludes` lists — not an assumed interface.

---

## 0. How to use this SPEC

**Read first:** D25–D27 in `DECISIONS.md`; `docs/LAKE_REPORT.md` (the real lake manifest — column names, grains, exclusions per table); the baseline §6 (v3 agent layer being refactored); the Wave 2 `src/lake/scope.py` + `manifest.py` (the surfaces this wave consumes).

**Work test-first, stage by stage.** Same discipline as Waves 1–2: write the stage's tests, implement to green, commit, advance. Never advance on red.

**This is the "answer quality matters" wave.** Unlike Waves 1–2 (correctness via distribution bands / structural invariants), agent quality is partly subjective. The D25 contract is what makes it *testable*: numbers are mechanically validated; routing and decline behavior are golden-tested. Quality beyond that (phrasing, insight framing) is judgment — flag for review, don't silently ship marginal.

**Pause-and-ask triggers (else run autonomously):**
- The real lake manifest can't support a question type a specialist needs (a grain gap) — flag, don't invent data.
- A DECISIONS clause is ambiguous/contradictory for the case at hand.
- The claims-validation grammar can't express a derivation an agent legitimately needs — flag before widening the grammar.
- Anything that would let an untraceable number reach the user as a stated fact (weakens the D25 guarantee).

**Scope (this wave):** refactor Orchestrator + 4 specialists onto the Wave 2 lake + the D25 contract; add the Conversational Advisor; the unified chart+prose pipeline; the claims validator; ~5–7 golden tests. **Out of scope:** the dashboard rebuild and ask-AI-about-chart (Wave 4 — though the agent contract is built so Wave 4 can pass chart context in).

**Models (D25.8):** Orchestrator always Haiku (`claude-haiku-4-5`). Specialists + Advisor: Haiku by default, switchable to Sonnet via a `SPECIALIST_MODEL` config knob.

---

## 1. The unified response contract (D25) — build FIRST, it's the keystone

Before any agent, build the contract every agent returns and the machinery that enforces it. This is to Wave 3 what the observable-data guard was to Wave 2.

### 1.1 The `AgentResponse` object
```
AgentResponse {
  result:       DataFrame — the MERGED comparison (own + peer), the single source of truth (D25.5)
  chart_intent: { kind, x, series, y_format, ... } — model names COLUMNS, never values
  chart:        figure — built deterministically from (intent + result)
  prose:        narrative — validated against result
  claims:       [{ text_span, value, source }] — number→data bindings the validator checks
  caveats:      [str]
  sql:          [{ surface, query, row_count }]
  grain_notes:  what the answering table does NOT carry (from the Wave 2 manifest)
  telemetry:    { model, tokens, cost, turns, converged }
}
```

### 1.2 The merge step (D25.5)
A typical answer reads own-tenant (full grain, to SKU) AND lake (peer benchmark at category/zone grain). Build the explicit merge: join into one comparison frame (`own_value`, `peer_benchmark`, computed `gap`) at a matching grain. **This merged frame is the single source of truth** — chart and claims validate against it, not against either raw result. Tests: a two-surface question produces one merged frame; chart/claims reference only merged columns.

### 1.3 The chart builder (D25.2/.3)
- Model emits `chart_intent`: `kind` ∈ the nine pattern families (`time_series_vs_peers`, `cross_merchant_comparison`, `heatmap`, `scatter_quadrant`, `waterfall`, `geo_map`, `kpi_callout`, `small_multiples`, `table_drilldown`) + which **result columns** map to x/series/y_format. **Never values.**
- Deterministic builder pulls the named columns from the merged result and renders via the surviving `chart_patterns.py` palette. The chart cannot contain a number absent from the result.
- Tests: chart values equal the result columns exactly (no model-supplied values path exists); an intent naming a missing column fails cleanly.

### 1.4 The claims validator (D25.4) — the defensibility mechanism
Strict guarantee, graceful handling. **Validation covers EVERY number that appears in the prose, not only declared claims** — a number in prose with no backing claim is a validation failure (the model must not be able to evade validation by failing to declare a figure). Three tiers:
1. Number traces to a cell or declared derivation, recomputes within tolerance → pass.
2. Within ~1% relative tolerance (configurable `CLAIM_TOLERANCE`) → pass, normalize to true value (the anti-brittleness valve for "≈", "roughly", rounding).
3. Doesn't trace (or is undeclared) → **strip the containing clause cleanly (not just the digits — no dangling fragments) or one correction pass; do NOT hard-reject the response.** If correction also fails → fall back to "I can't substantiate that figure" for that number only.
- **Undeclared-number scan:** the validator parses the prose for numeric tokens and confirms each is covered by a passing claim. Uncovered number → tier 3. This closes the "just don't declare it" bypass — the guarantee holds regardless of model diligence.
- **Derivation grammar — closed:** `difference (a−b)`, `ratio/share (a/total)`, `pct_change ((a−b)/b)`, `aggregate (sum/mean over cells)`. Each claim declares `{op, operands→cells}`; validator recomputes. No arbitrary model math.
- Tests: a fabricated *declared* number is stripped; a fabricated *undeclared* number in prose is also caught and stripped; a legit rounded number passes; each derivation op recomputes correctly on a fixture; an out-of-grammar derivation is rejected; stripping removes the whole clause (no fragments).

**Gate:** the contract, merge, chart builder, and validator all exist with tests green, exercised by a stub agent before any real agent is built.

---

## 2. Refactor the four specialists onto Wave 2 surfaces (D26.2)

Keep v3's structure (baseline §6.4: bounded tool loop, `MAX_TURNS`, prompt-per-specialist). Change: the tools now hit Wave 2's `data/lake/` + tenant surface, and every response goes through §1's contract.

Per-specialist surface mapping (against the **real manifest** in `docs/LAKE_REPORT.md`):

| Specialist | Lake table | Key columns (from manifest) | Grain limit (manifest `Excludes`) |
|---|---|---|---|
| Pricing | `lake_category_metrics` | `price_index`, `promo_active_share` | no peer SKU; no peer store; week finest |
| Demand | `lake_category_metrics` | `revenue_index`, `units_index`, `wow_delta` | no peer SKU; no daily (week finest) |
| Trade-Area | `lake_trade_area` + `lake_cross_merchant_cohorts` | `share_of_zone`, `zone_category_volume_index`; `cohort_size`, `median_combined_spend` | zone-level; cohorts window-level, median/IQR only |
| Anomaly | `lake_category_metrics` (as cross-merchant baseline) | `wow_delta`, `units_index` | **business anomalies ONLY — must NOT claim fraud/tampering (D20.3)** |

- Tools: tenant surface (own data, full grain, via Wave 2 isolation guards) + lake surface (peers, via `scope.py` — viewer excluded, `peer_relationship` relabeled, real `banner_code` stripped). Reuse Wave 1 `duckdb_io` for queries.
- Each specialist reads its table's `grain_notes` from the manifest into the response, so the contract knows what it can't claim.
- Tests per specialist: queries the right table; respects grain (e.g. Pricing never requests peer SKU); produces a valid merged `AgentResponse`; the dairy worked example (D23.5/D22.5) returns segment-peer category index + own SKU detail.

**Gate per specialist:** its tests green + committed before the next.

---

## 3. The Conversational Advisor (D26.3) — general-purpose fallback

- New agent, same §1 contract. **Not domain-locked** — can reach any lake table, including the two no specialist owns: `lake_payment_mix` ("contactless vs peers") and `lake_segment_mix` (behavioral segments — note the real labels: `frequent_value`, `occasional`, `occasional_premium`, `premium_loyalist`).
- **Owns decline-gracefully (D23.7):** uses each table's manifest `Excludes` to bound itself. Asked for peer-SKU pricing → "I can compare at category/subcategory level; peer SKU detail isn't available." Frames affinities/comparisons with **base rates** ("sauce attaches to 43% of pasta baskets, ~3× the store average"), not naked multipliers.
- Tests: answers a payment-mix question (table no specialist owns); declines an out-of-grain request gracefully (no hallucinated number); produces a valid `AgentResponse`.

**Gate:** Advisor tests green.

---

## 4. Orchestrator routing refactor (D26.4) + two dispatch paths

Keep v3's Haiku router + keyword fallback (baseline §6.3). **Change the "no match" target from a segment-default specialist to the Advisor.** Routing set: `pricing | demand | trade | anomaly | advisor`. Retire the segment-conditional force-default.

**Two dispatch paths (preserve the v3 distinction, baseline §6.2–6.3):**
- **Suggested-question pill → DIRECT to its mapped specialist, skipping the orchestrator.** Pills are pre-mapped to a specialist in the question registry, so routing is known — no router call needed. After dispatch, the pill uses the *identical* unified contract (§1) as everything else.
- **Free-form question AND ask-AI-about-chart (Wave 4) → the orchestrator routes.** Unknown intent needs routing; both go through the router.

**qid handling (the v3 suggested-question mechanics, resolved):** the suggested-question pills SURVIVE as a feature (the `questions.py` registry of question text per agent — a discovery/demo affordance). What's retired: the v3 *separate* per-qid dispatch with its own pattern chart and `chart_takeaways.py` caption. A clicked pill now resolves to its question text + mapped specialist, runs through the normal dispatch → standard `AgentResponse`. **`qid` persists only as a question identifier / cache key — it no longer drives a separate chart path or separate SQL.**

- Always Haiku for the router (D25.8).
- Tests: pricing free-form question routes to Pricing; ill-fitting/general free-form routes to Advisor (not force-routed); a clicked pill goes direct to its mapped specialist with no router call; keyword fallback still works on router parse failure.

**Gate:** routing tests green; both dispatch paths produce a standard `AgentResponse`.

---

## 5. Retire the v3 drift paths (D25.6)

- Delete the `make_chart` path that let the model write `series.values` — replaced by §1.3 intent + deterministic fill.
- Retire the per-qid independent-SQL pattern-chart fetchers **that fed agent responses** (`data.py` helpers like `category_pricing_leverage` used in the chat path) and `chart_takeaways.py` directional captions (existed only to mask the divergence).
- **KEEP:** `chart_patterns.py` (the nine families, now the deterministic renderer palette) and the standalone dashboard panels' own `data.py` sourcing (KPI strip, geography, catalog — they're not agent responses; Wave 4 owns them).
- Tests/audit: no code path lets a model-supplied value reach a chart; `chart_takeaways.py` removed; dashboard-panel data helpers untouched.

**Gate:** drift paths gone; standalone panels intact.

---

## 6. Agent regression testing — DEFERRED to v5 (D27.2)

**No golden tests, no cassette infrastructure in Wave 3.** v3's cassettes are invalid against the refactored agents (new prompts/lake/contract), and without cassettes golden tests need live LLM calls (slow, costly, non-deterministic). Golden tests only ever covered routing + grain/decline; the **D25 validator carries numbers live** and the **§6.5 preview harness** is the human-reviewed routing/decline backstop. So the Wave 3 quality bar = D25 runtime validator + §6.5 harness; automated agent-regression (a fresh deterministic replay layer + golden set) is a v5 item.

- Delete/do-not-port v3's cassette infra (`tests/cassettes/`, `record_baseline_cassettes.py`, `run_phase5_regression.py`) — invalid against refactored agents.
- Per-agent unit tests (Stage 2/3) still exist — routing, grain-respect, contract-validity on synthetic fixtures, no live LLM.

---

## 6.5 Agent preview harness + review artifact (the "can I see the output" deliverable)

The golden tests assert *correctness* (routing, numbers, decline) — they do NOT let a human judge *quality* (is the prose compelling, does the chart read well). This stage builds the artifact that answers "is the output as expected," the Wave 3 equivalent of Wave 1's DQ report / Wave 2's lake report. **Required — without it there is no way to eyeball agent output before Wave 4 builds a dashboard on top.**

- **`scripts/preview_agent.py`** — runs a question (+ viewer merchant) through the real agent path against the real `data/lake/` + tenant surface, and dumps the COMPLETE `AgentResponse`: rendered prose, the chart, the merged result table, the claims with validation status (pass/normalized/stripped), the SQL run, and which agent answered + how it routed.
- **Chart format: interactive HTML** — embed the actual Plotly figure as self-contained HTML (low cost; matches pixel-for-pixel what Wave 4's dashboard will render, so no surprises later). Static PNG rejected — HTML is the real artifact.
- **Batch mode driven by the existing suggested-question registry** — feed it the `questions.py` pills (every suggested question, per merchant/specialist) plus a small set of free-form questions, and emit ONE browsable HTML artifact (`docs/AGENT_PREVIEW.html` or similar) with each question's full rendered output (prose + interactive chart + table + claims + routing). This is the review artifact you read to decide "are these answers demo-ready."
- **Double duty:** this harness is also the golden-test certification tool (D27) — run a question, inspect the rendered output, certify it correct, then bless it as a golden. The inspection tool and the certification tool are the same thing.

Tests: the harness runs a pill question and a free-form question end-to-end and produces valid HTML; the batch covers the full suggested-question registry without error; the embedded chart is the same figure the contract produced (not a re-fetch).

**Gate:** `scripts/preview_agent.py` runs single + batch; `docs/AGENT_PREVIEW.html` generated covering all suggested questions; charts render as interactive HTML. **This is the human-review checkpoint — surface it for review before close.**

---

## 7. Final audit + close

- Re-read touched `CLAUDE.md`/comments; true of the v4 code (carry-over closing rule).
- Confirm an agent answers end-to-end from `data/lake/` + tenant surface in the dashboard-less harness.
- Commit to `v4` and `git push origin v4`. **NO PR, NO merge to main** — `main` frozen on `v3-final` until all of v4 done. Record DoD + which agents/tables wired in the close commit.

**Critical files (indicative):** `src/agents/response.py` (the `AgentResponse` + merge + claims validator), `src/agents/chart_build.py` (intent→figure off the palette), `src/agents/{pricing,demand,trade,anomaly,advisor}.py` (refactored), `src/agents/orchestrator.py` (routing refactor + two dispatch paths), `src/agents/llm.py` (model config knob), `scripts/preview_agent.py` (preview harness), `docs/AGENT_PREVIEW.html` (the review artifact, generated), `tests/agents/test_golden_*.py`. **Deleted:** `chart_takeaways.py`, the agent-feeding per-qid `data.py` fetchers, the model-values `make_chart` path.

---

## 8. Scope discipline

Wave 3 ships the refactored agents + unified contract + claims validation + golden net. **No dashboard rebuild, no ask-AI-about-chart (Wave 4).** But build the `AgentResponse` contract so Wave 4 can pass a chart-context object into an agent call (D9) without reshaping it — the contract is the seam Wave 4 plugs into.

*Next planning step: Wave 4 (dashboard rebuild to consume the agents + Parquet/DuckDB; ask-AI-about-chart per D9). Wave 4 is still principle-level — the last design drill.*