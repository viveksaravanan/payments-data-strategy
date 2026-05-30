"""Tests for src/storage/duckdb_io.py (Wave 1 Stage 2).

Pre-implementation tests per SPEC §0 test-first rule. The convention
fixed at Stage 2:

- Engine builds rows in pandas/numpy and writes Parquet.
- DuckDB is the read/query engine only.
- `read_parquet(path_or_glob)` returns a DuckDB *relation*, not a
  DataFrame. Tests aggregate over millions of rows; that is columnar
  work.
- Writes are deterministic: pinned pyarrow, single-threaded, sorted
  columns, stable sort key, no nondeterministic metadata.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from src.storage.duckdb_io import (
    read_parquet,
    write_parquet,
    write_partitioned_parquet,
)


def _sample_frame() -> pd.DataFrame:
    """A small, mixed-type frame the round-trip tests share."""
    return pd.DataFrame(
        {
            "store_id": ["S-03", "S-01", "S-02", "S-01"],
            "txn_id":   ["T-040", "T-010", "T-020", "T-030"],
            "qty":      [1, 3, 2, 1],
            "amount":   [9.99, 4.50, 7.25, 12.00],
            "tag":      ["a", "b", "a", "b"],
        }
    )


def test_roundtrip_returns_relation(tmp_path: Path) -> None:
    """read_parquet must return a DuckDB relation (the Stage 2 convention)."""
    df = _sample_frame()
    path = tmp_path / "f.parquet"
    write_parquet(df, path)
    rel = read_parquet(path)
    assert isinstance(rel, duckdb.DuckDBPyRelation), \
        f"read_parquet should return a DuckDB relation, got {type(rel)!r}"


def test_roundtrip_content_matches(tmp_path: Path) -> None:
    """Bytes go in, the same rows come out. Sort by a stable key on both
    sides because write reorders by sort_keys; columns also reorder
    alphabetically on write, so use check_like=True."""
    df = _sample_frame()
    path = tmp_path / "f.parquet"
    write_parquet(df, path, sort_keys=["store_id", "txn_id"])
    rel = read_parquet(path)
    got = rel.df().sort_values(["store_id", "txn_id"]).reset_index(drop=True)
    want = df.sort_values(["store_id", "txn_id"]).reset_index(drop=True)
    pd.testing.assert_frame_equal(got, want, check_dtype=False, check_like=True)


def test_partitioned_roundtrip(tmp_path: Path) -> None:
    """write_partitioned_parquet emits one file per partition; the
    multi-file dataset reads back as one logical relation via a glob."""
    df = _sample_frame()
    root = tmp_path / "ds"
    write_partitioned_parquet(df, root, partition_cols=["store_id"], sort_keys=["txn_id"])
    # One file per distinct store_id.
    files = sorted(root.rglob("*.parquet"))
    assert len(files) == df["store_id"].nunique() == 3
    # Glob-read returns all rows.
    rel = read_parquet(str(root / "**" / "*.parquet"))
    n = rel.aggregate("count(*) AS n").fetchone()[0]
    assert n == len(df)


def test_deterministic_writes_are_byte_identical(tmp_path: Path) -> None:
    """T18 prerequisite: two writes of the same DataFrame at the same
    sort/column order produce byte-identical Parquet. If this proves
    flaky across pyarrow internals, fall back to content-identical
    (sorted hash) per the Stage 2 plan."""
    df = _sample_frame()
    p1 = tmp_path / "a.parquet"
    p2 = tmp_path / "b.parquet"
    write_parquet(df, p1, sort_keys=["store_id", "txn_id"])
    write_parquet(df, p2, sort_keys=["store_id", "txn_id"])
    h1 = hashlib.sha256(p1.read_bytes()).hexdigest()
    h2 = hashlib.sha256(p2.read_bytes()).hexdigest()
    assert h1 == h2, "deterministic write produced different bytes"


def test_write_sorts_columns_alphabetically(tmp_path: Path) -> None:
    """Column order on disk is deterministic — alphabetical. Callers
    that need a presentation order set it at read time."""
    # Construct columns in non-alphabetical order.
    df = pd.DataFrame(
        {"z": [1, 2], "a": ["x", "y"], "m": [3.0, 4.0]}
    )
    path = tmp_path / "f.parquet"
    write_parquet(df, path)
    rel = read_parquet(path)
    cols = [c for c in rel.df().columns]
    assert cols == sorted(cols), f"columns must be alphabetical on disk; got {cols}"


def test_write_applies_sort_key(tmp_path: Path) -> None:
    """When sort_keys is set, rows are physically sorted before writing,
    so two runs against the same data produce the same row order."""
    df = _sample_frame()
    path = tmp_path / "f.parquet"
    write_parquet(df, path, sort_keys=["store_id", "txn_id"])
    rel = read_parquet(path)
    got = rel.df()
    expected = df.sort_values(["store_id", "txn_id"]).reset_index(drop=True)
    # On-disk row order is sorted; alphabetically reorder columns to match write.
    expected = expected[sorted(expected.columns)]
    pd.testing.assert_frame_equal(got, expected, check_dtype=False)


def test_partitioned_write_files_named_by_partition(tmp_path: Path) -> None:
    """Each partition file's path encodes its partition column value,
    so a Wave 2 scoped read can target one partition by path."""
    df = _sample_frame()
    root = tmp_path / "ds"
    write_partitioned_parquet(df, root, partition_cols=["store_id"], sort_keys=["txn_id"])
    # Hive-style partition directories: store_id=S-01/...parquet
    partition_dirs = sorted(p.name for p in root.iterdir() if p.is_dir())
    assert partition_dirs == ["store_id=S-01", "store_id=S-02", "store_id=S-03"]


@pytest.mark.parametrize("ext", [".parquet", ".pq"])
def test_read_parquet_accepts_single_file_path(tmp_path: Path, ext: str) -> None:
    df = _sample_frame()
    path = tmp_path / f"single{ext}"
    write_parquet(df, path)
    rel = read_parquet(path)
    n = rel.aggregate("count(*) AS n").fetchone()[0]
    assert n == len(df)
