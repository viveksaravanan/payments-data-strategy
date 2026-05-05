# Payments Data Strategy Demo

Synthetic cross-merchant transaction demo. Demonstrates the four-stage flow from the parent strategy doc: capture → anonymize (dual-path) → store → insight.

Three merchants — Kroger (grocery), Taco Bell (QSR), TJ Maxx (off-price retail) — share a 5,000-customer panel over 90 days. The dual-path architecture (strategy doc §8.3) means the database has two parallel layers: `tenant_*` tables (per-merchant, full granularity) and `lake_*` tables (cross-merchant, anonymized).

## Stack
Python 3.11, uv, SQLite, Streamlit, Anthropic SDK, pytest. No frameworks beyond these.

## Commands
- `make seed`      — generate raw, anonymize (both stages), load SQLite
- `make demo`      — seed + launch dashboard on `:8501`
- `make test`      — run pytest
- `make clean`     — wipe `data/` and the DB
- `uv run python -m src.generate.run_all`        — generation only
- `uv run python -m src.anonymize.pipeline`      — both anonymization stages
- `uv run python -m src.db.seed`                 — DB load only
- `uv run streamlit run src/dashboard/app.py`    — dashboard only

## Conventions

**Generation:**
- All knobs in `src/generate/parameters.py`. See `DATA.md` for the spec.
- Shared customer panel comes from `customers.py` — runs ONCE. Each merchant generator picks participating customers from this panel.
- `customer_pan` MUST be stable for a given customer across merchants. This is the cross-merchant join key. Tested in `tests/test_generation.py`.
- PII (`customer_name`, `customer_email`) is INTENTIONAL in raw data so the anonymization stage has something to strip. Never anonymize in `generate/`.

**Anonymization (two stages):**
- Stage 1, `tenant.py`: drop PII columns, hash PAN to `customer_id`. Full granularity preserved otherwise.
- Stage 2, `lake.py`: takes tenant output, additionally truncates ZIP5→ZIP3, adds `txn_hour_bucket`, downgrades line items to category-level, runs k=5 anonymity check.
- Each stage's output goes to a different folder: `data/anon/tenant/` and `data/anon/lake/`.

**Database:**
- SQLite, single file at `data/payments.db`.
- Two parallel table prefixes: `tenant_*` and `lake_*`.
- DB writes ONLY via `src/db/seed.py`. Reads via canned queries or agent tools.
- Raw PII NEVER reaches SQLite. If you find `customer_name`/`customer_email`/full PAN in the DB, that's a bug.

**Agents:**
- Two agents: `advisor.py` (Merchant Advisor — uses tenant + lake) and `analyst.py` (Network Analyst — lake only).
- Tool definitions in `tools.py`. Prompts in `prompts/*.md` (loaded as files, never inlined as Python strings).
- The SQL tools enforce SELECT-only at the runner level. Tenant tool additionally requires `WHERE merchant_id = '<x>'` predicate; rejects queries lacking it.
- Agents NEVER mutate the DB. `MAX_TURNS = 6`.

**Code:**
- One module = one concern. Files under ~200 lines.
- Final answers must include the SQL the agent ran. The dashboard depends on this.

## Gotchas
- Pass `RANDOM_SEED` from `parameters.py` everywhere — reproducibility is required and tested.
- SQLite needs `PRAGMA foreign_keys = ON` per connection. Done in `seed.py`; do it anywhere else you open a connection.
- Streamlit reruns the whole script on every interaction. Cache agent clients with `@st.cache_resource`.
- The Anthropic API key comes from `.env` via `python-dotenv`. Never hardcode.
- k=5 not k=50 (small data; documented in `ARCHITECTURE.md`).
- EBT only at Kroger. Taco Bell and TJ Maxx have no EBT in their `payment_mix`. Tested.

## Out of scope today
Postgres. Auth/authz. Deployment. Real-time streaming (Kafka/Flink). L-diversity. Differential privacy beyond a documented stub. Annual seasonality. The hardware/edge layer from strategy doc §3–§6 — data appears already-captured at the start of the pipeline.

## File guide
- `PLAN.md` — build plan with time-boxed blocks and the demo script
- `DATA.md` — synthetic data specification
- `ARCHITECTURE.md` — strategy-doc mapping and deferred items
- `src/generate/CLAUDE.md` — generation-specific conventions
- `src/agents/CLAUDE.md` — agent-specific conventions
