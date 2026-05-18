# Payments Data Strategy Demo — Build Plan

A 3.5-hour vertical slice of the Core Data Strategy & Solutions architecture. Demonstrates how a payments company can deliver merchants two complementary views — their own granular data and privacy-preserved cross-merchant insights — through AI agents.

> **Status (post-v2.5 refactor):** the original v2 build plan is preserved below for historical context. The codebase has since shipped the v2.5 refactor described in [`V2_5_DATA_DESIGN.md`](./V2_5_DATA_DESIGN.md) and tracked in [`docs/V2_5_RECONCILIATION.md`](./docs/V2_5_RECONCILIATION.md). When PLAN.md and the v2.5 docs disagree, **the v2.5 docs are authoritative**:
>
> - **Panel:** five merchants (Kroger, Acme, Winn-Dixie, Taco Bell, TJ Maxx), 10,000 customers, single Charlotte metro. Network Analyst was retired in Phase 5d.
> - **Architecture:** the lake is virtual — parameterized views in `src/lake/views.py` over the tenant tables. There is no separate `src/anonymize/` stage and no physical `lake_*` tables.
> - **Privacy:** generator emits `customer_id` directly; no PII at any stage; credit + debit only (no EBT, cash, or declines).
> - **Anomalies:** University City decline, Plaza Midwood Kroger avocado spike, coordinated pasta promos. Spec in `DATA.md` §9; demo script in §15 below.
>
> Reading order for current contributors: `README.md` → `ARCHITECTURE.md` → `DATA.md` → `V2_5_DATA_DESIGN.md`. Use this PLAN.md for the demo script (§15) and for git-archaeology context.

---

## 1. What we're building

A runnable demo with three merchants — Kroger (grocery), Taco Bell (QSR), TJ Maxx (off-price retail) — that walks through the four critical layers of the Core Data Strategy at small-data scale:

1. **Capture (synthetic).** Generate plausible transaction events for all three merchants. The same physical customer's `customer_pan` is consistent across merchants — this is the seed for cross-merchant analytics.
2. **Anonymize (dual-path).** Two stages. **Tenant-stage** removes PII (names, emails) and hashes the PAN to `customer_id` — full granularity preserved for the merchant's own use. **Lake-stage** takes tenant output and additionally applies ZIP3 truncation, hour-bucketing, and k-anonymity — used for cross-merchant analytics.
3. **Store.** SQLite database holds two parallel layers: `tenant_*` tables (per-merchant, full granularity) and `lake_*` tables (anonymized cross-merchant aggregate).
4. **Insight.** Two AI agents. **Merchant Advisor** queries both tenant and lake on behalf of a merchant. **Network Analyst** queries the lake only on behalf of a payments-company employee. Streamlit dashboard with a role selector exposes both.

**Definition of done:**
- `make demo` seeds the DB and launches the dashboard.
- The user picks a role (Kroger / Taco Bell / TJ Maxx / Network Analyst) and a question. The agent runs SQL against the appropriate tables and returns a finding with chart and SQL trace.
- Tenant isolation is enforced: when acting as Kroger, the SQL tool refuses to query other merchants' tenant tables.
- `pytest` passes.
- README explains the architecture and the small-data concessions (k=5 anonymity, 5k customers, 90 days, two of seven agents).

**Explicitly out of scope today:**
- The other five agents (Dynamic Pricing, Location Intelligence, Payment Optimization, Anomaly Detection as standalone, Consumer Segmentation as standalone).
- Real-time streaming (Kafka, Flink). Pipeline runs in batch.
- L-diversity, differential privacy beyond a documented stub.
- Authentication, deployment, multi-tenancy enforcement (the demo simulates tenant isolation via SQL filtering, not actual authn/authz).
- Postgres or any DB more complex than SQLite.
- Annual seasonality (90-day window doesn't cover it).
- The entire real-time/hardware/edge story from strategy doc §3–§6.

---

## 2. Time budget

3.5 hours starting at 1:30 PM. Hard stops; cut from the stretch list, not from the next block.

| Block | Time | Goal | Done when |
|---|---|---|---|
| 0. Setup | 1:30 – 1:40 (10m) | Repo scaffolded; `CLAUDE.md`, `DATA.md`, `ARCHITECTURE.md` written | `pytest` runs (zero tests, zero failures); `streamlit hello` serves on `:8501` |
| 1. Multi-merchant data generation | 1:40 – 2:35 (55m) | `python -m src.generate.run_all` produces shared customer panel + per-merchant transactions | Tests pass; cross-merchant PAN invariant verified; EBT-only-at-Kroger verified |
| 2. Dual-path anonymization + DB | 2:35 – 3:20 (45m) | Two-stage anonymization produces `tenant_*` and `lake_*` CSVs; SQLite holds both layers | DB integrity tests pass; cross-merchant query (customers active at ≥2 merchants in lake) returns >0; tenant tables have no NULL home_zip3 from k-anonymity (lake does) |
| 3. Two AI agents | 3:20 – 4:15 (55m) | Merchant Advisor (tenant + lake tools) and Network Analyst (lake only) both work | Both agents answer their role-appropriate canned questions; SQL tool rejects non-SELECT and rejects tenant queries that would cross merchants |
| 4. Role-based dashboard | 4:15 – 4:50 (35m) | Streamlit page with role selector; canned questions per role; agent renders headline + bullets + SQL + chart | All 4 roles work; switching roles changes the agent and the question list |
| 5. Polish + README | 4:50 – 5:00 (10m) | README + ARCHITECTURE.md final; `scripts/demo.sh` works fresh | New clone runs in < 2 minutes |

**Stretch (only if ahead at 4:15):** Demand Forecasting agent · second chart · second cross-merchant question variant.

**Cut order if behind:**
1. Stretch agents.
2. Network Analyst role (keep just the three merchant roles — cross-merchant insights still surface in merchant questions 4 and 5).
3. Agent unit tests (keep gen + anon + DB tests).
4. Drop TJ Maxx (Kroger + Taco Bell still demonstrates cross-merchant).

---

## 3. Architecture

```
   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
   │ generate/    │  │ generate/    │  │ generate/    │   ← strategy §5: Data Capture
   │  kroger.py   │  │ taco_bell.py │  │  tjmaxx.py   │
   └──────┬───────┘  └──────┬───────┘  └──────┬───────┘
          │                 │                 │
          │   raw CSVs (PII present, shared customer_pan across merchants)
          └─────────────────┼─────────────────┘
                            ▼
                ┌───────────────────────┐
                │  anonymize/tenant.py  │   ← strategy §8: Privacy Engine, stage 1
                │  drop name/email      │     (light: PII strip + PAN→hash)
                │  hash PAN→customer_id │
                └─────┬─────────────────┘
                      │ tenant CSVs
                      │ (full granularity, hashed customer_id)
                      ▼
            ┌────────────────────────┐
            │  anonymize/lake.py     │   ← strategy §8: Privacy Engine, stage 2
            │  ZIP5→ZIP3             │     (heavy: aggregate-grade anonymization)
            │  txn_ts→hour bucket    │
            │  k-anonymity (k=5)     │
            └─────┬──────────────────┘
                  │ lake CSVs
                  ▼
        ┌─────────────────────────────┐
        │     db/seed.py → SQLite     │   ← strategy §7+§8.3: dual-path storage
        │  tenant_* tables (per merchant)
        │  lake_*   tables (cross-merchant aggregate)
        └─────────────┬───────────────┘
                      │
            ┌─────────┴─────────┐
            ▼                   ▼
   ┌─────────────────┐   ┌─────────────────┐
   │ Merchant        │   │ Network Analyst │   ← strategy §10: AI Agents
   │ Advisor agent   │   │ agent           │
   │ tools:          │   │ tools:          │
   │  • query_tenant │   │  • query_lake   │
   │  • query_lake   │   │  • schema_info  │
   │  • schema_info  │   │                 │
   └────────┬────────┘   └────────┬────────┘
            │                     │
            └──────────┬──────────┘
                       ▼
              ┌──────────────────┐
              │  Streamlit UI    │
              │  Role selector:  │
              │  Kroger | TBL |  │
              │  TJX | Network   │
              └──────────────────┘
```

**Mapping to the strategy doc:**

| Strategy doc section | Demo equivalent | Concession |
|---|---|---|
| §3–6 Hardware/OS/Edge | Three `generate/*.py` modules produce the unified record | No actual terminal/Kafka/mTLS — data appears already-captured |
| §7 Cloud Data Platform | SQLite + Python loader | No streaming, no schema registry, no hot/warm/cold tiers |
| §8.1–8.2 Privacy techniques | `anonymize/tenant.py` + `anonymize/lake.py` (PAN hash, ZIP3, hour bucket, k=5) | k=5 not k=50; no l-diversity; differential privacy stubbed only |
| **§8.3 Dual-Path Data Isolation** | **`tenant_*` and `lake_*` tables; tenant queries auto-filtered by merchant; lake queries see only the anonymized aggregate** | Tenant isolation enforced via SQL filtering, not actual authn/authz |
| §9 Real-time pipeline | Batch | No latency targets enforced |
| §10 AI Agents | Conversational Business Advisor (Merchant Advisor) + a network-side variant; stretch for Demand Forecasting | 2 of 7 agents |
| §11 Merchant use cases | All three target merchants; canned questions exercise both single-merchant and cross-merchant analytics | Three merchants; small panel |

---

## 4. Tech stack — decisions and rationale

| Choice | Why | Alternative |
|---|---|---|
| Python 3.11+ | Fast iteration, full ecosystem | — |
| uv | ~10× faster than pip | pip + venv |
| SQLite | Zero setup, single file, real relational, handles 1M rows fine | DuckDB; Postgres (overkill) |
| Faker + numpy | Plausible PII before anonymization; distributional sampling | Mimesis; pure random |
| Streamlit | Fastest UI in a single file | Gradio; FastAPI + React |
| Anthropic SDK direct | One tool-use loop is ~30 lines | LangChain (overhead > benefit at this scale) |
| pytest | Standard | unittest |

**Not adding:** Docker, vector DB, ORM, charting library beyond Streamlit's built-ins.

---

## 5. Repository structure

```
payments-data-strategy/
├── CLAUDE.md                         # Project-wide guidance for Claude Code
├── README.md                         # Human-facing: what + how to run
├── PLAN.md                           # This file
├── DATA.md                           # Synthetic data spec (parameters, SKUs, anomalies)
├── ARCHITECTURE.md                   # Strategy-doc mapping; deferred items
├── pyproject.toml
├── .env.example
├── .gitignore
├── Makefile
├── data/
│   ├── raw/                          # PII-bearing CSVs (gitignored)
│   ├── anon/
│   │   ├── tenant/                   # Stage-1 output (gitignored)
│   │   └── lake/                     # Stage-2 output (gitignored)
│   └── payments.db                   # SQLite (gitignored)
├── src/
│   ├── __init__.py
│   ├── config.py
│   ├── generate/
│   │   ├── CLAUDE.md
│   │   ├── __init__.py
│   │   ├── parameters.py             # Global knobs + MERCHANT_CONFIGS (see DATA.md)
│   │   ├── customers.py              # Shared customer panel (PII at this stage)
│   │   ├── base.py                   # Parameterized basket/transaction generator
│   │   ├── catalog_kroger.py
│   │   ├── catalog_taco_bell.py
│   │   ├── catalog_tjmaxx.py
│   │   ├── kroger.py                 # base.py + Kroger config + catalog
│   │   ├── taco_bell.py
│   │   ├── tjmaxx.py
│   │   └── run_all.py                # CLI orchestrator
│   ├── anonymize/
│   │   ├── __init__.py
│   │   ├── hash.py                   # SHA-256 customer_id derivation
│   │   ├── generalize.py             # ZIP3, hour bucketing
│   │   ├── tenant.py                 # Stage 1: drop PII, hash PAN
│   │   ├── lake.py                   # Stage 2: ZIP3, bucket, k-anonymity
│   │   └── pipeline.py               # CLI: runs both stages
│   ├── db/
│   │   ├── schema.sql                # tenant_* and lake_* tables
│   │   ├── seed.py                   # Loads tenant + lake CSVs into SQLite
│   │   └── queries.py                # Canned analytical queries
│   ├── agents/
│   │   ├── CLAUDE.md
│   │   ├── __init__.py
│   │   ├── tools.py                  # query_tenant, query_lake, schema_info, chart_spec
│   │   ├── advisor.py                # Merchant Advisor (uses both tools)
│   │   ├── analyst.py                # Network Analyst (lake only)
│   │   ├── forecaster.py             # (stretch) Demand Forecasting
│   │   └── prompts/
│   │       ├── advisor.md
│   │       ├── analyst.md
│   │       └── forecaster.md
│   └── dashboard/
│       └── app.py                    # Streamlit, role selector
├── tests/
│   ├── test_generation.py
│   ├── test_anonymize.py
│   ├── test_db.py
│   └── test_agents.py
└── scripts/
    ├── demo.sh
    └── reset.sh
```

---

## 6. Synthetic data model

**See `DATA.md` for the full data spec.** Parameters, per-merchant configs, volume math, time/seasonality, customer behavior model, affinity pairs, example SKUs per merchant, planted anomalies, and payment-instrument distributions all live there.

Brief summary for context:
- 5,000 shared customers, 90 days, three merchants.
- ~110,000 transactions / ~1M line items total.
- Generation runtime target: under 30 seconds.
- The customer's `customer_pan` is stable across merchants — this is the seed for the cross-merchant join after hashing.

---

## 7. Anonymization pipeline (dual-path)

This is the strategically critical section. Two stages, two outputs, two purposes.

### 7.1 Stage 1: Tenant processing (`src/anonymize/tenant.py`)

**Purpose:** what each merchant sees about their own data.

Operations, in order:

1. **Drop PII columns.** `customer_name` and `customer_email` are removed entirely. They never appear in tenant output. (In a real payments pipeline these wouldn't have been captured at the terminal in the first place — they live in the customer's head, not in the basket. The demo carries them through generation just so the anonymization stage has something visible to strip.)
2. **Hash the PAN to a stable customer ID.** `customer_id = sha256(HASH_SECRET + customer_pan)[:16]`. Irreversible. Deterministic — same PAN produces same hash, every time.
3. **Keep everything else.** Full timestamps. Full ZIP5. Full basket details. Real prices. Real store IDs. Payment metadata.

**Output:** `data/anon/tenant/transactions.csv`, `tenant/transaction_items.csv`, `tenant/customers.csv`, etc.

### 7.2 Stage 2: Lake processing (`src/anonymize/lake.py`)

**Purpose:** the cross-merchant aggregated view used for benchmarking and cohort analysis.

Operations (applied to tenant-stage output):

1. **Truncate ZIPs.** `customer_home_zip5` → `customer_home_zip3`. `store_zip5` → `store_zip3`. ZIP3 covers ~100,000 people — too broad to identify individuals.
2. **Add hour-bucketed timestamp.** Original `txn_ts` retained on the record; new `txn_hour_bucket` (truncated to the hour) added for aggregate queries.
3. **K-anonymity check.** Group by `(age_band, income_band, home_zip3)`. Any group with fewer than `K_ANONYMITY_THRESHOLD = 5` customers has its `home_zip3` set to NULL (records kept). Log the suppression count.
4. **Differential privacy stub.** A no-op module with a docstring documenting where ε-bounded Laplacian noise would live. Don't build. Reference strategy doc §8.2.

**Output:** `data/anon/lake/customers.csv`, `lake/transactions.csv`, `lake/transaction_items.csv`.

### 7.3 Why this enables cross-merchant insights

The hash function and salt are the same for every merchant. So a given physical customer produces the same `customer_id` whether their transaction was at Kroger, Taco Bell, or TJ Maxx. In the lake tables, you can `JOIN ... USING (customer_id)` across merchants and find the same person. Combined with the additional anonymization, you can analyze cross-merchant patterns without exposing individuals — there's no path back from a row in the lake to a real human.

### 7.4 Demo "moment"

In the demo walkthrough, you can show three layers of progressive privacy:
- `data/raw/customers.csv` — names, emails, full ZIPs visible.
- SQLite `tenant_customers` table — no names, hashed IDs, full ZIP5 still present.
- SQLite `lake_customers` table — same hashed IDs, ZIP3 only, some `home_zip3` nulled by k-anonymity.

---

## 8. Database schema

**SQLite, single file at `data/payments.db`.** CSVs are intermediate workspace files only. Inside SQLite, two parallel table sets share a few common dimensions.

```sql
-- src/db/schema.sql
PRAGMA foreign_keys = ON;

-- ============== Shared dimensions ==============

CREATE TABLE merchants (
    merchant_id   TEXT PRIMARY KEY,             -- 'KRG','TBL','TJX'
    name          TEXT NOT NULL,
    segment       TEXT NOT NULL,                -- 'grocery','qsr','retail_offprice'
    mcc           TEXT NOT NULL
);

-- ============== Tenant layer (per-merchant, full granularity) ==============

CREATE TABLE tenant_customers (
    customer_id        TEXT PRIMARY KEY,
    age_band           TEXT NOT NULL,
    income_band        TEXT NOT NULL,
    home_zip5          TEXT NOT NULL,           -- full ZIP at this layer
    signup_date        DATE NOT NULL,
    primary_card_type  TEXT NOT NULL,
    has_mobile_wallet  INTEGER NOT NULL
);

CREATE TABLE tenant_stores (
    store_id      TEXT PRIMARY KEY,
    merchant_id   TEXT NOT NULL REFERENCES merchants(merchant_id),
    store_zip5    TEXT NOT NULL,                -- full ZIP at this layer
    region        TEXT NOT NULL,
    open_date     DATE NOT NULL
);

CREATE TABLE tenant_products (
    sku            TEXT PRIMARY KEY,
    merchant_id    TEXT NOT NULL REFERENCES merchants(merchant_id),
    name           TEXT NOT NULL,
    category       TEXT NOT NULL,
    subcategory    TEXT NOT NULL,
    is_organic     INTEGER NOT NULL,
    base_price     REAL NOT NULL
);

CREATE TABLE tenant_transactions (
    txn_id          TEXT PRIMARY KEY,
    merchant_id     TEXT NOT NULL REFERENCES merchants(merchant_id),
    customer_id     TEXT NOT NULL REFERENCES tenant_customers(customer_id),
    store_id        TEXT NOT NULL REFERENCES tenant_stores(store_id),
    txn_ts          DATETIME NOT NULL,          -- full timestamp at this layer
    payment_type    TEXT NOT NULL,
    card_network    TEXT,
    entry_mode      TEXT NOT NULL,
    wallet_type     TEXT,
    txn_total       REAL NOT NULL
);

CREATE TABLE tenant_transaction_items (
    txn_id         TEXT NOT NULL REFERENCES tenant_transactions(txn_id),
    line_id        INTEGER NOT NULL,
    sku            TEXT NOT NULL REFERENCES tenant_products(sku),
    qty            INTEGER NOT NULL CHECK (qty > 0),
    unit_price     REAL NOT NULL CHECK (unit_price >= 0),
    discount       REAL NOT NULL DEFAULT 0,
    line_total     REAL NOT NULL,
    PRIMARY KEY (txn_id, line_id)
);

-- ============== Lake layer (cross-merchant, additionally anonymized) ==============

CREATE TABLE lake_customers (
    customer_id        TEXT PRIMARY KEY,
    age_band           TEXT NOT NULL,
    income_band        TEXT NOT NULL,
    home_zip3          TEXT,                    -- ZIP3 only; NULL when k-anonymity suppresses
    signup_date        DATE NOT NULL,
    primary_card_type  TEXT NOT NULL,
    has_mobile_wallet  INTEGER NOT NULL
);

CREATE TABLE lake_transactions (
    txn_id          TEXT PRIMARY KEY,
    merchant_id     TEXT NOT NULL REFERENCES merchants(merchant_id),
    customer_id     TEXT NOT NULL REFERENCES lake_customers(customer_id),
    store_zip3      TEXT NOT NULL,              -- denormalized; no separate lake_stores table
    region          TEXT NOT NULL,
    txn_ts          DATETIME NOT NULL,
    txn_hour_bucket DATETIME NOT NULL,
    payment_type    TEXT NOT NULL,
    card_network    TEXT,
    entry_mode      TEXT NOT NULL,
    wallet_type     TEXT,
    txn_total       REAL NOT NULL
);

CREATE TABLE lake_transaction_items (
    txn_id         TEXT NOT NULL REFERENCES lake_transactions(txn_id),
    line_id        INTEGER NOT NULL,
    sku_category   TEXT NOT NULL,               -- subcategory; SKU-level not retained in lake
    qty            INTEGER NOT NULL,
    unit_price     REAL NOT NULL,
    line_total     REAL NOT NULL,
    PRIMARY KEY (txn_id, line_id)
);

-- ============== Indexes ==============

CREATE INDEX ix_t_txn_customer  ON tenant_transactions(customer_id);
CREATE INDEX ix_t_txn_merchant  ON tenant_transactions(merchant_id);
CREATE INDEX ix_t_txn_store     ON tenant_transactions(store_id);
CREATE INDEX ix_t_txn_ts        ON tenant_transactions(txn_ts);
CREATE INDEX ix_t_items_sku     ON tenant_transaction_items(sku);
CREATE INDEX ix_t_items_txn     ON tenant_transaction_items(txn_id);

CREATE INDEX ix_l_txn_customer  ON lake_transactions(customer_id);
CREATE INDEX ix_l_txn_merchant  ON lake_transactions(merchant_id);
CREATE INDEX ix_l_txn_ts        ON lake_transactions(txn_ts);
CREATE INDEX ix_l_items_txn     ON lake_transaction_items(txn_id);
```

Note the lake intentionally drops SKU-level detail to category level — the lake is for aggregate cross-merchant analytics, not for SKU-detail snooping. SKU-level stays in tenant only. (If a question genuinely needs SKU-level context for the lake, the answer is to add it then — but defaulting to category protects merchants' SKU-level detail from cross-merchant exposure.)

---

## 9. AI agent design

Two agents. Both sized small. Build Merchant Advisor first; Network Analyst is a thinner variant of the same loop.

### 9.1 Tools (`src/agents/tools.py`)

```python
TOOLS_MERCHANT = [
    {"name": "schema_info", "description": "Returns DDL for all tables.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "query_tenant",
     "description": (
         "Execute a read-only SQL SELECT against tenant_* tables. The runner automatically "
         "injects WHERE merchant_id = '<current_merchant>' into your query — you cannot see "
         "other merchants' tenant data. Returns up to 200 rows as JSON."
     ),
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "query_lake",
     "description": (
         "Execute a read-only SQL SELECT against lake_* tables. The lake covers all merchants "
         "but is k-anonymized. Use for cross-merchant benchmarks and aggregates. Returns up to 200 rows."
     ),
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "chart_spec",
     "description": "Declare a chart for the dashboard to render (bar or line).",
     "input_schema": {"type": "object", "properties": {
         "type": {"type": "string", "enum": ["bar", "line"]},
         "x": {"type": "string"}, "y": {"type": "string"}, "title": {"type": "string"}
     }, "required": ["type", "x", "y", "title"]}},
]

TOOLS_ANALYST = [TOOLS_MERCHANT[0], TOOLS_MERCHANT[2], TOOLS_MERCHANT[3]]   # schema, lake, chart
```

### 9.2 Tenant isolation enforcement

In `tools.py::query_tenant`, before executing:

1. Reject the query if it is not a single SELECT (regex check, same as before).
2. Parse the query (use `sqlglot` or a simple regex — sqlglot is overkill for the demo; regex is fine). If the query references any `tenant_*` table without a `merchant_id = '<current_merchant>'` filter or a `JOIN merchants ... WHERE merchants.merchant_id = '<current_merchant>'`, **inject** the filter automatically OR reject the query, depending on simplicity.
3. Recommended for the demo: just inject `WHERE merchant_id = '<x>'` as an `AND`-appended predicate using a simple wrapper:

```python
def query_tenant(sql: str, current_merchant: str) -> list[dict]:
    if not _is_safe_select(sql):
        raise ValueError("Only single SELECT statements allowed.")
    # Wrap in a CTE so the merchant filter is non-bypassable
    wrapped = f"""
        WITH user_query AS ({sql.rstrip(';')})
        SELECT * FROM user_query
        WHERE merchant_id = '{current_merchant}' OR 'merchant_id' NOT IN (
            SELECT name FROM pragma_table_info('user_query')
        )
    """
    # ...
```

The simpler approach for the demo: tell the agent in the system prompt to always include `WHERE merchant_id = '<x>'`, AND have the runner verify the predicate is present, rejecting the query if not. This is auditable.

### 9.3 Merchant Advisor (`src/agents/advisor.py`)

**Job:** answer questions for a specific merchant by combining their own tenant data with cross-merchant lake aggregates as needed.

**System prompt** (full text in `prompts/advisor.md`):
- Role: senior analyst at a payments company, advising the operations team at <current_merchant>.
- Available data (paste DDL).
- Rules: tenant queries see only this merchant; lake queries see anonymized cross-merchant data. Always SELECT, always LIMIT.
- Workflow: decide whether the question needs tenant only, lake only, or both. Run the queries. Synthesize.
- Output: headline finding, bullet detail, SQL of every query you ran in fenced blocks.

**Demo questions when role = Kroger:**
1. *"What are my top categories by revenue last week, and which subcategories drove each?"* → tenant only
2. *"What products are most often bought together with milk in my stores?"* → tenant only
3. *"Have any of my stores seen a drop in transaction count recently?"* → tenant only (finds planted anomaly)
4. *"How does my average basket size compare to other grocery merchants in the panel?"* → tenant + lake
5. *"What share of my customers also shop at QSRs, and how does that affect their behavior at my stores?"* → tenant + lake (the strategic punchline)

Equivalent question lists for Taco Bell and TJ Maxx, scaled to their segments.

### 9.4 Network Analyst (`src/agents/analyst.py`)

**Job:** answer cross-merchant industry questions on behalf of a payments-company employee.

Identical loop, lake tools only, different system prompt (`prompts/analyst.md`).

**Demo questions when role = Network Analyst:**
1. *"How many customers are active across all three merchants in the last 30 days, and what's their average spend at each?"* — lake only
2. *"How do pay-cycle effects differ across grocery, QSR, and retail?"* — lake only
3. *"Which customer segments emerged across merchants this month?"* — lake only (finds the planted "new parents" cohort)

### 9.5 Loop (shared)

```python
def ask(question: str, role: str) -> AgentResponse:
    tools = TOOLS_MERCHANT if role in MERCHANT_ROLES else TOOLS_ANALYST
    system_prompt = ADVISOR_PROMPT if role in MERCHANT_ROLES else ANALYST_PROMPT
    ctx = {"current_merchant": role} if role in MERCHANT_ROLES else {}
    messages = [{"role": "user", "content": question}]
    for _ in range(MAX_TURNS := 6):
        resp = client.messages.create(
            model="claude-opus-4-7", system=system_prompt, tools=tools,
            messages=messages, max_tokens=2048,
        )
        messages.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason == "end_turn":
            return AgentResponse(answer=..., sql=..., rows=..., chart_spec=...)
        # else execute tools with ctx, append tool_result, loop
```

### 9.6 Stretch: Demand Forecasting

If everything is done by 4:15. 7-day rolling mean per SKU with day-of-week adjustment. The model is honestly trivial; the agent's value is *explaining* the forecast (recent trend, seasonality cues, anomalies) using the LLM.

---

## 10. Dashboard

One Streamlit page. Top to bottom:

**1. Title + role selector.** A radio or selectbox: "Acting as: Kroger / Taco Bell / TJ Maxx / Network Analyst". Below it, a one-paragraph context box explaining what the user is currently looking at. Switching role rerenders the question list and reroutes the agent.

**2. Question interface.** A `st.selectbox` of the canned questions for the selected role plus a free-text input. Submit calls the appropriate agent (Merchant Advisor with merchant context, or Network Analyst).

**3. Answer area.** Headline finding (1–2 sentences). Bullet detail (3–5 bullets). An expander labeled "Show the SQL the agent ran" that contains the SQL of every query the agent executed, labeled by tool (tenant or lake).

**4. Data area.** If the agent emitted a `chart_spec` and the last query returned tabular data: `st.dataframe` of the rows, then `st.bar_chart` or `st.line_chart`.

Cache the agent clients with `@st.cache_resource`. No sidebar, no filters, no tabs — the role selector is the only switching control.

---

## 11. CLAUDE.md — content and best practices

Three rules: short and dense; command-heavy not prose-heavy; don't restate the obvious.

### 11.1 Root `CLAUDE.md`

```markdown
# Payments Data Strategy Demo

Synthetic cross-merchant transaction demo demonstrating the four-stage flow from the
parent strategy doc: capture → anonymize (dual-path) → store → insight.

Three merchants — Kroger (grocery), Taco Bell (QSR), TJ Maxx (off-price retail) —
share a 5,000-customer panel over 90 days. The dual-path architecture (strategy
doc §8.3) means the database has TWO parallel layers: tenant_* tables (per-merchant,
full granularity) and lake_* tables (cross-merchant, anonymized).

## Stack
Python 3.11, uv, SQLite, Streamlit, Anthropic SDK, pytest.

## Commands
- `make seed`                              — generate raw, anonymize (both stages), load SQLite
- `make demo`                              — seed + launch dashboard on :8501
- `make test`                              — run pytest
- `make clean`                             — wipe data/ and the DB
- `uv run python -m src.generate.run_all`
- `uv run python -m src.anonymize.pipeline` — runs both tenant.py and lake.py
- `uv run python -m src.db.seed`
- `uv run streamlit run src/dashboard/app.py`

## Conventions
- Generation knobs in `src/generate/parameters.py` only. See DATA.md for spec.
- The shared customer panel comes from `customers.py` — runs ONCE. Each merchant
  generator picks participating customers from this panel. `customer_pan` MUST
  be stable for a given customer across merchants — this is the cross-merchant join key.
- Anonymization is TWO STAGES: `tenant.py` (drop PII, hash PAN) then `lake.py`
  (ZIP3, hour bucket, k=5 anonymity). Each stage's output goes to a different folder.
- DB has tenant_* and lake_* tables. Tenant queries from agents MUST filter by merchant_id;
  this is enforced by `tools.query_tenant` not by SQL views.
- DB writes ONLY via `src/db/seed.py`. Reads via canned queries or agent tools.
- Agents never mutate. SQL tool enforces SELECT-only at the runner level.
- Raw PII (name, email, full PAN) NEVER reaches SQLite. If you find any in the DB, that's a bug.
- One module = one concern. Files under ~200 lines.
- Prompts live in `src/agents/prompts/*.md`, not Python strings.

## Gotchas
- `RANDOM_SEED` from `parameters.py` everywhere — reproducibility is required.
- SQLite needs `PRAGMA foreign_keys = ON` per connection.
- Streamlit reruns on every interaction. Cache agent clients with `@st.cache_resource`.
- The Anthropic API key comes from `.env` via `python-dotenv`.
- k=5 not k=50 (small data; documented in ARCHITECTURE.md).
- EBT only at Kroger. Taco Bell and TJ Maxx have no EBT in their `payment_mix`.

## Out of scope today
Postgres. Auth/authz. Deployment. Real-time streaming (Kafka/Flink).
L-diversity. Differential privacy beyond a stub. Annual seasonality.
The hardware/edge layer from strategy doc §3–§6 is not built — data appears
already-captured at the start of the pipeline.
```

### 11.2 `src/generate/CLAUDE.md` and `src/agents/CLAUDE.md`

(Largely as in earlier draft. Generation CLAUDE.md notes PII is intentional pre-anonymization, customer_pan must be stable across merchants, anomalies are intentional. Agents CLAUDE.md notes the SQL guard, tenant-isolation enforcement, MAX_TURNS = 6, prompts as files.)

### 11.3 Other markdown files in the repo

| File | Purpose | Length |
|---|---|---|
| `README.md` | 30-second pitch + how to run + screenshot placeholder + synthetic-data disclaimer | < 1 page |
| `PLAN.md` | This file | This length |
| `DATA.md` | Synthetic data spec (parameters, configs, volume, SKUs, anomalies) | 2–3 pages |
| `ARCHITECTURE.md` | Strategy-doc mapping, dual-path explanation, deferred items | 1 page |
| `src/agents/prompts/advisor.md` | Merchant Advisor system prompt | ~70 lines |
| `src/agents/prompts/analyst.md` | Network Analyst system prompt | ~50 lines |
| `src/agents/prompts/forecaster.md` | (Stretch) | ~30 lines |

---

## 12. Testing strategy

**`tests/test_generation.py`** (~12 min)
- Generated transactions non-empty per merchant; within configured time window.
- Foreign keys resolve.
- No negative quantities/prices; `line_total ≈ qty * unit_price - discount`.
- Same seed → identical output (deterministic hash check).
- Raw CSVs DO contain PII.
- **Cross-merchant invariant:** the same `customer_pan` appearing at multiple merchants always has the same value.
- **EBT rule:** zero EBT transactions at Taco Bell or TJ Maxx.

**`tests/test_anonymize.py`** (~15 min)
- Tenant CSVs do NOT contain `customer_name` or `customer_email`.
- Tenant `customer_id` is 16 hex chars, deterministic.
- Tenant CSVs retain full ZIP5 and full timestamps.
- Lake CSVs additionally have `home_zip3` (3 chars or NULL), `txn_hour_bucket`, no SKU-level detail (category only).
- Lake k-anonymity: every non-NULL `(age_band, income_band, home_zip3)` group has ≥ 5.
- Lake suppression count > 0 on default seed.
- **Cross-merchant invariant in lake:** the same `customer_pan` produces the same `customer_id` regardless of source merchant.

**`tests/test_db.py`** (~10 min)
- Schema applies cleanly.
- All indexes/FKs present.
- Both tenant and lake tables seed with matching row counts vs CSVs.
- A canned cross-merchant lake query (count distinct customers active at ≥2 merchants) returns > 0.

**`tests/test_agents.py`** (~15 min, only if time permits)
- SQL guard rejects DROP/UPDATE/INSERT/DELETE/ATTACH/multi-statement.
- `query_tenant` rejects or filters queries lacking `merchant_id =` predicate.
- Agent loop terminates within MAX_TURNS with stubbed model.
- Merchant Advisor (mocked) produces a non-empty answer for a tenant-only question.
- Network Analyst (mocked) cannot use `query_tenant` (the tool isn't even in its list).

**Skip:** E2E API tests, Streamlit UI tests, property tests, coverage thresholds, forecaster tests.

---

## 13. Step-by-step Claude Code prompts

**Block 0 (setup, 10m):**
> Initialize a Python project with uv. Create the directory structure from PLAN.md §5. Add a Makefile with `seed`, `demo`, `test`, `clean` targets. Write the root `CLAUDE.md` from §11.1, plus stub `src/generate/CLAUDE.md` and `src/agents/CLAUDE.md`. Create `pyproject.toml` (Appendix A), `.env.example`, and `.gitignore`. Stub `README.md` (one paragraph) and `ARCHITECTURE.md`. Extract PLAN.md §6 placeholder note ("see DATA.md") — actual DATA.md content gets written here too based on what was in our conversation.

**Block 1 (data generation, 55m):**
> Read DATA.md (full data spec). Implement `src/generate/parameters.py` with global params and `MERCHANT_CONFIGS` per DATA.md. Implement `customers.py` (shared 5k panel with PII), `base.py` (parameterized basket generator with bimodal sizing, day/hour shaping, pay-cycle bumps, promo lift, payment sampling, affinity pairs), `catalog_*.py` for all three merchants, `kroger.py` / `taco_bell.py` / `tjmaxx.py` (thin wrappers), `run_all.py` (CLI orchestrator that also injects the three planted anomalies at Kroger). Add `tests/test_generation.py` per PLAN.md §12.

**Block 2 (dual-path anonymization + DB, 45m):**
> Implement `src/anonymize/hash.py` and `generalize.py` (utility funcs). Implement `src/anonymize/tenant.py` (Stage 1: drops customer_name and customer_email entirely; hashes PAN to 16-char `customer_id`; preserves everything else). Implement `src/anonymize/lake.py` (Stage 2: takes tenant output as input; ZIP5→ZIP3; adds `txn_hour_bucket`; downgrades line items to category-level; runs k-anonymity check at k=5 nulling `home_zip3` for too-small groups; logs suppression count). Implement `pipeline.py` as CLI running both stages. Add `tests/test_anonymize.py`.
> Then create `src/db/schema.sql` per PLAN.md §8 (tenant_* and lake_* tables). Implement `src/db/seed.py` to load both layers. Add `tests/test_db.py`. Implement queries in `src/db/queries.py` matching the demo questions in §9.

**Block 3 (agents, 55m):**
> Implement `src/agents/tools.py` with `schema_info`, `query_tenant` (SELECT-only guard + merchant filter enforcement; takes `current_merchant` context), `query_lake` (SELECT-only guard), `chart_spec`. Write `prompts/advisor.md` (per §9.3 — include 1–2 example queries that combine tenant+lake) and `prompts/analyst.md` (per §9.4). Implement `advisor.py` and `analyst.py` sharing the loop in §9.5. Add `tests/test_agents.py`.

**Block 4 (dashboard, 35m):**
> Build `src/dashboard/app.py` per §10. Top: title + role selectbox (Kroger / Taco Bell / TJ Maxx / Network Analyst). On role change, reroute agent and reload canned questions list. Render the agent response: headline, bullets, SQL expander, dataframe + chart if chart_spec. Cache with `@st.cache_resource`. Use `python-dotenv`.

**Block 5 (polish, 10m):**
> Fill in README: 30-second pitch with the dual-path framing, "how to run in 2 minutes", architecture diagram (ASCII from §3), synthetic-data disclaimer, k=5 vs k=50 note, the cross-merchant join callout. Fill in `ARCHITECTURE.md` with the strategy-doc mapping table from PLAN.md §3, the dual-path explanation, and the deferred-items list (real-time, agents 3–7, l-diversity/DP, annual seasonality, hardware layer). Write `scripts/demo.sh`. Verify on a fresh checkout.

---

## 14. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Block 1 over-runs | Medium | High | `base.py` carries shared logic. If at 2:30 not done, drop TJ Maxx. |
| Cross-merchant PAN invariant breaks | Medium | High | Test for it. Keep `customer_pan` generation in `customers.py` only. |
| Tenant isolation enforcement is buggy | Medium | High | Add a test that queries `tenant_transactions` while role=Kroger and asserts zero rows from other merchants come back. |
| K-anonymity suppresses too aggressively | Low | High | Coarsen `age_band`/`income_band` if needed. |
| Agent generates broken SQL | Medium | High | Include 2 example queries in each system prompt. Lower MAX_TURNS to fail fast. |
| Streamlit can't see env var | Low | Medium | `python-dotenv` at top of `app.py`. |
| Anthropic API outage | Low | High | Build `--mock` mode in agents returning canned responses. |
| Tests fail at 4:55 PM | Medium | Low | Mark `xfail`, push, demo, fix tomorrow. |
| Scope creep | High | High | Re-read §1 "out of scope" before any new file. |
| Block 4 dashboard role-switching is finicky | Medium | Medium | Use `st.session_state` for the role; rebuild question list on every render. Don't try to optimize. |

---

## 15. Demo script (3-minute walkthrough at 5:00 PM)

Practice once at 4:55. The script anchors on the three v2.5 planted anomalies — see `DATA.md` §9 for the locked specifications.

1. **Open `data/raw/customers.csv`.** "What `customer_id` looks like in the panel — 16-char SHA-256, no PII at any stage. The same id appears for the same physical customer at every merchant they shop at; that's the cross-merchant join key."
2. **Open SQLite and `SELECT * FROM tenant_customers LIMIT 3`.** "Tenant view: full granularity — full ZIP5, behavioral segment, primary/secondary grocer affinity. This is what the merchant sees of their own customers."
3. **Show the lake is virtual.** Run a tiny `query_lake` from the dashboard's Kroger role. "There's no physical lake table — the runner computes peer-pseudonymized rows from the tenant tables on every query. ZIP3, hour bucket, txn_total bin, no `customer_id`."
4. **Open the dashboard. Role: Kroger.** Run "Have any of my stores seen a drop in transaction count week-over-week?" — the agent surfaces the **University City decline**. Stage 3 (Apr 26 – May 2) is the deepest at Kroger; Acme and Winn-Dixie also drop but less. *"All three grocers' University City stores hit. The pattern is sharper at Kroger because Kroger has the most foot traffic to lose."*
5. **Switch to "How does my dairy pricing compare to peers?"** Two SQL blocks in the expander — one tenant, one lake. "The agent decided to query both layers. The lake answer comes back peer-pseudonymized; we never see the underlying merchant_ids."
6. **Switch role to Acme.** Ask "How is my pasta promo performing?" The agent finds Acme's Apr 19–25 promo lifted spend per pasta basket because of the discount, but **pasta sales count is *down* during the window** — the planted failure. Compare with Kroger (same window-overlap) where pasta sales lift 2.2× during their Apr 15–21 promo.
7. **(Optional, if time)** Switch role to Kroger and ask "Anything strange in Plaza Midwood produce in late April?" The agent surfaces the **avocado spike** — Apr 22 is the peak (5× design multiplier). No comparable spike at Acme or Winn-Dixie Plaza Midwood. *"This is the kind of granular signal the lake hides on purpose — only the merchant viewing their own tenant data can drill into it."*

---

## 16. Stretch / next steps

1. Build the remaining 5 agents from strategy doc §10.2.
2. Add merchants (Shell, CVS, etc.).
3. Replace batch with Kafka + Flink for the real-time path.
4. Production-grade privacy: k=50, l-diversity, ε-bounded differential privacy.
5. Real authn/authz for tenant isolation (instead of SQL filtering).
6. Annual seasonality (DAYS=730, seasonal multipliers).
7. The hardware/edge story (terminal simulator, P2PE ceremony, mTLS to a fake cloud endpoint).

---

## Appendix A — `pyproject.toml`

```toml
[project]
name = "payments-data-strategy"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "anthropic>=0.40",
    "faker>=30",
    "numpy>=2",
    "pandas>=2",
    "python-dotenv>=1",
    "streamlit>=1.39",
]

[dependency-groups]
dev = ["pytest>=8", "ruff>=0.7"]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-v --tb=short"
```

## Appendix B — `Makefile`

```makefile
.PHONY: seed demo test clean

seed:
	uv run python -m src.generate.run_all
	uv run python -m src.anonymize.pipeline
	uv run python -m src.db.seed

demo: seed
	uv run streamlit run src/dashboard/app.py

test:
	uv run pytest

clean:
	rm -rf data/raw data/anon data/*.db
	mkdir -p data/raw data/anon/tenant data/anon/lake
```

## Appendix C — Sample `prompts/advisor.md`

```markdown
You are a senior analyst at a payments company, advising the operations team at
{{current_merchant}}. You answer questions by writing read-only SQL against two
data layers:

- **Tenant tables** (`tenant_*`): {{current_merchant}}'s own data, full granularity.
  Always include `WHERE merchant_id = '{{current_merchant_id}}'` in tenant queries.
  The runner enforces this; queries without the filter will be rejected.
- **Lake tables** (`lake_*`): anonymized cross-merchant aggregate. Use for industry
  benchmarks and cross-merchant context. K-anonymity (k=5) is applied; some
  records may have NULL home_zip3.

# Schema
(paste contents of src/db/schema.sql here)

# Decision: which layer?
- Single-merchant operational question → tenant only
- Industry benchmark or peer comparison → tenant + lake (run both, synthesize)
- "How do my customers behave at other merchants?" → tenant for who they are, lake for what they do elsewhere

# Examples

Q: "How does my basket size compare to grocery peers?"
1. query_tenant("SELECT AVG(items_per_txn) FROM (SELECT txn_id, COUNT(*) AS items_per_txn
   FROM tenant_transaction_items GROUP BY txn_id) WHERE ...")
2. query_lake("SELECT m.name, AVG(items) FROM lake_transactions JOIN merchants m USING(merchant_id)
   WHERE m.segment = 'grocery' GROUP BY m.merchant_id")

Q: "What share of my customers also shop at QSRs?"
1. query_tenant: get distinct customer_ids of {{current_merchant}}.
2. query_lake: of those customer_ids (passed as a SQL IN clause from result 1), how many appear with
   a QSR-segment merchant in lake_transactions?

# Rules
1. Always single SELECT, always LIMIT.
2. Never INSERT/UPDATE/DELETE/DROP/ATTACH or multi-statement.
3. Up to 6 tool turns. If stuck, say so honestly.
4. Cite numbers from queries, not memory. Empty result → say so.
5. Final answer format: headline (1–2 sentences), 3–5 bullets, every SQL query in a
   fenced ```sql block labeled by tool (tenant or lake).
6. Don't recommend actions the merchant didn't ask for.
```

## Appendix D — Sample `prompts/analyst.md`

```markdown
You are a payments-network analyst examining cross-merchant patterns across a
small panel of merchants (currently Kroger, Taco Bell, TJ Maxx).

You have one query tool: `query_lake`. You CANNOT see any individual merchant's
proprietary tenant data — only the anonymized lake. The lake has:
- Hashed customer_ids consistent across merchants (the cross-merchant join key)
- ZIP3 only; some NULL where k-anonymity (k=5) suppressed
- Hour-bucketed timestamps alongside raw ones
- Category-level line items, not SKU

# Schema
(paste lake_* tables and merchants table)

# Strategic framing
The payments network's unique vantage point is seeing the same customer across
multiple merchants. Lean into that in your analyses — questions that no single
merchant and no card network alone could answer.

# Rules
Same SELECT-only / LIMIT / 6-turn rules as the merchant advisor. Same output format.
Never claim to know individuals — the data does not support it.
```
