"""DuckDB+Parquet storage layer for v4.

Wave 1 introduces this module as the replacement for the v3 SQLite
seed (src/db/seed.py, quarantined). Engine modules under
src/generate/engine/ build rows in pandas/numpy and call
``write_parquet`` / ``write_partitioned_parquet`` to land them.
Tests and the data-quality report read via ``read_parquet``, which
returns a DuckDB relation — the convention fixed at SPEC §2.
"""
from src.storage.duckdb_io import (
    read_parquet,
    write_parquet,
    write_partitioned_parquet,
)

__all__ = [
    "read_parquet",
    "write_parquet",
    "write_partitioned_parquet",
]
