# Payments Data Strategy Demo (v4 / Waves 1+2+3)

Synthetic cross-merchant transaction demo. Models **Verifone** at the
intersection of POS data and payment data — observing baskets, payment
fields, and merchant context across an installed base of merchants.

The locked source-of-truth for the data layer is `docs/DECISIONS.md`
(D11-D20). Wave 1's build spec is `docs/SPEC_wave1_data_generation.md`.
The current dataset's measured magnitudes against the SPEC §6 bands
are in `docs/DQ_REPORT.md`. Read `docs/BASELINE.md` only for the v3
"before" snapshot — it predates v4.

## The panel

Five merchants in a single fictional metro modeled on Charlotte, NC:

| Merchant     | Segment      | Stores |
|--------------|--------------|--------|
| Kroger (KRG) | grocery      | 5      |
| Acme (ACM)   | grocery      | 5      |
| Winn-Dixie (WDX) | grocery  | 5      |
| Taco Bell (TBL) | qsr       | 9      |
| TJ Maxx (TJX) | off-price   | 5      |

29 stores total (D13.2 matrix). ~100,000 cards shared across the
panel with deliberate ~32% multi-merchant overlap (~6% all-three).
90-day window: **March 1, 2026 → May 29, 2026**.

Target ~1.67M transactions, ~10M line items at full scale; pilot mode
runs at 5k cards (~83k txns, ~5 min build).

## Stack

Python 3.11, uv, **DuckDB + Parquet** (D3, supersedes the v3 SQLite),
pyarrow (pinned for deterministic Parquet writes), pytest, pyyaml,
numpy, pandas. **Anthropic SDK** (Wave 3 — `src/agents/`), plotly
(headless figures from the deterministic chart builder). Streamlit +
Folium return in Wave 4 (dashboard rebuild).

## Commands

- `make seed`        — generate full-scale Parquet to `data/raw/` + `data/eval/`
- `make seed-pilot`  — generate at 5k cards (~5 min)
- `make test`        — pytest (engine tests + §6 acceptance battery + lake/agent tests)
- `make test-quick`  — engine unit tests only (skip data-quality fixture)
- `make dq-report`   — regenerate `docs/DQ_REPORT.md` from current Parquet
- `make lake-items`  — build the Wave 3.5 per-viewer line-item peer lake to `data/lake/items/<VIEWER>/`
- `make agent-preview` — run the preview harness against KRG (regenerates `docs/AGENT_PREVIEW.html`)
- `make clean`       — wipe `data/raw/`, `data/eval/`

The Wave 1 engine entry point is `python -m src.generate.engine.run_all`
(optionally with `--scale N`). The v3 `src.generate.run_all`,
`src.db.seed`, and v3 lake modules (`src/lake/views.py`,
`src/lake/peer_mapping.py`) were retired in Wave 1 Stage 7 +
Wave 2 Stage 7. The v3 demo remains at git tag `v3-final` if needed.

## Conventions

**Generation (Wave 1):**

- The engine is **config-driven** (D12). Add a 6th merchant or a new
  segment by editing YAML under `src/generate/config/` — no engine code
  changes. See `src/generate/CLAUDE.md` for the layered details.
- 8 causal layers per D11: geography → population → customers → trips
  → baskets → payment → pricing → events. One module per layer in
  `src/generate/engine/`.
- All randomness flows from `cfg.global_['seed']` (default 42) threaded
  through per-layer derived seeds. Two runs at the same seed produce
  **content-identical Parquet** (T18 verified).
- No PII at any stage. `card_id` is a 16-hex-char SHA-256 hash; no
  names, no emails, no raw PANs, no EBT/cash/declines.

**Storage (Wave 1):**

- DuckDB + Parquet (`src/storage/duckdb_io.py`). Engine builds rows
  in pandas/numpy and writes Parquet via `write_parquet` /
  `write_partitioned_parquet`; DuckDB is **read-only** at runtime.
  `read_parquet` returns a DuckDB relation, never a DataFrame.
- Deterministic writes: pyarrow pinned to 17.0.0, single-threaded,
  alphabetical column order, explicit `sort_keys`, no nondeterministic
  metadata.

**Output contract (SPEC §5):**

- `data/raw/`: merchants, zones, stores, customers, products,
  transactions, transaction_items, promotions. The Wave 2 lake will
  read from here.
- `data/eval/`: `anomalies_groundtruth` only. Physical separation
  enforces "the answer key never reaches the lake / agents" by
  construction.

**Acceptance:**

- 36 tests under `tests/data_quality/test_T01_to_T18.py` mapped to
  SPEC §6 invariants T1-T18. Measured magnitudes published in
  `docs/DQ_REPORT.md` next to each band.
- Engine-layer unit tests under `tests/test_engine_*.py` cover the
  D11 sub-stage invariants (one file per layer).

**Wave 2 lake (REMOVED in Wave 3.5 Stage E):**

- The five anonymized aggregate tables + their builders (`src/lake/build.py`),
  manifest, k-means Z-codes (`zones.py`), and query-time scope (`scope.py`)
  were removed once the line-item lake replaced them (SPEC §11). What
  survives from the Wave 2 layer: `observable_guard.py` (the §1 read
  guard — still used by the line-item builder) and `isolation.py` (the
  tenant predicate + CTE-wrap, used by `query_tenant`).

**Wave 3.5 lake + agents (current):**

- The peer surface is a **raw line-item lake** — five per-viewer
  `(lake_transactions, lake_stores)` pairs under `data/lake/items/<VIEWER>/`,
  built by `src/lake/build_line_items.py` (`make lake-items`). Generalized
  IDs, hour-bucketed time, real `neighborhood`, no consumer linkage,
  viewer-excluded. Queried with aggregating SQL via `query_lake_sql`
  (`src/lake/lake_sql.py`): single aggregating SELECT enforced on the
  DuckDB AST, k=5 line-count floor with `suppressed` surfaced.
- Five user-facing agents — four specialists + Advisor — under
  `src/agents/`. The flow is **two queries, then compare**: `query_tenant`
  (own) + `query_lake_sql` (peer), claims resolve per-frame. No merge
  step.
- The §1 unified response contract (D25) is the structural wall:
  `response.AgentResponse` is the only output type, now a **structured
  contract** (`headline` / `evidence` / `so_what`, with a derived
  read-only `.prose`); the two-pass `claims` validator runs per field
  (`validate_structured_response`) and catches every metric numeric;
  `merge_own_and_peer` + `chart_build` are kept **dormant** for the Wave 4
  dashboard. Charts are deferred (`CHARTS_ENABLED = False`).
- Four tools in `TOOLS_SPECIALIST`: `schema_info`, `query_tenant`,
  `query_lake_sql`, `emit_response`. (`read_lake_table` + `build_merge`
  removed in Stage E.)
- `scripts/preview_agent.py` is the human-review surface; output
  `docs/AGENT_PREVIEW.html`. Stage D/D.5 live-verified on KRG + ACM
  (structured answers, real-dollar peer comparison, §6 routing, no charts).
- Known residuals (model-text, mitigated not eliminated): magnitude /
  direction mislabels — see `docs/SPEC_wave3-5_lakelineitem.md §15`.

## Out of scope for Waves 1+2+3

Dashboard refactor to consume Parquet+lake via DuckDB — Wave 4.
"Ask-AI about this chart" affordance (D9) — Wave 4. Payment
Optimization + Segmentation as standalone specialists — D26.5 routes
them through the Advisor in Wave 3 by design. Production / S3 /
Lambda backends — deferred. Fraud / tampering anomalies (D20.3) —
explicitly out for v4. l-diversity and differential privacy —
deferred per D21.3 / D24.3. Golden-test regression infra — D27.2
deferred to v5.

## File guide

- `docs/DECISIONS.md` — D2-D24 locked source of truth.
- `docs/SPEC_wave1_data_generation.md` — Wave 1 build spec.
- `docs/SPEC_wave2_anonymization_lake.md` — Wave 2 build spec.
- `docs/DQ_REPORT.md` — Wave 1 measured magnitudes vs §6 bands.
- `docs/LAKE_REPORT.md` — Wave 2 privacy-posture artifact: cell
  counts, k-distribution, §8 applied-vs-deferred framing.
- `docs/BASELINE.md` — v3 "before" snapshot (historical).
- `src/generate/CLAUDE.md` — generation-specific architecture.
- `src/generate/config/` — the YAML knobs that drive the engine.
- `src/generate/engine/` — segment-agnostic 8-layer pipeline.
- `src/storage/duckdb_io.py` — Parquet IO + DuckDB read.
- `src/lake/` — `observable_guard.py` (§1 read guard) + `isolation.py`
  (tenant predicate/wrap) + `build_line_items.py` (line-item lake builder)
  + `lake_sql.py` (`query_lake_sql` engine). The Wave 2 aggregate
  builders/manifest/zones/scope were removed in Stage E.
- `src/agents/` — agents: 4 specialists + Advisor, the 4 tools
  (`schema_info`, `query_tenant`, `query_lake_sql`, `emit_response`),
  the §1 keystone modules (`response.py`, `claims.py`, `lake_tools.py`;
  `chart_build.py` dormant for Wave 4). See `src/agents/CLAUDE.md`.
- `src/agents/prompts/` — Markdown system prompts per specialist +
  `_shared_answering_rules.md` (Rules 1–8 + 7b) injected into every
  specialist prompt at render time.
- `tests/data_quality/` — §6 acceptance battery (T1-T18).
- `tests/lake/` — L01 observable-invariant + L02 tenant-isolation + the
  line-item lake acceptance tests (the Wave 2 aggregate L-battery was
  retired in Stage E).
- `tests/agents/` — Wave 3 unit tests (no live LLM; mocked via
  `_fake_llm.py`).
- `scripts/build_dq_report.py` — regenerate the DQ report.
- `scripts/build_lake.py` — build `data/lake/` from `data/raw/`.
- `scripts/build_lake_report.py` — regenerate the lake report.
- `scripts/preview_agent.py` — Wave 3 preview harness (D27.2). Output:
  `docs/AGENT_PREVIEW.html`.
