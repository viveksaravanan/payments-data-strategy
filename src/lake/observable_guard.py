"""Wave 2 §1 observable-data invariant guard (D23.1).

The lake derives ONLY from observable data — what a terminal/merchant
actually sees. Generation-time latent/profile columns are NEVER source
columns; reading them is reading the answer key, indefensible to an exec
and the §1 violation the whole Wave 2 design is built to prevent.

This module is the **whitelisted column accessor** that mechanically
prevents scaffolding leaks. Every read of ``data/raw/`` by the lake
builder must go through ``load_table`` or ``read_parquet_relation``
here; both raise ``ForbiddenColumnError`` if a forbidden table or column
is requested.

A second mechanism (static AST scan in
``tests/lake/test_L01_observable_data_invariant.py``) asserts no
code under ``src/lake/`` bypasses this accessor by calling
``pd.read_parquet`` / ``pq.read_table`` directly.

**The §1-violation-in-disguise pattern.** The most insidious failure
is reading a planted label (e.g., ``stores.zone_id``,
``customers.home_zone``) and relabeling it "derived". Both are
explicitly in the forbidden set even though they look harmless. Zone
derivation must come from coordinates (D23.2); behavioral segments
must come from transaction patterns (D23.3.3) — never by reading
``customers.loyalty_type`` or ``customers.primary_banner`` and
calling it "derived".
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

import duckdb
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_RAW = REPO_ROOT / "data" / "raw"


# ----- Allowed source columns per table ---------------------------------
# The lake builder may only read columns listed here. Reading any other
# column from these tables, or any column from a table not listed
# (FORBIDDEN_TABLES or unknown), raises ForbiddenColumnError.
#
# Rationale (D23.1): the lake reads only what a terminal observes —
# transaction facts, line items, store location. Generation scaffolding
# (planted segments, zone profiles, customer state) is the answer key
# and must never be touched.

ALLOWED_COLUMNS: dict[str, set[str]] = {
    # Transaction facts — fully observable per Wave 1 §5 contract.
    "transactions": {
        "txn_id", "customer_token", "store_id", "txn_ts",
        "tender", "network", "entry_mode", "wallet_provider",
        "wallet_at_tap", "connectivity_type",
        "subtotal", "discount_total", "n_lines",
        "segment", "banner_code",
    },
    "transaction_items": {
        "txn_id", "line_id", "sku", "canonical_id",
        "category", "subcategory", "qty", "unit_price",
        "discount", "promo_id", "line_total",
    },
    # Promo / catalog reference — observable to the merchant who ran them.
    "promotions": {
        "promo_id", "sku", "merchant_id", "promo_type",
        "start_date", "end_date", "depth_pct",
    },
    "products": {
        "sku", "merchant_id", "banner_code", "segment",
        "category", "subcategory", "canonical_id",
        "private_label", "base_price",
    },
    "merchants": {
        "merchant_id", "name", "segment", "positioning_tier",
        "store_count",
    },
    # Stores: ONLY observable location columns. NOTE: zone_id is the
    # planted zone label and is EXPLICITLY excluded from this set.
    # Reading zone_id and relabeling it "derived" is the §1-violation-
    # in-disguise pattern Stage 3 zone derivation must avoid.
    "stores": {
        "store_id", "merchant_id", "banner_code",
        "latitude", "longitude",
        "neighborhood", "metro_region", "open_date",
    },
}

ALLOWED_TABLES: set[str] = set(ALLOWED_COLUMNS.keys())

# Forbidden tables. The customers table holds card_id (= customer_token,
# observable as the tokenized identifier at the terminal) PLUS every
# piece of planted state (home_zone, loyalty_type, primary_banner,
# tender, network, wallet_enrolled, wallet_provider, affluence, etc.).
# The lake never needs the customers table — transactions already
# carries the observable customer_token. Loading customers risks
# pulling planted state by accident.
#
# Similarly, the zones table is the planted profile (affluence,
# residential_weight, density, household_skew, age_skew). The lake
# derives zones from store coordinates, not from this table.
FORBIDDEN_TABLES: set[str] = {"customers", "zones"}


class ForbiddenColumnError(ValueError):
    """Raised when the lake build tries to read a forbidden column or
    table — generation scaffolding that would violate the §1 invariant
    (D23.1)."""


def _validate_request(
    name: str,
    columns: Iterable[str] | None,
) -> list[str]:
    """Validate a table+columns request against the whitelist. Returns
    the resolved column list. Raises ForbiddenColumnError on violation."""
    if name in FORBIDDEN_TABLES:
        raise ForbiddenColumnError(
            f"Table {name!r} is forbidden source for the lake builder — "
            f"it is generation scaffolding (planted state / profile). "
            f"Lake reads only observable data. Allowed source tables: "
            f"{sorted(ALLOWED_TABLES)}."
        )
    if name not in ALLOWED_TABLES:
        raise ForbiddenColumnError(
            f"Table {name!r} is not in the allowed source set. "
            f"Allowed: {sorted(ALLOWED_TABLES)}."
        )

    allowed = ALLOWED_COLUMNS[name]
    if columns is None:
        return sorted(allowed)

    cols = list(columns)
    forbidden = [c for c in cols if c not in allowed]
    if forbidden:
        raise ForbiddenColumnError(
            f"Forbidden columns requested from {name!r}: {forbidden}. "
            f"These columns are generation scaffolding (not terminal-"
            f"observable). Allowed columns for {name!r}: "
            f"{sorted(allowed)}."
        )
    return cols


def _parquet_path(name: str) -> Path:
    path = DATA_RAW / f"{name}.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — run `make seed` to materialize "
            f"data/raw/ from the Wave 1 engine first."
        )
    return path


def load_table(
    name: str,
    columns: Iterable[str] | None = None,
) -> pd.DataFrame:
    """Whitelisted Parquet read.

    The only entrypoint the lake builder is allowed to use for reading
    ``data/raw/``. Forbidden tables and forbidden columns raise.

    Parameters
    ----------
    name
        Table name (e.g. ``"transactions"``). Must be in
        ``ALLOWED_TABLES``.
    columns
        Optional list of columns to load. Each must be in
        ``ALLOWED_COLUMNS[name]``. If ``None``, all allowed columns
        for the table are loaded.
    """
    cols = _validate_request(name, columns)
    return pd.read_parquet(_parquet_path(name), columns=cols)


def read_parquet_relation(
    name: str,
    columns: Iterable[str] | None = None,
) -> "duckdb.DuckDBPyRelation":
    """Whitelisted DuckDB relation reader for aggregation-heavy work.

    Same whitelist as ``load_table``; returns a DuckDB relation so
    aggregations on the 10.76M-row line-items table stay columnar.
    """
    cols = _validate_request(name, columns)
    path = _parquet_path(name)
    conn = duckdb.connect()
    col_list = ", ".join(cols)
    # Use single-quoted path; no user-controlled SQL.
    return conn.sql(f"SELECT {col_list} FROM read_parquet('{path}')")
