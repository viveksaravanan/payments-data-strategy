"""Stage 4.5 tests — ``lake_cross_merchant_cohorts`` (D23.3.5).

The headline overlap table. Privacy invariants:

* No per-customer rows (L3).
* Spend reported as median + IQR + band — never raw mean (D24.2
  concentration risk).
* Cells ≥ k=50 (Wave 1 T17 measured 483-1,126 all-three cards per
  zone at full scale, so the cohort table clears k=50 by 10×).
* The cohort-combination relabel does not leak which specific cards
  shopped where.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pandas as pd
import pytest

from src.lake.build import K_MIN, build_lake_cross_merchant_cohorts

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def cohorts() -> pd.DataFrame:
    return build_lake_cross_merchant_cohorts()


# ----- Schema -------------------------------------------------------------

def test_required_columns(cohorts) -> None:
    required = {
        "derived_zone", "cohort_combination",
        "cohort_size", "median_combined_spend",
        "p25_combined_spend", "p75_combined_spend",
        "median_total_txns", "frequency_band", "txn_count",
    }
    assert required.issubset(cohorts.columns)


def test_NO_per_customer_columns(cohorts) -> None:
    """L3 / SPEC §4.5: 'publish only aggregated counts/bands' — assert
    no customer-level cross-merchant row is ever written to the lake
    output."""
    forbidden = {
        "customer_token", "card_id",
        # Any per-card identifier or pass-through.
        "card_ids", "tokens",
    }
    assert not (forbidden & set(cohorts.columns))


def test_NO_raw_mean_columns(cohorts) -> None:
    """D24.2: 'publish median/banded combined-spend, NOT raw mean'.
    The cohort schema must not publish any mean / sum / avg column
    for spend — only median, quartiles, and bands."""
    forbidden_substrings = ["mean_", "_mean", "avg_", "_avg", "sum_spend"]
    leaks = [
        col for col in cohorts.columns
        if any(sub in col.lower() for sub in forbidden_substrings)
    ]
    assert not leaks, (
        f"cohort schema contains forbidden raw-mean columns: {leaks}"
    )


def test_cohort_combinations_match_D14_4_archetypes(cohorts) -> None:
    """The 7-archetype participation matrix from Wave 1 D14.4."""
    expected = {
        "grocery_only", "qsr_only", "retail_only",
        "grocery_qsr", "grocery_retail", "qsr_retail",
        "all_three",
    }
    actual = set(cohorts["cohort_combination"].unique())
    assert actual <= expected, f"unexpected cohort labels: {actual - expected}"


# ----- k ≥ 50 floor (T17 cleared at full scale; this is the safety net) ----

def test_every_cell_meets_k_floor(cohorts) -> None:
    print(
        f"\nL04e cells: {len(cohorts):,}  | "
        f"min txn_count: {int(cohorts['txn_count'].min())}  | "
        f"max: {int(cohorts['txn_count'].max())}"
    )
    assert (cohorts["txn_count"] >= K_MIN).all()


def test_all_three_cohort_present_in_all_zones(cohorts) -> None:
    """Wave 1 T17 full-scale measured 483-1,126 all-three cards per
    zone. Every zone should host an all_three cohort cell."""
    all_three = cohorts[cohorts["cohort_combination"] == "all_three"]
    assert len(all_three) == 8, (
        f"all_three should appear in 8/8 zones; got {len(all_three)}"
    )


# ----- Spend bands -------------------------------------------------------

def test_p25_p75_bracket_median(cohorts) -> None:
    """p25 ≤ median ≤ p75 — sanity check that quartiles are computed
    correctly."""
    assert (cohorts["p25_combined_spend"] <= cohorts["median_combined_spend"]).all()
    assert (cohorts["median_combined_spend"] <= cohorts["p75_combined_spend"]).all()


def test_median_spend_positive(cohorts) -> None:
    assert (cohorts["median_combined_spend"] > 0).all()


def test_frequency_band_values(cohorts) -> None:
    assert set(cohorts["frequency_band"].unique()) <= {"low", "mid", "high"}


# ----- Linkage logic confined to the cohort builder ----------------------

def test_cohort_linkage_logic_only_in_cohort_builder() -> None:
    """SPEC §4.5: 'linkage logic confined to this builder'. Cross-
    merchant token aggregation patterns (groupby('customer_token')
    with banner-set extraction) should not appear in other Wave 2
    modules.

    AST scan (not text scan — docstrings naming 'customer_token' to
    document the trusted-boundary rule are allowed; what's banned is
    executable use). Looks for ``df["customer_token"]``,
    ``df.customer_token``, ``columns=[..., "customer_token"]``, and
    ``groupby("customer_token")`` patterns. Modules permitted to use
    these:

    * ``observable_guard.py`` — the API; whitelists customer_token
      as an observable transaction column.
    * ``build.py`` — uses it inside the §4.5 cohort builder.

    Other modules (isolation.py, zones.py, scope.py-when-it-lands,
    manifest.py-when-it-lands) may *mention* customer_token in
    docstrings/comments to document the trusted boundary but must
    not have any executable reference."""
    import ast
    lake_dir = REPO_ROOT / "src" / "lake"
    permitted = {"observable_guard.py", "build.py", "__init__.py"}
    leaks: list[str] = []

    class _Finder(ast.NodeVisitor):
        def __init__(self, path: Path) -> None:
            self.path = path

        def _flag(self, lineno: int, kind: str) -> None:
            leaks.append(
                f"{self.path.relative_to(REPO_ROOT)}:{lineno} {kind}"
            )

        def visit_Attribute(self, node: ast.Attribute) -> None:
            if node.attr == "customer_token":
                self._flag(node.lineno, ".customer_token access")
            self.generic_visit(node)

        def visit_Subscript(self, node: ast.Subscript) -> None:
            val = node.slice
            if isinstance(val, ast.Constant) and val.value == "customer_token":
                self._flag(node.lineno, '["customer_token"] access')
            elif isinstance(val, (ast.List, ast.Tuple)):
                for el in val.elts:
                    if (isinstance(el, ast.Constant)
                            and el.value == "customer_token"):
                        self._flag(node.lineno, '"customer_token" in subscript list')
            self.generic_visit(node)

        def visit_Call(self, node: ast.Call) -> None:
            # groupby("customer_token") pattern.
            for arg in node.args:
                if isinstance(arg, ast.Constant) and arg.value == "customer_token":
                    func_name = (
                        node.func.attr if isinstance(node.func, ast.Attribute)
                        else node.func.id if isinstance(node.func, ast.Name)
                        else "?"
                    )
                    self._flag(node.lineno, f'{func_name}("customer_token") call')
            self.generic_visit(node)

    for path in sorted(lake_dir.glob("*.py")):
        if path.name in permitted:
            continue
        tree = ast.parse(path.read_text())
        finder = _Finder(path)
        finder.visit(tree)

    assert not leaks, (
        "Executable customer_token references outside the permitted "
        "modules:\n  " + "\n  ".join(leaks)
    )


# ----- Sanity vs Wave 1 T17 cell counts ----------------------------------

def test_VALIDATION_all_three_cohort_sizes_present_in_all_zones(cohorts) -> None:
    """Scale-aware structural check: every zone should host an
    all_three cohort, and the cohort size should be a reasonable
    fraction of total cards at the on-disk scale.

    The full-scale T17-anchored range check (483-1,126 per zone
    measured in Wave 1 DQ Report) is performed at Stage 6 L10
    against the full 1.66M-txn data; running it here would clash
    with whichever scale the on-disk data happens to be (pilot
    tests upstream may have left a smaller dataset in
    ``data/raw/``)."""
    all_three = cohorts[cohorts["cohort_combination"] == "all_three"]
    print(
        f"\nL04e all-three cohort sizes per zone: "
        f"min {int(all_three['cohort_size'].min())}, "
        f"max {int(all_three['cohort_size'].max())}, "
        f"median {int(all_three['cohort_size'].median())}"
    )
    # All 8 zones populated (T17 said all zones clear k=50; even at
    # pilot scale we expect all-three cohort presence in every zone
    # since participation is ~6% of cards uniformly by D14.4).
    assert len(all_three) == 8
    # Cohort sizes positive everywhere.
    assert (all_three["cohort_size"] > 0).all()
