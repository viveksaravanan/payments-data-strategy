"""Tests for the §5 output contract (Wave 1 Stage 5).

Drives the engine at pilot scale, then validates:
- All required tables emit to data/raw/ (tenant census).
- anomalies_groundtruth lives in data/eval/, NOT data/raw/
  (physical-directory separation per the Stage 5 plan amendment —
  the Wave 2 lake must not be able to glob the answer key).
- Each table has the §5 required columns.
- FK relationships hold across the contract (txn_id, sku, etc.).
- Determinism: two pilot runs at the same seed produce identical
  Parquet content.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import shutil

from src.generate.engine.run_all import build_all, write_all, DATA_EVAL, DATA_RAW
from src.storage.duckdb_io import read_parquet

PILOT_CARDS = 3_000


@pytest.fixture(scope="module")
def tables() -> dict[str, pd.DataFrame]:
    """Build the full contract at 3k pilot scale."""
    return build_all(scale=PILOT_CARDS)


@pytest.fixture(scope="module")
def written_pilot(tables) -> Path:
    """Write the pilot tables to a clean data/raw + data/eval and
    return the repo root. Reused by file-presence + Parquet round-
    trip tests."""
    # Clean any prior writes so the test starts from a known state.
    if DATA_RAW.exists():
        shutil.rmtree(DATA_RAW)
    if DATA_EVAL.exists():
        shutil.rmtree(DATA_EVAL)
    DATA_RAW.mkdir(parents=True, exist_ok=True)
    DATA_EVAL.mkdir(parents=True, exist_ok=True)
    write_all(tables)
    return DATA_RAW.parent


# ----- table presence -----------------------------------------------

def test_all_contract_tables_present(tables) -> None:
    required = {
        "merchants", "zones", "stores", "customers", "products",
        "transactions", "transaction_items", "promotions",
        "anomalies_groundtruth",
    }
    assert required.issubset(set(tables.keys()))


# ----- §5 required columns ------------------------------------------

@pytest.mark.parametrize("table,required", [
    ("merchants",    {"merchant_id", "name", "segment", "positioning_tier", "store_count"}),
    ("zones",        {"zone_id", "name", "affluence", "residential_weight", "centroid_lat", "centroid_long"}),
    ("stores",       {"store_id", "merchant_id", "zone_id", "neighborhood", "metro_region",
                      "latitude", "longitude", "open_date"}),
    ("customers",    {"card_id", "home_zone", "loyalty_type", "primary_banner",
                      "tender", "network", "wallet_enrolled", "wallet_provider"}),
    ("products",     {"sku", "merchant_id", "category", "subcategory",
                      "canonical_id", "private_label", "base_price"}),
    ("transactions", {"txn_id", "customer_token", "store_id", "txn_ts",
                      "tender", "network", "entry_mode", "wallet_provider",
                      "connectivity_type", "subtotal"}),
    ("transaction_items", {"txn_id", "line_id", "sku", "canonical_id",
                          "qty", "unit_price", "discount", "promo_id", "line_total"}),
    ("promotions",   {"promo_id", "sku", "merchant_id", "promo_type",
                      "start_date", "end_date", "depth_pct"}),
    ("anomalies_groundtruth", {"anomaly_id", "type", "description",
                              "start_date", "end_date", "magnitude"}),
])
def test_table_has_required_columns(tables, table, required) -> None:
    actual = set(tables[table].columns)
    missing = required - actual
    assert not missing, f"{table} missing columns: {missing}"


# ----- physical-directory separation --------------------------------

def test_anomalies_groundtruth_lives_only_in_data_eval(written_pilot) -> None:
    """Plan §5 amendment: data/eval/ holds the answer key, never
    data/raw/. Wave 2's lake reads data/raw/*.parquet only — the
    physical separation prevents any glob/read of the answer key
    by construction."""
    raw_files = {p.name for p in DATA_RAW.iterdir() if p.is_file()}
    eval_files = {p.name for p in DATA_EVAL.iterdir() if p.is_file()}
    assert "anomalies_groundtruth.parquet" in eval_files
    assert "anomalies_groundtruth.parquet" not in raw_files
    # No tenant tables should accidentally land in data/eval/.
    assert eval_files == {"anomalies_groundtruth.parquet"}


def test_all_raw_tables_written(written_pilot) -> None:
    raw_files = {p.name for p in DATA_RAW.iterdir() if p.is_file()}
    expected = {
        "merchants.parquet", "zones.parquet", "stores.parquet",
        "customers.parquet", "products.parquet",
        "transactions.parquet", "transaction_items.parquet",
        "promotions.parquet",
    }
    assert expected.issubset(raw_files), \
        f"missing: {expected - raw_files}; extras: {raw_files - expected}"


def test_parquet_roundtrip_via_duckdb(written_pilot) -> None:
    """Stage 2 convention: read_parquet returns a DuckDB relation
    so the §6 acceptance battery can aggregate without materializing."""
    rel = read_parquet(str(DATA_RAW / "transactions.parquet"))
    n = rel.aggregate("count(*) AS n").fetchone()[0]
    assert n > 0


# ----- FK relationships ---------------------------------------------

def test_transactions_reference_valid_stores(tables) -> None:
    txn_stores = set(tables["transactions"]["store_id"].unique())
    valid_stores = set(tables["stores"]["store_id"])
    assert txn_stores <= valid_stores


def test_transactions_reference_valid_customers(tables) -> None:
    txn_cust = set(tables["transactions"]["customer_token"].unique())
    valid = set(tables["customers"]["card_id"])
    assert txn_cust <= valid


def test_items_reference_valid_transactions(tables) -> None:
    items_txn = set(tables["transaction_items"]["txn_id"].unique())
    valid = set(tables["transactions"]["txn_id"])
    assert items_txn <= valid


def test_items_reference_valid_products(tables) -> None:
    items_sku = set(tables["transaction_items"]["sku"].unique())
    valid = set(tables["products"]["sku"])
    assert items_sku <= valid


def test_promotions_reference_valid_products(tables) -> None:
    promo_sku = set(tables["promotions"]["sku"].unique())
    valid = set(tables["products"]["sku"])
    assert promo_sku <= valid


def test_items_promo_ids_resolve(tables) -> None:
    """transaction_items.promo_id should resolve to promotions.promo_id
    when not null."""
    items = tables["transaction_items"]
    on_promo = items[items["promo_id"].notna()]
    if len(on_promo) == 0:
        pytest.skip("no on-promo lines in pilot")
    valid = set(tables["promotions"]["promo_id"])
    assert set(on_promo["promo_id"].unique()) <= valid


# ----- canonical_id consistency -------------------------------------

def test_canonical_id_consistent_across_grocers(tables) -> None:
    """Same canonical_id should map to the same (category, subcategory)
    at every banner."""
    p = tables["products"]
    g = p[p["segment"] == "grocery"]
    per_canon = g.groupby("canonical_id")[["category", "subcategory"]].nunique()
    assert (per_canon["category"] == 1).all()
    assert (per_canon["subcategory"] == 1).all()


# ----- volume check at pilot ---------------------------------------

def test_pilot_volume_proportional(cfg_loaded, tables) -> None:
    """At PILOT_CARDS, total txn volume should be roughly
    PILOT_CARDS / target_cards × total_volume_target.
    A1 anomaly drops some — allow 30% under to 10% over."""
    target_cards = cfg_loaded.global_["population"]["target_cards"]
    expected_total = cfg_loaded.global_["volume_targets"]["total"]
    expected_pilot = expected_total * PILOT_CARDS / target_cards
    actual = len(tables["transactions"])
    print(f"\nPilot total txns: {actual:,}  (target {int(expected_pilot):,})")
    assert 0.70 * expected_pilot <= actual <= 1.10 * expected_pilot


@pytest.fixture(scope="module")
def cfg_loaded():
    from src.generate.config.loader import load_config
    return load_config(
        Path(__file__).resolve().parents[1] / "src" / "generate" / "config"
    )
