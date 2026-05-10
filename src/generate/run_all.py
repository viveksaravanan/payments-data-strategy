"""Generation orchestrator.

    uv run python -m src.generate.run_all

Per-merchant modules provide static data (catalog, stores, promos,
affinity rule); the transaction loop is customer-centric and runs
once across all five merchants.

The three planted anomalies (University City decline, Plaza Midwood
Kroger avocado spike, coordinated pasta promos with divergent
outcomes) are wired in at generation time:

  - University City decline + avocado spike: the transaction
    generator (``transactions.py``) consults
    ``src.generate.anomalies`` per trip — University City decline
    gates trip emission via a keep-probability draw, avocado spike
    biases SKU sampling within PRODUCE.
  - Pasta promos: each grocer's ``build()`` appends a pinned pasta
    promo (``acme_pasta_promo.pinned_pasta_promo``) to its tenant
    promotions. The basket-level pasta multiplier biases SKU
    sampling within PANTRY during the promo window.
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import (
    acme, kroger, parameters as P, taco_bell, tjmaxx, transactions, winn_dixie,
)
from .customers import generate_customers

OUT_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    rng = np.random.default_rng(P.RANDOM_SEED)

    print("Generating customer panel...")
    customers_df = generate_customers(rng)

    merchants_df = pd.DataFrame([
        {"merchant_id": "KRG", "name": "Kroger",     "segment": "grocery",          "mcc": "5411"},
        {"merchant_id": "ACM", "name": "Acme",       "segment": "grocery",          "mcc": "5411"},
        {"merchant_id": "WDX", "name": "Winn-Dixie", "segment": "grocery",          "mcc": "5411"},
        {"merchant_id": "TBL", "name": "Taco Bell",  "segment": "qsr",              "mcc": "5814"},
        {"merchant_id": "TJX", "name": "TJ Maxx",    "segment": "off_price_retail", "mcc": "5651"},
    ])

    print("Building per-merchant static data...")
    merchant_data = {
        "kroger":     kroger.build(rng),
        "acme":       acme.build(rng),
        "winn_dixie": winn_dixie.build(rng),
        "taco_bell":  taco_bell.build(rng),
        "tjmaxx":     tjmaxx.build(rng),
    }

    products_df = pd.concat(
        [md.catalog for md in merchant_data.values()], ignore_index=True
    )
    stores_df = pd.concat(
        [md.stores for md in merchant_data.values()], ignore_index=True
    )
    promotions_df = pd.concat(
        [md.promotions for md in merchant_data.values()], ignore_index=True
    )

    print("Generating transactions (Layer 4 flow)...")
    transactions_df, items_df = transactions.generate_all(
        customers_df, merchant_data, rng
    )
    per_merchant_counts = transactions_df.groupby("merchant_id").size().to_dict()
    for mid in ("KRG", "ACM", "WDX", "TBL", "TJX"):
        print(f"  {mid}: {per_merchant_counts.get(mid, 0):,} transactions")

    print(f"Writing CSVs to {OUT_DIR}/ ...")
    customers_df.to_csv(OUT_DIR / "customers.csv", index=False)
    merchants_df.to_csv(OUT_DIR / "merchants.csv", index=False)
    stores_df.to_csv(OUT_DIR / "stores.csv", index=False)
    products_df.to_csv(OUT_DIR / "products.csv", index=False)
    promotions_df.to_csv(OUT_DIR / "promotions.csv", index=False)
    transactions_df.to_csv(OUT_DIR / "transactions.csv", index=False)
    items_df.to_csv(OUT_DIR / "transaction_items.csv", index=False)

    elapsed = time.time() - t0
    print(
        f"Done in {elapsed:.1f}s · "
        f"{len(customers_df):,} customers · {len(stores_df)} stores · "
        f"{len(products_df):,} SKUs · "
        f"{promotions_df['promo_id'].nunique() if not promotions_df.empty else 0} promos · "
        f"{len(transactions_df):,} txns · {len(items_df):,} line items"
    )


if __name__ == "__main__":
    main()
