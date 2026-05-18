# V2.5 Report Rewrite — Plan

Committed planning artifact, mirroring `docs/V2_5_RECONCILIATION.md`. Tracks the phased rebuild of `docs/report.html` to the v2.5 architecture.

---

## Context

The v2.5 codebase shipped (`docs/V2_5_RECONCILIATION.md` complete through Phase 7). `scripts/generate_report_data.py` emits a v2.5-correct JSON payload to `docs/report_data.json` / `docs/report_data.js`. But `docs/report.html` is still the v2 build (3 merchants, EBT-aware language, Network Analyst agent, dual-path tenant/lake-tables framing, anonymization-pipeline demo) — its narrative spine and many of its visualizations no longer match the data the script produces.

The goal: replace the report with a new editorial document that (a) reflects the v2.5 architecture and panel honestly, (b) reads in 3 minutes via headlines and visuals but supports 15 minutes of depth, (c) is a single self-contained HTML file (no external JSON, no CDN fetches), and (d) demonstrates rigor through honest accounting of what's preserved/approximate/lost. Audience: Verifone leadership and the strategy-doc readership.

This planning doc is the contract for the rewrite. It identifies the JSON payload extensions that need to ship before HTML work begins, names the library and palette choices, and phases the build into reviewable chunks.

---

## Library & format choices (proposals)

### Chart library: **Plotly.js basic** (`plotly-basic-2.x.min.js`, ~700 KB minified)

Justification:
- Hover tooltips with exact values are built-in — no per-chart custom code.
- `rangeslider` on time series is one config line (needed for the 90-day volume chart).
- Legend click → series toggle is built-in (covers the "merchant toggle" requirement on multi-series charts).
- "basic" build excludes 3D/WebGL/geo/finance modules that we don't need, cutting bundle ~75% vs full Plotly.
- Inlines cleanly into a single file (drop the minified bundle into a `<script>` tag).

Alternatives considered:
- **Chart.js (~80 KB):** lighter, but range slider needs a plugin, hover tooltips need configuration, and we'd hand-roll a lot. Not worth the dev-time cost.
- **D3 (~70 KB):** maximum flexibility, but every chart becomes 50–100 lines of code. Wrong tool for an editorial doc with 6–8 distinct visualizations.

### Map + system diagrams: **inline SVG** (no library)

Both Plotly and Chart.js need external map tiles (Mapbox token / Leaflet tile server) for a Charlotte store map. Inline SVG with the stores plotted on a longitude/latitude scatter and a simplified Mecklenburg-County polygon as background is lighter, fully offline, and editorially appropriate. Same approach for the dual-path architecture diagram.

### Delivery: **single self-contained HTML file**

Everything inlined: CSS in a `<style>` block, Plotly bundle in a `<script>` block, JSON payload embedded as `window.REPORT_DATA = { ... }` (no fetch). Estimated final size ~1.3–1.5 MB. The current report is ~1.74 MB across three files; consolidation actually reduces total bytes by deduplicating the JSON-loading fallback path.

### Color palette

Single editorial accent + restrained merchant differentiation only where it carries meaning. All colors WCAG-AA contrast against white background.

| Role | Hex | Use |
|---|---|---|
| **Accent** | `#0F4C81` | Headings, primary chart series, links, callouts |
| Surface | `#F7F8FA` | Section bands, hover states |
| Border | `#E2E5EA` | Table dividers, card outlines |
| Text primary | `#1A1F2E` | Body |
| Text secondary | `#4A5161` | Captions, metadata |
| Text muted | `#7B8294` | Footnotes |
| Anomaly highlight | `#C44536` | Anomaly callout strokes only |

Merchant colors (used only when distinguishing merchants is essential — store map, multi-merchant time series):

| Merchant | Hex | Note |
|---|---|---|
| Kroger (KRG) | `#0F4C81` | Reuses the accent (Kroger is the lens for most cross-merchant narratives) |
| Acme (ACM) | `#3A6FA5` | Medium blue |
| Winn-Dixie (WDX) | `#6F8FB8` | Light blue |
| Taco Bell (TBL) | `#C0563F` | Warm contrast |
| TJ Maxx (TJX) | `#5B7B58` | Muted green |

### Typography

System stack only (no webfonts → keeps self-contained guarantee, no FOUT).

```css
font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Inter, Roboto, sans-serif;
font-feature-settings: "ss01", "cv11";  /* if available */
```

Body 16px / 1.65 line-height. Display 28–40px with -0.01em tracking. Monospace blocks use `ui-monospace, 'SF Mono', 'JetBrains Mono', monospace` at 13px.

---

## JSON payload — required additions

Current keys in `report_data.json` (Phase 7): `generated_at, window, promo_days, anomaly_window, stats, merchants, revenue_by_category_kroger, daily_volume, hour_distribution, customer_overlap, pay_cycle, example_transaction, anonymization_demo, cross_merchant_finding, sql_basket_comparison, affinity_pairs, agents_status, schema, anomaly_callouts`.

The new sections require these **new top-level keys** (or extensions to existing keys). All additions go in `scripts/generate_report_data.py` before any HTML work begins.

| New key | Shape | Powers |
|---|---|---|
| `stores[]` | `{store_id, merchant_id, latitude, longitude, neighborhood, metro_region, store_zip5}` × 123 | Charlotte store map (§1.1) |
| `customer_breakdown` | `{behavioral_segment: {filler, stocker}, affinity: {loyalist, splitter, three_chain, lapsed_light}, primary_grocer_share: {KRG, ACM, WDX}, has_mobile_wallet_pct, card_type_share: {credit, debit, mixed}}` | Customer panel viz (§1.2) |
| `customer_zip_distribution[]` | `[{zip5, neighborhood, n_customers}]` | Customer geographic distribution (§1.2) |
| `cohorts` | `{n_at_all_3_grocers, n_at_any_2_grocers, n_at_grocer_and_qsr, n_at_grocer_and_retail, n_at_all_5}` | Cross-merchant cohort sizes (§1.2) |
| `catalog_stats` | `[{merchant_id, n_skus, n_categories, n_subcategories, tight_multiplier, loose_multiplier}]` for 3 grocers; `{shared_canonical_skus, krg_only, acm_only, wdx_only, krg_acm_only, krg_wdx_only, acm_wdx_only}` | Catalog comparison (§1.3) |
| `daily_volume_5merchant[]` | `[{date, KRG, ACM, WDX, TBL, TJX}]` × 90 | Replaces current 3-merchant `daily_volume`; powers the time-slider chart (§1.5) |
| `anatomy_table[]` | `[{category, data_element, source, privacy_treatment}]` — 8 rows (Transaction / Instrument / Mobile Wallet / Basket-SKU / Merchant / Temporal / Cardholder / Device) | The §5.2-style schema table (§1.4) |
| `anomaly_series` | Per-anomaly extended time series: `university_city_decline.weekly_by_grocer[]`, `plaza_midwood_avocado.daily_qty_by_merchant[]` (KRG/ACM/WDX columns, Apr 15–26), `acme_pasta_promo.daily_lines_by_grocer[]` (Apr 14–29) | Three anomaly charts (§1.6) |
| `peer_mapping` | `{KRG: {peer_a, peer_b, peer_c, peer_d}, ACM: {...}, ...}` 5×4 | Per-merchant peer mapping table (§2.2) |
| `lake_schema_annotations` | For each of the 21 `lake_transactions` + 6 `lake_stores` columns: `{name, source_tenant_col, treatment: "carried"/"transformed"/"derived", note}` | Tenant→lake schema comparison (§2.3) |
| `preservation_accounting` | `{preserved[], approximate[], lost[]}` — short prose strings (~6 per bucket) | Honest accounting table (§2.4) |

Keys to **remove or repurpose** from current payload:
- `agents_status` — drop entirely (no agent enumeration in the new report).
- `cross_merchant_finding.avg_spend_30d` — remove the `n_customers_all_three` framing; the cohort fact moves to `cohorts` and is referenced descriptively.
- `anonymization_demo` — repurpose: keep a minimal version that shows tenant→lake field transformation, used as the visual for §2.2.

Keys to **keep as-is**:
- `generated_at`, `window`, `stats`, `merchants`, `revenue_by_category_kroger`, `hour_distribution`, `customer_overlap`, `pay_cycle`, `example_transaction`, `sql_basket_comparison`, `affinity_pairs`, `schema`, `anomaly_callouts`, `anomaly_window`, `promo_days`.

`scripts/generate_report_data.py` already follows a pattern of one builder function per top-level key — the new keys add ~150 LOC total. Estimated 20–30 min of work to land before HTML begins.

---

## Section outline

Total target: ~2,500 words of prose + 12 visualizations + 4 tables. The section-by-section breakdown:

### Section 1 — The setup and data capture

#### 1.1 The environment (~150 words)

Narrative: Why this panel is realistic — single metro modeled on a real city (Charlotte ZIPs treated as fictional), five mainstream chains sized to plausible per-merchant footprints, 90 days covers two paydays + Easter + Memorial Day.

Visuals:
- **Charlotte store map** — inline SVG scatter. X = longitude (~-80.92 → -80.57), Y = latitude (~35.04 → 35.60). Each of 123 stores plotted as a 6px dot, colored by merchant. Simplified Mecklenburg-County polygon as background. Hover tooltip: store_id + neighborhood + merchant. **Data:** `stores[]`.
- **Headline stat row** — 5 stats (merchants / stores / customers / days / line items). Data: `stats` + `merchants[].n_stores`.

#### 1.2 The customers (~250 words)

Narrative: Why the customer model is realistic — affinity splits align with grocery-industry research, geographic clustering reflects real proximity-driven shopping, cross-merchant cohorts emerge naturally from the affinity model.

Visuals:
- **Affinity Sankey** (Plotly `sankey`) — left nodes: affinity type (4); right nodes: primary grocer (3). Flow widths = customer counts. **Data:** `customer_breakdown`.
- **Cross-merchant cohort bars** — horizontal bar chart, 5 cohorts (active at all 3 grocers / any 2 grocers / grocer+QSR / grocer+retail / all 5). **Data:** `cohorts`.
- **ZIP-level customer distribution** — small horizontal bar by neighborhood. **Data:** `customer_zip_distribution[]`.

#### 1.3 The grocery merchants and their catalogs (~200 words)

Narrative: Base + overlay architecture, tight/loose tier multipliers, why per-grocer differentiation is interesting (peer pricing comparisons become meaningful).

Visuals:
- **Catalog overlap table** — 4 rows: shared canonical SKUs (carried by all 3 grocers), KRG-only, ACM-only, WDX-only. Two columns: count + share-of-total. (Per user adjustment: a 4-row table is instantly readable; 3-set Venn-style visuals confuse at small sizes.) **Data:** `catalog_stats`.
- **Per-tier pricing differentiation** — 2×3 small-multiples bar chart: KRG/ACM/WDX × tight/loose tiers, showing the ±3% / ±7% multiplier. **Data:** `catalog_stats`.
- Pricing positioning callout box (text). **Data:** narrative.

#### 1.4 The anatomy of a transaction (~300 words — the centerpiece)

Narrative: A transaction is a layered record (header → line items → rollups). The §5.2 framework describes 8 categories of data captured at the moment of sale; this section walks through each and is the most important table in the report.

Visuals:
- **The §5.2 schema table** — 4 columns (Category / Data Element / Source / Privacy Treatment), 8 category rows: Transaction, Instrument, Mobile Wallet, Basket/SKU, Merchant, Temporal, Cardholder, Device. **Data:** `anatomy_table[]`. Styled as the report's centerpiece — generous padding, accent-colored category column, alternating row background. Source column uses strategy doc §5.2 vocabulary verbatim: `Payment Kernel`, `NFC subsystem`, `POS API Bridge`, `Device Agent config`, `System clock`, `Security Layer`, `Device Agent`.
- **Receipt-style transaction breakdown** (per user adjustment) — inline HTML/SVG laid out as a printed receipt using actual `example_transaction` values:
  - **Header block** at top — merchant / store / terminal / customer_id / timestamp / payment_type + card_network + entry_mode + wallet_type. Annotated: "→ `tenant_transactions` row".
  - **Line-item table** in the middle — columns SKU / qty / unit_price / discount / line_total / tax. Annotated: "→ `tenant_transaction_items` rows (one per SKU)".
  - **Rollup math** at the bottom — `subtotal + tax_total = txn_total` with the actual numbers. Annotated: "→ rollup invariant asserted in tests".
- **Explicit callout box**: `terminal_id` is captured but not surfaced in cross-merchant insights — deliberate scope choice (v2.5 design doc §1 "Scope note: terminals as analytical entities").

#### 1.5 Transaction volume over 90 days (~200 words)

Narrative: How the volume was generated — customer-centric loop, per-segment trip frequencies (grocery/QSR/retail), basket archetypes (stockup/fill-in/themed), week-level variance, day-of-week patterns, payday bumps. Why these are realistic.

Visuals:
- **Daily volume time series** — Plotly line chart, 5 traces (KRG/ACM/WDX/TBL/TJX), 90-day x-axis, `rangeslider` enabled. Default visibility: KRG / ACM / WDX visible; TBL / TJX hidden (`visible: 'legendonly'`) so the initial view isn't noisy — readers can click them on. Hover tooltip shows date + per-merchant counts. Vertical annotation lines: weekends (light shading), pay-cycle days (1st-3rd, 15th-17th), Easter (Apr 5), Memorial Day (May 25), pasta-promo windows. **Data:** `daily_volume_5merchant[]`.

#### 1.6 Planted anomalies (~250 words)

Narrative: Three deliberate signals planted in the data; brief mention of insight types they enable (peer comparison, uniqueness confirmation, competitive dynamics). **No AI agent names.** Each anomaly gets one paragraph + one chart.

Visuals (3 charts in a 3-column grid for desktop, stacked for mobile):
- **University City decline** — Plotly grouped bar, x = 4 stage-windows + pre-anomaly baseline, y = avg txns/day, 3 colored bars per group (KRG/ACM/WDX). Annotated with effective multipliers. **Data:** `anomaly_series.university_city_decline.weekly_by_grocer[]`.
- **Plaza Midwood Kroger avocado spike** — Plotly line + scatter, x = Apr 15–26, 3 series (KRG Plaza Midwood / ACM Plaza Midwood / WDX Plaza Midwood). Apr 22 peak annotated. **Data:** `anomaly_series.plaza_midwood_avocado.daily_qty_by_merchant[]`.
- **Coordinated pasta promos** — Plotly line, x = Apr 14–29, 3 series (KRG/ACM/WDX pasta lines/day). Promo windows shaded per merchant. Annotation: "Kroger 2.09× / Acme 0.82× / Winn-Dixie 1.26× in-window vs baseline". **Data:** `anomaly_series.acme_pasta_promo.daily_lines_by_grocer[]`.

### Section 2 — Anonymization and the privacy engine

#### 2.1 The dual-path architecture (~200 words)

Narrative: Tenant tables hold each merchant's data at full granularity. The "lake" is virtual — there are no physical lake tables; the lake is a parameterized view computed at query time. When a merchant queries the lake, its own data is excluded and the other four merchants appear pseudonymized.

Visual:
- **System architecture diagram** — large inline SVG schematic. Three rows: tenant tables (7 boxes, full-fidelity) → privacy engine (the view-builder function box, with annotations for the per-query inputs: viewing_merchant_id) → lake views (2 boxes: lake_transactions, lake_stores, with peer_id annotations). Arrows labeled with the transformations. Bottom strip: shows the 5×4 peer mapping permutations (KRG sees ACM as peer_a, etc.).
- **Pre-implementation step (per user adjustment).** Before drawing the SVG: produce a textual ASCII sketch of the diagram's structure (boxes, arrows, labels, groupings) and get user sign-off on the concept. Only then render the SVG. This step lives inside Phase F.

#### 2.2 The privacy engine mechanisms (~300 words)

Narrative: Four mechanisms — P2PE tokenization, generalization, k-anonymity (k=5), suppression of consumer linkage. For each: one paragraph describing what it does + a visual showing the transformation.

Visuals:
- **4-up mechanism grid** — 4 cards in a 2×2 layout. Each card has a heading, a 2–3 line description, and a small "before/after" SVG showing the transformation (e.g., ZIP5 `28205` → ZIP3 `282`; timestamp `2026-04-22 14:37:12` → `2026-04-22 + afternoon`; `$72.93` → `$50-75 bin`; `customer_id e653e5aa...` → dropped). **Data:** static + `anonymization_demo` (repurposed).
- **Per-merchant peer mapping table** (5×4) — clean editorial table. Each row = one viewing merchant. Each cell = which real merchant that peer label points to from this viewer's perspective. **Data:** `peer_mapping`.

#### 2.3 The lake schema (~250 words)

Narrative: Two logical tables; lake_transactions is wide and denormalized (21 columns, one row per peer line item); lake_stores is the 6-column store reference. Show that the lake adds columns (peer_id, peer_segment, lake_txn_id) while dropping others (customer_id, merchant_id-as-real-value, full timestamps, raw amounts).

Visuals:
- **Side-by-side schema comparison** — two-column table. Left: tenant_transactions (14 cols). Right: lake_transactions (21 cols). Lines drawn between equivalent columns with treatment labels (carried / transformed / dropped / derived). Inline SVG over the table. **Data:** `lake_schema_annotations`.
- **Smaller lake_stores schema card** below.

#### 2.4 Honest accounting (~250 words — critical for rigor)

Narrative: This is the part of the document that earns credibility. Three explicit buckets — what's fully preserved, what's approximate, what's truly lost.

Visual:
- **Three-column "preserved / approximate / lost" table** — each column ~6 bullet entries. Examples:
  - Preserved: peer pricing per canonical product, peer category mix, peer payment mix, peer hour-of-day distribution.
  - Approximate: peer average ticket (via bin midpoints), peer total transaction counts, peer trip-day distribution.
  - Lost: per-peer customer cohorts (no customer_id in lake), sub-hour timing, exact peer revenue, peer promo configuration metadata, individual peer store identities (only neighborhood + zip3 surface).
- **Data:** `preservation_accounting`. Tone: matter-of-fact, no hedging.

#### 2.5 What this enables (~200 words)

Narrative: Four classes of analytical capability the architecture supports. **No AI agent names.** Framing: "the analytical capabilities the architecture supports include..." Examples:
- Cross-merchant pricing comparison via canonical product matching
- Peer benchmarking via indexed/relative performance metrics
- Anomaly detection contextualized with peer baselines (e.g., is a sales spike market-wide or merchant-specific?)
- Trade area density analysis at the neighborhood/ZIP3 level

Visual:
- **4-up capability cards** — same card grid pattern as §2.2 mechanisms. Each card leads with a **plain-language example question** (per user adjustment — executive audience), followed by a 1–2 sentence description of the capability:
  - Cross-merchant pricing → *"How does my dairy pricing compare to peer grocers?"*
  - Peer benchmarking → *"Is my coffee category outperforming peers?"*
  - Anomaly detection → *"Is my University City decline market-wide or unique to me?"*
  - Trade area → *"Where are the underserved neighborhoods in the metro?"*
- SQL snippets are not the lead. They live in a per-card hover-reveal or a collapsible "show the query" expander beneath each card (reusing `sql_basket_comparison` + 3 new snippets). **Data:** static prose + `sql_basket_comparison` + 3 new snippets.

---

## Phased build plan

Mirrors the V2_5_RECONCILIATION.md cadence: each phase ends in a reviewable artifact, dependencies named, no phase blocks on the next.

### Phase A — JSON payload extensions

**Goal.** Ship all new keys into `scripts/generate_report_data.py` before any HTML work begins.

**Files.**
- `scripts/generate_report_data.py` — add: `_stores()`, `_customer_breakdown()`, `_customer_zip_distribution()`, `_cohorts()`, `_catalog_stats()`, `_daily_volume_5merchant()` (replaces 3-merchant), `_anatomy_table()`, `_anomaly_series()`, `_peer_mapping()`, `_lake_schema_annotations()`, `_preservation_accounting()`.
- Remove from payload: `agents_status` (drop), 3-merchant `daily_volume` (deprecate after readers move to `daily_volume_5merchant`).

**Reused utilities.** Existing module already has `_q`, `_scalar`, `_connect` — all new builders use them. Peer mapping comes from `src.generate.parameters.PEER_MAPPING`.

**Validation.** `make report` succeeds; `python3 -c "import json; d=json.load(open('docs/report_data.json')); assert {'stores','cohorts','peer_mapping','anomaly_series'} <= d.keys()"`.

**Dependencies.** None.

### Phase B — HTML scaffolding + Section 1.1 (Environment)

**Goal.** Single-file HTML skeleton with inline CSS, inline Plotly bundle, inline JSON payload (replacing the current fetch + fallback dual path). One working visualization (the Charlotte store map) end-to-end so the rendering pipeline is shaken out.

**Files.**
- New `docs/report.html` (write fresh; old file removed in Phase H).
- Inline `<script>` Plotly basic build (downloaded once to `/tmp`, pasted in).

**Reused patterns from old report.** `$()` selectors, `escapeHtml()`, scroll-spy with IntersectionObserver, `Chart.defaults`-style central typography config (adapt to Plotly's `layout.font`).

**Validation.** Open `docs/report.html` via `file://` in two browsers (Chrome + Safari). Map renders. Hover tooltip on a store dot shows store_id + neighborhood. No console errors.

**Dependencies.** Phase A.

### Phase C — Section 1.2 (Customers) + Section 1.3 (Catalogs)

**Goal.** Customer panel breakdown viz (Sankey + cohort bars + ZIP bars) and grocer catalog comparison.

**Files.** `docs/report.html` (extend).

**Validation.** All visuals render with correct numbers from JSON; affinity Sankey totals to 10,000 customers; cohort bar values match `_cohorts()` output.

**Dependencies.** Phase B.

### Phase D — Section 1.4 (Anatomy of a transaction) + Section 1.5 (Volume time series)

**Goal.** §5.2-style schema table (4 columns), layered transaction SVG, daily volume time series with range slider + merchant toggle.

**Files.** `docs/report.html` (extend).

**Validation.** Schema table renders 8 category rows; layered transaction SVG shows the actual `example_transaction` from JSON; time series defaults to full 90-day view with rangeslider; clicking legend items toggles merchant traces.

**Dependencies.** Phase B.

### Phase E — Section 1.6 (Anomalies — 3 charts)

**Goal.** Three anomaly visualizations with annotations matching the design-doc stage/date specs.

**Files.** `docs/report.html` (extend).

**Validation.** University City chart shows the 4-stage descent with KRG bars deepest at stage 3; Plaza Midwood chart shows the Apr 22 peak at KRG only; pasta-promo chart shows the diverging in-window ratios.

**Dependencies.** Phase A (specifically `anomaly_series`).

### Phase F — Section 2.1 (Architecture) + Section 2.2 (Privacy mechanisms)

**Goal.** Inline-SVG system diagram + 4-up mechanism cards + peer mapping table.

**Files.** `docs/report.html` (extend).

**Step F.0 — diagram concept review.** Before any SVG: produce a textual ASCII sketch of the §2.1 system diagram and get user approval on the concept (boxes / arrows / groupings / labels). Only after sign-off does the rendered SVG land. Per the user's explicit instruction.

**Validation.** ASCII sketch approved; system diagram renders cleanly; peer mapping table shows correct 5×4 mappings; 4 mechanism cards render with before/after transformations.

**Dependencies.** Phase B.

### Phase G — Section 2.3 (Lake schema) + Section 2.4 (Honest accounting) + Section 2.5 (What this enables)

**Goal.** Tenant→lake schema comparison; preserved/approximate/lost tables; four capability cards (no agent names).

**Files.** `docs/report.html` (extend).

**Validation.** Side-by-side schema comparison shows correct column-level annotations; preservation accounting table reads honestly (no hedging); SQL snippets in capability cards are syntactically valid against the v2.5 lake.

**Dependencies.** Phase A (for `lake_schema_annotations`, `preservation_accounting`).

### Phase H — Polish, inlining, decommission

**Goal.** Final QA. Inline JSON into the HTML (drop the fetch path entirely). Verify size budget. Remove `docs/report_data.js` and the JSON-fetch fallback. Delete the old `docs/report.html` (replace, don't keep both). Open in 3+ browsers via `file://`.

**Files.**
- `docs/report.html` (final).
- Delete `docs/report_data.js`.
- Keep `docs/report_data.json` as a build artifact (still emitted by `generate_report_data.py` for git-diffable changes; not consumed by the HTML).
- Optionally: update `scripts/generate_report_data.py` to also inline the payload into `docs/report.html` between `<!-- BEGIN_PAYLOAD -->` markers, so `make report` regenerates the report automatically.

**Validation.** `make clean && make seed && make report && open docs/report.html` works. Final HTML size <1.6 MB.

**Dependencies.** All prior phases.

---

## Reused infrastructure

| What | Where | Reuse note |
|---|---|---|
| Builder-function pattern in `generate_report_data.py` | `scripts/generate_report_data.py` | Each new payload key adds one builder function; orchestration in `main()`. |
| Peer mapping | `src.generate.parameters.PEER_MAPPING` | Single source of truth for the 5×4 peer table. |
| Lake view-builder column list | `src.lake.views._LAKE_TXN_SQL_TEMPLATE` (21 cols) and `_LAKE_STORES_SQL_TEMPLATE` (6 cols) | Authoritative column list for `lake_schema_annotations`. |
| Anomaly windows + multipliers | `src/generate/anomalies/{university_city_decline,plaza_midwood_avocado,acme_pasta_promo}.py` | Used to render the §1.6 charts' annotation overlays so they match the planted spec exactly. |
| Store metadata | `tenant_stores` table (123 rows with lat/long/neighborhood/metro_region) | Source for the Charlotte store map. |

---

## Open questions — resolutions (locked)

All six questions from the initial plan resolved in the user's review:

| # | Question | Resolution |
|---|---|---|
| Q1 | Plotly bundle size | Approved (~1.5 MB self-contained HTML is fine). |
| Q2 | Charlotte map fidelity | (a) — single Mecklenburg-County outline; skip neighborhood polylines. |
| Q3 | No AI agent names | Confirmed. The seven-agent list from `V2_5_DATA_DESIGN.md` §1 must not appear anywhere. Use "the analytical capabilities the architecture supports" framing. |
| Q4 | §1.4 Source vocabulary | Strategy doc §5.2 verbatim: `Payment Kernel`, `NFC subsystem`, `POS API Bridge`, `Device Agent config`, `System clock`, `Security Layer`, `Device Agent`. No invented terms. |
| Q5 | 5-merchant volume chart | All 5 traces with legend-toggle; default TBL and TJX to hidden (`visible: 'legendonly'`) to keep the initial view legible. |
| Q6 | Plan-doc disposition | (a) — commit to `docs/V2_5_REPORT_PLAN.md` as a planning artifact mirroring `docs/V2_5_RECONCILIATION.md`. |

Three plan adjustments applied above:
- §1.3 catalog viz → simple 4-row table instead of 3-set Venn-style bar.
- §1.4 layered transaction → receipt-style breakdown with database-table annotations, using the actual `example_transaction` values.
- §2.5 capability cards → lead with plain-language example questions; SQL behind hover-reveal / expander.
- §2.1 architecture diagram → produce an ASCII sketch for user approval before rendering the SVG (Step F.0).

---

## Verification

End-to-end test once the rewrite ships:

```bash
make clean && make seed && make report
# Verify size and selfcontained-ness:
wc -c docs/report.html                                    # expect <1.6 MB
grep -c 'fetch\|src=' docs/report.html                    # 0 external refs in body
grep -c '<script src=' docs/report.html                   # 0 external script tags

# Open and visually verify:
open docs/report.html                                     # macOS
# (or `python3 -m http.server 8000 -d docs` then http://localhost:8000/report.html)

# Verify each section renders and is interactive:
# - Charlotte map: hover a store dot → tooltip with merchant + neighborhood
# - 90-day volume chart: drag the range slider → x-axis updates; click legend → trace toggles
# - Anomaly charts: hover bars/dots → exact-value tooltips
# - Peer mapping table: each row valid against src/generate/parameters.PEER_MAPPING
# - Preservation table: spot-check 1–2 entries against V2_5_DATA_DESIGN.md disclosure rules
```

Test in Chrome + Safari + Firefox via `file://` (the self-contained guarantee).
