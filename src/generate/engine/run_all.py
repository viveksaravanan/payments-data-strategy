"""v4 engine orchestrator. Drives the 8 layers top-down and emits
Parquet per the SPEC §5 output contract.

* ``data/raw/`` holds the tenant census (merchants, zones, stores,
  customers, products, transactions, transaction_items, promotions).
  Wave 2's lake reads from here.
* ``data/eval/`` holds ``anomalies_groundtruth`` only. Physically
  separated from ``data/raw/`` so the lake / agent can never glob
  it — eval/test infrastructure, not data.

Pilot mode (``--scale N`` or ``main(scale=N)``) runs ~5-10k cards
through the entire pipeline. Used for fast feedback during Stage
4 development; full scale (cfg.global_['population']['target_cards'],
default 100k) runs at Stage 5/6.

All randomness flows from ``cfg.global_['seed']`` threaded through
per-layer derived seeds — two runs at the same seed produce
byte-identical Parquet at the Stage-2 storage layer.

Sort keys per table (Stage 2 deterministic-write convention):
  merchants:              merchant_id
  zones:                  zone_id
  stores:                 store_id
  customers:              card_id
  products:               sku
  promotions:             (promo_id, sku)
  transactions:           txn_id
  transaction_items:      (txn_id, line_id)
  anomalies_groundtruth:  anomaly_id
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np
import pandas as pd

from src.generate.config.loader import load_config
from src.generate.engine.baskets import build_basket_items
from src.generate.engine.catalog import build_catalog
from src.generate.engine.customers import build_customers
from src.generate.engine.events import (
    apply_anomaly_filter,
    build_a2_boost_lookup,
    build_a3_basket_mult_lookup,
    build_anomaly_schedule,
    build_promo_id_lookup,
    build_promo_lookup,
    build_promo_schedule,
)
from src.generate.engine.geography import build_stores, build_zones
from src.generate.engine.payment import build_payment
from src.generate.engine.population import build_population
from src.generate.engine.pricing import build_priced_items
from src.generate.engine.trips import build_trips
from src.storage.duckdb_io import write_parquet

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_ROOT = REPO_ROOT / "src" / "generate" / "config"
DATA_RAW = REPO_ROOT / "data" / "raw"
DATA_EVAL = REPO_ROOT / "data" / "eval"


def _step(name: str, t0: float) -> float:
    now = time.time()
    print(f"  [{now - t0:>6.1f}s] {name}")
    return now


def build_all(scale: int | None = None) -> dict[str, pd.DataFrame]:
    """Build every table in memory and return them as a dict.

    ``scale`` overrides ``cfg.global_['population']['target_cards']``
    when provided (pilot mode).
    """
    cfg = load_config(CONFIG_ROOT)
    seed = int(cfg.global_["seed"])

    t0 = time.time()
    print(f"v4 engine — scale={'full' if scale is None else scale}")

    # ----- Static reference tables -----
    zones_df = build_zones(cfg)
    stores_df = build_stores(cfg, np.random.default_rng(seed))
    catalog_df = build_catalog(cfg, np.random.default_rng(seed + 10))
    _step("zones / stores / catalog", t0)

    # ----- Event schedules (D20) -----
    promo_schedule = build_promo_schedule(
        cfg, catalog_df, np.random.default_rng(seed + 20),
    )
    anomaly_schedule = build_anomaly_schedule(cfg, stores_df)
    promo_depth_lookup = build_promo_lookup(promo_schedule)
    promo_id_lookup = build_promo_id_lookup(promo_schedule)
    a2_boost_lookup = build_a2_boost_lookup(anomaly_schedule)
    a3_basket_mult_lookup = build_a3_basket_mult_lookup(anomaly_schedule)
    _step("event schedules", t0)

    # ----- Layers 2-4 (population → customers → trips) -----
    population = build_population(
        cfg, np.random.default_rng(seed), n_cards=scale,
    )
    customers = build_customers(
        cfg, population, np.random.default_rng(seed + 1),
    )
    trips = build_trips(
        cfg, population, customers, stores_df, zones_df,
        np.random.default_rng(seed + 2),
    )
    trips = apply_anomaly_filter(
        trips, anomaly_schedule, stores_df,
        np.random.default_rng(seed + 4),
    )
    _step(f"population/customers/trips ({len(trips):,} trips after A1)", t0)

    # ----- Layers 5-7 (baskets → payment → pricing) -----
    basket_items = build_basket_items(
        cfg, trips, customers, catalog_df,
        np.random.default_rng(seed + 3),
        promo_depth_lookup=promo_depth_lookup,
        a2_boost_lookup=a2_boost_lookup,
        a3_basket_mult_lookup=a3_basket_mult_lookup,
    )
    _step(f"basket items ({len(basket_items):,} lines)", t0)

    payment = build_payment(
        cfg, trips, customers, stores_df,
        np.random.default_rng(seed + 6),
    )
    priced_items = build_priced_items(
        cfg, basket_items, catalog_df, trips, stores_df, zones_df,
        np.random.default_rng(seed + 5),
        promo_depth_lookup=promo_depth_lookup,
        promo_id_lookup=promo_id_lookup,
    )
    _step("payment + priced items", t0)

    # ----- Assemble §5 contract tables -----
    merchants_df = pd.DataFrame([
        {
            "merchant_id":      m["banner_code"],
            "name":             m["name"],
            "segment":          m["segment"],
            "positioning_tier": m["positioning_tier"],
            "store_count":      m["store_count"],
        }
        for _, m in sorted(cfg.merchants.items(), key=lambda kv: kv[1]["banner_code"])
    ])

    # transactions: trips × payment × per-trip subtotal aggregation.
    subtotals = (
        priced_items.groupby("trip_id")
        .agg(
            subtotal=("line_total", "sum"),
            discount_total=("discount", "sum"),
            n_lines=("line_id", "count"),
        )
        .reset_index()
    )
    # Denormalize tender + network from customers (D16.3 keeps them
    # at the card level; the §5 transactions contract has them as
    # per-row columns so downstream queries don't need a join).
    transactions_df = (
        trips
        .merge(payment, on="trip_id")
        .merge(subtotals, on="trip_id")
        .merge(
            customers[["card_id", "tender", "network"]],
            on="card_id",
        )
        .rename(columns={"trip_id": "txn_id", "card_id": "customer_token"})
    )
    transaction_items_df = priced_items.rename(columns={"trip_id": "txn_id"})

    _step("contract tables assembled", t0)

    return {
        "merchants":              merchants_df,
        "zones":                  zones_df,
        "stores":                 stores_df,
        "customers":              customers,
        "products":               catalog_df,
        "transactions":           transactions_df,
        "transaction_items":      transaction_items_df,
        "promotions":             promo_schedule,
        "anomalies_groundtruth":  anomaly_schedule,
    }


_SORT_KEYS: dict[str, list[str]] = {
    "merchants":              ["merchant_id"],
    "zones":                  ["zone_id"],
    "stores":                 ["store_id"],
    "customers":              ["card_id"],
    "products":               ["sku"],
    "promotions":             ["promo_id", "sku"],
    "transactions":           ["txn_id"],
    "transaction_items":      ["txn_id", "line_id"],
    "anomalies_groundtruth":  ["anomaly_id"],
}


def write_all(tables: dict[str, pd.DataFrame]) -> None:
    """Write tables to data/raw/ and data/eval/ per the §5 contract."""
    DATA_RAW.mkdir(parents=True, exist_ok=True)
    DATA_EVAL.mkdir(parents=True, exist_ok=True)

    # Tenant census → data/raw/
    raw_tables = {
        k: v for k, v in tables.items() if k != "anomalies_groundtruth"
    }
    for name, df in raw_tables.items():
        path = DATA_RAW / f"{name}.parquet"
        write_parquet(df, path, sort_keys=_SORT_KEYS[name])
        print(f"  wrote {path.relative_to(REPO_ROOT)}  ({len(df):,} rows)")

    # Answer key → data/eval/  (NOT data/raw/, by physical separation)
    eval_df = tables["anomalies_groundtruth"]
    eval_path = DATA_EVAL / "anomalies_groundtruth.parquet"
    write_parquet(eval_df, eval_path, sort_keys=_SORT_KEYS["anomalies_groundtruth"])
    print(f"  wrote {eval_path.relative_to(REPO_ROOT)}  ({len(eval_df):,} rows)")


def main(scale: int | None = None) -> None:
    t0 = time.time()
    tables = build_all(scale=scale)
    print("Writing Parquet…")
    write_all(tables)
    print(f"Done in {time.time() - t0:.1f}s.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="v4 engine — generate Parquet tables")
    parser.add_argument(
        "--scale", type=int, default=None,
        help="Pilot scale (number of cards). Omit for full scale.",
    )
    args = parser.parse_args()
    main(scale=args.scale)
