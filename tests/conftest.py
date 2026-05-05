"""Shared fixtures.

Auto-runs the full pipeline (generate -> anonymize -> seed) once per session if
the corresponding artifacts are missing, so any test file can be invoked
standalone without a prior `make seed`.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session", autouse=True)
def ensure_pipeline() -> None:
    raw_marker = ROOT / "data" / "raw" / "transactions.csv"
    lake_marker = ROOT / "data" / "anon" / "lake" / "transactions.csv"
    db_marker = ROOT / "data" / "payments.db"

    if not raw_marker.exists():
        from src.generate.run_all import main as gen_main
        gen_main()
    if not lake_marker.exists():
        from src.anonymize.pipeline import main as anon_main
        anon_main()
    if not db_marker.exists():
        from src.db.seed import main as seed_main
        seed_main()
