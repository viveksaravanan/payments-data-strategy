---
title: Payments Data Strategy
emoji: 📊
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 8501
pinned: false
license: mit
---

# Payments Data Strategy Demo

**Live demo:** https://huggingface.co/spaces/viveks2862/payments-data-strategy

A working demo of the data architecture in the Core Data Strategy & Solutions document:
synthetic cross-merchant transaction data, a privacy-preserving **peer lake** that lets a
merchant compare itself to its same-segment competitors without seeing anyone's raw data,
and an AI agent layer that answers natural-language questions over a merchant's own data
and the anonymized peer set.

> A payments company sees a join no one else can: the basket, the payment instrument, and
> the same person across multiple merchants. This demo makes that join concrete.

## What it shows

Five fictional merchants in a single fictional metro modeled on Charlotte, NC:

| Merchant       | Segment    | Stores |
|----------------|------------|--------|
| Kroger (KRG)   | grocery    | 5      |
| Acme (ACM)     | grocery    | 5      |
| Winn-Dixie (WDX) | grocery  | 5      |
| Taco Bell (TBL)  | qsr      | 9      |
| TJ Maxx (TJX)  | off-price  | 5      |

29 stores total. ~100,000 cards are shared across the panel with a deliberate ~32%
multi-merchant overlap (~6% shop all three grocers), over a 90-day window
(**March 1 – May 29, 2026**). At full scale the panel is ~1.67M transactions and ~10M
line items; a pilot mode runs at 5k cards (~83k txns) for fast iteration.

```
generate (config-driven)        data/raw/*.parquet        (each merchant's own data,
   8 causal layers, seed 42  ─▶  tenant census            full grain, no PII)
                                       │
                                       ├─▶ data/eval/ (anomaly answer key — never reaches the lake)
                                       │
                                       ▼
                          build per-viewer line-item peer lake   data/lake/items/<VIEWER>/
                          (viewer-excluded, peer_relationship,    (lake_transactions, lake_stores)
                           hour-bucketed, no consumer linkage, k=5)
                                       │
                                       ▼
                          4 specialists + Advisor  ◀── two-query flow: query_tenant (own)
                          (structured answers)          + query_lake_sql (peer), compare
                                       │
                                       ▼
                          Streamlit dashboard (DuckDB-on-Parquet, 5 merchant roles)
```

## Architecture

- **Tenant layer (physical).** Each merchant's own data at full granularity in Parquet
  under `data/raw/` (merchants, zones, stores, customers, products, transactions,
  transaction_items, promotions). DuckDB reads it read-only at runtime. Agent queries are
  gated by `WHERE banner_code = '<viewer>'` at the tool layer (`src/lake/isolation.py`).
- **Peer lake (line-item).** Five per-viewer `(lake_transactions, lake_stores)` pairs under
  `data/lake/items/<VIEWER>/`, built by `src/lake/build_line_items.py`. The viewing
  merchant's own rows are excluded at build time; the rest carry only a `peer_relationship`
  label (`'peer'` = same segment, `'merchant'` = different segment) — **no per-competitor
  identity**. IDs are generalized, time is hour-bucketed, neighborhoods are real names, and
  there is **no consumer linkage** (no `customer_id`). Queried with aggregating SQL via
  `query_lake_sql` (`src/lake/lake_sql.py`), which enforces a single aggregating `SELECT`
  on the DuckDB AST and a **k = 5** line-count floor (thin groups are suppressed).
- **Agents.** A Haiku orchestrator routes a free-form question to one of four specialists —
  **pricing, anomaly, demand, trade** — or the **Conversational Advisor**. Each runs a
  bounded tool loop (`schema_info`, `query_tenant`, `query_lake_sql`, `emit_response`) and
  returns a structured **`headline` / `evidence` / `so_what`** response. Every metric in the
  text is checked by a claims validator against the actual query result; untraceable numbers
  are stripped. Charts are deferred (agent answers are explanation-only for now).
- **Dashboard.** A Streamlit app (`src/dashboard/app.py`) with five merchant roles and five
  sections — KPIs, performance, geography (own + aggregate-peer overlay), catalog, customers —
  plus a chat panel wired to the agents. Reads Parquet through an in-memory DuckDB connection.

## How to run

Requires Python 3.11+, [uv](https://github.com/astral-sh/uv), and an Anthropic API key
(for the chat panel).

```bash
git clone <repo-url> && cd payments-data-strategy

cp .env.example .env          # paste your ANTHROPIC_API_KEY

make seed-pilot               # generate Parquet at 5k cards (~5 min); or `make seed` for full scale
make lake-items               # build the per-viewer line-item peer lake
uv run streamlit run src/dashboard/app.py   # dashboard at http://localhost:8501
```

Other targets: `make test` (full T1–T18 data-quality battery + lake/agent tests),
`make test-quick` (engine unit tests only), `make dq-report` (regenerate
`docs/DQ_REPORT.md`), `make agent-preview` (regenerate `docs/AGENT_PREVIEW.html`).

## What's in the demo

Switch roles in the dashboard and ask the chat panel free-form questions, or click a
suggested pill. Examples exercise own-only, peer-comparison, and combined paths:

- Top categories by revenue, and what drove each *(own)*
- Stores or neighborhoods with a recent transaction drop *(own — surfaces the planted decline)*
- How does my basket size / dairy unit pricing compare to peer grocers? *(own + peer)*

Three planted anomalies the agents and dashboard can surface (full spec in
[`docs/DECISIONS.md`](./docs/DECISIONS.md) D20; measured magnitudes in
[`docs/DQ_REPORT.md`](./docs/DQ_REPORT.md)):

- **University City / Eastway decline** — a multi-week grocery traffic decline, hardest on
  Winn-Dixie (~40%), lighter on Kroger (~15%) and Acme (~10%).
- **Kroger NoDa produce spike** — a short 4-day inclusion boost (Apr 21–24).
- **Coordinated pasta promos** — Kroger lift, Acme failure, Winn-Dixie modest lift in their
  respective late-April windows.

## Deployment (HuggingFace Spaces)

The Space runs the **Docker SDK**; the YAML front-matter at the top of this README
configures the SDK + port, the root `Dockerfile` builds the image, and
[`streamlit_app.py`](./streamlit_app.py) is the entry point (it promotes
`ANTHROPIC_API_KEY` from `st.secrets` and runs `src/dashboard/app.py`). The v4 deployment
expects the Parquet census + line-item lake to be present (`make seed` + `make lake-items`);
the v3 SQLite cold-boot regeneration path was retired and the v4 auto-provisioning path is a
follow-up — for the previously-shipped v3 deploy, check out the `v3-final` tag.

## Caveats

- **All data is synthetic.** Generated by `src/generate/`. The merchant names are
  plausible-sounding stand-ins; this demo is not affiliated with any real company.
- **No PII at any stage.** `card_id` is emitted directly as a 16-hex-char SHA-256 hash;
  there are no names, emails, raw PANs, or EBT/cash/declines on disk.
- **k = 5, not k ≥ 50.** Production-grade suppression calls for k ≥ 50; at this panel size
  that would erase most cells, so the demo uses k = 5. Only the threshold differs.
- **Four specialists + Advisor.** The strategy doc specifies seven personas; payment
  optimization and segmentation ride through the Advisor by design in this version.
- **Batch, not streaming.** The strategy doc describes a real-time pipeline; this demo runs
  in batch from a fixed seed (deterministic, content-identical Parquet at seed 42).

## Further reading

- [`docs/DECISIONS.md`](./docs/DECISIONS.md) — the locked design source of truth (D2–D27).
- [`CLAUDE.md`](./CLAUDE.md) — project conventions, commands, and the file guide.
- [`docs/SPEC_wave1_data_generation.md`](./docs/SPEC_wave1_data_generation.md) — the data generator,
  [`docs/SPEC_wave3-5_lakelineitem.md`](./docs/SPEC_wave3-5_lakelineitem.md) — the line-item lake,
  [`docs/SPEC_wave4_dashboard.md`](./docs/SPEC_wave4_dashboard.md) — the dashboard rebuild.
- [`docs/archive/`](./docs/archive/) — historical v2.5/v3 vision, audit, design, and report artifacts.

## License

MIT.
