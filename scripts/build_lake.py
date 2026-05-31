"""Build the Wave 2 lake — five Parquet tables + manifest manifest.

Runs the five lake builders against ``data/raw/`` and writes the
results to ``data/lake/*.parquet`` (deterministic Parquet writes via
``src.storage.duckdb_io.write_parquet``).

Usage: ``uv run python scripts/build_lake.py``  (or ``make lake``)

Outputs:

* ``data/lake/lake_category_metrics.parquet``
* ``data/lake/lake_payment_mix.parquet``
* ``data/lake/lake_segment_mix.parquet``
* ``data/lake/lake_trade_area.parquet``
* ``data/lake/lake_cross_merchant_cohorts.parquet``

Then run ``make lake-report`` to materialize ``docs/LAKE_REPORT.md``
from these tables.
"""
from __future__ import annotations

import time
from pathlib import Path

import pandas as pd

from src.lake.build import (
    build_lake_category_metrics,
    build_lake_cross_merchant_cohorts,
    build_lake_payment_mix,
    build_lake_segment_mix,
    build_lake_trade_area,
)
from src.storage.duckdb_io import write_parquet

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_LAKE = REPO_ROOT / "data" / "lake"


_SORT_KEYS: dict[str, list[str]] = {
    "lake_category_metrics": [
        "banner_code", "category", "subcategory",
        "derived_zone", "period_start", "grain",
    ],
    "lake_payment_mix": ["banner_code", "derived_zone", "month_start"],
    "lake_segment_mix": ["banner_code", "derived_zone", "behavioral_segment"],
    "lake_trade_area": ["derived_zone", "category", "banner_code"],
    "lake_cross_merchant_cohorts": ["derived_zone", "cohort_combination"],
}


def main() -> None:
    DATA_LAKE.mkdir(parents=True, exist_ok=True)

    builders = [
        ("lake_category_metrics", build_lake_category_metrics),
        ("lake_payment_mix", build_lake_payment_mix),
        ("lake_segment_mix", build_lake_segment_mix),
        ("lake_trade_area", build_lake_trade_area),
        ("lake_cross_merchant_cohorts", build_lake_cross_merchant_cohorts),
    ]
    t_total = time.time()
    for name, fn in builders:
        t0 = time.time()
        print(f"Building {name}…", flush=True)
        df = fn()
        path = DATA_LAKE / f"{name}.parquet"
        write_parquet(df, path, sort_keys=_SORT_KEYS[name])
        elapsed = time.time() - t0
        print(
            f"  wrote {path}  ({len(df):,} rows, {elapsed:.1f}s)",
            flush=True,
        )
    print(f"Done in {time.time() - t_total:.1f}s.", flush=True)


if __name__ == "__main__":
    main()
