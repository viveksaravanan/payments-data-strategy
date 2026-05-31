"""Stage 6 L12 — DP deferred cleanly (SPEC §6 L12, D21.3, D24.3).

D24.3 commits Wave 2 to the design choice: **no DP seam shipped**.

* No ``publish()`` wrapper around aggregates — the published numeric
  columns in the five lake tables ARE the future DP injection point.
* No no-op shim that would have to be wired through every metric and
  whose absence in one place would be the bug.

This invariant guards the design from drifting back to a seam-based
implementation. It asserts:

1. ``src/lake/build.py`` does NOT define a ``publish`` function or
   wrapper. Aggregate columns are emitted raw.
2. The lake build report (Stage 6 artifact) states the deferral
   honestly — DP not implemented, aggregates are the future
   injection point.

D24.1's small-N pseudonymity caveat (5 merchants) is also a §8 honest-
limit; it's verified separately by checking the report content
(Stage 6 close).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LAKE_DIR = REPO_ROOT / "src" / "lake"


# ----- No publish() seam ------------------------------------------------

def test_no_publish_function_in_lake_build() -> None:
    """The Wave 2 design (D24.3) does NOT add a publish() wrapper —
    building no-op DP infrastructure that doesn't do anything but
    would still have to be wired through every metric is theater.
    Aggregates ARE the future injection point.

    This assertion locks the design: any future PR that adds a
    publish() to src/lake/build.py needs to also justify why it's
    not a no-op shim.
    """
    src = (LAKE_DIR / "build.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "publish":
            pytest.fail(
                f"src/lake/build.py defines publish() at line "
                f"{node.lineno} — D24.3 says no DP seam this wave. "
                f"Aggregate columns are the future injection point."
            )


def test_no_publish_function_in_scope() -> None:
    """Same check on scope.py — DP would inject at scope time too in a
    seam-based design. None here either."""
    src = (LAKE_DIR / "scope.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "publish":
            pytest.fail(
                f"src/lake/scope.py defines publish() at line "
                f"{node.lineno} — see test_no_publish_function_in_lake_build."
            )


def test_no_publish_module_in_lake() -> None:
    """No `src/lake/publish.py` either. The directory layout itself
    signals the design choice."""
    assert not (LAKE_DIR / "publish.py").exists(), (
        "src/lake/publish.py exists — D24.3 says no DP seam this wave."
    )
    assert not (LAKE_DIR / "dp.py").exists(), (
        "src/lake/dp.py exists — D24.3 says no DP seam this wave."
    )


# ----- Aggregates ARE the injection point -------------------------------

def test_lake_aggregates_are_clean_numeric_columns(
    lake_category_metrics,
    lake_payment_mix,
    lake_trade_area,
    lake_segment_mix,
    lake_cohorts,
) -> None:
    """The five lake tables emit numeric aggregates as plain columns
    (no special types, no wrapper objects). DP later swaps the
    aggregate computation for noised versions; no schema change."""
    tables = {
        "category_metrics": lake_category_metrics,
        "payment_mix": lake_payment_mix,
        "trade_area": lake_trade_area,
        "segment_mix": lake_segment_mix,
        "cross_merchant_cohorts": lake_cohorts,
    }
    for name, df in tables.items():
        # The metric-bearing columns (anything not in {dim columns}) are
        # numeric. Cheap proxy: txn_count is in every table and must be
        # plain numeric.
        assert "txn_count" in df.columns, f"{name}: missing txn_count"
        assert df["txn_count"].dtype.kind in ("i", "u", "f"), (
            f"{name}: txn_count is not numeric"
        )


# ----- Report states deferral honestly ----------------------------------

def test_lake_report_states_dp_deferred() -> None:
    """SPEC §6 / D24.3: the build report MUST state DP deferred with
    reason. A §8-framed report that silently omits deferred
    techniques is a worse outcome than one that names them.

    Skipped if the report hasn't been generated yet (run
    `make lake-report` first)."""
    report = REPO_ROOT / "docs" / "LAKE_REPORT.md"
    if not report.exists():
        pytest.skip(
            "docs/LAKE_REPORT.md not yet generated — run "
            "`make lake-report` to materialize it."
        )
    text = report.read_text().lower()
    assert "differential privacy" in text, (
        "LAKE_REPORT.md must name differential privacy in the §8 framing"
    )
    assert "deferred" in text, (
        "LAKE_REPORT.md must state which techniques are deferred"
    )
    assert "injection point" in text or "future" in text, (
        "LAKE_REPORT.md should state aggregates are the future DP "
        "injection point (D24.3)"
    )


def test_lake_report_states_small_n_pseudonymity_caveat() -> None:
    """D24.1 caveat: with 5 merchants this is pseudonymization, not
    true anonymity. The report must say so honestly so an exec
    reader doesn't misread."""
    report = REPO_ROOT / "docs" / "LAKE_REPORT.md"
    if not report.exists():
        pytest.skip("docs/LAKE_REPORT.md not yet generated")
    text = report.read_text().lower()
    assert "pseudonym" in text, (
        "LAKE_REPORT.md must name pseudonymization (D24.1 small-N caveat)"
    )
    # The 5-merchant constraint should be cited so the caveat is
    # grounded in this panel, not hand-waved.
    assert "5 merchant" in text or "five merchant" in text or "small" in text, (
        "LAKE_REPORT.md must surface the small-N reason for the caveat"
    )


def test_lake_report_states_l_diversity_deferred() -> None:
    """D21.3: l-diversity is also deferred-with-reason. Must be
    named."""
    report = REPO_ROOT / "docs" / "LAKE_REPORT.md"
    if not report.exists():
        pytest.skip("docs/LAKE_REPORT.md not yet generated")
    text = report.read_text().lower()
    assert "l-diversity" in text or "diversity" in text
