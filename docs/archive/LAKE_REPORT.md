# Wave 2 Lake Report

_Generated 2026-05-31 from `data/lake/` built against the Wave 1 full-scale `data/raw/` (seed=42, deterministic)._

This is the Wave 2 privacy-posture artifact — same role
`docs/DQ_REPORT.md` played for Wave 1. It states what the
five anonymized lake tables look like at the published grain
and the §8 framing of what's applied vs deferred.

## Input coverage
- Source: `data/raw/` (Wave 1 tenant census, deterministic at seed=42).
- Transactions: **1,660,732**
- Line items: **10,764,855**
- Customers: **100,000**
- 90-day window: 2026-03-01 → 2026-05-29

## Per-table cell counts and k-distribution

### `lake_category_metrics`
- Cells published: **14,677**
- `txn_count` distribution: min=93, p5=239, p25=453, p50=743, p75=1,228, p95=2,829, max=10,203
- k≥50 floor: **✓ cleared** (min cell = 93 txns; 1× the floor)

- Grain distribution (k-ladder fire log):
    - `subcat_week`: 11,804 rows (80.4%)
    - `cat_week`: 2,873 rows (19.6%)
    - `cat_month`: 0 rows (0.0%)

### `lake_payment_mix`
- Cells published: **72**
- `txn_count` distribution: min=9,546, p5=11,346, p25=14,371, p50=20,770, p75=27,156, p95=52,431, max=62,893
- k≥50 floor: **✓ cleared** (min cell = 9,546 txns; 190× the floor)

### `lake_segment_mix`
- Cells published: **96**
- `txn_count` distribution: min=430, p5=1,612, p25=4,116, p50=9,166, p75=16,380, p95=79,713, max=142,851
- k≥50 floor: **✓ cleared** (min cell = 430 txns; 8× the floor)

- Behavioral segments (DERIVED — not the planted `loyalty_type`): {'frequent_value': 24, 'occasional': 24, 'occasional_premium': 24, 'premium_loyalist': 24}

### `lake_trade_area`
- Cells published: **221**
- `txn_count` distribution: min=6,345, p5=8,689, p25=14,610, p50=22,100, p75=34,202, p95=68,964, max=111,986
- k≥50 floor: **✓ cleared** (min cell = 6,345 txns; 126× the floor)

### `lake_cross_merchant_cohorts`
- Cells published: **52**
- `txn_count` distribution: min=1,466, p5=2,815, p25=9,159, p50=14,425, p75=29,864, p95=125,426, max=337,112
- k≥50 floor: **✓ cleared** (min cell = 1,466 txns; 29× the floor)

- All-three cohort per zone: min cohort_size=159, max=1986, median=423 cards.
  (Wave 1 T17 reported 483-1,126 all-three cards per
  **planted** `home_zone`; the cohort table groups by
  **behavioral** home zone — the zone where the card
  transacts most. Distributions differ when planted and
  behavioral zones disagree, but the order of magnitude
  matches.)

## §8 — Anonymization posture (honest)

### Applied this wave

- **Tokenization** — `card_id` is a 16-hex SHA-256 hash; no
  raw PANs, names, emails, EBT/cash. (Wave 1 generation.)
- **Generalization** — every lake table publishes at
  category/subcategory/zone/period grain. No SKU on the peer
  side. No per-store on the peer side.
- **Structural k≥50** — every published cell carries `txn_count`
  and is suppressed if below 50 transactions. The
  Wave 1 T17 measurement (cabarrus_edge = 483 all-three
  cards) confirms the binding zone clears the floor by ~10×.
- **Suppression** — the k-ladder coarsens grain (subcat→cat,
  week→month) and drops cells that still can't clear k.
- **Viewer exclusion + relationship relabel** — `scope.py`
  drops viewer rows, relabels peers as `segment_peer` /
  `cross_segment`, and strips real `banner_code` from the
  agent surface (D24.1 identity strip).

### Deferred — with reason

- **l-diversity** (D21.3) — deferred. The k≥50 floor is the
  primary anonymity threshold for Wave 2; l-diversity (every
  cell contains ≥ℓ distinct sensitive values) is layered on
  top in a later wave when sensitivity classes are formalized.
- **Differential privacy** (D21.3, D24.3) — deferred. **No
  publish() seam shipped this wave.** The published aggregate
  numeric columns in the five lake tables ARE the future DP
  injection point — when DP is added later, Laplace noise is
  applied to the aggregate computation at build time, with no
  schema change. Building no-op DP enforcement scaffolding
  around an identity wrapper would be theater (the flaw of the
  old name-based suppression design); the absence of the seam
  is intentional, not an oversight.

### Honest limit — small-N pseudonymity (D24.1)

The panel has **5 merchants** (KRG, ACM, WDX, TBL, TJX). With
such a small N, the `peer_relationship` relabel is
**pseudonymization**, not true anonymity:

- A viewer can often de-anonymize a `segment_peer` by
  elimination. Kroger seeing two `segment_peer` rows in a zone
  knows the candidate set is {ACM, WDX}; with auxiliary context
  (store count, banner footprint), they may narrow further.
- For sole-of-segment merchants (TBL, TJX), there is no
  `segment_peer` to relabel — every other merchant is
  `cross_segment` and identifiable by elimination.

The aggregate cell still stays k≥50 (no individual *consumer*
exposed); the residual risk is **which competitor** a benchmark
names — a business-confidentiality matter, not PII. This is
the design's honest limit, not a bug to fix in this wave.

### D24.2 cohort spend posture

Cross-merchant cohort spend is published as **median + IQR
(p25/p75) + frequency band**, never as raw mean (D24.2). Means
concentrate on whale spend in tight cohorts; the median +
quartile bands are robust to that.

## Grain manifest (D23.7)

Per-table machine-readable spec consumed by Wave 3 agents. Reflects the post-scope agent surface (`peer_relationship` where the raw lake stores `banner_code`).

### `lake_category_metrics`

- Finest grain: `peer × category × subcategory × derived_zone × week`
- Dimensions: `peer_relationship`, `category`, `subcategory`, `derived_zone`, `period_start`, `grain`
- Metrics: `txn_count`, `price_index`, `revenue_index`, `units_index`, `basket_penetration_share`, `promo_active_share`, `wow_delta`
- k floor: 50
- Ladder: subcat_week → cat_week → cat_month → suppress
- Excludes:
    - no peer SKU (own side reaches SKU via the tenant surface)
    - no peer store_id
    - no per-customer rows
    - no daily grain (week is finest temporal)

### `lake_cross_merchant_cohorts`

- Finest grain: `derived_zone × cohort_combination`
- Dimensions: `derived_zone`, `cohort_combination`
- Metrics: `cohort_size`, `median_combined_spend`, `p25_combined_spend`, `p75_combined_spend`, `median_total_txns`, `frequency_band`, `txn_count`
- k floor: 50
- Ladder: single grain — cohort cells clear k by ~10× per T17
- Excludes:
    - NO raw mean spend (D24.2 concentration risk — median + IQR only)
    - no per-customer rows
    - no per-merchant breakdown of spend
    - no time grain (window-level only)

### `lake_payment_mix`

- Finest grain: `peer × derived_zone × month`
- Dimensions: `peer_relationship`, `derived_zone`, `month_start`
- Metrics: `txn_count`, `contactless_share`, `chip_share`, `swipe_share`, `manual_share`, `credit_share`, `debit_share`, `visa_share`, `mc_share`, `amex_share`, `discover_share`, `wallet_share`, `apple_share_within_wallet`, `google_share_within_wallet`, `samsung_share_within_wallet`, `wifi_share`, `ethernet_share`, `cellular_share`
- k floor: 50
- Ladder: single grain — ladder unused at full scale (~13.8k txn/cell)
- Excludes:
    - no per-customer rows
    - no weekly grain (month is finest)
    - no per-store breakdown

### `lake_segment_mix`

- Finest grain: `peer × behavioral_segment × derived_zone`
- Dimensions: `peer_relationship`, `behavioral_segment`, `derived_zone`
- Metrics: `n_cards`, `share_of_zone_at_banner`, `median_basket`, `median_freq`, `txn_count`
- k floor: 50
- Ladder: single grain
- Excludes:
    - no time grain (window-level only)
    - no per-customer rows
    - no peer SKU
    - segments are DERIVED behaviorally — not the planted loyalty_type

### `lake_trade_area`

- Finest grain: `peer × derived_zone × category`
- Dimensions: `peer_relationship`, `derived_zone`, `category`
- Metrics: `store_count`, `cell_units`, `cell_revenue`, `share_of_zone`, `zone_category_volume_index`, `txn_count`
- k floor: 50
- Ladder: single grain — zone-level cells comfortably above k
- Excludes:
    - no time grain (window-level only)
    - no peer subcategory
    - no per-customer rows

---

_The L1-L12 acceptance battery (`tests/lake/test_L*.py`) is the machine-checked counterpart to this human-readable report._
