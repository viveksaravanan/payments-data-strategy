# Data generation (v4)

Config-driven synthetic data generator. Reads YAML configs from
`config/`, runs the 8-layer causal pipeline through `engine/`,
emits Parquet to `data/raw/` (tenant census) and `data/eval/`
(answer key for the planted anomalies).

## Architecture

The whole generator is **parameterized by configuration** (D12).
Adding a 6th merchant or a new segment is a config addition — no
engine-code change. The engine is **segment-agnostic**.

### Configs (`config/`)

- **`global.yaml`** — seed, 90-day window, per-segment volume
  targets, expected_store_count, gravity model d0, population
  target_cards.
- **`metro.yaml`** — the 8 zones (D13.1) with residential weights,
  affluence, density, household/age skew, centroid lat/long.
- **`segments/{grocery,qsr,off_price}.yaml`** — per-segment
  archetype (distance-decay β per D13.4, catalog model).
- **`merchants/{kroger,acme,winn_dixie,taco_bell,tj_maxx}.yaml`** —
  banner name, banner_code, segment ref, positioning_tier,
  store_count, zone_placement_bias (matches D13.2 matrix).

D12 invariants enforced by `config/loader.py::load_config`:
residential weights sum to 1.0; every merchant segment resolves;
every zone_placement_bias references a real zone; store_count
equals sum(placement); total stores match global expected count;
volume targets exist and reconcile.

### Engine (`engine/`)

One module per D11 causal layer. Each reads from the layers above
and emits a DataFrame. `engine/run_all.py` orchestrates the chain
and writes Parquet at the end.

```
engine/
  geography.py    Layer 1 (D13): 29 stores in 8 zones + centroids
  population.py   Layer 2 (D14): ~100k cards w/ intensity tiers + cohort
  customers.py    Layer 3 (D16): home zone, affluence, loyalty, card
  trips.py        Layer 4 (D15+D15b): temporal placement + gravity store
  baskets.py      Layer 5 (D17): mission + affinity + staples + size
  payment.py      Layer 6 (D18): entry_mode + wallet_at_tap + connectivity
  pricing.py      Layer 7 (D19): anchor × strategy × zone × time × noise
  catalog.py      Layer 7 partner: per-merchant SKU table + base_price
  events.py       Layer 8 (D20): promos + A1-A3 anomalies w/ ground truth
  run_all.py      orchestrator + Parquet writers
```

### Output contract (SPEC §5)

- `data/raw/`: merchants, zones, stores, customers, products,
  transactions, transaction_items, promotions
- `data/eval/`: anomalies_groundtruth (NEVER in data/raw/ — physical
  separation per the Wave 1 plan amendment; Wave 2 lake reads
  data/raw/*.parquet only)

## Conventions

- All randomness flows from one `np.random.default_rng(cfg.global_['seed'])`
  per layer (with derived seeds). No direct `np.random.*` calls.
- Iteration is in sorted config order so two runs at the same seed
  produce content-identical Parquet (T18; Stage 2 deterministic-write
  conventions).
- Parquet writes go through `src/storage/duckdb_io.py` which pins
  pyarrow, uses single-threaded writes, sorts columns alphabetically,
  and accepts an explicit sort_keys.
- The engine builds rows in pandas/numpy; DuckDB is the **read/query**
  engine used by the acceptance tests + DQ report.

## Pilot mode

`engine/run_all.py --scale N` runs at N cards instead of the full
100k. Used for fast iteration during development and by the
`tests/data_quality/` battery (5k cards, ~5 min build).
