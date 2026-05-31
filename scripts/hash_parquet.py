"""Compute deterministic hashes of every Parquet file under data/raw/
+ data/eval/. Used for T18 cross-run reproducibility verification.

Run after a full-scale generation to snapshot the output:
    uv run python scripts/hash_parquet.py > /tmp/wave1_run1.hashes

Then run again after a second generation and diff:
    uv run python scripts/hash_parquet.py > /tmp/wave1_run2.hashes
    diff /tmp/wave1_run1.hashes /tmp/wave1_run2.hashes
"""
from __future__ import annotations

import hashlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_RAW = REPO_ROOT / "data" / "raw"
DATA_EVAL = REPO_ROOT / "data" / "eval"


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    paths = sorted(list(DATA_RAW.rglob("*.parquet")) + list(DATA_EVAL.rglob("*.parquet")))
    for p in paths:
        rel = p.relative_to(REPO_ROOT)
        size = p.stat().st_size
        h = _hash(p)
        print(f"{h}  {size:>12}  {rel}")


if __name__ == "__main__":
    main()
