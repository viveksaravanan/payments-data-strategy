"""Shared fixtures for the Wave 1 §6 acceptance battery (T1-T18).

Runs the engine once at the pilot scale, writes Parquet to
``data/raw/`` + ``data/eval/``, then exposes the loaded tables via
module-scope fixtures so each T-test queries shared dataframes
rather than rebuilding.

Pilot scale: 5,000 cards. Hits roughly 5% of the §5 contract's
1.67M-txn target, which keeps the battery runnable in ~5 minutes
while preserving distribution shapes well enough for T1-T18 bands.
T17 small-cell readiness needs full-scale to bind in absolute
counts, so its pilot check tests *proportionality* (cells grow
roughly linearly with population).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.generate.config.loader import load_config
from src.generate.engine.run_all import build_all, write_all, DATA_RAW, DATA_EVAL

PILOT_CARDS = 5_000
CONFIG_ROOT = Path(__file__).resolve().parents[2] / "src" / "generate" / "config"


def _parquet_exists() -> bool:
    return (DATA_RAW / "transactions.parquet").exists()


@pytest.fixture(scope="session")
def cfg():
    return load_config(CONFIG_ROOT)


@pytest.fixture(scope="session")
def pilot_tables() -> dict[str, pd.DataFrame]:
    """Build the full pilot dataset once per session. Reuses Parquet
    on disk if a prior test session already produced it at the
    matching scale (transactions row-count ≈ expected pilot volume)."""
    import shutil
    # Rebuild always so we know scale is correct.
    if DATA_RAW.exists():
        shutil.rmtree(DATA_RAW)
    if DATA_EVAL.exists():
        shutil.rmtree(DATA_EVAL)
    DATA_RAW.mkdir(parents=True, exist_ok=True)
    DATA_EVAL.mkdir(parents=True, exist_ok=True)

    tables = build_all(scale=PILOT_CARDS)
    write_all(tables)
    return tables


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
