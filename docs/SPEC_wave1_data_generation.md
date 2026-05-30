# SPEC — Wave 1: Data Generation (v4)

**Hand-off spec for Claude Code. Execute closely and independently.**
**Authority:** `DECISIONS.md` is the source of truth. This SPEC operationalizes the data-model decisions (D2–D20) into a build. Where this SPEC and DECISIONS.md disagree, DECISIONS.md wins — pause and flag the conflict.

---

## 0. How to use this SPEC

**Read first:** `DECISIONS.md` in full (especially D11–D20, the data model) and `BASELINE.md` (the current repo state). Do not start coding until both are read.

**Work test-first, stage by stage.** For each stage below: (1) write the stage's tests from the acceptance criteria, (2) implement until green, (3) commit, (4) only then advance. **Never advance with red tests.** A stage is not "done" because the code runs — it is done when its tests pass.

**Pause-and-ask triggers (otherwise run autonomously):**
- A test cannot be made to pass without a design change not covered by DECISIONS.md.
- A DECISIONS.md decision is ambiguous or self-contradictory for the case at hand.
- A realism acceptance test fails and the fix would change a ratified number.
- Anything that would alter the downstream output contract (§5).

Otherwise: proceed without pausing. Commit per stage with a descriptive message.

**Scope:** This is Wave 1 of a multi-wave plan. It produces the **raw generated census** (tenant-level, identity intact) as Parquet, plus a test harness and a data-quality report. Anonymization, the lake, agents, and dashboard are **later waves with their own SPECs** — do not build them here.

---

## 1. Branch & cleanup (Wave 0 prerequisites)

1. Tag the current state: `git tag v3-final && git push --tags`. This is the recoverable working demo.
2. Cut the integration branch: `git checkout -b v4` off `main`. All work happens on short-lived feature branches merged into `v4`. **`main` stays deployable and untouched** until v4 is fully green.
3. **Cleanup (do this before new code, but do NOT pre-delete anything v4 will replace):**
   - Archive stale planning docs to `docs/archive/`.
   - Delete confirmed-dead code per BASELINE §10: `src/db/queries.py` (unused), fix the stale `src/agents/CLAUDE.md` references to `placeholders.py`.
   - Do **not** yet delete the old SQLite lake/seed or the per-merchant generator modules (`kroger.py`, etc.) — they die naturally as their config-driven replacements land. Removing them now just breaks the build.
   - Closing step of the wave: every comment and every `CLAUDE.md` line must be true of the code as it stands.
4. Stand up test scaffolding: `pytest` config, the `-m llm` opt-out marker (carry over from baseline), and a `tests/data_quality/` harness that the acceptance tests (§6) plug into.

**Gate:** branch exists, `v3-final` tagged, dead code removed, `pytest` runs (even with no tests yet).

---

## 2. Storage & engine swap (D3, D4)

- Replace SQLite with **DuckDB reading Parquet**. In-process; no server.
- The generator emits **Parquet files** (committed to the repo while small). No per-viewer lake materialization (that was the SQLite-era 5× blow-up).
- The lake is **single** and built later (Wave 2) by applying viewing-merchant exclusion + peer relabel as **query-time** predicates over one lake. Wave 1 only produces the raw tenant tables.
- S3 is **out of scope** for Wave 1 (deferred per D3); commit Parquet locally.

**Tests:** round-trip a small DataFrame → Parquet → DuckDB query returns identical rows; DuckDB reads a multi-file Parquet dataset.

**Gate:** storage layer reads/writes Parquet via DuckDB with passing tests.

---

## 3. Config-driven structure (D12)

The engine is generic; all merchant/segment/metro specifics live in config. **No per-merchant code modules** (supersedes baseline's `kroger.py`/`acme.py`/etc.).

Suggested layout:
```
src/generate/
  config/
    global.yaml          # seed=42, window dates (90d), volume targets (D5)
    metro.yaml           # 8 zones: profile, residential weight, centroid (D13.1)
    segments/
      grocery.yaml        # archetype: tiers, missions, daypart, β, payment physics, price-sensitivity
      qsr.yaml
      off_price.yaml
    merchants/
      acme.yaml kroger.yaml winn_dixie.yaml taco_bell.yaml tj_maxx.yaml
  engine/                 # one module per layer, segment-agnostic
    geography.py population.py customers.py trips.py
    baskets.py payment.py pricing.py events.py run_all.py
  catalogs/               # base catalog data + real price anchors
src/storage/duckdb_io.py
tests/
```

**Config schema (minimum fields):**
- *Segment archetype:* intensity tiers (D14.3), missions + category distributions (D17.1), affinity matrix (D17.2), daypart curve (D15.4), distance-decay β (D13.4), payment baselines (D18), category price-sensitivity (D19.2), promo type menu + lift-elasticity (D20.1), catalog model = fixed.
- *Merchant config:* `name`, `segment` (ref), `positioning_tier`, `store_count`, `zone_placement_bias`, `category_price_roles`, `private_label_share`, `per_sku_competitive_index`, `promo_timing_profile`, `assortment_breadth`, `payment_mix_overrides` (optional).
- *Metro/zone config:* per-zone affluence, residential weight, density, household skew, age skew, centroid.

**Config-validation tests (D12 invariants):** residential weights sum to 1.0; every merchant `segment` resolves to a defined archetype; every `zone_placement_bias` references valid zones; store counts and volume targets within the D5 band. A config violating an invariant fails before generation runs.

**Forward consideration:** adding a 6th merchant or a new segment must be a config addition only — include a test that instantiates a dummy extra QSR merchant from config and confirms the engine runs unchanged.

**Gate:** configs for all 5 merchants + 3 segments + metro load and pass validation.

---

## 4. Generation pipeline — the 8 layers (build strictly top-down)

Each layer reads the layers above. Implement and test in order; do not start a layer until the one above is green. Rules are in DECISIONS.md as cited — do not re-derive, implement.

| Stage | Layer | Implements | Key rules |
|---|---|---|---|
| 4.1 | Geography | place 24 stores in 8 zones; gravity geometry | D13: zones, placement matrix, centroids+jitter |
| 4.2 | Population | ~100k cards; intensity tiers; participation/overlap | D14: tier mixtures, participation matrix (~32% multi, 6% all-three) |
| 4.3 | Customer state | home zone, affluence, loyalty, preference vector, single card | D16: loyalty table, preference vector, one-token-but-extensible card |
| 4.4 | Trips | place each trip in time; resolve store via gravity×loyalty | D15 (temporal), D15b (store resolution) |
| 4.5 | Baskets | mission-draw ∩ preference ∩ assortment; affinity boost; quantities | D17: mission model, affinity matrix, staples, basket-size dist |
| 4.6 | Payment | entry mode, wallet-at-tap, connectivity (emergent) | D18 |
| 4.7 | Catalog & price | assortment per merchant; anchored price × strategy × promo × drift | D19: anchors, per-merchant strategy, KVI spread |
| 4.8 | Events | promos (with demand lift) + anomalies A1–A3 (multiplier hook) | D20 |

**Reproducibility:** one seeded RNG (seed=42) threaded through; no direct global RNG calls. Test: two runs produce byte-identical Parquet.

**Per-stage tests:** each stage gets unit tests for its own rules (e.g., 4.1: store-zone placement matches matrix; 4.4: weekend/weekday ratio in band; 4.5: basket-size distribution shape). The cross-cutting realism tests are §6.

**Gate per stage:** stage tests green + committed before the next stage.

---

## 5. Output / downstream contract (designed for the lake & agents)

Wave 1 outputs raw tenant Parquet tables. **This schema is a contract** — the anonymization/lake wave (Wave 2) and ultimately the agents depend on it carrying every insight-bearing dimension. Do not drop these.

Tables (raw, identity intact — anonymization happens in Wave 2):
- `merchants`, `zones`, `stores` (incl. `zone`, `lat/long`, `neighborhood`, `metro_region`)
- `customers` (incl. `home_zone`, loyalty/affinity attrs, card identity) — **identity present here; stripped in Wave 2**
- `products` (per merchant; incl. `category`, `subcategory`, **`canonical_id`** so the same product maps across merchants for cross-merchant comparison, `private_label` flag, `base_price`)
- `transactions` (incl. `customer_token`, `store_id`, `txn_ts`, payment attrs: `tender`, `network`, `entry_mode`, `wallet_provider`, `connectivity`)
- `transaction_items` (incl. `sku`, `canonical_id`, `qty`, `unit_price`, `discount`, `promo_id`)
- `promotions`
- `anomalies_groundtruth` — **the answer key for A1–A3 (eval/test only; must NOT flow into the lake or be visible to agents)**

**Insight-bearing dimensions that MUST survive to the lake (forward consideration):** category/subcategory, `canonical_id` (cross-merchant product matching), peer-banner mapping, zone/zip3, time bucket, payment attributes, and the cross-merchant linkage token. The lake will anonymize these; the data must contain them.

**Cross-merchant + small-cell readiness:** the engineered ~30% overlap (esp. the ~6% all-three) must be present and large enough per zone that the lake's k-anonymity (k=5) won't fully suppress the most interesting cross-merchant cells. §6 includes a test that flags if interesting cells are too thin — this is the early-warning for the lake design, surfaced now rather than post-hoc.

**Gate:** all tables emit as Parquet with the contract schema; a schema test asserts required columns present.

---

## 6. Acceptance tests — realism invariants (the wave's real definition of done)

These are distribution-level tests derived from the ratified numbers. They run against the generated dataset and gate the wave. Bands, not point values.

| # | Invariant | Acceptance | Source |
|---|---|---|---|
| T1 | Total volume | 1.5–1.8M txns; per-segment within ±10% of D5 targets | D5 |
| T2 | Grocery AOV | $48–62 blended; QSR $9–12; off-price $30–50 | D17.4, D19.6 |
| T3 | Store AUV | grocery ~$14–18M/yr equivalent | D5, D17.4 |
| T4 | Day-of-week | grocery weekend/weekday ratio 1.2–1.35; Sun ≥ Sat ≥ Fri | D15.2 |
| T5 | Daypart | Taco Bell late-night (9pm+) share 17–21% | D15.4 |
| T6 | Pay-cycle | early-month (1–10) + mid-month (15–17) lift visible, strongest for value zones | D15.3 |
| T7 | Population shape | ~100k cards; heavy tail; tier mix per segment in band | D14.3 |
| T8 | Cross-merchant overlap | 25–30% multi-merchant; ~6% all-three; per-segment actives reconcile | D14.4 |
| T9 | Loyalty concentration | pop-weighted primary-banner share 70–78% | D16.1 |
| T10 | Repeat purchase | customers' staple SKUs recur above chance (loyalty discoverable) | D16.2 |
| T11 | Affinity discoverability | **lift analysis surfaces the designed complement pairs AND emergent mission pairs** (lift > threshold); not derivable from category marginals alone | D17.1–2 |
| T12 | Basket heavy tail | top 20% of baskets ≈ 45–55% of unit volume | D17.4 |
| T13 | Payment mix | contactless 48–55% blended; mobile wallet 16–20%; grocery debit-leaning (emergent); entry mode varies by store clientele | D18 |
| T14 | Pricing variation | **no single banner cheapest on >70% of comparable SKUs**; private-label gap ~25%+; KVI cross-banner spread tight (<~10%); specialty spread wider | D19.2–3 |
| T15 | Promo behavior | 25–35% grocery units on promo; promoted SKUs show demand lift scaled to depth | D20.1 |
| T16 | Anomalies | A1–A3 each statistically detectable in the data AND localized (peer stores/zones unaffected); ground-truth recorded in `anomalies_groundtruth` | D20.2 |
| T17 | Small-cell/lake readiness | interesting cross-merchant cells (e.g., all-three shoppers per zone) exist at counts that survive k=5 — flag thin cells | §5, D8/lake |
| T18 | Reproducibility | fixed seed → byte-identical Parquet across two runs | D11 |

**Data-quality report:** generate a human-readable report (markdown/HTML) summarizing T1–T18 with the actual numbers — this is the artifact an exec/reviewer reads to trust the data. Produce it as the final build step.

**Gate:** all T1–T18 green + data-quality report generated.

---

## 7. Definition of done (Wave 1)

- `v4` branch; `v3-final` tagged; cleanup done; comments/CLAUDE.md accurate.
- DuckDB/Parquet storage; single-lake-ready raw tables (no per-viewer materialization).
- Config-driven engine; all configs validate; dummy-extra-merchant test passes.
- All 8 layers implemented, each stage's tests green.
- Output Parquet matches the §5 contract; schema test passes.
- All §6 acceptance tests green; data-quality report generated.
- Generation runs end-to-end from a clean checkout in one command (~target ≤10 min).
- Merged to `v4` via PR.

**Out of scope (later waves):** anonymization engine, the lake/query-time scoping, dashboard, agents, ask-AI. Those have their own SPECs.

---

*This SPEC covers data generation only. The next planning step is the anonymization & lake SPEC (Wave 2), which will be designed explicitly around the agents' insight needs — preserving the insight-bearing granularity this wave produces (§5) through anonymization without crushing the cross-merchant cells (T17).*
