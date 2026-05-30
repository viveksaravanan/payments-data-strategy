"""DuckDB+Parquet IO with deterministic-write conventions (Wave 1 §2).

Convention (fixed at Stage 2, applies at every call site):

* The engine builds rows in pandas/numpy and writes Parquet through
  ``write_parquet`` / ``write_partitioned_parquet``.
* DuckDB is the read/query engine. ``read_parquet`` returns a
  DuckDB relation — never a DataFrame. Test and report code
  aggregates over millions of rows; that is columnar work.
* Writes are deterministic so two runs at the same input produce
  byte-identical Parquet (T18 in the realism battery):
    - pyarrow is pinned in pyproject.toml.
    - Single-threaded writes (no per-run thread ordering).
    - Columns are sorted alphabetically before write.
    - Callers pass ``sort_keys`` to fix the row order; if omitted,
      no sort is applied (small tables / dimension tables).
    - No run timestamps or other nondeterministic Parquet metadata.

If byte-equality proves brittle across pyarrow internals, the
fallback (per the plan) is content-identical equivalence: a sorted
hash of the row payload. That fallback lives in the test harness,
not here — this module's job is to make byte-equality the default.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


_PARQUET_WRITE_KWARGS: dict[str, object] = {
    # Pinned for determinism: same library + same options ⇒ same bytes.
    "compression": "zstd",
    "compression_level": 3,
    # Disable any nondeterministic metadata pyarrow might inject.
    "write_statistics": True,
    "use_dictionary": True,
    # Stable physical layout.
    "row_group_size": 64_000,
    # Pinned writer version.
    "version": "2.6",
}


def _prepare_for_write(
    df: pd.DataFrame,
    *,
    sort_keys: Iterable[str] | None,
) -> pd.DataFrame:
    """Apply the deterministic-write transforms: sort rows by stable
    key (if provided) and reorder columns alphabetically. Returns a
    new DataFrame; the caller's frame is untouched."""
    out = df
    if sort_keys is not None:
        sort_keys = list(sort_keys)
        if sort_keys:
            out = out.sort_values(sort_keys, kind="mergesort").reset_index(drop=True)
    out = out[sorted(out.columns)]
    return out


def write_parquet(
    df: pd.DataFrame,
    path: str | Path,
    *,
    sort_keys: Iterable[str] | None = None,
) -> Path:
    """Write a single Parquet file at ``path``.

    Parameters
    ----------
    df
        The DataFrame to write. Not modified in place.
    path
        Destination path (a file, not a directory).
    sort_keys
        Optional stable sort key. When provided, rows are sorted by
        these columns before write, so two runs over the same data
        produce identical row order. Required for the large tenant
        tables (transactions, transaction_items) where row order
        would otherwise drift.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    prepared = _prepare_for_write(df, sort_keys=sort_keys)
    table = pa.Table.from_pandas(prepared, preserve_index=False)
    pq.write_table(table, path, **_PARQUET_WRITE_KWARGS)
    return path


def write_partitioned_parquet(
    df: pd.DataFrame,
    root: str | Path,
    *,
    partition_cols: Iterable[str],
    sort_keys: Iterable[str] | None = None,
) -> Path:
    """Write a Hive-partitioned Parquet dataset under ``root``.

    Each partition gets its own subdirectory named ``col=value`` and
    a single file inside. The partition value is encoded in the path,
    so a Wave 2 scoped read can target one partition by glob.

    Sort key applies *within* each partition before write, so partitions
    are individually deterministic.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    partition_cols = list(partition_cols)
    if not partition_cols:
        raise ValueError("write_partitioned_parquet requires at least one partition_col")

    # Sort the dataframe by (partition_cols, sort_keys) so within-partition
    # row order is deterministic.
    combined_sort = list(partition_cols)
    if sort_keys is not None:
        combined_sort = combined_sort + list(sort_keys)
    prepared = df.sort_values(combined_sort, kind="mergesort").reset_index(drop=True)

    for partition_values, subframe in prepared.groupby(partition_cols, sort=True):
        if not isinstance(partition_values, tuple):
            partition_values = (partition_values,)
        subdir = root
        for col, val in zip(partition_cols, partition_values):
            subdir = subdir / f"{col}={val}"
        subdir.mkdir(parents=True, exist_ok=True)
        body = subframe.drop(columns=list(partition_cols))
        write_parquet(body, subdir / "part-0.parquet", sort_keys=sort_keys)

    return root


def read_parquet(path_or_glob: str | Path) -> duckdb.DuckDBPyRelation:
    """Open one or more Parquet files as a DuckDB relation.

    ``path_or_glob`` may be a single file path or a glob pattern (e.g.
    ``"data/raw/transactions/**/*.parquet"``). The returned relation
    is lazy — DuckDB pushes filters and projections down to the
    Parquet reader, so callers should aggregate via DuckDB SQL or
    relational API and only materialize to pandas at the end.

    A fresh in-memory DuckDB connection is created per call. For
    long-lived sessions (the DQ report), open one connection and
    use ``conn.read_parquet`` directly.
    """
    pattern = str(path_or_glob)
    # Use a per-call connection so this stays a pure utility — no
    # hidden global state. The DQ report can hold its own connection.
    conn = duckdb.connect()
    return conn.read_parquet(pattern)
