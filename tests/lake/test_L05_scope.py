"""Stage 5 tests — query-time dual-path scoping (SPEC §5, D22.1-.3,
D24.1).

L5 / L5b acceptance:

* For viewer=Kroger, the scoped lake excludes Kroger and tags Acme +
  Winn-Dixie as ``segment_peer``, Taco Bell + TJ Maxx as
  ``cross_segment``.
* The real ``banner_code`` column never reaches the agent surface
  (D24.1 identity strip).
* The L5b assertion ``assert_no_identity_leak`` catches stray
  identity columns before they leak through.
* Cohort tables (which carry no per-merchant rows by construction)
  pass through scope unchanged.
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.lake.scope import (
    IdentityLeakError,
    UnknownViewerError,
    assert_no_identity_leak,
    scope_for_viewer,
    scope_many,
)


@pytest.fixture(scope="module")
def banner_lake() -> pd.DataFrame:
    """A minimal banner-keyed lake fixture — one row per merchant."""
    return pd.DataFrame([
        {"banner_code": "KRG", "derived_zone": "Z01", "category": "produce",
         "txn_count": 1000, "price_index": 1.05},
        {"banner_code": "ACM", "derived_zone": "Z01", "category": "produce",
         "txn_count": 800,  "price_index": 0.95},
        {"banner_code": "WDX", "derived_zone": "Z01", "category": "produce",
         "txn_count": 700,  "price_index": 0.90},
        {"banner_code": "TBL", "derived_zone": "Z01", "category": "qsr_meal",
         "txn_count": 1200, "price_index": 1.10},
        {"banner_code": "TJX", "derived_zone": "Z01", "category": "apparel",
         "txn_count": 600,  "price_index": 0.85},
    ])


# ----- L5: viewer exclusion + relabel ------------------------------------

def test_viewer_kroger_excluded(banner_lake) -> None:
    scoped = scope_for_viewer(banner_lake, "KRG")
    assert len(scoped) == 4   # KRG row gone


def test_viewer_acme_excluded(banner_lake) -> None:
    scoped = scope_for_viewer(banner_lake, "ACM")
    assert len(scoped) == 4


def test_kroger_sees_segment_peers_and_cross_segment(banner_lake) -> None:
    scoped = scope_for_viewer(banner_lake, "KRG")
    # ACM + WDX → segment_peer; TBL + TJX → cross_segment.
    by_count = scoped["peer_relationship"].value_counts().to_dict()
    assert by_count == {"segment_peer": 2, "cross_segment": 2}


def test_taco_bell_sees_no_segment_peer(banner_lake) -> None:
    """Taco Bell is the only QSR — for viewer=TBL, every other row
    is cross_segment."""
    scoped = scope_for_viewer(banner_lake, "TBL")
    assert (scoped["peer_relationship"] == "cross_segment").all()
    assert len(scoped) == 4


def test_tj_maxx_sees_no_segment_peer(banner_lake) -> None:
    """TJ Maxx is the only off-price — viewer=TJX, all others
    cross_segment."""
    scoped = scope_for_viewer(banner_lake, "TJX")
    assert (scoped["peer_relationship"] == "cross_segment").all()
    assert len(scoped) == 4


# ----- L5b: identity strip ------------------------------------------------

def test_banner_code_dropped_from_output(banner_lake) -> None:
    """D24.1: real merchant identity must NEVER reach the agent
    surface. After scope, banner_code is gone."""
    scoped = scope_for_viewer(banner_lake, "KRG")
    assert "banner_code" not in scoped.columns


def test_assert_no_identity_leak_passes_on_scoped(banner_lake) -> None:
    scoped = scope_for_viewer(banner_lake, "KRG")
    # Must not raise.
    assert_no_identity_leak(scoped)


def test_assert_no_identity_leak_catches_raw(banner_lake) -> None:
    """The L5b guard would catch a regression where scope failed to
    strip identity."""
    with pytest.raises(IdentityLeakError):
        assert_no_identity_leak(banner_lake)


def test_assert_no_identity_leak_catches_merchant_id() -> None:
    df = pd.DataFrame([{"merchant_id": "KRG", "x": 1}])
    with pytest.raises(IdentityLeakError):
        assert_no_identity_leak(df)


# ----- Unknown viewer ----------------------------------------------------

def test_unknown_viewer_raises(banner_lake) -> None:
    with pytest.raises(UnknownViewerError):
        scope_for_viewer(banner_lake, "XXX")


# ----- Tables without banner column (cohorts) ---------------------------

def test_cohort_table_passes_through() -> None:
    """``lake_cross_merchant_cohorts`` doesn't carry banner_code —
    cohorts are inherently cross-merchant. Scope passes them through
    unchanged."""
    cohorts = pd.DataFrame([
        {"derived_zone": "Z01", "cohort_combination": "all_three",
         "cohort_size": 500, "median_combined_spend": 1200.0},
    ])
    scoped = scope_for_viewer(cohorts, "KRG")
    assert "peer_relationship" not in scoped.columns
    assert len(scoped) == 1


# ----- Multi-table scope ------------------------------------------------

def test_scope_many_routes_all_tables(banner_lake) -> None:
    """``scope_many`` applies the right transformation per-table."""
    cohorts = pd.DataFrame([
        {"derived_zone": "Z01", "cohort_combination": "all_three",
         "cohort_size": 500, "median_combined_spend": 1200.0},
    ])
    scoped = scope_many(
        {"category_metrics": banner_lake, "cohorts": cohorts},
        "KRG",
    )
    # Banner-keyed table got scoped.
    assert "banner_code" not in scoped["category_metrics"].columns
    assert "peer_relationship" in scoped["category_metrics"].columns
    # Cohort table unchanged.
    assert "peer_relationship" not in scoped["cohorts"].columns
    assert len(scoped["cohorts"]) == 1


# ----- D23.5 dairy worked example shape (small fixture) -----------------

def test_dairy_worked_example_shape() -> None:
    """D23.5: 'For viewer=Kroger, peers in segment_peer for dairy at
    cat/subcat × zone × week, plus own side reaching SKU via tenant.'
    The lake side returns segment_peer dairy rows after scope."""
    dairy = pd.DataFrame([
        {"banner_code": "KRG", "category": "dairy", "subcategory": "milk",
         "derived_zone": "Z03", "period_start": "2026-04-05",
         "grain": "subcat_week", "txn_count": 800, "price_index": 1.02},
        {"banner_code": "ACM", "category": "dairy", "subcategory": "milk",
         "derived_zone": "Z03", "period_start": "2026-04-05",
         "grain": "subcat_week", "txn_count": 600, "price_index": 0.94},
        {"banner_code": "WDX", "category": "dairy", "subcategory": "milk",
         "derived_zone": "Z03", "period_start": "2026-04-05",
         "grain": "subcat_week", "txn_count": 550, "price_index": 0.91},
        {"banner_code": "TBL", "category": "dairy", "subcategory": "milk",
         "derived_zone": "Z03", "period_start": "2026-04-05",
         "grain": "subcat_week", "txn_count": 80,  "price_index": 1.20},
    ])
    scoped = scope_for_viewer(dairy, "KRG")
    # Kroger row gone, others tagged.
    assert "banner_code" not in scoped.columns
    seg_peers = scoped[scoped["peer_relationship"] == "segment_peer"]
    cross = scoped[scoped["peer_relationship"] == "cross_segment"]
    assert len(seg_peers) == 2   # ACM + WDX
    assert len(cross) == 1       # TBL
    # Per-row metrics preserved for the agent to read.
    assert {"category", "subcategory", "derived_zone", "period_start",
            "grain", "txn_count", "price_index"}.issubset(scoped.columns)
