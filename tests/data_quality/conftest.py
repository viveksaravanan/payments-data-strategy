"""Shared fixtures for the Wave 1 §6 acceptance battery (T1-T18).

Two modes:
1. **Existing Parquet on disk** — if ``data/raw/transactions.parquet``
   already exists, the fixture loads tables directly from disk.
   This is the path used after a deliberate full-scale generation
   (``uv run python -m src.generate.engine.run_all``).
2. **Pilot rebuild** — otherwise, build at the scale specified by
   the ``WAVE1_TEST_SCALE`` env var (default 5000 cards). Used in
   CI / quick iteration where the full 100k build is too slow.

Scale is computed dynamically from the actual customers table size
so the T-tests work at any scale (5k pilot, 100k full, anywhere
in between).
"""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import pytest

from src.generate.config.loader import load_config
from src.generate.engine.run_all import build_all, write_all, DATA_RAW, DATA_EVAL

DEFAULT_PILOT_CARDS = 5_000
CONFIG_ROOT = Path(__file__).resolve().parents[2] / "src" / "generate" / "config"

_RAW_TABLE_NAMES = [
    "merchants", "zones", "stores", "customers", "products",
    "transactions", "transaction_items", "promotions",
]


def _load_existing_tables() -> dict[str, pd.DataFrame]:
    tables = {
        name: pd.read_parquet(DATA_RAW / f"{name}.parquet")
        for name in _RAW_TABLE_NAMES
    }
    tables["anomalies_groundtruth"] = pd.read_parquet(
        DATA_EVAL / "anomalies_groundtruth.parquet"
    )
    return tables


@pytest.fixture(scope="session")
def cfg():
    return load_config(CONFIG_ROOT)


@pytest.fixture(scope="session")
def pilot_tables(cfg) -> dict[str, pd.DataFrame]:
    """Return the engine output tables.

    If data/raw/ already contains a full set of Parquet files,
    they're loaded as-is — preserves intentional full-scale runs.
    Otherwise builds at WAVE1_TEST_SCALE (default 5000).
    """
    if (DATA_RAW / "transactions.parquet").exists():
        return _load_existing_tables()

    import shutil
    if DATA_RAW.exists():
        shutil.rmtree(DATA_RAW)
    if DATA_EVAL.exists():
        shutil.rmtree(DATA_EVAL)
    DATA_RAW.mkdir(parents=True, exist_ok=True)
    DATA_EVAL.mkdir(parents=True, exist_ok=True)

    scale = int(os.environ.get("WAVE1_TEST_SCALE", str(DEFAULT_PILOT_CARDS)))
    tables = build_all(scale=scale)
    write_all(tables)
    return tables


@pytest.fixture(scope="session")
def scale_frac(cfg, pilot_tables) -> float:
    """Actual scale as a fraction of the full target (100k cards).
    1.0 = full-scale; 0.05 = 5k pilot."""
    target = cfg.global_["population"]["target_cards"]
    return len(pilot_tables["customers"]) / target


# Per-table accessor fixtures for terser test code
@pytest.fixture(scope="session")
def merchants(pilot_tables):    return pilot_tables["merchants"]
@pytest.fixture(scope="session")
def zones(pilot_tables):        return pilot_tables["zones"]
@pytest.fixture(scope="session")
def stores(pilot_tables):       return pilot_tables["stores"]
@pytest.fixture(scope="session")
def customers(pilot_tables):    return pilot_tables["customers"]
@pytest.fixture(scope="session")
def products(pilot_tables):     return pilot_tables["products"]
@pytest.fixture(scope="session")
def transactions(pilot_tables): return pilot_tables["transactions"]
@pytest.fixture(scope="session")
def items(pilot_tables):        return pilot_tables["transaction_items"]
@pytest.fixture(scope="session")
def promotions(pilot_tables):   return pilot_tables["promotions"]
@pytest.fixture(scope="session")
def anomalies(pilot_tables):    return pilot_tables["anomalies_groundtruth"]
