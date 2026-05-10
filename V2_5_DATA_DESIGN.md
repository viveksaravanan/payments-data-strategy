# V2.5 Data Design — Source of Truth

This document captures the agreed-upon design for the demo's data layer.
It's the target we're building toward, not a description of what's
currently generated.

Status: **Phase 1 LOCKED. Layer 4 (Generation Logic) LOCKED. Phase 2 LOCKED. Design complete.**

---

## How this doc is organized

The design has three parts:

- **Phase 1: Tenant data** — what gets generated and stored. Locked.
- **Layer 4: Generation Logic** — how reality gets simulated to produce
  Phase 1 data. Locked.
- **Phase 2: Anonymization & lake views** — how the tenant data gets
  transformed for cross-merchant analytics. Locked.

---

## Layer 1 — What entity are we modeling?

### The entity

The demo models **Verifone** — a payment terminal manufacturer + acquirer-
processor + cloud platform, sitting at the intersection of POS data and
payment data. Verifone occupies a position in the commerce stack that is
structurally different from card networks (which see card rails but no
basket detail) and from pure POS vendors (which see basket but only at
one merchant).

### The position

When a customer checks out at a Verifone-equipped merchant:

1. The cashier rings up the basket on the merchant's POS.
2. The POS sends the basket payload to the Verifone terminal via the POS
   API Bridge (line items, quantities, prices, discounts, tax).
3. The customer presents a card. The terminal reads it.
4. The terminal's payment kernel constructs the authorization request,
   sends it through Verifone's processing infrastructure, gets back an
   approved / declined response.
5. The terminal merges the basket payload with the auth response into a
   unified transaction record.
6. That record goes to Verifone's cloud platform.

Verifone observes this entire flow at every merchant they serve.

### What Verifone sees

For any single transaction crossing one of their terminals:

- **The basket** — every line item: SKU, name, quantity, price, discount,
  tax
- **The payment** — card type (credit / debit), network, entry mode,
  mobile wallet, auth response, settlement status
- **The merchant context** — merchant, store, terminal_id
- **The customer's tokenized identity** — irreversible hash of card PAN,
  stable across visits
- **Time** — full timestamp

Critically: this is observed **across multiple merchants in the installed
base**.

### What Verifone does not see

- Cash transactions (electronic-rail visibility only — credit and debit)
- **EBT transactions** — strategy doc §5.2 lists captured card types as
  credit and debit only.
- Anything at merchants using a different terminal vendor
- Customer name, email, address
- Customer demographics like age and income (deferred for v2.5)
- **Declined transactions** — skipped for v2.5 simplicity
- Behavioral motivation behind purchases — only the basket and payment
- **Merchant promotion schedules.** Verifone observes applied discounts at
  the line item level (per the basket payload from POS), but does not see
  the merchant's underlying promotion configuration — promo names, types,
  start/end dates, or campaign metadata. These exist as merchant-internal
  systems. Promotional analytics in the lake happen via discount-pattern
  observation, not via direct access to promo schedules.

### Strategy doc field-mapping reference

| Category | Data Element | Privacy Treatment |
|---|---|---|
| Transaction | Amount, currency, auth code, response code | Binned/rounded for aggregate analytics |
| Instrument | Card type, network, entry mode | Category-level only; no card details |
| Mobile Wallet | Apple/Google/Samsung indicator | Wallet type flag only |
| **Basket / SKU** | **Item descriptions, quantities, unit prices, discounts, tax, product categories** | **Product-level; no consumer linkage** |
| Merchant | Merchant ID, MCC, store name, lat/long, terminal ID | Location generalized to postal code for aggregate |
| Temporal | Timestamp, day of week, time-of-day | Time bucketed for aggregate analytics |
| Cardholder | Tokenized PAN, hashed reference | Irreversible tokenization |

### Cross-merchant peer disclosure rules

| Aspect | Allowed | Not allowed |
|---|---|---|
| Peer identity | Pseudonym (peer_a, peer_b) | Direct name |
| Peer segment (grocery / qsr / retail) | Yes (publicly observable) | — |
| Peer promo existence and structure (timing, depth, SKU coverage) | Yes (inferred from discount patterns) | — |
| Peer promo configuration metadata (names, types, exact start/end) | — | Not captured by Verifone |
| Peer promo performance (indexed/relative) | Yes (e.g., "2.2× baseline") | — |
| Peer absolute revenue | — | Never |
| Peer absolute customer counts | — | Aggregate only, k=5 suppression |
| Peer absolute unit volumes | — | Approximate via bin midpoints |
| Cross-merchant SKU matching (via canonical names) | Yes | — |
| Peer pricing per product | Yes (publicly observable) | — |

### AI agent personas (per strategy doc §10.2)

The strategy doc specifies seven AI agents. The demo's data architecture
supports all seven. All agents are **merchant-scoped** — there is no
network-level analyst agent. When the demo switches between merchants,
all agents inherit the new merchant context.

| Agent | Primary data needs | Cross-merchant context |
|---|---|---|
| **1. Demand Forecasting** | Own SKU/category transaction history, time series | Cross-merchant SKU/category demand trends from `lake_transactions` |
| **2. Dynamic Pricing & Benchmarking** | Own SKU prices, transactions | Peer pricing per product (canonical product matching) |
| **3. Consumer Segmentation** | Own customer transaction history, behavioral segments | Aggregate behavioral patterns from peer transactions (no peer customer cohorts) |
| **4. Location & Trade Area Intelligence** | Own store locations, transaction patterns by store | Peer store density at neighborhood/ZIP3 level |
| **5. Payment Optimization Advisor** | Own payment field distributions | Peer payment mix benchmarks |
| **6. Anomaly Detection & Fraud Intelligence** | Real-time own transaction stream | Cross-merchant baselines for "what's normal" |
| **7. Conversational Business Advisor** | Inherits from agents it orchestrates | Inherits from agents it orchestrates |

**Privacy posture across agents:**

- All agents query own merchant's tenant data fully (no anonymization
  within own merchant)
- All agents query lake data with peers pseudonymized
- Peer absolute revenue/volume is never exposed; relative/indexed metrics
  are
- Product-level cross-merchant insight is enabled (per strategy doc's
  basket/SKU privacy treatment)

### Scope note: terminals as analytical entities

v2.5 deliberately scopes out device-level analytics. What's preserved:
`terminal_id` as a string field on transactions. What's deferred: a
`tenant_terminals` table.

---

## Layer 2 — The metro and the panel

### The region

The demo is set in a **single fictional metro of ~2.7 million people**,
loosely modeled on Charlotte, NC. All merchants in the panel operate in
this metro by demo convention.

The metro uses ~30 ZIP codes from real Charlotte ZIPs (treated as
fictional). Population distribution: urban core 30%, inner suburbs 40%,
outer suburbs and exurbs 30%.

The 90-day window: **March 1, 2026 through May 29, 2026**. This window
covers Easter (April 5) and Memorial Day (May 25).

### The merchant panel

| Merchant | Segment | MCC | Stores in panel |
|---|---|---|---|
| **Kroger** | grocery | 5411 | 30 |
| **Acme** | grocery | 5411 | 25 |
| **Winn-Dixie** | grocery | 5411 | 20 |
| **Taco Bell** | qsr | 5814 | 40 |
| **TJ Maxx** | off_price_retail | 5651 | 8 |

**Total: 123 stores across the panel.**

### Sources of variance between the three grocers

| Differentiator | Kroger | Acme | Winn-Dixie |
|---|---|---|---|
| Store count in metro | 30 | 25 | 20 |
| Catalog size | ~1,100 SKUs | ~1,000 SKUs | ~880 SKUs |
| Price positioning (staples + center-store) | baseline | +3% | -3% |
| Price positioning (non-food) | baseline | +7% | -7% |

### Payment-mix per merchant

| Merchant | Credit | Debit |
|---|---|---|
| Kroger / Acme / Winn-Dixie | ~65% | ~35% |
| Taco Bell | ~55% | ~45% |
| TJ Maxx | ~74% | ~26% |

### Customer panel

**10,000 customers**.

### Grocer affinity model

| Affinity type | Share | Customer count |
|---|---|---|
| Loyalist | 55% | ~5,500 |
| Splitter | 30% | ~3,000 |
| Three-chain shopper | 12% | ~1,200 |
| Lapsed/light | 3% | ~300 |

Primary grocer assignment proportional to store count: Kroger 40%,
Acme 33%, Winn-Dixie 27%. ~10% of customers are primary at a chain that
is not their geographically closest grocer.

### Cross-merchant cohorts

| Cohort | Customer count |
|---|---|
| Active at all three grocers | ~1,400–1,800 |
| Active at exactly two grocers | ~2,500–3,000 |
| Active at one grocer only | ~5,000–5,500 |
| Active at all five merchants | ~700–1,000 |

### Volume estimates

Across 90 days: ~180,000–250,000 transactions, ~2.0–3.0M line items.

---

# PHASE 1: TENANT DATA — LOCKED

The tenant data is the full per-merchant view. Each merchant's analytics
agent queries this layer with merchant-id filtering enforced.

Tables:

- `merchants` — the five businesses
- `tenant_stores` — physical store locations
- `tenant_customers` — the 10,000-customer panel
- `tenant_products` — SKU catalog per merchant
- `tenant_transactions` — checkout events
- `tenant_transaction_items` — line items per transaction
- `tenant_promotions` — campaigns and discounts (used during data
  generation; not exposed to the lake)

A `tenant_terminals` table is not included; terminal identity is preserved
via `terminal_id` string field on transactions.

---

## Phase 1, Layer 0 — Merchants

### `merchants` schema

| Column | Type | Notes |
|---|---|---|
| `merchant_id` | TEXT PK | `KRG`, `ACM`, `WDX`, `TBL`, `TJX` |
| `name` | TEXT | `Kroger`, `Acme`, etc. |
| `mcc` | TEXT | `5411` (grocery), `5814` (qsr), `5651` (retail) |
| `segment` | TEXT | `grocery`, `qsr`, `off_price_retail` |

Four columns. Both `mcc` and `segment` are stored (not derived) so the
lake transformation can carry segment through cleanly without hardcoding.

### Volume

5 rows.

---

## Phase 1, Layer 3a — Stores

### `tenant_stores` schema

| Column | Type | Notes |
|---|---|---|
| `store_id` | TEXT PK | Format: `<merchant>-NC-<seq>` |
| `merchant_id` | TEXT FK | References `merchants(merchant_id)` |
| `store_zip5` | TEXT | The store's 5-digit ZIP |
| `neighborhood` | TEXT | Named neighborhood like `Plaza Midwood` |
| `latitude` | REAL | Real coordinate within the ZIP |
| `longitude` | REAL | Real coordinate within the ZIP |
| `metro_region` | TEXT | `urban_core` / `inner_suburbs` / `outer_suburbs` |
| `open_date` | DATE | When the store opened |

### Distribution

123 stores total. Per-merchant + per-region distribution per the panel
design. Stores placed at ZIP centroid + ±0.02° jitter.

### Neighborhood map

| Neighborhood | Region tier | Approx ZIPs |
|---|---|---|
| Uptown / Center City | Urban core | 28202 |
| Plaza Midwood | Urban core | 28205 |
| NoDa | Urban core | 28206 |
| Dilworth | Urban core | 28203 |
| SouthPark | Inner suburbs | 28210, 28211 |
| Ballantyne | Inner suburbs | 28277 |
| University City | Inner suburbs | 28213, 28223 |
| Matthews | Outer suburbs | 28104, 28105 |
| Huntersville | Outer suburbs | 28078 |
| Pineville | Outer suburbs | 28134 |
| Concord | Outer suburbs/exurbs | 28025, 28027 |
| Mooresville | Exurbs | 28115, 28117 |

---

## Phase 1, Layer 3b — Customers

### `tenant_customers` schema

| Column | Type | Notes |
|---|---|---|
| `customer_id` | TEXT PK | 16-char SHA-256 hash |
| `home_zip5` | TEXT | Home postal code |
| `behavioral_segment` | TEXT | Inferred: `filler` / `stocker` |
| `grocer_affinity_type` | TEXT | Inferred affinity profile |
| `primary_grocer` | TEXT | `KRG` / `ACM` / `WDX` |
| `secondary_grocer` | TEXT, nullable | For splitters and three-chain |
| `primary_card_type` | TEXT | Inferred: `credit` / `debit` / `mixed` |
| `has_mobile_wallet` | INTEGER | 0 or 1 |
| `signup_date` | DATE | When Verifone first observed this card |

---

## Phase 1, Layer 3c — Products / SKUs

### Architecture: base catalog + per-merchant overlays

- Canonical base catalog (`base_grocery_catalog.json`) defining the
  universe of grocery products
- Per-grocer overlay files specifying which canonical SKUs they carry
  plus per-category price multipliers
- Generator joins base + overlay → produces three sets of `tenant_products`
  rows with merchant-prefixed SKU codes

### `tenant_products` schema

| Column | Type | Notes |
|---|---|---|
| `sku` | TEXT PK | Merchant-prefixed: `<merchant>-<CATEGORY>-<NNNN>` |
| `merchant_id` | TEXT FK | References `merchants(merchant_id)` |
| `name` | TEXT | The canonical product name (shared across grocers) |
| `category` | TEXT | Top-level category from shared vocabulary |
| `subcategory` | TEXT | Specific group from shared vocabulary |
| `base_price` | REAL | Per-merchant list price |

### Two-tier category-aware pricing

| Tier | Categories | Acme | Winn-Dixie |
|---|---|---|---|
| **Tight** (staples + center-store) | DAIRY, BAKERY, PRODUCE, MEAT, PANTRY, SNACKS, BEVERAGES, FROZEN | 1.03× | 0.97× |
| **Loose** (non-food) | HOUSEHOLD, PERSONAL, BABY, PET | 1.07× | 0.93× |

Plus per-SKU noise of ±2% during catalog generation.

### Catalog volumes

| Merchant | SKU count |
|---|---|
| Kroger | ~1,100 |
| Acme | ~1,000 |
| Winn-Dixie | ~880 |
| Taco Bell | ~60 |
| TJ Maxx | ~200 |

**Total: ~3,240 SKUs.**

---

## Phase 1, Layer 3d — Transactions

### `tenant_transactions` schema

| Column | Type | Notes |
|---|---|---|
| `txn_id` | TEXT PK | Format: `<merchant>-<NNNNNNN>` |
| `merchant_id` | TEXT FK | References `merchants(merchant_id)` |
| `store_id` | TEXT FK | References `tenant_stores(store_id)` |
| `terminal_id` | TEXT | String, format `<store_id>-T<NN>`. No FK. |
| `customer_id` | TEXT FK | References `tenant_customers(customer_id)` |
| `txn_ts` | DATETIME | Full UTC timestamp |
| `payment_type` | TEXT | `credit` / `debit` |
| `card_network` | TEXT | `visa` / `mc` / `amex` / `discover` |
| `entry_mode` | TEXT | `chip` / `contactless` / `swipe` / `manual` |
| `wallet_type` | TEXT, nullable | `apple` / `google` / `samsung` / NULL |
| `connectivity_type` | TEXT | `wifi` / `cellular_4g` / `cellular_5g` / `ethernet` |
| `subtotal` | REAL | Sum of line_total across items |
| `tax_total` | REAL | Sum of tax across items |
| `txn_total` | REAL | subtotal + tax_total |

14 columns. No `auth_status` (declines skipped).

---

## Phase 1, Layer 3e — Transaction items

### `tenant_transaction_items` schema

| Column | Type | Notes |
|---|---|---|
| `txn_id` | TEXT FK | References `tenant_transactions(txn_id)` |
| `line_id` | INTEGER | Sequence within transaction |
| `sku` | TEXT FK | References `tenant_products(sku)` |
| `qty` | INTEGER | Number of units, ≥ 1 |
| `unit_price` | REAL | = product's `base_price` exactly |
| `discount` | REAL | From promotion if applicable, else 0 |
| `tax` | REAL | (line_total) × tax_rate(category) |
| `line_total` | REAL | (unit_price × qty) - discount |
| `promo_id` | TEXT FK, nullable | References `tenant_promotions(promo_id)` |

### Tax model

| Category type | Rate |
|---|---|
| Tax-exempt grocery (DAIRY, BAKERY basic, PRODUCE, MEAT, FROZEN, PANTRY) | 0% |
| Non-food / prepared (HOUSEHOLD, PERSONAL, BABY, PET) | 7% |
| QSR (MAIN, SIDE, DRINK, COMBO) | 7% |
| Retail (APPAREL, HOME, ACCESSORY) | 7% |
| BEVERAGES, SNACKS | 4% |

---

## Phase 1, Layer 3f — Promotions (tenant only)

### `tenant_promotions` schema

| Column | Type | Notes |
|---|---|---|
| `promo_id` | TEXT PK | Format: `<merchant>-PROMO-<NNNN>` |
| `merchant_id` | TEXT FK | References `merchants(merchant_id)` |
| `sku` | TEXT FK | References `tenant_products(sku)` — one row per affected SKU |
| `start_date` | DATE | First day promo is active |
| `end_date` | DATE | Last day promo is active (inclusive) |
| `discount_pct` | REAL | 0.15 = 15% off |
| `promo_name` | TEXT | Human-readable, used as campaign grouping key |
| `promo_type` | TEXT | `weekly_ad` / `holiday` / `lto` / `clearance` |

### Promotions are tenant-only

The `tenant_promotions` table is used during **data generation** to drive
realistic discount patterns on transaction line items. Each merchant's own
agent can also query this table for own-merchant campaign analytics.

The `tenant_promotions` data is **not** transformed into the lake.
Verifone doesn't observe merchant promo schedules in production —
they only observe applied discounts at the line item level. Cross-merchant
promotional analytics in the lake happen via discount-pattern observation
on `lake_transactions`, not via direct access to peer promo schedules.

### Volume

| Merchant | Promos per 90 days |
|---|---|
| Kroger | ~25 |
| Acme | ~20 |
| Winn-Dixie | ~18 |
| Taco Bell | ~6 |
| TJ Maxx | ~4 |

**Total: ~73 distinct promotions.**

---

# LAYER 4: GENERATION LOGIC — LOCKED

(Six-step generator flow. Implementation rules for trip frequency,
per-customer week-level variance, day-of-week patterns, basket archetypes,
payment correlations, planted anomalies. See earlier doc state for full
detail; preserved here for reference.)

90-day window: **March 1, 2026 through May 29, 2026**.

Three planted anomalies:

1. **University City decline** — All three grocers in University City
   neighborhood see traffic drops late April / May (4-stage ramp).
   Cross-merchant enrichment shows market-wide pattern.
2. **Plaza Midwood Kroger avocado spike** — One Kroger store sees
   elevated avocado purchases April 21–24 (4-day pattern, peak April 22).
3. **Acme failed pasta promo** — Acme's pasta promo April 19–25 fails
   while Kroger's competing promo April 15–21 succeeds. Demonstrates
   competitive market dynamics, detected via discount-pattern observation
   in the lake.

---

# PHASE 2: ANONYMIZATION & LAKE — LOCKED

## The mental model

> "Tenant is full-fidelity data scoped to me; lake is structurally-
> anonymized data about everyone else."

Each merchant has their own tenant data (their full books). The lake is
what they see *about other merchants* — with privacy controls applied.

The lake **excludes own merchant data**. When Kroger queries the lake, it
contains Acme + Winn-Dixie + Taco Bell + TJ Maxx data, pseudonymized.
Kroger's own data lives in tenant. When the demo switches to Acme, Acme's
lake view contains the *other* four merchants pseudonymized.

## The privacy engine

A single transformation pipeline applies four privacy mechanisms uniformly
to all data flowing from tenant to lake:

### 1. P2PE tokenization

Already built into `customer_id` — it's a 16-char SHA-256 hash of the
tokenized PAN. No raw PAN exists anywhere in the data. Carried through
from tenant to lake unchanged where customer references survive
(generally they don't — see #5).

### 2. Generalization

Reduce precision of identifying fields:

- **Location**: lat/long dropped, ZIP5 → ZIP3, neighborhood retained
- **Timestamp**: full UTC timestamp split into `txn_date` + 2-hour bucket
- **Transaction amounts**: `txn_total` binned into 10-bin scale (see below)
- **Per-line prices**: `unit_price`, `discount`, `line_total` carried
  precisely (publicly observable in real life — anyone can walk into Acme
  and see shelf prices)

**Time-of-day bucket scheme** (10 buckets, 2 hours each):

| Bucket | Hour range |
|---|---|
| `early_morning` | 5–7am |
| `morning` | 7–9am |
| `mid_morning` | 9–11am |
| `lunch` | 11am–1pm |
| `afternoon` | 1–3pm |
| `late_afternoon` | 3–5pm |
| `evening` | 5–7pm |
| `dinner` | 7–9pm |
| `late_evening` | 9–11pm |
| `late_night` | 11pm–5am |

**Transaction total bin scheme** (10 bins):

| Bin | Range |
|---|---|
| `$0-5` | < $5 |
| `$5-10` | $5–10 |
| `$10-20` | $10–20 |
| `$20-35` | $20–35 |
| `$35-50` | $35–50 |
| `$50-75` | $50–75 |
| `$75-100` | $75–100 |
| `$100-150` | $100–150 |
| `$150-250` | $150–250 |
| `$250+` | $250+ |

### 3. K-Anonymity (k = 5)

For aggregate queries on customer-level dimensions (cohort sizes by ZIP3,
behavioral patterns by neighborhood, etc.), any cell with fewer than 5
records is suppressed. The agent gets back "data suppressed for privacy"
rather than partial-data answers.

**v2.5 uses k=5** given the 10,000-customer panel size. Production with
millions of customers per metro would use k≥50 per strategy doc; the
mechanism is identical, the threshold scales with panel size.

### 4. Suppression of consumer linkage

Per strategy doc §5.2 (basket/SKU privacy treatment: "product-level; no
consumer linkage"), the `customer_id` field is **dropped** from
`lake_transactions`. Peer transactions exist in the lake but cannot be
tied back to specific customers. This is the strongest privacy mechanism
in the design.

### Mechanisms not implemented in v2.5

- **L-Diversity** — mostly auto-satisfied by our data (sensitive attributes
  vary across all groups in our generation). Tracked as v3+ enhancement
  for explicit verification.
- **Differential Privacy** — calibrated noise on aggregates is too complex
  for v2.5. Tracked as v3+ enhancement. Production would inject Laplacian
  noise per epsilon-bounded DP.

## Per-merchant peer mappings

Each merchant has a stable peer mapping. When that merchant queries the
lake, peers appear with consistent labels. The mapping is computed
deterministically from `merchants.segment`: same-segment peers get
peer_a/b first, then cross-segment peers alphabetically by merchant_id.

| Viewing as | peer_a | peer_b | peer_c | peer_d |
|---|---|---|---|---|
| Kroger | Acme | Winn-Dixie | Taco Bell | TJ Maxx |
| Acme | Kroger | Winn-Dixie | Taco Bell | TJ Maxx |
| Winn-Dixie | Acme | Kroger | Taco Bell | TJ Maxx |
| Taco Bell | Acme | Kroger | Winn-Dixie | TJ Maxx |
| TJ Maxx | Acme | Kroger | Winn-Dixie | Taco Bell |

The mapping is documented and stable — not session-randomized. For a demo
this is sufficient; production would add per-session randomization.

The `peer_segment` field on lake tables is **carried from
`merchants.segment`** at lake-build time, not hardcoded. Adding a new
merchant in v3 (e.g., a fourth grocer) automatically integrates into the
mapping with the right segment.

## The lake architecture: two tables

The lake has exactly two tables:

1. **`lake_transactions`** — wide, denormalized. One row per transaction
   line item with all peer + transaction + product + promotion context.
2. **`lake_stores`** — store-level reference for geographic queries.

That's it. Customers, products, and promotions are **not** separate lake
tables — their analytical content is denormalized into `lake_transactions`
or queried via aggregation.

### Why two tables (not six)

Three reasons:

1. **AI-agent friendly.** Simpler schemas produce more reliable LLM-
   generated SQL. Most cross-merchant queries become single-table or
   single-join queries.
2. **Read-only analytical workload.** The lake is snapshot-stable, never
   updated. Denormalization is correct for analytical workloads (modern
   data warehouses do this routinely).
3. **Matches the "one privacy engine" mental model.** Tenant data flows
   through the engine and emerges as anonymized lake data with minimal
   structural complexity.

## `lake_transactions` schema

| Column | Type | Source / transformation |
|---|---|---|
| `lake_txn_id` | TEXT | Generated, opaque ID |
| `line_id` | INTEGER | Carried |
| `peer_id` | TEXT | From per-merchant mapping (replaces merchant_id) |
| `peer_segment` | TEXT | Carried from `merchants.segment` |
| `lake_store_id` | TEXT FK | References `lake_stores` |
| `txn_date` | DATE | Date only, derived from `txn_ts` |
| `txn_hour_bucket` | TEXT | 2-hour bucket label |
| `payment_type` | TEXT | Carried |
| `card_network` | TEXT | Carried |
| `entry_mode` | TEXT | Carried |
| `wallet_type` | TEXT, nullable | Carried |
| `connectivity_type` | TEXT | Carried |
| `txn_total_bin` | TEXT | Binned (10-bin scale) |
| `canonical_name` | TEXT | Product name from base catalog |
| `category` | TEXT | Product category |
| `subcategory` | TEXT | Product subcategory |
| `unit_price` | REAL | Carried (publicly observable) |
| `qty` | INTEGER | Carried |
| `discount` | REAL | Carried |
| `line_total` | REAL | Carried |
| `discount_pct_applied` | REAL, nullable | If line had a discount, the percent applied |

21 columns. One row per transaction line item.

### What's dropped from tenant → lake_transactions

- Original `txn_id`, original `sku` — replaced with opaque IDs to prevent
  merchant-prefix leakage
- `terminal_id` — reveals store, not analytically valuable for cross-
  merchant work
- `customer_id` — stripped per strategy doc "no consumer linkage"
- Full UTC `txn_ts` — generalized to date + bucket
- `subtotal`, `tax_total`, `txn_total` (exact) — replaced by `txn_total_bin`
- `tax` per line — derivable from category and amount; not surfaced
- `promo_id`, `promo_name`, `promo_type`, exact `start_date` / `end_date` —
  Verifone doesn't observe merchant promo schedules. The fact that a
  discount was applied is observable (via `discount` field); the campaign
  metadata is not.

## `lake_stores` schema

| Column | Type | Notes |
|---|---|---|
| `lake_store_id` | TEXT PK | Generated, opaque ID |
| `peer_id` | TEXT | From per-merchant mapping |
| `peer_segment` | TEXT | Carried from `merchants.segment` |
| `store_zip3` | TEXT | First 3 digits of ZIP |
| `neighborhood` | TEXT | Carried unchanged |
| `metro_region` | TEXT | Carried unchanged |

Six columns.

### What's dropped from tenant_stores → lake_stores

- Original `store_id` — replaced with opaque ID
- `merchant_id` — replaced with `peer_id`
- Full ZIP5 — generalized to ZIP3
- `latitude`, `longitude` — dropped (privacy)
- `open_date` — dropped

## Implementation: lake as parameterized views

The lake doesn't physically exist as fixed tables. Instead, the lake is
implemented as **parameterized query functions** that take the viewing
merchant as input and apply transformations dynamically:

```python
def get_lake_transactions(viewing_merchant_id):
    mapping = build_peer_mapping(viewing_merchant_id)
    return query("""
        SELECT
          generate_opaque_id(t.txn_id, l.line_id) AS lake_txn_id,
          l.line_id,
          mapping[t.merchant_id] AS peer_id,
          m.segment AS peer_segment,
          generate_opaque_id(t.store_id) AS lake_store_id,
          DATE(t.txn_ts) AS txn_date,
          to_hour_bucket(t.txn_ts) AS txn_hour_bucket,
          t.payment_type, t.card_network, t.entry_mode,
          t.wallet_type, t.connectivity_type,
          to_bin(t.txn_total) AS txn_total_bin,
          p.name AS canonical_name,
          p.category, p.subcategory,
          l.unit_price, l.qty, l.discount, l.line_total,
          CASE WHEN l.discount > 0
               THEN ROUND(l.discount / (l.unit_price * l.qty), 2)
               ELSE NULL END AS discount_pct_applied
        FROM tenant_transactions t
        JOIN tenant_transaction_items l ON l.txn_id = t.txn_id
        JOIN tenant_products p ON p.sku = l.sku
        JOIN merchants m ON m.merchant_id = t.merchant_id
        WHERE t.merchant_id != :viewing
    """, viewing=viewing_merchant_id)
```

Same pattern for `get_lake_stores(viewing_merchant_id)`.

This approach:
- No physical lake tables to keep in sync with tenant
- Filtering and pseudonymization applied at query time
- The viewing merchant's data is automatically excluded
- Easy to extend — new fields just get added to the query

## Honest accounting: what's preserved, approximate, and lost

### Fully preserved at the lake

- Peer pricing per product (exact)
- Peer category mix and basket composition
- Peer payment mix (card type, network, entry mode, wallet, connectivity)
- Peer geographic distribution at neighborhood level
- Peer day-of-week and hour-bucket patterns
- Peer SKU-level demand patterns (via canonical names)
- Cross-merchant pricing comparisons via canonical names
- Peer discount activity (when, what SKUs, how deep)

### Approximate (answerable with reduced precision)

- **Average peer ticket** — via bin midpoints, ~$5-25 imprecision
- **Peer transaction count** — aggregate counts subject to k=5 suppression
- **Peer geographic precision** — neighborhood/ZIP3, not full address
- **Promo timing** — visible via discount activity windows, not exact
  start/end (Verifone doesn't observe promo configuration)

### Truly lost / impossible at the lake

- **Per-peer-customer cohort analytics** — no customer rows for peers.
  The Consumer Segmentation Agent is constrained to own-merchant
  segmentation plus aggregate behavioral pattern comparison.
- **Cross-merchant customer tracking** — linkage stripped per strategy doc.
- **Sub-hour transaction timing** — hour-bucket precision only.
- **Exact peer revenue totals** — bins only.
- **Promotion configuration metadata** — names, types, exact schedules
  not observable by Verifone in production.
- **Long-tail SKU presence** — only SKUs with at least one transaction
  appear in the lake (no separate catalog view).

The privacy treatments cleanly map to what's lost — exactly the things
the strategy doc says should be protected — while preserving what's
needed for cross-merchant analytics.

## Agent feasibility verified

Each of the seven AI agents from §10.2 is supported by this architecture.

| Agent | Verified |
|---|---|
| 1. Demand Forecasting | ✓ Cross-merchant SKU/category trends from `lake_transactions` |
| 2. Dynamic Pricing & Benchmarking | ✓ Peer pricing per product (canonical product matching). Strongest capability. |
| 3. Consumer Segmentation | ⚠ Constrained — can segment own customers fully; peer customer cohorts not directly queryable |
| 4. Location & Trade Area Intelligence | ✓ Peer stores at neighborhood/ZIP3 from `lake_stores` |
| 5. Payment Optimization Advisor | ✓ Peer payment mix from `lake_transactions` |
| 6. Anomaly Detection & Fraud Intelligence | ✓ Cross-merchant baselines from `lake_transactions`. The University City decline anomaly demonstrates this — agent finds own decline, queries lake for peers in same neighborhood, distinguishes shared-signal from merchant-specific. |
| 7. Conversational Business Advisor | ✓ Routes queries to specialist agents; inherits their access |

**Documented constraints:**

- Consumer Segmentation can't directly compare customer cohorts across
  merchants. Operates on aggregate behavioral patterns instead.
- Real-time fraud detection requires live ML inference; v2.5 surfaces
  patterns from data, not real-time models.
- Demographic-aware segmentation deferred to v3+ (no demographics in v2.5).

These are documented limitations, not architectural blockers.

---

## Open decisions / v3+ enhancements

- **Demographic enrichment.** Add `customer_enrichment` sidecar table.
- **EBT support.** Excluded from v2.5.
- **Declines / decline rates.** Excluded from v2.5.
- **Terminals as a first-class table.** Currently string-only.
- **Brand as separate column.**
- **Per-merchant product name variations.** Canonical names shared in v2.5.
- **Organic flag.** Removed from v2.5.
- **Per-store regional pricing.** Merchant-level pricing only.
- **Loyalty programs.** Not modeled in v2.5.
- **Multi-buy / coupons.** Modeled as percentage discounts in v2.5.
- **Real-time ML inference for agents.** v2.5 surfaces patterns; live
  models in v3+.
- **Per-session pseudonym randomization.** v2.5 uses stable per-merchant
  mappings.
- **L-Diversity explicit verification.** Mostly auto-satisfied; not
  explicitly checked in v2.5.
- **Differential Privacy with epsilon bounds.** Deferred to v3+; v2.5
  does not implement calibrated noise on aggregates.
- **K=50 anonymity threshold.** v2.5 uses k=5 given panel size.
- **Customer cohort cross-merchant analytics.** Constrained by strategy
  doc's "no consumer linkage" rule. v3+ could explore privacy-preserving
  cohort analytics if a use case emerges.
- **Lake catalog visibility for zero-volume SKUs.** Long-tail products
  with no transactions are not in the lake. v3+ could add a `lake_products`
  reference table if catalog-presence questions become important.
