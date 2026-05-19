# V3 Audit

Audit run 2026-05-17. Findings against `V3_VISION.md`.
0
The thesis tested below: a payments company sitting on baskets +
payments + customers across multiple merchants can deliver each
merchant a dashboard + AI partner producing cross-merchant insights
no merchant could build alone — dashboard standalone-readable, agent
calibrated to evidence strength.

---

## Section 1: Architecture

### 1.1 Lake-as-views under interactive dashboard load

**Finding.** `src/lake/views.py` is the only path to the lake. Every
query routes through `_LAKE_TXN_SQL_TEMPLATE`, which joins
`tenant_transactions` → `tenant_transaction_items` → `tenant_products`
→ `merchants`, filters `merchant_id != :viewing`, and per-row computes
three Python-resident SQLite UDFs: `_opaque_id` (salted SHA-256,
truncated, called twice per row — `lake_txn_id` and `lake_store_id`),
`_to_hour_bucket`, `_to_total_bin`. At ~230k transactions and ~6
items/basket, an unfiltered viewer sees on the order of **1.1M peer
line-item rows**, each running three Python UDF calls. Indexes exist
on `merchant_id`, `txn_ts`, `items.txn_id`, `items.sku` (good — the
joins are fine), but the per-row Python hop is the bottleneck. The
agent's `query_lake` re-wraps the same template as CTEs, so the
agent path inherits the cost.

**Why it matters for v3.** The vision's gold-standard beat is a
click→drilldown→agent flow with chart updates between each step. A
2-3s spinner per dashboard panel kills "lean forward." Dashboard panels
that pre-filter to a category or date window will land at 50k-200k
rows and stay tolerable (~300-800ms in SQLite at this UDF cost); a
panel-of-all-categories chart will not. The agent's interactive
follow-ups inherit the same latency.

**Recommendation.** Pre-materialize a per-viewer cache at seed time:
five SQL views `lake_transactions_KRG..TJX` (and `lake_stores_*`)
populated as physical tables in `src/db/seed.py` directly after the
tenant load. Indexed on `(category, txn_date)` and `(peer_id, txn_date)`.
Disk cost ≈ 5 × 30MB. The agent's `query_lake` keeps the CTE wrapper
contract but the CTE bodies become `SELECT * FROM lake_transactions_<viewer>`
— same agent semantics, no Python UDFs at query time. Dashboard panels
read the materialized tables directly. Tradeoff: seed time grows from
~80s to ~120s; lake views become two-stage (build at seed, read at
query) rather than purely virtual.

**Decision needed from me?** Decided: materialize at seed time.
Constraint: agent-facing SQL contracts (`lake_transactions`,
`lake_stores`) must not change — the materialization is transparent
to the agent. The runner's CTE wrapper rewrites bodies to
`SELECT * FROM lake_transactions_<viewer>`; the agent never sees
the per-viewer table names.

**Measured speedup (Phase 1.5, 2026-05-17).** Two representative
queries, best-of-3 timings against the local DB:

| Query | Pre-1.5 (UDF template) | Post-1.5 (materialized) | Speedup |
|---|---:|---:|---:|
| Dairy aggregate (anchor-chart shape: `canonical_name × peer_id`, `WHERE category='DAIRY'`) | 1,726 ms | 298 ms | 5.8× |
| Peer × txn_date over the full 90-day window | 2,807 ms | 126 ms | 22.3× |

Audit's pre-1.5 estimates (300–800 ms filtered, 2–3 s broad) held.
Post-1.5 numbers put even the broad-panel query under 150 ms —
inside the lean-forward budget for an interactive panel.

Seed-time cost of the materialization: +91 s (5 viewers × ~18 s
each to run the UDF template + build the indexes). Local `make seed`
end-to-end went from ~30 s to 120 s — paid once per build, not per
query.

---

### 1.2 k=5 suppression on the two anchor charts

**Finding.** `apply_k_anonymity` (views.py:150) is a utility function;
**nothing in the lake pipeline auto-applies it**. The lake views
return raw rows; aggregation is the caller's job, suppression too.
Walking the two anchor charts:

- **Dairy price comparison (Anchor 1).** Aggregate query is roughly
  `SELECT canonical_name, peer_id, AVG(unit_price), COUNT(*) FROM
  lake_transactions WHERE category='DAIRY' GROUP BY ...`. Cells per
  (SKU × peer) over 90 days at a grocery viewer: a 25-store peer
  selling whole milk ~3×/store/day × 90 days = ~6,750 lines. k=5
  doesn't bite at SKU×peer. *Drilldown* by day (per the vision
  spec) drops cells to ~75/day — still comfortably k≥5. Suppression
  is invisible.
- **University City decline (Anchor 2).** Own-merchant chart is
  tenant, no suppression. The peer cross-check (`is it market-wide?`)
  query is `peer_id × neighborhood='University City' × week`. Per-peer
  UC store count is 1-3; weekly cells ≈ 200-600 rows. k≥5 by a wide
  margin. The grocer with the smallest UC footprint is still fine.

Suppression doesn't bite either anchor chart at their natural rollup
granularity. It would bite a tight drilldown (single SKU × single
day × single peer ≈ 1-5 rows), but the vision spec doesn't include
that level of drilldown.

**Why it matters for v3.** The privacy story holds for the demo path.
But: the gap between "k=5 is the design" and "the lake views don't
enforce it" will be visible to anyone reading `views.py` in the
deep-dive. A viewer asking *"so a query returning 2 rows in a peer
cell — what happens?"* gets the answer *"the agent receives 2 rows."*
That's not a v3 problem unless we claim k=5 is enforced.

**Recommendation.** Two options. (a) Wire `apply_k_anonymity` into
the agent runner — after every `query_lake` result, if a `count` /
`n` column is present, drop sub-k rows and surface a note. Cost: one
function call in `tools.py::query_lake`. (b) Leave as-is and reframe
the deep-dive narrative: "k=5 is the threshold we'd enforce at
publish time; at the query level the lake exposes raw rows." Honest
but weaker.

**Decision needed from me?** Decided: wire `apply_k_anonymity` into
the agent's `query_lake` tool. When a lake result has a count
column, drop sub-k rows and surface a "X rows suppressed for
privacy" note in the tool result. Also: update the lake-query
system prompt instructions for every agent — when querying the
lake for breakdowns by customer-dimension attributes (ZIP3,
behavioral segment), the agent must include `COUNT(*)` so
suppression can apply. The runner can't auto-suppress without a
count column.

---

### 1.3 Peer mapping is stable; segment metadata makes peers identifiable

**Finding.** `src/generate/parameters.py:353` defines `PEER_MAPPING`:
per-viewer, deterministic, same-segment-first-then-alphabetical. KRG
viewing → `peer_a = ACM`, `peer_b = WDX`, `peer_c = TBL`, `peer_d = TJX`,
*always.* `lake_transactions.peer_segment` is preserved verbatim, so:
- For any grocer-viewer, `peer_c` has `peer_segment='qsr'` — the
  panel contains exactly one QSR (TBL). Identity is recoverable in
  one fact lookup.
- `peer_d` for grocers is `off_price_retail` — only TJX. Same.
- `peer_a` / `peer_b` are both grocery — alphabetical rule maps
  them to ACM and WDX given documented panel membership.

In a 5-merchant panel with one merchant per non-grocery segment, every
peer label is reverse-engineerable from segment + the public mapping
rule, without any session state or query history.

**Why it matters for v3.** Stakeholder pitch: never surfaces. Deep
dive: a sharp viewer will ask. The current design pseudonymizes
labels but doesn't claim privacy guarantees — the prompts forbid the
agent from naming peers, the dashboard surfaces only `peer_a`..`peer_d`,
and that's the contract. Stable-not-randomized doesn't add risk for
v3 interactivity (the mapping is already public-rule-derivable;
follow-ups don't reveal anything that query 1 didn't).

**Recommendation.** Keep the mapping. Add one paragraph to the
deep-dive script: "label rotation across sessions is a v4 mechanism;
in a 5-merchant demo panel segment metadata makes labels trivially
reverse-engineerable, which is why we treat pseudonymization as a
*labeling* affordance, not a privacy guarantee. Real-world deployments
use larger panels and randomized labels."

**Decision needed from me?** No.

---

### 1.4 "No consumer linkage" — chart constraints for v3

**Finding.** `customer_id` is dropped from the lake (lake views never
SELECT it). Constrains what cross-merchant charts can compute:

- **Possible:** aggregate share-of-trips ("dairy attach rate"),
  category-level peer comparison ("your dairy revenue index vs peer
  baseline"), peer-side price levels, store catchment density. The
  v3 vision's dairy-attach-rate claim is computable as
  `trips_with_dairy / total_trips` per peer — no `customer_id`
  needed.
- **Not possible:** per-customer cross-merchant ratios, customer-cohort
  behavior at peers, share-of-wallet quartiles, "of customers who
  shop with me, what % also shop at peers" — anything that needs
  a customer to appear in both tenant and lake aggregations.

**Why it matters for v3.** The vision's anchor charts and agent
responses don't require customer linkage. The agent's "substituting
to peers" framing is presented as a hypothesis to investigate (not
data-derived), exactly because the data can't make the claim.
That's correct v3 posture per the calibration doctrine.

**Recommendation.** Keep the rule. Add a one-line note to the
deep-dive: "cross-merchant analysis is aggregate-only by design;
per-customer joins across merchants are a v4 privacy decision."

**Decision needed from me?** No — but flag any *new* v3 chart
ideas in chat that need customer linkage; they'd require a v4
privacy revisit.

---

### 1.5 Tenant isolation via predicate regex — brittleness

**Finding.** `src/agents/tools.py::has_merchant_predicate` is a
literal-substring regex: it checks that the SQL contains
`merchant_id = 'KRG'` (or double-quoted) *somewhere*. Bypass
patterns that satisfy the check but neutralize isolation:

- `WHERE merchant_id = 'KRG' OR 1=1` — predicate present, predicate
  disabled.
- `WHERE merchant_id = 'KRG' OR merchant_id = 'ACM'` — returns
  both. Substring-found, no logical isolation.
- `WHERE merchant_id = 'KRG' AND (subquery returning rows from
  another merchant)` — only blocked because tenant queries can't
  reference `tenant_*` cross-merchant... but the tool doesn't block
  that.

Risk profile for v3:
- **Stakeholder pitch:** zero (LLM is cooperative, prompts forbid
  the patterns).
- **Deep dive:** a viewer reading `tools.py` and asking "how is
  tenant isolation actually enforced?" sees four lines of regex.
  That lands as fragile even if no exploit happens in the demo.

**Why it matters for v3.** The vision asks the product to survive a
10-minute deep dive without revealing a seam. Regex-only isolation
is the most likely seam a curious technical viewer points at.

**Recommendation.** Add per-viewer SQLite views at seed time:
`CREATE VIEW tenant_view_KRG AS SELECT * FROM tenant_transactions
WHERE merchant_id = 'KRG'`, one per merchant, one per tenant table.
The agent's `query_tenant` rewrites the agent's SQL to reference
the view (or just routes the SQL against a connection scoped to
the view-name namespace). Views can't be bypassed by clever
WHERE clauses. Zero new dependencies. Keeps the regex check as a
belt-and-suspenders second layer.

**Decision needed from me?** Decided: add per-viewer SQLite views
(`tenant_view_<merchant>_<table>`) at seed time. The agent's
`query_tenant` rewrites SQL to reference the appropriate view based
on viewing merchant. Keep the existing regex predicate check as a
second layer of defense.

---

### 1.6 Agent architecture — what's real, what's scaffolding

`src/agents/` has six modules + four prompts + a base class. Mapped
against V3_VISION's named agents (advisor / anomaly / demand / pricing
/ trade):

| Module | Status | V3 disposition |
|---|---|---|
| `orchestrator.py` | **Live.** Routes free-form questions via a Haiku router + keyword fallback. Dispatches to a specialist. This is the v3 "Conversational Business Advisor" / "advisor" in the vision's naming. | **Ship.** This is what the chat panel calls for free-form input. |
| `pricing.py` | **Live.** Specialist subclass + prompt. Wired into the dashboard. | **Ship.** Anchor-chart agent. |
| `anomaly.py` | **Live.** Specialist subclass + prompt. Three planted anomalies enumerated in the prompt. | **Ship.** Anchor-chart agent. |
| `demand.py` | **Live.** Specialist subclass + prompt. Slow-mover + campaign attribution. | **Ship.** |
| `trade.py` | **Live.** Specialist subclass + prompt. Catchment + siting. | **Ship.** |
| `specialist.py` | Base class infrastructure. Shared tool loop, caveats parser, response shape. | **Ship as-is.** |
| `advisor.py` | **Legacy v2 implementation.** `MerchantAdvisor` class with its own tool loop, custom mock mode, not used by the dashboard or orchestrator. Only callers are `tests/test_agents.py`. | **Archive.** Either move to `docs/archive/` or delete and remove its tests. v3's "advisor" is now the orchestrator. |
| `prompts/advisor.md` | Prompt for the legacy class. Unused in product. | **Archive with `advisor.py`.** |

**Recommendation.** Archive `advisor.py` + `prompts/advisor.md` +
the test file's `MerchantAdvisor` tests in a single PR. Update root
`CLAUDE.md` and `src/agents/CLAUDE.md` to match (see §2.1). v3
shipping inventory: orchestrator + 4 specialists + base class. Five
visible agents to the user (Conversational Advisor + Pricing +
Anomaly + Demand + Trade), exactly the vision's count.

**Decision needed from me?** Decided: archive `src/agents/advisor.py`,
`src/agents/prompts/advisor.md`, and the `MerchantAdvisor` test code
to `docs/archive/legacy_agent/`. Update root `CLAUDE.md` and
`src/agents/CLAUDE.md` to describe the real agent inventory
(orchestrator + 4 specialists). The orchestrator may retain
user-facing naming like "Conversational Business Advisor" or
"Merchant Advisor" — that naming is in the orchestrator, not the
legacy class.

---

## Section 2: Drift between docs and code

### 2.1 Root `CLAUDE.md` claims a single agent

- **Doc:** `CLAUDE.md:80` *"Single agent today: advisor.py
  (Merchant Advisor — uses tenant + lake views, scoped to a
  current_merchant_id)."* Reinforced at line 83 (*"specialists
  beyond the Merchant Advisor are deferred"*) and line 117 (*"Specialist
  agent personas beyond the single Merchant Advisor."*).
- **Code:** five live agents (`orchestrator.py` + 4 specialists),
  plus the legacy `advisor.py` only referenced by tests. The chat
  panel routes through specialists, not `advisor.py`.
- **`src/agents/CLAUDE.md:4`** has the same drift: *"specialists
  beyond the Conversational Business Advisor pattern are deferred."*
- **Recommendation.** **Update the docs.** Rewrite the agents section
  of root `CLAUDE.md` and the `src/agents/CLAUDE.md` opener to name
  the live inventory (orchestrator + 4 specialists) and mention that
  `advisor.py` is legacy / archived. Pair with §1.6's archive of
  `advisor.py`.

### 2.2 `Makefile` `report` target — script exists; the user's prior was wrong

- **Doc:** `Makefile:6` references `scripts/generate_report_data.py`.
- **Code:** `scripts/generate_report_data.py` **exists** (60 KB,
  alongside `build_report_html.py`). The user's framing in the
  audit prompt — "doesn't exist in the tree" — is incorrect.
- **Recommendation.** **No action.** The Makefile target works. If
  the report-build pipeline isn't part of v3 (the `report.html`
  artifact in `docs/` is a static build), consider whether `make
  report` is still in the v3 workflow or should be archived
  alongside the report-pipeline scripts. Separate question.

### 2.3 `PLAN.md` is the v2 build plan with a banner

- **Doc:** `PLAN.md` (47 KB) describes the v2 architecture in detail
  — 3 merchants, EBT-at-Kroger, separate `src/anonymize/` stage,
  Network Analyst agent. Top banner (lines 5-13) tells readers the
  v2.5 docs are authoritative.
- **Code:** v2.5 reality — 5 merchants, virtual lake, no anonymize
  stage, no Network Analyst, no EBT.
- **Recommendation.** Decided: archive `PLAN.md` (47 KB v2 plan) to
  `docs/archive/PLAN_v2.md`. Write a new ~1-page `PLAN.md` at the
  root that points readers to: `V3_VISION.md` (current plan),
  `V2_5_DATA_DESIGN.md` (data layer source of truth),
  `ARCHITECTURE.md` (strategy-doc mapping), and
  `docs/archive/PLAN_v2.md` (historical v2 plan).

### 2.4 `docs/` mixes active design + completed phase scaffolding

Inventory:

| File | Status | Recommendation |
|---|---|---|
| `docs/V2_5_RECONCILIATION.md` (45 KB) | Phased v2→v2.5 refactor plan. Root `CLAUDE.md:107` calls it *"phased plan that tracked the v2 → v2.5 refactor (complete; kept for context)."* | **Archive** to `docs/archive/`. |
| `docs/V2_5_AGENTS_PLAN.md` (50 KB) | Phased agent build plan (Phases 2A-2D). Implementation has shipped. | **Archive.** |
| `docs/V2_5_REPORT_PLAN.md` (28 KB) | Report build plan. Report artifact exists; plan is post-hoc context. | **Archive.** |
| `docs/V2_5_PHASE2A5_RESULTS.md` (4 KB) | Single-phase validation results. | **Archive or delete.** Validation artifact. |
| `docs/DASHBOARD_PLAN.md` (30 KB) | Phase 1 dashboard plan. Dashboard shipped and has evolved past it. | **Archive.** |
| `docs/DEMO_SCRIPT_AGENTS.md` (11 KB) | Demo script for the v2.5 agent phases. v3 will have a new demo script (the gold-standard beat in V3_VISION). | **Archive** or revise as the v3 demo script. |
| `docs/report.html` + `index.html` + `report_data.{js,json}` | Built report artifact. | **Keep** — output, not scaffolding. |

- **Recommendation.** **Archive five files in one pass** to
  `docs/archive/`. Net `docs/` shrinks from ~10 active to ~3
  (V3 vision lives at root; the built report stays; everything else
  is history). Reading order for a new contributor becomes
  `README.md` → `V3_VISION.md` → `V2_5_DATA_DESIGN.md` →
  `ARCHITECTURE.md`.

### 2.5 `scripts/phase2*_validate.py` — completed validation artifacts

- **Code:** 8 phase-validation scripts under `scripts/`
  (`phase2a5_validate.py`, `phase2a6_validate.py`, `phase2b1c1`,
  `phase2bc`, `phase2c1_diagnose_demand`, `phase2d1_demo_verify`,
  `phase2d1_matrix`, `phase2d_cache_verify`, `phase2d_matrix`).
- None referenced by `Makefile`, `pytest`, or any other module
  outside `scripts/`.
- **Recommendation.** **Delete** (or move to `scripts/archive/` if
  you want git-archaeology continuity). They're frozen-in-time
  validation runs against phased build milestones that have all
  completed.

### 2.6 README has a "Streamlit Cloud" section that's stale

- Noted in an earlier plan as "queued cleanup." Deployment is now
  HF Spaces (Docker SDK + LFS-shipped DB). README's deploy section
  still describes the old cold-start build flow.
- **Recommendation.** **Update.** Replace the Streamlit Cloud
  section with the HF Spaces flow. Quick fix.

### 2.7 `use_container_width` deprecation noise

- Noted earlier as queued cleanup. Streamlit 1.57 emits future-
  deprecation warnings on every dashboard render
  (`use_container_width=True` → `width="stretch"`).
- Not a v3 blocker; cosmetic in the console.
- **Recommendation.** **Defer** unless the dashboard log noise
  becomes part of a deep-dive optic.

---

## Section 3: Data-decision sanity

### 3.1 Three planted anomalies — magnitudes for stakeholder demo

Walking each against "would this pop in a chart without explanation?":

- **University City decline.** Per-grocer peak effective multipliers
  (Apr 26 – May 2): KRG **0.55×** (~45% drop), ACM **0.64×** (~36%),
  WDX **0.685×** (~32%). Each grocer has 1-3 UC stores × ~30
  txns/store/day baseline → KRG goes from ~75 to ~41 daily txns at
  peak. The 4-stage ramp (1.10 → 0.85 → 0.55 → 0.65) draws a
  recognizable curve. Visible on any daily-traffic chart by week.
  *Excellent* for the "is this affecting peers?" cross-merchant
  beat — all three grocers ramp together, demonstrating market-wide
  context the agent can frame as "not idiosyncratic to you."
- **Plaza Midwood avocado spike.** KRG PM only, avocado SKUs
  only, 4 days (Apr 21-24, peak Apr 22 at 5×). Single-store,
  single-SKU, single-window — exactly what the anomaly agent should
  flag as a *local* event with no peer corroboration. Magnitude
  is dramatic at the right granularity (per-SKU per-store per-day)
  and vanishes at broader rollups (great teaching moment).
- **Pasta promo divergence.** KRG +2.2×, ACM 0.8× (failure), WDX
  +1.4×, three overlapping windows in late April. The ACM failure
  is the v3 demand-agent gold play: discount was applied, volume
  *fell*, agent has to recommend an investigation. Magnitudes are
  strong enough to read in a weekly bar chart.

**Why it matters for v3.** All three magnitudes are demo-friendly
without tuning. None require statistical squinting. Each maps to a
distinct cross-merchant agent move (peer corroboration for UC; peer
non-corroboration for Plaza Midwood; competitive contrast for pasta).

**Recommendation.** **Keep all three.** No amplitude changes needed
for v3 stakeholder demo. *Consider* one addition: the v3 vision's
gold beat anchors on dairy pricing, not on any of the three planted
anomalies. There's no planted "your dairy is systematically priced
above peers" signal — the dairy gap will exist only to the extent
the catalog overlays' tier multipliers happen to create it. Worth
verifying once data is regenerated; may need a fourth planted signal
(intentional KRG dairy premium) to guarantee the anchor chart tells
the story cleanly.

**Decision needed from me?** Decided: do not plant a fourth
anomaly. Do not manipulate catalog overlays to force the dairy
spread. The dairy chart's takeaway sentence is computed from the
data and will say whatever is true. Phase 2 verifies what the data
actually shows. If the dairy chart doesn't tell a strong story,
`V3_VISION.md`'s worked example becomes illustrative of the
chart-design pattern rather than a promise about specific numbers,
and we may pick a different anchor question for the demo.

### 3.2 Per-merchant peer mapping (same-segment first, then alphabetical)

Covered in §1.3. **Keep.** No change.

### 3.3 Two-tier pricing for grocery overlays

**Finding.** Per `src/generate/CLAUDE.md`, per-grocer overlays in
`data/catalogs/overlays/` set inclusion list + tier multipliers
against `base_grocery_catalog.json`. Two-tier means each SKU at each
grocer multiplies the base price by a fixed factor.

**Why it matters for v3.** The dairy anchor chart's headline ("you're
priced above peers on 7 of 10 dairy staples") is determined entirely
by the overlay tier-multiplier configuration. Two tiers means binary
choices per SKU per grocer — not enough variance to produce realistic
"some SKUs above, some below" distributions unless the overlay sets
mixed tiers per category.

**Recommendation.** Verify in Phase 2 what the actual spread is, and
document what the chart honestly shows. Tuning overlays to engineer
a specific dairy spread crosses the same line as planting a fourth
anomaly (see §3.1 decision) — the chart's takeaway must reflect the
data, not stage-manage it.

**Decision needed from me?** No — this is a Phase 2 (post-audit)
verification step.

### 3.4 Tax model (5-tier by category)

**Finding.** 0% exempt grocery / 4% snacks-beverages / 7% non-food
+ prepared. Stored in `tenant_transactions.tax_total` and
`tenant_transaction_items.tax`. **Tax is not exposed in the lake**
(neither `tax_total` nor `tax` appears in `lake_transactions`).

**Why it matters for v3.** Tax is invisible to every demo path —
no chart uses it, no agent prompt mentions it. 5-tier exists for
realism flavor, but the dashboard and agents don't read it. Could
be 1-tier and no one would notice.

**Recommendation.** **Keep.** Zero maintenance cost; preserves the
realistic-flavor for anyone reading the schema. No change.

**Decision needed from me?** No.

### 3.5 10-bucket time-of-day and 10-bin txn_total

**Finding.** Both are present in the lake (`txn_hour_bucket`,
`txn_total_bin`). Time-bucketing is used by `views.render_time_patterns`
(hour × day-of-week heatmap). txn_total binning serves the privacy
goal (obscure exact totals) and could feed a peer-ticket-distribution
chart.

**Why it matters for v3.** Both have demo value at the right
chart. 10 buckets / bins is finer than typical visualizations
(charts often use 5-7), but the agent or dashboard layer can
re-aggregate for display. Not a fragility — granularity is easier
to reduce than to recover.

**Recommendation.** **Keep both as designed.** No change.

**Decision needed from me?** No.

### 3.6 k=5 threshold given v3's likely query shapes

Covered in §1.2 — k=5 doesn't bite the anchor charts at their
natural rollup granularity. The real issue is the *enforcement gap*
(documented but not auto-applied), called out as decision in §1.2.

**Recommendation.** No change to the k value. Decision on enforcement
in §1.2.

**Decision needed from me?** No (folded into §1.2).

---

## Decisions (all locked 2026-05-17)

1. **§1.1 — Materialize the lake at seed time.** Per-viewer tables;
   agent-facing SQL contracts unchanged.
2. **§1.2 — Wire k=5 suppression into the agent's `query_lake`.**
   Update lake-query prompt instructions to require COUNT(*) on
   customer-dimension breakdowns.
3. **§1.5 — Add per-viewer tenant SQLite views.** Regex check stays
   as a second layer.
4. **§1.6 — Archive legacy `advisor.py`** to `docs/archive/legacy_agent/`.
   Update both `CLAUDE.md` files.
5. **§3.1 — Do not plant a fourth anomaly; do not manipulate data.**
   The dairy chart honestly reports what the data shows. Phase 2
   verifies. V3_VISION.md's worked example is illustrative of shape,
   not a promise of specific numbers.
6. **§2.3 — Archive PLAN.md** to `docs/archive/PLAN_v2.md`; write a
   1-page pointer.

Non-decision cleanups (no debate, proceed in the same execution pass):
- Update root `CLAUDE.md` and `src/agents/CLAUDE.md` to describe real
  agent inventory (§2.1).
- Archive five docs in `docs/`: `V2_5_RECONCILIATION.md`,
  `V2_5_AGENTS_PLAN.md`, `V2_5_REPORT_PLAN.md`,
  `V2_5_PHASE2A5_RESULTS.md`, `DASHBOARD_PLAN.md` (§2.4).
- Move eight `phase2*_validate.py` scripts to `scripts/archive/` (§2.5).
- Update README's deployment section: Streamlit Cloud → HF Spaces (§2.6).

---

## Phase 1.5 completed 2026-05-17

All six locked decisions executed in six commits — one per step,
verified independently. Final test count: 212 (was 213 pre-Phase-1.5;
delta of 6 archived MerchantAdvisor tests, +5 new bypass + suppression
tests). Streamlit boots clean (HTTP 200 on root + rerun) at each step
boundary. Anchor-chart lake queries measured 5.8× / 22.3× faster
post-materialization (see §1.1 table).

### Steps executed

| Step | Commit | What landed |
|---|---|---|
| 1 | `522dcea` | Doc archives: `PLAN.md` → `docs/archive/PLAN_v2.md` + new 1-page pointer; five `docs/V2_5_*.md` + `DASHBOARD_PLAN.md` → `docs/archive/`; nine `scripts/phase2*_validate.py` + `phase2c1_diagnose_demand.py` → `scripts/archive/`; README "Streamlit Cloud" → "HuggingFace Spaces"; baselined `V3_AUDIT.md`. |
| 2 | `0cff41a` | Archived legacy `MerchantAdvisor`: `src/agents/advisor.py` + `prompts/advisor.md` → `docs/archive/legacy_agent/`. Split `tests/test_agents.py` — 6 MerchantAdvisor tests moved to `legacy_agent/test_legacy_advisor.py.archived`. Rewrote root `CLAUDE.md` agents section + `src/agents/CLAUDE.md` opener. |
| 3 | `df7c827` | 30 per-viewer tenant views added to `schema.sql` (5 merchants × 6 tables). `query_tenant` CTE-wraps the agent's SQL to shadow `tenant_<table>` with `tenant_view_<viewer>_<table>`. Customers view uses DISTINCT + JOIN against transactions (defeats `GROUP BY primary_grocer` panel-distribution leakage). 3 new bypass-attempt tests. |
| 4 | `f315b40` | k=5 suppression hook in `query_lake`. New `_find_count_column_index` + `_maybe_suppress_sub_k`. Each specialist prompt gains a rule: "include `COUNT(*) AS n` on customer-dimension breakdowns so suppression can apply." 2 new suppression tests. |
| 5 | `4900155` | Lake materialized at seed time as 10 per-viewer physical tables. `get_lake_*` rewritten to read materialized tables; agent SQL contract preserved via CTE wrapper. Indexes on anchor-chart query shapes. Test rewrites: 10 `sql_filter` calls in `test_lake_views.py` from tenant aliases to lake column names, plus the legacy `test_no_physical_lake_tables` rewritten (see Deviation 1 below). Stale "lake is virtual" docstrings updated in `views.py`, `schema.sql`, `queries.py`. `V2_5_DATA_DESIGN.md` got a "Phase 1.5: materialized at seed time" subsection. `V3_AUDIT.md` §1.1 got the speedup table. |
| 6 | *(this commit)* | Phase 1.5 summary appended here. No code changes; verifications below. |

### Deviations from plan

**1. `tests/test_db.py::test_no_physical_lake_tables` needed rewriting.**
Beyond the planned 10 `sql_filter` rewrites in `test_lake_views.py`,
one more test required surgery in Step 5: `test_no_physical_lake_tables`
asserted the v2.5 invariant *"v2.5 holds no physical lake_* tables —
the lake is virtual,"* which Decision §1.1 explicitly inverts. The
test failure was the architecture change landing correctly, not a
problem with the materialization. Surfaced via `AskUserQuestion`
before patching per the workflow contract; user-approved rewrite to
`test_lake_materialized_tables_present` asserts the 10 expected
per-viewer tables exist, each non-empty, with nothing else matching
`lake_%`. The new test catches both broken materialization (empty
tables) and future drift (e.g., a 6th viewer added without updating
the panel set).

**2. Materialization is slower than the plan's estimate.** The plan
expected ~40 s of materialization work added to seed time; actual cost
is ~91 s (5 viewers × ~18 s each — the UDF template is the
bottleneck, exactly the cost we're amortizing away from runtime). Net
`make seed` end-to-end: ~30 s → ~120 s. Paid once per build; runtime
queries dropped from 1.7–2.8 s to 126–298 ms, more than recouping the
build-time spend on the first dashboard interaction.

**3. Documentation drift discovered during Step 5 was wider than the
single file named in the plan.** Plan called for updating
`V2_5_DATA_DESIGN.md`'s "lake as parameterized views" section; in
practice three more files had stale "lake is virtual / computed at
query time" claims (`src/lake/views.py` top docstring, `src/db/schema.sql`
header comment, `src/db/queries.py` docstring). Fixed in the Step 5
commit alongside the design-doc update. None is a behavior change;
all are documentation precision.

### Design calls that came up mid-execution

**Idempotency of the lake materialization.** Confirmed `seed.py`'s
existing pattern (`if DB_PATH.exists(): DB_PATH.unlink()` at the top
of `main()`) makes `CREATE TABLE` / `CREATE VIEW` DDL inherently
idempotent against the wipe — no `IF NOT EXISTS` needed on the views
in `schema.sql`. For the lake materialization in `seed.py`, added
defensive `DROP TABLE IF EXISTS … ; CREATE TABLE … AS SELECT` plus
`CREATE INDEX IF NOT EXISTS` so a hypothetical future workflow that
doesn't wipe the DB (e.g., incremental rebuild) stays correct. Did
not run `make seed && make seed` end-to-end as a probe (3–4 minute
cost, redundant against the DB-wipe pattern); the defensive DDL is
the standard SQLite idiom and the wipe pattern is the actual
execution path today.

**Where `unsafe_allow_html` lives.** The plan's verification step
expected zero hits in `src/dashboard/` overall; the actual repo state
has many pre-existing hits in `views.py`, `app.py`, `styling.py` (the
dashboard's panel-card / KPI-card layout HTML — out of scope for
Phase 1.5). `src/dashboard/chat.py` is clean (zero hits — that was
removed in a prior task). Phase 1.5 added zero new
`unsafe_allow_html` lines anywhere; the verification's strict claim
was over-stated and the Phase-1.5-specific invariant ("Phase 1.5
adds no new `unsafe_allow_html`") holds.

### HF Spaces deployment note (out-of-band)

The `data/payments.db` shipped via Git LFS to HF Spaces was built
against the pre-Phase-1.5 schema — it has neither the 30 new tenant
views nor the 10 materialized lake tables. The dashboard would fail
on the first `query_lake` against `lake_transactions_<viewer>` after
this commit chain ships to HF as-is. To bring HF in sync: locally
`make seed` to rebuild the DB (already done — sits at HEAD on disk),
then `git add data/payments.db && git push hfspace main`. This is a
deployment operation, not Phase 1.5 source work; left to the user
to run when ready to push the foundation hardening to production.

### Files touched (top-line)

```
Step 1: 18 files (PLAN.md + new pointer; 5 docs + 9 scripts to
        archive; README; V3_AUDIT.md baselined)
Step 2:  6 files (advisor.py + advisor.md archived; test split;
        2 CLAUDE.md files rewritten)
Step 3:  3 files (schema.sql, tools.py, test_agents.py)
Step 4:  6 files (tools.py, 4 specialist prompts, test_agents.py)
Step 5:  9 files (seed.py, views.py, schema.sql, queries.py,
        tools.py, V2_5_DATA_DESIGN.md, V3_AUDIT.md,
        test_db.py, test_lake_views.py)
Step 6:  1 file  (V3_AUDIT.md — this section)
```

The `data/payments.db` regenerated locally during Step 5 is
intentionally not committed to git proper — the binary is LFS-tracked
and updates separately from the source code commits.

### Final test results

```
$ uv run pytest -q
================ 212 passed, 21 deselected in 60.7s ================
```

Test deltas across the six steps:

| | Pre-1.5 | Step 2 | Step 3 | Step 4 | Step 5 |
|---|---:|---:|---:|---:|---:|
| Total passing | 213 | 207 | 210 | 212 | 212 |
| Δ | — | −6 archived | +3 bypass | +2 suppression | 0 (rewrites) |

---

## Phase 1.6 completed 2026-05-19

Synthetic-data calibration to address two Phase 2 findings:

1. Customer overlap unrealistically broad (87% shopping at 3+
   merchants in 90 days).
2. Grocer pricing positioning invisible (±2.4% noise envelope on
   dairy staples, no positioning signal).

Calibration discipline: parameter-level targets set before regenerating,
no iteration to chase specific chart findings, one tuning pass
allowed per parameter family if regenerated data wildly implausible
(unused — both passes landed in target bands).

### Pass 1 — affinity + pricing positioning

Commit: `Phase 1.6 Pass 1: calibrate affinity + pricing parameters`.

Parameters changed:
- `LOYALIST_CHAIN_CHOICE` (parameters.py:143): 0.90/0.08/0.02 →
  0.94/0.05/0.01. Loyalists' mean 2nd-grocer visits drop from
  ~1.8 to ~1.1 over 22 trips.
- `QSR_TRIP_BUCKETS` (parameters.py:120-123): 50%/50% bimodal at
  6-15 / 0-3 trips → 30%/40%/30% trimodal at 6-12 / 2-5 / 0-1
  trips. More realistic gradient.
- Grocer overlay multipliers in `data/catalogs/overlays/`:
  tight tier ACM 1.03 → 1.05, WDX 0.97 → 0.95. Loose tier ACM
  1.07 → 1.10, WDX 0.93 → 0.90. KRG remains 1.00/1.00 baseline.
  Symmetric multipliers around KRG so peer-average centers at KRG.

Verification (V3_DATA_QUERIES_PASS1.md):
- Section 3.3 customer-overlap distribution shifted from
  1.7/11.7/36.6/38.5/11.5 (orig) to 3.7/17.0/39.3/31.4/8.6 (P1).
  20.7% at 1-2 merchants vs 30% target; 31.4% at 4 merchants vs
  20% target. Direction correct, magnitude moderate. Accepted —
  meaningfully more realistic than baseline.
- Section 2.1 KRG-vs-peer-average gaps remained near zero (by
  design — symmetric multipliers collapse to 0 at peer-average).
- Section 2.3 per-peer dairy gaps: KRG -4.97% vs peer_a (ACM),
  +4.91% vs peer_b (WDX), both within ±2% noise envelope.
  Verified pricing change fired correctly.
- Section 2.4 per-peer household gaps: KRG -9.52% vs peer_a,
  +11.02% vs peer_b. Verified loose-tier multipliers.

Verification-method finding: when symmetric calibration is in play,
peer-average queries collapse to baseline by construction. The
visible signal lives in per-peer slices, not peer-average. Folded
into V3_VISION.md's chart spec revision (Deliverable 2).

### Pass 2 — trade area + category emphasis + shopping patterns

Commit: `Phase 1.6 Pass 2: differentiate grocers along trade-area,
category mix, basket size`.

New mechanism: per-merchant category-purchase-probability weights.
Mechanism (b) from the Pass 2 mapping report — did not exist
previously. Category emphasis was purely inventory-driven (mechanism
a), which is why grocer category mixes were near-identical
pre-Pass 2.

Parameters changed (all new dicts in parameters.py + threading
merchant_id through metro.py and transactions.py):
- `MERCHANT_NEIGHBORHOOD_BIAS`: ACM concentrates in SouthPark,
  Ballantyne, Dilworth (2.0× weight) and de-emphasizes NoDa,
  Pineville, Mooresville (0.5×). WDX concentrates in NoDa, UC,
  Pineville, Mooresville (2.0-2.5×) and de-emphasizes affluent
  neighborhoods (0.4-0.5×). KRG broad-coverage baseline (no
  overlay).
- 5-neighborhood shared comparison footprint enforced via
  `require_zips`: Dilworth, SouthPark, University City, Ballantyne,
  Plaza Midwood. Each grocer has ≥2 stores in each. Critical for
  cross-merchant peer comparison at the neighborhood level.
- `MERCHANT_CATEGORY_BIAS`: KRG produce 1.20 / meat 1.10
  (fresh-forward). ACM dairy 1.20 / bakery 1.10 / pantry 0.85 /
  household 0.90 (premium-prepared). WDX pantry 1.20 / frozen
  1.15 / produce 0.85 (value-pantry).
- `MERCHANT_BASKET_SIZE_MULT`: ACM 0.90, KRG 1.00, WDX 1.20.
  High-end cap on hi parameter when mult > 1.0 (WDX max basket
  capped at original triangular ceiling to prevent unrealistic
  48-item baskets).

Verification (V3_DATA_QUERIES_PASS2.md):
- Section 4.5 trade area distribution: ACM 16/25 = 64% in
  affluent neighborhoods (above the 35-40% target). WDX 5/20 =
  25% in working-class neighborhoods (below the 35-40% target).
  Asymmetry is a deliberate tradeoff: the 5-neighborhood shared
  footprint consumes 10 of WDX's 20 stores, leaving less room
  for working-class concentration. The shared footprint is
  load-bearing for the cross-merchant peer comparison demo
  story; accepting weaker WDX trade-area positioning is the
  correct tradeoff.
- Section 5.1 category emphasis: KRG produce +1.4pp, meat +1.1pp.
  ACM dairy +2.1pp, pantry -1.9pp. WDX pantry +2.4pp, frozen
  appears in top 5 at 7.7%, produce -1.5pp. Grocers' top-5
  category fingerprints now distinct (KRG: MEAT/PANTRY/PRODUCE/
  DAIRY/HOUSEHOLD; ACM: MEAT/PANTRY/DAIRY/PRODUCE/HOUSEHOLD;
  WDX: MEAT/PANTRY/DAIRY/HOUSEHOLD/BEVERAGES).
- Section 4.1 basket size: ACM median 9, KRG median 10, WDX
  median 11. WDX p95 30, max 40 (cap verified). No unrealistic
  high-end baskets.
- Section 4.2 ticket: ACM median $72.57, KRG $77.75, WDX $80.86.
  Ticket inversion from original target (premium grocer no longer
  the highest-ticket grocer). Pass 2 finding: basket-size
  dimension dominates unit-price dimension in trip-total terms.
  This is realistic — real value grocers see higher tickets than
  premium grocers because shoppers stock up. Demo storyline
  shifts from "ACM tickets higher" to "ACM tickets reflect
  smaller-pricier baskets, WDX reflects larger-cheaper baskets."
- Section 5.6 Pass 1 pricing preserved: per-peer dairy gaps
  -4.97 / +4.58 (vs Pass 1's -6.18 / +4.91). Within noise
  envelope; Pass 1 calibration not perturbed by Pass 2 changes.
- Section 3.3 customer overlap distribution preserved: ±1pp from
  Pass 1 across all five buckets. Pass 1 calibration not
  perturbed.
- Section 6.1/6.2 University City decline preserved: KRG ~500
  weekly baseline → ~270 at trough, ACM ~530 → ~400, WDX ~340
  → ~280. Planted anomaly still reads clearly post-regeneration.

Observation worth filing for Phase 3: TBL transaction count
dropped from ~49K to ~36K (-27%) due to the Pass 1 QSR trip-bucket
recalibration. Customer count at TBL similar (~7,700); each
customer making fewer trips. Plenty of activity for demo
analytics; just less than pre-calibration.

### Phase 1.6 summary

Foundation now supports the v3 cross-merchant story credibly:
- Customers have meaningful primary-grocer preferences (cross-
  merchant insight isn't tautological since "your customers
  shop everywhere" isn't true anymore).
- Grocers are visibly distinct on four dimensions: pricing
  positioning, trade area, category emphasis, basket size /
  ticket shape.
- Planted anomalies preserved.
- Cross-merchant peer comparison footprint preserved (5 shared
  neighborhoods, ≥2 stores per grocer per neighborhood).

Deploy implication: same schema as Phase 1.5. HF Spaces redeploy
is data-only (LFS-shipped DB needs rebuild + repush); no schema
migration. Same caveat as Phase 1.5 close-out: hold the deploy
until Phase 3-6 work is more shippable.

No tuning pass used. Both passes accepted as regenerated.
