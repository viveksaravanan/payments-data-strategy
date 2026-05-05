# Data Specification

The full spec for synthetic data generation. This file is the source of truth for `src/generate/parameters.py` and the generation modules. If you change a parameter here, change it in code; if you change it in code, change it here.

---

## 1. What gets generated

A 90-day shopping panel for 5,000 fictional customers across three merchants:

- **Kroger** — grocery, MCC 5411, ~25 stores
- **Taco Bell** — QSR, MCC 5814, ~40 stores
- **TJ Maxx** — off-price retail, MCC 5651, ~15 stores

Outputs (CSVs in `data/raw/`):
- `customers.csv` — shared 5,000-row customer panel (with PII)
- `merchants.csv` — three-row dimension
- `stores.csv` — 80 stores across all three merchants
- `products.csv` — combined catalog (~1,760 SKUs across merchants)
- `transactions.csv` — ~110,000 rows
- `transaction_items.csv` — ~960,000 rows

The generation runs in under 30 seconds at default settings. CSVs are intermediate workspace files; the anonymization pipeline reads them and produces tenant + lake CSVs that get loaded into SQLite.

---

## 2. Global parameters

```python
# src/generate/parameters.py

# Panel size and time window
N_CUSTOMERS                 = 5_000
DAYS                        = 90              # rolling window ending today

# Promotional events (specific dates that lift transaction count)
PROMO_DAYS                  = ["2026-04-15", "2026-04-22", "2026-05-01"]

# Anomaly injection (Kroger only — see §9)
ANOMALY_INJECT              = True

# Reproducibility
RANDOM_SEED                 = 42

# Anonymization
HASH_SECRET                 = "demo-only-not-a-real-secret"   # rotate in prod
K_ANONYMITY_THRESHOLD       = 5               # strategy doc spec is k≥50; demo uses 5
ZIP_TRUNCATION              = 3
TIME_BUCKET_HOURS           = 1
```

---

## 3. Per-merchant configs

Each merchant config holds the segment-specific cadence, basket shape, payment mix, and catalog size.

```python
MERCHANT_CONFIGS = {
    "kroger": {
        "merchant_id":           "KRG",
        "name":                  "Kroger",
        "segment":                "grocery",
        "mcc":                    "5411",
        "n_stores":               25,
        "participation_rate":     0.90,         # of N_CUSTOMERS who shop here
        "txn_per_week":           1.2,          # mean; bimodal underneath
        "avg_basket_size":        12,           # items per transaction
        "avg_ticket":             65.00,        # USD
        "promo_lift":             1.6,          # multiplier on promo days
        "payment_mix":            {"credit": 0.55, "debit": 0.30, "ebt": 0.10, "cash": 0.05},
        "wallet_share":           0.20,         # of card txns with mobile wallet
        "n_skus":                 1500,
        "organic_share":          0.18,
        "peak_days":              [5, 6],        # Sat, Sun (Mon=0)
        "peak_hours":             [10, 11, 17, 18, 19],
    },
    "taco_bell": {
        "merchant_id":           "TBL",
        "name":                   "Taco Bell",
        "segment":                "qsr",
        "mcc":                    "5814",
        "n_stores":               40,
        "participation_rate":     0.60,
        "txn_per_week":           0.8,
        "avg_basket_size":        3,
        "avg_ticket":             9.00,
        "promo_lift":             1.3,
        "payment_mix":            {"credit": 0.50, "debit": 0.40, "cash": 0.10},
        "wallet_share":           0.30,         # QSR over-indexes on contactless
        "n_skus":                 60,
        "organic_share":          0.0,
        "peak_days":              [4, 5],        # Fri, Sat
        "peak_hours":             [12, 13, 19, 20, 21],
    },
    "tjmaxx": {
        "merchant_id":           "TJX",
        "name":                   "TJ Maxx",
        "segment":                "retail_offprice",
        "mcc":                    "5651",
        "n_stores":               15,
        "participation_rate":     0.40,
        "txn_per_week":           0.35,         # ~1 visit every 2-3 weeks
        "avg_basket_size":        4,
        "avg_ticket":             55.00,
        "promo_lift":             1.2,
        "payment_mix":            {"credit": 0.70, "debit": 0.25, "cash": 0.05},
        "wallet_share":           0.15,
        "n_skus":                 200,
        "organic_share":          0.0,
        "peak_days":              [5, 6],        # Sat, Sun
        "peak_hours":              [11, 12, 13, 14, 15],
    },
}
```

**Hard rules baked into these configs:**

- **EBT only at Kroger.** SNAP rules generally exclude QSR and apparel retail. Taco Bell and TJ Maxx have no EBT key in `payment_mix`. Generation must respect this — the test suite checks for it.
- **Wallet share varies by segment.** QSR (30%) > grocery (20%) > retail (15%), reflecting real industry contactless adoption.
- **Peak hours differ.** Grocery peaks evenings + weekend mornings; QSR peaks lunch + dinner; retail peaks weekend afternoons.
- **Catalog size scales with segment.** Real Kroger has ~40k SKUs; we use 1,500. Real Taco Bell has ~60 menu items; we use 60. TJ Maxx products are one-off; we use 200 generic categorical SKUs.

---

## 4. Volume math

At default settings, expected output:

| Merchant | Customers active | Txn/week | Total txns (90d) | Items/txn | Total line items |
|---|---|---|---|---|---|
| Kroger | 4,500 (90% × 5k) | 1.2 | ~69,400 | 12 | ~833,000 |
| Taco Bell | 3,000 (60% × 5k) | 0.8 | ~30,900 | 3 | ~93,000 |
| TJ Maxx | 2,000 (40% × 5k) | 0.35 | ~9,000 | 4 | ~36,000 |
| **Totals** | **5,000 unique** | — | **~109,300** | — | **~962,000** |

Customer overlap (controlled by participation rates):
- All three merchants: ~1,080 customers (90% × 60% × 40% × 5,000)
- Exactly two merchants: ~1,860 customers
- Exactly one merchant: ~1,860 customers
- Zero merchants: ~200 customers (in the panel but never transact in the window — realistic)

**Storage:** SQLite `payments.db` ≈ 200 MB. **Generation runtime target:** under 30 seconds. **DB seeding target:** under 15 seconds.

If iteration speed becomes a concern: set `N_CUSTOMERS = 1000` and `DAYS = 30`. Volume drops ~10×. Use the full-size run for the actual demo.

---

## 5. Time window and seasonality

The window is **90 days ending on the run date** so the data always looks current. A run today (May 5, 2026) covers Feb 4 → May 5, 2026.

### Captured patterns

- **Day-of-week.** Saturday is biggest for grocery and retail; Friday/Saturday for QSR. Wired through `peak_days` multipliers: ~1.4× peak, ~0.7× trough.
- **Time-of-day.** Wired through `peak_hours`. Sample timestamps from a multi-modal distribution.
- **Pay-cycle bumps.** Apply ~1.15× transaction-count multiplier on the 1st–3rd and 15th–17th of each month (paychecks; SNAP/EBT lump). Three months captures this clearly. Used by demo questions.
- **Promo days.** Three dates in `PROMO_DAYS`. Each merchant lifts its transaction count by its `promo_lift` multiplier on those days.
- **Memorial Day weekend.** May 23–25, 2026, falls in the window. Visible as a long-weekend traffic shape.

### NOT captured (document honestly in `ARCHITECTURE.md`)

- **Annual seasonality** — pumpkin spike, holiday baking, summer BBQ. Needs 1–2 years.
- **Weather effects** — fake weather correlations are demo theater, not insight.
- **Macroeconomic drift** — inflation, tariffs.

The honest framing: the demo simulates **intra-week and intra-month rhythms** plus **promotional events**, not annual seasons. Enough for genuine pattern-finding and for "what happened on day X" questions to land.

---

## 6. Customer behavior model

A single customer's intrinsic behavior is set once (in `customers.py`) and applied across all merchants they visit.

### Behavioral segments

| Segment | Share | Behavior |
|---|---|---|
| Filler | ~70% | Small frequent baskets at grocery; lunch/quick QSR visits. Weekday-evening skew. Lower ticket. |
| Stocker | ~30% | Large biweekly grocery baskets; bigger but less frequent retail and QSR. Weekend-morning skew. Higher ticket. |
| Lapser | ~5% (overlapping) | Transacts only 1–2 times across 90 days. Realistic long tail; useful for retention questions. |

### Within-merchant basket sizing (bimodal mixture)

```python
if rng.random() < 0.7:
    basket_size = max(1, int(rng.normal(avg_basket * 0.6, avg_basket * 0.2)))   # filler-style basket
else:
    basket_size = max(2, int(rng.normal(avg_basket * 2.5, avg_basket * 0.5)))   # stocker-style basket
```

Customer-level traits set in `customers.py`:
- `customer_pan` (16-digit synthetic, stable across all merchants — **the critical invariant**)
- `customer_name`, `customer_email` (Faker; stripped during anonymization)
- `age_band`: one of `18-24`, `25-34`, `35-44`, `45-54`, `55-64`, `65+`
- `income_band`: one of `<35k`, `35-75k`, `75-125k`, `125-200k`, `200k+`
- `home_zip5`: full 5-digit ZIP
- `signup_date`: a date in the last 5 years
- `behavioral_segment`: `filler` or `stocker`
- `primary_card_type`: `credit`, `debit`, `ebt`, or `mixed`
- `has_mobile_wallet`: 0/1

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

## 8. Example SKUs

### Kroger (~1,500 SKUs)

SKU format: `KRG-{CATEGORY3}-{NNNN}` (e.g. `KRG-DAIRY-0042`).

| Category | Approx count | Examples |
|---|---|---|
| Produce | 120 | Bananas (lb), Honeycrisp apples (3 lb bag), Romaine hearts (3-pack), Strawberries (1 lb), Avocados (4-pack) |
| Dairy | 140 | Whole milk (gallon), 2% milk (gallon), Greek yogurt (32 oz), Sharp cheddar (8 oz block), Eggs (dozen large), Half & half (quart) |
| Bakery | 80 | Sourdough boule, Hamburger buns (8 ct), Bagels (6 ct), Croissants (4 ct), Tortillas flour (10 ct) |
| Meat & Seafood | 150 | Chicken breast boneless (lb), 80/20 ground beef (lb), Atlantic salmon (lb), Bacon (12 oz), Pork chops (lb) |
| Frozen | 140 | Frozen pizza pepperoni, Frozen broccoli (12 oz), Vanilla ice cream (pint), Frozen waffles (10 ct), Frozen french fries (32 oz) |
| Pantry | 220 | Spaghetti (1 lb box), Marinara sauce (24 oz), Peanut butter (16 oz), Olive oil (17 oz), Long-grain rice (5 lb), Black beans (15 oz can), Flour (5 lb), Sugar (4 lb) |
| Snacks | 140 | Potato chips classic (8 oz), Chocolate sandwich cookies, Granola bars (12 ct), Roasted almonds (16 oz), Pretzels (16 oz) |
| Beverages | 160 | Cola (12-pk cans), Sparkling water lime (12-pk), Ground coffee (30 oz), Orange juice (89 oz), Bottled water (24-pk) |
| Household | 120 | Paper towels (6 pk), Toilet paper (12 pk), Laundry detergent (50 oz), Dish soap (28 oz), Trash bags (13 gal, 80 ct) |
| Personal Care | 100 | Toothpaste (6 oz), Body wash (16 oz), Shampoo (20 oz), Razor blade refills (8 ct), Deodorant |
| Baby | 80 | Diapers size 3 (144 ct), Infant formula (23 oz), Baby wipes (720 ct), Baby food pouches (4 oz), Diaper rash cream |
| Pet | 50 | Dry dog food (30 lb), Cat litter (35 lb), Wet cat food (5.5 oz can), Dog treats, Cat treats |

Base prices range $0.69 (single banana) to $89.99 (30-lb dog food). Most cluster $2–$15. Apply ±10% noise to `unit_price` per transaction; promotional days lower prices ~15% on a random subset of SKUs.

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

## 9. Planted anomalies

Three deliberate signals the AI agent finds when asked. **All three are at Kroger** (highest data volume, easiest to find).

| # | Anomaly | Location | Window | How agent finds it |
|---|---|---|---|---|
| 1 | Price spike: SKU `KRG-PRODUCE-0042` (Avocados, 4-pack) at 5× base price | Kroger, all stores | One specific day in last 7 days | "Any unusual price moves recently?" |
| 2 | Store dropout: store `KRG-OH-0011` transaction count cut by 30% | Single Kroger store | Last 7 days | "Have any of my stores seen a drop in transaction count recently?" |
| 3 | Cohort surge: ~50 customers start buying baby SKUs they hadn't before | Kroger panel-wide | Last 21 days | "Any emerging customer segments this month?" / "What new patterns have you seen?" |

Anomalies are gated by `ANOMALY_INJECT = True`. Set to False for clean data (useful when iterating on the agent).

---

## 10. Payment instrument distributions

Per `MERCHANT_CONFIGS[m]["payment_mix"]`. Sampled per transaction.

| Payment type | Kroger | Taco Bell | TJ Maxx | Notes |
|---|---|---|---|---|
| Credit | 55% | 50% | 70% | Visa/MC/Amex/Disc subdistribution applied (Visa 60%, MC 25%, Amex 10%, Disc 5%) |
| Debit | 30% | 40% | 25% | Visa/MC subdistribution |
| EBT | 10% | — | — | SNAP rules: Kroger only |
| Cash | 5% | 10% | 5% | No `card_network` or `wallet_type` |

**Mobile wallet share** (of card transactions only): Kroger 20%, Taco Bell 30%, TJ Maxx 15%. When a wallet is used, sampled uniformly across `apple`, `google`, `samsung`.

**Entry mode** (of card transactions): contactless 65%, chip 30%, swipe 5%. Reflects modern terminal capability with legacy fallback.

**EBT-specific rule:** EBT transactions exclude prepared foods. Implemented by filtering the basket sampler — when payment_type is EBT, exclude SKUs with category `bakery` (some prepared) and explicitly mark certain SKUs as EBT-ineligible. This makes the data more credible and gives the agent a real pattern to find ("EBT customers have different basket composition").

---

## 11. The cross-merchant invariant

This is the single most important property of the data, and the property most likely to silently break. Document it loudly here, in `CLAUDE.md`, and in the test suite.

### What the invariant says

For any single physical customer in the panel, the value of `customer_pan` is **identical** across all transactions, regardless of merchant. The same `customer_pan` produces the same `customer_id` after hashing, which is what makes cross-merchant analysis possible in the lake.

### How it's enforced

`customers.py` runs **once** and produces the master 5,000-row customer panel including `customer_pan`. Each merchant generator (`kroger.py`, `taco_bell.py`, `tjmaxx.py`) reads from this panel and uses the existing `customer_pan` for every transaction it generates for that customer. No merchant generator creates `customer_pan` values.

### How it's tested

`tests/test_generation.py` includes a check that for any `customer_pan` appearing in transactions from multiple merchants, all rows for that PAN share the same value. If the test fails, the invariant has been violated and cross-merchant analytics will silently produce wrong answers.

---

## 12. Reproducibility

`RANDOM_SEED = 42` is locked. Same seed → same data, byte-for-byte. The test suite asserts this with a deterministic content hash of `transactions.csv`.

If you genuinely need to re-randomize for some reason: change the seed, re-run, expect to update the deterministic-hash test fixture. Don't change the seed for "variety" — a stable demo is more valuable than a varied one.

When generating in `base.py`, take a `numpy.random.Generator` argument seeded once at the top level. Don't call `np.random.*` directly anywhere — that uses the global state and breaks reproducibility.
