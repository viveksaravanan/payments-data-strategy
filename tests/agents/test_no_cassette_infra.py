"""Stage 6 — assert no cassette / golden-test infra (SPEC §6, D27.2).

Wave 3 explicitly drops golden tests + cassettes from the suite —
v3's cassettes are invalid against the refactored agents, and live-
LLM golden tests are slow / costly / non-deterministic. The Wave 3
quality bar is the D25 runtime validator (numbers, always-on) +
the §6.5 preview harness (routing/decline, human-reviewed).
Automated agent-regression (a fresh deterministic replay layer) is
a v5 item.

These assertions lock in the design choice — any future PR that
adds golden tests or cassette infra must also justify why it isn't
the theater D27.2 calls out.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_no_cassettes_directory() -> None:
    """v3's tests/cassettes/ is retired."""
    assert not (REPO_ROOT / "tests" / "cassettes").exists()


def test_no_cassette_helpers_module() -> None:
    """v3's tests/cassette_helpers.py is retired."""
    assert not (REPO_ROOT / "tests" / "cassette_helpers.py").exists()


def test_no_record_baseline_cassettes_script() -> None:
    """v3's scripts/record_baseline_cassettes.py is retired."""
    assert not (REPO_ROOT / "scripts" / "record_baseline_cassettes.py").exists()


def test_no_run_phase5_regression_script() -> None:
    """v3's scripts/run_phase5_regression.py is retired."""
    assert not (REPO_ROOT / "scripts" / "run_phase5_regression.py").exists()


def test_no_golden_test_file_in_tests_agents() -> None:
    """No `tests/agents/test_golden_*.py` exists — D27.2 defers
    the golden-test layer to v5."""
    matches = list((REPO_ROOT / "tests" / "agents").glob("test_golden_*.py"))
    assert not matches, f"Unexpected golden test files: {matches}"


def test_no_vcr_dependency() -> None:
    """Confirm pyproject.toml doesn't pull pytest-vcr / vcrpy
    (the typical v3-era cassette stack). Future PRs adding either
    should re-open the D27.2 deferral conversation."""
    pyproject = REPO_ROOT / "pyproject.toml"
    if not pyproject.exists():
        return
    text = pyproject.read_text().lower()
    assert "pytest-vcr" not in text
    assert "vcrpy" not in text
