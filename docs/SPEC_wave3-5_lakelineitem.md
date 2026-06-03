# SPEC — Wave 3.5: Line-Item Lake + SQL Surface (Lake & Agent Re-Architecture)

**Status:** Draft for build
**Branch target:** `v4` (continues from closed Wave 3 state; NO PR, NO merge to main)
**Supersedes:** the Wave 2 manifest-aggregate lake and the `read_lake_table` surface; most of the Stage 6.5 merge/compose compensation layer.

---

## 1. Why this exists (the one-paragraph rationale)

Wave 2 published peer data as pre-computed aggregate tables at fixed dimensional grains, in unitless indices, reachable only through a filter-only tool (`read_lake_table`). The agent could **consume** those aggregates but never **compose** its own view. Sixteen Stage 6.5 structural fixes (Fix 9–14) were each a compensation for that single constraint — teaching the model to navigate a closed dimensional vocabulary it did not design. The residuals (pricing direction-only, flat demand index, geography label-space mismatch, chart-intent shape variance, run-to-run merge instability) all trace to that upstream choice, not to the agent infrastructure. Wave 3.5 removes the root cause: **the peer lake becomes raw line-item data the agent queries with SQL — the same motion it already uses for tenant data.** A cross-merchant comparison becomes "query own, query peer, compare," with no merge tool, no manifest grain matching, no index transform.

---

## 2. Locked decisions (the foundation — do not re-litigate during build)

1. **Line-item lake + SQL surface replaces the aggregate tables.** Verify-then-remove, not a destructive swap (see §11).
2. **Real geography.** Lake carries the real `neighborhood` name (observable). The derived Z-code concept is removed entirely. **No `zone` field** — `zone_id` is a *planted profile* column that `observable_guard.py` forbids reading (§1 / D23.1), so it cannot become a published "real zone." The only other observable geography is `metro_region` (urban_core / inner_suburbs / outer_suburbs); it's available as an optional coarse grain but not published by default. `neighborhood` is the geography grain.
3. **No differential-privacy noise.** Peer values are published as real aggregates in real units.
4. **Minimal privacy posture:** identity hidden (peer_relationship label, never merchant name or pseudonym) + `k=5` cell suppression at query time. Nothing more for now; build up later.
5. **All analytical fields raw:** `unit_price`, `qty`, `discount`, `line_total`, `category`, `subcategory`, and payment dims all kept unbinned. Protection comes from the query-time floor, not from coarsening values.
6. **Charts are held for Wave 4.** Agent responses in 3.5 are prose + grounded claims + the result table only. No chart generation.
7. **Per-viewer materialized scoping.** 5 physical lake copies (one per merchant), viewer-excluded and peer-set-selected at build time.

### Pressure-test resolutions (decided; flagged reversible where noted)

| # | Concern | Decision | Reversible? |
|---|---------|----------|-------------|
| 1 | 2-store peer comparison is thin/noisy | **Lean in** — report what clears k=5; no special hedging required | Yes — can add count-based hedging later |
| 2 | MIN/MAX expose a real individual value through an aggregate | **Allow for now** — accept the small exposure; tighten later | Yes — ban on raw value columns later |
| 3 | Conversational advisor has no fixed specialist | **Route to a specialist if possible; if not, default to cross-segment labeled** | — |
| 4 | Count injection must survive nested SQL (CTEs/joins/subqueries) | **Build the robust version** (outer-wrap / re-aggregate) | No — must be correct |
| 5 | Synthetic merchants may be too homogeneous to make comparison interesting | **Trust existing data** — sanity-check during verification only | — |
| 6 | Build no longer matches strategy-doc §8 privacy posture | **Add the note** — demonstrator runs minimal privacy; §8 is production target | — |

---

## 3. Data architecture

### 3.1 Two line-item tables (mirroring v3's shape)

**`lake_transactions`** — one row per peer purchase line:

| Column | Treatment | Notes |
|--------|-----------|-------|
| `lake_txn_id` | Tokenized (SHA-256 + salt) | non-reversible; enables GROUP/JOIN without exposing real IDs |
| `lake_line_id` | Tokenized | `lake_txn_id` + within-txn sequence; non-reversible, unique |
| `lake_store_id` | Tokenized | joins to `lake_stores` |
| `txn_date` | Generalized | date only |
| `hour_bucket` | Generalized | coarse time-of-day, 10 buckets/day (raw `txn_ts` dropped) |
| `peer_relationship` | Identity label | `peer` (same segment as viewer) or `merchant` (different segment). NEVER a name or pseudonym |
| `category` | **Raw** | primary analytical dimension |
| `subcategory` | **Raw** | |
| `unit_price` | **Raw** | the whole point — enables real-dollar comparison |
| `qty` | **Raw** | |
| `discount` | **Raw** | |
| `line_total` | **Raw** | |
| `payment_type` | **Raw** | powers payment-mix questions. Source raw column: `tender` (credit/debit) |
| `card_network` | **Raw** | source raw column: `network` (visa/mc/amex/discover) |
| `entry_mode` | **Raw** | source raw column: `entry_mode` (contactless/chip/swipe/manual) |
| `wallet_type` | **Raw** | source raw columns: `wallet_provider` (apple/google/samsung/NULL) + `wallet_at_tap` |

> **Column-name mapping (build §13.A reads the real raw schema):** `payment_type←tender`, `card_network←network`, `wallet_type←wallet_provider`, `entry_mode←entry_mode`. Use the renamed forms in the lake so the agent's SQL and prompt examples reference stable names. All source columns are in `observable_guard.ALLOWED_COLUMNS` for `transactions`.

**Dropped entirely:** any `customer_id` / per-shopper linkage. No consumer-level thread at any grain. (Non-negotiable even at minimal privacy.)

**`lake_stores`** — peer store reference:

| Column | Treatment |
|--------|-----------|
| `lake_store_id` | Tokenized (joins to transactions) |
| `peer_relationship` | `peer` / `merchant` |
| `peer_segment` | segment label (`grocery` / `qsr` / `off_price`) — for routing logic. Canonical config vocab (see §4) |
| `neighborhood` | **Raw real name** (observable; no Z-code) |

(No `zone` column — see decision §2.2. No `store_zip3` either: the v4 raw schema carries **no ZIP** anywhere — `stores` has only lat/long + `neighborhood` + `metro_region` + planted `zone_id`. `neighborhood` is the sole published geography. lat/long are **not** carried — they'd localize a real store. `metro_region` may be added later as an optional coarse grain.)

### 3.2 Identity labeling rule

`peer_relationship` is computed **relative to the viewing merchant**:
- A store in the **same segment** as the viewer → `peer`.
- A store in a **different segment** → `merchant`.

This is why it must be set at per-viewer build time (§5), not stored globally — the same physical store is a `peer` to a same-segment viewer and a `merchant` to a different-segment one.

### 3.3 Build-time generalization steps (inherited from v3, with our dials)

- Tokenize `lake_txn_id`, `line_id`, `lake_store_id` (SHA-256 + salt; non-reversible). **The salt is derived from `cfg.global_['seed']` (fixed, default 42), not random** — the project guarantees byte-identical Parquet across runs (pyarrow pinned, single-threaded, T18). A random salt would break that determinism.
- `txn_ts` → `txn_date` (date) + coarse hour bucket (e.g. 10 buckets/day). Raw timestamp dropped.
- **Geography:** carry `neighborhood` through raw — it is the sole published geography. There is **no ZIP** in the v4 raw schema, so there is no `store_zip3`. Do **not** carry lat/long (would localize a real store), do **not** read `zone_id` (planted; forbidden by `observable_guard.py`), and do **not** run the k-means Z-code derivation (`src/lake/zones.py`) — all removed (§2.2).
- Drop `customer_id` and any per-shopper field.
- Set `peer_relationship` per viewer (§3.2).
- **Viewer exclusion baked in** — the viewing merchant's own rows are absent from their lake (structural, not a runtime filter).
- All other analytical fields pass through **raw**.

---

## 4. The five merchants & segment structure

Segment labels use the **canonical config vocab** (`src/generate/config/`, `src/lake/scope.py`): `grocery`, `qsr`, `off_price`. (Not `grocer`/`retail` — TJX is `off_price`, not "retail".)

| Merchant | Banner | Segment | Same-segment peers |
|----------|--------|---------|--------------------|
| Kroger | KRG | `grocery` | ACM, WDX (2 peers) |
| Acme | ACM | `grocery` | KRG, WDX (2 peers) |
| Winn-Dixie | WDX | `grocery` | KRG, ACM (2 peers) |
| Taco Bell | TBL | `qsr` | **none** |
| TJ Maxx | TJX | `off_price` | **none** |

**Consequence:** only the three grocers have same-segment peers (2 each). QSR (TBL) and off-price (TJX) have zero. This drives the peer-availability routing in §6 and the `k=5` floor choice (k=50 would suppress nearly every 2-store peer slice; k=5 lets 2-store category comparisons return data).

---

## 5. Viewer scoping (per-viewer materialization)

At build time, produce **5 pairs** of `(lake_transactions, lake_stores)` — one pair per merchant. Each pair:
- **Excludes** the viewing merchant's own rows.
- **Includes** the other 4 merchants' rows, each labeled `peer` or `merchant` relative to this viewer.

Sizing (full scale): the lake is line-items, not transactions — ~10M line items (the ~1.67M figure is *transactions*). Each per-viewer copy excludes the viewer, so it's ~4/5 of the base ≈ ~8M lines; 5 copies ≈ ~40M line rows total ≈ 4× base. As compressed Parquet that's still modest (well under a couple GB), trivial at demo scale. **Pilot mode** (5k cards, ~83k txns) produces a proportionally tiny lake; Stage D (§13) verification runs against whatever `data/lake/` currently holds — confirm which scale before reading peer magnitudes.

`query_lake_sql` resolves the unqualified `lake_transactions` / `lake_stores` references to **the current viewer's pair** via CTE-wrap (§8).

> **Scaling note (Wave 4+):** per-viewer materialization is fine at demo scale (5 merchants). If merchant count grows large, switch to query-time scoping (one shared table, server-injected `WHERE` exclusion + peer-set filter). Not a now concern.

---

## 6. Peer-availability routing (server-determined, NOT model-inferred)

The system knows each viewer's **segment-peer count** (from §4). Routing keys off the **running specialist**, not the model's judgment:

| Specialist / question type | Viewer has ≥1 segment peer | Viewer has 0 segment peers |
|---|---|---|
| **Pricing** | Normal peer comparison ("your X vs the N peers' avg") | **"No comparable peers available."** Own-data only. Do NOT substitute cross-segment. |
| **Demand / Trade-area / Trends** | Normal peer comparison | **Cross-segment comparison, clearly labeled** "broader merchant set, not same-segment." |
| **Anomaly** | Peer baseline where applicable | Cross-segment baseline, labeled; or own-trend only if cross-segment is meaningless |

**Conversational Advisor (free-form):** route to the appropriate specialist when the question maps to one; that specialist's rule then applies. If the question does not map cleanly to a specialist, **default to cross-segment labeled** (decision #3). The decline-side is reserved for the pricing-type case where cross-segment is genuinely apples-to-oranges.

**Implementation:** each viewer's lake build emits a small metadata record — `segment_peer_count` and `segment` — that the specialist behavior keys off. The routing is a structural branch on that metadata, not a prompt instruction the model must remember.

---

## 7. Privacy mechanics

### 7.1 The k-floor

- Constant: `LAKE_K_FLOOR = 5` (single configurable constant; raise later without code change).
- Any returned group/cell backed by **fewer than 5 underlying line records is dropped** before results reach the agent.
- The agent is told **"N cells suppressed for thin coverage"** so it can retry at a coarser grain rather than silently miss data.

### 7.2 Aggregating-only (the primary protection)

- `query_lake_sql` permits **aggregating queries only**. A query whose outermost `SELECT` lacks **both** a `GROUP BY` and any aggregate function — including a bare `SELECT *` or `SELECT category, unit_price FROM lake_transactions ...` — would return raw individual line rows and is **rejected** with a legible reason.
- **Detect this on the parsed AST, not with regex.** The existing `isolation.py` uses regex, which cannot reliably tell an aggregating query from a raw-row select across CTEs/joins/subqueries (see §7.3 for the same constraint on count injection). Use **DuckDB's own SQL parser** — `json_serialize_sql(?)` returns the statement AST as JSON with no new dependency (DuckDB is already the engine) — and inspect the *outermost* `SELECT`: accept only if it has a `GROUP BY` or every projected expression is an aggregate. Reject `SELECT *` outright.
- The AST aggregating-only check above is what makes raw-row exposure *structurally* impossible; the `k=5` count-floor is a second line of defense for thin cells. **Correction (verified in build):** the earlier claim that grouping by `lake_txn_id` "produces count-1 groups" is **wrong** — it produces one group per basket of `N` *lines*, so a basket with ≥5 lines clears the line-floor. A query like `SELECT lake_txn_id, SUM(line_total) … GROUP BY lake_txn_id` therefore returns anonymous **per-basket** aggregates for ≥5-line baskets. This is an accepted residual at the demo's minimal posture: there is **no consumer linkage** (card/customer dropped at build), so these are unlinkable single anonymous transactions — the same class of "small exposure" as the MIN/MAX allowance below, not PII.
- **MIN/MAX and percentile-type functions are ALLOWED for now** (decision #2) — accepted small exposure of a single real value. Flagged for later tightening (raise the posture together): *"ban single-row-exposing functions on raw value columns"* AND *"floor on `COUNT(DISTINCT lake_txn_id) ≥ k` so each published cell spans ≥k distinct transactions, not ≥k lines"* — the latter closes the per-basket residual but is not always injectable (a CTE that projects away `lake_txn_id` leaves no txn id at the outer grain), so it needs the same constrained-contract treatment as §7.3.

### 7.3 Count injection (robust — decision #4)

- The server **injects a per-group record count** into the agent's query automatically; the agent writes natural analytical SQL and never has to add `COUNT(*)`.
- **Must handle nested SQL** — CTEs, subqueries, joins to `lake_stores`. The naive "assume a flat GROUP BY" approach is forbidden: it breaks on real queries and, worse, can miscount silently and let thin cells through.
- **There is no post-hoc way to recover per-group line counts** from an already-aggregated result — the rows are gone. So the count must be injected into the agent query's *own* outermost projection, which means parsing the SQL.
- **Pin the mechanism: DuckDB `json_serialize_sql()` AST** (same parser as §7.2; no new dependency). Walk to the outermost `SELECT`, read its `GROUP BY` keys (or detect a whole-table single-group aggregate), inject `COUNT(*) AS _k` into that projection, run, suppress rows with `_k < LAKE_K_FLOOR`, then **strip `_k`** before the result reaches the agent and use the dropped count only for the "N suppressed" notice. **Explicitly forbidden:** the regex / flat-GROUP-BY shortcut.
- **Constrain the contract so "inject at the outermost SELECT" is provably correct.** The subtle trap: if aggregation happens inside a CTE/subquery and the outermost `SELECT` merely passes it through (`WITH x AS (SELECT category, AVG(price) ... GROUP BY category) SELECT * FROM x`), then `COUNT(*)` at the outer level counts *CTE output rows* (one per category), **not** the underlying lines — silently wrong, lets thin cells through. So **require the result-grain aggregation to BE the outermost `SELECT`** (joins to `lake_stores` and subqueries/CTEs in the `FROM` that don't pre-aggregate are fine; this covers every realistic query — filter + join + `GROUP BY category`/`neighborhood`/`payment_type`). **Reject** the aggregate-in-CTE-then-passthrough shape with a clear, correctable message (e.g. "aggregate at the top-level SELECT, not inside a CTE the outer query only selects from"). This bounds the problem to a shape where the injected `_k` provably counts base lines — far lower future-rework risk than chasing fully-general nested-aggregation injection. This *tightens* decision #4's "handle nested SQL" to "handle nesting in the FROM, with the count-bearing aggregation at the outer grain."
- **Count grain is line records.** `k=5` counts underlying *lines*, not transactions (a txn with N lines contributes N rows). Transaction-level analytics (e.g. payment-mix shares) must use `COUNT(DISTINCT lake_txn_id)` in the analytical SELECT; the injected `_k` line count is purely the suppression gate. Make this explicit in the payment-mix prompt example (§13.C).

---

## 8. The `query_lake_sql` tool

Mirrors `query_tenant` so the existing grounding path handles its results unchanged.

- **Input:** a single SQL `SELECT` string.
- **Enforcement:**
  - single statement only; `SELECT` only (no DDL/DML/multiple statements);
  - aggregating-only (§7.2) — reject raw-row selects with a clear reason;
  - violations return a legible error the agent can correct from.
- **Scoping:** CTE-wrapped so `lake_transactions` / `lake_stores` resolve to **the current viewer's materialized pair**. Agent writes `FROM lake_transactions` and it transparently resolves to that viewer's peers.
- **Count injection + k=5 suppression** applied to the result (§7).
- **Output:** rows in the **same `_df_to_payload` shape `query_tenant` already returns** — so CellLookup / ValueRef / the §1.4 validator check claims against lake results with zero changes.

---

## 9. Agent flow after the rebuild

A cross-merchant comparison becomes three steps, all native motions:

1. `query_tenant(sql)` → own numbers (e.g. own dairy ASP = $3.50).
2. `query_lake_sql(sql)` → peer numbers, real dollars (e.g. peer dairy ASP = $3.42, over the 2 grocer peers, ≥5 lines/cell).
3. Reason over both, write prose, state grounded claims via CellLookup against either result set.

**Gone from the 3.5 prose path:** `build_merge` tool, auto-invoke, dual-frame routing, manifest grain matching, the index transform, `read_lake_table`. The agent no longer reconstructs a comparison from mismatched shapes — it runs two queries and compares, exactly as it already does within tenant data.

**Retained dormant (NOT deleted):** the core `merge_own_and_peer` *join* (see §10.1). It plays no role in 3.5's prose grounding — claims resolve per-frame — but it produces the `own_value` / `peer_benchmark` / `gap` column shape the Wave 4 dual-series chart builders already expect, so deleting it would force a Wave 4 rebuild. It's parked alongside the (also dormant) chart layer.

---

## 10. What is PRESERVED unchanged

Do **not** touch these — they are correct and the rebuild relies on them:

- **§1.4 validator** (claim grounding, CLAIM_TOLERANCE, two-pass scan). Works identically against `query_lake_sql` results.
- **CellLookup** and **ValueRef** — resolve against the new query results the same way they resolve against tenant/aggregate results today. (ValueRef now references real-dollar cells instead of index cells; resolution is unchanged.)
  - **Implementation note (frame registration):** with the `merged` frame gone (§11.2), `CellLookup.resolve`'s frame-walk simplifies to `tenant` → `lake`, and `ValueRef` keeps its `frame="lake"` default. For resolution to stay literally unchanged, the specialist must register the `query_lake_sql` result under the **`"lake"`** key in the `frames` dict passed to the validator (today it holds `_tenant_frame` / `_lake_frame` — re-point `_lake_frame` at the `query_lake_sql` result). A peer claim with no explicit `frame` then resolves against the lake result exactly as before.
- **`query_tenant`** path, `wrap_tenant_query`, `check_tenant_predicate` — unchanged; `query_lake_sql` reuses the CTE-wrap pattern.
- **Tenant-side everything.** Own-merchant data path is untouched.
- **The specialist dispatch model** (pills → named specialist). Reused for §6 routing.
- **Identity-strip principle and viewer-exclusion intent** — preserved (now at k=5 line-item level).

### 10.1 Wave 4 chart forward-compatibility (decision: keep the join dormant)

Charts are held for Wave 4 (§2, decision 6), but 3.5's design decisions determine whether Wave 4 charts are a *wiring* task or a *rebuild*. `chart_build.build_chart(intent, result)` reads every plotted number from a column of **one** result DataFrame (the model names columns, never values — the D25.2 guarantee). The new `query_tenant` / `query_lake_sql` outputs are real DataFrames (`_df_to_payload` `frame` key) keyed on real GROUP BY dimensions, so:

- **7 of 9 chart kinds are forward-compatible with zero rework** — `kpi_callout`, `table_drilldown`, `heatmap`, `small_multiples`, `geo_map`, `scatter_quadrant`, `waterfall` read plain dimension + metric columns. A single query result frame feeds them directly.
- **2 dual-series kinds** — `time_series_vs_peers`, `cross_merchant_comparison` (and the Fix 14 peer-series auto-add) — require **own and peer in one frame** with `own_value` / `peer_benchmark` / `gap` columns. 3.5 removes the prose-path merge AND bakes viewer-exclusion into the lake, so own (tenant) and peer (lake) arrive in **two separate frames** that no single query can combine.

**To keep Wave 4 a wiring task (chosen approach):**
1. **Keep `chart_build.py` + the `chart_intent` schema dormant** (feature-flag off, §11.2) — do not delete.
2. **Keep the core `merge_own_and_peer` join dormant** — but strip its index/grounding-era baggage: remove `check_magnitude_compatibility` (dead in real-unit world), the broadcast fallback (already trimmed in Wave 3 Stage 7), and the auto-invoke / dual-frame / merge-fail-payload routing (grounding is per-frame now). What survives is the plain join that emits `own_value` / `peer_benchmark` / `gap` — exactly the column contract the dual-series builders + Fix 14 already speak. Wave 4 calls it as a **plot-time join** of the two result frames; it is presentation-only and has no role in claim grounding.
3. **Encourage a shared GROUP BY dimension.** When the agent intends a comparable own + peer pair, both queries should `GROUP BY` the same dimension (e.g. `category`, `txn_date`) so the Wave 4 plot-join keys cleanly. Worth a one-line prompt nudge in §13.C (not a hard constraint — 3.5 prose works without it).

Net: in Wave 4, an own-vs-peer overlay chart = `query_tenant` frame + `query_lake_sql` frame → dormant `merge_own_and_peer` (now a pure plot-join) → existing `build_chart`. No new join logic, no chart-builder changes, grounding untouched.

---

## 11. ROLLBACK / UNDO — Wave 2 & Wave 3 components no longer needed

> **Sequencing rule:** ADD the line-item lake + tool + prompts and VERIFY (§13) **before** removing anything below. Each removal is its own commit for attributability. If removing a piece regresses a verified pill, stop and report rather than forcing.

### 11.1 Remove (Wave 2 lake — superseded)

| Component | Action | Notes |
|---|---|---|
| 5 aggregate Parquet tables (`lake_category_metrics`, `lake_payment_mix`, `lake_segment_mix`, `lake_trade_area`, `lake_cross_merchant_cohorts`) | **Remove** after verification | The line-item lake replaces all of them via SQL |
| The manifest (`src/lake/manifest.py` → dimensions/metrics, `manifest_for`) | **Remove** | No fixed-grain vocabulary anymore |
| `read_lake_table` tool + filter-whitelist logic | **Remove** from `TOOLS_SPECIALIST` | Replaced by `query_lake_sql` |
| The index transforms (price_index / units_index / revenue_index construction in the lake builder) | **Remove** | Real units now |
| Derived Z-code (k-means zone) generation + the neighborhood→Z mapping | **Remove** | Real geography now |
| `_compute_lake_aggregates` / the surfaced aggregates block (Fix 11a) | **Remove** | Was a workaround for the 50-row preview; SQL composes directly now |

### 11.2 Remove (Wave 3 / Stage 6.5 compensation layer — root cause gone)

| Component | Action | Notes |
|---|---|---|
| `build_merge` tool + `_dispatch_build_merge` + auto-invoke (`_auto_invoke_build_merge`) — Fix 9/10a | **Remove** | No merge step; agent runs two queries |
| Dual-frame routing + `_merge_fail_payload` + `_fallback_carry_both_sides` (Fix 10b) | **Remove** | No merge to fail |
| `check_magnitude_compatibility` + peer/own broadcast (response.py) | **Remove** | Index-era baggage; dead in real-unit world (broadcast already trimmed in Wave 3 Stage 7) |
| `merge_own_and_peer` core join (response.py) | **KEEP dormant — do NOT remove** | Plays no role in 3.5 prose grounding (claims resolve per-frame), but produces the `own_value`/`peer_benchmark`/`gap` shape the Wave 4 dual-series chart builders expect. Parked with the dormant chart layer — see §10.1. |
| Frame-walk for untagged peer claims (Fix 10c) | **Simplify/Remove** | Claims now resolve against the specific query result they came from; multi-frame walk unnecessary |
| `peer_value_col` auto-pick + per-agent metric pin (Fix 12) | **Remove** | No peer column to pick; SQL names its own columns |
| IN-clause CellLookup handling added for manifest filters (Fix 11b) | **Review** | Keep `.isin` support if still used by claim row_filters; remove only the manifest-specific path |
| `EMIT_RESPONSE`/`BUILD_MERGE` merge gating in specialist tool loop | **Remove** the build_merge gating | Emit no longer waits on a merge turn |
| Chart layer entirely (Fix 14 auto-add peer series, chart_intent, chart_build, reconciler) | **Feature-flag OFF — do NOT delete** | Charts return in Wave 4. Leave `chart_build.py` dormant (flag the response path to skip chart generation) rather than deleting it, so Wave 4 doesn't rebuild from scratch. Just gate it off in the specialist's emit path. |

### 11.3 Keep but re-point

| Component | Action |
|---|---|
| ValueRef | **Keep** — now references real-dollar cells from `query_lake_sql` results |
| Specialist prompts | **Rewrite** the lake-access block: teach `query_lake_sql` vs `query_tenant`; remove all `read_lake_table` / manifest-grain / index-caveat language; add the §6 peer-availability rules |
| `_df_to_payload` | **Keep** — both query tools use it |

### 11.4 Privacy guardrails — DOWNGRADE, document

| Guardrail | Wave 2 | Wave 3.5 | Action |
|---|---|---|---|
| Cell floor | k≥50 build-time | **k=5 query-time** | Configurable constant |
| Value form | index | **real units** | — |
| Identity | peer_relationship | peer_relationship (kept) | unchanged |
| Customer linkage | none | none | unchanged |
| `observable_guard.py` | enforced | **keep** if cheap — still blocks the builder reading planted profile columns | Recommended keep |

---

## 12. Strategy-doc update (decision #6)

Add a note to the Core Data Strategy doc §8 (Anonymization & Privacy Engine):

> *The demonstrator implementation uses a deliberately minimal privacy posture — a line-item peer lake at `k=5` cell suppression with anonymized IDs, ZIP3, hour-bucketed timestamps, dropped consumer linkage, and identity reduced to a peer/merchant relationship label. The stronger posture described in this section (k≥50, differential-privacy-noised aggregates) is the production target; the demonstrator trades it for analytical fidelity (real dollar comparisons, composable SQL) on synthetic data where no real merchant can be re-identified.*

This converts a doc/build discrepancy into a stated, defensible design choice.

---

## 13. Build stages

**Stage A — Build the line-item lake.**
`src/lake/build_line_items.py`: read raw `transactions` + `transaction_items` + `stores` (via `observable_guard` if kept), apply generalization (§3.3), set `peer_relationship` per viewer, exclude viewer, write 5 per-viewer `lake_transactions` + `lake_stores` pairs. Emit per-viewer metadata (`segment`, `segment_peer_count`). Makefile target `make lake-items`.
Tests: acceptance pair confirming no PII / no real merchant name (no `banner_code`/`merchant_id`) / no `customer_id`/`customer_token` / no lat-long / no raw `txn_ts` timestamp / no `zone_id` in output; viewer-exclusion holds (viewer's own rows absent from their pair); `peer_relationship` correct relative to viewer.

**Stage B — Wire `query_lake_sql`.**
New tool in `TOOLS_SPECIALIST`. CTE-wrap to viewer's pair (reuse `wrap_tenant_query` pattern). Single-SELECT + aggregating-only enforcement. Robust count injection + k=5 suppression + "N suppressed" notice. `_df_to_payload` output shape.
Tests: scoping (resolves to correct viewer's pair, viewer excluded); raw-row select rejected; nested-query count injection correct (CTE + join case); k=5 drops thin cells; suppression notice surfaced.

**Stage C — Prompt rewrite + routing.**
Rewrite each specialist's lake block. Remove `read_lake_table`/manifest/index language. Add `query_lake_sql` usage + 3 examples:
- "compare your dairy ASP $/unit to peers" → `query_lake_sql` (`SELECT category, AVG(line_total/qty) ... GROUP BY category`)
- "my own per-store revenue" → `query_tenant`
- a payment-mix peer question → `query_lake_sql`, counting **transactions** not lines: `SELECT payment_type, COUNT(DISTINCT lake_txn_id) AS txns ... GROUP BY payment_type` (the injected `_k` line-count is the suppression gate only — §7.3)
Encode §6 peer-availability routing from `segment_peer_count` metadata. Advisor: route-to-specialist-else-cross-segment-labeled.

**Stage D — Verification batch (Haiku, 12 KRG pills + free-form dairy, plus spot-check the other 4 viewers).**
Expected structural unlocks:
- **A1** (UC decline): per-week peer txn counts via SQL on real neighborhood; side-by-side own-vs-peer decline surfaces.
- **P1/P3** (pricing): real dollar peer comparison ("your $3.50 vs peer $3.42").
- **D7** (revenue gap): composable decomposition by category/promo/volume.
- **QSR (TBL) / off-price (TJX) viewers:** pricing → "no peers available"; trends/trade-area → cross-segment labeled.
- **Sanity-check (decision #5):** do the 3 grocers show meaningfully different category prices/volumes? If flat, flag as a data-generation issue (separate from this rebuild), not a lake bug.

**Stage E — Remove superseded components (§11), one commit per logical group, after D is green.** Then close.

---

## 14. Verification gate (what "done" means)

- A1 / P1 / P3 / D7 produce grounded, real-unit, peer-aware answers (not fallback, not index).
- QSR (TBL) / off-price (TJX) routing behaves per §6 (decline on pricing, labeled cross-segment on trends).
- No raw line row is ever returnable; thin cells suppressed; suppression surfaced.
- Existing grounding tests green against `query_lake_sql` results unchanged.
- No pill crashes; no over-claim of precision flagged as a blocker (decision #1 — thin peer numbers are acceptable).
- Strategy-doc §8 note added.
- Commit to v4, push. NO PR, NO merge to main.

---

## 15. Deferred / future (explicitly NOT in 3.5)

- Charts (Wave 4 dashboard). Forward-compat hooks locked in §10.1: single-series kinds build directly from query frames; dual-series overlays reuse the dormant `merge_own_and_peer` join as a plot-time join. No chart code ships in 3.5.
- Tightening privacy: ban MIN/MAX on raw value columns; raise k; reintroduce DP-noised aggregates for a production posture.
- Query-time scoping (if merchant count grows large).
- Standalone Payment-Optimization and Segmentation specialists (currently ride through Advisor).
- Streaming (`call_with_tools_streaming`) wiring.

### Known residuals (Wave 3.5 D.5 — mitigated by prompt discipline, not validator-enforced)

The §1.4 validator checks a number's *value* against a result cell — it does **not**
check the unit suffix, the comparative word, or whether the model ran a query. Two
model-text residuals surfaced in D.5 verification and are mitigated via the shared
answering rules (Rule 4b) but can still recur; the durable fix is a server-side check,
deferred:

- **Magnitude/scale mislabel.** The model can abbreviate a `6,400,000` cell as "$6.4B"
  instead of "$6.4M". Rule 4b tells it to match the cell's order of magnitude. A robust
  fix is a server-side scale sanity check (compare the stated magnitude band to the
  resolved cell) at validation time.
- **Direction mislabel.** A comparative word ("above"/"below") can disagree with the
  sign of own − peer (e.g. a headline says "above peers in meat" while the meat value is
  below). Rule 4b tells it to read direction off the numbers. A robust fix is a check
  that the comparative word adjacent to a claim matches the sign of the gap.

A3 ("which categories are spiking/dropping?") previously ran tenant-only and fell back
on some runs; the cross-category worked example added to `anomaly.md` made it issue the
peer `query_lake_sql` reliably (3/3 in re-verification).