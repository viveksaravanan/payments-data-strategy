"""Wave 3 Stage 6.5 follow-up #5 — regression tests for the five
root-cause structural fixes called out in the read-only inspection.

* Fix 1 — Stop silent merge fallback. Specialist rejects emit_response
  with an empty/broken merge spec when both frames are populated,
  surfacing a tool error rather than degrading to the lake frame +
  misleading chart-skipped caveat.
* Fix 2 — Unit/magnitude guard. ``check_magnitude_compatibility``
  catches mismatched scales (e.g. raw $ vs index ≈1.0); the
  specialist surfaces it as a tool error.
* Fix 3 — period_start dtype coercion. ``merge_own_and_peer`` auto-
  coerces date32 (object dtype, ``datetime.date``) and
  ``datetime64[us]`` join keys to a common type, so the merge no
  longer silently produces 0 rows.
* Fix 4 — Prose sanitizer strips stray ``</prose>``,
  ``<parameter ...>``, ``<antml ...>`` markers (plus everything
  after) from the model's prose before validation.
* Fix 5 — No-emit-before-data. Specialist rejects emit_response
  when neither tenant nor lake produced a non-empty frame this
  session.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from src.agents.context import MerchantContext
from src.agents.lake_tools import LakeToolError, sanitize_prose
from src.agents.pricing import PricingSpecialist
from src.agents.response import (
    MergeUnitMismatchError,
    check_magnitude_compatibility,
    merge_own_and_peer,
)


# ---------------------------------------------------------------------
# Fix 4 — Prose sanitizer
# ---------------------------------------------------------------------

def test_sanitize_prose_strips_close_prose_tag() -> None:
    prose = "Real analysis sentence with $5.99 ASP.\n</prose>\n<parameter name=\"chart_intent\">\n{\"kind\": \"kpi_callout\"}"
    cleaned = sanitize_prose(prose)
    assert "</prose>" not in cleaned
    assert "<parameter" not in cleaned
    assert "kpi_callout" not in cleaned
    assert "Real analysis sentence with $5.99 ASP." in cleaned


def test_sanitize_prose_strips_antml_invoke_tags() -> None:
    prose = "Sentence one.<invoke name='emit'><parameter>...</parameter></invoke>"
    cleaned = sanitize_prose(prose)
    assert "<antml" not in cleaned
    assert "Sentence one." in cleaned


def test_sanitize_prose_passes_clean_prose_through() -> None:
    prose = "Your dairy price index is 1.06 above peers."
    assert sanitize_prose(prose) == prose


def test_sanitize_prose_handles_empty_input() -> None:
    assert sanitize_prose("") == ""
    assert sanitize_prose("   ") == ""


def test_sanitize_prose_keeps_curly_braces_unrelated_to_xml() -> None:
    """JSON-shaped text without an XML tool-use marker should
    survive — only the XML markers (and trailing content) get
    stripped."""
    prose = "Set {kind: indicator} as a placeholder marker — not XML."
    cleaned = sanitize_prose(prose)
    assert "{kind: indicator}" in cleaned


# ---------------------------------------------------------------------
# Fix 3 — date dtype coercion in the merge layer
# ---------------------------------------------------------------------

def test_merge_coerces_date32_and_datetime64() -> None:
    """The lake's parquet ``date32[day]`` round-trips as object dtype
    with ``datetime.date`` instances; the tenant's DuckDB
    ``DATE_TRUNC('week', txn_ts)`` materializes as ``datetime64[us]``.
    pandas merge is type-strict — the merge must coerce both sides
    so identical-looking dates actually match."""
    own = pd.DataFrame({
        "period_start": pd.to_datetime(["2026-03-02", "2026-03-09"]),
        "category":     ["DAIRY", "DAIRY"],
        "own_avg_qty":  [1.20, 1.25],
    })
    peer = pd.DataFrame({
        "period_start": [dt.date(2026, 3, 2), dt.date(2026, 3, 9)],
        "category":     ["DAIRY", "DAIRY"],
        "units_index":  [0.95, 1.05],
        "peer_relationship": ["segment_peer", "segment_peer"],
    })
    # Sanity: dtypes are different before merge.
    assert str(own["period_start"].dtype).startswith("datetime64")
    assert peer["period_start"].dtype == object

    merged = merge_own_and_peer(
        own, peer,
        on=["category", "period_start"],
        own_value_col="own_avg_qty",
        peer_value_col="units_index",
        gap_op="difference",
    )
    # 2 own × 2 peer matched on (category, period_start) → 2 rows.
    assert len(merged) == 2
    assert "own_value" in merged.columns
    assert "peer_benchmark" in merged.columns


def test_merge_dtype_matched_keys_unchanged() -> None:
    """When both sides have the same dtype, the merge runs without
    coercing — confirms the coercion only fires on mismatch."""
    own = pd.DataFrame({"k": [1, 2], "own_v": [10.0, 20.0]})
    peer = pd.DataFrame({"k": [1, 2], "peer_v": [5.0, 6.0]})
    merged = merge_own_and_peer(
        own, peer, on=["k"],
        own_value_col="own_v", peer_value_col="peer_v",
    )
    assert len(merged) == 2


# ---------------------------------------------------------------------
# Fix 2 — magnitude compatibility check
# ---------------------------------------------------------------------

def test_magnitude_check_flags_raw_revenue_vs_index() -> None:
    """D4's batch-7 bug — raw revenue (~625k) subtracted from a
    unitless index (~1.0). The check must flag this as
    incompatible."""
    merged = pd.DataFrame({
        "own_value":      [625779.0, 411222.0, 583011.0],
        "peer_benchmark": [1.002,    0.998,    1.011],
    })
    ok, diag = check_magnitude_compatibility(merged)
    assert ok is False
    assert diag["ratio"] > 100


def test_magnitude_check_passes_comparable_units() -> None:
    """own AVG(qty) ~1.2 vs peer units_index ~1.0 — comparable
    units, ratio ~1.2× < 100× threshold."""
    merged = pd.DataFrame({
        "own_value":      [1.20, 1.25, 1.30],
        "peer_benchmark": [0.95, 1.05, 1.00],
    })
    ok, diag = check_magnitude_compatibility(merged)
    assert ok is True
    assert diag["ratio"] < 100


def test_magnitude_check_handles_zero_peer_safely() -> None:
    """When peer_benchmark median is 0 (e.g. wow_delta close to
    flat), the check returns True (no ratio defined) rather than
    raising or false-positiving."""
    merged = pd.DataFrame({
        "own_value":      [1.20, 1.25],
        "peer_benchmark": [0.0,  0.0],
    })
    ok, _ = check_magnitude_compatibility(merged)
    assert ok is True


def test_magnitude_check_handles_empty_merge() -> None:
    """An empty merge result (no rows) is structurally compatible
    by default — there's no comparison to make."""
    merged = pd.DataFrame(
        {"own_value": [], "peer_benchmark": []}
    )
    ok, _ = check_magnitude_compatibility(merged)
    assert ok is True


# ---------------------------------------------------------------------
# Fixes 1 + 2 + 5 — Specialist emit_response preconditions
# ---------------------------------------------------------------------

@pytest.fixture
def viewer_krg() -> MerchantContext:
    return MerchantContext.for_merchant("KRG")


def test_emit_rejected_with_no_data_fetched(viewer_krg) -> None:
    """Fix 5: emit_response with neither tenant nor lake frame
    populated must raise LakeToolError. The previous behavior was
    to accept it, return an empty result + 'no data fetched'
    caveat — surfaced as a degraded section in the preview."""
    specialist = PricingSpecialist(viewer_krg)
    specialist._reset_state()
    # No frames set; emit_response with any args.
    with pytest.raises(LakeToolError) as exc:
        specialist._dispatch_tool("emit_response", {
            "prose": "anything",
            "chart_intent": {"kind": "kpi_callout"},
            "claims": [],
        })
    assert "fetch data" in str(exc.value).lower()


def test_emit_rejected_with_both_frames_but_empty_merge_spec(viewer_krg) -> None:
    """Fix 1: when both tenant and lake frames are populated and
    the specialist requires a merge (Pricing's default
    MERGE_REQUIRED=True), an empty merge spec must surface a tool
    error rather than silently falling back to the lake frame."""
    specialist = PricingSpecialist(viewer_krg)
    specialist._reset_state()
    specialist._tenant_frame = pd.DataFrame({
        "category": ["DAIRY"], "own_avg_qty": [1.2],
    })
    specialist._lake_frame = pd.DataFrame({
        "category": ["DAIRY"], "units_index": [0.95],
        "peer_relationship": ["segment_peer"],
    })

    with pytest.raises(LakeToolError) as exc:
        specialist._dispatch_tool("emit_response", {
            "prose": "vague",
            "merge": {},
            "chart_intent": {"kind": "kpi_callout"},
            "claims": [],
        })
    assert "merge" in str(exc.value).lower()


def test_emit_rejected_when_merge_fails_to_run(viewer_krg) -> None:
    """Fix 1: merge spec references a join key not present in the
    own frame → MergeGrainError surfaces as LakeToolError to the
    model with the available columns listed."""
    specialist = PricingSpecialist(viewer_krg)
    specialist._reset_state()
    specialist._tenant_frame = pd.DataFrame({
        "category": ["DAIRY"], "own_avg_qty": [1.2],
    })
    specialist._lake_frame = pd.DataFrame({
        "category": ["DAIRY"], "derived_zone": ["Z05"],
        "units_index": [0.95], "peer_relationship": ["segment_peer"],
    })

    with pytest.raises(LakeToolError) as exc:
        specialist._dispatch_tool("emit_response", {
            "prose": "v",
            "merge": {
                "on": ["category", "derived_zone"],  # not in tenant
                "own_value_col": "own_avg_qty",
                "peer_value_col": "units_index",
                "gap_op": "difference",
            },
            "chart_intent": {"kind": "kpi_callout"},
            "claims": [],
        })
    msg = str(exc.value).lower()
    assert "derived_zone" in msg or "join key" in msg


def test_magnitude_mismatch_accepts_as_side_by_side(viewer_krg) -> None:
    """Wave 3 Stage 6.5 follow-up #6 softening (Fix A): when own and
    peer columns are in different units, the merge layer nullifies
    the ``gap`` column and the specialist accepts the emit with a
    side-by-side caveat. NO rejection (was rejection in v3-of-this-
    file; the rejection caused retry-thrash on P2/D3/D4/T4)."""
    specialist = PricingSpecialist(viewer_krg)
    specialist._reset_state()
    specialist._tenant_frame = pd.DataFrame({
        "category": ["DAIRY"], "own_revenue": [625779.0],
    })
    specialist._lake_frame = pd.DataFrame({
        "category": ["DAIRY"], "revenue_index": [1.002],
        "peer_relationship": ["segment_peer"],
    })
    # No raise — emit is accepted.
    out = specialist._dispatch_tool("emit_response", {
        "prose": "Your $625k revenue against a peer revenue_index of 1.002 indicates ~baseline performance.",
        "merge": {
            "on": ["category"],
            "own_value_col": "own_revenue",
            "peer_value_col": "revenue_index",
            "gap_op": "difference",
        },
        "chart_intent": {"kind": "kpi_callout", "value": "own_value"},
        "claims": [],
    })
    assert out == {"ok": True}
    assert specialist._emit_args is not None
    # The merge produced a directional-only result; finalize_from_emit
    # would surface the side-by-side caveat.
    from src.agents.response import merge_own_and_peer
    merged = merge_own_and_peer(
        specialist._tenant_frame, specialist._lake_frame,
        on=["category"], own_value_col="own_revenue",
        peer_value_col="revenue_index",
    )
    assert merged.attrs["gap_is_directional"] is True
    import math
    assert math.isnan(merged["gap"].iloc[0])


def test_emit_accepted_when_merge_clean_and_units_match(viewer_krg) -> None:
    """Sanity check: when both frames are populated, merge spec is
    valid, and own_value / peer_benchmark are comparable in scale,
    emit_response is accepted (no raise; _emit_args captured)."""
    specialist = PricingSpecialist(viewer_krg)
    specialist._reset_state()
    specialist._tenant_frame = pd.DataFrame({
        "category": ["DAIRY"], "own_avg_qty": [1.2],
    })
    specialist._lake_frame = pd.DataFrame({
        "category": ["DAIRY"], "units_index": [0.95],
        "peer_relationship": ["segment_peer"],
    })
    out = specialist._dispatch_tool("emit_response", {
        "prose": "Concrete answer with 1.2 vs 0.95.",
        "merge": {
            "on": ["category"],
            "own_value_col": "own_avg_qty",
            "peer_value_col": "units_index",
            "gap_op": "difference",
        },
        "chart_intent": {"kind": "kpi_callout"},
        "claims": [],
    })
    assert out == {"ok": True}
    assert specialist._emit_args is not None


def test_precondition_retry_cap_force_accepts_after_n_rejections(viewer_krg) -> None:
    """Wave 3 Stage 6.5 follow-up #6 (Fix B): after
    MAX_PRECONDITION_REJECTIONS rejections of emit_response, the
    next rejection becomes a force-accept (no raise) so the loop
    doesn't burn turns/cost re-asking the model. The downstream
    graceful path handles the bad spec; the user sees a caveat."""
    from src.agents.specialist import MAX_PRECONDITION_REJECTIONS
    specialist = PricingSpecialist(viewer_krg)
    specialist._reset_state()
    specialist._tenant_frame = pd.DataFrame({
        "category": ["DAIRY"], "own_v": [1.2],
    })
    specialist._lake_frame = pd.DataFrame({
        "category": ["DAIRY"], "peer_v": [1.0],
        "peer_relationship": ["segment_peer"],
    })
    bad_args = {
        "prose": "v",
        "merge": {},   # empty — both frames present → reject
        "chart_intent": {"kind": "kpi_callout"},
        "claims": [],
    }
    # First N-1 rejections raise.
    for _ in range(MAX_PRECONDITION_REJECTIONS - 1):
        with pytest.raises(LakeToolError):
            specialist._dispatch_tool("emit_response", bad_args)
    # The Nth rejection force-accepts.
    out = specialist._dispatch_tool("emit_response", bad_args)
    assert out == {"ok": True}
    assert specialist._force_accept_emit is True
    assert specialist._emit_args is not None


def test_max_turns_is_six(viewer_krg) -> None:
    """Wave 3 Stage 6.5 follow-up #6 (Fix B): MAX_TURNS lowered
    from 10 to 6 — converging pills finish in 3-5; 10 only ever
    extended doomed pills."""
    from src.agents.specialist import DEFAULT_MAX_TURNS
    specialist = PricingSpecialist(viewer_krg)
    assert DEFAULT_MAX_TURNS == 6
    assert specialist.MAX_TURNS == 6


def test_wall_clock_ceiling_constant_present() -> None:
    """Wall-clock ceiling exists and is at the 90s default."""
    from src.agents.specialist import WALL_CLOCK_CEILING_SEC
    assert WALL_CLOCK_CEILING_SEC == 90.0


# ---------------------------------------------------------------------
# Wave 3 Stage 6.5 follow-up #7 — column reconciler in chart_build
# ---------------------------------------------------------------------

def test_reconciler_remaps_known_synonym() -> None:
    """own_store_count → store_count via the synonym table."""
    from src.agents.chart_build import _reconcile_intent
    result = pd.DataFrame({
        "derived_zone": ["Z05", "Z08"],
        "store_count": [3, 1],
    })
    intent = {
        "kind": "cross_merchant_comparison",
        "x": "derived_zone",
        "series": ["own_store_count"],
    }
    reconciled, notes = _reconcile_intent(intent, result)
    assert reconciled["series"] == ["store_count"]
    assert any("own_store_count" in n for n in notes)


def test_reconciler_strips_own_prefix() -> None:
    """own_value (no synonym entry) → value via prefix stripping when
    `value` exists in result."""
    from src.agents.chart_build import _reconcile_intent
    result = pd.DataFrame({"category": ["DAIRY"], "value": [1.2]})
    intent = {"kind": "kpi_callout", "value": "own_value"}
    # own_value is a known synonym → maps via synonym table to itself
    # (no remap), so test on a truly synthetic prefix case:
    intent2 = {"kind": "kpi_callout", "value": "own_revenue_per_txn"}
    result2 = pd.DataFrame({"revenue_per_txn": [12.5]})
    reconciled, notes = _reconcile_intent(intent2, result2)
    assert reconciled["value"] == "revenue_per_txn"


def test_reconciler_partial_series_drops_invalid() -> None:
    """A series list with one valid + one invalid column drops the
    invalid one — a 1-series chart beats no chart."""
    from src.agents.chart_build import _reconcile_intent
    result = pd.DataFrame({
        "period_start": ["2026-04-01", "2026-04-08"],
        "own_value": [1.0, 1.1],
        "peer_benchmark": [0.95, 1.05],
    })
    intent = {
        "kind": "time_series_vs_peers",
        "x": "period_start",
        "series": ["own_value", "fictional_metric"],
    }
    reconciled, notes = _reconcile_intent(intent, result)
    assert "own_value" in reconciled["series"]
    assert "fictional_metric" not in reconciled["series"]
    assert any("fictional_metric" in n for n in notes)


def test_reconciler_case_insensitive() -> None:
    from src.agents.chart_build import _reconcile_intent
    result = pd.DataFrame({
        "Price_Index": [1.05, 0.95],
        "Volume": [100, 200],
        "Category": ["DAIRY", "BREAD"],
    })
    intent = {
        "kind": "scatter_quadrant",
        "x": "price_index",      # lowercase, result has "Price_Index"
        "y": "volume",            # lowercase, result has "Volume"
        "label": "category",      # lowercase, result has "Category"
    }
    reconciled, _ = _reconcile_intent(intent, result)
    assert reconciled["x"] == "Price_Index"
    assert reconciled["y"] == "Volume"
    assert reconciled["label"] == "Category"


def test_reconciler_passthrough_when_columns_already_match() -> None:
    """No remap, no notes when intent columns are already in result."""
    from src.agents.chart_build import _reconcile_intent
    result = pd.DataFrame({
        "period_start": ["2026-04-01"],
        "own_value": [1.0],
        "peer_benchmark": [0.95],
    })
    intent = {
        "kind": "time_series_vs_peers",
        "x": "period_start",
        "series": ["own_value", "peer_benchmark"],
    }
    reconciled, notes = _reconcile_intent(intent, result)
    assert reconciled["series"] == ["own_value", "peer_benchmark"]
    assert notes == []


def test_build_chart_uses_reconciler() -> None:
    """End-to-end: build_chart() reconciles synonyms before assertion."""
    from src.agents.chart_build import build_chart
    result = pd.DataFrame({
        "derived_zone": ["Z02", "Z05", "Z08"],
        "store_count": [2, 4, 1],
    })
    intent = {
        "kind": "cross_merchant_comparison",
        "x": "derived_zone",
        "series": ["own_store_count"],   # synonym for store_count
        "title": "Stores by zone",
        "y_format": "count",
    }
    fig = build_chart(intent, result)
    assert fig is not None  # builder succeeded — reconciler worked


# ---------------------------------------------------------------------
# Wave 3 Stage 6.5 follow-up #7 — merge_incomplete carries own columns
# ---------------------------------------------------------------------

def test_build_result_empty_merge_carries_own_columns(viewer_krg) -> None:
    """When both frames are present and the merge spec is empty, the
    fallback frame carries the own side's columns alongside the
    peer frame so the chart reconciler has something to remap
    against. The `merge_incomplete` flag fires the side-by-side
    caveat downstream."""
    specialist = PricingSpecialist(viewer_krg)
    specialist._reset_state()
    specialist._tenant_frame = pd.DataFrame({
        "category": ["DAIRY"],
        "own_asp": [3.99],
    })
    specialist._lake_frame = pd.DataFrame({
        "category": ["DAIRY", "BREAKFAST"],
        "price_index": [1.06, 0.98],
        "peer_relationship": ["segment_peer", "segment_peer"],
    })
    out = specialist._build_result({})  # empty merge spec
    assert out.attrs.get("merge_incomplete") is True
    assert "own_asp" in out.columns
    assert "price_index" in out.columns


def test_build_result_failed_merge_carries_own_columns(viewer_krg) -> None:
    """If the merge raises (bad keys), fallback carries own columns
    instead of silently dropping them to peer.copy()."""
    specialist = PricingSpecialist(viewer_krg)
    specialist._reset_state()
    specialist._tenant_frame = pd.DataFrame({
        "category": ["DAIRY"],
        "own_asp": [3.99],
    })
    specialist._lake_frame = pd.DataFrame({
        "category": ["DAIRY"],
        "price_index": [1.06],
        "peer_relationship": ["segment_peer"],
    })
    # Bad merge spec — references a column that doesn't exist anywhere.
    out = specialist._build_result({
        "on": ["nonexistent_key"],
        "own_value_col": "own_asp",
        "peer_value_col": "price_index",
    })
    assert out.attrs.get("merge_incomplete") is True
    assert "own_asp" in out.columns


def test_emit_accepted_for_advisor_with_lake_only(viewer_krg) -> None:
    """Fix 1 respects MERGE_REQUIRED=False for the Advisor — a
    single lake-frame answer with no tenant fetch doesn't trigger
    the merge precondition."""
    from src.agents.advisor import ConversationalAdvisor
    advisor = ConversationalAdvisor(viewer_krg)
    advisor._reset_state()
    advisor._lake_frame = pd.DataFrame({
        "derived_zone": ["Z05"], "contactless_share": [0.62],
        "peer_relationship": ["segment_peer"],
    })
    out = advisor._dispatch_tool("emit_response", {
        "prose": "Peer contactless share is 0.62.",
        "merge": {},
        "chart_intent": {"kind": "kpi_callout", "value": "contactless_share"},
        "claims": [],
    })
    assert out == {"ok": True}
