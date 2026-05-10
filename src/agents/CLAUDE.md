# Agents

The advisor is the only merchant-scoped agent currently shipping. The
strategy doc §10.2 specifies seven personas; specialists beyond the
Conversational Business Advisor pattern are deferred. The Network
Analyst from v2 has been retired — all v2.5 agents are merchant-scoped
(every query inherits a viewing-merchant context).

- **`advisor.py`** — Merchant Advisor. Tools: `schema_info`,
  `query_tenant`, `query_lake`, `chart_spec`. Used by all five merchant
  roles in the dashboard. Receives `current_merchant_id` at construction
  time; the runner passes it into both tenant and lake tool calls.

The shared loop is in the agent file rather than abstracted out — the
duplication is small and keeps the loop legible.

## Hard rules

- **`query_tenant` enforces tenant isolation.** Every query must
  include `WHERE merchant_id = '<current_merchant>'` (or join on
  `merchants` with the same predicate). Queries lacking the predicate
  are rejected before execution. The check lives in
  `tools.query_tenant`. If you modify it, update
  `tests/test_agents.py::test_tenant_isolation`.

- **`query_lake` runs against view-builders, not physical tables.**
  The lake exposes exactly two logical tables: `lake_transactions`
  (21 columns; one row per peer line item) and `lake_stores` (6
  columns; peer store reference). Both are computed at query time
  from the tenant tables via `src.lake.views.get_lake_*` — there are
  no physical `lake_*` tables in v2.5 (Phase 5c removes the v2 ones
  that still exist as a safety net).

  The runner takes the agent's SELECT, validates it, and prepends two
  CTEs (`WITH lake_transactions AS (...), lake_stores AS (...)`) that
  shadow the physical tables and bake in the viewing merchant. The
  agent writes ordinary SQL like `SELECT peer_id, AVG(unit_price) FROM
  lake_transactions WHERE category = 'DAIRY' GROUP BY peer_id`; the
  runner rewrites it transparently.

  Lake-tool rejections (all enforced before any DB connection opens):
  - Anything that isn't a single SELECT (no semicolons, no DDL/DML).
  - References to any v2-era physical lake table that isn't part of
    the v2.5 virtual model (the runner maintains the rejection list).
  - References to `tenant_*` tables — those go through `query_tenant`.
  - Queries that don't reference at least one of `lake_transactions`
    or `lake_stores`.

  The viewing merchant's own data is excluded automatically; peers
  are pseudonymized as `peer_a`..`peer_d` per the locked Phase 2
  mapping (`V2_5_DATA_DESIGN.md` lines 957–970). The agent never sees
  underlying merchant_ids; `customer_id` is dropped from lake output
  per "no consumer linkage".

- **All SQL tools are SELECT-only.** Reject anything that is not a
  single SELECT statement before executing — regex check, before any
  DB connection. Never trust the model to self-restrict.

- **`MAX_TURNS = 6`.** Hard cap. If the loop hasn't terminated, return
  what the agent has and surface "didn't converge" in the dashboard.
  Don't raise without adding a regression test.

- **Final answers must include the SQL.** The dashboard renders it in
  an expander. The agent's answer is not trustworthy without it.

## Style

- **Prompts live in `prompts/*.md`**, never as Python strings. Edit
  them directly. Load with `Path("prompts/advisor.md").read_text()` at
  module import. No f-string interpolation in prompts — pass dynamic
  context (current_merchant, today's date) as a separate user message.
- **Tools are real-Python, not LLM-described.** A tool is a function
  that the runner invokes when the model emits a `tool_use` block; the
  runner appends the result as a `tool_result`. No wrapper frameworks.
- **Keep the loop boring.** The hardest debugging in this code happens
  when the loop does something clever. It shouldn't.

## Lake schema reference (what the agent sees)

```
lake_transactions (21 columns)
  lake_txn_id           opaque 16-char id
  line_id               1, 2, 3, ...
  peer_id               'peer_a'/'peer_b'/'peer_c'/'peer_d'
  peer_segment          'grocery'/'qsr'/'off_price_retail'
  lake_store_id         opaque 16-char id (FK to lake_stores)
  txn_date              YYYY-MM-DD
  txn_hour_bucket       early_morning / morning / mid_morning / lunch /
                        afternoon / late_afternoon / evening / dinner /
                        late_evening / late_night
  payment_type          'credit' / 'debit'
  card_network          'visa' / 'mc' / 'amex' / 'discover'
  entry_mode            'contactless' / 'chip' / 'swipe' / 'manual'
  wallet_type           nullable: 'apple' / 'google' / 'samsung'
  connectivity_type     'wifi' / 'cellular_4g' / 'cellular_5g' / 'ethernet'
  txn_total_bin         '$0-5' / '$5-10' / '$10-20' / '$20-35' /
                        '$35-50' / '$50-75' / '$75-100' / '$100-150' /
                        '$150-250' / '$250+'
  canonical_name        product name (shared across grocers)
  category              top-level category
  subcategory           subcategory
  unit_price, qty       carried precisely from tenant
  discount, line_total  carried precisely
  discount_pct_applied  nullable: discount / (unit_price * qty)

lake_stores (6 columns)
  lake_store_id         opaque 16-char id
  peer_id               'peer_a'..'peer_d'
  peer_segment          carried from peer's segment
  store_zip3            ZIP3 only
  neighborhood          carried unchanged
  metro_region          'urban_core'/'inner_suburbs'/'outer_suburbs'
```

## Models

Default to `claude-opus-4-7`. For unit tests against the real API
(rare), use `claude-haiku-4-5` to keep cost down. Most unit tests
should mock the client — see `tests/test_agents.py` for the pattern.

## Mock mode

Each agent supports `--mock` flag (or `mock=True` constructor arg). In
mock mode, the agent skips API calls and returns canned responses. This
is the demo safety net: if the API key is missing or there's an outage,
the dashboard still works.

The canned responses live as constants in each agent file. Update them
when you update the demo questions or the lake schema (so the canned
SQL stays runnable).
