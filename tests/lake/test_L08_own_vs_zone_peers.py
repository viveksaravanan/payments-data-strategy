"""Stage 6 L8 — own-store vs zone-peers benchmark (SPEC §6 L8, D23.6).

D23.6: "A multi-store grocer benchmarks each of its stores against
peers in that store's zone." This is the dual-path applied at
store-level granularity:

* Own side (tenant surface): each Kroger store's own metrics. The
  agent gets these via the tenant predicate.
* Peer side (lake): for that store's ``derived_zone``, the
  ``segment_peer`` rows from ``lake_category_metrics`` and
  ``lake_trade_area``.

The lake side must be queryable per-zone so a multi-store merchant
can ask "store 5 vs peers in Z03". This test exercises that shape.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_RAW = REPO_ROOT / "data" / "raw"


# ----- Lake supports per-zone peer benchmarking -------------------------

def test_category_metrics_is_per_zone(lake_category_metrics) -> None:
    """The category_metrics table carries ``derived_zone``, so a
    store-level question can filter on the store's zone."""
    cat = lake_category_metrics
    assert "derived_zone" in cat.columns
    zones = cat["derived_zone"].unique()
    assert len(zones) == 8, f"expected 8 derived zones; got {len(zones)}"


def test_trade_area_is_per_zone(lake_trade_area) -> None:
    """trade_area is the trade-area density table — per-zone × category
    × merchant. Multi-store merchant benchmarks each store against
    same-zone peers via this table."""
    trade = lake_trade_area
    assert "derived_zone" in trade.columns
    # Each zone hosts multiple merchants (the panel covers all 8 zones).
    by_zone = trade.groupby("derived_zone")["banner_code"].nunique()
    assert (by_zone >= 2).all(), (
        f"each zone should host ≥2 merchants for benchmarking; "
        f"min was {by_zone.min()}"
    )


def test_multistore_kroger_benchmark_where_peers_exist(
    lake_trade_area,
) -> None:
    """D23.6: 'a multi-store grocer benchmarks each store vs peers in
    that store's zone' — i.e. the lake supports per-zone peer
    benchmarking **where peers exist**.

    Honest coverage finding: not every Kroger zone has a peer grocer.
    In the panel as configured (D13.2), zones Z02 and Z06 have KRG
    stores but **zero** ACM or WDX stores. Those zones produce no
    segment_peer rows by construction — the data the lake can publish
    is the data the panel contains. The agent (Wave 3) reads the
    absence and declines the per-zone benchmark in those zones.

    Contract: at least one Kroger zone has segment_peer rows (so the
    benchmarking surface is functional somewhere); per-zone coverage
    is reported below for Wave 3 to consume.
    """
    from src.lake.observable_guard import load_table
    from src.lake.scope import scope_for_viewer
    from src.lake.zones import derive_zone_for_store

    stores = load_table(
        "stores", columns=["store_id", "banner_code", "latitude", "longitude"]
    )
    zones = derive_zone_for_store(stores[["store_id", "latitude", "longitude"]])
    stores = stores.merge(zones, on="store_id")
    kroger_zones = sorted(
        stores.loc[stores["banner_code"] == "KRG", "derived_zone"].unique()
    )
    # Same-segment peer store counts per Kroger zone — explains coverage.
    peer_grocers = stores[
        (stores["banner_code"].isin(("ACM", "WDX")))
        & (stores["derived_zone"].isin(kroger_zones))
    ]
    peers_by_zone = peer_grocers.groupby("derived_zone").size().to_dict()
    print(f"\nL8 Kroger occupies zones: {kroger_zones}")
    print(f"L8 peer-grocer store counts in those zones: {peers_by_zone}")

    scoped = scope_for_viewer(lake_trade_area, "KRG")

    zones_with_peers: list[str] = []
    zones_without_peers: list[str] = []
    for zone in kroger_zones:
        rows = scoped[
            (scoped["derived_zone"] == zone)
            & (scoped["peer_relationship"] == "segment_peer")
        ]
        if len(rows) > 0:
            zones_with_peers.append(zone)
        else:
            zones_without_peers.append(zone)

    print(f"L8 zones with segment_peer rows: {zones_with_peers}")
    print(f"L8 zones without (peer absence): {zones_without_peers}")
    # L8 contract: the benchmarking surface is functional in at least
    # one Kroger zone. Zone-level absence reflects actual panel
    # composition (Z02/Z06 have no peer grocers) and is the honest
    # data result — it would be a §1 violation to manufacture peer
    # rows where no peer transactions exist.
    assert len(zones_with_peers) >= 1, (
        f"L8 contract: at least one Kroger zone must host segment_peer "
        f"cells. None do — that's a build bug, not a coverage gap."
    )
    # The absences must correspond to literal panel structure, not a
    # build dropping cells: every "without_peers" zone has zero peer-
    # grocer stores in the panel.
    for zone in zones_without_peers:
        assert peers_by_zone.get(zone, 0) == 0, (
            f"L8 anomaly: zone {zone} has {peers_by_zone.get(zone, 0)} "
            f"peer-grocer stores but no segment_peer cells — the build "
            f"should have produced cells if peer transactions exist."
        )


def test_taco_bell_has_no_segment_peers_anywhere(lake_trade_area) -> None:
    """Taco Bell is the sole QSR. In every zone it sits in, scoped
    lake produces only cross_segment rows — never segment_peer."""
    from src.lake.scope import scope_for_viewer

    scoped = scope_for_viewer(lake_trade_area, "TBL")
    assert (scoped["peer_relationship"] == "cross_segment").all()


# ----- Tenant side: store-level own data --------------------------------

def test_tenant_predicate_accepts_per_store_own_query() -> None:
    """A Kroger store-level own-side query through the tenant
    predicate is allowed."""
    from src.lake.isolation import check_tenant_predicate

    sql = (
        "SELECT t.store_id, COUNT(*) FROM transactions t "
        "WHERE t.banner_code = 'KRG' GROUP BY t.store_id"
    )
    check_tenant_predicate(sql, "KRG")


def test_tenant_predicate_rejects_cross_merchant_store_query() -> None:
    """Asking 'what are Acme's stores doing?' through the tenant
    predicate while viewer=KRG → rejected. Peer benchmarks come
    through the lake, not the tenant."""
    from src.lake.isolation import (
        TenantIsolationError, check_tenant_predicate,
    )

    sql = (
        "SELECT t.store_id, COUNT(*) FROM transactions t "
        "WHERE t.banner_code = 'ACM' GROUP BY t.store_id"
    )
    with pytest.raises(TenantIsolationError):
        check_tenant_predicate(sql, "KRG")


# ----- Lake does not publish peer store_id -----------------------------

def test_no_peer_store_id_in_lake(lake_trade_area) -> None:
    """L8 invariant: lake does not publish peer-level store_id —
    that would reveal individual peer stores. Peer side stops at
    zone level."""
    assert "store_id" not in lake_trade_area.columns


def test_no_peer_store_id_in_category_metrics(lake_category_metrics) -> None:
    assert "store_id" not in lake_category_metrics.columns
