# Agents

Five user-facing agents currently ship — all merchant-scoped (every
query inherits a viewing-merchant context). The Network Analyst from v2
has been retired. The strategy doc §10.2 specifies seven personas; the
remaining two stay on the v4 roadmap.

- **`orchestrator.py`** — **Conversational Business Advisor.** Routes a
  free-form question to a specialist via a Haiku-based router prompt
  (`prompts/orchestrator.md`), with a keyword-based fallback if the
  router fails. No tool loop here; the orchestrator just classifies and
  dispatches. The dashboard's chat panel calls this for free-form input
  and prepends the routing decision ("Routed to the Pricing & Benchmarking
  Agent…") to the specialist's response.
- **`pricing.py`** — **Pricing & Benchmarking Agent.** Per-SKU pricing,
  category share, peer-relative price gaps. **`MAX_TURNS = 10`**
  (standardized across all specialists in Phase 5.1.5 to 8, then
  bumped to 10 in Phase 5.1.9 to accommodate the analytical
  re-query workflow introduced by chart-takeaway injection).
- **`anomaly.py`** — **Anomaly Detection Agent.** Operational anomalies
  only (no fraud). Knows the three planted signals (University City
  decline, Plaza Midwood avocado spike, pasta-promo divergence) and
  the privacy rule on naming. `MAX_TURNS = 10`.
- **`demand.py`** — **Demand Forecasting & Campaign Adjudication
  Agent.** Slow-mover analysis, campaign attribution, projected promo
  uplift. `MAX_TURNS = 10`.
- **`trade.py`** — **Trade Area Intelligence Agent.** Catchment density,
  underserved neighborhoods, new-store siting. `MAX_TURNS = 10`.

All four specialists subclass **`specialist.py::Specialist`** — the
shared bounded tool loop, the streaming-tokens callback, the caveats
parser, the `SpecialistResponse` dataclass. Tools and SQL guards live
in **`tools.py`** (shared across orchestrator-routed specialists).
Prompts live in **`prompts/<name>.md`** loaded once at module import.

Suggested-question dispatch from the chat panel routes through
`src/dashboard/placeholders.py::dispatch` (which calls
`_llm_dispatch`); free-form input routes through
`placeholders.py::dispatch_orchestrated` (which calls the
orchestrator). The dispatch layer keeps the chat-history shape uniform
across both paths.

The legacy v2 `advisor.py` (`MerchantAdvisor` class) was archived in
Phase 1.5 to `docs/archive/legacy_agent/advisor.py` — the orchestrator
is its v3 replacement. Tests for it moved alongside, file extension
renamed so pytest skips them.

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

- **`MAX_TURNS = 10`.** Hard cap, standardized across all four
  specialists in Phase 5.1.5 to 8, then bumped to 10 in Phase
  5.1.9 to accommodate the analytical re-query workflow introduced
  by chart-takeaway injection. If the loop hasn't terminated,
  return what the agent has and surface "didn't converge" in the
  dashboard. Don't raise without adding a regression test.

- **Final answers must include the SQL.** The dashboard renders it in
  an expander. The agent's answer is not trustworthy without it.

## Style

- **Prompts live in `prompts/*.md`**, never as Python strings. Edit
  them directly. Each specialist's class declares `PROMPT_PATH = Path(__file__).parent / "prompts" / "<name>.md"`;
  the base class reads it once at construction. Dynamic context
  (`{{viewer_id}}`, `{{viewer_name}}`, `{{viewer_segment}}`) is rendered
  via plain string replacement inside the base class, not via Python
  f-strings.
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

Specialists + router both run on `claude-haiku-4-5-20251001`.
Phase 5.1.8 attempted a bump to Sonnet 4.6 to fix
chart-vs-prose contradictions; the smoke confirmed the root
cause was architectural (different analytical windows in
agent SQL vs chart helper), not model capability — addressed
in Phase 5.1.9 via chart-takeaway pre-injection. Haiku
retained for cost (5–10× lower) and latency (2–3× faster).

For unit tests against the real API (rare), prefer
`claude-haiku-4-5` to keep cost down. Most unit tests should mock
the client — see `tests/test_agents.py` for the pattern.

## Mock / fallback mode

The v3 specialists themselves don't have a `mock=True` constructor arg
— that was a v2 affordance on the legacy `MerchantAdvisor` (now
archived). The dashboard's offline safety net is at the **dispatch**
layer in `src/dashboard/placeholders.py`:

- `_llm_dispatch(agent_id, qid, merchant_id, …)` runs the specialist
  via `spec.answer(...)`.
- `_hardcoded_dispatch(agent_id, qid, merchant_id)` returns a canned
  result for the suggested-question id, using Phase 1 placeholder
  handlers registered in `HANDLERS`.
- The dispatch wrapper falls back to `_hardcoded_dispatch` when
  `ANTHROPIC_API_KEY` is missing or the LLM call raises. This keeps
  the dashboard usable as a static demo without API credentials.

When you add a new suggested question, register a `HANDLERS` entry so
the fallback path doesn't surface "No placeholder handler is wired."
Phase 1.5's question-curation pass made all live qids HANDLERS-covered;
keep that property.

# Development workflow notes

## Prompt / class-attribute changes require process restart

Streamlit's hot-reload does NOT reliably pick up changes to:

- Specialist prompt files in `src/agents/prompts/*.md`
- Class attributes like `MAX_TURNS` in `src/agents/{specialist}.py`
- Model identifiers in `src/agents/llm.py`

Python bytecode caching in `__pycache__/` directories can hold
the previous values even after a file save. The visible symptom
is the dashboard behaving as if the change never landed
(stale MAX_TURNS, stale model selection, stale prompt
instructions).

**After editing any of these files:**

1. Stop Streamlit (Ctrl+C in the running terminal)
2. Optionally clear bytecode: `find . -name "__pycache__" -type d -exec rm -rf {} +`
3. Restart: `uv run streamlit run src/dashboard/app.py`

This is especially important during Phase 5 prompt-iteration
work, where small prompt changes need fast turnaround. Build the
restart into your testing loop.
