"""DuckDB+Parquet storage layer for v4.

Wave 1 introduces this module as the replacement for the v3 SQLite
seed (``src/db/seed.py``, retired at Wave 2 Stage 7). Engine modules
under ``src/generate/engine/`` build rows in pandas/numpy and call
``write_parquet`` / ``write_partitioned_parquet`` to land them.
Tests and the data-quality report read via ``read_parquet``, which
returns a DuckDB relation — the convention fixed at SPEC §2.
The Wave 2 lake builders in ``src/lake/build.py`` write through the
same ``write_parquet`` to ``data/lake/``.
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
