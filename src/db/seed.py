"""Load tenant + lake CSVs into ``data/payments.db``.

The DB file is rebuilt from scratch on every run so the seed is idempotent.
Foreign-key enforcement is set per-connection (PRAGMA is not persisted).

    uv run python -m src.db.seed
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "payments.db"
SCHEMA_PATH = ROOT / "src" / "db" / "schema.sql"
TENANT = ROOT / "data" / "anon" / "tenant"
LAKE = ROOT / "data" / "anon" / "lake"

# Schema column lists — CSVs may carry generation-only extras (e.g. ebt_eligible
# on Kroger products); we project to schema columns at insert time.
TENANT_CUST_COLS = [
    "customer_id", "age_band", "income_band", "home_zip5", "signup_date",
    "primary_card_type", "has_mobile_wallet",
]
TENANT_STORE_COLS = ["store_id", "merchant_id", "store_zip5", "region", "open_date"]
TENANT_PROD_COLS = [
    "sku", "merchant_id", "name", "category", "subcategory", "is_organic", "base_price",
]
TENANT_TXN_COLS = [
    "txn_id", "merchant_id", "customer_id", "store_id", "txn_ts",
    "payment_type", "card_network", "entry_mode", "wallet_type", "txn_total",
]
TENANT_ITEM_COLS = [
    "txn_id", "line_id", "sku", "qty", "unit_price", "discount", "line_total",
]
LAKE_CUST_COLS = [
    "customer_id", "age_band", "income_band", "home_zip3", "signup_date",
    "primary_card_type", "has_mobile_wallet",
]
LAKE_TXN_COLS = [
    "txn_id", "merchant_id", "customer_id", "store_zip3", "region",
    "txn_ts", "txn_hour_bucket", "payment_type", "card_network",
    "entry_mode", "wallet_type", "txn_total",
]
LAKE_ITEM_COLS = ["txn_id", "line_id", "sku_category", "qty", "unit_price", "line_total"]

MERCHANT_COLS = ["merchant_id", "name", "segment", "mcc"]


def _load(conn: sqlite3.Connection, df: pd.DataFrame, cols: list[str], table: str) -> None:
    df[cols].to_sql(table, conn, if_exists="append", index=False, chunksize=10_000)


def main() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()

    t0 = time.time()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text())

    print(f"[seed] applied schema to {DB_PATH}")

    # Parents first.
    merchants = pd.read_csv(TENANT / "merchants.csv")
    _load(conn, merchants, MERCHANT_COLS, "merchants")

    # Tenant layer.
    tcust = pd.read_csv(TENANT / "customers.csv",
                        dtype={"customer_id": str, "home_zip5": str})
    _load(conn, tcust, TENANT_CUST_COLS, "tenant_customers")

    tstores = pd.read_csv(TENANT / "stores.csv",
                          dtype={"store_id": str, "store_zip5": str})
    _load(conn, tstores, TENANT_STORE_COLS, "tenant_stores")

    tprods = pd.read_csv(TENANT / "products.csv")
    _load(conn, tprods, TENANT_PROD_COLS, "tenant_products")

    ttxns = pd.read_csv(TENANT / "transactions.csv",
                        dtype={"customer_id": str, "txn_id": str, "store_id": str})
    _load(conn, ttxns, TENANT_TXN_COLS, "tenant_transactions")

    titems = pd.read_csv(TENANT / "transaction_items.csv",
                         dtype={"txn_id": str, "sku": str})
    _load(conn, titems, TENANT_ITEM_COLS, "tenant_transaction_items")

    # Lake layer.
    lcust = pd.read_csv(LAKE / "customers.csv",
                        dtype={"customer_id": str, "home_zip3": str})
    _load(conn, lcust, LAKE_CUST_COLS, "lake_customers")

    ltxns = pd.read_csv(LAKE / "transactions.csv",
                        dtype={"customer_id": str, "txn_id": str, "store_zip3": str})
    _load(conn, ltxns, LAKE_TXN_COLS, "lake_transactions")

    litems = pd.read_csv(LAKE / "transaction_items.csv",
                         dtype={"txn_id": str})
    _load(conn, litems, LAKE_ITEM_COLS, "lake_transaction_items")

    conn.commit()
    conn.close()

    print(f"[seed] done in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
