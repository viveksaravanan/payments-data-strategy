# Data generation

Module for synthetic data generation. Reads `parameters.py` and `MERCHANT_CONFIGS`; writes CSVs to `data/raw/`.

## Architecture

- **`customers.py`** runs ONCE. Produces the shared 5,000-row customer panel with PII, customer-level traits (filler/stocker), and `customer_pan`. All merchant generators consume this panel.
- **`base.py`** holds the parameterized basket and transaction generator: bimodal basket sizing, day/hour shaping, pay-cycle bumps, promo-day lift, payment-type sampling, affinity pairs.
- **`kroger.py` / `taco_bell.py` / `tjmaxx.py`** are thin (~50 lines each). Each loads its catalog, picks participating customers from the shared panel using `participation_rate`, and calls `base.py` with the merchant config.
- **`catalog_*.py`** modules build per-merchant SKU catalogs per the spec in `DATA.md` §8.
- **`run_all.py`** orchestrates: customers → kroger → taco_bell → tjmaxx → inject anomalies (Kroger only) → write CSVs.

## Cross-merchant invariant (critical)

`customer_pan` MUST be stable for a given physical customer across all merchants. This is what makes `customer_id = sha256(HASH_SECRET + customer_pan)[:16]` a cross-merchant join key in the lake. If `kroger.py` and `taco_bell.py` produce different PANs for the same customer, the cross-merchant join silently breaks.

**Enforcement:** `customer_pan` is generated only in `customers.py`. Merchant generators read it; they never create new PANs. Tested in `tests/test_generation.py`.

## PII

PII is INTENTIONAL in this stage. `customers.py` produces real-looking names and emails because the anonymization stage downstream needs something visible to strip. Do NOT anonymize anything in this module — that's the job of `src/anonymize/`.

## Reproducibility

All randomness is seeded from `parameters.RANDOM_SEED`. If you add a generator, take a `numpy.random.Generator` argument — don't call `np.random.*` directly. Direct calls use global state and break reproducibility.

The transaction generator is the slow path. Default config produces ~110k transactions; keep total runtime under 30 seconds. If a change pushes over, vectorize with numpy instead of using Python loops.

## Anomalies

Anomalies (price spike, store dropout, cohort surge) are intentional and controlled by `ANOMALY_INJECT`. They exist so the agent has something genuinely interesting to find. Do NOT "clean them up." All three are at Kroger (highest volume = easiest to find). See `DATA.md` §9 for specifics.

## EBT rule

EBT is allowed only at Kroger. SNAP rules generally exclude QSR and apparel retail. Taco Bell and TJ Maxx have no EBT key in their `payment_mix`. Tested.

EBT transactions also exclude prepared foods at Kroger — implement by filtering the basket sampler when `payment_type == "ebt"`. This makes the data more credible and gives the agent a real pattern to find.

## Outputs

- `data/raw/customers.csv` — with PII (`customer_name`, `customer_email`, `home_zip5`, `customer_pan`)
- `data/raw/merchants.csv`
- `data/raw/stores.csv`
- `data/raw/products.csv`
- `data/raw/transactions.csv` — all merchants, distinguished by `merchant_id`
- `data/raw/transaction_items.csv`
