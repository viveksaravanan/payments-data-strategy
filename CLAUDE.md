# Payments Data Strategy Demo (v4 / Wave 1)

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
numpy, pandas. Anthropic SDK + Streamlit + Folium will return in
Wave 3 (agents) and Wave 4 (dashboard).

## Commands

- `make seed`        — generate full-scale Parquet to `data/raw/` + `data/eval/`
- `make seed-pilot`  — generate at 5k cards (~5 min)
- `make test`        — pytest (engine tests + §6 acceptance battery)
- `make test-quick`  — engine unit tests only (skip data-quality fixture)
- `make dq-report`   — regenerate `docs/DQ_REPORT.md` from current Parquet
- `make clean`       — wipe `data/raw/`, `data/eval/`

The Wave 1 engine entry point is `python -m src.generate.engine.run_all`
(optionally with `--scale N`). The v3 `src.generate.run_all` and
`src.db.seed` modules were retired in Wave 1 Stage 7 — the v3 demo
remains at git tag `v3-final` if needed.

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

## Out of scope for Wave 1

Anonymization engine, the cross-merchant lake (Wave 2). Agent
unification (D8) and "ask-AI about this chart" (D9) — Wave 3.
Dashboard refactor to consume Parquet via DuckDB — Wave 4.
Production / S3 / Lambda backends — deferred. Fraud / tampering
anomalies (D20.3) — explicitly out for v4.

## File guide

- `docs/DECISIONS.md` — D2-D20 locked source of truth.
- `docs/SPEC_wave1_data_generation.md` — Wave 1 build spec.
- `docs/DQ_REPORT.md` — current measured magnitudes vs §6 bands.
- `docs/BASELINE.md` — v3 "before" snapshot (historical).
- `src/generate/CLAUDE.md` — generation-specific architecture.
- `src/generate/config/` — the YAML knobs that drive the engine.
- `src/generate/engine/` — segment-agnostic 8-layer pipeline.
- `src/storage/duckdb_io.py` — Parquet IO + DuckDB read.
- `tests/data_quality/` — §6 acceptance battery.
- `scripts/build_dq_report.py` — regenerate the DQ report.
