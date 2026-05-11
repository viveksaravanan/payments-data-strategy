# Payments Data Strategy Demo

A working demo of the data architecture described in the Core Data Strategy & Solutions document — synthetic cross-merchant transaction data, a privacy-engine that exposes the cross-merchant lake as parameterized views, and an AI agent that answers natural-language questions about both a merchant's own data and privacy-preserved peer aggregates.

## What it shows

> A payments company sees a join no one else can: the basket, the payment instrument, and the same person across multiple merchants. This demo makes that join concrete.

Five fictional merchants in a single Charlotte, NC metro:

- **Kroger** (grocery, 30 stores)
- **Acme** (grocery, 25 stores)
- **Winn-Dixie** (grocery, 20 stores)
- **Taco Bell** (QSR, 40 stores)
- **TJ Maxx** (off-price retail, 8 stores)

share a **10,000-customer panel** over a 90-day window (Mar 1 – May 29, 2026). The data flows through a four-stage pipeline:

```
capture (synthetic)  →  store (SQLite, tenant_* tables only)
        │                        │
        │                        ▼
        │              privacy engine (lake-as-views, computed per query)
        │                        │
        ▼                        ▼
  no PII at any stage      Merchant Advisor agent  →  Streamlit dashboard
```

The architecture has one physical layer plus one virtual layer:

- **Tenant tables** — each merchant's own data at full granularity. Kroger sees Kroger; queries are gated by `WHERE merchant_id = '<current_merchant>'` at the agent tool layer.
- **Lake (virtual)** — two logical tables (`lake_transactions`, `lake_stores`) computed at query time from the tenant tables by `src/lake/views.py`. The viewing merchant's own rows are excluded; the other four merchants are pseudonymized as `peer_a..peer_d`. Opaque IDs replace internal keys; ZIP5 → ZIP3; full timestamp → 10-bucket time-of-day; `txn_total` → 10-bin label; **`customer_id` is dropped** ("no consumer linkage" per strategy doc §5.2).

A Streamlit dashboard lets you switch between five merchant roles and ask questions answered by an AI agent that decides which layer (tenant, lake, or both) to query.

## How to run

Requires Python 3.11+, [uv](https://github.com/astral-sh/uv), and an Anthropic API key.

```bash
# Clone
git clone <repo-url> && cd payments-data-strategy

# API key
cp .env.example .env
# Edit .env and paste your ANTHROPIC_API_KEY

# Run
make demo
```

`make demo` generates synthetic data, loads SQLite, and launches the dashboard on `http://localhost:8501`. `scripts/demo.sh` is the equivalent shell wrapper. Total cold-start time: ~2 minutes (~80s generation, ~25s SQLite seed, plus Streamlit startup).

## What's in the demo

Each role's canned questions exercise tenant-only, lake-only, and combined-layer paths. A few examples:

**As Kroger** (or Acme, or Winn-Dixie):
1. Top categories by revenue last week, and which subcategories drove each *(tenant)*
2. Which products are bought together with whole milk *(tenant)*
3. Stores with a recent transaction-count drop *(tenant — surfaces the planted **University City decline**)*
4. How does my basket size compare to peer grocers? *(tenant + lake)*
5. How does my dairy unit pricing compare to peer grocers? *(tenant + lake)*

**As Taco Bell:** menu-item revenue, basket combinations, store dropouts, ticket-size and entry-mode comparisons against QSR peers.

**As TJ Maxx:** category revenue, multi-category baskets, store dropouts, ticket-size and entry-mode comparisons against retail peers.

Three planted anomalies the agent can find when asked (full spec in [`DATA.md`](./DATA.md) §9):

- **University City decline** — 4-stage traffic ramp on KRG/ACM/WDX University City stores; deepest at Kroger.
- **Plaza Midwood Kroger avocado spike** — 4-day pattern peaking Apr 22; Kroger only.
- **Coordinated pasta promos** — KRG lift, Acme failure, Winn-Dixie modest lift in their respective late-April windows.

Each answer shows the SQL the agent ran in an expandable panel — the demo is auditable, not magic. There's also a **MOCK MODE** toggle in the page header for offline demos (skips the LLM API and returns canned responses).

## Architecture

```
   ┌──────┐ ┌──────┐ ┌─────────┐ ┌──────────┐ ┌─────────┐
   │Kroger│ │ Acme │ │WinnDixie│ │ Taco Bell│ │ TJ Maxx │   capture
   └──┬───┘ └──┬───┘ └────┬────┘ └────┬─────┘ └────┬────┘   (no PII at any stage)
      └────────┴──────────┴───────────┴────────────┘
                              │ raw CSVs (customer_id is a SHA-256
                              │ of a never-persisted synthetic PAN)
                              ▼
                  ┌────────────────────────┐
                  │  SQLite, tenant_*      │   store
                  │  tables only           │
                  └────────────┬───────────┘
                               ▼
                  ┌────────────────────────┐
                  │  src/lake/views.py     │   privacy engine
                  │  - exclude viewer      │   (computed at query time)
                  │  - peer_a..peer_d      │
                  │  - opaque IDs, ZIP3,   │
                  │    hour buckets, bins  │
                  │  - no customer_id      │
                  └────────────┬───────────┘
                               ▼
                  ┌────────────────────────┐
                  │  Merchant Advisor      │   insight
                  │  Streamlit dashboard   │
                  │  (5-merchant roles)    │
                  └────────────────────────┘
```

See [`ARCHITECTURE.md`](./ARCHITECTURE.md) for the mapping to the parent Core Data Strategy document and the deferred roadmap.

## Important caveats

- **All data is synthetic.** Generated by `src/generate/`. No real customers, real cards, or real merchants are involved. The merchant names are plausible-sounding stand-ins; this demo is not affiliated with Kroger, Acme, Winn-Dixie, Taco Bell, or TJ Maxx.
- **No PII at any stage.** The generator emits `customer_id` directly as a 16-char SHA-256 of a never-persisted synthetic PAN. There is no separate anonymization stage and no raw PAN, name, email, or demographic band exists on disk or in the DB.
- **k = 5, not k ≥ 50.** Production-grade aggregate-cell suppression (per the strategy doc §8.2) calls for k ≥ 50. This demo uses k = 5 because the panel is 10,000 customers; with 50 the suppression would eliminate most of the data. The architecture supports any k; only the threshold differs.
- **One of seven agents.** The strategy doc specifies seven specialist personas (§10.2). This demo builds the Conversational Business Advisor as a Merchant Advisor; the other six follow the same architectural pattern and are roadmap.
- **Batch, not streaming.** The strategy doc describes a real-time pipeline (Kafka, Flink, sub-second latency). This demo runs in batch.

## Files

- [`PLAN.md`](./PLAN.md) — build plan with time-boxed blocks
- [`DATA.md`](./DATA.md) — synthetic data specification (panel, schema, generator, anomalies)
- [`ARCHITECTURE.md`](./ARCHITECTURE.md) — strategy-doc mapping and deferred items
- [`V2_5_DATA_DESIGN.md`](./V2_5_DATA_DESIGN.md) — locked target spec for the data layer
- [`docs/V2_5_RECONCILIATION.md`](./docs/V2_5_RECONCILIATION.md) — phased refactor plan from v2 to v2.5
- [`docs/archive/v2_audit.md`](./docs/archive/v2_audit.md) — historical audit of the v2 implementation
- [`CLAUDE.md`](./CLAUDE.md) — conventions and commands for working with Claude Code

## License

(Add your license here — MIT or Apache 2.0 are reasonable defaults for a demo.)
