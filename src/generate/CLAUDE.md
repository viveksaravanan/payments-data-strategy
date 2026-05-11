# Data generation

Module for synthetic data generation. Reads `parameters.py`. Writes CSVs to `data/raw/`.

## Architecture

- **`metro.py`** — Charlotte-metro geography helpers: ZIP5 → neighborhood
  → metro region lookup, ZIP centroid + jitter for store coordinates,
  per-segment store distribution. `assign_store_zips` accepts
  `require_zips=` so each grocer's `build()` can guarantee a store
  exists in the anomaly-anchor neighborhoods (University City for all
  three grocers; Plaza Midwood for Kroger).
- **`customers.py`** runs ONCE. Produces the shared 10,000-customer panel
  with `customer_id` (16-char SHA-256, generated directly here — no PAN,
  no PII), `home_zip5`, `behavioral_segment` (filler/stocker),
  `grocer_affinity_type` (loyalist/splitter/three_chain/lapsed_light),
  `primary_grocer`, `secondary_grocer`, `primary_card_type`,
  `has_mobile_wallet`, `signup_date`. All merchant generators consume
  this panel.
- **`transactions.py`** holds the unified Layer 4 transaction generator.
  Customer-centric loop (one pass over the 10K panel emitting all five
  merchants' transactions), Steps 4a–4l per `V2_5_DATA_DESIGN.md`:
  trip frequency, active-weeks variance, per-trip merchant choice,
  basket archetypes, per-category quantity distributions, payment
  generation. Anomaly hooks live here (UC keep-probability,
  Plaza Midwood avocado weight, pasta-promo SKU weight).
- **`kroger.py` / `acme.py` / `winn_dixie.py` / `taco_bell.py` /
  `tjmaxx.py`** — five thin per-merchant wrappers (~50 lines each).
  Each loads its catalog, builds its Charlotte stores, generates
  promotions (each grocer reserves one slot for the pinned pasta
  promo), and returns a `MerchantData` bundle for the unified
  generator.
- **`catalog_grocery.py`** + `catalog_acme.py` / `catalog_winn_dixie.py` /
  `catalog_taco_bell.py` / `catalog_tjmaxx.py` — base + per-grocer
  overlay model. Grocers share `data/catalogs/base_grocery_catalog.json`;
  per-grocer overlays in `data/catalogs/overlays/` set inclusion list
  and tier multipliers.
- **`promotions.py`** — `tenant_promotions` rows (random per-segment
  campaigns). Per-grocer counts: KRG 25 / ACM 20 / WDX 18 / TBL 6 /
  TJX 4. Each grocer's `build()` reserves one slot for the pinned
  pasta promo from `anomalies/`.
- **`tax.py`** — five-tier tax model (0% exempt grocery / 4% snacks &
  beverages / 7% non-food and prepared-food).
- **`anomalies/`** — three Phase 6 planted signals: University City
  decline (4-stage ramp), Plaza Midwood Kroger avocado spike (4-day
  pattern), coordinated pasta promos (KRG lift / Acme failure /
  WDX lift). See `DATA.md` §9 and `V2_5_DATA_DESIGN.md` "Planted
  Anomalies" for specs.
- **`run_all.py`** orchestrates: customers → 5 merchant generators →
  unified `transactions.generate_all()` → write CSVs.

## Cross-merchant invariant (critical)

`customer_id` MUST be stable for a given physical customer across all
merchants. It is the cross-merchant join key. Generated in `customers.py`
as `sha256(HASH_SECRET + synthetic_pan)[:16]` where `synthetic_pan` is
internal and never written to disk. Tested in `tests/test_generation.py`.

**Enforcement:** `customer_id` is generated only in `customers.py`.
Merchant generators read it; they never compute new ids.

## No PII, no EBT/cash/declines

The generator does not produce names, emails, raw PANs, or demographic
bands. `customer_id` is computed in `customers.py` from a synthetic PAN
that is never written to disk — there is no separate anonymization
stage. Payment generation is credit + debit only per Layer 1's
"captured rails" rule; cash, EBT, and declined transactions are out of
scope for v2.5.

## Reproducibility

All randomness is seeded from `parameters.RANDOM_SEED`. If you add a
generator, take a `numpy.random.Generator` argument — don't call
`np.random.*` directly. Direct calls use global state and break
reproducibility.

The transaction generator is the slow path. Default config produces
~230k–240k transactions in ~80 seconds. Volume target band is
180k–250k (`tests/test_generation.py::test_total_volume_in_design_target`).
If a change pushes over, vectorize with numpy.

## Outputs

- `data/raw/customers.csv` — `customer_id` and panel attributes
  (no PII)
- `data/raw/merchants.csv`
- `data/raw/stores.csv`
- `data/raw/products.csv`
- `data/raw/promotions.csv`
- `data/raw/transactions.csv` — all merchants, distinguished by
  `merchant_id`. References `customer_id` directly.
- `data/raw/transaction_items.csv`
