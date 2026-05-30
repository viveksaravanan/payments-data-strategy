# DECISIONS — Next-Phase Architecture & Data

**Date:** 2026-05-30
**Status:** Aligned in planning. Precedes `SPEC.md`.
**Scope:** Decisions governing the next iteration of the payments-data-strategy repo, made against `docs/BASELINE.md` (the "before" snapshot). Where a decision changes something the baseline describes, it is noted as **Supersedes baseline**.

## Purpose & bar

This iteration is a **real working prototype** that must withstand scrutiny from a Verifone executive. The standard is tight, concrete, and defensible — every number on screen must trace to data, and claims must hold up to a deeper dive. Handwavy demos do not pass. The code must be thorough and test-driven so the system survives inspection, not just a walkthrough.

The guiding reference for capabilities and framing remains the Core Data Strategy & Solutions document (cross-merchant intelligence from device to cloud).

---

## D1 — Data model: census at served merchants, not a panel

**Decision.** Model the data as a **census of card-present transactions at the merchants Verifone serves**, not a sampled consumer panel.

**Rationale.** Verifone's terminals capture every card transaction at a served merchant. If Verifone serves all of a banner's lanes, it sees *all* of that banner's card volume — a census, not a sample. A "panel" framing (a recruited subset of shoppers projected to market) was considered and **rejected**: it misrepresents the actual data position and would understate per-store volume in a way that contradicts the premise.

**The only legitimate "sample" caveats — and they are narrow:**
1. **Card-present only.** Verifone sees a card at a terminal, not a person's whole wallet — cash, other cards, and spend at non-Verifone merchants are unobserved. A customer's *complete* behavior is partially observed; their card-present behavior at served merchants is complete.
2. **Served-merchant coverage.** Cross-merchant comparison covers the merchants Verifone serves, a non-random slice of the market (competitors run other terminal vendors). Census-accurate for the client set; silent about non-clients.

**Implications.**
- Per-store volume must look **real** (hundreds–thousands/day), not toy-scale. The baseline's ~21 txns/store/day is a demo artifact, not a defensible figure.
- Absolute market-size dollar claims require an explicit, stated coverage assumption. Relative claims (shares, ratios, trends, affinities) need no adjustment and are fully supported by the census.
- A documented **minimum-cell threshold** governs the finest grain at which a number is reported, for statistical reliability (parallel in spirit to the existing k=5 privacy suppression, but a separate concern).

---

## D2 — Merchants & geography: real banner names, explicitly fictional metro

**Decision.** Use **Kroger, Acme, Winn-Dixie, Taco Bell, and TJ Maxx** as banner names, placed in a **fictional metro modeled on Charlotte**, with a **visible disclaimer** that the metro and footprint are fictional and used only for illustration.

**Rationale.** The names ground the discussion and convey recognizable positioning (mainstream-national / value / regional grocery; QSR; off-price retail). The fictional-metro disclaimer is **required** because the real-world geography is impossible and a grocery-savvy exec would catch it instantly:
- Kroger's banner exited North Carolina (Raleigh-Durham, 2020); its only NC presence is Harris Teeter (Kroger-owned, the Charlotte market-share leader). A "Kroger store in Charlotte" is not real.
- Acme Markets is a Northeast / Mid-Atlantic banner (PA, NJ, DE, NY, CT, MD) — no Charlotte presence.
- Winn-Dixie is a Deep South banner (AL, FL, GA, LA, MS), headquartered in Jacksonville, and is being shrunk through divestitures — not a Carolinas chain.
- The three grocers do not coexist in any real metro (three different regions), yet the cross-merchant thesis requires them to share one market.

**Implications.**
- Keep the behavioral positioning the generator already encodes (e.g. upscale vs. value vs. mainstream grocery via neighborhood bias and basket-size multipliers).
- The disclaimer is a product requirement, surfaced in the UI and the report, not a footnote.
- Add the brand-specific texture currently missing: **Taco Bell late-night daypart** (the real 10pm–2am surge) and **TJ Maxx rotating/treasure-hunt assortment** rather than a fixed catalog.

---

## D3 — Storage & query engine: DuckDB over Parquet (replaces SQLite)

**Decision.** Replace SQLite with **DuckDB reading Parquet** as the storage and query layer.
**Supersedes baseline.** §5 (SQLite single-file `payments.db`), §10.7 (2.7GB DB / ~2-min cold start).

**Rationale.** The constraint was never *where* bytes live — it was the engine. SQLite is row-oriented and was materializing the lake five times (per viewer), which is what drove the 2.7GB size at only 235k transactions. DuckDB is an in-process **OLAP** engine (same "no server" simplicity as SQLite, drops into tests/CI/HF Spaces unchanged) built for group-bys, aggregations, and peer comparisons, and it reads Parquet directly. Parquet is columnar and compressed (~5–10× smaller than equivalent SQLite tables), and DuckDB handles tens of millions of rows on a laptop without strain. This is also **closer to a production architecture** (columnar lake + analytical engine), which strengthens the exec narrative.

**Implications.**
- Generator emits **Parquet** (committed to the repo while it stays small; deploy never regenerates, so the cold-start regen path is removed).
- **S3 is optional and deferred.** DuckDB's `httpfs` extension can read Parquet from S3 (`SELECT ... FROM 's3://.../lake/*.parquet'`), which mirrors a production staging layer and is the one-line upgrade for the production story. Not a prerequisite; adds AWS credentials + a network hop. Commit Parquet locally for now; reach for S3 when the production narrative is wanted.
- Everything in the data and agent phases is written against DuckDB/Parquet from the start.

---

## D4 — Single lake with query-time viewer scoping (removes the 5× blow-up)

**Decision.** Materialize **one** lake. Apply viewing-merchant exclusion and peer relabeling **at query time** via the existing CTE-wrap mechanism, rather than pre-materializing a separate lake per viewer.
**Supersedes baseline.** §4.2 / §5.5 (ten per-viewer `lake_*_<M>` physical tables), §10.5 (query-time lake machinery noted as currently vestigial — it gets re-activated here).

**Rationale.** The per-viewer materialization was the ~4–5× row multiplier behind the size problem. With DuckDB, applying exclusion + peer relabel as query-time predicates is cheap, and it removes the multiplier at the root. This is a **required line item** in the data phase, not optional cleanup — without it, the larger footprint in D5 makes the size problem worse, not better.

**Implications.**
- Tenant isolation still rests on the two existing guards (merchant-predicate check + CTE remap to the viewer scope); these remain load-bearing and must stay correct.
- The vestigial UDF registration and legacy-table rejection lists (baseline §10.5) get revisited as part of this change.

---

## D5 — Footprint & volume (the healthy sample)

**Decision.** Single fictional metro, 90-day window, the following footprint:

| Segment | Banners | Stores/banner | Stores | Realistic txns/store/day |
|---|---|---|---|---|
| Grocery | 3 (Kroger / Acme / Winn-Dixie) | 5 | 15 | ~800 |
| QSR | 1 (Taco Bell) | — | 9 | ~450 |
| Off-price retail | 1 (TJ Maxx) | — | 5 | ~500 |
| **Total** | | | **24** | |

**Resulting volume (target band):**
- Grocery: 15 × 800 × 90 ≈ **1.08M**
- QSR: 9 × 450 × 90 ≈ **365k**
- Retail: 5 × 500 × 90 ≈ **225k**
- **Total ≈ 1.67M transactions**, ~10M line items, **~100k distinct cards**.

**Rationale.**
- **Multiple stores per banner is what produces insight.** A single Taco Bell or TJ Maxx supports no cross-store comparison, no anomaly *localization*, and no trade-area analysis. 9 QSR / 5 retail / 5-per-grocer lets the system say "this store underperforms the other eight in its trade area" and localize anomalies to neighborhoods rather than whole banners.
- **Per-store volume is defensible.** ~800 grocery/day ≈ ~$16M/yr at a ~$55 basket (AOV refined in D17.4; ~$55 is within the $40–60 POS grocery-transaction band); ~450 QSR/day ≈ ~$1.5M/yr at ~$9–12; ~500 retail/day ≈ ~$5.5M/yr at ~$30–50 — each in a real range for its format. (Exact AUV emerges from basket-size × price and is test-enforced per D17.4.)
- **90-day window** is retained now that storage no longer forces it shorter — gives ~3 pay cycles and stronger trend/seasonality stories.
- **In Parquet this is a couple hundred MB** and sub-second on DuckDB — roughly 7× today's data in a fraction of the footprint.

**Hold here.** More rows beyond ~1.67M buys seed time, not insight. Generation is a one-time ~10-minute build; the stories come from store count, engineered overlap, and emergent structure — not raw volume.

---

## D6 — Engineered cross-merchant overlap

**Decision.** Deliberately construct **~25–30% of the ~100k cards as multi-merchant shoppers** (grocery+QSR, grocery+retail, all-three), so the same tokenized cards recur across banners.

**Rationale.** Under a census model, cross-merchant overlap must be *engineered*, not left to coincidental token collisions. The cross-merchant story is the crown jewel of the strategy doc; it needs a real, discoverable overlapping population to be credible.

---

## D7 — Data realism upgrades (so agents can find real, unique insight)

**Decision.** Address the structural realism gaps that currently cap what the agents can discover. Ranked by impact:

1. **Emergent affinities, not hardcoded rules.** Generate baskets from a latent co-occurrence structure (affinity matrix / simple embedding) so associations *emerge* and are discoverable via lift, rather than only returning the handful of registered closures.
   - *Supersedes baseline* §3.4(i) / §3.7 (rule-based affinity, no emergent structure).
2. **Within-customer preference persistence.** Give each card a persistent preference vector / favorite-SKU memory so repeat purchase is real and segmentation/loyalty stories hold.
   - *Supersedes baseline* §3.7 (SKU choice re-sampled fresh each trip).
3. **Real geographic store choice.** Replace the per-customer "closest" shuffle with lat/long distance + a gravity model, so trade-area analysis is genuinely geographic.
   - *Supersedes baseline* §3.4(d) / §10.2 (store "closest" is a per-customer shuffle).
4. **Price variation.** Introduce price dispersion across stores and over time, with promos reflected in unit price.
   - *Supersedes baseline* §3.4(vi) / §10.2 (`# v2.5: no noise`, frozen prices).
5. **Payment-mix correction.** Grocery should skew **debit** (currently 65/35 credit/debit — likely backwards for grocery).
   - *Supersedes baseline* §3.5.
6. **Brand texture.** Taco Bell late-night daypart; TJ Maxx rotating assortment (see D2).

---

## D8 — Agent response unification: query result is the single source of truth

**Decision.** One architecture: **the query result (DataFrame) is the only source of truth; the model authors *intent*, never *values*.**
**Supersedes baseline.** §6.7, §10.1 (chart values re-written by the model; dashboard per-qid pattern chart runs independent SQL and can disagree with the agent's prose).

**One agent turn produces:**
- **SQL** (model-authored) → executed by code into a **DataFrame** (the truth).
- A **chart intent** (model-authored): `{pattern, x_col, y_cols, series_col, value_format}` — column references only, no numbers. The model picks the pattern that fits the question + data; `chart_patterns.py` becomes the **menu it selects from**, fed by the DataFrame.
- The **chart**, built deterministically by code from the DataFrame using that mapping. Values come from the data, never the model.
- **Headline metrics**, computed in code from the DataFrame.
- **Prose** (model-authored), constrained to reference only numbers present in the result. A validation pass parses numeric claims and asserts each traces to the DataFrame within tolerance; untraceable numbers are flagged/regenerated.
- **Caveats** where *factual* ones (k-anon suppression fired, peer-set size, date window) are injected by code from what actually happened.

**Retire the parallel chart path.** The per-qid independent `data.py` chart for the agent surface is removed; the qid "pattern" becomes a *hint* to the agent, not a separate query. Prose, table, and chart provably agree because all derive from the same rows. "Show me where that number comes from" always resolves to a row in the table and a line in the SQL.

---

## D9 — "Ask AI about this chart" (depends on D8)

**Decision.** Every dashboard chart gets an **"Ask AI about this"** affordance. Clicking passes a structured **chart-context object** — `{chart_id, title, metric, dimension, filter_state, merchant_id, source_data/source_query, plain_language_summary}` — to the agent, which runs the **same unified workflow** (D8): grounds on the context, decides the deeper question worth answering, pulls its own data for the deeper cut, picks the matching pattern, returns a unified prose+viz+caveats response that references what the user was looking at.

**Sequencing constraint.** This **depends on D8 landing first.** If dashboard charts still run independent SQL, "ask about this chart" inherits the divergence. Once everything is "data → intent → render," dashboard and agent surfaces speak the same data language and reconcile by construction.

---

## D10 — Defensibility is encoded as tests, not asserted

**Decision.** Each thread ships with tests that *are* the guarantee:
- **Data:** distribution-level invariants (per-segment AOV bands, payment-mix shares within tolerance, daypart shape, planted anomalies statistically detectable, affinities discoverable via lift without knowing the rules) + a generated **data-quality report** an exec can read.
- **Agent unification:** golden tests on the existing cassette infra — for a fixed question + fixed data, assert every prose number ∈ the DataFrame, chart values == DataFrame values, and code-injected caveats present when suppression fires.
- **Ask-about-chart:** chart-context object round-trips; the agent's response is provably grounded in the passed context (same metric/dimension/filters).

---

# DATA MODEL

The decisions below (D11+) specify how transactions are generated. They are ratified **layer by layer in causal order**; each layer constrains the one below it, so approving in order means upstream choices are never re-litigated. Ratification status is tracked in the build-order table under D11.

## D11 — Generation thesis: latent-first, observable-derived *(ratified)*

**Decision.** A transaction is the **observable byproduct of latent agents (customers) acting in a geography against merchant assortments — never a bag of independently sampled fields.** Persistent state is pushed *upstream* into durable latent variables; the correlations that make data feel real (and discoverable) fall out of the causal chain rather than being sampled at emit-time.

**Why this replaces the current approach.** The baseline draws too much at emit-time — SKU, payment type, network, and entry mode are each sampled fresh per transaction (baseline §3.5, §3.7). With no persistent structure underneath, nothing is *discoverable*: affinities only echo hand-authored rules, customers have no favorites, per-store differences are noise. Moving state upstream is what makes agent insight real.

**Causal generation order — generate strictly top-down, each layer conditioned on those above:**

| Layer | Decision | What it fixes | Status |
|---|---|---|---|
| 0. Thesis | D11 | The spine itself | ✅ ratified |
| 1. Geography & zones | D13 | Where stores go, where people live | ✅ ratified |
| 2. Population shape | D14 | Heavy-tailed trip distribution; ~100k cards | ✅ ratified |
| 3. Customer durable state | D16 | Loyalty, preference vector, single-card identity (extensible) | ✅ ratified |
| 4. Trips | D15 + D15b | Temporal placement ✅ + store resolution ✅ | ✅ ratified |
| 5. Baskets & affinity | D17 | Emergent co-occurrence (mission model) | ✅ ratified |
| 6. Payment | D18 | Entry mode / wallet-at-tap / connectivity (emergent from card + customer) | ✅ ratified |
| 7. Catalog & price | D19 | Per-merchant pricing strategy + fixed catalogs | ✅ ratified |
| 8. Anomaly injection | D20 | Promo system + 3 business anomalies (multiplier hook) | ✅ ratified |

**Data model COMPLETE (Layers 1–8) as of D20.** Fraud/tampering anomalies explicitly out of scope for v4 (see D20).

**Note on ordering:** Layers 3 and 4 were swapped from D11's original draft — customer durable state must precede trips because store/banner resolution consumes loyalty. Anomalies are **not** a generation layer; they are a perturbation applied last, on top of fully-built data (see D15b / D16-note).

**The four theses that follow (all ratified):**
1. **Persistent customer state is the source of all realism.** A card's favorite SKUs, grocer loyalty, card portfolio, and home zone are fixed once and expressed across all 90 days. Repeat purchase, loyalty, and segmentation become real because they're modeled, not coincidental.
2. **Geography is causal, not cosmetic.** A store's location determines its clientele, which determines basket size, category mix, and payment mix. This is the only basis on which trade-area/location intelligence is defensible.
3. **Affinities emerge** from a latent co-occurrence model combined with customer habit, discoverable via lift — including associations never hand-authored.
4. **Prices are anchored to reality and varied by structured factors** — never frozen, never pure noise — so "pricing leverage" is a real, explainable signal.

## D12 — The data model is configuration-driven *(ratified)*

**Decision.** Generation is parameterized by **configuration objects**, not hardcoded per-merchant logic. Adding a new merchant (another QSR, another retailer) or a new segment must be a matter of **adding a config entry**, not rewriting generation code. A single generation engine reads the configs and runs the same causal pipeline (D11) regardless of how many merchants/segments exist.

**Rationale.** The baseline has per-merchant builder modules (`kroger.py`, `acme.py`, `taco_bell.py`, …) and per-merchant constants scattered through `parameters.py` — adding a merchant means touching code in several places (baseline §2). That does not scale and invites drift. A config-driven model makes the footprint a declarative artifact and keeps the engine generic.

**Three config layers:**

1. **Segment archetype** — defines a *kind* of merchant. One per segment (`grocery`, `qsr`, `off_price_retail`, and future `pharmacy`, `fuel`, …). Holds the behavioral physics for that segment:
   - trip-frequency model, basket-size model, daypart curve
   - distance-decay **β** and store attractiveness (D13)
   - default payment-mix (tender / network / entry)
   - **catalog model type**: `fixed` (grocery/QSR) vs `ephemeral` (off-price churn — see D7.6)
   - category schema for the segment
   *Adding a new segment* = add one archetype.

2. **Merchant config** — instantiates a banner *within* a segment. Holds:
   - `name`, `segment` (ref to an archetype), `positioning_tier` (`premium` / `mainstream` / `value`)
   - `store_count`, `zone_placement_bias` (which zone archetypes it favors)
   - `price_tier_modifier`, `private_label_share`, optional `catalog_overlay`
   - optional `payment_mix_overrides`
   *Adding a new merchant* = add one merchant config referencing an existing segment archetype + a placement bias + a tier. **No new generation code.**

3. **Zone config** — the metro (D13): the list of zones with profiles, residential weights, and centroids. *Retuning the metro* = edit this list.

**Config invariants (test-enforced, ties to D10):** residential weights sum to 1.0; every merchant's `zone_placement_bias` references valid zones; every merchant's `segment` references a defined archetype; total stores and per-segment volume targets stay within the D5 band. A config that violates an invariant fails a test before generation runs.

**Worked example — "add a second QSR (e.g. a burger chain)":** add a merchant config `{name: "...", segment: qsr, tier: mainstream, store_count: 7, zone_placement_bias: [commercial corridors], price_tier_modifier: +0.05}`. The engine then places its stores by the existing gravity model, generates baskets from the QSR archetype's menu/daypart physics, and assigns payment from the QSR defaults — all with zero engine changes. Same path for another retailer.

## D13 — Layer 1: Geography & Zones *(ratified)*

Everything inherits from this layer. It defines the metro, where stores sit, where customers live, and the store-choice math. It does **not** decide banner choice for grocery customers (that is loyalty × gravity, ratified later in Layers 3–4).

### D13.1 — The metro: 8 zones

A fictional metro modeled on Charlotte's structure. Names are **Charlotte-style and retained** (the metro is disclaimed as fictional per D2, so real-world fidelity objections don't bite; invented names were the alternative and were not chosen). Each zone's profile *causes* downstream behavior.

| Zone | Archetype | Affluence (spend ×) | Residential weight | Density | Household skew | Age skew |
|---|---|---|---|---|---|---|
| Center City | dense urban core | 1.15 | 8% | high | small (1–2) | young professional |
| Dilworth | established affluent | 1.45 | 12% | medium | family (3–4) | 35–55 |
| Ballantyne | newer affluent exurb | 1.40 | 14% | low | large family (4+) | 30–50 |
| NoDa | trendy / gentrifying | 1.10 | 10% | med-high | small | young (25–35) |
| University City | university district | 0.80 | 13% | medium | students + some family | 18–30 |
| Eastway | working-class value corridor | 0.75 | 16% | medium | larger households | mixed |
| Matthews | mainstream middle suburb | 1.00 | 19% | low-med | family | 30–55 |
| Cabarrus Edge | rural / exurban fringe | 0.90 | 8% | low | large | 35–60 |

Residential weights sum to 100% and drive where the ~100k customers live. **Affluence is a skew with within-zone variance, not a determinant** — no zone is monolithic. Each profile field feeds a specific later layer: affluence → basket size + premium-SKU propensity + payment mix; household skew → basket size; age skew → QSR late-night + mobile-wallet adoption; density → within-zone travel distance. Each zone has a centroid (lat/long) defined in the zone config.

### D13.2 — Store placement (24 stores) by banner positioning

Stores are placed where their banner's positioning fits the zone — the source of explainable per-store differences. (Counts are the `store_count` × `zone_placement_bias` of each merchant config, D12.)

| Zone | Kroger (5) | Acme (5) | Winn-Dixie (5) | Taco Bell (9) | TJ Maxx (5) |
|---|---|---|---|---|---|
| Center City | 1 | 1 | — | 1 | — |
| Dilworth | — | 1 | — | — | — |
| Ballantyne | 1 | 1 | — | 1 | 1 |
| NoDa | 1 | 1 | — | 1 | — |
| University City | 1 | — | 1 | 2 | 1 |
| Eastway | — | — | 2 | 1 | — |
| Matthews | 1 | 1 | 1 | 2 | 2 |
| Cabarrus Edge | — | — | 1 | 1 | 1 |

Positioning logic: Acme (premium) skews affluent/trendy and never enters the value corridor or exurb; Winn-Dixie (value) skews value/university/exurban and never enters Dilworth; Kroger (mainstream) spreads broadly; Taco Bell follows commercial corridors and the university (late-night); TJ Maxx sits in suburban retail/power centers. Placement is adjustable — e.g. concentrate two grocers in one zone for sharper head-to-head competitive stories.

### D13.3 — Home-zone assignment

Each customer is assigned a home zone by the residential-weight column, then draws affluence, household size, and age **conditioned on that zone's profile, with spread** (e.g. Gaussian around the zone mean, clipped). This makes a "loyal Dilworth Acme shopper" a consistent entity across all 90 days. (Full customer state is Layer 4.)

### D13.4 — Store choice: gravity model

Replaces the per-customer "closest" shuffle (baseline §3.4(d), §10.2) outright. Each store = its zone's centroid ± small jitter (keeps the baseline ±0.02°). Distance is straight-line between a customer's home zone and a store.

> **P(s) ∝ A_s / (d(z,s) + d₀)^β**

- **A_s** = store attractiveness by format (large-format grocery and destination off-price pull from far; QSR is local/impulse).
- **β** = distance-decay, per segment (lives in the segment archetype, D12):

| Segment | β | Behavior encoded |
|---|---|---|
| Grocery | 2.0 | People shop close; strong decay |
| QSR | 2.2 | Very local / impulse / on-commute |
| Off-price retail | 1.3 | Willing to drive to a destination |

- **d₀** = small constant so a same-zone store doesn't divide by zero.

**Layer boundary (explicit):** this defines the geographic pull function and where everything sits. It does **not** decide which *banner* a grocery customer picks — that is the customer's loyalty state (Layer 4) composed with this gravity at trip time (Layer 3). "Which Acme store" = gravity; "Acme vs. Winn-Dixie at all" = loyalty × gravity.

---

## D14 — Layer 2: Population shape *(ratified)*

This layer assigns each of the ~100k cards an **activity profile**: which segments it touches and how active it is in each (a per-segment trip budget). It does **not** place trips in time/space (Layer 4) or assign preferences/loyalty/card identity (Layer 3).

### D14.1 — Reconciliation principle (defensibility)

The population is **solved backward from the D5 volume targets**, not asserted. For each segment: `active cards × mean trips per active card = segment transaction total`. If the shape doesn't multiply out to 1.08M / 365k / 225k, it is wrong by definition. **Test-enforced (D10):** generation must land within the D5 volume band or it fails before anything downstream runs.

### D14.2 — Population size: ~100k, derived

| Segment | Transactions | Mean trips/active card | ⇒ Active cards |
|---|---|---|---|
| Grocery | 1.08M | ~16.6 (~1.3/wk) | ~65k |
| QSR | 365k | ~10.1 | ~36k |
| Off-price retail | 225k | ~6.1 | ~37k |
| | | **Σ memberships** | **~138k** |

~138k segment-memberships across ~100k distinct cards; the overlap (D14.4) closes the gap. ~100k matches D5.

### D14.3 — Heavy tail: intensity tiers per segment

Activity within a segment is a **mixture of three intensity tiers** (more interpretable and configurable than a raw negative-binomial fit). Within a tier, per-card trip count is drawn from a small dispersed distribution (Poisson/triangular) around the tier mean, clipped to the tier range. **Tier definitions live in the segment archetype (D12)** — a new merchant inherits its segment's tiers.

**Grocery** (mean ~16.6):
| Tier | Share of grocery-active | Trips/90d (μ) | Cadence |
|---|---|---|---|
| Core household | 20% | 28–40 (~34) | 2.5–3×/wk; volume backbone |
| Regular | 45% | 14–22 (~18) | ~1.5×/wk |
| Occasional | 35% | 3–10 (~6) | top-up / secondary store |

**QSR** (mean ~10.1):
| Tier | Share | Trips/90d (μ) | Cadence |
|---|---|---|---|
| Heavy user | 15% | 30–55 (~40) | brand-loyal near-daily |
| Regular | 35% | 8–15 (~11) | ~weekly |
| Occasional | 50% | 2–6 (~4) | long tail |

**Off-price retail** (mean ~6.1):
| Tier | Share | Trips/90d (μ) | Cadence |
|---|---|---|---|
| Enthusiast | 12% | 10–20 (~16) | treasure-hunt regular |
| Regular | 33% | 4–8 (~7) | ~monthly+ |
| Occasional | 55% | 1–3 (~2.5) | one-and-done tail |

The large occasional tiers are deliberate — real per-store distinct-card counts are dominated by occasionals, and **new-vs-returning analytics depend on this tail existing.** Small core/heavy tiers carry disproportionate volume (the 80/20 shape).

### D14.4 — Segment participation & overlap (satisfies D6)

Seven participation archetypes, summing to 100k, solved so the per-segment actives (D14.2) are hit and multi-merchant lands at ~32% (top of the D6 25–30% band, rounded):

| Participation | Share | ⇒ cards |
|---|---|---|
| Grocery only | 37% | 37k |
| Retail only | 18% | 18k |
| QSR only | 13% | 13k |
| Grocery + QSR | 13% | 13k |
| Grocery + Retail | 9% | 9k |
| Grocery + QSR + Retail | 6% | 6k |
| QSR + Retail | 4% | 4k |
| **Multi-merchant total** | **32%** | **32k** |

Cross-check: grocery-active 37+13+9+6 = 65k ✓; QSR-active 13+13+6+4 = 36k ✓; retail-active 18+9+6+4 = 37k ✓. Design intent: the **all-three 6%** is the premium cross-shopper — the small, high-value population that makes the cross-merchant story real and discoverable; **grocery anchors overlap** (only the 4% QSR+Retail group excludes it), matching grocery's role as the high-frequency hub.

### D14.5 — Realism anchors (sourced)

- **Grocery:** the primary U.S. grocery shopper averages ~1.6 trips/week (FMI, 2023), and shoppers use ~2 grocery stores/week (Drive Research, 2024). Our per-banner frequencies account for store-splitting; the blended mean (~1.3/wk) sits just below the primary-shopper headline because our population includes a large secondary-store/occasional tail. **Defensible.**
- **QSR:** among adults who buy fast food, ~2.7 visits/week *across all brands combined* (USDA ERS); ~65% eat fast food weekly. Our QSR segment is a **single brand**, so our ~0.8 visits/week mean is one brand's share of that total, and the heavy tier (~4/wk at the brand) is the brand-loyalist minority. **Defensible.**
- **Off-price (TJ Maxx):** least externally anchored — no clean published per-visit figure. Shape (small enthusiast core, large occasional tail) is sound by reasoning and matches the treasure-hunt model. **Flagged to revisit** if harder data surfaces.

### D14.6 — Configurability (D12)

- **Intensity tiers** are a property of the segment archetype — a new QSR/retailer inherits the tier mix automatically.
- **The participation matrix is metro-level config, not per-merchant.** Adding a merchant adds capacity to its segment; the solver re-balances the matrix to hit the new volume target while preserving the ~25–30% multi-merchant constraint. No hand-editing of overlap percentages per merchant.

### D14.7 — Boundaries & hooks

- **Affluence is kept out of trip frequency.** Zone affluence expresses through *basket* (Layer 5) and *payment* (Layer 6), not how often you shop. (Affluent households doing fewer-but-larger stock-up trips is a basket-archetype effect at L5, not a frequency effect.) Flag if you'd rather affluence lightly bias tier assignment — an easy config lever.
- **Cohort tag (hook for L3).** Each card is tagged **established / new-in-window / lapsing** here; Layer 3 places first-appearance dates from it so new-vs-returning analytics work. (Assignment in L2; placement in L3.)

---

## D15 — Layer 4a: Trip temporal placement *(ratified)*

*(Layer numbering reflects the swap: customer state is now Layer 3, trips Layer 4. The temporal half of trips is locked here; the spatial half is D15b, pending the customer layer.)*

This turns each card's per-segment trip budget (D14) into dated, timed transactions. Store resolution is D15b.

### D15.1 — Week-level spread
Retain the baseline's active-weeks + Dirichlet mechanism (it is sound), re-tuned to the intensity tiers: heavy/core cards are active in nearly all 13 weeks; occasional cards in fewer. Trips distribute across active weeks with mild concentration, then land on days via the weights below.

### D15.2 — Day-of-week weights (relative multipliers)

**Grocery** — weekend-skewed (Sunday historically busiest, Saturday second, Friday third; FMI/Circana). ERS: ~13% shop on an average weekday vs ~16% on an average weekend day (2014–17), a ~1.25× weekend lift.

| Sun | Sat | Fri | Mon | Thu | Wed | Tue |
|---|---|---|---|---|---|---|
| 1.25 | 1.20 | 1.05 | 1.00 | 0.95 | 0.90 | 0.85 |

(Weekend/weekday ratio ≈ 1.29; Tuesday lightest.)

**QSR (Taco Bell)** — Friday/Saturday peak; late-night amplifies the weekend further (see daypart).

| Fri | Sat | Sun | Thu | Wed | Mon | Tue |
|---|---|---|---|---|---|---|
| 1.25 | 1.20 | 1.05 | 1.00 | 0.95 | 0.90 | 0.90 |

**Off-price retail (TJ Maxx)** — strong weekend destination skew.

| Sat | Sun | Fri | Thu | Mon | Wed | Tue |
|---|---|---|---|---|---|---|
| 1.35 | 1.20 | 1.10 | 0.90 | 0.90 | 0.85 | 0.85 |

### D15.3 — Pay-cycle overlay
Applied on top of day-of-week. Strongest for grocery (especially value-skewed zones — Eastway, University City), modest for QSR, negligible for off-price:
- **Early-month (days 1–10): ~1.10–1.20×, front-loaded** — paychecks + benefits; EBT/SNAP funds typically load in the first 10 days of the month (state-dependent), and stores run busier on payday around month start/end.
- **Mid-month (days 15–17): ~1.10×** (biweekly/semi-monthly paychecks).

### D15.4 — Daypart / hour curves

**Grocery** — weekday bimodal: peaks 11am–1pm and 3–4pm (ERS) plus a 5–7pm commute peak; weekend single late-morning-to-afternoon hump (busiest midday onward, with a 3–5pm bump).

**QSR (Taco Bell)** — daypart mix below. Taco Bell over-indexes on nontraditional dayparts: it estimates ~one-fourth of sales come from the 2–5pm + after-midnight periods combined (QSR Magazine); late-night is only ~4% of QSR sales industry-wide (Technomic) but Taco Bell sits well above that.

| Daypart | Window | Share |
|---|---|---|
| Breakfast | 6–10am | 7% |
| Lunch (peak) | 10am–2pm | 31% |
| Afternoon ("Happier Hour") | 2–5pm | 13% |
| Dinner | 5–9pm | 30% |
| Late evening | 9pm–12am | 12% |
| After midnight | 12–3am | 7% |

Late-night block (9pm+) = 19% vs the ~4% industry average — the Taco Bell signature; amplifies further on Fri/Sat.

**Off-price retail** — midday/afternoon weighted; 11am–5pm weekend peak; lighter, flatter weekdays.

### D15.5 — Cohort & first-appearance placement
From the D14.7 tags: **established** cards transact from day 1 across the full window; **new-in-window** cards get a first-appearance date (weighted toward later weeks) and transact only after it; **lapsing** cards concentrate early and taper to zero before window end. This makes new-vs-returning analytics real rather than uniform.

### D15.6 — Anomaly hooks (engine support only)
The placement engine accepts **localized multipliers on placement probability, scoped to zone × store × time-window × category, as a first-class input.** Hook points are defined here; the *specific* planted signals and their parameters are authored later, with the Anomaly agent's targets (see Layer 8 note). For Layer 4 the requirement is only that the engine supports injection.

## D15b — Layer 4b: Store resolution *(ratified)*

- **Within a banner, which store:** the L1 gravity model `P(s) ∝ A_s / (d + d₀)^β` (D13.4). Fully specified.
- **Which banner (grocery):** `P(banner b) ∝ loyalty_weight(b) × gravity_pull(b)`, where gravity_pull aggregates b's stores' attractiveness/distance. **loyalty_weight is durable customer state** — supplied by D16.1. Now closed: customer loyalty (D16) is ratified, so banner-then-store resolution is fully specified.

### Anomalies are a final-step perturbation, not a layer (build-order step 8)
An anomaly bends an existing distribution for a scoped slice (zone × store × time × category) — it builds nothing new, so it can only be applied after the data it perturbs exists. Anomalies are therefore the **last data step**, after baskets and pricing. Their parameters are best authored as the **answer key to the Anomaly Detection agent** (a perturbation + the ground-truth the agent must rediscover), so signal and detection are designed together. The engine is already anomaly-ready via D15.6.

---

## D16 — Layer 3: Customer durable state *(ratified)*

Attributes fixed once per card and expressed across all 90 days. Closes the D15b dependency.

### D16.1 — Banner loyalty (grocery)
A "strong-primary, real-spillover" model, not exclusivity: 92% of grocery shoppers have one primary store but only ~13% are loyal to a single retailer; the average shopper touches ~4.4 banners/month, and convenient location is the #1 store-choice driver (FMI; Progressive Grocer 2024). Loyalty weights map from the existing `grocer_affinity_type`:

| Affinity type | Pop. share | Primary | Secondary | Third |
|---|---|---|---|---|
| Loyalist | 55% | ~88% | ~10% | ~2% |
| Splitter | 30% | ~60% | ~38% | ~2% |
| Three-chain | 12% | ~45% | ~32% | ~23% |
| Lapsed/light | 3% | ~70% | ~25% | ~5% |

At trip time (D15b): `P(banner) ∝ loyalty_weight(b) × gravity_pull(b)` — loyalty sets base preference, distance sharpens it (matches "convenience #1").

**Defensibility — population-weighted ~74% primary concentration.** Published all-channel "primary store share" runs ~50–67%, but that counts Walmart/club/dollar/online (Walmart alone is where ~25% of shoppers go most often). Our model has only three traditional grocers and no Walmart/club/dollar, so concentration among the three should run *higher* than the all-channel figure. ~74% is defensible, even conservative.

### D16.2 — Preference vector (feeds Layer 5)
Each card gets a **persistent category-preference distribution** (Dirichlet around its segment's base category mix) plus a small set of **staple SKUs** it buys repeatedly — the "favorite cereal" mechanism whose absence the baseline flagged (§3.7, SKU re-sampled each trip). Conditioned on household (size → baby/pet weight) and affluence (→ premium-tier propensity). Staples ride high per-trip inclusion, matching reality (dairy bought by ~82% of shoppers per trip). Structural realism device; validated indirectly via repeat-purchase rate at Layer 5.

### D16.3 — Card identity: one token per customer *(ratified)*, structured for extensibility

**Decision.** One card token per customer for v4 — every customer transacts on a single card used everywhere. This keeps cross-merchant linkage clean (every multi-merchant card is trivially linkable). Built **portfolio-ready** so multi-card is a later config change, not a rewrite.

Each customer's card carries:
- **Tender** (credit/debit), assigned by **affluence + age** (affluent/older → credit; value/younger → debit).
- **Network**, conditioned on tender: debit ≈ Visa ~60 / MC ~38 / other ~2; credit ≈ Visa ~50 / MC ~25 / Amex ~13–19 / Discover ~5–6. (Corrects baseline §3.5, which drew network largely independent of tender.)
- **Wallet enrollment** flag (~45%, age/affluence-skewed); provider Apple ~55 / Google ~30 / Samsung ~15.

**Per-merchant payment mix is emergent, not a knob — resolves D7.5.** With one card used everywhere, a store's debit/credit mix falls out of *who shops there*: value-zone (debit-skewed) customers concentrate at the value grocer, so it naturally sees more debit. This fixes the baseline's wrong grocery-credit-heavy 65/35 by construction rather than decree, and respects the D14 boundary (affluence drives payment, not frequency). Per-transaction *tender choice* collapses (one card); entry mode (contactless/chip/swipe) and wallet-at-tap remain per-transaction (Layer 6), conditioned on the wallet flag + merchant + daypart.

**Extensibility (D12).** Model the card as a `cards: [...]` list with a `primary` flag, instantiated at length 1. Config knob `cards_per_customer` (default 1) + a per-transaction card-selection policy stub → multi-card-with-primary becomes a config change plus policy fill-in. The primary card is the designated cross-merchant linkage key when expanded.

**Anchors:** ~90% debit / ~82% credit ownership; ~3.9 credit cards avg among holders (collapsed to the single primary here); ~35% credit / ~30% debit of transactions by count (Fed 2025 Diary); ~45% proximity mobile-pay use; Apple ~49–54% of wallet base/taps.

### D16.4 — Boundaries & config
Affluence drives basket + payment, not trip frequency (D14 boundary held). Loyalty archetype shares are metro-level config; tender/wallet skews are segment/zone config; preference-vector generation is generic across segments.

---

## D17 — Layer 5: Baskets & affinity *(ratified)*

A basket is assembled from the customer's preference vector (D16.2) ∩ the store's assortment (Layer 7), under a latent **trip mission**, with a complementary-affinity boost, sized by archetype. Emergent, discoverable co-occurrence is the goal (satisfies D7.1).

### D17.1 — Mission model (emergent coherence — the core fix)
Replaces the baseline's hardcoded co-occurrence rules (which can only echo the ~handful of authored closures). Per trip: sample a **mission** (conditioned on the customer's preference vector, daypart, archetype), then fill the basket by drawing categories from the mission's distribution, intersected with customer preferences and store assortment. Co-occurrence **emerges** from shared missions and is discoverable via lift — including combinations never hand-authored. This matches how real baskets form (mission/trip-type shopping is the documented retail-analytics norm).

| Segment | Example missions (each = a category distribution) |
|---|---|
| Grocery | weekly stock-up · meal-tonight · breakfast/staples · snacks & beverages · household/cleaning · baby & pet · occasion/BBQ |
| QSR | combo meal · snack run · group order · breakfast |
| Off-price | apparel refresh · home goods · gifting |

### D17.2 — Complementary-affinity boost (designed discoverable pairs)
A modest pairwise affinity matrix over categories/subcategories: when a complement is already in the basket, boost the partner's draw probability (pasta↔sauce, chips↔salsa, diapers↔wipes, burger↔buns). Most pairs neutral; a handful strong. Gives a few intentional, discoverable affinities on top of the emergent mission-driven ones — so agents have both designed and emergent patterns to surface via lift.

### D17.3 — Customer habit / repeat purchase
Within a mission's category, item selection biases toward the customer's **staple SKUs** (D16.2), so a household's favorites recur across trips → discoverable loyalty and repeat-purchase realism. (Fixes baseline §3.7, SKU re-sampled fresh each trip.)

### D17.4 — Basket size, distribution & AOV
Keep the baseline's triangular machinery by segment × archetype, re-tuned to real shapes. Conventional supermarket baskets run ~15–30 items vs 4–6 for specialty; blended grocery ~12–15. Distribution is **heavy-tailed (Poisson-lognormal); top 20% of baskets ≈ 50% of unit sales / 40% of dollars** (ScienceDirect) — a test target. Grocery archetypes interact with the pay-cycle (D15.3: stock-up early-month, fill-in late-month).

| Archetype | Items | Mix |
|---|---|---|
| Stock-up | 15–30 | early-month weighted |
| Fill-in | 5–8 | late-month weighted |
| Quick | 2–3 | top-up |

QSR is combo-structured (entree + drink + side, 2–5 items); off-price 1–12.

**AOV & D5 reconciliation.** Our model is transaction-level, so the anchor is POS transaction value (~$40–60 for grocery; ~$49 pre-2020, rising with inflation), **not** survey "trip" figures (~$174, which conflate the weekly shop). Blended grocery transaction ≈ **~$55** (stock-up ~$110, fill-in ~$28, occasional ~$18). This refines D5's ~$45 placeholder upward → 800 txns/day × $55 × 365 ≈ **~$16M/yr** per grocery store (real mid-size supermarket range, improves on the $13M at $45). QSR ~$9–12 (Taco Bell value positioning); off-price ~$30–50. Exact AOV emerges from basket-size × Layer-7 prices; **test-enforced** to hit per-segment bands and reconcile with D5 store AUV.

### D17.5 — Per-line attributes
Quantity per line by category (keep baseline `QTY_DISTRIBUTION`); unit price set in Layer 7 (basket layer references, doesn't set); promo state applies if the SKU is on promo in the window (interacts with Layer 7 promo design); de-dup within basket (keep baseline).

### D17.6 — Catalog depth decision (resolves open item 6)
**Keep ~1,100 grocery SKUs for v4.** Category/subcategory-level affinities — the defensible, agent-relevant ones an exec cares about — are well-supported at this depth. SKU-level brand-to-brand affinity is thinner but nice-to-have, not core. **Revisit only if the affinity-discoverability tests come back thin.**

### D17.7 — Configurability (D12)
Missions, the affinity matrix, and archetype shares are segment-archetype config — a new merchant inherits its segment's missions/affinities. Designed affinity pairs are a short config list; emergent affinities need no authoring.

### D17.8 — Realism anchors (sourced)
- Conventional supermarket basket ~15–30 items, specialty 4–6; blended ~12–15 (BusinessDojo, Ibotta, Circana).
- Basket-size distribution ~Poisson-lognormal; top 20% baskets ≈ 50% units / 40% dollars (ScienceDirect).
- Grocery POS transaction value ~$40–60 (Clearly Payments 2025; ~$49 pre-2020, Earnest) — transaction-level, distinct from survey "trip" figures.
- Mission/trip-type shopping is the documented norm; stock-up vs fill-in interacts with pay-cycle (Grocery Dive).

---

## D18 — Layer 6: Payment *(ratified)*

Short layer — D16.3 already locked tender, network, and the emergent per-merchant credit/debit mix. Layer 6 sets only the three per-transaction fields D16 deferred.

### D18.1 — Entry mode (emergent, not an independent draw)
Key change from baseline (which drew entry mode independently per segment, §3.5): entry mode is conditioned on **customer** (wallet-enrollment flag + age, from D16/zone) × **merchant** (segment, terminal capability) × **daypart**. Per-store entry-mode mix emerges from clientele — what lets the Payment Optimization agent say something real ("this store skews mobile because its clientele is younger"). Population-blended segment baselines (2026), anchored to the ~50% national face-to-face contactless rate:

| Segment | Contactless | Chip | Swipe | Manual |
|---|---|---|---|---|
| Grocery | ~52% | ~40% | ~7% | ~1% |
| QSR (Taco Bell) | ~63% | ~30% | ~5% | ~2% |
| Off-price retail | ~48% | ~44% | ~7% | ~1% |

QSR highest (drive-thru, younger, late-night); off-price chip-heavier (older skew). Swipe declining to ~5–7%; card-present manual ~1–2%.

### D18.2 — Wallet-at-tap
Whether a contactless tap is phone/watch vs physical card is **gated by the customer's D16 wallet-enrollment flag**: enrolled customers use phone for ~55–70% of taps, non-enrolled tap the card. Population-wide → ~16–20% of all transactions on mobile wallet (matches ~16% NA POS share trending to 31% by 2027). Provider split from D16: Apple ~55 / Google ~30 / Samsung ~15.

### D18.3 — Connectivity type (terminal telemetry)
Device-layer field set by **store terminal form factor**, not consumer behavior: fixed countertop (grocery, off-price) skews wifi/ethernet; QSR counter+drive-thru wifi/ethernet + some cellular. Rough split wifi ~55% / ethernet ~30% / cellular ~15%, varying by format. Feeds the Payment Optimization / device-health narrative.

### D18.4 — Realism anchors (sourced) & configurability
Contactless ~50% of face-to-face (Visa); ~75% of cards contactless-capable; 87% prefer contactless (Payroc); mobile wallet ~16% of POS → 31% of in-store by 2027 (eMarketer). Entry-mode baselines and wallet-at-tap rates live in segment-archetype config (D12); a new merchant inherits its segment's payment physics.

---

## D19 — Layer 7: Catalog & price *(ratified)*

The last data-content layer. Sets assortment and price; closes the AOV chain with D17/D5. Implements D7.4 (price variation).

### D19.1 — Pricing heuristic (replaces baseline `base × tier × ±2%`)
Anchor-to-reality × structured modifiers, in order:
1. **Real category anchor** — every SKU starts from a real 2025 price point (references checkable: 2% milk ~$2.78–3.50/gal, eggs ~$3.47/doz, ground beef ~$4.69–5.75/lb, butter ~$3.75/4-stick, shredded cheddar ~$2.79/12oz). Exact anchors pinned in `SPEC.md` from a reference table (BLS / retail price data).
2. **Per-merchant pricing strategy** (see D19.2 — replaces the flat tier modifier).
3. **Zone effect** — affluent-area stores +~2–4%; modest (within-banner geographic variation is small).
4. **Promo state** — promoted SKUs drop by promo depth *in unit_price* during the window (closes the frozen-price gap; depths from baseline).
5. **Time drift** — mild reprice over 90 days (~±1–2%); grocery inflation ran ~2% YoY in 2025 (BofA), prorated + occasional step changes, so price time series isn't flat.
6. **Idiosyncratic noise** — small (~±1%), last; not the primary driver.

### D19.2 — Per-merchant pricing strategy (NOT a flat tier) — the SKU-level insight engine
A flat "+10% premium" collapses cross-merchant analysis to a constant. Real grocery pricing flips item-by-item: in head-to-head comparisons no single store is cheapest on everything (Aldi wins private label, Walmart some national brands, etc.). To reproduce that *discoverable* variation, four item-level levers replace the flat tier:

1. **Category price-role per merchant** — each grocer is aggressive (traffic-driver KVI) on some categories and monetizes others. Premium competitive on organic produce, marks up prepared/deli; value cheapest on staples, at-par on specialty.
2. **Private-label vs national-brand divergence** — the biggest real lever (the value grocer's savings come from PL depth). Value PL deeply discounted; national brands closer to par.
3. **Per-SKU competitive index** — controlled item-level idiosyncrasy on top of category role, so gaps aren't fully derivable from tier + category.
4. **Promo-timing divergence** — merchants promote different SKUs in different weeks, so cross-merchant gaps *flip over time*.

**Category price-sensitivity makes "leverage" a computed finding.** Staples = known-value items → tight cross-banner spread; specialty/discretionary → wide spread. Leverage exists where spread is wide AND volume is high — computed, not planted.

### D19.3 — Grocer differentiation summary

| | Acme (premium) | Kroger (mainstream) | Winn-Dixie (value) |
|---|---|---|---|
| Assortment | Widest specialty/organic tail | Broadest balanced (national + PL) | Leaner SKU count; thin specialty |
| Private-label share | ~20% | ~25–30% | ~40–50% (its identity) |
| Aggressive on (cheap) | Organic produce, KVI staples | Promoted national brands, loyalty pricing | PL staples, center-store basics |
| Monetizes (marks up) | Prepared/deli, specialty | Middle; promo-driven | National brands, specialty |
| Net basket vs mainstream | ~+8–12% (concentrated in specialty) | baseline | ~−7% (concentrated in PL/staples) |

Illustrative SKU-level behavior (exact in spec) — **who's cheapest flips by SKU type**, which is the insight:

| SKU type | Acme | Kroger | Winn-Dixie | Cheapest |
|---|---|---|---|---|
| 2% milk 1gal (KVI) | $3.59 | $3.49 | $3.29 | Value, tight ~9% spread |
| National-brand cereal | $5.49 | $4.79 (promo) | $4.99 | **Kroger** (not value) |
| Private-label equiv | $3.79 | $3.19 | $2.69 | Value, wide ~29% |
| Organic pasta sauce | $5.49 | $5.29 | not carried | assortment gap at value |

Insights this enables (SKU-level, cross-merchant, non-mechanical): "On PL you're 25%+ below peers but *above* Kroger on promoted national-brand center-store — margin leaking to a competitor"; "You're missing the organic line both peers carry — assortment gap, not pricing"; "Your milk is competitive but Kroger undercuts you on rotating promo SKUs every other week."

### D19.4 — Assortment differentiation
Banners differ in *what* they carry; private label is the biggest lever. Value ~40–50% PL + fewer SKUs; mainstream ~25–30% PL; premium ~20% PL + specialty/organic tail. Store-level variation: affluent-area/larger stores carry more of the premium tail. Grocery stays ~1,100 SKUs (D17.6).

### D19.5 — TJ Maxx & QSR catalogs (resolves open item 7)
**TJ Maxx: fixed catalog, no rotation.** Rotating/ephemeral SKUs rejected — too much complexity for thin analytic payoff. Off-price is modeled as a normal fixed catalog priced MSRP-anchor × deep discount (~20–60% below MSRP) with a visible compare-at. **QSR (Taco Bell):** menu anchored to real value positioning (items ~$1.29–8, value menu ≤$3, combos ~$5–9), LTOs tied to D15 dayparts; single brand → no cross-banner peer pricing.

### D19.6 — AOV closure (with D17 / D5)
These prices × D17 basket sizes produce the AOVs (grocery ~$55, QSR ~$9–12, off-price ~$30–50). **Test-enforced** to land in the per-segment bands and reconcile with D5 store AUV. This closes the AOV chain.

### D19.7 — Configurability (D12) — settable for a new merchant across segments
All pricing/assortment behavior is config, so a new merchant inherits its segment's pricing physics and is differentiated by its own config block:
- **Segment archetype:** category schema, per-category price-sensitivity (KVI-tight vs specialty-wide spread), base-anchor reference, catalog model (fixed).
- **Merchant config:** `positioning_tier`, `category_price_roles` (aggressive/monetize map), `private_label_share`, `per_sku_competitive_index` params, `promo_timing_profile`, `assortment_breadth` (which categories/tail carried).
- **Adding a merchant** = set those fields; the engine applies the same anchor × strategy × promo × drift pipeline with zero new code. A second QSR or a new grocer slots in by config alone.

### D19.8 — Realism anchors (sourced)
- Intra-traditional-grocer spread ~±8–12%; hard-discount (Aldi) ~24% below traditional — our value tier is traditional, so ~−7%, not Aldi-level (Cheapism).
- No single store cheapest on everything; who-wins flips item-by-item (Cheapism, GOBankingRates).
- Real 2025 category prices (GOBankingRates/Rachel Cruze comparison); grocery inflation ~2% YoY 2025, +23% vs 2019 (BofA).
- Private label is the largest price lever (Aldi savings driven by PL).
- Off-price ~20–60% below MSRP.

---

## D20 — Layer 8: Promotions & planted anomalies *(ratified)*

Two kinds of events layered onto built data: **promos** = expected business rhythm (Demand/Pricing agents account for them); **anomalies** = rarer planted signals the Anomaly agent must rediscover (its answer key). Both use the D15.6 multiplier hook; both are config lists.

### D20.1 — Promo system (business rhythm)
**Types by segment.** Grocery: weekly ad/circular (weekly refresh, 50–150 SKUs, modest depth), loss-leader/TPR (deep KVI cuts), seasonal/holiday, BOGO & multi-buy, clearance. QSR: LTOs (narrow), persistent value menu, combo/bundle, 2–5pm daypart deal (D15). Off-price: markdown/clearance cadence.

**Penetration & depth.** ~25–35% of grocery units on promotion (CPG benchmark); trade/consumer promo ~20% of CPG revenue (McKinsey, Nielsen). Depths: weekly ad 10–25%, holiday 15–30%, BOGO ~50%, clearance 20–50%, loss-leader deep on select KVIs. Coverage broad for weekly ad, narrow for LTO.

**Demand response (the piece the baseline lacked).** A promoted SKU gets a basket-inclusion + quantity **lift** during its window, scaled to discount depth × category elasticity (real lifts ~10–200%+). This makes promos detectable as *causal* — the Demand agent sees the spike, the Pricing agent ties it to the discount.

**Cross-merchant divergence (D19.2 lever 4).** Merchants promote different SKUs in different weeks → price gaps flip week to week (dynamic pricing intelligence).

### D20.2 — Planted anomalies (Anomaly agent's answer key)
Each = a scoped perturbation + ground-truth to rediscover; designed detectable-but-not-trivial. All three use the multiplier hook (no transaction-injection needed).

| # | Anomaly | Type | Scope | Window | Detection signature |
|---|---|---|---|---|---|
| A1 | Localized demand decline | business | one zone's grocery stores, one banner hit hardest | multi-week arc (e.g. wks 5–13) | sustained trend drop at specific stores vs. stable peers |
| A2 | Category demand spike | event | one category at one store | a few days | sharp short-lived lift; point anomaly in category-store series |
| A3 | Competitive share shift | business | one banner ↑ / peer ↓ in shared zones | a promo window | cross-merchant correlation in overlapping trade areas |

These re-home the baseline's three signals (University City decline, avocado spike, pasta promo) onto the new metro, with A3 reframed as a competitive dynamic.

### D20.3 — Fraud/tampering explicitly OUT of scope for v4
A4 (card-testing) and A5 (terminal tampering) were proposed and **removed** — no fraud/tampering modeling in v4. **Implication (recorded to prevent over-claiming):** the Anomaly Detection agent is scoped to *business* anomalies (declines, spikes, competitive shifts); it must not claim fraud detection, since no fraud is planted. Fraud is a clean future addition (add A4/A5 + a transaction-injection hook) but not now.

### D20.4 — Configurability (D12)
- **Promos:** `promo_timing_profile` per merchant (cadence, calendar) + segment-level type menu, depth ranges, lift-elasticity. New merchant inherits its segment's promo physics.
- **Anomalies:** `anomalies: [{type, scope, window, magnitude, shape}]` applied via the multiplier hook. Adding/tuning = edit the list; the answer key updates with it.

### D20.5 — Realism anchors
Promo penetration ~25–35% of grocery units; trade/consumer promo ~20% of CPG revenue (McKinsey, Nielsen/Cliffedge); lift scaled to depth × elasticity. A1–A3 map to real phenomena (competitor-driven decline, viral/event spike, competitive share shift).

---

## Open items (decide before / during `SPEC.md`)

1. **Phase sequencing — NOT YET DECIDED.** Options: (a) data realism first, then agents; (b) agent unification first; (c) both in parallel. Note D9 must follow D8 regardless.
2. **Exact per-store AUV targets**, pinned to sourced figures, for D5.
3. **S3 timing** (D3): confirmed deferred; revisit when the production narrative is wanted.
4. **Disclaimer wording & placement** (D2) for the fictional metro.
5. **DATA MODEL FULLY COMPLETE (Layers 1–8).** Geography, population, customers, trips, baskets/affinity, payment, catalog/price, promos & anomalies — all ratified with sourced realism anchors. Ready for SPEC conversion.
6. **Grocery catalog depth — RESOLVED (D17.6):** keep ~1,100 SKUs for v4; revisit only if affinity-discoverability tests come back thin.
7. **TJ Maxx assortment fidelity — RESOLVED (D19.5):** fixed catalog, no rotation.
8. **Anomaly scope — RESOLVED (D20):** 3 business anomalies (A1–A3); fraud/tampering (A4–A5) out of scope for v4. Anomaly agent scoped to business anomalies, must not claim fraud detection.
9. **Execution plan — pending capture (NEXT).** Branch strategy (`v4` integration branch off `main`, short-lived feature branches, `v3-final` tag), dependency-gated waves (0: cleanup/test scaffolding; 1: data+config+storage; 2: anonymization+lake; 3: agent contract+chart library; 4: dashboard+ask-AI), test-first gating per wave, cleanup-as-Wave-0. **Open sub-question:** convert DECISIONS→SPEC per-wave just-in-time (lean, leaning this) vs. all up front.
10. **Agent & dashboard detailed design — pending.** D8 (unification), D9 (ask-AI), and the strategy-doc agents decided at principle level, not yet drilled. Note from D20.3: Anomaly agent is business-anomaly-only in v4. Slated for Waves 3–4 / their per-wave SPEC.

---

*This record supersedes `docs/BASELINE.md` only where explicitly noted; the baseline remains the accurate "before" snapshot for everything else. Next step: turn these decisions into `SPEC.md`, then validate against the live code in Claude Code plan mode before implementing.*
