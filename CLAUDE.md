# Payments Data Strategy Demo

Synthetic cross-merchant transaction demo. Models **Verifone** at the
intersection of POS data and payment data — observing baskets, payment fields,
and merchant context across an installed base of merchants.

The locked target for the data layer is `V2_5_DATA_DESIGN.md`. Read it
before changing generator behavior, schema, or lake mechanics. The
incremental refactor from v2 is tracked in `docs/V2_5_RECONCILIATION.md`.

## The panel

Five merchants in a single fictional metro modeled on Charlotte, NC:

| Merchant | Segment | Stores |
|---|---|---|
| Kroger (KRG) | grocery | 30 |
| Acme (ACM) | grocery | 25 |
| Winn-Dixie (WDX) | grocery | 20 |
| Taco Bell (TBL) | qsr | 40 |
| TJ Maxx (TJX) | off_price_retail | 8 |

10,000 customers shared across the panel. 90-day window:
**March 1, 2026 → May 29, 2026** (covers Easter Apr 5 and Memorial Day May 25).

## Stack

Python 3.11, uv, SQLite, Streamlit, Anthropic SDK, pytest. No frameworks
beyond these.

## Commands

- `make seed`      — generate raw + load SQLite
- `make demo`      — seed + launch dashboard on `:8501`
- `make test`      — run pytest
- `make clean`     — wipe `data/` and the DB
- `uv run python -m src.generate.run_all`        — generation only
- `uv run python -m src.db.seed`                 — DB load only
- `uv run streamlit run src/dashboard/app.py`    — dashboard only

## Conventions

**Generation:**
- All knobs in `src/generate/parameters.py`. See `DATA.md` for the spec
  and `V2_5_DATA_DESIGN.md` Layer 4 for the generator algorithm.
- Shared customer panel comes from `customers.py` — runs ONCE. Each
  merchant generator picks participating customers from this panel,
  weighted by `primary_grocer` / `secondary_grocer` / affinity type.
- `customer_id` MUST be stable for a given physical customer across
  merchants. It's a 16-char SHA-256 hash, generated directly in
  `customers.py`. Tested in `tests/test_generation.py`.
- No raw PAN, no `customer_name`, no `customer_email` — generator never
  produces PII at any stage. There is no separate anonymization step.
- No EBT, no cash, no declines — credit and debit only per strategy doc
  §5.2 captured rails.

**Privacy / lake:**
- The lake is **virtual** — implemented as parameterized query functions
  in `src/lake/views.py` over the tenant tables. There are no physical
  `lake_*` tables in SQLite.
- The lake **excludes the viewing merchant**. When Kroger queries the
  lake, it sees the other four pseudonymized as `peer_a`..`peer_d`.
- Privacy mechanisms (per `V2_5_DATA_DESIGN.md` Phase 2): generalization
  (ZIP5→ZIP3, timestamp→date+2hr-bucket, txn_total→10-bin), k=5 cell
  suppression on aggregate customer-dimension queries, and **suppression
  of consumer linkage** — `customer_id` is dropped from lake output per
  strategy doc §5.2 ("product-level; no consumer linkage").

**Database:**
- SQLite, single file at `data/payments.db`.
- Tenant tables only: `merchants`, `tenant_customers`, `tenant_stores`,
  `tenant_products`, `tenant_promotions`, `tenant_transactions`,
  `tenant_transaction_items`. No `lake_*` tables.
- DB writes ONLY via `src/db/seed.py`. Reads via canned queries or agent
  tools.
- No PII reaches SQLite. If you find names/emails/PAN in the DB, that's
  a bug.

**Agents:**
- Single agent today: `advisor.py` (Merchant Advisor — uses tenant +
  lake views, scoped to a `current_merchant_id`). The strategy doc
  specifies seven merchant-scoped agent personas; specialists beyond
  the Merchant Advisor are deferred.
- Tool definitions in `tools.py`. Prompts in `prompts/*.md` (loaded as
  files, never inlined as Python strings).
- The SQL tools enforce SELECT-only at the runner level. The tenant
  tool requires `WHERE merchant_id = '<current_merchant>'`. The lake
  tool runs SQL through `src/lake/views.py::get_lake_*` with the
  viewing merchant threaded in — it cannot reference physical
  `lake_*` tables (there are none).
- Agents NEVER mutate the DB. `MAX_TURNS = 6`.

**Code:**
- One module = one concern. Files under ~200 lines.
- Final answers must include the SQL the agent ran. The dashboard
  depends on this.

## Gotchas

- Pass `RANDOM_SEED` from `parameters.py` everywhere — reproducibility
  is required and tested.
- SQLite needs `PRAGMA foreign_keys = ON` per connection. Done in
  `seed.py`; do it anywhere else you open a connection.
- Streamlit reruns the whole script on every interaction. Cache agent
  clients with `@st.cache_resource`.
- The Anthropic API key comes from `.env` via `python-dotenv`. Never
  hardcode.
- k=5 not k=50 (panel size; documented in `ARCHITECTURE.md`).

## Out of scope today

Postgres. Auth/authz. Deployment. Real-time streaming (Kafka/Flink).
L-diversity explicit verification. Differential privacy beyond a
documented stub. Annual seasonality. EBT, cash, declined transactions.
Demographics (age/income bands). The hardware/edge layer from strategy
doc §3–§6 — data appears already-captured at the start of the pipeline.
Specialist agent personas beyond the single Merchant Advisor.

## File guide

- `V2_5_DATA_DESIGN.md` — locked source of truth for the data layer
  (panel, schema, generator, lake views, anomalies).
- `docs/V2_5_RECONCILIATION.md` — phased plan that tracked the v2 → v2.5
  refactor (complete; kept for context).
- `docs/archive/v2_audit.md` — historical audit of the pre-v2.5 codebase
  (kept for context).
- `PLAN.md` — build plan with time-boxed blocks and the demo script.
- `DATA.md` — synthetic data specification (output side).
- `ARCHITECTURE.md` — strategy-doc mapping and deferred items.
- `README.md` — top-level overview for first-time readers.
- `src/generate/CLAUDE.md` — generation-specific conventions.
- `src/agents/CLAUDE.md` — agent-specific conventions.
