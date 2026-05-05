"""Stage 1 — Tenant processing.

Reads ``data/raw/`` and writes ``data/anon/tenant/``. PII columns
(``customer_name``, ``customer_email``, ``customer_pan``) are dropped from the
customers table; the PAN is hashed to a stable ``customer_id`` and that field
replaces ``customer_pan`` everywhere it appeared. Everything else is preserved
at full granularity (full ZIP5, full timestamps, SKU-level baskets).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .hash import customer_id_for

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
TENANT = ROOT / "data" / "anon" / "tenant"

TENANT_CUSTOMER_COLS = [
    "customer_id", "age_band", "income_band", "home_zip5", "signup_date",
    "primary_card_type", "has_mobile_wallet",
]
TENANT_STORE_COLS = ["store_id", "merchant_id", "store_zip5", "region", "open_date"]
TENANT_PRODUCT_COLS = [
    "sku", "merchant_id", "name", "category", "subcategory", "is_organic", "base_price",
]
TENANT_TXN_COLS = [
    "txn_id", "merchant_id", "customer_id", "store_id", "txn_ts",
    "payment_type", "card_network", "entry_mode", "wallet_type", "txn_total",
]
TENANT_ITEM_COLS = [
    "txn_id", "line_id", "sku", "qty", "unit_price", "discount", "line_total",
]
MERCHANT_COLS = ["merchant_id", "name", "segment", "mcc"]


def run() -> None:
    TENANT.mkdir(parents=True, exist_ok=True)

    customers = pd.read_csv(RAW / "customers.csv",
                            dtype={"customer_pan": str, "home_zip5": str})
    customers["customer_id"] = customers["customer_pan"].map(customer_id_for)
    customers[TENANT_CUSTOMER_COLS].to_csv(TENANT / "customers.csv", index=False)

    pan_to_id = dict(zip(customers["customer_pan"], customers["customer_id"]))

    transactions = pd.read_csv(RAW / "transactions.csv",
                               dtype={"customer_pan": str, "txn_id": str,
                                      "store_id": str})
    transactions["customer_id"] = transactions["customer_pan"].map(pan_to_id)
    transactions[TENANT_TXN_COLS].to_csv(TENANT / "transactions.csv", index=False)

    items = pd.read_csv(RAW / "transaction_items.csv", dtype={"txn_id": str, "sku": str})
    items[TENANT_ITEM_COLS].to_csv(TENANT / "transaction_items.csv", index=False)

    stores = pd.read_csv(RAW / "stores.csv",
                         dtype={"store_id": str, "store_zip5": str})
    stores[TENANT_STORE_COLS].to_csv(TENANT / "stores.csv", index=False)

    products = pd.read_csv(RAW / "products.csv")
    products[TENANT_PRODUCT_COLS].to_csv(TENANT / "products.csv", index=False)

    merchants = pd.read_csv(RAW / "merchants.csv")
    merchants[MERCHANT_COLS].to_csv(TENANT / "merchants.csv", index=False)

    print(f"[tenant] wrote {len(customers):,} customers, "
          f"{len(transactions):,} transactions, {len(items):,} items "
          f"to {TENANT}")


if __name__ == "__main__":
    run()
