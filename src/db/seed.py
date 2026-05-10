"""Load tenant CSVs into ``data/payments.db``.

The DB file is rebuilt from scratch on every run so the seed is idempotent.
Foreign-key enforcement is set per-connection (PRAGMA is not persisted).
The lake is not a physical layer in v2.5 — it's computed at query time
from the tenant tables (see ``src/lake/views.py``).

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
RAW = ROOT / "data" / "raw"

# Schema column lists. CSVs are projected to these columns at insert
# time so generation-only fields (if any are added in future phases)
# don't reach the DB.
TENANT_CUST_COLS = [
    "customer_id", "home_zip5", "behavioral_segment", "grocer_affinity_type",
    "primary_grocer", "secondary_grocer", "primary_card_type",
    "has_mobile_wallet", "signup_date",
]
TENANT_STORE_COLS = [
    "store_id", "merchant_id", "store_zip5", "neighborhood", "metro_region",
    "latitude", "longitude", "open_date",
]
TENANT_PROD_COLS = [
    "sku", "merchant_id", "name", "category", "subcategory", "base_price",
]
TENANT_TXN_COLS = [
    "txn_id", "merchant_id", "customer_id", "store_id", "terminal_id",
    "txn_ts", "payment_type", "card_network", "entry_mode", "wallet_type",
    "connectivity_type", "subtotal", "tax_total", "txn_total",
]
TENANT_ITEM_COLS = [
    "txn_id", "line_id", "sku", "qty", "unit_price", "discount", "tax",
    "line_total", "promo_id",
]
TENANT_PROMOTION_COLS = [
    "promo_id", "merchant_id", "sku", "start_date", "end_date",
    "discount_pct", "promo_name", "promo_type",
]

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
    merchants = pd.read_csv(RAW / "merchants.csv")
    _load(conn, merchants, MERCHANT_COLS, "merchants")

    tcust = pd.read_csv(RAW / "customers.csv",
                        dtype={"customer_id": str, "home_zip5": str})
    _load(conn, tcust, TENANT_CUST_COLS, "tenant_customers")

    tstores = pd.read_csv(RAW / "stores.csv",
                          dtype={"store_id": str, "store_zip5": str})
    _load(conn, tstores, TENANT_STORE_COLS, "tenant_stores")

    tprods = pd.read_csv(RAW / "products.csv")
    _load(conn, tprods, TENANT_PROD_COLS, "tenant_products")

    # tenant_promotions must be loaded BEFORE tenant_transaction_items so the
    # promo_id FK on items resolves on insert.
    tpromos = pd.read_csv(RAW / "promotions.csv",
                          dtype={"promo_id": str, "sku": str})
    _load(conn, tpromos, TENANT_PROMOTION_COLS, "tenant_promotions")

    ttxns = pd.read_csv(
        RAW / "transactions.csv",
        dtype={
            "customer_id": str, "txn_id": str, "store_id": str,
            "terminal_id": str,
        },
    )
    _load(conn, ttxns, TENANT_TXN_COLS, "tenant_transactions")

    titems = pd.read_csv(
        RAW / "transaction_items.csv",
        dtype={"txn_id": str, "sku": str, "promo_id": str},
    )
    _load(conn, titems, TENANT_ITEM_COLS, "tenant_transaction_items")

    conn.commit()
    conn.close()

    print(f"[seed] done in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
