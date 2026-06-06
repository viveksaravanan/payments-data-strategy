# SPEC — Wave 2: Anonymization & Lake (v4)

**Hand-off spec for Claude Code. Execute closely and independently.**
**Authority:** `DECISIONS.md` is the source of truth — specifically D21 (anonymization model), D22 (dual-path structure), D23 (lake tables, enrichment, observable-data rule). Where this SPEC and DECISIONS.md disagree, DECISIONS.md wins — pause and flag.

**Prerequisite — SATISFIED (Wave 1 closed at full scale).** Wave 1 is committed on `v4` (not merged — `main` stays frozen on `v3-final` until all of v4 is done) and its full-scale DQ report is green. **T17 is resolved, not pending:** at full 100k scale, all 8 zones clear the privacy threshold — smallest zone (Cabarrus Edge) holds **483 all-three cards**, clearing k≥50 by ~10× (and k=5 by ~96×). **The designed per-zone × all-three grain locks as-is — no coarsening required.** There is even headroom for finer cuts (per-zone × all-three × time-bucket) if a table wants them.

The k-guard ladder (D21.4) below is therefore a **safety net, not an expected step** — it should rarely fire on the cohort/zone-level tables. The one table where it *may* fire is `lake_category_metrics` at its finest grain (subcategory × zone × week), since that's the most granular cut; the ladder handles that locally.

---

## 0. How to use this SPEC

**Read first:** D21, D22, D23 in `DECISIONS.md`; the Wave 1 `SPEC` §5 (the `data/raw/` contract this wave consumes); the Wave 1 DQ report (real cell counts).

**Work test-first, stage by stage.** Same discipline as Wave 1: write the stage's tests, implement to green, commit, advance. Never advance on red.

**Pause-and-ask triggers (else run autonomously):**
- A k-grain can't reach k≥50 even after the coarsening ladder (D21.4) — flag; don't lower k.
- An agent question in scope (D23.7) can't be answered by the table set as designed — flag a possible table-grain gap.
- A DECISIONS clause is ambiguous/contradictory for the case at hand.
- Anything that would weaken a privacy guarantee (k, isolation, observable-data rule).

**Scope (this wave):** the anonymization pipeline, the five lake aggregate tables with enrichment, query-time dual-path scoping (viewer exclusion + relationship relabel), and the tenant-isolation guards. **Out of scope:** agents, dashboard, ask-AI, l-diversity, differential privacy (D21.3 — deferred; no DP seam shipped, the aggregate columns are the future injection point, see §4.6).

**Inputs:** `data/raw/*.parquet` (Wave 1 tenant census, 1.66M txns / 10.76M line items). **Never read `data/eval/`** (anomaly answer key — forbidden by construction).

**`data/raw/` is gitignored — it exists locally from the Wave 1 full-scale run, NOT in the repo.** If it's absent (fresh checkout or different machine), regenerate it first via `make seed` (~56 min, deterministic at seed=42) before building the lake. Do not assume the Parquet is checked in.

**Privacy threshold = k≥50 (D21's bar, the strategy-doc §8 figure).** Wave 1's T17 report quotes headroom over k=5, but that's just the lower reference floor — the binding cell (483) clears the real k≥50 bar by ~10×. **Wave 2 enforces k≥50 everywhere**, not k=5. Wherever this SPEC or the tests say "k", it means **50**.

---

## 1. The observable-data invariant (build this guard FIRST — D23.1)

Before any lake code, stand up the invariant that governs the whole wave.

- The lake builder may read **only** observable columns: `transactions`, `transaction_items`, and **store location** from `stores`.
- **Forbidden source columns** (generation scaffolding): `customer.segment`, `customer.preference_vector`, `customer.loyalty_type`, wallet-enrollment flag, `zone.affluence`/`zone.density`/`zone.age` (any zone *profile*), and any other latent/planted field.
- **Implement as an enforced test:** a build-time assertion + CI test that fails if the lake build touches any forbidden column. The cleanest mechanism: load the lake's source frames through a whitelisted column accessor and assert the forbidden set is never referenced. This test is the thing that mechanically prevents scaffolding leaks.

**Gate:** the invariant test exists and passes against a no-op build; attempting to read a forbidden column fails the test.

---

## 2. Tenant-isolation guards (D22.4)

Re-implement over DuckDB/Parquet the two guards that make the dual path real:
- **Predicate check** — every tenant-surface query is verified scoped to the viewer's own merchant; a query referencing another merchant's tenant data is rejected.
- **Query remap/wrap** — wrap the agent's tenant query so it can only ever resolve to the viewer's own merchant, regardless of what was written.
- The lake builder is physically constrained to read `data/raw/*.parquet` only; assert it cannot glob `data/eval/`.
- **Disambiguate two distinct boundaries (do not conflate in one test):** (a) the lake builder may link card tokens *across merchants* **inside the §4.5 cohort step** — this is ALLOWED (it's the trusted boundary); (b) the lake builder may NEVER read `data/eval/` ground-truth — FORBIDDEN. The isolation test must permit (a) while rejecting (b); an over-broad test that bans all cross-boundary access will false-positive on the legitimate cohort linkage.

**Tests:** a tenant query scoped to the viewer returns rows; a tenant query naming another merchant is rejected/remapped to empty; the lake builder rejects any `data/eval/` path.

**Gate:** isolation guard tests green. Peers are reachable ONLY through the lake (§3+).

---

## 3. Zones: observable grouping + behavioral character (D23.2)

- **Zone grouping key** = stores grouped by observable location (lat/long → geographic grouping / ZIP3). Derive a `zone` mapping for every store from coordinates; do **not** read the planted zone definition's profile.
- **Zone character** (if/when needed for reasoning) = derived from observable behavior in the zone (avg basket, premium-vs-value banner volume mix, price posture, promo sensitivity). No external census enrichment. No reading `zone.affluence`.
- **Validation test (D23.1 free check):** behaviorally-derived zone character correlates with the planted profile (without reading it). Correlation present → generation coherent; absent → flag.

**Gate:** zone mapping derived from store location; behavioral-character derivation tested; correlation check runs.

---

## 4. The five lake tables (D23.3) — build each test-first

Common to all (D23.3/.4): `peer_relationship` is **resolved at query time, NOT stored** (a peer's relationship depends on the viewer) — store `merchant` and let §5 resolve relationship per-viewer. Every table stores `txn_count` (k guard) and enrichment fields (§4.6). Use **consistent shared keys**: aligned `zone`, aligned time grain where present, one shared `category` taxonomy.

**k-guard ladder (apply to every table, D21.4):** compute at finest grain → if a cell `txn_count < 50`, coarsen one step (subcategory→category, week→month) → repeat → if still < 50, **suppress** the cell. Record suppressed-cell stats in the build report.

### 4.1 `lake_category_metrics` (the workhorse; T17-sensitive)
- Grain: merchant × category (× subcategory where ≥k) × zone × week
- Source: items+transactions+stores. Columns per D23.3.1: price_index, revenue_index, units_index, basket_penetration_share, promo_active_share, wow_delta, txn_count.
- Tests: cells all ≥ k=50 after laddering; price_index = cell mean ÷ metro-category mean (verify on a hand-computed fixture); subcategory present only where ≥k.

### 4.2 `lake_payment_mix`
- Grain: merchant × payment-attr × zone (× month). Columns per D23.3.2.
- Tests: shares sum to 1.0 per cell; cells ≥ k; mobile-wallet provider split present.

### 4.3 `lake_segment_mix` (BEHAVIORAL — D23.3.3)
- **Compute behavioral segments from observable features only** (frequency, basket/spend band, recency/regularity, weekday/weekend skew, promo share, PL share, price-index-paid, payment behavior, daypart). Cluster/bucket → segments. **Assert the build never reads `customer.segment`** (§1).
- Grain: merchant × derived_behavioral_segment × zone. Columns: segment share, per-segment basket/frequency bands, txn_count.
- Tests: segments derived without forbidden columns (§1 invariant covers this); shares sum to 1.0 per cell; cells ≥ k; (validation) derived segments correlate with planted segments without reading them.

### 4.4 `lake_trade_area`
- Grain: zone × category (× merchant). Columns per D23.3.4 (density, presence/mix, zone volume index, share_of_zone).
- Tests: share_of_zone sums to ~1.0 across merchants per zone×category; cells ≥ k (zone-level → comfortable).

### 4.5 `lake_cross_merchant_cohorts` (headline)
- Built INSIDE the trusted boundary: token linkage across merchants is allowed **only** in this build step; **publish only aggregated counts/bands** — assert no customer-level cross-merchant row is ever written to the lake output.
- Grain: zone × merchant-combination. Columns: cohort_size, **median/banded combined-spend (NOT raw mean — D24.2 concentration risk)**, cross-shop frequency band.
- Tests: cohort cells ≥ k=50 (e.g. all-three/zone ≈ 750); output contains no per-customer rows (schema assertion); linkage logic confined to this builder; **spend published as median/band, never raw mean.**

### 4.6 Enrichment + DP-readiness (D23.4, D21.3)
- Compute indices/deltas/shares at build time (above). Verify each enrichment on a small hand-computed fixture so the math is trusted.
- **DP deferred — no seam shipped (resolved, D21.3/D24.3).** Differential privacy is a future wave. Do **not** build a `publish()` seam or AST-enforce it now — building and testing enforcement scaffolding around an identity no-op is real complexity for zero current behavior (and an unenforced seam would be theater, the flaw of the old name-based suppression). Instead: the **published aggregates in these five tables ARE the future DP injection point** — when DP is added later, Laplace noise is applied to the aggregate values at build time, no schema change needed. That's recorded in DECISIONS (D24.3); nothing to implement this wave beyond keeping aggregates as clean numeric columns.

**Gate per table:** that table's tests green + committed before the next.

---

## 5. Query-time dual-path scoping (D22.1–.3) + grain metadata

- **Single lake**, no per-viewer materialization. Implement a query-time wrapper that, given a viewer merchant M, over any lake table: (a) `WHERE merchant != M` (viewer exclusion), (b) resolves `peer_relationship` per row relative to M (same segment as M → `segment_peer`; else `cross_segment`), (c) **strips the real peer merchant identity** — the agent surface receives ONLY the relabeled token, never the real peer name (D24.1). Real `merchant` may exist *inside* the lake (needed to resolve relationship); it must not *exit* to the viewer.
- **Small-N honesty (D24.1):** with 5 merchants this is *pseudonymization*, not true anonymity — a viewer can often infer which competitor a `segment_peer` benchmark is by elimination. The aggregate cell stays k≥50 (no *consumer* exposed; the residual is *which competitor*, a business-confidentiality matter). State this in the report; do not imply peers are fully anonymous.
- **Own/peer split (D22.1):** the agent's own-merchant data comes from the tenant surface (§2, full grain); peers come from the lake (this wrapper). One helper per surface; never mix.
- **Grain metadata (D23.7):** publish a small machine-readable manifest per table — finest grain, dimensions, and **what it does NOT carry** (e.g., "no peer SKU", "weekly not daily") — so Wave 3 agents know their limits and can decline gracefully.

**Tests:** for viewer=Kroger, lake excludes Kroger and tags other grocers `segment_peer`, Taco Bell/TJ Maxx `cross_segment`; the dairy worked example (D23.5) returns the expected shape; grain manifest present and accurate.

**Gate:** dual-path scoping + relationship relabel + grain manifest tested green.

---

## 6. Acceptance tests — the wave's definition of done

Run against the built lake over the real Wave 1 data.

| # | Invariant | Acceptance |
|---|---|---|
| L1 | Observable-data invariant | build reads no forbidden column (§1); CI enforced |
| L2 | k≥50 everywhere | every published cell in every table has txn_count ≥ **50** (D21 bar, not k=5); suppressed-cell report generated |
| L3 | No raw identity in lake | no customer token, no SKU, no single-store, no cross-merchant per-customer row in any lake output |
| L4 | Tenant isolation | cross-merchant tenant query rejected/empty; lake can't read `data/eval/` |
| L5 | Viewer exclusion + relabel | per viewer, own merchant absent; peers correctly tagged segment_peer/cross_segment |
| L5b | **Identity stripped (D24.1)** | real peer merchant name NEVER reaches the agent surface; only relabeled token exits |
| L6 | Enrichment correctness | indices/deltas/shares match hand-computed fixtures |
| L7 | Dairy worked example (D23.5) | returns segment-peer dairy index at category/subcategory; own side reaches SKU |
| L8 | Own-store vs zone-peers (D23.6) | a multi-store grocer benchmarks each store vs peers in its store's zone |
| L9 | Behavioral segments | derived without planted labels; correlate with planted (validation) |
| L10 | Cohorts | cross-merchant cohorts ≥ k; counts only; **spend as median/band not mean (D24.2)** |
| L11 | Grain metadata | manifest present per table; declares finest grain + exclusions |
| L12 | DP deferred cleanly | no `publish` seam shipped (D21.3); aggregates are clean numeric columns = the future DP injection point (D24.3); build report states DP deferred |

**Build report:** emit a human-readable report — per table: cell counts, suppressed-cell counts/%, k-distribution, and which grains had to coarsen. This is the artifact that proves the privacy posture (k≥50 structural) and shows how much data suppression cost (the T17 question, now answered on real data). **The report MUST also state the §8 gap honestly (D24.3):** list what's applied (tokenization, generalization, structural k≥50, suppression, viewer exclusion+relabel) AND what's deferred-with-reason (l-diversity, differential privacy) — plus the D24.1 small-N pseudonymity caveat. A §8-framed report that silently omits deferred techniques is a worse outcome than one that names them.

**Gate:** L1–L12 green + build report generated.

---

## 7. Final audit + close

- Re-read every touched `CLAUDE.md`/comment; ensure true of the v4 code (carry-over closing rule).
- Confirm the lake builds end-to-end from `data/raw/` in one command.
- **Commit to `v4` and push (`git push origin v4`). NO pull request, NO merge to `main`.** `main` stays frozen on `v3-final` until all of v4 (Waves 1–4) is complete. Wave 2 accumulates on `v4` alongside Wave 1, same as Wave 1 closed. Record the DoD checklist + build-report highlights in the close commit message.

**Critical files (indicative):** `src/lake/build.py` (the five-table builder + k-ladder + enrichment), `src/lake/observable_guard.py` (§1 invariant), `src/lake/scope.py` (query-time viewer exclusion + relationship relabel), `src/lake/isolation.py` (tenant guards), `src/lake/manifest.py` (grain metadata), `tests/lake/test_L01..L12.py`, `scripts/build_lake_report.py`. **Quarantined `src/db/seed.py` is replaced here** — wire the lake build as the materialization path and remove the deprecation stub.

---

## 8. Scope discipline

Wave 2 produces the anonymized lake + dual-path access + isolation guards. **No agents, no dashboard, no ask-AI. No l-diversity, no DP (deferred — aggregate columns are the future injection point, no seam shipped).** Those are later waves. The lake's grain manifest and enriched comparatives are the contract Wave 3 (agents) consumes.

*Next planning step after this: Wave 3 agent design — the agent contract over (tenant surface + lake), reasoning over enriched comparatives + grain metadata, with the D23.7 decompose/decline behavior and the D20.3 business-anomaly-only scope.*
