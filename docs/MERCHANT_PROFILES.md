# Merchant Profiles — Generation-Ready Spec

Defines the **expected numbers** for each merchant and each segment, so the generator can be calibrated to reproduce them and validated against them. Every parameter is tagged **[SOURCED]** (anchored to a real benchmark) or **[ASSUMPTION]** (a defensible modeling choice, labeled as such).

**Framing:** fictional/typical metro; the **merchants are the real chains** — Kroger, Acme (Albertsons), Winn-Dixie (Southeastern Grocers). These three operate in *different* real regions (Midwest/South, Northeast, Deep South) and cannot coexist in one real market — which is exactly why the shared metro must be fictional. Profiles use each chain's **real chain-level economics**, placed into a typical metro.

Status: **Part A (Grocery), Part B (QSR), Part C (Population & Transactions) — all drafted. Core transaction logic complete; anomalies + promotions deferred.**

---

# PART A — GROCERY SEGMENT

## A1. Store count — how many, and why

**Typical-metro anchor.** A large US metro (~2–3M people) carries a few hundred grocery stores. US average density is ~1 supermarket per ~7,350 people (FMI: 45,575 supermarkets / US pop); a dense metro runs higher — the modeled Charlotte-style metro is ~1 per 4,000 (~737 stores for ~3M people) [SOURCED]. A single major banner operating in such a metro would, in reality, have anywhere from ~15 to ~100 stores.

**Modeling decision: 15 grocery stores — Kroger 6 / Acme 5 / Winn-Dixie 4** [ASSUMPTION], a deliberate small sample (~2% of the metro's stores) of what each banner's real metro footprint would be. Rationale:
- **4–6 stores/banner is the floor for within-banner analysis** — anomaly localization (one store spikes, a sub-region declines) needs multiple stores per banner to read as *localized* not *banner-wide*.
- **Small enough to fully author**, every store in a named zone with a real trade area.
- **6/5/4 ordering encodes real banner scale** (Kroger is the largest chain; Winn-Dixie the smallest) — differentiation starts at store count.

## A2. Per-store sales volume — the anchor

**Real anchor.** FMI 2024: average supermarket = **$711,806/week ≈ $37M/year** [SOURCED]. The three banners spread around it by each **real chain's** productivity and positioning:

| Banner | v2 per-store AUV | Real anchor |
|---|---|---|
| Kroger | **~$45M** | Kroger ~$150B / ~2,700 stores; large-format mainstream, above FMI avg [SOURCED, derived] |
| Acme | **~$32M** | Albertsons ~$35M/store; Acme a conventional NE banner, ≈ FMI avg [SOURCED, derived] |
| Winn-Dixie | **~$22M** | Southeastern Grocers ~$10B / ~495 stores ≈ $20M/store; value, below avg [SOURCED, derived] |

**Two calibration facts:**
1. The gap from today's $15.42M is **traffic, not basket** — FMI in-store transaction = **$45.70** [SOURCED], already below the repo's $53.59. → raise trips ~2.4×, hold basket ~$45–55.
2. **Differentiation is productivity + assortment, not size.** All three real chains run **similar-size stores (~45–50k sqft)** [SOURCED: Albertsons 35–107k; Winn-Dixie ~44–50k]. Kroger's $45M vs Winn-Dixie's $22M comes from **(a) sales productivity** (sales per sqft — positioning, traffic, customer affluence) and **(b) a bigger catalog** (Kroger ~1,350 SKUs vs WDX ~1,050): a wider assortment raises store pull (one-stop shopping → higher A_s) and lets stockup baskets fill more fully. Both should **emerge from the customer mix** (affluent customers → bigger baskets → higher $/store), not be dialed by square footage.

## A3. SKU depth

Real range **15,000–60,000+ SKUs**; FMI 2024 average **31,795 items** [SOURCED]. v2 models **~1,000–1,400 per banner ≈ 3–4% of a real store** [ASSUMPTION], justified by grocery's steep 80/20: a small high-velocity core carries nearly all volume; the long tail is near-zero-velocity slow-movers.

## A4. Department sales mix — the demand backbone (a VALIDATION TARGET)

[SOURCED: FMI/Circana 2023] Fresh = 42% of sales; dry grocery 38%; frozen 5%. **Meat is the #1 traffic/sales driver, produce #2.**

| Department | Real sales share | Role |
|---|---|---|
| Dry grocery (center store) | ~38% | Canned, pasta, cereal, snacks, beverages, condiments |
| Meat & seafood | ~13% | **#1 demand driver** |
| Produce | ~11% | **#2 demand driver** |
| Dairy & eggs | ~8% | High-frequency staple |
| Frozen | ~5% | Meals, ice cream |
| Deli + prepared | ~7% | Growth area |
| Bakery | ~3% | Perimeter |
| HBC / household / GM / other | ~15% | Basket completers |

**v2 validates the generated sales mix against this table** (meat ~11–13%, produce ~11%, dairy ~8%, dry ~38%).

## A5. Category hierarchy — Department → Category → Subcategory → SKU

[SOURCED: grocery category management] Real stores run **Department → Category → Subcategory → SKU**. Grocery alone has 50+ categories; meat divides by species then cut; yogurt by style then size.

Worked examples for the model:
- **Dairy** → **Milk** → **2% reduced-fat** → *"Store Brand 2% Milk, 1 gal"*
- **Meat & Seafood** → **Poultry** → **Chicken breast** → *"Boneless Skinless Chicken Breast, per lb"*
- **Pantry** → **Pasta Sauce** → **Marinara** → *"Brand X Marinara, 24 oz"*

**All three grouping levels are required** because peer comparison happens at the *subcategory* level when exact SKUs differ across banners (meeting note: "compare at category/subcategory level"). Kroger's and Winn-Dixie's "2% milk" are different SKU records but the same *subcategory* — that's the comparison key. (Repo today effectively has 2 levels; v2 adds the department tier + real names/descriptions.)

## A6. The velocity principle — sales mix ≠ SKU-count mix

[SOURCED reasoning] SKU count follows **assortment depth**; sales follow **velocity**. Center store holds the most SKUs but produce/meat are *few SKUs at very high velocity*. The generator allocates SKUs by assortment depth, then hits the department *sales* mix via per-department velocity — not by stuffing perishables with SKUs.

Recommended ~1,300-SKU allocation (fullest banner) [ASSUMPTION, shaped to real depth]:

| Department | ~SKUs | % of SKUs | drives % of sales |
|---|---|---|---|
| Dry grocery (center store) | ~390 | 30% | ~38% |
| Snacks & candy | ~130 | 10% | (center) |
| Beverages | ~115 | 9% | (center) |
| Frozen | ~105 | 8% | ~5% |
| Meat & seafood | ~105 | 8% | **~13%** (high velocity) |
| Dairy & eggs | ~90 | 7% | ~8% |
| Produce | ~90 | 7% | **~11%** (high velocity) |
| HBC / household / paper | ~145 | 11% | ~10% |
| Bakery / deli | ~100 | 8% | ~7% |
| Baby / pet | ~40 | 3% | ~3% |

## A7. Pricing roles — the KVI basis

[SOURCED: category-management role framework] Each category gets a role — **Destination** (traffic-driver, tight price index), Routine, Seasonal, Convenience. Destination categories (milk, eggs, bread, bananas, ground beef) are KVIs priced *tight* across banners; specialty items carry margin and banner spread. → Keep the existing KVI-dampening / specialty-amplification logic; it now has a named real-world basis ("no banner cheapest on everything").

---

## A8. Merchant profiles (real-chain anchored)

### Kroger — mainstream, largest
- **Real chain:** Kroger — #1 US grocer, ~2,700 stores, ~$150B sales, large-format. Midwest/South. Mainstream, broadest assortment.
- Stores **6** · size **~50k sqft** · AUV **~$45M** · banner/yr **~$270M**
- SKUs **~1,350** (fullest) · private label **~27%** [SOURCED: Numerator] · price **at/near market**
- Demand signature: widest assortment, biggest baskets, high productivity (affluent + mainstream mix).

### Acme — premium (relative), mid
- **Real chain:** Acme Markets — Albertsons banner, ~160 stores, founded Philadelphia 1891, Northeast/Mid-Atlantic. Conventional supermarket positioned at the higher-price end. *(Realism note: Acme is a conventional chain, not a specialty-premium grocer like Whole Foods; "premium" here is the relative within-panel tier, preserved from the original design.)*
- Stores **5** · size **~48k sqft** · AUV **~$32M** · banner/yr **~$160M**
- SKUs **~1,250** · private label **~19%** [SOURCED: Albertsons, Numerator] · price **+5–8% premium**
- Demand signature: higher price index, premium/specialty lean, lower private-label reliance.

### Winn-Dixie — value, smallest
- **Real chain:** Winn-Dixie — Southeastern Grocers' flagship, Deep South/Florida, ~$10B / ~495 stores at peak (~$20M/store). Value banner.
- Stores **4** · size **~45k sqft** · AUV **~$22M** · banner/yr **~$88M**
- SKUs **~1,050** (leanest) · private label **~25%** [ASSUMPTION — ACCEPTED; value banners lean heavier, no exact SEG figure] · price **−4–6% value**
- Demand signature: value staples, heavy private label, lower sales-per-sqft productivity, KVI-driven traffic.

**Panel reconciliation:** ~$270M + $160M + $88M = **~$518M/yr**, 15 stores, ~$35M avg (just under FMI $37M — representative metro leaning slightly smaller). Window (90 days ≈ 24.7%) ≈ **$128M** ÷ ~$48 basket ≈ **~2.67M grocery transactions**.

---

## A9. Expected generation outputs (validation targets)

The generator is correct when it returns:
1. **Per-banner AUV:** Kroger > Acme > Winn-Dixie ≈ $45M / $32M / $22M, achieved via **productivity** (sales/sqft) with sqft ~constant.
2. **Department sales mix:** meat ~11–13%, produce ~11%, dairy ~8%, dry grocery ~38% (per A4).
3. **Private-label share:** ~27% / ~19% / ~25% by banner.
4. **Pricing:** no banner cheapest >70% of the time; KVI spread tight, specialty wide.
5. **Basket:** ~$45–55 AOV; traffic raised ~2.4× vs current to hit per-store AUV.
6. **Total grocery:** ~$128M window sales, ~2.67M transactions, 15 stores.

---

## A10. Sources (grocery)

- **FMI Food Industry Facts 2024** — avg supermarket 42,453 sqft; avg weekly sales $711,806 (~$37M/yr); $45.70 in-store transaction; 31,795 items; net margin 1.7%; $18.55 weekly sales/sqft.
- **FMI / Circana State of Fresh 2023** — fresh 42% of sales (meat 11%, produce 11%, dairy 8%, deli 4%, prepared 3%, bakery 3%, seafood 2%); frozen 5%; dry grocery 38%.
- **Kroger** (Statista / company) — ~$150B sales, ~2,700 stores; large-format mainstream; private label 27% (Numerator).
- **Acme Markets / Albertsons** (Acme/Albertsons, Wikipedia, Encyclopedia.com) — ~160 stores NE/Mid-Atlantic, founded 1891; Albertsons store range 35,000–107,000 sqft; Albertsons ~2,253 stores; private label 19% (Numerator).
- **Winn-Dixie / Southeastern Grocers** (Grocery Dive, Progressive Grocer, Jax Daily Record) — ~$10B / ~495 stores at peak (~$20M/store); stores ~44–50k sqft; value banner; ~130 stores post-2025 divestitures.
- **Metro density** — Charlotte-style metro ~737 grocery stores / ~3M people (~1 per 4,000); US avg ~1 per 7,350.
- **Grocery category management** (grocerynerd; CM 8-step frameworks) — Department→Category→Subcategory→SKU; 50+ grocery categories; category roles Destination/Routine/Seasonal/Convenience with tight price index on Destination/KVI.
- **Centric / industry** — average store 15,000–60,000+ SKUs.

---

## A11. Metro geography & grocery store placement (LOCKED)

**The two dials.** Each zone carries **affluence** (→ basket size, dollars per trip) and **residential weight** (→ footprint, share of trips originating there). They do different jobs and must not be conflated.

| Zone | Affluence | Resid. weight | Character |
|---|---|---|---|
| Dilworth | 1.45 | 0.12 | Most affluent |
| Ballantyne | 1.40 | 0.14 | Affluent, populous |
| Center City | 1.15 | 0.08 | Urban core (dense, few residents) |
| NoDa | 1.10 | 0.10 | Gentrifying |
| Matthews | 1.00 | 0.19 | Suburban anchor — most residents |
| Cabarrus Edge | 0.90 | 0.08 | Low-density exurb |
| University City | 0.80 | 0.13 | Student / mid |
| Eastway | 0.75 | 0.16 | Dense working-class |

**Store placement (15 grocery stores)** — premium clusters affluent, value concentrates working-class, mainstream spans broadly [ASSUMPTION, matches real chain strategy]:

| Zone | Kroger | Acme | Winn-Dixie |
|---|---|---|---|
| Dilworth (1.45) | ● | ● | |
| Ballantyne (1.40) | ● | ● | |
| Center City (1.15) | ● | ● | |
| NoDa (1.10) | ● | ● | |
| Matthews (1.00) | ● | ● | ● |
| Cabarrus Edge (0.90) | | | ● |
| University City (0.80) | ● | | ● |
| Eastway (0.75) | | | ● |
| **Total** | **6** | **5** | **4** |

- Acme: 5 most affluent zones, skips the 3 poorest (pure premium clustering).
- Kroger: 4 affluent + Matthews + University City (mainstream breadth), skips only the 2 poorest.
- Winn-Dixie: 3 poorest zones + Matthews hedge (value stronghold).
- **Overlaps:** Kroger vs Acme head-to-head in 5 affluent zones; Matthews is the only all-three zone. These overlaps power peer comparison.

**Footprint & volume mechanism.** Store choice = gravity `P(s|z) ∝ A_s / (dist + d0)^β` (grocery β≈2.0, proximity dominates) combined with banner loyalty. A store's **footprint** = nearby residential weight × proximity × A_s × loyalty; its **$ volume** = footprint × basket, basket scaling with the affluence of customers who reach it.

**Footprint and revenue diverge — this is the point:**
- **Busiest stores:** Matthews (all three banners, weight 0.19) and Winn-Dixie–Eastway (weight 0.16).
- **Highest-revenue stores:** Kroger/Acme in Dilworth/Ballantyne — affluent baskets make the dollars at moderate footprint.
- **Winn-Dixie signature:** high traffic, small baskets → ~$22M via volume of trips, not basket (low sales/sqft) — authentic value economics.
- **Center City:** few residents (0.08); relies on urban density/daytime traffic, not residential pull.

**Calibration:** banner A_s set Kroger > Acme > Winn-Dixie so placement + affluence + loyalty land per-store AUVs at ~$45M / $32M / $22M. Productivity differentiation **emerges** from customer mix (affluent baskets + banner pull), not from store size — consistent with latent-first.

---

## A12. Catalog architecture & SKU generation (LOCKED)

**Two-layer model — each merchant has its OWN unique SKU records.**
- **Hidden canonical layer (`canonical_id`)** — generation scaffold marking the *same product concept* across banners (e.g., "2% milk, 1 gal"). **Never emitted** to observable data, lake, or agents. Exists only to (a) coordinate pricing so "the same milk" is comparable across banners (KVI logic), and (b) serve as the grading/validation key.
- **Observable per-banner SKU layer** — each merchant gets its **own `sku_code`, own product name, own category labels**. Kroger's and Winn-Dixie's "2% milk" are *different SKU records*. Mirrors reality: no shared catalog; each merchant has its own item master.
- **Consequence:** peer comparison happens at the shared **subcategory** level ("2% milk"), never SKU-to-SKU. The hidden canonical layer maps each banner's unique SKUs onto the common subcategory so comparison is clean.

**Three product tiers (within each banner's assortment):**
1. **Shared-core (national brands)** — same canonical concept carried by all banners. The peer-price backbone; directly comparable.
2. **Private label** — each banner's own store brand; **distinct SKU records** (not just a flag) **[LOCKED]**, priced below national brand, comparable at subcategory level. Share ~27% Kroger / ~19% Acme / ~25% Winn-Dixie. Private-label share becomes an *emergent purchasing outcome* (what customers pick), not a pre-assigned attribute.
3. **Banner-unique tail** — items only one banner carries (regional/specialty). Own-history only, no peer — gives the agent a legitimate "no peer comparison available."

**Generation method — curated-combinatorial (no AI-slop):**
- Humans author small **controlled vocabularies** per subcategory (types × sizes × brand tiers), e.g. milk: {whole, 2%, skim, lactose-free} × {half-gal, gallon} × {national, store brand}.
- Engine **deterministically combines** into real names + templated descriptions: `"{brand} {type} Milk, {size}"` → *"Store Brand 2% Milk, 1 gal."*
- Fully deterministic (same seed → same catalog), auditable, zero free-text LLM generation.
- Each banner draws its carried assortment from canonical: **Kroger fullest (~1,350), Winn-Dixie leanest (~1,050)**, trimming the specialty tail (real value-banner pattern).

**Three taxonomy layers [LOCKED]:**
1. **Merchant-specific** (observable, per-merchant) — each merchant's own department/category/subcategory labels + own product name. Diverges across merchants (real life). The raw material a terminal emits.
2. **Functional** (observable, shared) — normalized department/category/subcategory every product maps onto. The **comparison key**; conceptually "the normalized layer Verifone builds." Peer comparison rides this.
3. **Canonical** (hidden, `canonical_map.csv`) — exact-product identity; never emitted; pricing coordination + validation only; the finest grain a normalization engine would recover.

**`products.csv` columns (observable):**
`segment, banner, sku_code, product_name, description, brand, private_label, size, merchant_department, merchant_category, merchant_subcategory, functional_department, functional_category, functional_subcategory, shelf_price`

Example (Kroger 2% milk): `grocery, KRG, KRG-DR-MLK-0021, "Kroger 2% Reduced Fat Milk, 1 gal", "...", Kroger, TRUE, "1 gal", Dairy, Milk, Reduced Fat Milk, Dairy & Eggs, Milk, "2% Reduced-Fat Milk", 3.79`

**`canonical_map.csv` (hidden):** `banner, sku_code, canonical_id` → e.g. `KRG, KRG-DR-MLK-0021, GRO.MILK.2PCT.GAL.PL`

**Grocery divergence example** (merchant labels differ; functional lines up):
| banner | product_name | merchant_dept | merchant_subcat | functional_dept | functional_subcat |
|---|---|---|---|---|---|
| KRG | Kroger 2% Reduced Fat Milk, 1 gal | Dairy | Reduced Fat Milk | Dairy & Eggs | 2% Reduced-Fat Milk |
| ACM | Lucerne 2% Milk, 1 gal | Dairy & Eggs | White Milk | Dairy & Eggs | 2% Reduced-Fat Milk |
| WDX | SE Grocers 2% Milk, 1 gal | Refrigerated | Milk | Dairy & Eggs | 2% Reduced-Fat Milk |

**QSR example** (functional_category comparable; subcategory differs for entrées, shared for nuggets):
| banner | product_name | merchant_category | functional_category | functional_subcat |
|---|---|---|---|---|
| TBL | Crunchwrap Supreme | Specialties | Entrée | Wrap/Handheld |
| BKG | Whopper | Flame-Grilled Burgers | Entrée | Burger |
| CFA | Original Chicken Sandwich | Sandwiches | Entrée | Chicken Sandwich |
| TBL | Crispy Chicken Nuggets | Chicken | Chicken | Chicken Nuggets |
| BKG | 8 Pc Chicken Nuggets | Chicken & Fish | Chicken | Chicken Nuggets |
| CFA | 8-ct Nuggets | Nuggets & Strips | Chicken | Chicken Nuggets |

**Scope note (v2) — the whole clean catalog is an *enrichment*, not merchant-provided.** As a terminal/payments company, Verifone captures only raw line data (`sku_code` + short/truncated description + price/qty) plus free merchant/store identity and segment (MCC). It does **not** receive any merchant's clean item master — own or peer — because the merchant would have to provide it and we assume they won't. So the clean catalog here (real names, merchant labels, AND functional taxonomy) is the *assumed-normalized* layer standing in for the deferred enrichment. Themes 1-4 provide it as-if solved, for **own-data and peer alike**. **Theme 5 removes it for own AND peer** (own-data not exempt), deriving names/categories/functional from raw capture. Only merchant/store identity + segment stay free → store- and segment-level analysis always available; product-level structure must be derived. This pass keeps names clean (no raw strings yet).

**No UPC / shared key.** The terminal captures the **merchant's own sku_code + description string** — not a normalized cross-merchant UPC. So all peer comparison happens at the **subcategory** level; the hidden `canonical_id` is the validation ground truth only (never used at query time), and is exactly what a future normalization engine would try to recover.

**Catalog is a static committed artifact [LOCKED].** The catalog is reference data (item master), not simulation output — so it's **generated once and committed**, and the transaction generator reads it as a build input (not rebuilt each run). Strengthens determinism (no authoring drift), makes the catalog human-reviewable, decouples authoring from the pipeline. Two artifacts to preserve the hidden/observable split:
- **`products.csv`** (observable, reviewable): `banner, sku_code, department, category, subcategory, product_name, description, brand, private_label, shelf_price`. **No `canonical_id`.** Read by dashboard / agents / lake.
- **`canonical_map.csv`** (hidden): `sku_code → canonical_id`, stored in the eval/answer-key area, never read by lake or agents.

Format: **CSV as source of truth** (spreadsheet-reviewable, git-diffable), optionally converted to Parquet at load. Tiny (~3,900 rows, <1 MB) → commits directly to the repo, **no HF storage concern** (unlike transactions, which stay regenerated-in-container). Authoring script (curated-combinatorial) remains the reproducible source; changing the catalog = re-run + re-commit. **Shelf price** in the catalog = base anchor × banner positioning × private-label factor; dynamic modifiers (zone, time drift, noise) apply at transaction time.

### Worked example — Category: Pantry · Subcategory: Oatmeal

Hidden canonical (never emitted): `PANTRY.OATMEAL.QUAKER_OF_42` (national), `PANTRY.OATMEAL.STOREBRAND_OF_42` (private label).

*National brand (all three carry — same canonical):*
| Merchant | sku_code | product_name | PL | price |
|---|---|---|---|---|
| Kroger | KRG-PN-OAT-0142 | Quaker Oats Old Fashioned 42 oz | No | $6.49 |
| Acme | 0044711-23 | Quaker Oats Old Fashioned 42 oz | No | $6.99 |
| Winn-Dixie | WDX118840 | Quaker Oats Old Fashioned 42 oz | No | $6.29 |

→ different sku_code (own item master / code format) + different price; same product via hidden canonical.

*Private label (same canonical concept, names differ per brand):*
| Merchant | sku_code | product_name | PL | price |
|---|---|---|---|---|
| Kroger | KRG-PN-OAT-0143 | Kroger Old Fashioned Oats 42 oz | Yes | $4.79 |
| Acme | 0044711-40 | Signature SELECT Old Fashioned Oats 42 oz | Yes | $5.29 |
| Winn-Dixie | WDX118857 | SE Grocers Old Fashioned Oats 42 oz | Yes | $4.29 |

→ name-match fails; comparable only at subcategory level.

*Banner-unique tail (Acme only → no peer):*
| Acme | 0044711-55 | Bob's Red Mill Steel Cut Oats 24 oz | No | $7.99 |

**Overlap tiers visible in one subcategory:** shared-core (Quaker, all 3, strongest comparison) · private-label (all 3, subcategory-level only) · tail (Acme-only, no peer). High-overlap subcategories = staples (milk, eggs, bread, oatmeal, soda, ground beef, bananas); low/no overlap = Acme premium/organic tail, Winn-Dixie deep-value tail.

## A13. Category transaction mix by merchant (VALIDATION TARGET — emergent)

Category mix must **emerge from customer affluence mix** (latent-first), not a per-banner dial. Affluent shoppers → more fresh/premium/prepared; value shoppers → more center-store staples + private label.

| Department / tier | Kroger (mainstream) | Acme (premium/affluent) | Winn-Dixie (value/working-class) |
|---|---|---|---|
| Premium & specialty (organic, deli, prepared, bakery) | ~average | over-indexes | under-indexes |
| Fresh — produce & meat | ~average (high volume) | over on $ (premium cuts, organic) | average units (value cuts) |
| Center-store staples (canned, dry, pasta, rice) | ~average | under-indexes | over-indexes |
| Private label (unit share) | ~27% | ~19% (under) | ~25%+ (over) |
| Large-pack / value items | some | little | over-indexes |

- **Kroger:** "everything store" — high absolute volume across all; mix ≈ industry average.
- **Acme:** affluent customers → more premium produce, specialty cheese, prepared, bakery, organic, wine; lowest private label; high $/basket in fresh.
- **Winn-Dixie:** working-class customers → center-store staples, value meat, staple dairy/bread, highest private label; high transaction counts at low $/line.

Ties to A11: Acme's fresh/premium = high **dollars per basket**; Winn-Dixie's staples = high **transaction counts** at low dollars — same customer-mix mechanism driving both store footprint and category movement.

---

# PART B — QSR SEGMENT (Taco Bell / Burger King / Chick-fil-A)

Real chains; menus pulled from each chain's site. QSR items are **not comparable item-to-item** — comparison is at functional-category / daypart / attach level.

## B1. Unit count & placement (LOCKED)

**Anchor.** A ~3M metro has ~1,500+ QSR units; national brands run dozens each (TB/BK ~50–60, CFA ~20–25). Panel samples **Taco Bell 9 / Burger King 8 / Chick-fil-A 6 = 23 units** [ASSUMPTION] — count ordering mirrors real (TB≈BK > CFA units), but CFA's 6 out-earn the other 17 combined. Placement follows **traffic & daytime population, not affluence**, with brand skews:

| Zone | Affl | TB (9) | BK (8) | CFA (6) |
|---|---|---|---|---|
| Dilworth | 1.45 | – | – | 1 |
| Ballantyne | 1.40 | – | 1 | 1 |
| Center City | 1.15 | 1 | 1 | – |
| NoDa | 1.10 | 1 | 1 | 1 |
| Matthews | 1.00 | 2 | 2 | 2 |
| Cabarrus Edge | 0.90 | 1 | 1 | – |
| University City | 0.80 | 2 | 1 | 1 |
| Eastway | 0.75 | 2 | 1 | – |

- **CFA:** affluent/suburban-family + campus; skips dense-working/exurb.
- **TB:** student/young/value/high-traffic (University City, Eastway, Matthews); skips affluent low-density.
- **BK:** broad value/roadside; everywhere but most affluent.
- **Matthews** (highest pop) = all three ×2, the QSR battleground.

## B2. Per-unit economics & dayparts (LOCKED)

| | Chick-fil-A | Taco Bell | Burger King |
|---|---|---|---|
| Units | 6 | 9 | 8 |
| AUV | ~$8.0M [SOURCED: QSR 50 blended $7.45M / FDD standalone $9.4M] | ~$2.1M [SOURCED: QSR 50] | ~$1.6M [SOURCED: Technomic] |
| Check (avg) | ~$13.50 [SOURCED-ish: meal ~$13.72] | ~$8.50 [ASSUMPTION, in-range] | ~$9.50 [ASSUMPTION, in-range] |
| Banner/yr | ~$48M | ~$18.9M | ~$12.8M |
| Daypart driver | lunch+dinner; **closed Sundays (hard-zero)**; no late night | **late-night ~18% post-9pm** | **breakfast daypart** (Croissan'wich) |

Total QSR ≈ **$79.7M/yr → ~$19.7M window → ~1.76M transactions** (2–4 item baskets, ~5.3M line items).

## B3. Menu size & catalog structure

Menu depth ≈ near-full fidelity (real QSR menus 50–150 items): **TB ~70–90, BK ~70–90, CFA ~50–70; ~200–240 total items.** Structure: **Functional Category → chain Subcategory → Item.**

Real menus (authored directly from each chain's site — small enough that curated-combinatorial isn't needed):
- **Taco Bell:** Tacos (Crunchy, Doritos Locos, Cheesy Gordita Crunch), Burritos (Bean, Beefy 5-Layer, Grilled Cheese), Specialties (Crunchwrap Supreme, Chalupa Supreme, Mexican Pizza), Quesadillas, Nachos (BellGrande, Nacho Fries), Sides & Sweets (Cinnamon Twists, Cinnabon Delights), Drinks (Baja Blast, freezes), Breakfast, Cravings Boxes.
- **Burger King:** Burgers (Whopper, Bacon King, Whopper Jr., Impossible Whopper), Chicken & Fish (Original Chicken Sandwich, Royal Crispy, Nuggets, Chicken Fries, Big Fish), Sides (fries, onion rings), Breakfast (Croissan'wich, French Toast Sticks, Hash Browns), Drinks & Coffee, Desserts (soft serve, shakes, Hershey's Pie), Meals.
- **Chick-fil-A:** Sandwiches (Original, Deluxe, Spicy, Grilled), Nuggets & Strips, Salads (Cobb, Spicy Southwest, Market), Sides (Waffle Fries, Mac & Cheese, Fruit Cup), Breakfast (Chicken Biscuit, Egg White Grill, Hash Brown Scramble Burrito), Treats (Icedream, shakes, cookies), Beverages (Lemonade, Sunjoy, tea), Sauces.

## B4. Shared functional taxonomy — the comparison backbone (LOCKED)

| Functional category | Taco Bell | Burger King | Chick-fil-A |
|---|---|---|---|
| Entrée / main | tacos, burritos, specialties | burgers | chicken sandwiches |
| Chicken (nuggets/strips) | Chicken Nuggets | Nuggets, Chicken Fries | Nuggets, Strips |
| Sides | Nacho Fries, nachos | fries, onion rings | Waffle Fries, Mac & Cheese |
| Beverages | Baja Blast, freezes | soft drinks, coffee | lemonade, tea, shakes |
| Breakfast | Breakfast Crunchwrap | Croissan'wich, French Toast Sticks | Chicken Biscuit, Egg White Grill |
| Desserts / treats | Cinnamon Twists, Cinnabon Delights | soft serve, shakes, pies | Icedream, cookies, shakes |
| Combos | Cravings Boxes | Whopper Meal | combo meals |

**Genuinely shared subcategories (cleanest comparison):** Chicken Nuggets (all three), Fries, Fountain soft drinks, Breakfast, Shakes/frozen treats. Everything else is **banner-unique** — opposite of grocery. So QSR peer comparison = **average check, combo-attach rate, beverage attach, daypart mix, functional-category share** — never item-to-item.

**Catalog model (same two-layer as grocery):** per-chain unique items (own codes/names) + hidden canonical mapping each item → shared functional category/subcategory. Almost no shared-core (a Whopper exists only at BK); the exception is generic **fountain drinks** (Coca-Cola products appear across chains). Peer comparison rides the functional taxonomy, not item identity.

## B5. Combo-attach affinity (LOCKED — new; repo has none for QSR)

QSR baskets are driven by **combo-attach: entrée → side → drink**, with the combo bundle the dominant pattern. This is the QSR equivalent of grocery PASTA→SAUCE, and makes attach-rate a comparable peer metric. Add per-chain combo-attach + beverage-attach rates.

## B6. Catalog as static artifact (QSR)

Same pattern as grocery: menus **authored once and committed** (QSR rows in `products.csv`; QSR item → functional-category in `canonical_map.csv`). Tiny (~200–240 items). Directly authored from real menus (no combinatorial generation).

## B7. Expected generation outputs (validation targets)

1. **Per-banner volume:** CFA ~$48M > TB ~$18.9M > BK ~$12.8M; **CFA 6 units > TB 9 + BK 8 combined.**
2. **Checks:** CFA ~$13.50 > BK ~$9.50 > TB ~$8.50.
3. **Dayparts:** CFA Sunday = hard-zero; TB ~18% post-9pm; BK breakfast daypart present.
4. **Combo-attach:** entrée→side→drink attach materializes; beverage attach comparable across chains.
5. **Total QSR:** ~$19.7M window, ~1.76M transactions, 23 units.

---

# PART C — POPULATION & TRANSACTIONS (LOCKED — core logic, no anomalies)

Principle: **latent-first, observable-derived.** Build hidden shopper truths first, derive the observable transaction from them, so correlations emerge causally.

## C1. Card universe
**~155,000 cards**, one shared universe. Participation: **56k grocery-only (36%) · 28k QSR-only (18%) · 71k both (46%)** [ASSUMPTION — tuned]. Grocery-active 127k; QSR-active 99k. The 46% "both" group powers cross-segment analysis.

## C2. Durable latents (built in order)
- **home_zone** — drawn from zone residential weights (more cards in Matthews 0.19, Eastway 0.16). Drives footprint.
- **affluence** — Gaussian around home-zone affluence + within-zone spread. Master latent → basket value, PL choice, payment.
- **grocery loyalty** — primary banner + type (loyalist/splitter/three-chain); primary leans by affluence+zone (affluent→KRG/ACM, value→WDX) → banner customer mixes emerge.
- **QSR preference** — brand leanings (CFA family/affluent, TB young/value).
- **payment identity** — credit-vs-debit logistic in affluence; network; wallet enrollment [SOURCED: Fed 2025 Diary]. Tender follows wealth, not merchant.
- **staples** — few deterministic always-buy SKUs per card.

## C3. Timing (when)
- **Day of week** — grocery weekend skew; QSR Fri–Sat peak; TB late-night tail.
- **Daypart** — grocery midday+evening; QSR lunch/dinner; **BK breakfast**; **TB ~18% post-9pm**; **CFA closed Sundays (hard-zero)**, no late-night.
- **Pay-cycle** — early/mid-month payday bumps (~+17%).
- **Seasonality [NEW]** — gentle spring upward drift across Mar–May (food-at-home recovers through spring, [SOURCED: USDA ERS]); concentrated spikes: **Easter (Apr 5)** baking/ham/eggs, **Memorial Day (May 25)** grilling meat/snacks/beverages; light Cinco de Mayo / Mother's Day. Topline swing modest (~5% [SOURCED]); spikes concentrate in relevant categories.

## C4. Store choice (where) — gravity
**P(store s | zone z) ∝ A_s / (distance + d₀)^β**
- **A_s** banner-differentiated (KRG>ACM>WDX; CFA>BK) — productivity lever, not size.
- **β** per segment (grocery ~2.0, QSR ~2.2). Proximity dominates.
- Grocery: gravity × **loyalty** (loyalist concentrates at primary). QSR: gravity × **brand preference**.

## C5. Baskets (what & how much)
1. **Mission** sets category mix + size range (grocery: weekly stockup / meal tonight / fill-in / breakfast; QSR: combo / snack / group / breakfast).
2. **Basket size** — triangular per mission (grocery stockup 15–22, fill-in 4–7, quick 2–3; QSR 2–4). Produces heavy tail.
3. **Selection** shaped by:
   - **Affinity** — grocery matrix (PASTA→SAUCE, MILK→CEREAL, CHIPS→SALSA, BREAD→BUTTER, DIAPERS→WIPES); QSR combo-attach (entrée→side→drink).
   - **National vs PL** by affluence → **PL share emerges** (~27/19/25%).
   - **Staples** included with high probability.
- Emergent: **department sales mix** (meat ~13%, produce ~11%, dry ~38%) and **by-merchant skew** (A13) fall out of affluence + missions.

## C6. Payment
Entry mode / wallet / connectivity from payment identity + segment baseline (wallet→contactless; QSR→more cellular). Mix ~53% contactless / ~17% wallet [Fed Diary].

## C7. Realized price [SIMPLIFIED]
Realized line price = **catalog `shelf_price`, unchanged.** No zone effect, no time drift, no line noise. Cross-banner differentiation (positioning, PL discount, KVI-tight/specialty-wide) is fully baked into the shelf price, so "no banner cheapest on everything" still holds; realized price is just simpler and fully deterministic.

## C8. Worked transaction (end to end)
Eastway card (affluence ~0.78, debit, WDX loyalist, regular): Saturday late-morning mid-May (weekend + payday + spring lift trip) → gravity+loyalty → **Winn-Dixie Eastway** → mission *weekly stockup* → ~16 items, center-store-heavy; pasta→sauce fires, milk→cereal fires, staples included, picks SE Grocers PL where available → debit/contactless/wifi → each line priced at the WDX shelf price (flat) → emits 1 transaction (~$52) + ~16 line-item rows (each with functional_subcategory for comparison).

## C9. Volume reconciliation (validation targets)

| Level | Target | Emerges from |
|---|---|---|
| Per grocery card | ~20 trips/90d (~1.6/wk), ~$50 basket | intensity tiers + missions |
| Per QSR card | ~18 visits/90d (~1.4/wk) | intensity tiers |
| Per grocery store | KRG ~$40M · ACM ~$32M · WDX ~$22M /yr | A_s × placement × loyalty × affluence baskets |
| Per QSR unit | CFA ~$8M · TB ~$2.1M · BK ~$1.6M /yr | brand A_s × placement × check |
| Per neighborhood | footprint ∝ residential weight; $ ∝ affluence | Matthews/Eastway busiest; Dilworth/Ballantyne highest $/basket |
| **Total (90-day window)** | **~155k cards · ~4.3M txns · ~33M line items · ~$148M** | grocery $128M + QSR $19.7M |

**Two independent levers:** total scale = population × trip budgets (calibrated to per-store AUV); differentiation = A_s + affluence mix. Nothing dialed at output — all emerges from latents.

**Excluded from this pass:** planted anomalies (A1–A3) and promotions (open decision) — core transaction logic only.

---

# APPENDIX D — TUNED ASSUMPTIONS (recommended values)

Every soft parameter with a recommended value. [SOURCED] = real anchor; [TUNED] = modeling choice calibrated to hit AUV / realistic range.

| Parameter | Recommended | Tag / basis |
|---|---|---|
| Cards | 155,000 | [TUNED] derived from volume reconciliation |
| Participation split | grocery-only 36% / QSR-only 18% / both 46% | [TUNED] high cross-category card use realistic; 46% "both" powers cross-segment |
| Grocery trips | ~1.5/wk blended (core 20% ≈2.9 · regular 45% ≈1.5 · occasional 35% ≈0.6, per wk) | [SOURCED range] ~1.5–2/wk; tiers calibrated to AUV |
| QSR visits | ~1.4/wk blended (heavy 12% ≈3 · regular 42% ≈1.4 · occasional 46% ≈0.6) | [TUNED] calibrated to AUV |
| Grocery AOV | ~$48–50 blended; per banner emerges (KRG/ACM ~$52–55, WDX ~$42) | [SOURCED] FMI $45.70; per-banner spread emerges from affluence |
| Grocery basket items | stockup 15–22 · fill-in 4–7 · quick 2–3 (blended ~11) | [TUNED] realistic trip-type mix, heavy tail |
| QSR check | CFA ~$13.50 · BK ~$9.50 · TB ~$8.50 | CFA [SOURCED]; TB/BK [TUNED] in-range |
| QSR items | 2–4 (~3: entrée+side+drink) | [TUNED] combo structure |
| SKU counts (assortment lever) | Kroger 1,350 · Acme 1,250 · Winn-Dixie 1,050 | [TUNED] now a volume lever (pull + basket fill) |
| Store attractiveness A_s | grocery KRG 1.30 / ACM 1.00 / WDX 0.80 · QSR CFA 1.40 / TB 1.00 / BK 0.90 | [TUNED] to hit AUV; reflects brand pull + assortment |
| Distance decay β | grocery 2.0 · QSR 2.2 | [SOURCED] standard retail gravity |
| Loyalty types | loyalist 55% / splitter 30% / three-chain 12% / lapsed 3% | [TUNED] → ~76% concentration |
| Primary banner | ~KRG 40% / ACM 33% / WDX 27% (emerges from affluence/zone) | [TUNED] prior; emerges |
| Private label share | KRG 27% / ACM 19% / WDX 25% | KRG/ACM [SOURCED]; WDX [ACCEPTED] |
| Payment | contactless ~53% · wallet ~17% · credit-vs-debit logistic in affluence | [SOURCED] Fed 2025 Diary |
| Seasonality | spring drift +~4% across window · Easter wk +~25% (baking/ham/eggs) · Memorial Day wk +~30% (grilling/snacks/bev) · light Cinco/Mother's Day | [SOURCED] USDA spring recovery + holiday category spikes |

**Reconciliation check:** grocery ~127k × ~1.5/wk × 12.86 wk × ~$50 ≈ $490M/yr (target $488–518M ✓); QSR ~99k × ~1.4/wk × ~$11 ≈ $79M/yr (target $79.7M ✓); A_s × store-count weighting reproduces ~49/33/18 grocery dollar split (KRG/ACM/WDX).
