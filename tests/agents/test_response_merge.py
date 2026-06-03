"""Stage 1a tests — ``AgentResponse`` shape + ``merge_own_and_peer``
(SPEC §1.1, §1.2; D25.1, D25.5).

The contract has two pieces validated here:

* The ``AgentResponse`` dataclass holds the documented fields with the
  right defaults — agents that construct one without filling caveats /
  sql / grain_notes don't crash.
* The ``merge_own_and_peer`` helper joins tenant-side (full grain) and
  lake-side (peer at matching grain) frames into the canonical
  comparison shape: ``on`` keys + ``own_value`` + ``peer_benchmark`` +
  ``gap``, with viewer-scoping + identity-strip preconditions enforced.

Subsequent stages (1b chart builder, 1c claims validator) operate on
the merged frame, so getting the shape right here is load-bearing.
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.agents.response import (
    AgentResponse,
    MergeGrainError,
    SqlSurface,
    Telemetry,
    ViewerScopingError,
    merge_own_and_peer,
)


# ---------------------------------------------------------------------
# AgentResponse dataclass shape
# ---------------------------------------------------------------------

def test_agentresponse_minimal_construction() -> None:
    """Required fields only — caveats / sql / grain_notes / telemetry
    default to empty / None so agents don't have to fill them when
    there's nothing to say."""
    resp = AgentResponse(
        result=pd.DataFrame({"category": ["DAIRY"], "own_value": [1.05],
                             "peer_benchmark": [1.0], "gap": [0.05]}),
        chart_intent={"kind": "cross_merchant_comparison"},
        chart=object(),
        headline="Your dairy pricing is roughly in line with peers.",
        claims=[],
    )
    assert resp.caveats == []
    assert resp.sql == []
    assert resp.grain_notes == []
    assert resp.telemetry is None


def test_agentresponse_full_construction() -> None:
    """Every field filled — exercises every dataclass slot."""
    resp = AgentResponse(
        result=pd.DataFrame({"x": [1]}),
        chart_intent={"kind": "kpi_callout"},
        chart=object(),
        headline="One KPI.",
        claims=[],
        caveats=["k-anonymity suppressed 3 cells"],
        sql=[
            SqlSurface(surface="tenant", query="SELECT 1", row_count=1),
            SqlSurface(surface="lake", query="SELECT 2", row_count=2),
        ],
        grain_notes=["no peer SKU"],
        telemetry=Telemetry(model="claude-haiku-4-5", turns=2),
    )
    assert resp.telemetry.model == "claude-haiku-4-5"
    assert len(resp.sql) == 2
    assert resp.sql[0].surface == "tenant"
    assert resp.grain_notes == ["no peer SKU"]


# ---------------------------------------------------------------------
# merge_own_and_peer — happy path: dairy SKU prices vs peer index
# ---------------------------------------------------------------------

@pytest.fixture
def own_dairy() -> pd.DataFrame:
    """Synthetic tenant-side frame: viewer KRG's milk + butter SKUs in
    Zone 5 for the week of 2026-04-05. Carries banner_code for the
    viewer-scoping check."""
    return pd.DataFrame([
        {"banner_code": "KRG", "category": "DAIRY", "derived_zone": "Z05",
         "period_start": "2026-04-05", "own_avg_price": 4.20},
    ])


@pytest.fixture
def peer_dairy() -> pd.DataFrame:
    """Synthetic lake-side peer frame: dairy category at zone Z05 for
    the same week. Already scope_for_viewer'd — no banner_code, has
    peer_relationship instead."""
    return pd.DataFrame([
        {"category": "DAIRY", "derived_zone": "Z05",
         "period_start": "2026-04-05",
         "peer_relationship": "segment_peer", "price_index": 1.00,
         "txn_count": 600},
    ])


def test_merge_shape_and_gap(own_dairy, peer_dairy) -> None:
    merged = merge_own_and_peer(
        own_dairy, peer_dairy,
        on=["category", "derived_zone", "period_start"],
        own_value_col="own_avg_price",
        peer_value_col="price_index",
        viewer="KRG",
    )
    assert len(merged) == 1
    row = merged.iloc[0]
    assert row["own_value"] == 4.20
    assert row["peer_benchmark"] == 1.00
    assert row["gap"] == pytest.approx(4.20 - 1.00)  # difference is the default
    assert row["peer_relationship"] == "segment_peer"
    assert row["txn_count"] == 600
    # Banner-code from own_df is not carried through (it's not in `on`),
    # which matches the source-of-truth shape we want for downstream.
    assert "banner_code" not in merged.columns


def test_merge_ratio_gap(own_dairy, peer_dairy) -> None:
    merged = merge_own_and_peer(
        own_dairy, peer_dairy,
        on=["category", "derived_zone", "period_start"],
        own_value_col="own_avg_price",
        peer_value_col="price_index",
        gap_op="ratio",
        viewer="KRG",
    )
    assert merged.iloc[0]["gap"] == pytest.approx(4.20 / 1.00)


def test_merge_inner_join_drops_unmatched() -> None:
    """Categories present on one side only don't carry through. The
    own side has DAIRY + MEAT; the peer side only DAIRY."""
    own = pd.DataFrame([
        {"banner_code": "KRG", "category": "DAIRY", "own_units": 100},
        {"banner_code": "KRG", "category": "MEAT", "own_units": 80},
    ])
    peer = pd.DataFrame([
        {"category": "DAIRY", "peer_relationship": "segment_peer",
         "units_index": 1.10},
    ])
    merged = merge_own_and_peer(
        own, peer, on=["category"],
        own_value_col="own_units",
        peer_value_col="units_index",
        viewer="KRG",
    )
    assert set(merged["category"]) == {"DAIRY"}
    assert len(merged) == 1


# ---------------------------------------------------------------------
# merge_own_and_peer — error paths
# ---------------------------------------------------------------------

def test_missing_join_key_in_own_raises(peer_dairy) -> None:
    own_no_zone = pd.DataFrame([
        {"banner_code": "KRG", "category": "DAIRY",
         "period_start": "2026-04-05", "own_avg_price": 4.20},
    ])
    with pytest.raises(MergeGrainError) as exc:
        merge_own_and_peer(
            own_no_zone, peer_dairy,
            on=["category", "derived_zone", "period_start"],
            own_value_col="own_avg_price",
            peer_value_col="price_index",
        )
    assert "derived_zone" in str(exc.value)


def test_missing_join_key_in_peer_raises(own_dairy) -> None:
    peer_no_period = pd.DataFrame([
        {"category": "DAIRY", "derived_zone": "Z05",
         "peer_relationship": "segment_peer", "price_index": 1.00},
    ])
    with pytest.raises(MergeGrainError) as exc:
        merge_own_and_peer(
            own_dairy, peer_no_period,
            on=["category", "derived_zone", "period_start"],
            own_value_col="own_avg_price",
            peer_value_col="price_index",
        )
    assert "period_start" in str(exc.value)


def test_missing_value_col_raises(own_dairy, peer_dairy) -> None:
    with pytest.raises(MergeGrainError):
        merge_own_and_peer(
            own_dairy, peer_dairy,
            on=["category", "derived_zone", "period_start"],
            own_value_col="not_a_real_column",
            peer_value_col="price_index",
        )


def test_viewer_scoping_check_catches_wrong_banner(peer_dairy) -> None:
    """Own frame has a row for ACM while viewer is KRG. The merge
    helper must refuse — the own (tenant) frame should be filtered
    to the viewer upstream by the tenant predicate."""
    own_with_acm = pd.DataFrame([
        {"banner_code": "KRG", "category": "DAIRY", "derived_zone": "Z05",
         "period_start": "2026-04-05", "own_avg_price": 4.20},
        {"banner_code": "ACM", "category": "DAIRY", "derived_zone": "Z05",
         "period_start": "2026-04-05", "own_avg_price": 3.80},
    ])
    with pytest.raises(ViewerScopingError) as exc:
        merge_own_and_peer(
            own_with_acm, peer_dairy,
            on=["category", "derived_zone", "period_start"],
            own_value_col="own_avg_price",
            peer_value_col="price_index",
            viewer="KRG",
        )
    assert "ACM" in str(exc.value)


def test_peer_identity_leak_raises(own_dairy) -> None:
    """Peer frame still carries banner_code → identity leak; must
    raise. The peer frame should be scope_for_viewer'd before reaching
    the merge step."""
    peer_with_identity = pd.DataFrame([
        {"banner_code": "ACM", "category": "DAIRY", "derived_zone": "Z05",
         "period_start": "2026-04-05", "price_index": 1.00},
    ])
    with pytest.raises(ViewerScopingError) as exc:
        merge_own_and_peer(
            own_dairy, peer_with_identity,
            on=["category", "derived_zone", "period_start"],
            own_value_col="own_avg_price",
            peer_value_col="price_index",
            viewer="KRG",
        )
    assert "banner_code" in str(exc.value)


def test_peer_identity_leak_catches_merchant_id(own_dairy) -> None:
    peer_with_id = pd.DataFrame([
        {"merchant_id": "KRG", "category": "DAIRY", "derived_zone": "Z05",
         "period_start": "2026-04-05", "price_index": 1.00},
    ])
    with pytest.raises(ViewerScopingError):
        merge_own_and_peer(
            own_dairy, peer_with_id,
            on=["category", "derived_zone", "period_start"],
            own_value_col="own_avg_price",
            peer_value_col="price_index",
            viewer="KRG",
        )


# ---------------------------------------------------------------------
# merge_own_and_peer — multi-row demand-style merge
# ---------------------------------------------------------------------

def test_merge_multi_period_demand_style() -> None:
    """The Demand specialist will merge own units vs peer units_index
    at (category, derived_zone, period_start) over multiple weeks.
    Exercise that shape."""
    own = pd.DataFrame([
        {"banner_code": "KRG", "category": "DAIRY", "derived_zone": "Z05",
         "period_start": "2026-04-05", "own_units": 1000},
        {"banner_code": "KRG", "category": "DAIRY", "derived_zone": "Z05",
         "period_start": "2026-04-12", "own_units": 1100},
        {"banner_code": "KRG", "category": "DAIRY", "derived_zone": "Z05",
         "period_start": "2026-04-19", "own_units": 900},
    ])
    peer = pd.DataFrame([
        {"category": "DAIRY", "derived_zone": "Z05",
         "period_start": "2026-04-05",
         "peer_relationship": "segment_peer", "units_index": 1.00},
        {"category": "DAIRY", "derived_zone": "Z05",
         "period_start": "2026-04-12",
         "peer_relationship": "segment_peer", "units_index": 1.05},
        {"category": "DAIRY", "derived_zone": "Z05",
         "period_start": "2026-04-19",
         "peer_relationship": "segment_peer", "units_index": 0.95},
    ])
    merged = merge_own_and_peer(
        own, peer,
        on=["category", "derived_zone", "period_start"],
        own_value_col="own_units",
        peer_value_col="units_index",
        viewer="KRG",
    )
    assert len(merged) == 3
    # Each row has the three canonical columns.
    assert set(["own_value", "peer_benchmark", "gap"]).issubset(merged.columns)
    # Sort order: pandas merge preserves left-frame row order in default
    # inner join when keys are unique on the right.
    assert list(merged["period_start"]) == [
        "2026-04-05", "2026-04-12", "2026-04-19",
    ]


def test_skip_viewer_check_when_own_has_no_banner_col() -> None:
    """Some agents will pre-filter the own frame in SQL and drop
    banner_code before merging. ``merge_own_and_peer`` should not
    require it — passing ``viewer`` is fine but skipped if the column
    is absent."""
    own_no_banner = pd.DataFrame([
        {"category": "DAIRY", "own_units": 100},
    ])
    peer = pd.DataFrame([
        {"category": "DAIRY", "peer_relationship": "segment_peer",
         "units_index": 1.0},
    ])
    merged = merge_own_and_peer(
        own_no_banner, peer, on=["category"],
        own_value_col="own_units",
        peer_value_col="units_index",
        viewer="KRG",
    )
    assert len(merged) == 1
