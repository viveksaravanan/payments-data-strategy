"""Stage 2 — Lake processing.

Reads ``data/anon/tenant/`` and writes ``data/anon/lake/``. Operations:

  * ZIP5 -> ZIP3 (customers, stores).
  * Add ``txn_hour_bucket`` alongside the original ``txn_ts``.
  * Downgrade transaction items from SKU level to category level.
  * Apply k=5 anonymity to ``(age_band, income_band, home_zip3)`` — small
    groups have ``home_zip3`` set to NULL, with a logged suppression count.

Stores and products do not exist as separate lake tables; ``store_zip3`` and
``region`` are denormalized into ``lake_transactions``, and SKU-level detail
collapses to ``sku_category`` in ``lake_transaction_items``.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.generate.parameters import K_ANONYMITY_THRESHOLD

from .generalize import truncate_zip  # noqa: F401  (truncate_zip used inline below)

ROOT = Path(__file__).resolve().parents[2]
TENANT = ROOT / "data" / "anon" / "tenant"
LAKE = ROOT / "data" / "anon" / "lake"

LAKE_CUSTOMER_COLS = [
    "customer_id", "age_band", "income_band", "home_zip3", "signup_date",
    "primary_card_type", "has_mobile_wallet",
]
LAKE_TXN_COLS = [
    "txn_id", "merchant_id", "customer_id", "store_zip3", "region",
    "txn_ts", "txn_hour_bucket", "payment_type", "card_network",
    "entry_mode", "wallet_type", "txn_total",
]
LAKE_ITEM_COLS = ["txn_id", "line_id", "sku_category", "qty", "unit_price", "line_total"]


def _apply_k_anonymity(customers: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    counts = customers.groupby(
        ["age_band", "income_band", "home_zip3"], dropna=False
    ).transform("size")
    suppress = counts < K_ANONYMITY_THRESHOLD
    n = int(suppress.sum())
    customers = customers.copy()
    customers.loc[suppress, "home_zip3"] = pd.NA
    return customers, n


def _aggregate_items_to_category(
    items: pd.DataFrame, products: pd.DataFrame
) -> pd.DataFrame:
    merged = items.merge(products[["sku", "category"]], on="sku", how="inner")
    grouped = merged.groupby(["txn_id", "category"], as_index=False).agg(
        qty=("qty", "sum"),
        line_total=("line_total", "sum"),
    )
    grouped["unit_price"] = (grouped["line_total"] / grouped["qty"]).round(2)
    grouped["line_id"] = grouped.groupby("txn_id").cumcount() + 1
    grouped = grouped.rename(columns={"category": "sku_category"})
    return grouped[LAKE_ITEM_COLS]


def run() -> None:
    LAKE.mkdir(parents=True, exist_ok=True)

    # ---- Customers: ZIP3 + k-anonymity ----
    customers = pd.read_csv(TENANT / "customers.csv",
                            dtype={"customer_id": str, "home_zip5": str})
    customers["home_zip3"] = customers["home_zip5"].str[:3]
    customers, suppressed = _apply_k_anonymity(customers)
    customers[LAKE_CUSTOMER_COLS].to_csv(LAKE / "customers.csv", index=False)
    print(
        f"[lake] k-anonymity (k={K_ANONYMITY_THRESHOLD}): "
        f"suppressed home_zip3 for {suppressed:,} of {len(customers):,} customers"
    )

    # ---- Transactions: denormalize store_zip3+region, add hour bucket ----
    txns = pd.read_csv(TENANT / "transactions.csv",
                       dtype={"customer_id": str, "txn_id": str, "store_id": str})
    stores = pd.read_csv(TENANT / "stores.csv",
                         dtype={"store_id": str, "store_zip5": str})
    txns = txns.merge(stores[["store_id", "store_zip5", "region"]], on="store_id", how="left")
    txns["store_zip3"] = txns["store_zip5"].str[:3]
    txns["txn_hour_bucket"] = txns["txn_ts"].str.slice(0, 13) + ":00:00"
    txns[LAKE_TXN_COLS].to_csv(LAKE / "transactions.csv", index=False)
    print(f"[lake] wrote {len(txns):,} transactions")

    # ---- Items: collapse to category level ----
    items = pd.read_csv(TENANT / "transaction_items.csv",
                        dtype={"txn_id": str, "sku": str})
    products = pd.read_csv(TENANT / "products.csv")
    lake_items = _aggregate_items_to_category(items, products)
    lake_items.to_csv(LAKE / "transaction_items.csv", index=False)
    print(
        f"[lake] aggregated {len(items):,} SKU lines -> "
        f"{len(lake_items):,} category lines"
    )


if __name__ == "__main__":
    run()
