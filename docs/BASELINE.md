# BASELINE — How the repo works today

**Date written:** 2026-05-30. **Branch:** main (HEAD = `93db1b2`). **Read-only baseline.**

This document describes the codebase as it exists today, with `file:line` citations so any claim can be spot-checked. It is descriptive, not prescriptive — section 10 lists observations about fragility, but proposes no fixes.

For anything that could not be determined from static reading, the doc says so explicitly rather than guessing.

---

## 1. Overview

This is a single-process Streamlit demo of a synthetic cross-merchant payments dataset, modeled after Verifone's hypothetical position at the join of POS basket data and payment data. Five fictional merchants share a 10,000-customer panel across a fictional Charlotte metro for a 90-day window (2026-03-01 → 2026-05-29). The pipeline is fully reproducible from `RANDOM_SEED = 42` in `src/generate/parameters.py:20`.

End-to-end flow:

1. **Generate** (`src/generate/run_all.py:main`) — Builds the customer panel (`customers.py`), the five per-merchant catalogs/stores/promotions (`kroger.py`, `acme.py`, `winn_dixie.py`, `taco_bell.py`, `tjmaxx.py`), and the unified transaction stream (`transactions.py`). Writes 7 CSVs to `data/raw/`. No PII is ever materialized — `customer_id` is the first 16 hex chars of `sha256("demo-only-not-a-real-secret" + synthetic_pan)` and the PAN is never written.
2. **Load** (`src/db/seed.py:main`) — Recreates `data/payments.db` from `src/db/schema.sql`, loads the 7 CSVs into 7 physical `tenant_*` tables, then **materializes the privacy lake at seed time** as ten per-viewer physical tables (`lake_transactions_<M>` and `lake_stores_<M>` for each of the five merchants).
3. **Anonymize** (`src/lake/views.py`) — The lake materialization applies SHA-256 tokenization of `txn_id` and `store_id`, ZIP5→ZIP3 generalization, full timestamp → date+10-bucket time-of-day, `txn_total` → 10-bin label, viewing-merchant exclusion, and peer pseudonymization to `peer_a..peer_d`. `customer_id` is dropped entirely from the lake ("no consumer linkage"). A separate k=5 cell-suppression wrapper runs at agent query time against count-like columns.
4. **Serve queries** (`src/agents/`) — Five agents: an `Orchestrator` (free-form question → Haiku-based router) and four `Specialist`s (`PricingSpecialist`, `AnomalyDetectionSpecialist`, `DemandForecastingSpecialist`, `TradeAreaSpecialist`). All four specialists share `specialist.py::Specialist` — a bounded tool loop (`MAX_TURNS = 10`) over four tools: `schema_info`, `query_tenant`, `query_lake`, `make_chart`. All LLM calls go to `claude-haiku-4-5-20251001`.
5. **Render** (`src/dashboard/`) — A Streamlit app (`app.py`) renders KPI strip, geography, catalog, and customer sections plus a chat panel overlay (`chat.py`). The chat panel dispatches suggested questions through `agents.py::dispatch` (cached per session) or free-form questions through `agents.py::dispatch_orchestrated`. Agent responses are rendered as prose (markdown) + an agent-produced Plotly chart + a results table + a caveats bullet list, with a *separately computed* dashboard "pattern chart" for the question id (sourced from independent SQL in `data.py`).

The lake "is virtual" rhetorically but **physical at runtime**: per Phase 1.5 (V3), the materialized per-viewer tables sit in the same SQLite file. The agent never references them by physical name — `tools.py::query_lake` CTE-wraps the agent's SQL so unqualified `lake_transactions` / `lake_stores` references resolve to the viewer's pre-baked tables.

---

## 2. File & directory map

### Generation (`src/generate/`, 38 Python files, ~2,600 lines including catalogs)

| File | Lines | Role |
|---|---|---|
| `parameters.py` | 452 | Every tunable knob: `RANDOM_SEED`, `HASH_SECRET`, panel size, dates, geography, affinity shares, trip frequency tables, calendar multipliers, basket archetypes, payment-mix distributions, per-merchant biases. |
| `run_all.py` | 102 | Orchestrator: customers → 5 merchant builders → unified transactions → write 7 CSVs to `data/raw/`. |
| `customers.py` | 112 | Builds the shared 10,000-customer panel. Generates `customer_id` directly from a synthetic-PAN SHA-256 (never persisted). |
| `metro.py` | 126 | Charlotte-metro geography: ZIP5→neighborhood, ZIP centroid + jitter, segment+tier weighted store ZIP assignment with required-ZIPs override for anomaly anchors. |
| `transactions.py` | 956 | Unified Layer 4 transaction generator. Owns trip-frequency sampling, active-weeks Dirichlet split, calendar multiplier (pay-cycle + peak-day + peak-hour), per-trip merchant choice, store choice, archetype, basket size, per-line SKU sampling with affinity rules, payment-method draw. Anomaly hooks live here. |
| `kroger.py` / `acme.py` / `winn_dixie.py` | 79 / 57 / 57 | Three thin grocer wrappers — each calls catalog → stores → promotions and reserves a slot for the pinned pasta promo. |
| `taco_bell.py` / `tjmaxx.py` | 44 / 44 | QSR and retail equivalents. |
| `catalog_grocery.py` | 175 | Base+overlay model: loads `data/catalogs/base_grocery_catalog.json` (1,112 canonical SKUs), filters per overlay, applies tier multiplier and ±2% noise. Affinity rule registration. |
| `catalog_acme.py` / `catalog_winn_dixie.py` | 15 / 15 | Per-grocer overlay loaders. |
| `catalog_taco_bell.py` | 85 | QSR catalog: 60 SKUs across 7 categories. Affinity rules (entree→drink, combo→sweet). |
| `catalog_tjmaxx.py` | 77 | Retail catalog: 200 SKUs across 8 categories. Affinity rules (apparel→accessory, kitchen→home). |
| `promotions.py` | 220 | Per-segment promo generation. KRG 25 / ACM 20 / WDX 18 / TBL 6 / TJX 4 campaigns. |
| `tax.py` | 65 | Five-tier tax model (0% exempt grocery / 4% snacks+beverages / 7% non-food+prepared). |
| `anomalies/university_city_decline.py` | — | 4-stage keep-probability multiplier on UC ZIPs for all three grocers. |
| `anomalies/plaza_midwood_avocado.py` | — | 4-day daily multiplier on avocado SKUs at Kroger Plaza Midwood. |
| `anomalies/acme_pasta_promo.py` | — | Pinned per-grocer pasta promo + basket-level pasta multiplier (KRG lift, ACM failure, WDX modest lift). |
| `CLAUDE.md` | — | Generation-specific conventions; accurate as of read. |

### Lake / anonymization (`src/lake/`, 3 files, ~485 lines)

| File | Lines | Role |
|---|---|---|
| `views.py` | 399 | Lake templates and helpers. Defines `HOUR_BUCKETS` (10 buckets), `TOTAL_BINS` (10 bins), `K_ANONYMITY_K = 5`, salt `"v2.5-lake-opaque-salt"`. Exports `generate_opaque_id`, `to_hour_bucket`, `to_total_bin`, `apply_k_anonymity`, `register_lake_functions`, `lake_transactions_sql`, `lake_stores_sql`, `_build_lake_transactions_sql` (seed-time), `_build_lake_stores_sql` (seed-time), `get_lake_transactions`, `get_lake_stores`. |
| `peer_mapping.py` | 56 | Build `{merchant_id: peer_label}` and a SQLite CASE expression keyed off `parameters.PEER_MAPPING`. |
| `__init__.py` | 30 | Public surface re-exports. |

### Storage (`src/db/`, 4 files)

| File | Lines | Role |
|---|---|---|
| `schema.sql` | 194 | Seven physical `tenant_*` tables + 8 indexes + 30 per-viewer `tenant_view_<M>_*` views (5 merchants × 6 tables). Lake tables are **not** declared here. |
| `seed.py` | 171 | Resets the DB file, applies the schema, loads CSVs in FK-correct order, then materializes the 10 per-viewer lake tables (`lake_transactions_<M>`, `lake_stores_<M>`) for each of 5 viewers. |
| `queries.py` | 173 | Five reference SQL helpers — `top_categories_by_revenue_last_week`, `items_co_purchased_with`, `store_dropouts_last_7_days`, `my_basket_size_and_grocery_peer_basket_size`, `my_dairy_pricing_vs_peers`. Could not find any importer in `src/dashboard/` or `src/agents/` that consumes these — possibly dead code retained as documentation. |

### Agents (`src/agents/`, 9 Python files + 5 prompts, ~1,800 lines)

| File | Lines | Role |
|---|---|---|
| `specialist.py` | 324 | Base class. Tool loop, caveats parser, `SpecialistResponse` dataclass, `DEFAULT_MAX_TURNS = 10`, `MAX_TOKENS = 4096`. |
| `orchestrator.py` | 347 | `Orchestrator.ask`, `route()` (single Haiku call), `_keyword_route()` fallback, segment-conditional default specialist map (grocer→anomaly, qsr→demand, retail→pricing). |
| `pricing.py` | 20 | `PricingSpecialist(AGENT_LABEL="Pricing & Benchmarking Agent", PROMPT_PATH=prompts/pricing.md)`. |
| `anomaly.py` | 20 | `AnomalyDetectionSpecialist`. |
| `demand.py` | 20 | `DemandForecastingSpecialist`. |
| `trade.py` | 21 | `TradeAreaSpecialist`. |
| `tools.py` | 694 | Tool schemas, guards (`is_safe_select`, `has_merchant_predicate`, lake validators), CTE wrapping, `_exec_select`, `_maybe_suppress_sub_k` (k=5), `make_chart` (Plotly Figure builder), `trim_for_llm`. |
| `llm.py` | 262 | Anthropic client wrapper, `MODEL_ROUTER` and `MODEL_SPECIALIST` constants, `call_with_tools` + `call_with_tools_streaming`, `CallTelemetry`, cost accounting. |
| `context.py` | 72 | `MerchantContext` — immutable per-viewer binding with bound `query_tenant`, `query_lake`, `make_chart`, `schema_info` methods. |
| `prompts/orchestrator.md` | 82 | Router prompt. |
| `prompts/pricing.md` | — | Pricing specialist persona + tool usage rules + caveats fence format. |
| `prompts/anomaly.md` | — | Anomaly specialist; knows the three planted signals. |
| `prompts/demand.md` | — | Demand specialist; date literals are hardcoded (2026 window). |
| `prompts/trade.md` | — | Trade specialist; cross-segment peers allowed. |
| `CLAUDE.md` | — | Agent-specific conventions — **stale in one place** (see §10): references `src/dashboard/placeholders.py::_llm_dispatch` and `_hardcoded_dispatch`, but the file is now `src/dashboard/agents.py` with `dispatch` / `dispatch_orchestrated` / `_run_specialist`. |

### Dashboard (`src/dashboard/`, 10 Python files, ~10,200 lines)

| File | Lines | Role |
|---|---|---|
| `app.py` | 229 | Streamlit entry: page config, merchant selectbox, filter row, five vertical sections, chat overlay invocation. Owns `st.session_state` keys: `merchant_id`, `filters_by_merchant`, `chat_messages_by_merchant`, `active_agent`, `chat_state`. |
| `chat.py` | 1,261 | Chat overlay rendering. Owns suggested-question pill render, free-form form, live-turn render with progress + token streaming, prose/chart/table/caveats render, per-qid pattern-chart render. |
| `agents.py` | 252 | Dispatch layer. `dispatch(agent_id, qid, merchant_id, ...)` (cached), `dispatch_orchestrated(merchant_id, raw_question, ...)` (uncached), `_run_specialist`, error response builders. |
| `data.py` | 4,202 | Cached SQL helpers for dashboard KPIs, sparklines, and pattern-chart data sources. P1 = `category_peer_pricing_gaps`, P3 = `category_pricing_leverage`, etc. All `@st.cache_data` keyed by `(merchant_id, filters)`. |
| `views.py` | 767 | Section renderers (KPI strip, performance, geography, catalog, customers). |
| `chart_patterns.py` | 1,844 | Nine reusable chart patterns + dashboard pattern-chart renderers. Plotly for charts, Folium for maps, pandas Styler for tables. |
| `chart_takeaways.py` | 665 | Per-qid directional caption logic. Captions are sentence-level descriptions ("widest gap is in beverages"); they intentionally avoid specific numbers (per Phase 5.5.2 design note). |
| `questions.py` | 159 | Suggested-question registry: `{merchant_segment: {specialist: [{id, text, pattern}, ...]}}`. |
| `styling.py` | 799 | CSS injection, dark/light theme overrides, chat-state classes. |
| `__init__.py` | 0 | Empty. |

### Config / infra (root)

| File | Role |
|---|---|
| `CLAUDE.md` | Root conventions; accurate. |
| `Makefile` | `make seed`, `make demo` (= `make seed && make report && streamlit run …`), `make test`, `make clean`, `make report`. |
| `Dockerfile` | python:3.11-slim base; installs requirements.txt; runs Streamlit on `streamlit_app.py`. HF Spaces uses UID 1000. |
| `streamlit_app.py` | HF Spaces entry point. Promotes `st.secrets["ANTHROPIC_API_KEY"]` into env. If `data/payments.db` missing, runs `src.generate.run_all` then `src.db.seed` in-process (~2 min cold start) before launching the dashboard via `runpy`. |
| `pyproject.toml` | Python 3.11+; dependencies: anthropic ≥0.40, faker, folium, matplotlib ≥3.8, numpy ≥2, pandas ≥2, plotly ≥5, python-dotenv, streamlit ≥1.39, streamlit-folium. Pytest opt-out for `-m llm`. |
| `requirements.txt` | Pinned dependency list for the Docker image (HF Spaces). |
| `.env` / `.env.example` | `ANTHROPIC_API_KEY=...`. Loaded by `python-dotenv`. |
| `.streamlit/config.toml` | Light theme (`#0F4C81` primary, `#FFFFFF` background); `browser.gatherUsageStats = false`. |
| `scripts/demo.sh` | Wraps `make clean && make seed && make demo`. |
| `scripts/build_report_html.py` + `scripts/generate_report_data.py` | Build the static interactive `docs/report.html` from `docs/report_data.json`. Not part of the live dashboard. |
| `scripts/record_baseline_cassettes.py` | Recording infrastructure for the 12 baseline LLM-response cassettes under `tests/cassettes/baseline/`. |
| `scripts/run_phase5_regression.py` | Phase 5 cassette regression runner. |

### Tests (`tests/`)

`test_generation.py`, `test_db.py`, `test_catalogs.py`, `test_lake_views.py`, `test_phase3_promos_and_tax.py`, `test_filter_wiring.py`, `test_agents.py`, `test_agents_phase2.py`, `test_cassette_infrastructure.py`, plus 12 baseline LLM cassettes under `tests/cassettes/baseline/` and matching `tests/cassettes/comparisons/`.

### Docs (`docs/`)

Active: `V4_AUDIT.md`, `V3_AUDIT.md`, `V3_VISION.md`, `V3_AGENTS_DESIGN.md`, `DEMO_SCRIPT_AGENTS.md`, `index.html`, `report.html`, `report_data.json`, `report_data.js`. Archive at `docs/archive/` including legacy `legacy_agent/advisor.py` (the v2 `MerchantAdvisor` that the v3 orchestrator replaced).

### Potentially dead / vestigial

- `src/db/queries.py` — five reference helpers; not imported by the live dashboard or agents based on grep.
- `register_lake_functions` (`views.py:304-312`) — registers three SQLite UDFs (`_opaque_id`, `_to_hour_bucket`, `_to_total_bin`) on every read-only connection (`tools.py::_exec_select` line 286-316). Post-Phase-1.5 the lake is pre-materialized, so the UDFs are not invoked by agent queries. Still called at seed time when building the materialized tables.
- `_VALID_VIEWERS` rejection list in `views.py` for old physical lake tables (`lake_customers`, `lake_transaction_items`) — guards against legacy SQL that would have referenced v2 tables that no longer exist.
- `placeholders.py` references in `src/agents/CLAUDE.md` — file does not exist in `src/dashboard/`; current dispatch is `agents.py`.

---

## 3. Data layer

All randomness flows from one seeded `np.random.default_rng(P.RANDOM_SEED)` instance constructed in `run_all.py:42`. There are no direct `np.random.*` calls.

### 3.1 Entry & outputs

`src/generate/run_all.py:main` (lines 39-102):

1. `rng = np.random.default_rng(P.RANDOM_SEED)` (line 42; seed = 42, `parameters.py:20`).
2. `customers_df = generate_customers(rng)` (line 45).
3. Build merchants metadata DataFrame inline (lines 47-53; 5 rows: KRG, ACM, WDX, TBL, TJX).
4. For each merchant `m`, call `m.build(rng)` returning a `MerchantData` bundle (stores, products, promotions) (lines 57-61).
5. Concat per-merchant frames into `stores_df`, `products_df`, `promotions_df` (lines 64-72).
6. `transactions_df, items_df = transactions.generate_all(customers_df, merchant_data, rng)` (line 75).
7. Write 7 CSVs to `data/raw/`: `customers.csv`, `merchants.csv`, `stores.csv`, `products.csv`, `promotions.csv`, `transactions.csv`, `transaction_items.csv` (lines 82-89).

Volume target band per the design doc is 180k-250k transactions, default produces ~230k-240k in ~80 seconds. Asserted in `tests/test_generation.py::test_total_volume_in_design_target` (mentioned in `src/generate/CLAUDE.md`).

### 3.2 Customer panel

`src/generate/customers.py:generate_customers` (lines 62-112). Always one pass over 10,000 customers (`parameters.py:33` `N_CUSTOMERS = 10000`). Field-by-field:

- **`customer_id`** — synthetic PAN is `rng.integers(10**15, 10**16)` (line 99). Then `sha256(HASH_SECRET + str(pan)).hexdigest()[:16]` (lines 21-27, helper `_customer_id_for_pan`). `HASH_SECRET = "demo-only-not-a-real-secret"` (`parameters.py:27`). The synthetic PAN is never persisted or referenced after this line.
- **`home_zip5`** — uniform draw from `P.METRO_ZIPS` (17 ZIPs in `parameters.py:63-65`) (line 74).
- **`behavioral_segment`** — `rng.choice(["filler","stocker"], p=[0.7, 0.3])` (line 77). `filler` = small frequent baskets; `stocker` = large infrequent.
- **`grocer_affinity_type`** — `rng.choice(["loyalist","splitter","three_chain","lapsed_light"], p=[0.55, 0.30, 0.12, 0.03])` (line 78; `parameters.py:81-86` `GROCER_AFFINITY_SHARES`).
- **`primary_grocer`** — `rng.choice(["KRG","ACM","WDX"], p=[0.40, 0.33, 0.27])` (line 79; `parameters.py:87-91` `PRIMARY_GROCER_SHARES`, proportional to store count: 30/25/20).
- **`secondary_grocer`** — branching by affinity (lines 44-59):
  - `loyalist` → `None`
  - `splitter` → choose one of the two remaining grocers uniformly
  - `three_chain` → choose one of the two remaining (the third surfaces at trip-choice time)
  - `lapsed_light` → `None`
- **`primary_card_type`** — `rng.choice(["credit","debit","mixed"], p=[0.55, 0.35, 0.10])` (lines 83-85). *Note*: `primary_card_type` is stored on the customer row, but the per-transaction payment_type draw in `transactions.py` is keyed off **per-merchant payment_mix**, not customer card preference — these are independent. See §3.5.
- **`has_mobile_wallet`** — `rng.choice([0, 1], p=[0.45, 0.55])` (line 86).
- **`signup_date`** — `END_DATE - rng.integers(0, 365*5)` (lines 89-93). Establishes the earliest valid trip date in the trip-distribution step.

The panel runs once. All five merchant generators then consume the same `customers_df` — `customer_id` is stable across merchants by construction.

### 3.3 Per-merchant entities

**Stores.** Per-merchant `build_stores(rng)` (e.g., `kroger.py` lines 36-58). Store count from `MERCHANT_CONFIGS[merchant]["n_stores"]` (`parameters.py:309-350`): KRG 30, ACM 25, WDX 20, TBL 40, TJX 8. `store_id` format = `<MID>-NC-<NNNN>` (4-digit zero-padded). ZIP assignment via `metro.assign_store_zips()` using segment+tier weights (`metro.py:66-91` `_TIER_WEIGHTS`: grocery → urban_core 1.2, inner_suburbs 1.4, outer 1.0; qsr → outer 1.4; retail → inner 1.6). Per-merchant neighborhood bias overlay (`parameters.py:367-390` `MERCHANT_NEIGHBORHOOD_BIAS`: ACM toward SouthPark/Ballantyne, WDX toward NoDa/Pineville, KRG neutral). `assign_store_zips()` also accepts `require_zips=` so each grocer's `build()` can pin a store into the anomaly-anchor neighborhoods (`SHARED_FOOTPRINT_ZIPS` list in `kroger.py:25-32`). `latitude/longitude` = ZIP centroid (from `metro._ZIP_CENTROIDS`) ± uniform jitter of ±0.02° (`metro.py:45-52`). `open_date` = `END_DATE - rng.integers(365, 365*10)` (random 1-10 years before window end).

**Products (catalogs).** Grocers use the base+overlay model in `catalog_grocery.build_catalog(merchant_key)` (lines 57-116):
- Base catalog: `data/catalogs/base_grocery_catalog.json` — 1,112 canonical SKUs with 12 categories (DAIRY, BAKERY, PRODUCE, MEAT, FROZEN, PANTRY, SNACKS, BEVERAGES, HOUSEHOLD, PERSONAL, BABY, PET).
- Per-grocer overlay (e.g., `data/catalogs/overlays/kroger.json`) selects which canonical SKUs are carried (`included_canonical_skus`) and provides `tight_multiplier`, `loose_multiplier`.
- Effective price = `base_price * tier_multiplier * (1 + rng.uniform(-0.02, 0.02))` (lines 101-102).
- SKU code format = `<MID>-<canonical_sku>` (line 105). Categories and subcategories preserved from the base catalog so the lake's `canonical_name` field can match across merchants.

QSR catalog (`catalog_taco_bell.py`): 60 SKUs across 7 categories (TACO 8, BURR 10, SPEC 8, COMBO 6, SIDE 6, DRINK 12, BFAST 10). Prices set per-category by range (TACO 1.29-4.49, etc., lines 42-63). Includes a `type` field used by affinity rules.

Retail catalog (`catalog_tjmaxx.py`): 200 SKUs across 8 categories (WOM 50, MEN 35, KID 25, SHO 25, ACC 20, HOM 30, BTY 10, JEW 5; lines 42-62).

**Promotions.** `promotions.generate_for_merchant` (lines 139-188). Per-merchant target counts (`parameters.py:94-100`): KRG 25 (including 1 pinned pasta promo), ACM 20 (1 pinned), WDX 18 (1 pinned), TBL 6, TJX 4. Each promotion produces one DB row **per affected SKU**. Type mix differs by segment (`promotions.py:38-42`) — grocery: 55% weekly_ad / 15% holiday / 20% lto / 10% clearance; QSR: 65% lto / 20% holiday / 15% weekly_ad; retail: 55% clearance / 30% lto / 15% holiday. Duration ranges by type (lines 45-50): weekly_ad 7d, holiday 5-7d, lto 14-21d, clearance 21-30d. SKU count per promo varies wildly by segment×type (e.g., grocery weekly_ad covers 70-140 SKUs, QSR lto only 3-6). Discount depth by type (lines 84-89): weekly_ad 10-25%, holiday 15-30%, lto 10-20%, clearance 20-40%.

### 3.4 Transaction & basket generation

`src/generate/transactions.py:generate_all` (lines 527-737). Customer-centric loop: one pass over 10,000 customers, emitting all five merchants' transactions per customer.

**(a) Trip frequency.** `_sample_trip_count_grocery` (lines 79-94) samples from a triangular distribution with mode at the low end, with the bounds depending on `(behavioral_segment, grocer_affinity_type)` per `parameters.py:101-108` `TRIP_FREQUENCY_GROCERY`:

| | loyalist | splitter | three_chain |
|---|---|---|---|
| filler | 18-24 | 20-28 | 22-30 |
| stocker | 8-14 | 10-16 | 12-18 |

`lapsed_light` instead samples a bucket from `LAPSED_TRIP_BUCKETS` (`parameters.py:112-116`): 50% chance of (0,0), 30% chance of (1,2), 20% chance of (1,2). QSR uses `QSR_TRIP_BUCKETS` (`parameters.py:123-127`): 30% (6,12) / 40% (2,5) / 30% (0,1). Retail uses `RETAIL_TRIP_BUCKETS` (`parameters.py:131-134`): 30% (2,6) / 70% (0,1).

**(b) Active-weeks variance + within-week placement.** `_distribute_trip_dates` (lines 115-181). The 90-day window is divided into 13 calendar weeks. First, sample the number of *active* weeks from `ACTIVE_WEEKS_DIST = {9: 0.10, 10: 0.20, 11: 0.35, 12: 0.35}` (`parameters.py:138-139`). Then pick that many weeks at random (line 144). Distribute trips across active weeks with Dirichlet(2.0) weights (line 152), guaranteeing ≥1 trip per active week. Within each week, day choice is weighted by the calendar multiplier:

- **Pay cycle**: days {1,2,3,15,16,17} get 1.15× multiplier (`parameters.py:143-144` `PAY_CYCLE_DAYS`, `PAY_CYCLE_MULTIPLIER`).
- **Peak days**: grocery weighting weekends (Sat=5, Sun=6); QSR weighting Fri-Sat (4,5); retail Sat-Sun (5,6) — multiplier 1.4× on peak days, 0.9× elsewhere (`parameters.py:157-163`).
- **Earliest valid day**: trips never fall before `signup_date`.

**(c) Per-trip merchant choice (grocery only).** `_grocery_choice_distribution` (lines 188-236). The customer's affinity rules combine with day-of-week to produce a (chain_list, prob_list):

| Affinity | Day | Primary | Secondary | Third |
|---|---|---|---|---|
| loyalist | any | 94% | 5% | 1% |
| splitter | Tue-Thu | 50% | 50% | — |
| splitter | Fri-Sat | 80% | 20% | — |
| splitter | other | 65% | 30% | 5% |
| three_chain | any | 50% | 30% | 20% |
| lapsed_light | any | 50% | 30% | 20% |

Each trip's chain is sampled independently — there is no within-customer Markov chain.

**(d) Within-chain store choice.** `_pick_store_index` (lines 243-260) draws from probabilities `(0.70, 0.25, 0.05)` for "closest", "second-closest", "other" (`parameters.py:154` `STORE_CHOICE_PROBS`). **Important**: there is no real geographic distance — the customer's "closest" is the first entry in a per-customer shuffled list of the chain's stores (line 591). Different customers see different stores as "closest". This is documented as the Phase 4 stub.

**(e) Time of day.** Hour weights per segment (`parameters.py:164-168` `PEAK_HOURS_BY_SEGMENT`): grocery {10,11,12,17,18,19}, qsr {12,13,19,20,21}, retail {11,…,19}. Base hour distribution from a 24-hour weight table (`transactions.py:454-463`): 0.05 (0-5am), 0.4 (6-9am), 1.0 (10-21), 0.3 (22-23). Peak hours get a 3.0× multiplier. Minute and second are uniform on [0,59] (lines 772-776).

**(f) Terminal assignment.** Per store, terminal count is sampled once from [4, 8] (`parameters.py:171` `TERMINALS_PER_STORE_RANGE`) at `_build_terminal_pools` (lines 470-480). Terminal IDs are `<store_id>-T<NN>`. Selection of terminal *for a transaction* is uniform across the store's pool.

**(g) Basket archetype (grocery only).** `_sample_archetype` (lines 267-270) draws from `ARCHETYPE_SHARES`: stockup 40%, fill_in 45%, themed 15% (`parameters.py:174`).

**(h) Basket size.** `_sample_basket_size` (lines 291-309) draws a triangular sample by `(behavioral_segment, archetype)` for grocery (`parameters.py:227-234`):

| | filler | stocker |
|---|---|---|
| fill_in | (3, 6, 10) | (5, 10, 14) |
| stockup | (8, 14, 18) | (18, 28, 40) |
| themed | (5, 10, 14) | (10, 18, 25) |

QSR fixed `(2, 3, 5)`; retail fixed `(1, 5, 12)` (`parameters.py:235-236`). Then a per-merchant multiplier applies (`parameters.py:422-426` `MERCHANT_BASKET_SIZE_MULT`): ACM 0.90×, KRG 1.00×, WDX 1.20×.

**(i) Per-line SKU sampling.** `_generate_line_items` (lines 883-956):

1. Sample a category for each line, weighted by `ARCHETYPE_CATEGORY_WEIGHTS` (grocery; `parameters.py:180-223`) or uniform (qsr/retail).
2. Within the chosen category, pick a SKU. Apply per-merchant category bias (`MERCHANT_CATEGORY_BIAS`, `parameters.py:397-413`) and the anomaly SKU multipliers (avocado for PRODUCE on Plaza Midwood dates, pasta for PANTRY on each grocer's pasta-promo window) if applicable.
3. Deduplicate within the basket (set tracking, line 906). Attempt budget = `basket_size * 4` (line 908) to prevent infinite loops if the candidate pool is small.
4. Apply affinity rules (e.g., diapers → infant formula at 45%, spaghetti → marinara at 55%, milk → cheerios at 30%; grocery rules in `catalog_grocery.py:126-175`). Each rule is a closure invoked per anchor SKU.
5. Per-line quantity from `QTY_DISTRIBUTION` per category (`parameters.py:241-275`). E.g., PRODUCE: {1: 0.55, 2: 0.25, 3: 0.12, 4: 0.05, 5: 0.03}.
6. Unit price = catalog price *(no per-line noise — `transactions.py:935`, explicit comment in code)*.
7. Promo lookup: O(1) into a precomputed `(day_offset, sku)` dict (line 939); discount applied with probability `DISCOUNT_APPLY_PROB = 0.85` (`parameters.py:279`) if a promo exists.
8. Per-category tax rate from `tax.tax_rate(category)` (line 946; rates in `tax.py`).
9. `line_total = unit_price * qty - discount`; `tax = line_total * tax_rate`.

### 3.5 Payment fields

`_emit_trip` (lines 744-869), payment block lines 827-831:

- **`payment_type`**: `rng.choice(["credit","debit"], p=merchant_mix)`. Per-merchant payment_mix from `MERCHANT_CONFIGS` (`parameters.py:309-350`): grocers 65/35, TBL 55/45, TJX 74/26.
- **`card_network`**: conditioned on `payment_type` (`parameters.py:282-283`). Credit: visa 50% / mc 30% / amex 12% / discover 8%. Debit: visa 60% / mc 38% / discover 1% / amex 1%.
- **`entry_mode`**: per-segment (`parameters.py:286-290`). Grocery: contactless 55% / chip 35% / swipe 9% / manual 1%. QSR: contactless 70% / chip 22% / swipe 5% / manual 3%. Retail: contactless 45% / chip 45% / swipe 9% / manual 1%.
- **`wallet_type`**: only set if `entry_mode == "contactless"` AND `customer.has_mobile_wallet == 1` AND a fresh draw lands within `WALLET_USE_PROB = 0.70` (`parameters.py:293`). Then sample from `WALLET_PROVIDER_DIST`: apple 50% / google 30% / samsung 20% (line 294).
- **`connectivity_type`**: uniform per `CONNECTIVITY_DISTRIBUTION` (`parameters.py:297-302`) — wifi 65% / cellular_4g 25% / cellular_5g 8% / ethernet 2%.

**Not generated:** raw PAN, BIN, last4, auth_code, declines, cash, EBT. Captured rails only.

### 3.6 Anomalies

Three planted signals are injected at generation time.

**University City decline** (`anomalies/university_city_decline.py`). Scope: all three grocers' UC stores (ZIPs 28213, 28223). Window: Apr 12 – May 29, 2026. Implementation: a trip keep-probability draw inside the transaction loop (`transactions.py:785-789`). Four stages with stage multipliers and per-grocer magnitudes:

| Stage | Dates | Stage mult | KRG mag | ACM mag | WDX mag |
|---|---|---|---|---|---|
| 1 | Apr 12-18 | 1.10 | 1.00 | 0.80 | 0.70 |
| 2 | Apr 19-25 | 0.85 | 1.00 | 0.80 | 0.70 |
| 3 | Apr 26-May 2 | 0.55 | 1.00 | 0.80 | 0.70 |
| 4 | May 3-29 | 0.65 | 1.00 | 0.80 | 0.70 |

Effective keep-prob = `1 - magnitude * (1 - stage_mult)` (per the Explore agent's read of `university_city_decline.py:79`). KRG hits hardest (`magnitude = 1.00`).

**Plaza Midwood avocado spike** (`anomalies/plaza_midwood_avocado.py`). Scope: Kroger Plaza Midwood store (ZIP 28205) only, PRODUCE category only. SKU match: product name contains "avocado" case-insensitive. Window and daily multipliers: Apr 21 ×1.5, Apr 22 ×5.0, Apr 23 ×3.0, Apr 24 ×1.5. Implementation: SKU selection bias in PRODUCE category (`transactions.py:801-803`).

**Coordinated pasta promos** (`anomalies/acme_pasta_promo.py`). Scope: each grocer reserves one pinned promo slot for a pasta promotion (per-grocer `build()` appends the pinned promo). Implementation: basket-level pasta multiplier on PANTRY SKUs with subcategory `pasta` (`transactions.py:800`).

| Merchant | Window | Discount | Basket mult | Outcome |
|---|---|---|---|---|
| KRG | Apr 15-21 | 25% | 2.2× | lift |
| ACM | Apr 19-25 | 20% | 0.8× | failure |
| WDX | Apr 22-28 | 15% | 1.4× | modest lift |

### 3.7 Structure vs randomness — explicit callout

**Where the data has real structure that an analyst could find:**
- Customer affinity persists across trips: a loyalist's primary grocer is the same physical merchant for ~94% of trips throughout the 90 days.
- Pay-cycle + peak-day + peak-hour multipliers shape time series at the day-of-month / day-of-week / hour-of-day granularity. A weekend-grocery lift is real, not noise.
- Basket archetype shapes category mix and basket size jointly (`stockup` baskets have weighted category distributions plus larger size).
- Per-merchant biases differentiate the three grocers in neighborhood footprint, category mix, basket size, and payment-method mix.
- The three planted anomalies create reproducible temporal patterns that the anomaly specialist can detect from time-series alone.
- SKU affinity rules tie items together within a single basket (diapers → formula, milk → cheerios, etc.).

**Where the data is effectively independent random draws:**
- Per-line SKU selection within a basket is sampled fresh each time — there is no within-customer history learning across trips. A customer's "favorite cereal" is not modeled.
- Unit prices have **no per-line noise** in v2.5 (`transactions.py:935`, explicit `# v2.5: no noise`). Every line for a given SKU at a given merchant has identical unit_price.
- Per-transaction `card_network`, `entry_mode`, `wallet_type`, `connectivity_type` are independent draws keyed only off segment/merchant — they are not correlated with the specific customer, the time of day, or each other (except for the `entry_mode == "contactless"` gate on `wallet_type`).
- Terminal_id is selected uniformly within the chosen store's terminal pool.
- Customer-store "closest" relationship is a per-customer shuffle, not real distance.

---

## 4. Anonymization stage

There is no separate post-CSV anonymization step. Privacy is enforced in two places: (a) at lake materialization during `seed.py`, and (b) at agent query time via the k=5 wrapper.

### 4.1 At generation time (proactive avoidance)

`customer_id` is the 16-hex-char SHA-256 of `HASH_SECRET + synthetic_pan` (`customers.py:21-27`). The synthetic PAN is constructed at line 99 and never persisted. The CSVs at `data/raw/` contain no names, emails, raw PANs, BINs, last4s, or auth codes — these are simply not produced. So the "anonymization" of customer identity is structural: there is no PII to strip because the generator never emits any.

### 4.2 At lake materialization (`seed.py:main` lines 126-162)

For each viewer in `{KRG, ACM, WDX, TBL, TJX}` (`views.py:_VALID_VIEWERS`), `seed.py` runs:

```python
txn_sql = _build_lake_transactions_sql(viewer).replace(":viewing", f"'{viewer}'")
conn.execute(f"DROP TABLE IF EXISTS lake_transactions_{viewer}")
conn.execute(f"CREATE TABLE lake_transactions_{viewer} AS {txn_sql}")
# + four indexes for anchor query patterns
# + same for lake_stores_{viewer}
```

The lake template (`views.py:_LAKE_TXN_SQL_TEMPLATE`, surfaced via `_build_lake_transactions_sql` lines 266-277) selects from `tenant_transactions JOIN tenant_transaction_items JOIN tenant_products JOIN tenant_stores`, applying:

| Privacy technique | How | Where |
|---|---|---|
| **Tokenization** of `txn_id`, `line_id` → `lake_txn_id` | `_opaque_id(t.txn_id, l.line_id)` = SHA-256(`"v2.5-lake-opaque-salt"\|txn_id\|line_id`)[:16] | `views.py:143-155` (`generate_opaque_id`), salt at line 58 |
| **Tokenization** of `store_id` → `lake_store_id` | `_opaque_id(t.store_id)` | same function, single-arg form |
| **Generalization** of `store_zip5` → `store_zip3` | `SUBSTR(s.store_zip5, 1, 3)` | template line 239 (lake_stores) |
| **Generalization** of `txn_ts` → `txn_date` + `txn_hour_bucket` | `DATE(t.txn_ts)` and `_to_hour_bucket(t.txn_ts)` | template line 208-209 |
| **10-bucket hour map** | early_morning / morning / mid_morning / lunch / afternoon / late_afternoon / evening / dinner / late_evening / late_night | `to_hour_bucket` `views.py:85-114`, list at 61-72 |
| **Generalization** of `txn_total` → `txn_total_bin` | `_to_total_bin(t.txn_total)` over `TOTAL_BINS` | `to_total_bin` `views.py:117-140`, list 74-78 (`$0-5` through `$250+`) |
| **Viewing-merchant exclusion** | `WHERE t.merchant_id != :viewing` (substituted to literal at seed time) | template lines 230, 244 |
| **Peer pseudonymization** | `CASE t.merchant_id WHEN 'XXX' THEN 'peer_a' …` from `peer_case_sql(viewer, "t.merchant_id")` | `peer_mapping.py:41-56`; pulled from `parameters.PEER_MAPPING` (`parameters.py:432-438`) |
| **Consumer-linkage suppression** | `customer_id` is simply absent from the SELECT list | template `views.py:201-231` (test enforces: `tests/test_lake_views.py:262-268`) |

Peer pseudonymization is **stable per build** (deterministic from the mapping) but **not randomized per query**. Because the mapping is baked into the materialized table, the assignment never changes across queries.

### 4.3 At agent query time (k=5)

`tools.py::query_lake` (lines 492-532) wraps the agent's SQL in two CTEs (`WITH lake_transactions AS (SELECT * FROM lake_transactions_<viewer>), lake_stores AS (...)`) and executes. After execution, `_maybe_suppress_sub_k` (lines 454-489) inspects the result columns. If any column name in `{count, cnt, n, *_count, *_n}` exists, it filters rows where that count < `K_ANONYMITY_K = 5` (`views.py:52`) and adds a `"suppression"` note to the response dict.

This means k=5 only fires if the model wrote `COUNT(*) AS n` (or similar) in its SQL. The specialist prompts instruct the model to include such columns when grouping by customer dimensions, but there is no programmatic guarantee — a query that produces small cells without a count column bypasses the suppression. This is the documented behavior, not a bug.

### 4.4 What is *not* implemented

L-diversity verification, differential privacy (beyond a stub), real GDPR-style "right to be forgotten" workflow, separate-user SQLite ACLs. The project doc explicitly lists these as out of scope.

---

## 5. Storage & schema

Reproduced from `src/db/schema.sql` (194 lines). One SQLite file at `data/payments.db`. `PRAGMA foreign_keys = ON` is set per-connection (not persisted by SQLite).

### 5.1 Shared dimension

```sql
CREATE TABLE merchants (
    merchant_id   TEXT PRIMARY KEY,             -- 'KRG','ACM','WDX','TBL','TJX'
    name          TEXT NOT NULL,
    segment       TEXT NOT NULL,                -- 'grocery','qsr','off_price_retail'
    mcc           TEXT NOT NULL
);
```

5 rows.

### 5.2 Tenant tables

```sql
CREATE TABLE tenant_customers (
    customer_id           TEXT PRIMARY KEY,
    home_zip5             TEXT NOT NULL,
    behavioral_segment    TEXT NOT NULL,        -- 'filler','stocker'
    grocer_affinity_type  TEXT NOT NULL,        -- 'loyalist','splitter','three_chain','lapsed_light'
    primary_grocer        TEXT NOT NULL,        -- 'KRG','ACM','WDX'
    secondary_grocer      TEXT,                 -- nullable
    primary_card_type     TEXT NOT NULL,        -- 'credit','debit','mixed'
    has_mobile_wallet     INTEGER NOT NULL,
    signup_date           DATE NOT NULL
);

CREATE TABLE tenant_stores (
    store_id      TEXT PRIMARY KEY,
    merchant_id   TEXT NOT NULL REFERENCES merchants(merchant_id),
    store_zip5    TEXT NOT NULL,                -- full ZIP at this layer
    neighborhood  TEXT NOT NULL,
    metro_region  TEXT NOT NULL,                -- 'urban_core','inner_suburbs','outer_suburbs'
    latitude      REAL NOT NULL,
    longitude     REAL NOT NULL,
    open_date     DATE NOT NULL
);

CREATE TABLE tenant_products (
    sku            TEXT PRIMARY KEY,
    merchant_id    TEXT NOT NULL REFERENCES merchants(merchant_id),
    name           TEXT NOT NULL,
    category       TEXT NOT NULL,
    subcategory    TEXT NOT NULL,
    base_price     REAL NOT NULL
);

CREATE TABLE tenant_transactions (
    txn_id            TEXT PRIMARY KEY,
    merchant_id       TEXT NOT NULL REFERENCES merchants(merchant_id),
    customer_id       TEXT NOT NULL REFERENCES tenant_customers(customer_id),
    store_id          TEXT NOT NULL REFERENCES tenant_stores(store_id),
    terminal_id       TEXT NOT NULL,             -- '<store_id>-T<NN>'; no FK
    txn_ts            DATETIME NOT NULL,         -- full timestamp at this layer
    payment_type      TEXT NOT NULL,
    card_network      TEXT,
    entry_mode        TEXT NOT NULL,
    wallet_type       TEXT,
    connectivity_type TEXT NOT NULL,
    subtotal          REAL NOT NULL,             -- sum of line_total across items
    tax_total         REAL NOT NULL,             -- sum of tax across items
    txn_total         REAL NOT NULL              -- subtotal + tax_total
);

CREATE TABLE tenant_transaction_items (
    txn_id         TEXT NOT NULL REFERENCES tenant_transactions(txn_id),
    line_id        INTEGER NOT NULL,
    sku            TEXT NOT NULL REFERENCES tenant_products(sku),
    qty            INTEGER NOT NULL CHECK (qty > 0),
    unit_price     REAL NOT NULL CHECK (unit_price >= 0),
    discount       REAL NOT NULL DEFAULT 0,
    tax            REAL NOT NULL DEFAULT 0,
    line_total     REAL NOT NULL,                -- (unit_price × qty) - discount
    promo_id       TEXT,                         -- nullable FK to tenant_promotions(promo_id)
    PRIMARY KEY (txn_id, line_id)
);

CREATE TABLE tenant_promotions (
    promo_id       TEXT NOT NULL,                -- '<merchant>-PROMO-<NNNN>' (one row per affected SKU)
    merchant_id    TEXT NOT NULL REFERENCES merchants(merchant_id),
    sku            TEXT NOT NULL REFERENCES tenant_products(sku),
    start_date     DATE NOT NULL,
    end_date       DATE NOT NULL,
    discount_pct   REAL NOT NULL,                -- 0.15 == 15% off
    promo_name     TEXT NOT NULL,
    promo_type     TEXT NOT NULL,                -- 'weekly_ad'/'holiday'/'lto'/'clearance'
    PRIMARY KEY (promo_id, sku)
);
```

Note: `tenant_transaction_items.promo_id` is documented as a "nullable FK" in the source comment (line 83) but is NOT declared as `REFERENCES tenant_promotions(promo_id)` in the DDL — `tenant_promotions`'s PK is the composite `(promo_id, sku)`, so a single-column FK from the item line wouldn't be enforced anyway. Load order in `seed.py` puts `tenant_promotions` before `tenant_transaction_items` (lines 100-104) so any orphan-promo-id would surface as a load error if FK enforcement were active on that pair.

### 5.3 Indexes

```sql
CREATE INDEX ix_t_txn_customer  ON tenant_transactions(customer_id);
CREATE INDEX ix_t_txn_merchant  ON tenant_transactions(merchant_id);
CREATE INDEX ix_t_txn_store     ON tenant_transactions(store_id);
CREATE INDEX ix_t_txn_ts        ON tenant_transactions(txn_ts);
CREATE INDEX ix_t_items_sku     ON tenant_transaction_items(sku);
CREATE INDEX ix_t_items_txn     ON tenant_transaction_items(txn_id);
CREATE INDEX ix_t_promo_sku     ON tenant_promotions(merchant_id, sku);
CREATE INDEX ix_t_promo_dates   ON tenant_promotions(start_date, end_date);
```

### 5.4 Per-viewer tenant isolation views (lines 130-193)

For each of `{KRG, ACM, WDX, TBL, TJX}`, six views: `tenant_view_<M>_customers`, `_stores`, `_products`, `_transactions`, `_transaction_items`, `_promotions`. The `_customers` view is built as `SELECT DISTINCT c.* FROM tenant_customers c JOIN tenant_transactions t ON t.customer_id = c.customer_id WHERE t.merchant_id = '<M>'` — only customers who have transacted with `<M>` appear in `<M>`'s view. The other five views are simple `WHERE merchant_id = '<M>'` filters (or, for `_transaction_items`, a join through `_transactions`). 30 views total.

### 5.5 Lake tables (materialized at seed time, NOT in schema.sql)

For each viewer, `seed.py::main` (lines 126-162) creates:

- `lake_transactions_<M>` — 21 columns: `lake_txn_id` (opaque), `line_id`, `peer_id` (peer_a..peer_d), `peer_segment`, `lake_store_id` (opaque), `txn_date`, `txn_hour_bucket`, `payment_type`, `card_network`, `entry_mode`, `wallet_type`, `connectivity_type`, `txn_total_bin`, `canonical_name`, `category`, `subcategory`, `unit_price`, `qty`, `discount`, `line_total`, `discount_pct_applied`.
- `lake_stores_<M>` — 6 columns: `lake_store_id`, `peer_id`, `peer_segment`, `store_zip3`, `neighborhood`, `metro_region`.

Indexes are created on the materialized lake tables for the anchor query patterns (lines 138-149 of seed.py; exact columns documented there).

### 5.6 Load process

`seed.py::main` (lines 73-171):

1. Delete `data/payments.db` if present (line 76) — idempotent reset.
2. Connect; `PRAGMA foreign_keys = ON` (line 80); `executescript(schema.sql)` to create all tables, indexes, and views (line 82).
3. Load `merchants` first (lines 86-87), then `tenant_customers`, `tenant_stores`, `tenant_products` (lines 89-98).
4. Load `tenant_promotions` (lines 102-104) **before** `tenant_transaction_items` (lines 115-119) so the item rows' `promo_id` references valid promo rows.
5. Load `tenant_transactions` (lines 106-113).
6. Loop over viewers and materialize the 10 lake tables (lines 126-162).
7. Commit; close (lines 164-165).

All loads use `pandas.DataFrame.to_sql(..., if_exists="append", index=False, chunksize=10_000)` (`seed.py:_load`, lines 69-70).

### 5.7 Customer ID presence

Present in: `tenant_customers.customer_id` (PK), `tenant_transactions.customer_id` (FK), every `tenant_view_<M>_customers` and `_transactions` view. Absent from: every lake table (the lake template omits it from the SELECT list). Tested in `tests/test_lake_views.py:262-268`.

---

## 6. Agent layer

This is the most behaviorally important section. The trace is grounded in `src/agents/specialist.py`, `orchestrator.py`, `tools.py`, and `context.py`.

### 6.1 Models

`src/agents/llm.py:MODEL_ROUTER = MODEL_SPECIALIST = "claude-haiku-4-5-20251001"` (per `src/agents/CLAUDE.md` and confirmed by direct reads). Pricing $1/$5 per MTok. The router uses 200 max_tokens (`orchestrator.py:252`); specialists use 4096 max_tokens per turn (`specialist.py:43`).

### 6.2 Suggested-question path (no orchestrator)

1. User clicks a pill in the chat panel. Button key = `q_{merchant_id}_{agent}_{qid}` (`chat.py` render_chat_panel, around line 1126 per the recon).
2. The click sets `state.pending_dispatch = {"kind": "question", "qid": qid, "agent_id": agent_id, ...}` and `state.agent_running = True`; Streamlit reruns.
3. On the rerun, `_render_live_turn` calls `src/dashboard/agents.py::dispatch(agent_id, qid, merchant_id, progress=..., on_token=...)` at `agents.py:191`.
4. `dispatch` consults `st.session_state["llm_cache"]` keyed by `(agent_id, qid, merchant_id)` (`agents.py:90, 102`). On hit, returns the cached dict immediately.
5. On miss, `dispatch` calls `_run_specialist(agent_id, qid, merchant_id, ...)` at `agents.py:148`, which builds a `MerchantContext`, instantiates the specialist class, looks up the question text from `src/dashboard/questions.py`, and calls `spec.answer(question, progress=progress, on_token=on_token)`.

### 6.3 Free-form path (orchestrator routes)

1. User types into the free-form text area and submits.
2. `chat.py` sets `state.pending_dispatch = {"kind": "free", "question": ...}`; Streamlit reruns.
3. `_render_live_turn` calls `agents.py::dispatch_orchestrated(merchant_id, raw_question, progress=..., on_token=...)` at `agents.py:226`. **Never cached.**
4. `dispatch_orchestrated` builds a `MerchantContext` and calls `Orchestrator(context).ask(question, progress=progress, on_token=on_token)` (`orchestrator.py:309-347`).
5. `Orchestrator.ask`:
   - Fires `progress(0, "Picking a specialist…")` (line 318).
   - Calls `route(question, ctx)` (line 319).
   - `route` (lines 238-274) renders the orchestrator prompt with viewer substitutions and makes **one Haiku call** — `client.messages.create(model="claude-haiku-4-5-20251001", system=rendered_prompt, messages=[{"role":"user","content":question}], max_tokens=200)`. No tools.
   - `_parse_router_output` (lines 201-222) extracts a JSON `{primary, secondary, rationale}` object via regex `\{[^{}]*\}` and validates `primary` is one of `("pricing", "anomaly", "demand", "trade")`. On any parse failure → fall through to `_keyword_route`.
   - `_keyword_route` (lines 155-191) matches the question lowercase against `_KEYWORD_RULES` (lines 136-152). On match → that specialist. On miss → segment-conditional default from `_SEGMENT_DEFAULT_SPECIALIST` (lines 51-56): grocer→anomaly, qsr→demand, retail→pricing, unknown→demand.
   - Returns a `RoutingDecision` with `via_fallback` set true when the keyword fallback was used.
6. `Orchestrator.ask` then `_build_specialist(decision.primary, ctx)` (line 322) and calls `spec.answer(...)` (line 323).
7. The orchestrator's response (`OrchestratorResponse.to_dict`) prepends `"_Routed to the **<spec_label>** (<rationale>).\_\n\n"` to the specialist's prose (lines 108-117) and folds the router's tokens/cost into the specialist's telemetry.

### 6.4 The specialist tool loop (specialist.py:144-244)

Every specialist subclass overrides only four class attributes (`AGENT_LABEL`, `PROMPT_PATH`, optionally `TOOLS` and `MAX_TURNS`); the loop is identical.

Setup (`answer` body, lines 162-171):
- Reset per-call state: `_sql_log`, `_last_table`, `_chart`, token counters.
- `messages = [{"role": "user", "content": question}]`.
- `use_streaming = on_token is not None`.

Loop (lines 173-237):

```python
for turn in range(self.MAX_TURNS):           # MAX_TURNS = 10
    if progress is not None:
        progress(turn, PROGRESS_MESSAGES[min(turn, 3)])
    if use_streaming:
        resp, tel = L.call_with_tools_streaming(
            model=self.MODEL,                 # claude-haiku-4-5-20251001
            system=self._system_prompt,       # rendered specialist prompt
            tools=self.TOOLS,                 # T.TOOLS_SPECIALIST
            messages=messages,
            max_tokens=MAX_TOKENS,            # 4096
            on_text_delta=on_token,
        )
    else:
        resp, tel = L.call_with_tools(...)    # same args, non-streaming
    # accumulate tokens + cost
    messages.append({"role": "assistant", "content": resp.content})
    if resp.stop_reason != "tool_use":
        text = self._extract_text(resp.content)
        return self._finalize(text, converged=True, turns=turn + 1)
    # else: process all tool_use blocks, append tool_result messages,
    # then loop
```

On exhaustion (line 238), return a partial with `converged=False` and a stock "I couldn't converge in N turns" prose.

Each tool_use block is dispatched through `_dispatch_tool` (lines 248-274), which routes to `MerchantContext`:

- `schema_info` → `ctx.schema_info()` returns `SCHEMA_PATH.read_text()` (full DDL).
- `query_tenant(query=…)` → `ctx.query_tenant(args["query"])` → `tools.py::query_tenant`. Appends `{"tool": "tenant", "query": …, "row_count": …}` to `_sql_log` and stores the result in `_last_table` (specialist.py:255-259).
- `query_lake(query=…)` → `ctx.query_lake(args["query"])` → `tools.py::query_lake`. Appends `{"tool": "lake", ...}` to `_sql_log`; stores result in `_last_table` (lines 261-267).
- `make_chart(spec=…)` → `ctx.make_chart(args)` → `tools.py::make_chart` returns a Plotly `go.Figure`. The Figure is stored on `self._chart` (line 271); the LLM gets back `T.make_chart_ack(args)` — a small acknowledgment dict (kind, title, series count, point count) — *not* the Figure binary.

Tool-result payloads sent back to the LLM are trimmed by `tools.py::trim_for_llm`:
- Cap at `LLM_ROW_BUDGET = 20` rows (`tools.py:48`).
- Round floats to `LLM_FLOAT_PRECISION = 2` (`tools.py:52`).
- Drop all-null columns.
- Add a `"note"` field if rows were truncated.
The specialist keeps the full result in `_last_table` for final rendering.

### 6.5 SQL guards (tools.py)

`query_tenant`:
- `is_safe_select(sql)` — regex check (single SELECT, no DDL/DML, no semicolons). At `tools.py:230-239`.
- `has_merchant_predicate(sql, merchant_id)` — regex check that the SQL contains `WHERE … merchant_id = '<merchant_id>'` (or double-quoted equivalent). At `tools.py:242-248`.
- CTE-wrap: prepend `WITH tenant_customers AS (SELECT * FROM tenant_view_<M>_customers), tenant_stores AS (…), tenant_products AS (…), tenant_transactions AS (…), tenant_transaction_items AS (…), tenant_promotions AS (…)` so any unqualified `tenant_*` reference in the agent's SQL resolves to the per-viewer view (lines 403-412 per the recon).
- Execute via `_exec_select` (lines 286-316): URI mode read-only SQLite connection, `PRAGMA foreign_keys = ON`, `register_lake_functions` optional (passed `False` for tenant queries), fetch up to `MAX_ROWS = 200` (with one extra to flag truncation), return `{columns, rows, row_count, truncated}`.

`query_lake`:
- Single SELECT check.
- Reject `tenant_*` references and any `FORBIDDEN_LAKE_TABLES = ("lake_customers", "lake_transaction_items")` (`tools.py:63`) — these are legacy v2 names. Reject if it doesn't reference at least one of `ALLOWED_LAKE_VIEWS = ("lake_transactions", "lake_stores")` (`tools.py:57`).
- CTE-wrap: `WITH lake_transactions AS ({lake_transactions_sql(viewer)}), lake_stores AS ({lake_stores_sql(viewer)})` then the agent's SQL. The `lake_transactions_sql` and `lake_stores_sql` runtime helpers (`views.py:287-301`) return `SELECT * FROM lake_transactions_<viewer>` — i.e., they read from the materialized per-viewer tables. Validation of `viewer` against `_VALID_VIEWERS` happens at line 293.
- Execute via `_exec_select`.
- Run result through `_maybe_suppress_sub_k` (tools.py:454-489) before returning — see §4.3 above.

### 6.6 The chart tool (`tools.py::make_chart`)

Input spec (`tools.py:567-576`): `{kind, title, x, series, y_format, x_axis_title?, y_axis_title?}` where `kind ∈ {grouped_bar, horizontal_bar, line, donut}`, `series = [{name, values}, …]`, `y_format ∈ {currency, count, pct, float}`. The function builds a `plotly.graph_objects.Figure` using a chart palette (`tools.py:544-681`). The Figure is stored on the specialist's `_chart` and returned as part of the `SpecialistResponse`. The LLM gets back a small ack via `make_chart_ack` so it knows the call succeeded without paying the figure's serialized-tokens cost.

### 6.7 Number, narrative, chart provenance (the critical question)

The specialist produces, in order:

1. **The agent's prose** (final turn's text, sans the caveats fence). This is free-form natural language emitted by the LLM. Any number cited in the prose comes from the LLM reading its own tool-result history (the `tool_result` blocks appended at line 236 after each tool call).
2. **The agent's table** = `_last_table` converted to a `pd.DataFrame` (lines 287-292 of `_finalize`). This is the result of the **most recent** `query_tenant` or `query_lake` call — if the agent ran tenant then lake then a chart, the table is the lake result. **The table is the raw tool result, not a model-generated artifact.**
3. **The agent's chart** = the `go.Figure` from the most recent `make_chart` call. The spec's `series` values are written by the model — they are not re-fetched from the DataFrame. So while the model *intends* the chart series values to match its prose values (typically because it copied them from the same `tool_result` block), there is **no programmatic guarantee** that `chart.data[i].y[j]` equals the corresponding number cited in prose.
4. **The caveats list** = a JSON array parsed out of a trailing triple-backtick `caveats` fence (`_CAVEATS_RE = re.compile(r"```caveats\s*\n(.*?)\n```\s*$", DOTALL|IGNORECASE)`, specialist.py:58-61). Parsed in `_split_caveats` (lines 307-324); fails open with an empty list on malformed JSON.

So inside a single specialist call, **the prose, the table, and the chart all share a single SQL source of truth, but the chart values pass through the model** and are not re-derived from the table. The table itself is direct from SQL.

Separately, the **dashboard pattern chart** for the question id (rendered by `chat.py::_render_question_chart` via `QUESTION_RENDERERS[qid]`) is computed by helpers in `src/dashboard/data.py` — for example, P1's pattern chart uses `data.py::category_peer_pricing_gaps` (`data.py:775`), P3 uses `data.py::category_pricing_leverage` (`data.py:909`). These helpers run their own SQL (with the current filter state applied) and feed the result to `chart_patterns.py`. The pattern chart is therefore **independent of the agent's SQL**: it can show different numbers because it uses different aggregation windows, filters, or peer joins. Per the design note in `chart_takeaways.py:14-27`, captions on the pattern chart are intentionally **directional** ("widest gap is in beverages") rather than numeric, to mask this divergence.

### 6.8 SQL surfacing

The specialist's `_sql_log` is returned in `SpecialistResponse.sql` (line 299). The chat panel stores it on the response dict. Whether and how the chat UI renders an expander to show the SQL was not fully determinable from a static read of `chat.py` (1,261 lines); the response dict carries the data, and per `src/agents/CLAUDE.md` the rule is "Final answers must include the SQL." Could not determine without running the dashboard whether the expander is currently visible by default.

### 6.9 Tool definitions surfaced to the model

From `tools.py:SCHEMA_INFO_TOOL` (lines 70-81), `QUERY_TENANT_TOOL` (lines 83-101), `QUERY_LAKE_TOOL`, `MAKE_CHART_TOOL`. `TOOLS_SPECIALIST = [SCHEMA_INFO_TOOL, QUERY_TENANT_TOOL, QUERY_LAKE_TOOL, MAKE_CHART_TOOL]`. The descriptions tell the model about the SELECT-only rule and the mandatory `WHERE merchant_id = '…'` predicate; the prompt reinforces these.

### 6.10 Prompts

All five prompts live as `.md` files in `src/agents/prompts/` and are loaded once at class construction via `Path.read_text()` then string-replaced (`{{viewer_id}}`, `{{viewer_name}}`, `{{viewer_segment}}`). The orchestrator prompt also gets a routing-normalized segment label (`grocer`/`qsr`/`retail`/`unknown`) via `_segment_for_merchant`. The specialist prompts dictate the four-part answer shape (Headline / Evidence / Therefore / Caveats), require every number to come from a tool call, and prescribe the triple-backtick `caveats` fence at the end so the parser can split prose from caveats.

---

## 7. Dashboard

### 7.1 Page structure (`src/dashboard/app.py`, 229 lines)

Single Streamlit script. Top-level layout:

- **Header** (top of `app.py`): page config, merchant selectbox, date-range and store-filter row, KPI sparkline strip.
- **Body** (vertical sections rendered by `src/dashboard/views.py`):
  1. KPI Strip (4 cards: Revenue, Customers, Avg Txn, Units).
  2. Performance section (60/40 split: map + insights).
  3. Geography section (Folium map with store markers, color-coded by metric).
  4. Catalog section (category mix, top products).
  5. Customers section (new vs returning, cohorts, density).
- **Chat panel overlay** rendered by `src/dashboard/chat.py::render_chat_panel`. Three states controlled by `state.chat_state`:
  - `"closed"` — a small `✦` edge button.
  - `"side"` — 40vw wide panel with a scrim backdrop.
  - `"expanded"` — 90vw wide panel.

### 7.2 Session state (initialized in `app.py:68-77`)

```python
state.setdefault("merchant_id", "KRG")
state.setdefault("filters_by_merchant", {})  # per-merchant filter dicts
state.setdefault("chat_messages_by_merchant", {})  # per-merchant chat history
state.setdefault("active_agent", "pricing")
state.setdefault("chat_state", "closed")
state.setdefault("chat_expanded", False)  # legacy
```

`filters_by_merchant[m]` shape: `{"date_start": date, "date_end": date, "stores": [store_ids], "categories": []}` (categories filter is set programmatically only, no UI control).

### 7.3 Chat panel flows

`chat.py::render_chat_panel` (lines ~997-1261 per the recon):

- Renders a specialist tab selector at the top, then the suggested-question pills for the current `(merchant_segment, active_agent)` combination from `src/dashboard/questions.py`. Pill button key = `q_{merchant_id}_{agent}_{qid}` to avoid Streamlit's key collisions across merchants.
- Renders prior chat history from `state.chat_messages_by_merchant[merchant_id]`.
- Renders a free-form input form at the bottom (`clear_on_submit=True`).
- On submit/click, sets `state.pending_dispatch` and `state.agent_running`; Streamlit reruns. On the next render, `_render_live_turn` (lines 937-990 per the recon) is invoked with a `runner` callable.

For a suggested question, `runner` is `lambda p, t: agents.dispatch(agent_id, qid, merchant_id, progress=p, on_token=t)`. For a free-form question, `runner = lambda p, t: agents.dispatch_orchestrated(merchant_id, question, progress=p, on_token=t)`.

`_render_live_turn` creates the assistant chat bubble, holds a placeholder for streamed text, registers `on_progress(turn_idx, message)` and `on_token(delta)` callbacks, then awaits the runner's return. As text deltas arrive, the placeholder updates. When the runner returns the response dict, `_finalize_response` (the recon's term) renders the final components.

### 7.4 Rendering of an agent response

The response dict shape (from `SpecialistResponse.to_dict` or `OrchestratorResponse.to_dict`):

```python
{
    "agent": str,                       # display label
    "prose": str,                       # final narrative; caveats fence stripped
    "table": pd.DataFrame | None,       # last query result, full precision
    "chart": plotly.go.Figure | None,   # from make_chart tool call
    "caveats": [str],                   # parsed from caveats fence
    "sql": [{"tool", "query", "row_count"}, ...],
    "telemetry": {input_tokens, output_tokens, cost_usd, turns, converged},
    "routing": {...} | None,            # orchestrator path only
}
```

Render order (per the recon's read of `chat.py`):

1. `prose` → `st.markdown` after `_escape_dollars` (`$` would otherwise trigger Streamlit's LaTeX inline math). Caveats fence stripped via `_strip_caveats_tail`.
2. `chart` → `st.plotly_chart(chart, use_container_width=True)` if present. This is the agent's chart.
3. `table` → `st.dataframe(table, use_container_width=True)` if non-empty. This is the agent's last SQL result, full precision.
4. `caveats` → bullet list, italic label, dollar-escaped.
5. `QUESTION_RENDERERS[qid]` (registered in `chat.py` lines 758-791 per the recon) — if the qid has a registered pattern-chart renderer (e.g., `_render_p1`, `_render_p3`), call it. Each renderer fetches fresh data from `data.py` (current filter state applied), renders via `chart_patterns.render_*()`, and appends a directional caption from `chart_takeaways.compute_takeaway(qid, …)`.

### 7.5 Pattern charts (`src/dashboard/chart_patterns.py`, 1,844 lines)

Nine pattern families used across both dashboard sections and chat responses:

1. Time-series-vs-peers (line chart with peer overlays).
2. Cross-merchant comparison (horizontal bar or grouped bar).
3. Heatmap (diverging red/white/blue or sequential).
4. Scatter with peers (used for "leverage" / quadrant visualizations).
5. Waterfall / decomposition (stacked bars showing driver contributions).
6. Geographic map (Folium with neighborhood polygons, store markers, color scale; uses `streamlit-folium`).
7. Small multiples (placeholder; not heavily used).
8. KPI callout (single metric + sparkline + delta).
9. Table with drilldown — uses `pandas.Styler.background_gradient(cmap="RdBu", subset=cols, vmin=-vabs, vmax=vabs)` (`chart_patterns.py` ~line 1642). This is the reason `matplotlib>=3.8,<4.0` is in `pyproject.toml` — pandas Styler's `background_gradient` requires it. The commit `b1dc3d7` "deploy: add matplotlib to runtime deps for pandas Styler.background_gradient" confirms this.

### 7.6 Caching

- Per-session LLM cache in `st.session_state["llm_cache"]` keyed by `(agent_id, qid, merchant_id)` (`agents.py:90, 102`). Suggested questions only; never caches errors; lifetime = browser session.
- `@st.cache_data` decorators on many helpers in `data.py` keyed by `(merchant_id, _filters_key(filters))` so KPI/pattern-chart data is reused across reruns within a session.
- `@st.cache_resource` for shared resources where applicable (e.g., a DB connection in some helpers).

### 7.7 Mock fallback

Per `src/agents/CLAUDE.md`: if `ANTHROPIC_API_KEY` is missing or the LLM call raises, the dispatch wrapper falls back to a hardcoded handler (`_hardcoded_dispatch` / `HANDLERS` registry) for suggested questions. The exact location is described in the CLAUDE.md as `src/dashboard/placeholders.py`, but that file does not exist — see §10. The current `src/dashboard/agents.py` has `_error_response` and `_orchestrator_error_response` builders (lines 55, 68); whether and how a hardcoded fallback for canned questions is wired in `agents.py` vs returned as an error could not be fully traced from static reading.

---

## 8. Config & how to run

### 8.1 CLAUDE.md files (3)

- **Root `CLAUDE.md`** — accurate. Locks the data design to `V2_5_DATA_DESIGN.md`, summarizes the panel (5 merchants, 10k customers, Mar 1 – May 29 2026), captures generation/privacy/DB/agents/code conventions. Notes that final answers must include the SQL the agent ran.
- **`src/generate/CLAUDE.md`** — accurate. Describes the module architecture, cross-merchant `customer_id` invariant, no-PII rule, reproducibility, default volume target (~230k-240k in ~80s), and listed CSV outputs.
- **`src/agents/CLAUDE.md`** — mostly accurate, **stale in one section**. References `src/dashboard/placeholders.py::_llm_dispatch` and `_hardcoded_dispatch` for suggested-question dispatch. The current file is `src/dashboard/agents.py` with `dispatch`, `dispatch_orchestrated`, and `_run_specialist`. The conceptual model (suggested vs free-form, hardcoded fallback) is still accurate; only the file/function names are stale.

### 8.2 Environment

- `ANTHROPIC_API_KEY` loaded from `.env` via `python-dotenv` (per root `CLAUDE.md`). `.env.example` provided.
- On HF Spaces, the entry point promotes `st.secrets["ANTHROPIC_API_KEY"]` into the process env (`streamlit_app.py:21-26`) wrapped in `try/except` to handle the local case where no `.streamlit/secrets.toml` exists.

### 8.3 Deployment — HF Spaces (Docker SDK)

Confirmed from `README.md` YAML header:

```yaml
---
title: Payments Data Strategy
emoji: 📊
sdk: docker
app_port: 8501
license: mit
---
```

Live URL stated in README: `https://huggingface.co/spaces/viveks2862/payments-data-strategy`. The deployment chain:

1. HF Spaces builds the Docker image from `Dockerfile` (python:3.11-slim, UID 1000 user, `pip install -r requirements.txt`, copy app, `EXPOSE 8501`).
2. Container start: `streamlit run streamlit_app.py --server.port=8501 --server.address=0.0.0.0 --server.headless=true --browser.gatherUsageStats=false`.
3. `streamlit_app.py`:
   - Loads `ANTHROPIC_API_KEY` from `st.secrets` if present.
   - Checks `data/payments.db`. If missing (cold container), runs `src.generate.run_all` then `src.db.seed` as subprocesses inside the same container with `st.spinner` UI. ~2 minutes.
   - Once DB exists, runs `src/dashboard/app.py` via `runpy.run_path(name="__main__")` so Streamlit's rerun loop picks up the dashboard cleanly.

Per `README.md`: the DB is not in git because the per-viewer materialized lake brings it to ~2.7 GB. HF Pro is required to keep the container warm; otherwise every visitor pays the 2-minute cold start.

### 8.4 Deployment — GitHub Pages

No `.github/` directory. No Pages-specific config files found. `docs/index.html` and `docs/report.html` exist (latter built by `scripts/build_report_html.py` from `docs/report_data.json`). Could not determine without checking the repo's Pages settings on GitHub whether these are actually served via Pages.

### 8.5 Local commands

```bash
# Install deps (Python 3.11+, uv)
uv sync

# Setup .env
cp .env.example .env  # then paste ANTHROPIC_API_KEY

# One-time: generate data + load DB
make seed   # = uv run python -m src.generate.run_all && uv run python -m src.db.seed

# (Optional) build the static interactive report
make report # = uv run python scripts/generate_report_data.py && build_report_html.py

# Launch dashboard
make demo   # = make seed + make report + uv run streamlit run src/dashboard/app.py

# Run tests (LLM tests opted out via pytest marker)
make test

# Wipe and rebuild
make clean && bash scripts/demo.sh
```

`pyproject.toml` enforces `python>=3.11`. Pytest config:

```toml
addopts = "-v --tb=short -m 'not llm'"
markers = ["llm: tests that hit the Anthropic API (opt-in via `-m llm`); requires ANTHROPIC_API_KEY"]
```

So `make test` runs only non-LLM tests by default. The LLM cassette infrastructure (`tests/cassette_helpers.py`, `tests/cassettes/baseline/*.json`, `scripts/record_baseline_cassettes.py`, `scripts/run_phase5_regression.py`) supports recording and replaying agent responses for regression.

---

## 9. End-to-end trace

**Question:** "Which categories show the biggest pricing-leverage opportunity?" — qid **P3** for grocer merchants (`src/dashboard/questions.py:24-26`, pattern `pattern_4_scatter`), as Kroger (`merchant_id = "KRG"`).

This question exercises both `query_tenant` and `query_lake`, and per the prompts will usually trigger a `make_chart` call.

| # | Hop | File / function |
|---|---|---|
| 1 | User loads dashboard at `http://localhost:8501`. Streamlit boots `app.py`. Sidebar/header initializes merchant selectbox; KRG is the default. | `src/dashboard/app.py` (top of file) |
| 2 | KPI strip, geography, catalog, customer sections render via `views.py` from cached `data.py` helpers. Chat panel is initially closed (`state.chat_state = "closed"`). | `src/dashboard/views.py` + `data.py` |
| 3 | User clicks the `✦` button to open the chat panel. `state.chat_state` flips to `"side"`; rerun. `chat.py::render_chat_panel` renders the panel with the Pricing tab active (`state.active_agent = "pricing"`). | `src/dashboard/chat.py::render_chat_panel` |
| 4 | Pricing tab shows three pills for grocer/pricing (P1, P2, P3) from `questions.py`. User clicks the P3 pill — button key `q_KRG_pricing_P3`. Click handler sets `state.pending_dispatch = {"kind": "question", "qid": "P3", "agent_id": "pricing", "agent_label": "Pricing & Benchmarking Agent"}` and `state.agent_running = True`. Rerun. | `chat.py` |
| 5 | On rerun, `chat.py` sees `pending_dispatch.kind == "question"` and calls `_render_live_turn(runner=lambda p,t: agents.dispatch("pricing", "P3", "KRG", progress=p, on_token=t))`. | `chat.py::_render_live_turn` (lines ~937-990) |
| 6 | `agents.py::dispatch` (line 191) consults `st.session_state["llm_cache"][("pricing","P3","KRG")]`. Assume cache miss. | `src/dashboard/agents.py:dispatch` |
| 7 | `_run_specialist` (line 148) builds a `MerchantContext` (`viewing_merchant_id="KRG"`, etc.), looks up the question text "Which categories show the biggest pricing-leverage opportunity?" from `questions.py`, instantiates `PricingSpecialist(ctx)`. The `Specialist.__init__` reads `prompts/pricing.md` and substitutes the `{{viewer_*}}` placeholders. | `agents.py::_run_specialist`, `specialist.py:115-129`, `pricing.py:1-20` |
| 8 | `spec.answer(question, progress=…, on_token=…)` enters the tool loop. Turn 0: `progress(0, "Looking up your data…")`. | `specialist.py:144-244` |
| 9 | First LLM call: `L.call_with_tools_streaming(model="claude-haiku-4-5-20251001", system=pricing_prompt, tools=[schema_info,query_tenant,query_lake,make_chart], messages=[{user:"Which categories…"}], max_tokens=4096, on_text_delta=on_token)`. Model returns `stop_reason="tool_use"` with one or more `tool_use` blocks (typically `query_tenant` to get own category prices first). | `src/agents/llm.py::call_with_tools_streaming` |
| 10 | Specialist dispatches the `query_tenant` block. `tools.py::query_tenant(query, merchant_id="KRG", db_path)`: `is_safe_select` passes; `has_merchant_predicate("KRG")` passes (the prompt requires `WHERE merchant_id = 'KRG'`); CTE-wrap so `tenant_*` references resolve to `tenant_view_KRG_*`; `_exec_select` runs against `data/payments.db?mode=ro`; returns `{columns, rows (≤200), row_count, truncated}`. Specialist appends to `_sql_log` and stores in `_last_table`. | `tools.py::query_tenant`, `_exec_select` |
| 11 | Result is trimmed via `trim_for_llm` (cap 20 rows, 2-decimal floats) and posted back as a `tool_result` block. Loop continues. | `tools.py::trim_for_llm` (lines 319-376) |
| 12 | Turn 1: `progress(1, "Comparing with peer data…")`. Second LLM call. Model issues `query_lake` for peer category prices. | same |
| 13 | `tools.py::query_lake`: single-SELECT check; rejects any `tenant_*` reference; wraps with `WITH lake_transactions AS (SELECT * FROM lake_transactions_KRG), lake_stores AS (SELECT * FROM lake_stores_KRG)`; executes; `_maybe_suppress_sub_k` inspects result for count columns (the prompt instructs the model to include `COUNT(*) AS n`). Rows with `n < 5` are dropped, `"suppression"` added to the dict. Result is appended to `_sql_log`, `_last_table` updated. | `tools.py::query_lake`, `_maybe_suppress_sub_k` |
| 14 | Turn 2: `progress(2, "Building the comparison…")`. Third LLM call. Typical model behavior on P3 (pattern_4_scatter): emit a `make_chart` block with `kind="grouped_bar"` or scatter-like spec, x = category names, series = `[{name:"You", values:[…]}, {name:"peer_a", values:[…]}, …]`, `y_format="currency"` or `"pct"`. | same |
| 15 | `tools.py::make_chart` builds a `go.Figure` and stores it on `spec._chart`. Returns `make_chart_ack` (small dict) as the tool_result. | `tools.py::make_chart` |
| 16 | Turn 3: `progress(3, "Finalizing analysis…")`. Fourth LLM call. Model emits final prose (Headline / Evidence / Therefore / Caveats fence) with `stop_reason="end_turn"`. As text deltas stream, `on_token` fires and `_render_live_turn` updates its placeholder in the UI. | same; `chat.py::_render_live_turn` |
| 17 | `_finalize(text, converged=True, turns=4)`: `_split_caveats` (`specialist.py:307-324`) matches the trailing ` ```caveats\n[...]\n``` ` fence, parses the JSON list, splits prose from caveats. Builds DataFrame from `_last_table`. Returns `SpecialistResponse`. | `specialist.py:_finalize`, `_split_caveats` |
| 18 | `SpecialistResponse.to_dict()` shapes the result for the dashboard. `dispatch` caches the dict in `st.session_state["llm_cache"][("pricing","P3","KRG")]` and returns it. | `agents.py::dispatch` |
| 19 | `_render_live_turn` renders the final response in the assistant bubble: prose via `st.markdown` (dollar-escaped), then the agent's chart via `st.plotly_chart`, then the agent's table via `st.dataframe`, then caveats as a bullet list. | `chat.py::_render_live_turn` |
| 20 | After the agent response, `chat.py` calls `QUESTION_RENDERERS["P3"]` (registered in `chat.py:173 _render_p3`). That helper calls `data.py::category_pricing_leverage("KRG", filters)` (line 909) which runs SQL via `data.py::_category_pricing_leverage_cached` (cached by `@st.cache_data`), formats it for `chart_patterns.py::render_scatter_quadrant` (or whatever pattern_4 helper), and renders a pattern chart with a directional caption from `chart_takeaways.compute_takeaway("P3", …)`. | `chat.py::_render_p3`, `data.py::category_pricing_leverage`, `chart_patterns.py`, `chart_takeaways.py` |
| 21 | The user sees: a routing line if free-form (not in this trace — suggested questions skip the orchestrator), an agent prose paragraph, an agent grouped-bar chart, an agent table of the lake result, a caveats list, then a pattern-chart heatmap or scatter (from independent SQL) with a caption. The agent's numbers and the pattern chart's numbers may differ. | UI |

Total LLM calls in this trace: **4** (all specialist; zero router calls because suggested questions skip the orchestrator). Total turns: 4 (or up to 10 — `MAX_TURNS`). If streaming is on, the user sees the prose text appear incrementally during turn 3.

---

## 10. Current limitations & risks

Observations only.

### 10.1 The "single source of truth" gap

Inside one specialist call, the agent's prose and the agent's chart share the same SQL tool result, but the chart's `series.values` are *re-written by the model* in the `make_chart` spec — they are not programmatically derived from the DataFrame. If the model mis-transcribes a number from `tool_result` to chart spec, the chart and prose will disagree and there is no check to catch it.

Across the agent and the dashboard pattern chart, the two run **independent SQL** against different aggregation windows and filter states. The dashboard pattern chart for qid P3 uses `data.py::category_pricing_leverage` (cached on the current filter dict); the agent runs whatever SQL it decides on. Numbers can and do diverge, intentionally accepted per Phase 5.5.2's design note in `chart_takeaways.py:14-27`. Pattern-chart captions are kept directional to mask this.

### 10.2 Data realism

- **Unit prices have no per-line noise** (`transactions.py:935`, explicit `# v2.5: no noise`). Every basket line for SKU X at merchant M has the same `unit_price` to two decimals. Real POS data has occasional price changes within a day.
- **Customer-store "closest" is a per-customer shuffle, not real distance.** The `(0.70, 0.25, 0.05)` "closest/second/other" weights apply to a list whose ordering is random per customer (`transactions.py:591`). A given physical store will be "closest" for some customers and "third" for others, even if they share a ZIP.
- **No within-customer basket history learning.** Each trip's SKU sampling is independent given the customer's affinity and the basket's archetype. A customer who bought a SKU last week is no more or less likely to buy it again.
- **No annual seasonality.** Window is 90 days; only the pay-cycle, day-of-week, hour-of-day, peak-day, peak-hour, and three planted anomalies create temporal structure.
- **Payment fields are independent of customer.** `card_network`/`entry_mode`/`wallet_type`/`connectivity_type` are drawn per transaction off the merchant's segment and a global wallet/connectivity distribution. A given customer's transactions do not consistently use the same network or entry mode beyond what random sampling would produce.
- **No declines, no cash, no EBT, no PAN/BIN/last4.** Out of scope by design.

### 10.3 Stale documentation

- `src/agents/CLAUDE.md` references `src/dashboard/placeholders.py::_llm_dispatch` and `_hardcoded_dispatch`. The file is `src/dashboard/agents.py` with `dispatch`, `dispatch_orchestrated`, `_run_specialist`. The conceptual model is right; only the names/paths are stale.
- The README architecture diagram says "lake (virtual)" but post-Phase-1.5 the lake is **physically materialized** per viewer at seed time. Logically virtual at agent interface, physically present in `data/payments.db`. Worth flagging if anyone reads the diagram literally.

### 10.4 K=5 suppression is name-based, not value-based

`_maybe_suppress_sub_k` inspects column names against a regex (`count`, `cnt`, `n`, `*_count`, `*_n`). A model that writes a lake query producing customer-level small cells under a column named `"unique_users"` or `"buyers"` would bypass the suppression. The prompt instructs the model to use `COUNT(*) AS n` on customer-dimension breakdowns, but this is not enforced.

### 10.5 Vestigial code

- `register_lake_functions` (`views.py:304-312`) registers three UDFs on every read-only connection (`tools.py::_exec_select`, with `register_lake=False` by default at lines 286-316). Post-Phase-1.5 these are not invoked at agent query time — they were needed when the lake template ran at query time. They are still called at seed time when building the materialized tables.
- `src/db/queries.py` contains five canned SQL helpers; none appear to be imported by the live dashboard or agents based on grep.

### 10.6 Bytecode/prompt staleness in dev

Per `src/agents/CLAUDE.md`, Streamlit's hot-reload does NOT reliably pick up changes to prompt files (`src/agents/prompts/*.md`), class attributes (`MAX_TURNS` etc.), or model identifiers in `src/agents/llm.py`. Python's bytecode cache holds the previous values until the Streamlit process is restarted. This is fragile during prompt iteration.

### 10.7 DB size & deployment

The materialized per-viewer lake is the reason `data/payments.db` reaches ~2.7 GB and cannot be committed. The cold-boot regen path in `streamlit_app.py` (~2 minutes) is the user-facing consequence. HF Pro is required to keep the container warm; on free-tier Spaces every visitor would pay the cold start.

### 10.8 SQL surfacing in the UI

The specialist's `_sql_log` is returned in the response dict, and `src/agents/CLAUDE.md` states "Final answers must include the SQL" with "an expander" implied. Whether the chat UI currently renders this expander by default could not be determined from static reading of `chat.py` — could not verify without running the dashboard.

### 10.9 SELECT-only guarantees vs SQLite UDFs

The SQL guards are regex-based (`is_safe_select`, `has_merchant_predicate`). They run *before* the DB connection opens, which is good. They allow read-only connection mode (`?mode=ro`) which prevents writes structurally. UDFs are registered but only for read paths. No write path exists in the runtime — agents cannot mutate the DB by construction.

### 10.10 Tight 2026 date literals in prompts

`src/agents/prompts/demand.md` (per the recon) includes specific date literals like "2026-05-23 to 2026-05-29 = last 7 days". These will rot if the dataset window shifts. The data layer's window is fixed in `parameters.py:34-36` (`START_DATE = 2026-03-01`, `END_DATE = 2026-05-29`, `DAYS = 90`), so as long as those don't change, the prompt dates stay aligned. Worth flagging because the prompt files don't import the parameters — the alignment is by hand.

### 10.11 Defense-in-depth

Tenant isolation relies on:
1. Regex guard at `query_tenant` requiring the merchant_id predicate.
2. CTE wrap mapping `tenant_*` to `tenant_view_<M>_*`.

The CTE wrap is the load-bearing part — even if the predicate check were bypassed, the CTE remapping would still scope the query to the viewer's view. There is no separate SQLite user role with restricted permissions (SQLite doesn't support them), and the agent runs in the same process with full read access to the DB file. A coding bug that bypassed both the regex AND the CTE wrap would leak data; the architecture relies on those two checks being kept correct in `tools.py`.

### 10.12 Charts rely on the model for chart-spec correctness

`make_chart` accepts any `series.values` the model writes. If the model writes a values array of the wrong length relative to `x`, or with the wrong type, the Plotly Figure construction may fail. The handling of that failure (in `_dispatch_tool`'s `except Exception` at `specialist.py:217-219`) sets `result = {"error": ...}` and continues — the chart for that turn is simply absent. Whether the model retries successfully or whether the user sees a missing chart could not be determined from static reading.

---

**End of baseline.** Anything in this document can be verified against the file paths and line numbers cited. Where I was uncertain, I said so explicitly. The next round of improvements can use this as the "before" snapshot.
