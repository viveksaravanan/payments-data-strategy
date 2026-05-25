"""Shared fixtures.

Auto-runs the full pipeline (generate -> seed) once per session if the
corresponding artifacts are missing, so any test file can be invoked
standalone without a prior `make seed`.
"""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session", autouse=True)
def ensure_pipeline() -> None:
    raw_marker = ROOT / "data" / "raw" / "transactions.csv"
    db_marker = ROOT / "data" / "payments.db"

    if not raw_marker.exists():
        from src.generate.run_all import main as gen_main
        gen_main()
    if not db_marker.exists():
        from src.db.seed import main as seed_main
        seed_main()


@pytest.fixture
def baseline_cassettes() -> dict[str, dict]:
    """Loads every baseline cassette into a dict keyed by the file
    stem (``{specialist}_{qid}_{merchant}``). Empty dict if no
    baseline cassettes have been recorded yet."""
    baseline_dir = ROOT / "tests" / "cassettes" / "baseline"
    out: dict[str, dict] = {}
    if not baseline_dir.exists():
        return out
    for path in sorted(baseline_dir.glob("*.json")):
        out[path.stem] = json.loads(path.read_text())
    return out
