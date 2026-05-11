# Data Specification

The full spec for synthetic data generation. This file is the source of truth for `src/generate/parameters.py` and the generation modules. If you change a parameter here, change it in code; if you change it in code, change it here.

> **v2.5 transition note** — `V2_5_DATA_DESIGN.md` is the locked target for the data layer. The refactor lands in phases tracked by `docs/V2_5_RECONCILIATION.md`. This document is being updated incrementally:
> - **Phase 1 (this update):** §1 (what gets generated), §3 (per-merchant configs at the panel-shape level), §11 (cross-merchant join key) reflect the v2.5 panel.
> - **Phase 2:** §8 (catalog architecture).
> - **Phase 3:** §3 transaction-field expansion + new `tenant_promotions` section + tax model.
> - **Phase 4:** final field-level pass (Layer 4 generator flow).
>
> Sections not yet refreshed still describe the v2 implementation. When `V2_5_DATA_DESIGN.md` and this file disagree, the design doc wins.

---

## 1. What gets generated

A 90-day shopping panel for 10,000 fictional customers across five merchants in a single fictional metro modeled on Charlotte, NC:

- **Kroger** — grocery, MCC 5411, 30 stores
- **Acme** — grocery, MCC 5411, 25 stores
- **Winn-Dixie** — grocery, MCC 5411, 20 stores
- **Taco Bell** — QSR, MCC 5814, 40 stores
- **TJ Maxx** — off-price retail, MCC 5651, 8 stores

Outputs (CSVs in `data/raw/`):
- `customers.csv` — shared 10,000-row customer panel. Contains `customer_id` (16-char SHA-256 hash, generated directly by the customer generator), `home_zip5` (Charlotte panel ZIP), `behavioral_segment`, `grocer_affinity_type`, `primary_grocer`, `secondary_grocer`, `primary_card_type`, `has_mobile_wallet`, `signup_date`. **No PII** — no names, emails, raw PANs, or demographic bands.
- `merchants.csv` — five-row dimension.
- `stores.csv` — 123 stores across all five merchants, all in the Charlotte metro. Each store has `neighborhood`, `metro_region` (`urban_core` / `inner_suburbs` / `outer_suburbs`), and `latitude`/`longitude` (ZIP centroid + ±0.02° jitter).
- `products.csv` — combined catalog. KRG/ACM/WDX use a shared base catalog with per-grocer overlays and two-tier pricing.
- `promotions.csv` — `tenant_promotions` rows, including the three pinned pasta-promo campaigns that drive the §9 anomaly.
- `transactions.csv` — references `customer_id` directly (no PAN). Volume scales with the 10K panel × 5 merchants — see test bounds in `tests/test_generation.py`.
- `transaction_items.csv` — line items.

Generation runs end-to-end in ~80 seconds at default settings. The CSVs in `data/raw/` are loaded directly into SQLite by `src/db/seed.py`; there is no separate anonymization stage. The lake is virtual — computed at query time from the tenant tables by `src/lake/views.py`.

---

## 2. Global parameters

```python
# src/generate/parameters.py

# Panel size and time window
N_CUSTOMERS = 10_000
START_DATE  = date(2026, 3, 1)
END_DATE    = date(2026, 5, 29)
DAYS        = 90

# Reproducibility
RANDOM_SEED = 42

# Hash secret — consumed by src/generate/customers.py to derive
# customer_id from a never-persisted synthetic PAN.
HASH_SECRET = "demo-only-not-a-real-secret"
```

Layer 4 generation tables (trip frequency, active-weeks variance, basket archetypes, per-category quantity distributions, payment generation, etc.) live in `parameters.py` as well. See `V2_5_DATA_DESIGN.md` Steps 4a–4l for the spec — the design doc is the source of truth for generator behavior; this file describes outputs.

---

## 3. Per-merchant configs

Phase 4 simplified the per-merchant config to the v2.5 essentials. The Layer 4 generator now owns trip frequency, day-of-week shaping, basket sizing, and category weighting — those are no longer per-merchant config knobs.

```python
MERCHANT_CONFIGS = {
    "kroger":     {"merchant_id": "KRG", "name": "Kroger",     "segment": "grocery",          "mcc": "5411", "n_stores": 30, "payment_mix": {"credit": 0.65, "debit": 0.35}},
    "acme":       {"merchant_id": "ACM", "name": "Acme",       "segment": "grocery",          "mcc": "5411", "n_stores": 25, "payment_mix": {"credit": 0.65, "debit": 0.35}},
    "winn_dixie": {"merchant_id": "WDX", "name": "Winn-Dixie", "segment": "grocery",          "mcc": "5411", "n_stores": 20, "payment_mix": {"credit": 0.65, "debit": 0.35}},
    "taco_bell":  {"merchant_id": "TBL", "name": "Taco Bell",  "segment": "qsr",              "mcc": "5814", "n_stores": 40, "payment_mix": {"credit": 0.55, "debit": 0.45}},
    "tjmaxx":     {"merchant_id": "TJX", "name": "TJ Maxx",    "segment": "off_price_retail", "mcc": "5651", "n_stores": 8,  "payment_mix": {"credit": 0.74, "debit": 0.26}},
}
```

`payment_mix` is **credit + debit only** (Layer 1: "captured rails: credit and debit only" — no EBT, no cash, no declines).

---

## 4. Volume math

Phase 4 Layer 4 generator targets per `V2_5_DATA_DESIGN.md` Step 6 sanity checks:

| Quantity | Target | Notes |
|---|---|---|
| Transactions | 180,000–250,000 | Drives 90-day volume |
| Line items | 2.0M–3.0M | Avg qty per line ~1.4–1.6 |
| Customers in panel | 10,000 | Exact |
| Stores in panel | 123 | Exact (KRG 30 + ACM 25 + WDX 20 + TBL 40 + TJX 8) |
| Distinct promotions | ~73 | KRG 25 / ACM 20 / WDX 18 / TBL 6 / TJX 4 |
| SKUs in catalog | ~3,240 | KRG 1,112 + ACM 1,000 + WDX 877 + TBL 60 + TJX 200 |

Trip frequency is per-customer, driven by Step 4a:

| Segment + affinity | Grocery trips per 90 days |
|---|---|
| Filler + Loyalist | 18–24 |
| Filler + Splitter | 20–28 |
| Filler + Three-chain | 22–30 |
| Stocker + Loyalist | 8–14 |
| Stocker + Splitter | 10–16 |
| Stocker + Three-chain | 12–18 |
| Lapsed (any) | 50% have 0 trips; 30% have 1–2 clustered; 20% have 1–2 spread out |

QSR: 50% of customers have 6–15 Taco Bell trips, 50% have 0–3 (independent of grocer affinity). Retail: 30% of customers have 2–6 TJ Maxx trips, 70% have 0–1. Trip counts are skewed toward the lower bound of each range (triangular with mode at lo) — this lands the panel total inside the 180k–250k target; uniform sampling overshoots ~5%.

**Storage:** SQLite `payments.db` ≈ 350 MB. **Generation runtime:** ~100s end-to-end at default seed.

---

## 5. Time window and seasonality

The window is **90 days, March 1 → May 29, 2026** (Layer 2). It deliberately covers Easter (April 5) and Memorial Day (May 25).

### Captured patterns

- **Day-of-week** (Layer 4 Step 4g). Grocery + retail peak Saturday/Sunday; QSR peaks Friday/Saturday.
- **Time-of-day** (Step 4g). Grocery peaks 10am–2pm and 5–7pm; QSR peaks 12–1pm and 7–9pm; retail peaks 11am–3pm and 4–7pm.
- **Per-customer active-weeks variance** (Step 4b). Each customer is active in 9–12 of the 13 weeks; trips are bursty across active weeks.
- **Pay-cycle bumps** (Step 4g). ~1.15× multiplier on the 1st–3rd and 15th–17th of each month, layered on top of active-weeks variance.
- **Promotional discounts** driven by the `tenant_promotions` table: 73 distinct campaigns across the panel, each covering multiple SKUs over a date range.

### NOT captured (document honestly in `ARCHITECTURE.md`)

- **Annual seasonality** — pumpkin spike, holiday baking, summer BBQ. Needs 1–2 years.
- **Weather effects** — fake weather correlations are demo theater, not insight.
- **Macroeconomic drift** — inflation, tariffs.

The honest framing: the demo simulates **intra-week and intra-month rhythms** plus **promotional events**, not annual seasons. Enough for genuine pattern-finding and for "what happened on day X" questions to land.

---

## 6. Customer behavior model

A single customer's intrinsic behavior is set once (in `customers.py`) and applied across every merchant they visit.

### Behavioral segments

| Segment | Share | Behavior |
|---|---|---|
| Filler | ~70% | Smaller, more frequent baskets. More fill-in archetypes. Lower per-trip ticket. |
| Stocker | ~30% | Larger, less frequent baskets. More stockup archetypes. Higher per-trip ticket. |

The lapsed-light cohort lives in `grocer_affinity_type = 'lapsed_light'` (not a separate behavioral segment) — those customers have very low overall trip counts per Step 4a.

### Basket sizing (Layer 4 Step 4j)

Basket size is sampled per (segment, behavioral_segment, archetype). Triangular distribution; values from `V2_5_DATA_DESIGN.md` Step 4j:

| Merchant + segment | Avg lines per basket | Range |
|---|---|---|
| Grocery + filler + fill-in | ~6 | 3–10 |
| Grocery + filler + stockup | ~14 | 8–18 |
| Grocery + filler + themed | ~10 | 5–14 |
| Grocery + stocker + fill-in | ~10 | 5–14 |
| Grocery + stocker + stockup | ~28 | 18–40 |
| Grocery + stocker + themed | ~18 | 10–25 |
| Taco Bell | ~3 | 2–5 |
| TJ Maxx | ~5 | 1–12 |

### Basket archetypes (grocery only — Layer 4 Step 4i)

For each grocery transaction the generator samples one of three archetypes. Each archetype has a different category-share weighting.

| Archetype | Share | Bias |
|---|---|---|
| Stockup | 40% | Pantry, household, dairy, meat — heavier replenishment baskets |
| Fill-in | 45% | Perishables (produce, dairy, bakery) — smaller baskets |
| Themed | 15% | BBQ / party / special-meal mix (snacks, beverages, meat) |

QSR and retail baskets are mostly homogeneous (no archetypes).

### Per-line quantity (Layer 4 Step 4k)

Quantity per line is sampled from a per-category distribution from the design's Step 4k table. E.g. `DAIRY` is 65% qty=1, 25% qty=2, 8% qty=3, 2% qty=4. Average qty across all lines lands at ~1.4–1.6.

### Customer-level traits set in `customers.py`

- `customer_id`: 16-char SHA-256 hash (stable across all merchants — **the critical invariant**)
- `home_zip5`: full 5-digit Charlotte panel ZIP
- `behavioral_segment`: `filler` or `stocker`
- `grocer_affinity_type`: `loyalist` / `splitter` / `three_chain` / `lapsed_light` (55 / 30 / 12 / 3)
- `primary_grocer`: `KRG` / `ACM` / `WDX` (proportional to grocer store count)
- `secondary_grocer`: nullable; only set for splitters and three-chain shoppers
- `primary_card_type`: `credit` / `debit` / `mixed` (no EBT in v2.5)
- `has_mobile_wallet`: 0/1
- `signup_date`: a date in the last 5 years

---

## 7. Affinity pairs

Without these, "what's bought with X?" returns noise. Wire deliberate co-purchase patterns into the basket sampler.

### Kroger (5 pairs)

| Anchor | Companion | P(companion | anchor) |
|---|---|---|
| Diapers | Infant formula | 0.45 |
| Pasta | Marinara sauce | 0.55 |
| Tortillas | Ground beef | 0.40 |
| Tortillas | Shredded cheese | 0.45 |
| Whole milk | Cereal | 0.30 |
| Coffee | Half & half | 0.40 |

### Taco Bell (2 pairs)

| Anchor | Companion | P(companion | anchor) |
|---|---|---|
| Any taco/burrito/specialty entree | Drink | 0.70 |
| Combo meal | Cinnamon Twists | 0.35 |

### TJ Maxx (2 patterns)

| Anchor | Companion | P(companion | anchor) |
|---|---|---|
| Women's apparel | Accessory (handbag/jewelry) | 0.40 |
| Kitchen towels | Other home goods (decorative) | 0.50 |

---

## 8. Catalog architecture

### Base + per-grocer overlay (Phase 2 onward)

Kroger, Acme, and Winn-Dixie share a **canonical base catalog** of ~1,112 SKUs and apply a per-grocer **overlay** specifying (a) which canonical SKUs the grocer carries and (b) per-tier price multipliers. This produces three sets of `tenant_products` rows whose `name`, `category`, and `subcategory` are identical for shared SKUs but whose `base_price` differs by tier.

Files:

- `data/catalogs/base_grocery_catalog.json` — array of canonical SKU records (`canonical_sku`, `name`, `category`, `subcategory`, `base_price`).
- `data/catalogs/overlays/{kroger,acme,winn_dixie}.json` — per-grocer `merchant_id`, `keep_fraction`, `tight_multiplier`, `loose_multiplier`, `tight_categories`, `loose_categories`, `included_canonical_skus`.
- `scripts/build_v2_5_catalogs.py` — one-off generator that produces the above four files from the existing per-category JSONs in `data/catalogs/kroger/`. Re-run only when the canonical universe or per-grocer SKU shares change deliberately.

The runtime catalog builder lives at `src/generate/catalog_grocery.py`. `catalog_acme.py` and `catalog_winn_dixie.py` are thin shims over it.

### Two-tier pricing

Per category, prices are scaled by a tier multiplier × per-SKU ±2% noise (applied at catalog-build time, not per-transaction).

| Tier | Categories | Kroger | Acme | Winn-Dixie |
|---|---|---|---|---|
| **Tight** (staples + center-store) | DAIRY, BAKERY, PRODUCE, MEAT, PANTRY, SNACKS, BEVERAGES, FROZEN | 1.00× | 1.03× | 0.97× |
| **Loose** (non-food) | HOUSEHOLD, PERSONAL, BABY, PET | 1.00× | 1.07× | 0.93× |

So an Acme staple ends up at `kroger_canonical_price × 1.03 × (1 ± 2%)`; an Acme non-food at `× 1.07 × (1 ± 2%)`. Winn-Dixie inverts. The ±2% noise is deterministic per (merchant, canonical_sku) — the catalog is reproducible across runs.

### Per-grocer SKU counts

Grocers carry overlapping but not identical subsets of the canonical universe. The smaller a grocer's overlay, the more long-tail SKUs are missing.

| Grocer | SKU count | `keep_fraction` |
|---|---|---|
| Kroger | 1,112 | 1.00 |
| Acme | 1,000 | 0.90 |
| Winn-Dixie | 877 | 0.79 |

SKU IDs are merchant-prefixed: `<MERCHANT_ID>-<CATEGORY>-<NNNN>`. The canonical number suffix (`PRODUCE-0042` → `KRG-PRODUCE-0042`, `ACM-PRODUCE-0042`, `WDX-PRODUCE-0042`) is preserved across grocers — useful when scanning data.

### Example canonical SKUs

| Category | Approx count | Examples (canonical names) |
|---|---|---|
| PRODUCE | 93 | Bananas (lb), Honeycrisp apples (3 lb bag), Romaine hearts (3-pack), Strawberries (1 lb), Avocados (4-pack) |
| DAIRY | 89 | Whole milk (gallon), 2% milk (gallon), Greek yogurt (32 oz), Sharp cheddar (8 oz block), Eggs (dozen large), Half and half (quart) |
| BAKERY | 66 | Sourdough boule, Hamburger buns (8 ct), Bagels (6 ct), Croissants (4 ct), Tortilla wraps |
| MEAT | 98 | Chicken breast boneless (lb), 80/20 ground beef, Atlantic salmon (lb), Bacon (12 oz), Pork chops (lb) |
| FROZEN | 99 | Frozen pizza pepperoni, Frozen broccoli (12 oz), Vanilla ice cream (pint), Frozen waffles (10 ct) |
| PANTRY | 202 | Spaghetti (1 lb box), Marinara sauce traditional, Peanut butter (16 oz), Olive oil (17 oz), Black beans (15 oz can) |
| SNACKS | 99 | Potato chips classic (8 oz), Chocolate sandwich cookies, Granola bars (12 ct), Roasted almonds (16 oz) |
| BEVERAGES | 105 | Cola (12-pk cans), Sparkling water lime (12-pk), Folgers ground coffee, Orange juice (89 oz) |
| HOUSEHOLD | 79 | Paper towels (6 pk), Toilet paper (12 pk), Laundry detergent (50 oz), Trash bags (13 gal, 80 ct) |
| PERSONAL | 78 | Toothpaste (6 oz), Body wash (16 oz), Shampoo (20 oz), Razor blade refills (8 ct), Deodorant |
| BABY | 60 | Diapers size 3 Pampers, Infant formula Similac Advance, Baby wipes (720 ct), Baby food pouches |
| PET | 44 | Dry dog food (30 lb), Cat litter (35 lb), Wet cat food (5.5 oz can), Dog treats |

Base prices range from < $1 (single banana) to ~$90 (30-lb dog food). Most cluster $2–$15.

### Taco Bell (60 SKUs)

SKU format: `TBL-{TYPE}-{NN}` (e.g. `TBL-BURR-03`).

| Type | Count | Examples |
|---|---|---|
| Tacos | 8 | Crunchy Taco, Soft Taco, Doritos Locos Taco, Crunchy Taco Supreme, Soft Taco Supreme |
| Burritos | 10 | Bean Burrito, Beefy 5-Layer Burrito, Burrito Supreme, Chicken Chipotle Melt, Cheesy Bean & Rice |
| Specialties | 8 | Crunchwrap Supreme, Mexican Pizza, Quesadilla, Cheesy Gordita Crunch, Chalupa Supreme |
| Combos | 6 | $5 Cravings Box, Build Your Own Cravings Box, Deluxe Cravings Box, Big Box |
| Sides | 6 | Cinnamon Twists, Chips & Cheese, Nachos, Black Beans & Rice |
| Drinks | 12 | Baja Blast Lg, Baja Blast Md, Coke Lg, Coke Md, Diet Coke Lg, Sprite Md, Iced Tea Lg, Iced Coffee, Bottled Water |
| Breakfast | 10 | Breakfast Crunchwrap, Breakfast Burrito, Hash Brown, Cinnabon Delights (2 ct), Cinnabon Delights (4 ct) |

Prices $1.29 (single taco) to $11.99 (deluxe combo). Most $2–$7.

### TJ Maxx (200 SKUs)

SKU format: `TJX-{CATEGORY3}-{NNN}` (e.g. `TJX-WOM-042`). TJ Maxx sells one-off product lots so we use generic categorical SKU names rather than specific products.

| Category | Approx count | Examples |
|---|---|---|
| Women's Apparel | 50 | Women's blouse, Women's denim, Designer dress, Athletic wear (top), Athletic wear (bottom), Sweater |
| Men's Apparel | 35 | Men's polo, Men's chinos, Designer button-down, Athletic shorts, Sweater |
| Kids | 25 | Kids' jeans, Kids' tee, Toddler dress, Kids' pajamas, Kids' jacket |
| Shoes | 25 | Athletic shoes, Women's heels, Men's casual shoes, Kids' sneakers, Boots |
| Handbags & Accessories | 20 | Designer handbag, Crossbody bag, Wallet, Belt, Sunglasses, Scarf |
| Home Goods | 30 | Throw pillow, Kitchen towel set, Picture frame, Decorative vase, Wall art, Candle |
| Beauty | 10 | Body lotion, Hand cream, Hair care set, Fragrance |
| Jewelry | 5 | Earrings, Necklace, Bracelet, Watch |

Prices $4.99 (small accessory) to $199.99 (designer handbag). Most $15–$65.

---

## 8.5 Promotions (Phase 3)

`tenant_promotions` is the per-merchant campaign table used during data generation. Each campaign covers multiple SKUs → one row per (`promo_id`, `sku`). When the basket sampler builds a line for a SKU on a date covered by an active promo at that merchant, the line gets the promo's `discount_pct` and its `promo_id` is recorded on `tenant_transaction_items.promo_id`.

### Schema

| Column | Type | Notes |
|---|---|---|
| `promo_id` | TEXT | Format: `<merchant>-PROMO-<NNNN>` (campaign id; **not** unique alone) |
| `merchant_id` | TEXT FK | References `merchants(merchant_id)` |
| `sku` | TEXT FK | References `tenant_products(sku)` — one row per affected SKU |
| `start_date` | DATE | First day promo is active (inclusive) |
| `end_date` | DATE | Last day promo is active (inclusive) |
| `discount_pct` | REAL | `0.15` = 15% off; applied as `unit_price × qty × discount_pct` |
| `promo_name` | TEXT | Human-readable; used as campaign grouping key |
| `promo_type` | TEXT | `weekly_ad` / `holiday` / `lto` / `clearance` |

Primary key: `(promo_id, sku)`.

### Volumes per merchant

| Merchant | Promos / 90 days | Source |
|---|---|---|
| Kroger | ~25 | `V2_5_DATA_DESIGN.md` Layer 3f |
| Acme | ~20 | |
| Winn-Dixie | ~18 | |
| Taco Bell | ~6 | |
| TJ Maxx | ~4 | |

Total ~73 distinct campaigns across the panel. Of those, three slots (one per grocer) are reserved for the pinned pasta-promo trio that drives the Phase 6 anomaly — see §9 for details.

### Per-segment promo-type mix

| Segment | weekly_ad | holiday | lto | clearance |
|---|---|---|---|---|
| Grocery | 55% | 15% | 20% | 10% |
| QSR | 15% | 20% | 65% | — |
| Off-price retail | — | 15% | 30% | 55% |

Implementation: `src/generate/promotions.py`.

### Lake exposure

`tenant_promotions` is **tenant-only**. The lake never exposes campaign metadata — Verifone observes applied discounts at the line item level, not merchant promo schedules. Cross-merchant promotional analytics in the lake happen via discount-pattern observation on `lake_transactions`. (Phase 5 view-builders enforce this; Phase 3 lake CSVs already exclude promo metadata.)

---

## 8.6 Tax model (Phase 3)

Per-line tax is computed at generation time as `tax = round(line_total × rate, 2)`, with `rate` looked up by category in `src/generate/tax.py`. Five-tier model per `V2_5_DATA_DESIGN.md` Layer 3e:

| Tier | Categories | Rate |
|---|---|---|
| Tax-exempt grocery | DAIRY, BAKERY, PRODUCE, MEAT, FROZEN, PANTRY | **0%** |
| Discretionary grocery | BEVERAGES, SNACKS | **4%** |
| Non-food grocery | HOUSEHOLD, PERSONAL, BABY, PET | **7%** |
| QSR food (Taco Bell vocabulary) | TACO, BURR, SPEC, COMBO, SIDE, DRINK, BFAST | **7%** |
| Retail (TJ Maxx vocabulary) | WOM, MEN, KID, SHO, ACC, HOM, BTY, JEW | **7%** |

A test in `tests/test_phase3_promos_and_tax.py` asserts that the tax-exempt grocery categories sum to $0 across the panel and that every line's tax matches `round(line_total × rate, 2)` exactly.

---

## 8.7 New transaction-level fields (Phase 3)

`tenant_transactions` gained four columns; `tenant_transaction_items` gained two.

| Table | Column | Type | Notes |
|---|---|---|---|
| `tenant_transactions` | `terminal_id` | TEXT | `<store_id>-T<NN>`. Phase 3 uses 4 terminals per store (`T01`..`T04`). No FK; design defers `tenant_terminals` to v3+. |
| `tenant_transactions` | `connectivity_type` | TEXT | Sampled per `V2_5_DATA_DESIGN.md` Step 4l: 65% wifi, 25% cellular_4g, 8% cellular_5g, 2% ethernet (uniform across merchants for v2.5). |
| `tenant_transactions` | `subtotal` | REAL | Sum of `line_total` across the transaction's line items. Atomic — `subtotal == sum(line_total)` per txn is asserted in tests. |
| `tenant_transactions` | `tax_total` | REAL | Sum of `tax` across the transaction's line items. |
| `tenant_transactions` | `txn_total` | REAL | Now defined as `subtotal + tax_total` (was previously sum of line totals; tax was implicit). |
| `tenant_transaction_items` | `tax` | REAL | Per-line tax per the model above. |
| `tenant_transaction_items` | `promo_id` | TEXT (nullable) | If a promo at this merchant covered this SKU on this date, the campaign id; otherwise NULL. |

The rollup invariant `txn_total == round(subtotal + tax_total, 2)` is asserted per-transaction in `tests/test_phase3_promos_and_tax.py`.

---

## 9. Planted anomalies

Three deliberate signals the AI agent finds when asked. The locked specifications are in `V2_5_DATA_DESIGN.md` "Planted Anomalies"; the implementation lives in `src/generate/anomalies/`. Each anomaly hooks into transaction generation so the signal is intrinsic to the data — no post-hoc injection step.

### A. University City decline (`anomalies/university_city_decline.py`)

Four-stage ramp on the three grocers' University City stores (neighborhood `"University City"`, ZIPs 28213/28223). Per-grocer effective multipliers come from the design's "magnitude" parameter (KRG full, ACM 80%, WDX 70% of the swing).

| Stage | Window           | Base multiplier | KRG effective | ACM effective | WDX effective |
|-------|------------------|----------------:|--------------:|--------------:|--------------:|
| 1     | Apr 12 – Apr 18  | 1.10            | 1.00          | 1.00          | 1.00          |
| 2     | Apr 19 – Apr 25  | 0.85            | 0.85          | 0.88          | 0.90          |
| 3     | Apr 26 – May 2   | 0.55 (peak)     | 0.55          | 0.64          | 0.69          |
| 4     | May 3  – May 29  | 0.65            | 0.65          | 0.72          | 0.76          |

Implementation: `uc_trip_keep_probability(merchant_id, neighborhood, txn_date)` returns the effective multiplier; the transaction generator drops a trip with probability `1 - keep_prob` after the store is selected. The boost stage (1.10×) caps to keep-prob 1.0 — no extra trips are injected; the dominant signal is the decline.

### B. Plaza Midwood Kroger avocado spike (`anomalies/plaza_midwood_avocado.py`)

Four-day pattern; Kroger Plaza Midwood (28205) only. Acme and Winn-Dixie Plaza Midwood stores show normal avocado levels.

| Date         | Multiplier on avocado SKU selection inside PRODUCE |
|--------------|---------------------------------------------------:|
| Apr 21       | 1.5×                                               |
| Apr 22       | 5.0× (peak)                                        |
| Apr 23       | 3.0×                                               |
| Apr 24       | 1.5×                                               |

Avocado SKUs are the three PRODUCE products whose name contains "avocado" (4-pack, organic 4-pack, single Hass). The generator weight-multiplies these SKUs inside the PRODUCE category for the affected (merchant, neighborhood, date) tuples; volume of trips is unchanged.

### C. Coordinated pasta promos (`anomalies/acme_pasta_promo.py`)

Three pinned `tenant_promotions` rows (one per grocer) plus per-grocer basket-level multipliers active during each window:

| Merchant   | Window           | Discount | SKU target | Basket lift |
|------------|------------------|---------:|-----------:|------------:|
| Kroger     | Apr 15 – Apr 21  | 25%      | up to 25   | 2.2× (lift) |
| Acme       | Apr 19 – Apr 25  | 20%      | up to 20   | 0.8× (fail) |
| Winn-Dixie | Apr 22 – Apr 28  | 15%      | up to 12   | 1.4× (lift) |

The promo provides the price discount when a pasta SKU is purchased during its window. The basket multiplier biases SKU sampling within PANTRY so pasta is chosen at `multiplier × baseline` rate. **Acme's <1.0× is the planted failure** — the discount is in place but pasta sales lag, so the demo can answer "did the promo work?" with a number.

Each grocer's `build()` reserves one slot from its random promo budget for the pinned pasta promo so per-merchant promo totals stay at the design target (KRG 25 / ACM 20 / WDX 18).

### Detectability

`tests/test_generation.py::test_university_city_decline_per_grocer`, `::test_plaza_midwood_kroger_avocado_spike`, and `::test_pasta_promo_lift_and_suppression` assert each anomaly's planted signal is empirically observable. The same numbers are surfaced in the report payload as `anomaly_callouts` so the static report shows the "before/after" without re-running the agent.

---

## 10. Payment instrument distributions

Phase 4 cuts to credit + debit only per Layer 1 ("captured rails: credit and debit only"). No EBT, no cash, no declines.

Per-transaction generation per `V2_5_DATA_DESIGN.md` Step 4l:

| Payment type | Grocery (KRG/ACM/WDX) | Taco Bell | TJ Maxx |
|---|---|---|---|
| Credit | 65% | 55% | 74% |
| Debit | 35% | 45% | 26% |

**Card network** sub-distribution (Step 4l): credit → 50% Visa / 30% MC / 12% Amex / 8% Discover. Debit → 60% Visa / 38% MC / 1% each Discover / Amex.

**Entry mode** per segment (Step 4l):

| Segment | Contactless | Chip | Swipe | Manual |
|---|---|---|---|---|
| QSR | 70% | 22% | 5% | 3% |
| Grocery | 55% | 35% | 9% | 1% |
| Off-price retail | 45% | 45% | 9% | 1% |

**Mobile wallet** (Step 4l): only sampled if `entry_mode == 'contactless'` and `has_mobile_wallet == 1`. Then 70% chance the wallet is used (50% Apple / 30% Google / 20% Samsung); 30% NULL.

**Connectivity type** (Step 4l): 65% wifi / 25% cellular_4g / 8% cellular_5g / 2% ethernet, uniform across merchants for v2.5.

---

## 11. The cross-merchant invariant

This is the single most important property of the data, and the property most likely to silently break. Document it loudly here, in `CLAUDE.md`, and in the test suite.

### What the invariant says

For any single physical customer in the panel, the value of `customer_id` is **identical** across all transactions, regardless of merchant. That stable hash is what makes cross-merchant analysis possible in the lake.

### How it's enforced (Phase 1, v2.5)

`customers.py` runs **once** and produces the master 10,000-row customer panel. The `customer_id` is generated directly there as a 16-char SHA-256 of `HASH_SECRET + synthetic_pan`, where `synthetic_pan` is internal and never written to disk. Each merchant generator (`kroger.py`, `acme.py`, `winn_dixie.py`, `taco_bell.py`, `tjmaxx.py`) reads from the master panel and uses the existing `customer_id` for every transaction. No merchant generator creates `customer_id` values.

### How it's tested

`tests/test_generation.py::test_cross_merchant_customer_invariant` checks that every `customer_id` in transactions exists in the master panel and that real overlap exists (≥100 customers shop at ≥2 merchants). If the test fails, the invariant has been violated and cross-merchant analytics will silently produce wrong answers.

---

## 12. Reproducibility

`RANDOM_SEED = 42` is locked. Same seed → same data, byte-for-byte. The test suite asserts this with a deterministic content hash of `transactions.csv`.

If you genuinely need to re-randomize for some reason: change the seed, re-run, expect to update the deterministic-hash test fixture. Don't change the seed for "variety" — a stable demo is more valuable than a varied one.

When generating in `transactions.py` or any merchant module, take a `numpy.random.Generator` argument seeded once at the top level. Don't call `np.random.*` directly anywhere — that uses the global state and breaks reproducibility.

---

## 13. Strategy Doc §5.2 Field Mapping

Comparison of fields specified in strategy doc §5.2 ("On-Device Data Capture: The Unified Transaction Record") against what the demo actually generates.

| Strategy doc §5.2 field | Demo field | Status | Notes |
|---|---|---|---|
| Tokenized PAN | `customer_id` | Built | Generated as a 16-char SHA-256 of a synthetic PAN that is never written to disk. No raw PAN, no name, no email at any stage. |
| Card type | `payment_type` | Built | Credit + debit only (Layer 1 "captured rails"); no EBT, cash, or declines in v2.5. |
| Card network | `card_network` | Built | |
| Issuer BIN | — | Missing | Skippable for analytics demo. |
| Authorization code | — | Missing | Not relevant. |
| Entry mode | `entry_mode` | Built | |
| Mobile wallet | `wallet_type` | Built | Sampled when `entry_mode='contactless'` and `has_mobile_wallet=1`. |
| Line items with SKU | `tenant_transaction_items` | Built | |
| Quantity per line | `qty` | Built | Per-category distributions per Step 4k. |
| Unit price | `unit_price` | Built | |
| Line total | `line_total` | Built | |
| Discount per line | `discount` | Built | Populated when an active `tenant_promotions` row covers the (sku, day) pair, with 85% application probability. |
| Promo / coupon attribution | `promo_id` (FK on items) | Built | One row per (campaign, sku) in `tenant_promotions`. |
| Tax | `tax` (per line) + `tax_total` (per txn) | Built | Five-tier rate by category (0%/4%/7%); rollup invariant `subtotal + tax_total = txn_total`. |
| `merchant_id` | `merchant_id` | Built | |
| MCC | `mcc` | Built | |
| `store_id` | `store_id` | Built | |
| Store ZIP | `store_zip5` (tenant) / `store_zip3` (lake) | Built | |
| `terminal_id` | `terminal_id` (`<store_id>-T<NN>`) | Built | 4–8 terminals/store. |
| Connectivity type | `connectivity_type` | Built | wifi / cellular_4g / cellular_5g / ethernet. |
| Transaction timestamp | `txn_ts` (tenant) / `txn_date` + `txn_hour_bucket` (lake) | Built | Lake additionally bins `txn_total` into 10 dollar buckets. |
| Loyalty ID | — | Missing | Optional merchant-side identifier. |
| Device telemetry (firmware, etc.) | — | Missing | Not relevant for analytics demo. |

The analytics-relevant fields are all built; missing fields are either device-telemetry (defer to real-time pipeline implementation) or merchant-side loyalty.
