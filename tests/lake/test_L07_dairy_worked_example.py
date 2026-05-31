"""Stage 6 L7 — dairy worked example (SPEC §6 L7, D23.5).

D23.5 worked example: "For viewer=Kroger, return a dairy index at
category/subcategory granularity from segment_peer banners in the
same zone × week. Own side reaches SKU via the tenant surface."

This test composes:

1. ``build_lake_category_metrics`` — finest dairy cells
2. ``scope_for_viewer(..., "KRG")`` — segment_peer view
3. The resulting frame supports the dairy index query at
   (category, subcategory, zone, week, grain=subcat_week).

It also verifies the **dual-path split** by exercise:

* Lake side (peers): scoped frame with ``peer_relationship`` and no
  ``banner_code``.
* Tenant side (own): the Wave 1 transactions Parquet still has
  Kroger SKUs under the tenant-isolation predicate; we don't actually
  build the SKU rollup here (that's the agent's job in Wave 3), but
  we assert the prerequisite data is reachable on the tenant surface
  for Kroger and not for peers.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_RAW = REPO_ROOT / "data" / "raw"


@pytest.fixture(scope="module")
def category_metrics(lake_category_metrics) -> pd.DataFrame:
    """Reuse the session-scoped lake_category_metrics fixture from
    conftest.py so we don't rebuild the lake here."""
    return lake_category_metrics


# ----- Lake side: peers via scope ---------------------------------------

def test_dairy_lake_returns_segment_peer_rows_for_kroger(category_metrics) -> None:
    """The category_metrics table must contain dairy rows for KRG's
    segment peers (ACM, WDX) at the subcat_week grain — what D23.5
    asks for."""
    from src.lake.scope import scope_for_viewer

    dairy = category_metrics[category_metrics["category"] == "DAIRY"]
    if len(dairy) == 0:
        pytest.skip("no dairy cells in lake (catalog mismatch?)")

    scoped = scope_for_viewer(dairy, "KRG")
    # Post-scope, no banner_code.
    assert "banner_code" not in scoped.columns
    # Should have rows tagged segment_peer (ACM + WDX).
    seg_peers = scoped[scoped["peer_relationship"] == "segment_peer"]
    assert len(seg_peers) > 0, (
        "Dairy lake should contain segment_peer rows for viewer=KRG"
    )
    # And the rows preserve the dimensions the agent needs.
    needed = {"category", "subcategory", "derived_zone",
              "period_start", "grain", "price_index", "txn_count"}
    assert needed.issubset(scoped.columns)


def test_dairy_subcat_week_grain_present(category_metrics) -> None:
    """D23.5 asks for category/subcategory granularity. At full scale,
    most dairy cells should make it to subcat_week (the finest grain)."""
    dairy = category_metrics[category_metrics["category"] == "DAIRY"]
    if len(dairy) == 0:
        pytest.skip("no dairy cells")
    subcat_week_share = (dairy["grain"] == "subcat_week").mean()
    print(
        f"\nL7 dairy grain distribution: "
        f"subcat_week={subcat_week_share*100:.1f}%, "
        f"cat_week={(dairy['grain']=='cat_week').mean()*100:.1f}%, "
        f"cat_month={(dairy['grain']=='cat_month').mean()*100:.1f}%"
    )
    # Full scale: dairy cells should clear subcat × zone × week ≥ 50 txns
    # most of the time. Both subcat-week and cat-week rows are emitted
    # for the same period when both clear k; the agent picks the finest.
    assert subcat_week_share > 0.3, (
        f"Expected subcat_week to dominate dairy at full scale; "
        f"got {subcat_week_share*100:.1f}%"
    )


def test_dairy_price_index_makes_sense_for_kroger(category_metrics) -> None:
    """For dairy cells scoped to viewer=Kroger, peer price_index
    should center near 1.0 (the index is per-cell / metro mean).
    Median across cells should be in [0.5, 2.0]."""
    from src.lake.scope import scope_for_viewer

    dairy = category_metrics[category_metrics["category"] == "DAIRY"]
    if len(dairy) == 0:
        pytest.skip("no dairy cells")
    scoped = scope_for_viewer(dairy, "KRG")
    median_pi = scoped["price_index"].median()
    print(f"\nL7 dairy peer price_index median (viewer=KRG): {median_pi:.4f}")
    assert 0.5 < median_pi < 2.0


# ----- Tenant side: own SKU reachability --------------------------------

def test_kroger_own_skus_reachable_via_tenant_predicate() -> None:
    """The dual-path split: own data (Kroger) at SKU grain comes from
    the tenant surface, not the lake. Verify the tenant predicate
    accepts an own-SKU query."""
    from src.lake.isolation import check_tenant_predicate

    sql = (
        "SELECT i.sku, SUM(i.qty) FROM transaction_items i "
        "JOIN transactions t ON i.txn_id = t.txn_id "
        "WHERE t.banner_code = 'KRG' "
        "GROUP BY i.sku"
    )
    # Must not raise — KRG querying own SKUs at terminal grain is OK.
    check_tenant_predicate(sql, "KRG")


def test_kroger_cannot_query_peer_skus_via_tenant() -> None:
    """The dual-path closure: KRG cannot reach ACM SKUs through the
    tenant predicate. Peer SKUs are NOT available anywhere — the lake
    only publishes category/subcategory grain."""
    from src.lake.isolation import (
        TenantIsolationError, check_tenant_predicate,
    )

    sql = (
        "SELECT i.sku, SUM(i.qty) FROM transaction_items i "
        "JOIN transactions t ON i.txn_id = t.txn_id "
        "WHERE t.banner_code = 'ACM' "
        "GROUP BY i.sku"
    )
    # KRG asking about ACM SKUs via the tenant surface → rejected.
    with pytest.raises(TenantIsolationError):
        check_tenant_predicate(sql, "KRG")


def test_lake_does_not_publish_peer_sku(category_metrics) -> None:
    """L7 contract: peer side reaches category/subcategory only.
    No sku column anywhere in the lake's category_metrics table."""
    assert "sku" not in category_metrics.columns
    assert "canonical_id" not in category_metrics.columns
