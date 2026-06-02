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
