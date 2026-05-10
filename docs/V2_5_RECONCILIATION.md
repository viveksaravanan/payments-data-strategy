# V2.5 Reconciliation — v2 → v2.5

Comparison of the existing v2 implementation in `src/` against the locked
target in `V2_5_DATA_DESIGN.md`. Drives the incremental refactor: anything
already aligned is preserved, anything that diverges is changed only as far
as the design requires.

Action key:
- **PRESERVE** — current code matches the target; do nothing.
- **MODIFY** — current code is close but needs schema/logic edits.
- **REPLACE** — current code is structurally wrong; rewrite the module.
- **ADD NEW** — no current implementation; new module needed.
- **REMOVE** — current code has functionality the target explicitly drops.

---

## 1. Reconciliation table

### 1.1 Panel-level constants and geography

| Component | v2 has | v2.5 specifies | Action | Notes |
|---|---|---|---|---|
| Panel size | 5,000 customers (`P.N_CUSTOMERS`) | 10,000 customers | **MODIFY** | Single constant flip; cascades to all downstream volumes. |
| Date window | 90 days ending **2026-05-05** | 90 days, **2026-03-01 → 2026-05-29** (covers Easter Apr 5, Memorial Day May 25) | **MODIFY** | `END_DATE` shift; `PROMO_DAYS` list also needs to be replaced (v2 picks Apr 15 / 22, May 1 — none align with the v2.5 holiday calendar). |
| Geography model | 25 ZIP3 prefixes spread across NY/CA/TX/OH/etc. | Single fictional Charlotte metro, ~30 named ZIP5s, with neighborhoods + metro region tiers | **REPLACE** | Whole geography assumption changes; need a `metro.py` (or similar) lookup of ZIP5 → neighborhood → region. |
| Merchant count | 3 (Kroger, Taco Bell, TJ Maxx) | 5 (+ Acme, Winn-Dixie) | **ADD NEW** | Two new full generators, two new catalog overlays. |
| Reproducibility (`RANDOM_SEED`) | Threaded through `generate_customers` and merchant generators | Same approach | **PRESERVE** | The single-`Generator` pattern carries over. |
| Behavioral-segment partition | `filler` / `stocker` (with overlapping `is_lapser` flag) | `filler` / `stocker` only; `is_lapser` not in target schema | **MODIFY** | Keep filler/stocker assignment; drop `is_lapser` (or rename into the affinity model). |
| `MERCHANT_CONFIGS` shape | One config dict per merchant with payment-mix, category-weights, etc. | Same pattern works, but five entries and **no EBT** | **MODIFY** | Drop `ebt` from all `payment_mix`. Add Acme + Winn-Dixie configs. |
| Promo-day flat list (`PROMO_DAYS`) | Three global flat-multiplier days | v2.5 promotions live in a real `tenant_promotions` table, not a global flat list | **REPLACE** | Promo logic moves into `tenant_promotions` driven by start/end dates and `discount_pct`. |

### 1.2 `merchants` table

| Component | v2 has | v2.5 specifies | Action |
|---|---|---|---|
| `merchants` schema | `merchant_id, name, segment, mcc` | Same four columns | **PRESERVE** |
| Merchant rows | KRG, TBL, TJX | KRG, ACM, WDX, TBL, TJX | **MODIFY** |
| `segment` values | `grocery`, `qsr`, `retail_offprice` | `grocery`, `qsr`, **`off_price_retail`** | **MODIFY** — global rename `retail_offprice` → `off_price_retail` (decided). String is referenced in advisor blurbs, queries, prompt text, and tests; grep before changing. |

### 1.3 `tenant_customers`

| Field | v2 has | v2.5 specifies | Action |
|---|---|---|---|
| `customer_id` | 16-char SHA-256 of HASH_SECRET+PAN; produced by anonymize stage from `customer_pan` | Same hash output, but produced **directly in the generator** | **MODIFY** — move SHA-256 logic into `customers.py`; the entire `src/anonymize/` stage collapses (Q1 resolved). |
| `customer_pan` | Stored in raw `customers.csv` for the join across merchants | Not in target schema (raw PAN should never exist) | **REMOVE** — never written to disk; lives only as a transient seed for the hash inside `customers.py`. |
| `customer_name`, `customer_email` | Generated as PII so anonymize stage has something to strip | Not in target | **REMOVE** — strategy doc field-mapping omits these; PII-strip theater not needed once Stage 1 collapses. |
| `age_band`, `income_band` | Sampled from weighted bands | **Not in v2.5** (demographics deferred to v3+) | **REMOVE** |
| `home_zip5` | Sampled from 25-ZIP3-prefix universe | Sampled from Charlotte metro ZIP5 set | **MODIFY** (logic preserved, vocabulary changed) |
| `behavioral_segment` (filler/stocker) | Present | Present | **PRESERVE** |
| `is_lapser` | Overlapping flag in v2 | Not in v2.5 — the lapsed cohort is represented by `grocer_affinity_type = 'lapsed_light'`, not a parallel flag | **REMOVE** |
| `grocer_affinity_type` | — | `loyalist` / `splitter` / `three-chain` / `lapsed_light` (55/30/12/3) | **ADD NEW** |
| `primary_grocer`, `secondary_grocer` | — | Required (KRG/ACM/WDX, with secondary nullable) | **ADD NEW** |
| `primary_card_type` | `credit/debit/ebt/mixed` from `[0.50, 0.35, 0.05, 0.10]` | `credit/debit/mixed` (no EBT) | **MODIFY** |
| `has_mobile_wallet` | 0/1 | Same | **PRESERVE** |
| `signup_date` | Random offset from END_DATE (≤ 5y back) | "When Verifone first observed this card" — same idea | **PRESERVE** |

### 1.4 `tenant_stores`

| Field | v2 has | v2.5 specifies | Action |
|---|---|---|---|
| `store_id` | `<merchant>-<state>-<NNNN>` | `<merchant>-NC-<seq>` (single state) | **MODIFY** (format simplification) |
| `merchant_id` | Present | Present | **PRESERVE** |
| `store_zip5` | 5-digit ZIP from arbitrary US prefixes | 5-digit Charlotte ZIP | **MODIFY** (vocabulary) |
| `region` | `midwest/south/west/northeast` | **`metro_region` ∈ {urban_core, inner_suburbs, outer_suburbs}** | **REPLACE** (column name + value vocabulary) |
| `neighborhood` | — | Required, e.g. `Plaza Midwood`, `University City` | **ADD NEW** |
| `latitude`, `longitude` | — | Required (ZIP centroid + ±0.02° jitter) | **ADD NEW** |
| `open_date` | Present | Present | **PRESERVE** |
| Per-merchant store counts | KRG 25, TBL 40, TJX 15 (= 80) | KRG 30, ACM 25, WDX 20, TBL 40, TJX 8 (= 123) | **MODIFY** (TJX especially: 15 → 8) |

### 1.5 `tenant_products`

| Field | v2 has | v2.5 specifies | Action |
|---|---|---|---|
| `sku` | `<MERCHANT>-<CATEGORY>-NNNN` for Kroger; per-type prefixes for TBL/TJX | `<merchant>-<CATEGORY>-NNNN` uniformly | **MODIFY** (regularize TBL/TJX SKU format) |
| `merchant_id`, `name`, `category`, `subcategory`, `base_price` | All present | All present | **PRESERVE** |
| `is_organic` | Present (0/1) | **Removed in v2.5** | **REMOVE** |
| `ebt_eligible` | Present on Kroger products (drives EBT-eligible basket sampler) | Not in target | **REMOVE** |
| Catalog source | Kroger from per-category JSON files; TBL/TJX hand-coded inline in `catalog_taco_bell.py` / `catalog_tjmaxx.py` | Shared **base grocery catalog** + per-grocer overlay files (KRG/ACM/WDX); TBL/TJX still per-merchant | **REPLACE** for grocers; **PRESERVE** the JSON-driven approach as a starting point but generalize it to read a base catalog + overlay |
| Pricing differentiation | None across merchants (each is its own catalog) | Two-tier multipliers (1.03/0.97 tight, 1.07/0.93 loose) on Acme/Winn-Dixie + ±2% per-SKU noise | **ADD NEW** |
| Catalog volumes | KRG ~1,000 (loaded), TBL 60, TJX 200 (≈1,260 total) | KRG ~1,100, ACM ~1,000, WDX ~880, TBL ~60, TJX ~200 (≈3,240 total) | **MODIFY** + **ADD NEW** |

### 1.6 `tenant_transactions`

| Field | v2 has | v2.5 specifies | Action |
|---|---|---|---|
| `txn_id` | `<merchant>-NNNNNNN` | Same | **PRESERVE** |
| `merchant_id`, `customer_id` (post-anonymize), `store_id`, `txn_ts` | Present | Present | **PRESERVE** (modulo source-of-`customer_id`; see §3) |
| `terminal_id` | — | Required string field, format `<store_id>-T<NN>`, no FK | **ADD NEW** |
| `payment_type` | `credit/debit/ebt/cash` | **`credit/debit` only** (no EBT, no cash, no declines) | **MODIFY** |
| `card_network` | Present | Present | **PRESERVE** |
| `entry_mode` | `chip/contactless/swipe` (+ cash/ebt special-cases) | `chip/contactless/swipe/manual` | **MODIFY** |
| `wallet_type` | Present | Present | **PRESERVE** |
| `connectivity_type` | — | Required: `wifi/cellular_4g/cellular_5g/ethernet` | **ADD NEW** |
| `subtotal` | — | Required | **ADD NEW** |
| `tax_total` | — | Required | **ADD NEW** |
| `txn_total` | Present (sum of line totals, no tax) | `subtotal + tax_total` | **MODIFY** (now needs the tax model) |

### 1.7 `tenant_transaction_items`

| Field | v2 has | v2.5 specifies | Action |
|---|---|---|---|
| `txn_id`, `line_id`, `sku`, `qty`, `unit_price`, `discount`, `line_total` | All present | All present | **PRESERVE** |
| `tax` | — | Required (`line_total × tax_rate(category)`); five-tier tax model | **ADD NEW** |
| `promo_id` | — | Required (nullable FK to `tenant_promotions`) | **ADD NEW** |
| Promo discount mechanism | Global `PROMO_DAYS` × random `PROMO_SKU_FRACTION` of catalog × flat `PROMO_DISCOUNT_RATE` | Discounts come from rows in `tenant_promotions` overlapping the txn date and the SKU | **REPLACE** |
| Quantity model | Always `qty=1` | Spec says `qty ≥ 1` | **MODIFY** (current single-qty is a known simplification; v2.5 needs occasional multi-qty for realism but spec doesn't pin a distribution — see open question Q3) |

### 1.8 `tenant_promotions` (NEW table)

| Component | v2 has | v2.5 specifies | Action |
|---|---|---|---|
| Table itself | — | `promo_id, merchant_id, sku, start_date, end_date, discount_pct, promo_name, promo_type` (one row per affected SKU); volumes ~73 promos across 90 days | **ADD NEW** |
| Generator | — | Drives discounts on transaction line items at generation time; not exposed to the lake | **ADD NEW** |
| Schema in `schema.sql` | — | Add | **ADD NEW** |

### 1.9 Generation orchestration / `run_all.py`

| Component | v2 has | v2.5 specifies | Action |
|---|---|---|---|
| Six-step generator flow | Customers → KRG → TBL → TJX → inject Kroger anomalies → write CSVs | Should be: customers + stores → products + promotions → transactions + items → planted anomalies → write | **REPLACE** (orchestration shape changes because new tables are added and anomalies move) |
| EBT injection logic in `base.py` | `ebt_eligible` filter, EBT-eligibility renormalization, EBT entry-mode special case | Removed entirely | **REMOVE** |
| Bimodal basket sizing (filler/stocker) | Present in `base.py` | The design's "bimodal basket archetype" rule applies | **PRESERVE** |
| Day-of-week / hour-of-day shaping | Present | Same intent (peak days/hours per config) | **PRESERVE** |
| Pay-cycle bumps | Present (`PAY_CYCLE_DAYS`, `PAY_CYCLE_MULTIPLIER`) | Design says "per-customer week-level variance" — pay-cycle is a reasonable proxy | **PRESERVE** (verify after Phase 1 Layer 4 detail; see open question Q4) |
| Per-customer Poisson trip count | Present | Design says "implementation rules for trip frequency" | **PRESERVE** (validate counts produce 180k–250k range after panel doubles) |
| Card-network sub-distribution | Present | Same | **PRESERVE** |
| `participation_rate` mechanism | Each merchant samples a fraction of the panel | Replaced by `primary_grocer` + `grocer_affinity_type` for grocers; TBL/TJX likely keep a participation-style sampler | **MODIFY** (grocer participation flows from affinity model, not flat rate) |

### 1.10 Anomalies

| Anomaly | v2 has | v2.5 specifies | Action |
|---|---|---|---|
| 1 — Avocado price spike (random KRG day, 5×) | Present in `_inject_price_spike` | **Plaza Midwood Kroger avocado spike, April 21–24, peak Apr 22, 4-day shape** — quantity-spike, not price-spike | **REPLACE** (target SKU still avocado-ish, but date-locked, store-locked to Plaza Midwood Kroger, quantity-driven) |
| 2 — Store dropout `KRG-OH-0011` (30% drop, last 7 days) | Present in `_inject_store_dropout` | **University City decline** — affects all three grocers' University City stores, late April / May, 4-stage ramp | **REPLACE** |
| 3 — Baby cohort surge (~50 customers, last 21 days) | Present in `_inject_baby_cohort` | **Acme failed pasta promo April 19–25** vs. Kroger competing promo April 15–21 succeeding | **REPLACE** (entirely new mechanism — promo lift differential, not a cohort surge) |

### 1.11 Anonymization / Privacy engine

| Component | v2 has | v2.5 specifies | Action |
|---|---|---|---|
| Stage 1 `tenant.py` (drop PII, hash PAN) | Drops `customer_name/email`, hashes `customer_pan` → `customer_id` | If PII is removed at the generation stage (per §1.3), this whole stage can collapse into **identity copy** (or be removed entirely) | **REMOVE** (or thin to a no-op) |
| Stage 2 physical lake CSVs (`data/anon/lake/`) | Three CSVs: `lake_customers`, `lake_transactions`, `lake_transaction_items` | Lake is **virtual** — implemented as parameterized query functions (`get_lake_transactions(viewing_merchant_id)` etc.) | **REPLACE** |
| `lake_customers` table | Has age_band/income_band/zip3/k-anon | Customer table is **dropped** from lake entirely (suppression of consumer linkage; demographics also gone) | **REMOVE** |
| `lake_stores` | None (denormalized into `lake_transactions`) | New explicit `lake_stores` (opaque IDs, ZIP3, neighborhood, region, peer_id, peer_segment) | **ADD NEW** |
| `lake_transactions` (target) | One row per txn; line items are a separate table | One row **per line item** — wide, denormalized; 21 columns including canonical product name + category + line-level price/qty/discount | **REPLACE** |
| `lake_transaction_items` | Present (qty + line_total aggregated to category) | Does not exist in v2.5 | **REMOVE** |
| ZIP truncation | ZIP5 → ZIP3 (`generalize.truncate_zip`) | Same | **PRESERVE** |
| Hour bucketing | Per-hour string truncation (`txn_hour_bucket`) | **10 named 2-hour buckets** (`early_morning`, `morning`, ... `late_night`) | **REPLACE** |
| Txn total binning | Not present (v2 lake exposes exact `txn_total`) | 10-bin `txn_total_bin` scheme ($0–5 … $250+) | **ADD NEW** |
| K-anonymity | k=5 on `(age_band, income_band, home_zip3)` for `lake_customers` | k=5 on **aggregate customer-dimension queries** (cohort sizes by ZIP3, behavioral patterns by neighborhood) — not on a customer table that no longer exists | **REPLACE** (mechanism moves from row-level suppression to aggregate-cell suppression at query time) |
| Customer-level lake linkage | `lake_transactions.customer_id` present | **`customer_id` dropped from lake** per "no consumer linkage" | **REMOVE** |
| Per-merchant peer mapping (peer_a/b/c/d) | None | Required, deterministic from segment then merchant_id | **ADD NEW** |
| Opaque IDs (`lake_txn_id`, `lake_store_id`) | None | Required, generated to prevent merchant-prefix leakage | **ADD NEW** |
| Differential privacy stub | `dp.py` (no-op + docstring) | Explicitly deferred to v3+ | **PRESERVE** (no work needed; stub remains accurate) |

### 1.12 Database (`schema.sql`, `seed.py`)

| Component | v2 has | v2.5 specifies | Action |
|---|---|---|---|
| `merchants` table | Yes | Yes | **PRESERVE** |
| `tenant_*` physical tables | customers, stores, products, transactions, transaction_items | All those + `tenant_promotions`, with column changes | **MODIFY + ADD NEW** |
| `lake_*` physical tables | customers, transactions, transaction_items | **None.** Lake is parameterized query functions over tenant tables | **REMOVE** |
| `seed.py` ingest | Loads CSVs from `data/anon/{tenant,lake}/` | Loads tenant CSVs only; no lake CSVs to load | **MODIFY** |
| Indexes on lake tables | Present | Not needed (no physical lake tables) | **REMOVE** |
| `PRAGMA foreign_keys = ON` per connection | Present in `seed.py` and `tools.py::_exec_select` | Same requirement | **PRESERVE** |

### 1.13 Agents and tools

| Component | v2 has | v2.5 specifies | Action |
|---|---|---|---|
| Tool `query_tenant` | Enforces `WHERE merchant_id = '<x>'` | Same intent | **PRESERVE** |
| Tool `query_lake` | Direct SELECT on `lake_*` tables | Must call `get_lake_*(viewing_merchant_id)` view-builder; viewing merchant must be threaded in | **REPLACE** |
| Tool `schema_info` | Returns the static `schema.sql` text | Should describe both physical tenant tables and the virtual lake view shape | **MODIFY** |
| Tool `chart_spec` | Metadata-only chart hint | Unaffected by data redesign | **PRESERVE** |
| `MerchantAdvisor` (per-merchant) | Present, takes `current_merchant_id` | Strategy doc spec: 7 merchant-scoped agents (Demand Forecasting, Pricing & Benchmarking, Consumer Segmentation, Trade Area, Payment Optimization, Anomaly Detection, Conversational Advisor) | **MODIFY** (the existing single advisor maps to "Conversational Business Advisor" #7; the other six are net-new and can be deferred — see open question Q5) |
| `NetworkAnalyst` (lake-only, no tenant context) | Present | **Not in v2.5** — "all agents are merchant-scoped; no network-level analyst" | **REMOVE** |
| `MAX_TURNS = 6` | Present | Not contradicted | **PRESERVE** |
| Mock mode | Present | Not contradicted | **PRESERVE** |
| `prompts/advisor.md`, `prompts/analyst.md` | Present | Advisor prompt needs to learn the new schema + peer concept; analyst prompt removed | **MODIFY + REMOVE** |

### 1.14 Dashboard, queries, tests

| Component | v2 has | v2.5 specifies | Action |
|---|---|---|---|
| Dashboard role selector | Kroger / Taco Bell / TJ Maxx / Network Analyst | Five merchants, no Network Analyst | **MODIFY + REMOVE** (Phase 5d) |
| Canned questions per role | Hard-coded list per role | Updated to match the new anomalies and peer mapping | **MODIFY** (Phase 5d) |
| `db/queries.py` canned SQL | References `lake_transactions.merchant_id`, `lake_transactions.customer_id`, etc. | Those columns no longer exist post-refactor (peer_id replaces merchant_id; customer_id dropped) | **REPLACE** (Phase 5b) |
| `tests/test_generation.py` | EBT-only-at-Kroger test, cross-merchant `customer_pan` invariant | EBT test deletes; PAN invariant becomes "same `customer_id` across merchants for a given physical customer" | **MODIFY** (Phase 1) |
| `tests/test_anonymize.py` | Tests CSV pipeline outputs | Tests validate the view-builder instead | **REPLACE** (Phase 5a creates `test_lake_views.py`; Phase 5c deletes the old file) |
| `tests/test_db.py` | Tests `lake_*` tables are loaded | Lake tables don't exist; trims to tenant-only DB smoke tests | **MODIFY** (Phase 5c) |
| `tests/test_agents.py` | Tests merchant-isolation regex | Regex stays; lake-tool tests update for view-builder signature; analyst tests removed | **MODIFY** (Phase 5b drops lake-table refs; Phase 5d drops analyst tests) |

### 1.15 Scripts

| Component | v2 has | v2.5 specifies | Action |
|---|---|---|---|
| `scripts/demo.sh` (19-line wrapper) | `make clean && make seed && make demo`; help text mentions "anonymize (tenant + lake)" | Same wrapper still works; help text needs to drop "anonymize" reference | **MODIFY** (Phase 5c — same phase that drops the Makefile anonymize step) |
| `scripts/generate_report_data.py` (529 lines) | Reads v2 schema deeply: `lake_customers`, `lake_transactions.merchant_id`, `lake_transactions.customer_id`, `tenant_customers.age_band/income_band`, `is_organic`, raw-CSV PII (`_anonymization_demo`); imports `src.anonymize.hash`; hardcodes `MERCHANT_KEYS = {KRG, TBL, TJX}`; embedded `_schema()` constant duplicates `schema.sql`; pay-cycle commentary references SNAP/EBT | The functions referenced no longer return; merchants list grows to five; the anonymization-demo function loses its source data when PII is dropped from raw output | **REPLACE** — but staged: **Phase 1** stub the file to print "v2.5 report TBD; rerun after Phase 5" so `make report` doesn't crash mid-refactor. **Phase 5c** rewrite to query view-builders for cross-merchant sections and tenant tables for per-merchant sections. **Phase 6** add anomaly callouts to the report payload. (Low priority feature, but high blast radius if left referencing deleted symbols.) |

### 1.16 Build, instructions, narrative docs

These are listed separately because each phase updates the relevant files
inline (per the rule at the top of §2), rather than batching them all into
a final pass.

| File | Why touched | Phase that owns the update |
|---|---|---|
| `Makefile` | Build flow changes when stages collapse | Phase 5c (drop `anonymize` invocation from `seed` target) |
| `CLAUDE.md` (root) | Project-wide instructions read by future Claude sessions; describes 3 merchants, 5,000 customers, EBT, dual-path tenant/lake | **Phase 0** (project-wide context) |
| `src/generate/CLAUDE.md` | Generator instructions read while writing generator code; describes EBT rules, 5,000 customer panel, PII intent, three merchants, anomalies | **Phase 1** (before generator changes) |
| `src/agents/CLAUDE.md` | Agent instructions; describes Network Analyst, the lake schema, the tools | **Phase 5b** (when the lake tool changes) |
| `DATA.md` | Synthetic data spec | **Phase 1** (panel + customer + store sections) → **Phase 2** (catalog architecture) → **Phase 3** (promotions + tax + transaction-field additions) → **Phase 4** (final field-level pass) |
| `ARCHITECTURE.md` | Strategy-doc mapping, dual-path framing | **Phase 1** (panel/metro + agent-roster framing) → **Phase 5c** (lake-as-views replaces dual-path narrative) |
| `PLAN.md` | Build plan + demo script | **Phase 6** (demo script needs new anomalies) → **Phase 7** (final readthrough) |
| `README.md` | Top-level overview | **Phase 7** (small surface; do last) |
| `V2_AUDIT.md` | v2-era audit notes | **Phase 7** (delete or move to `docs/archive/`) |

---

## 2. Phased implementation plan

Each phase is independently runnable: `make seed && make test` should pass at
every phase boundary. Phases are ordered so each one delivers a coherent slice
without leaving the codebase wedged.

**Doc updates are part of the phase, not deferred.** Two reasons:

1. **`CLAUDE.md` files are instructions, not documentation.** Future Claude
   sessions read them as their working context. If `src/generate/CLAUDE.md`
   describes EBT rules during Phase 4, the next session will reintroduce
   EBT logic. So `CLAUDE.md` files are updated in the *first* phase that
   touches their domain — never deferred.
2. **Spec docs (`DATA.md`, `ARCHITECTURE.md`) drift if not updated
   alongside code.** Each phase that changes schema, panel structure, or
   data architecture updates the corresponding section of these docs in
   the same PR.

By the time Phase 7 starts, there should be very little left in it: a
final consistency readthrough, README cleanup, and `V2_AUDIT.md`
disposition. Phase 7 is *not* the place to discover that
`src/generate/CLAUDE.md` still describes 5,000 customers.

The owning-phase mapping for each doc is in §1.16.

**The Makefile** is a build instruction file too. Update it in the phase
that changes build behavior (currently only Phase 5c — the `anonymize`
step removal — but flag any future phase that adds a new make target).

### Phase 0 — Lock the surface area

**Goal.** Add v2.5 constants alongside v2 constants without breaking anything,
and update project-wide instructions so subsequent phases don't drift back to
v2 behavior.

**Files (code/config).**
- `src/generate/parameters.py` — add `METRO_ZIPS`, neighborhood map, new
  date window (in a feature flag), new merchant configs for ACM/WDX, drop
  EBT from `payment_mix`, add `connectivity_type` distribution, peer-mapping
  helper.

**Files (instruction docs — required, not deferred).**
- `CLAUDE.md` (root) — overhaul: 5 merchants, 10,000-customer panel,
  Charlotte metro, no EBT, lake-as-views, no Network Analyst, new
  date window. This is the document the next Claude session reads.
- `docs/V2_5_RECONCILIATION.md` — this file.

**Validation.**
- Existing `make seed && make test` still pass (no behavioral change).
- New constants importable.
- `grep -E "5,000|EBT|Network Analyst|retail_offprice|dual.path" CLAUDE.md`
  returns no matches.

**Dependencies.** None.

---

### Phase 1 — Geography and panel structure

**Goal.** Pivot to single-metro Charlotte panel; rebuild customers and stores;
keep transactions still working with v2 logic.

**Files (code).**
- New: `src/generate/metro.py` — ZIP5 → neighborhood → metro_region lookup;
  ZIP centroid coordinates with jitter.
- Rewrite: `src/generate/customers.py` — drop PII, drop demographics, add
  `grocer_affinity_type`, `primary_grocer`, `secondary_grocer`. Generate
  `customer_id` directly (move SHA-256 here from `anonymize/hash.py`). Panel
  size 10,000.
- Rewrite: `src/generate/kroger.py`, `taco_bell.py`, `tjmaxx.py` — store
  builders use Charlotte ZIPs + neighborhood + lat/long + new region tiers.
- Add: `src/generate/acme.py`, `src/generate/winn_dixie.py` — new generators
  (initially can clone `kroger.py` and load a stub catalog).
- `src/db/schema.sql` — update `tenant_customers`, `tenant_stores` columns;
  drop demographics, drop `is_organic`/`ebt_eligible` from products.
- `src/db/seed.py` — match new column lists.
- `scripts/generate_report_data.py` — stub to a no-op print
  ("v2.5 report regeneration TBD; will be rewritten in Phase 5c"). Prevents
  `make report` crashing on missing columns or removed `src.anonymize.hash`
  import during the refactor.

**Files (instruction docs — required, not deferred).**
- `src/generate/CLAUDE.md` — rewrite before generator code lands: drop EBT
  rules, drop "PII is intentional" framing, drop 5,000-customer reference,
  add affinity model + primary_grocer concept, add 10k panel, add
  Charlotte-metro framing.

**Files (spec docs).**
- `DATA.md` — update customer schema, store schema, panel size, geography
  sections.
- `ARCHITECTURE.md` — update panel/metro section + agent-roster framing
  (note that Network Analyst is going away in Phase 5d).

**Files (tests).**
- `tests/test_generation.py` — drop EBT-eligible test, update PAN invariant
  to `customer_id` invariant, add Charlotte-metro assertion, add
  affinity-mix-band assertion.

**Validation.**
- `make seed` succeeds.
- `make report` succeeds (it's a stub now, but doesn't crash).
- 10,000 customers produced; all `home_zip5` ∈ Charlotte ZIP set.
- 123 stores total with the per-merchant breakdown (KRG 30, ACM 25, WDX 20,
  TBL 40, TJX 8); each store has a non-null `neighborhood` and `metro_region`.
- The grocer affinity-type mix is within 1pp of (55/30/12/3).
- Customer cross-merchant cohort counts fall in the design's quoted bands.
- `src/generate/CLAUDE.md` describes only v2.5 patterns; no EBT, no PII
  generation, no 5,000.

**Dependencies.** Phase 0.

---

### Phase 2 — Catalog: shared base + per-grocer overlays

**Goal.** Make Kroger / Acme / Winn-Dixie share a base grocery catalog with
per-merchant pricing tiers.

**Files (code/data).**
- New: `data/catalogs/base_grocery_catalog.json` — canonical SKU universe
  (extracted from `data/catalogs/kroger/*.json` plus a few additions).
- New: `data/catalogs/overlays/{kroger,acme,winn_dixie}.json` — which
  canonical SKUs each grocer carries; per-category multipliers per the
  two-tier scheme.
- Rewrite: `src/generate/catalog_kroger.py` → generic
  `src/generate/catalog_grocery.py` taking an overlay path.
- New: `src/generate/catalog_acme.py`, `catalog_winn_dixie.py` (thin shims
  around `catalog_grocery`).
- Drop: `is_organic`, `ebt_eligible` columns from product output.

**Files (spec docs).**
- `DATA.md` — rewrite the catalog architecture section to describe the
  base + overlay model, the two-tier multiplier table, and the new
  per-grocer SKU counts.

**Files (tests).**
- `tests/test_generation.py` — add cross-grocer SKU-name overlap test
  (canonical names must match across grocers); add per-tier price ratio
  test.

**Validation.**
- Kroger ~1,100, Acme ~1,000, Winn-Dixie ~880 SKUs (within ±5%).
- For SKUs present in both Kroger and Acme overlays, Acme's price is within
  ±5% of `kroger × multiplier × (1 ± 2% noise)`.
- Same for Winn-Dixie at the inverse multipliers.
- `DATA.md`'s catalog section matches the implementation; no stale
  `is_organic`/`ebt_eligible` references remain.

**Dependencies.** Phase 1.

---

### Phase 3 — Promotions table + tax model

**Goal.** Replace the `PROMO_DAYS` flat list with a real `tenant_promotions`
table that drives line-item discounts at generation time. Add the per-line
`tax` field.

**Files (code/schema).**
- `src/db/schema.sql` — add `tenant_promotions`; add `tax`, `promo_id` to
  `tenant_transaction_items`; add `subtotal`, `tax_total`,
  `connectivity_type`, `terminal_id` to `tenant_transactions`.
- New: `src/generate/promotions.py` — produces ~73 promos (KRG 25, ACM 20,
  WDX 18, TBL 6, TJX 4) with realistic types and date ranges.
- New: `src/generate/tax.py` — five-tier tax-rate-by-category lookup.
- `src/generate/base.py` — line-item generation looks up active promo by
  (merchant, sku, date) and pulls `discount_pct` from the row; computes
  `tax`; aggregates `subtotal` / `tax_total` / `txn_total`.
- `src/generate/run_all.py` — orchestrate promotions before transactions.
- `src/db/seed.py` — load `tenant_promotions`.

**Files (spec docs).**
- `DATA.md` — add the `tenant_promotions` schema section, the tax-rate
  table, and the new transaction-level fields (`subtotal`, `tax_total`,
  `connectivity_type`, `terminal_id`).

**Files (tests).**
- `tests/test_generation.py` — `tax = round(line_total × rate, 2)` per row;
  `txn_total = subtotal + tax_total` per row; non-zero `discount` always
  has a covering `promo_id`.

**Validation.**
- `tenant_promotions` row counts within ±10% of design targets.
- Every line item with a non-zero `discount` has a non-null `promo_id` and
  a covering row in `tenant_promotions`.
- Tax-exempt grocery categories sum to $0 tax across the panel.
- `DATA.md`'s schema section reflects all new fields.

**Dependencies.** Phases 1 and 2.

---

### Phase 4 — Transaction-field expansion + EBT removal

**Goal.** Drop EBT entirely; add `terminal_id`, `connectivity_type`,
`subtotal`, `tax_total`. (Schema landed in Phase 3 already; this phase is
the generator code change.) Encode the per-category quantity distributions
and per-customer active-weeks variance once Q3/Q4/Q6 are unblocked.

**Files (code).**
- `src/generate/parameters.py` — drop EBT from all `payment_mix`; add
  `CONNECTIVITY_TYPE` distribution (per Q6); add per-category quantity
  distribution table (per Q3); add active-weeks variance parameters
  (per Q4).
- `src/generate/base.py` — drop the EBT-eligible filter logic and the
  category renormalization branch; sample `connectivity_type` per
  transaction; sample `terminal_id` deterministically per store
  (`<store_id>-T<NN>`); apply per-category qty distribution; apply
  per-customer active-weeks variance alongside pay-cycle bumps.
- `src/generate/run_all.py` — anomaly injection moved out (Phase 6).

**Files (spec docs).**
- `DATA.md` — final field-level pass: confirm tenant tables in DATA.md
  match `schema.sql` exactly. Add the qty-distribution and active-weeks
  spec excerpts here so the doc is self-contained.

**Files (tests).**
- `tests/test_generation.py` — drop EBT-only-at-Kroger test; add
  "no transaction has `payment_type='ebt'`" test; assert `terminal_id` per
  txn falls in the store's terminal pool; assert qty distribution per
  category falls in the spec'd band.

**Validation.**
- Zero EBT rows.
- All transactions have non-null `terminal_id` and `connectivity_type`.
- Per-txn `subtotal == sum(line_total)` and `txn_total == subtotal + tax_total`.
- Connectivity mix matches the spec (Q6) within ±2pp.
- Quantity-by-category distribution matches the spec (Q3).

**Dependencies.** Phase 3 + resolution of Q3, Q4, Q6.

---

### Phase 5 — Lake as parameterized views

Split into four sub-phases so the codebase keeps working while the lake
flips from physical to virtual. **Each sub-phase ends with a passing
`make seed && make test`** and an in-place doc update if a touched file
has a `CLAUDE.md` neighbor.

#### Phase 5a — Build view-builders + tests (no agent changes)

**Goal.** Stand up the parameterized view-builder modules and prove they
return correct shapes. Agents still hit the old physical lake.

**Files.**
- New: `src/lake/__init__.py`, `src/lake/peer_mapping.py`,
  `src/lake/views.py` — `get_lake_transactions(viewing_merchant_id, sql_filter=None)`,
  `get_lake_stores(viewing_merchant_id, ...)`, `to_hour_bucket()`,
  `to_total_bin()`, `generate_opaque_id()`.
- New: `tests/test_lake_views.py` — peer mapping is correct from each
  viewing merchant; viewing merchant's own data is excluded; opaque IDs
  are deterministic; bin/bucket vocabularies match the design exactly;
  k=5 suppression triggers in the right cell.

**Validation.**
- `get_lake_transactions("KRG")` returns zero Kroger-underlying rows;
  `peer_a == ACM`, `peer_b == WDX` per the table at line 587 of the
  design doc.
- No `customer_id` in any view-builder output.
- `txn_total_bin` ∈ {`$0-5` … `$250+`}; `txn_hour_bucket` ∈ the 10
  documented labels.
- All existing tests still pass; physical lake still in DB.

**Dependencies.** Phases 1–4.

#### Phase 5b — Wire view-builders into agents

**Goal.** Replace `query_lake` with the view-builder pathway. Update
agent prompts. Physical lake tables still exist as a safety net.

**Files (code).**
- `src/agents/tools.py` — `query_lake` now takes `viewing_merchant_id`
  and runs the SQL produced by `get_lake_*`. Reject queries that
  reference physical `lake_*` tables.
- `src/agents/advisor.py` — pass `current_merchant_id` through to the
  lake tool.
- `src/agents/prompts/advisor.md` — teach the new lake schema + peer
  concept; document that the lake excludes the viewing merchant.
- `src/db/queries.py` — rewrite peer queries to use the view-builder
  output shape (peer_id, txn_total_bin, etc.).

**Files (instruction docs — required, not deferred).**
- `src/agents/CLAUDE.md` — required rewrite **before** the agent code
  changes: drop the Network Analyst entry, drop "lake schema is in
  schema.sql" framing, document the view-builder interface, document
  that `query_lake` requires `viewing_merchant_id`. The next Claude
  session reads this file when editing agents.

**Files (tests).**
- `tests/test_agents.py` — update lake-tool tests for the new signature;
  drop the lake regex-substring case that referenced `lake_transactions`.

**Validation.**
- Advisor mock-mode runs end-to-end against view-builder lake.
- All `db/queries.py` canned queries return the expected shape.
- Tenant isolation regex test still passes unchanged.
- `src/agents/CLAUDE.md` no longer references `analyst.py`,
  `lake_customers`, or "lake_* tables".

**Dependencies.** Phase 5a.

#### Phase 5c — Delete the physical lake and `src/anonymize/`

**Goal.** Reclaim the surface area now that nothing reads from it.

**Files (code/build).**
- Delete: `src/anonymize/` entirely (including `lake.py`, `tenant.py`,
  `pipeline.py`, `generalize.py`, `hash.py`, `dp.py`, `__init__.py`).
  Hash logic already moved to `src/generate/customers.py` in Phase 1.
- Delete: `data/anon/` (clean delete per Q10).
- `src/db/schema.sql` — drop all `lake_*` tables and indexes.
- `src/db/seed.py` — drop the lake-CSV ingest section.
- `Makefile` — drop the `anonymize` invocation from the `seed` target.
- `scripts/demo.sh` — update the help text on line 15
  (`generate raw, anonymize (tenant + lake), load SQLite` → `generate
  raw and load SQLite`).
- `scripts/generate_report_data.py` — full rewrite from the Phase 1
  stub: query view-builders for cross-merchant sections, tenant tables
  for per-merchant sections, drop `_anonymization_demo`, drop
  `MERCHANT_KEYS` hardcode (use `merchants` table), update embedded
  `_schema()` to match v2.5.
- `tests/test_anonymize.py` — delete (covered by `test_lake_views.py`).
- `tests/test_db.py` — drop assertions about `lake_*` tables.

**Files (spec docs).**
- `ARCHITECTURE.md` — rewrite the data-architecture section: replace the
  dual-path tenant/lake-tables framing with the lake-as-views model;
  describe the privacy engine and per-merchant peer mapping.

**Validation.**
- `make clean && make seed && make test && make report` succeeds.
- No `data/anon/` directory regenerated.
- `sqlite3 data/payments.db ".tables"` lists no `lake_*` tables.
- `grep -r "data/anon\|lake_customers\|lake_transactions\|src.anonymize" src/ tests/ scripts/` returns nothing.
- `ARCHITECTURE.md` no longer describes a physical lake layer.

**Dependencies.** Phase 5b.

#### Phase 5d — Dashboard role selector and canned queries

**Goal.** Make the dashboard reflect the v2.5 panel and remove the
Network Analyst role.

**Files.**
- `src/agents/analyst.py` — delete file.
- `src/agents/prompts/analyst.md` — delete file.
- `src/dashboard/app.py` — `ROLES` becomes the five merchants;
  `ROLE_TO_MERCHANT_ID` extends to ACM/WDX; canned questions per role
  updated to reference the new schema (peer_id rather than merchant_id;
  five-merchant context in blurbs).
- `tests/test_agents.py` — drop NetworkAnalyst tests.

**Validation.**
- Dashboard launches (`make demo` or `streamlit run`); each of five
  merchant roles can run at least one canned question end-to-end in
  mock mode.

**Dependencies.** Phase 5c.

---

### Phase 6 — Replace anomalies

**Goal.** Replace the three v2 Kroger-only anomalies with the three v2.5
anomalies.

**Files (code).**
- New module per anomaly inside `src/generate/anomalies/`:
  - `university_city_decline.py` — 4-stage ramp on KRG/ACM/WDX stores in
    University City, late-Apr/May.
  - `plaza_midwood_avocado.py` — 4-day quantity spike at Plaza Midwood
    Kroger, Apr 21–24, peak Apr 22.
  - `acme_pasta_promo.py` — Acme pasta promo Apr 19–25 underperforms;
    Kroger pasta promo Apr 15–21 outperforms (works alongside Phase 3
    promotions).
- Delete: the three `_inject_*` helpers in `run_all.py`.
- `scripts/generate_report_data.py` — add an anomaly-callouts section
  to the report payload referencing the three planted signals.

**Files (spec / narrative docs).**
- `PLAN.md` — update the demo-script section to reference the new
  anomalies; drop avocado-price-spike / store-dropout / baby-cohort
  callouts.
- `DATA.md` — update the anomalies section to describe the new three.

**Files (tests).**
- `tests/test_generation.py` — one assertion per anomaly that the planted
  signal is detectable in the data (e.g., Plaza Midwood Kroger avocado
  qty on Apr 22 ≥ 3× the prior-week median for the same store/SKU).

**Validation.**
- Each anomaly is detectable by the corresponding canned demo query.
- No residual references to the deleted anomalies in code or docs.

**Dependencies.** Phases 1, 2, 3, 5.

---

### Phase 7 — Final consistency pass

**Goal.** Cleanup, not catch-up. Each of the major instruction and spec
docs has already been updated in its owning phase (root `CLAUDE.md` in
Phase 0; `src/generate/CLAUDE.md` in Phase 1; `DATA.md` in 1/2/3/4;
`ARCHITECTURE.md` in 1 and 5c; `src/agents/CLAUDE.md` in 5b;
`PLAN.md`/`DATA.md` anomaly section in Phase 6). This phase is a final
readthrough.

**Files.**
- `README.md` — top-level overview update; small surface, do last.
- `V2_AUDIT.md` — delete or move to `docs/archive/v2_audit.md`
  (depending on whether you want it preserved as historical context).
- All other markdown — readthrough only; fix anything missed in earlier
  phases.

**Validation.**
- `grep -E "5,000 customer|EBT|Network Analyst|is_organic|retail_offprice|customer_pan|lake_customers|lake_transaction_items|src/anonymize|age_band|income_band" docs/ src/ tests/ scripts/ *.md` returns nothing.
- README screenshot/example transactions show v2.5 shape.

**Dependencies.** Phase 6.

---

## 3. Open questions — status

### Resolved (locked before Phase 0)

| ID | Resolution |
|---|---|
| **Q1** | **Generate `customer_id` directly** in `src/generate/customers.py` using the existing SHA-256 hash. PAN never written to disk. `src/anonymize/` collapses entirely (Phase 5c). |
| **Q2** | **`off_price_retail`** (v2.5 spelling) is canonical. Global rename of `retail_offprice` everywhere it appears. |
| **Q5** | Aim for all 7 agents long-term, but **defer agent persona design** for now. Phase 5b keeps the single Merchant Advisor (mapped to persona #7). Specialist agents are a separate workstream after the data refactor lands. |
| **Q7** | Accept the broken-anomaly window during Phases 1–5. Anomalies are explicitly Phase 6 work. |
| **Q8** | **No `tenant_terminals` table.** Terminal identity stays as a string field. |
| **Q9** | Lake formula confirmed: `discount_pct_applied = discount / (unit_price * qty)`. |
| **Q10** | Clean delete of `data/anon/` and `src/anonymize/` on Phase 5c. No empty dirs left behind. |

### §1.3 correction

The reconciliation table previously read `is_lapser → REMOVE (or fold into the new "Lapsed/light" affinity bucket)`. Corrected: **REMOVE entirely.** The lapsed cohort is represented by `grocer_affinity_type = 'lapsed_light'`, not a parallel flag.

### Awaiting design-doc input (blockers for the affected phase)

The locked `V2_5_DATA_DESIGN.md` in this repo has a 5-line stub for Layer 4
that says *"See earlier doc state for full detail; preserved here for
reference."* The three items below were flagged as "in the design doc" but
their content is not present in the file. Resolution paths: paste the
relevant excerpts, point me at the earlier doc state, or treat them as
explicit decisions and add them to `V2_5_DATA_DESIGN.md` so it's
self-contained.

### Q3 — Per-category quantity distributions
**Said to be in Layer 3e.** Layer 3e in the file currently has only
`qty | INTEGER | Number of units, ≥ 1` and a tax-rate-by-category table. No
per-category quantity-distribution table is present.
**Affects:** Phase 4 (transaction generation).

### Q4 — Per-customer active-weeks variance
**Said to be in Layer 4 Step 4b.** Layer 4 in the file is a stub with no
numbered steps. Decision recorded: keep pay-cycle bumps **and** add the
per-customer active-weeks variance from Step 4b — but I need the spec to
implement it.
**Affects:** Phase 4 (transaction generation).

### Q6 — Connectivity-type distribution
**Said to be in Layer 4 Step 4l: "65% wifi, 25% cellular_4g, 8%
cellular_5g, 2% ethernet (uniform across merchants for v2.5)."** This
string is not in `V2_5_DATA_DESIGN.md`. If the numbers are correct, I'll
encode them as the default once you confirm the source.
**Affects:** Phase 4 (transaction generation).
