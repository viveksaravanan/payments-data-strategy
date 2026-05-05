"""Generation orchestrator.

    uv run python -m src.generate.run_all

Generates the shared customer panel, runs all three merchant generators, injects
the three planted Kroger anomalies (DATA.md §9), and writes six CSVs to
``data/raw/``.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from . import kroger, parameters as P, taco_bell, tjmaxx
from .customers import generate_customers

OUT_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"


# ---------------------------------------------------------------------------
# Anomalies — DATA.md §9 (Kroger only)
# ---------------------------------------------------------------------------

def _inject_price_spike(
    transactions: pd.DataFrame,
    items: pd.DataFrame,
    rng: np.random.Generator,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Anomaly 1 — KRG-PRODUCE-0042 at 5× base price on one day in last 7 days."""
    target_sku = "KRG-PRODUCE-0042"
    spike_day = (P.END_DATE - timedelta(days=int(rng.integers(1, 7)))).isoformat()
    txn_ids_on_day = transactions.loc[
        (transactions["merchant_id"] == "KRG")
        & transactions["txn_ts"].str.startswith(spike_day),
        "txn_id",
    ].to_numpy()
    if len(txn_ids_on_day) == 0:
        return transactions, items

    mask = items["txn_id"].isin(txn_ids_on_day) & (items["sku"] == target_sku)
    if not mask.any():
        return transactions, items

    items.loc[mask, "unit_price"] = (items.loc[mask, "unit_price"] * 5).round(2)
    items.loc[mask, "line_total"] = (
        items.loc[mask, "qty"] * items.loc[mask, "unit_price"] - items.loc[mask, "discount"]
    ).round(2)

    affected = items.loc[mask, "txn_id"].unique()
    new_totals = (
        items[items["txn_id"].isin(affected)]
        .groupby("txn_id")["line_total"].sum().round(2)
    )
    transactions = transactions.set_index("txn_id")
    transactions.loc[new_totals.index, "txn_total"] = new_totals
    transactions = transactions.reset_index()
    return transactions, items


def _inject_store_dropout(
    transactions: pd.DataFrame,
    items: pd.DataFrame,
    rng: np.random.Generator,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Anomaly 2 — drop 30% of KRG-OH-0011 transactions over the last 7 days."""
    target_store = "KRG-OH-0011"
    cutoff = (P.END_DATE - timedelta(days=6)).isoformat()
    mask = (
        (transactions["merchant_id"] == "KRG")
        & (transactions["store_id"] == target_store)
        & (transactions["txn_ts"] >= cutoff)
    )
    candidates = transactions.loc[mask, "txn_id"].to_numpy()
    if len(candidates) == 0:
        return transactions, items

    n_drop = int(round(len(candidates) * 0.30))
    if n_drop == 0:
        return transactions, items
    drop_ids = rng.choice(candidates, size=n_drop, replace=False)
    transactions = transactions[~transactions["txn_id"].isin(drop_ids)].reset_index(drop=True)
    items = items[~items["txn_id"].isin(drop_ids)].reset_index(drop=True)
    return transactions, items


def _inject_baby_cohort(
    transactions: pd.DataFrame,
    items: pd.DataFrame,
    catalog: pd.DataFrame,
    rng: np.random.Generator,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Anomaly 3 — ~50 customers begin buying baby SKUs in the last 21 days."""
    cutoff = (P.END_DATE - timedelta(days=20)).isoformat()
    krg_mask = transactions["merchant_id"] == "KRG"
    last21_mask = krg_mask & (transactions["txn_ts"] >= cutoff)

    baby_skus = catalog.loc[catalog["category"] == "BABY", "sku"].to_numpy()
    if len(baby_skus) == 0:
        return transactions, items

    # Customers who never bought BABY items at Kroger.
    krg_items = items.merge(
        transactions[krg_mask][["txn_id", "customer_pan"]],
        on="txn_id", how="inner",
    )
    baby_buyers = set(krg_items.loc[krg_items["sku"].isin(baby_skus), "customer_pan"])
    krg_active = set(transactions.loc[last21_mask, "customer_pan"])
    eligible = list(krg_active - baby_buyers)
    if not eligible:
        return transactions, items

    n_pick = min(50, len(eligible))
    chosen = rng.choice(eligible, size=n_pick, replace=False)

    target_txns = transactions[last21_mask & transactions["customer_pan"].isin(chosen)]
    if target_txns.empty:
        return transactions, items

    # For each chosen customer, append a baby SKU as a new line item to each of
    # their last-21-day Kroger transactions. Recompute txn_total afterward.
    sku_prices = dict(zip(catalog["sku"], catalog["base_price"]))
    next_line_per_txn = (
        items[items["txn_id"].isin(target_txns["txn_id"])]
        .groupby("txn_id")["line_id"].max().to_dict()
    )

    new_rows = []
    for txn_id in target_txns["txn_id"].to_numpy():
        sku = str(rng.choice(baby_skus))
        base_price = sku_prices[sku]
        unit_price = round(base_price * (1.0 + (rng.random() * 2 - 1) * P.PRICE_NOISE_FRAC), 2)
        next_line = int(next_line_per_txn.get(txn_id, 0)) + 1
        new_rows.append({
            "txn_id":     txn_id,
            "line_id":    next_line,
            "sku":        sku,
            "qty":        1,
            "unit_price": unit_price,
            "discount":   0.0,
            "line_total": unit_price,
        })
    items = pd.concat([items, pd.DataFrame(new_rows)], ignore_index=True)

    affected = [r["txn_id"] for r in new_rows]
    new_totals = (
        items[items["txn_id"].isin(affected)]
        .groupby("txn_id")["line_total"].sum().round(2)
    )
    transactions = transactions.set_index("txn_id")
    transactions.loc[new_totals.index, "txn_total"] = new_totals
    transactions = transactions.reset_index()
    return transactions, items


def inject_anomalies(
    transactions: pd.DataFrame,
    items: pd.DataFrame,
    krg_catalog: pd.DataFrame,
    rng: np.random.Generator,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    transactions, items = _inject_price_spike(transactions, items, rng)
    transactions, items = _inject_store_dropout(transactions, items, rng)
    transactions, items = _inject_baby_cohort(transactions, items, krg_catalog, rng)
    return transactions, items


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    rng = np.random.default_rng(P.RANDOM_SEED)

    print("Generating customer panel...")
    customers_df = generate_customers(rng)

    merchants_df = pd.DataFrame([
        {"merchant_id": "KRG", "name": "Kroger",    "segment": "grocery",        "mcc": "5411"},
        {"merchant_id": "TBL", "name": "Taco Bell", "segment": "qsr",            "mcc": "5814"},
        {"merchant_id": "TJX", "name": "TJ Maxx",   "segment": "retail_offprice", "mcc": "5651"},
    ])

    print("Generating Kroger transactions...")
    krg_catalog, krg_stores, krg_txns, krg_items = kroger.generate(customers_df, rng)
    print(f"  {len(krg_txns):,} transactions, {len(krg_items):,} items")

    print("Generating Taco Bell transactions...")
    tbl_catalog, tbl_stores, tbl_txns, tbl_items = taco_bell.generate(customers_df, rng)
    print(f"  {len(tbl_txns):,} transactions, {len(tbl_items):,} items")

    print("Generating TJ Maxx transactions...")
    tjx_catalog, tjx_stores, tjx_txns, tjx_items = tjmaxx.generate(customers_df, rng)
    print(f"  {len(tjx_txns):,} transactions, {len(tjx_items):,} items")

    products_df = pd.concat([krg_catalog, tbl_catalog, tjx_catalog], ignore_index=True)
    stores_df = pd.concat([krg_stores, tbl_stores, tjx_stores], ignore_index=True)
    transactions_df = pd.concat([krg_txns, tbl_txns, tjx_txns], ignore_index=True)
    items_df = pd.concat([krg_items, tbl_items, tjx_items], ignore_index=True)

    if P.ANOMALY_INJECT:
        print("Injecting Kroger anomalies (price spike, store dropout, baby cohort)...")
        transactions_df, items_df = inject_anomalies(transactions_df, items_df, krg_catalog, rng)

    print(f"Writing CSVs to {OUT_DIR}/ ...")
    customers_df.to_csv(OUT_DIR / "customers.csv", index=False)
    merchants_df.to_csv(OUT_DIR / "merchants.csv", index=False)
    stores_df.to_csv(OUT_DIR / "stores.csv", index=False)
    products_df.to_csv(OUT_DIR / "products.csv", index=False)
    transactions_df.to_csv(OUT_DIR / "transactions.csv", index=False)
    items_df.to_csv(OUT_DIR / "transaction_items.csv", index=False)

    elapsed = time.time() - t0
    print(
        f"Done in {elapsed:.1f}s · "
        f"{len(customers_df):,} customers · {len(stores_df)} stores · "
        f"{len(products_df):,} SKUs · {len(transactions_df):,} txns · "
        f"{len(items_df):,} line items"
    )


if __name__ == "__main__":
    main()
