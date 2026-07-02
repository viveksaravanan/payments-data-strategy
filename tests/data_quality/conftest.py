"""Shared fixtures for the Wave 1 §6 acceptance battery (T1-T18).

Loads the on-disk Parquet from ``data/raw/`` and ``data/eval/`` as
read-only. **Tests never write to the production data paths** —
the suite is run *against* a previously-materialized generation,
not as a side-effect of test invocation. If ``data/raw/`` is
empty, the fixture ``pytest.skip``s with a pointer to ``make seed``.

Scale is computed dynamically from the actual customers table size
so the T-tests work at any scale (5k pilot, 100k full, anywhere
in between).

**Test-isolation fix (Wave 2 audit):** previous versions of this
fixture silently rebuilt at pilot scale (``shutil.rmtree(DATA_RAW)``
+ ``write_all(tables)``) when ``data/raw/`` was missing, which
clobbered Wave 1's frozen full-scale dataset whenever the
generation directory happened to be cleared. Replaced with an
explicit skip — the harness is now an observer of the published
artifact, not a participant in producing it.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.generate.config.loader import load_config
from src.generate.engine.run_all import DATA_RAW, DATA_EVAL

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
    """Return the engine output tables read from disk.

    Skips the data-quality battery if ``data/raw/`` is empty —
    materialize Wave 1's output first with ``make seed`` (or
    ``uv run python -m src.generate.engine.run_all``). The
    fixture is **read-only**; it never writes to the production
    data paths.
    """
    if not (DATA_RAW / "transactions.parquet").exists():
        pytest.skip(
            "data/raw/ missing — run `make seed` to materialize "
            "Wave 1 output before running the data-quality battery. "
            "The battery is an observer of the on-disk artifact, "
            "not a participant in producing it."
        )
    return _load_existing_tables()


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


@pytest.fixture(scope="session")
def items_x(pilot_tables) -> pd.DataFrame:
    """datamodel-v2: line items joined to products on ``sku`` to resolve
    the taxonomy/PL/price that the line no longer carries. Exposes
    ``category`` (= functional_category), ``subcategory`` (=
    functional_subcategory), ``functional_department``, ``private_label``,
    ``shelf_price``, and ``banner_code`` alongside the line fields — the
    §A12 normalization boundary (everything resolves via the products join)."""
    items = pilot_tables["transaction_items"]
    prods = pilot_tables["products"][[
        "sku", "banner_code", "functional_department",
        "functional_category", "functional_subcategory",
        "private_label", "shelf_price",
    ]].rename(columns={
        "functional_category": "category",
        "functional_subcategory": "subcategory",
    })
    return items.merge(prods, on="sku", how="left")
