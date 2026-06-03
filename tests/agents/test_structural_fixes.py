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


# ---------------------------------------------------------------------
# Wave 3 Stage 6.5 follow-up #8 — sanitizer narration backstop
# ---------------------------------------------------------------------

from src.agents.lake_tools import business_fallback as _business_fallback

_FALLBACK = _business_fallback()


def test_sanitize_prose_replaces_system_issue_leak() -> None:
    """The exact P1 leak from round-2 batch."""
    prose = (
        "Unable to retrieve complete peer data due to a system issue "
        "filtering by peer relationship. Based on your KRG pricing, "
        "your average ASP is $5.99. To provide a full peer comparison, "
        "I'll need to retry the peer benchmark fetch with corrected "
        "parameters."
    )
    assert sanitize_prose(prose) == _FALLBACK


def test_sanitize_prose_replaces_let_me_pull_narration() -> None:
    prose = (
        "Your dairy index is 1.05. Let me pull peer comparisons to "
        "see how that stacks up."
    )
    assert sanitize_prose(prose) == _FALLBACK


def test_sanitize_prose_replaces_i_need_to_planning() -> None:
    prose = (
        "To assess category performance, I need to compare own velocity "
        "against the peer baseline."
    )
    assert sanitize_prose(prose) == _FALLBACK


def test_sanitize_prose_replaces_corrected_parameters() -> None:
    prose = "I'll retry with corrected parameters."
    assert sanitize_prose(prose) == _FALLBACK


def test_sanitize_prose_replaces_question_ending(viewer_krg=None) -> None:
    """Wave 3.5 — a field ending in a question (the punt-with-a-question
    failure) is neutralized to the business fallback. Agents commit to an
    answer; they never ask the user what to do next."""
    prose = (
        "Your data does not reveal a clear anomaly signal. Would you like "
        "me to focus on a specific category or time window?"
    )
    assert sanitize_prose(prose) == _FALLBACK


def test_sanitize_prose_replaces_i_would_need_to_narration() -> None:
    """Wave 3.5 — 'I would need to compare …' intention-narration is a
    non-answer; neutralized to the fallback."""
    prose = (
        "I would need to compare your week-over-week changes to peer "
        "benchmarks to surface true anomalies."
    )
    assert sanitize_prose(prose) == _FALLBACK


def test_sanitize_prose_preserves_clean_finding_prose() -> None:
    """Clean prose without narration MUST pass through unchanged."""
    prose = (
        "Your dairy price index of 1.06 is 6% above the segment peer "
        "baseline of 1.00, the largest gap in your portfolio."
    )
    assert sanitize_prose(prose) == prose


def test_sanitize_prose_preserves_legitimate_business_phrasing() -> None:
    """Business language like 'need to lift list prices' must survive —
    only INTERNAL mechanics get caught, not merchant action language."""
    prose = (
        "Lift pantry list prices 3-5% before next quarter and recheck "
        "velocity."
    )
    assert sanitize_prose(prose) == prose


def test_sanitize_prose_xml_strip_runs_before_narration_check() -> None:
    """The XML strip pass runs first; if the residue is clean, narration
    detector should not fire."""
    prose = (
        "Your dairy index is 1.05.\n</prose>\n<parameter>...</parameter>"
    )
    assert sanitize_prose(prose) == "Your dairy index is 1.05."


def test_sanitize_prose_handles_opening_prose_tag_wrap() -> None:
    """Wave 3 Stage 6.5 follow-up #8 round-3 batch: Haiku wrapped its
    answer in <prose>...</prose>, the trailing-junk regex matched
    the OPENING <prose> at position 0 and ate the entire string with
    .*, producing empty prose. The fix strips bare opening <prose>
    before the trailing-junk pass."""
    prose = (
        "<prose>Your baby category runs at $13.63 above the peer "
        "price index of 1.09.</prose>"
    )
    out = sanitize_prose(prose)
    assert "Your baby category runs at $13.63" in out
    assert "<prose>" not in out
    assert "</prose>" not in out


def test_sanitize_prose_handles_wrap_with_trailing_tool_blob() -> None:
    """<prose>answer</prose><parameter>...</parameter> — strip both
    tags + the trailing parameter blob, keep the answer."""
    prose = (
        "<prose>Your dairy index is 1.05.</prose>"
        "<parameter name='chart_intent'>{\"kind\":\"kpi_callout\"}</parameter>"
    )
    out = sanitize_prose(prose)
    assert out == "Your dairy index is 1.05."


# ---------------------------------------------------------------------
# Wave 3 Stage 6.5 follow-up #8 — chart-authoring regression protections
# ---------------------------------------------------------------------

def test_kpi_callout_without_value_field_still_fails() -> None:
    """Fix 8a tightened demand.md but the chart builder's per-kind
    required-field check is what actually rejects {"kind":
    "kpi_callout"} with no value. Regression-protect that."""
    import pandas as pd
    from src.agents.chart_build import (
        UnsupportedIntentError, build_chart,
    )
    result = pd.DataFrame({"own_value": [1.05]})
    with pytest.raises(UnsupportedIntentError):
        build_chart({"kind": "kpi_callout", "title": "x"}, result)


def test_prose_business_fallback_when_all_claims_stripped(viewer_krg) -> None:
    """Wave 3 Stage 6.5 Fix 9e: when prose is empty AND all claims
    are stripped (e.g. naked CellLookups that don't match), prose
    falls back to ``business_fallback()`` — a single canonical
    business-language string. No mechanics terms reach the user."""
    specialist = PricingSpecialist(viewer_krg)
    specialist._reset_state()
    specialist._tenant_frame = pd.DataFrame({
        "category": ["MEAT"], "own_value": [1000.0],
        "peer_benchmark": [1.06], "gap": [998.94],
    })
    specialist._emit_args = {
        "headline": "",
        "chart_intent": {"kind": "kpi_callout", "value": "own_value"},
        "claims": [
            {
                "text_span": "Meat revenue is $3.94M",
                "value": 3940000.0,  # wrong (real is 1000.0)
                "source": {
                    "type": "CellLookup",
                    "row_filter": {"category": "MEAT"},
                    "column": "own_value",
                },
            },
        ],
        "caveats": [],
    }
    response = specialist._finalize_from_emit(converged=True, turns=4)
    assert response.prose == _business_fallback()


def test_evidence_promoted_to_headline_when_headline_empty(viewer_krg) -> None:
    """Wave 3.5 — the old fragment-from-claims backfill is gone. When the
    model emits an empty headline but a non-empty (validated) evidence
    list, the first surviving evidence point is PROMOTED to the headline
    so the answer still leads with a real finding — never a fragment
    run-on joined from claim text_spans."""
    specialist = PricingSpecialist(viewer_krg)
    specialist._reset_state()
    # Clean-merge path — the merged frame is the result-of-record.
    specialist._tenant_frame = pd.DataFrame({
        "category": ["DAIRY"], "own_value": [3.99],
        "peer_benchmark": [1.06], "gap": [2.93],
        "price_index": [1.06],
    })
    specialist._emit_args = {
        "headline": "",   # ← model forgot to write the lead
        "evidence": ["Your dairy price index is 1.06 against peers."],
        "claims": [
            {
                "text_span": "dairy price index is 1.06",
                "value": 1.06,
                "source": {
                    "type": "CellLookup",
                    "row_filter": {"category": "DAIRY"},
                    "column": "price_index",
                },
            },
        ],
        "caveats": [],
    }
    response = specialist._finalize_from_emit(converged=True, turns=4)
    # Promoted: the lead is the (only) evidence point; evidence emptied.
    assert "dairy price index is 1.06" in response.headline.lower()
    assert response.evidence == []
    # And NOT the canonical all-stripped fallback.
    assert response.headline != _business_fallback()


def test_empty_headline_and_no_evidence_falls_back(viewer_krg) -> None:
    """Wave 3.5 — when neither headline nor evidence survive (and there
    is nothing to promote), the lead is the single canonical
    business-fallback string, never a claim-fragment synthesis."""
    specialist = PricingSpecialist(viewer_krg)
    specialist._reset_state()
    specialist._tenant_frame = pd.DataFrame({
        "category": ["DAIRY"], "own_value": [3.99],
        "peer_benchmark": [1.06], "gap": [2.93], "price_index": [1.06],
    })
    specialist._emit_args = {
        "headline": "",
        "evidence": [],
        "claims": [
            {
                "text_span": "dairy price index is 1.06",
                "value": 1.06,
                "source": {
                    "type": "CellLookup",
                    "row_filter": {"category": "DAIRY"},
                    "column": "price_index",
                },
            },
        ],
        "caveats": [],
    }
    response = specialist._finalize_from_emit(converged=True, turns=4)
    assert response.prose == _business_fallback()


def test_chart_intent_with_sentence_in_value_field_still_fails() -> None:
    """Fix 8a forbids `value="Need peer data"` in the prompt. The
    reconciler does NOT silently invent a column when the named
    value is a sentence/placeholder with no real-column match."""
    import pandas as pd
    from src.agents.chart_build import (
        MissingColumnError, UnsupportedIntentError, build_chart,
    )
    result = pd.DataFrame({"period_start": ["2026-04-01"], "revenue": [100]})
    with pytest.raises((MissingColumnError, UnsupportedIntentError)):
        build_chart(
            {"kind": "kpi_callout", "value": "Need peer data",
             "title": "x"},
            result,
        )


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
            "headline": "anything",
            "chart_intent": {"kind": "kpi_callout"},
            "claims": [],
        })
    assert "fetch data" in str(exc.value).lower()


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


# ---------------------------------------------------------------------
# Wave 3 Stage 6.5 follow-up #7 — merge_incomplete carries own columns
# (Stage 7 trim: broadcast fallback retired; tests cover the
# now-simpler own.copy() fallback path)
# ---------------------------------------------------------------------

def test_build_result_empty_merge_returns_own_with_incomplete_flag(viewer_krg) -> None:
    """Stage 7 trim: when both frames are present and the merge spec
    is empty, the legacy _build_result returns own.copy() with
    merge_incomplete attr set. The broadcast fallback that used to
    repeat own scalars down peer rows is retired (it caused the
    all-stripped cascade pre-Fix-7; Fix 9b's dual-frame on the
    emit_response path handles real merge failures)."""
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
    # Stage 7 trim: own.copy() returned — peer columns are NOT
    # broadcast into it (that was the all-stripped-cascade bug pre-
    # Fix-7). Dual-frame routing on the emit_response path handles
    # peer access via _merge_fail_payload.
    assert "own_asp" in out.columns
    assert "price_index" not in out.columns


def test_build_result_failed_merge_returns_own_with_incomplete_flag(viewer_krg) -> None:
    """Stage 7 trim: if the merge raises (bad keys), the legacy
    _build_result returns own.copy() with merge_incomplete attr set.
    The broadcast-carry-both-sides fallback is retired."""
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
    # Stage 7 trim: peer columns not broadcast in.
    assert "price_index" not in out.columns


# ---------------------------------------------------------------------
# Wave 3 Stage 6.5 Fix 9 — merge-as-its-own-tool-turn + companion fixes
# ---------------------------------------------------------------------


def test_claim_with_tenant_frame_resolves(viewer_krg) -> None:
    """Fix 9b: in the merge-fail dual-frame path, a claim setting
    ``source.frame = 'tenant'`` resolves against the real tenant
    frame (not against the single ``result`` argument the validator
    receives)."""
    from src.agents.claims import (
        CellLookup,
        Claim,
        validate_claims,
    )
    tenant = pd.DataFrame({"category": ["DAIRY"], "own_revenue": [625779.0]})
    lake = pd.DataFrame({"category": ["DAIRY"], "price_index": [1.06]})
    claim = Claim(
        text_span="own revenue is 625779",
        value=625779.0,
        source=CellLookup(
            row_filter={"category": "DAIRY"},
            column="own_revenue",
            frame="tenant",
        ),
    )
    report = validate_claims(
        "your own revenue is 625779 above the segment baseline",
        [claim],
        result=lake,                     # not where the claim lives
        frames={"tenant": tenant, "lake": lake},
    )
    assert report.claim_dispositions[0].status == "passed"


def test_claim_with_lake_frame_resolves(viewer_krg) -> None:
    """Fix 9b mirror: claim setting ``source.frame = 'lake'`` resolves
    against the lake frame, even when ``result`` is the tenant
    frame."""
    from src.agents.claims import (
        CellLookup,
        Claim,
        validate_claims,
    )
    tenant = pd.DataFrame({"category": ["DAIRY"], "own_revenue": [625779.0]})
    lake = pd.DataFrame({"category": ["DAIRY"], "price_index": [1.06]})
    claim = Claim(
        text_span="peer price_index of 1.06",
        value=1.06,
        source=CellLookup(
            row_filter={"category": "DAIRY"},
            column="price_index",
            frame="lake",
        ),
    )
    report = validate_claims(
        "segment peers run at peer price_index of 1.06.",
        [claim],
        result=tenant,
        frames={"tenant": tenant, "lake": lake},
    )
    assert report.claim_dispositions[0].status == "passed"


def test_multi_row_cell_lookup_without_agg_rejected_at_emit(viewer_krg) -> None:
    """Fix 9c: a CellLookup whose row_filter matches multiple rows
    AND has no ``agg`` is rejected at emit time with a legible
    Rule-7 error, BEFORE the validator silently strips it. The
    model retries with ``agg="sum"`` or ``agg="mean"``."""
    specialist = PricingSpecialist(viewer_krg)
    specialist._reset_state()
    # Multi-row tenant frame: filter on category matches 3 rows.
    specialist._tenant_frame = pd.DataFrame({
        "category": ["DAIRY", "DAIRY", "DAIRY"],
        "period_start": ["2026-04-06", "2026-04-13", "2026-04-20"],
        "own_value":   [1000.0, 1100.0, 1200.0],
        "peer_benchmark": [1.0, 1.05, 1.06],
        "gap":         [999.0, 1099.0, 1198.99],
    })
    bad_args = {
        "headline": "Meat revenue totals 3.94M.",
        "chart_intent": {"kind": "kpi_callout", "value": "own_value"},
        "claims": [
            {
                "text_span": "Meat revenue totals 3.94M",
                "value": 3940000.0,
                "source": {
                    "type": "CellLookup",
                    "row_filter": {"category": "DAIRY"},  # matches 3 rows
                    "column": "own_value",
                    # NO agg — this is the bug Fix 9c surfaces.
                },
            },
        ],
        "caveats": [],
    }
    with pytest.raises(LakeToolError) as exc:
        specialist._dispatch_tool("emit_response", bad_args)
    msg = str(exc.value)
    assert "agg" in msg
    assert "matches 3 rows" in msg or "matches" in msg


def test_chart_intent_is_ignored_when_charts_deferred(viewer_krg) -> None:
    """Wave 3.5 §11.2 — charts are held for Wave 4. The model can no
    longer author a chart_intent (removed from the emit schema), but if
    a stray one leaks through it is SILENTLY ignored: ``chart=None`` and
    NO chart-error caveat reaches the user (this is the very leak — an
    ``UnsupportedIntentError`` in a caveat — that prompted the gate-off).
    The chart-builder's own numeric-axis guard remains tested directly
    in ``test_chart_build.py`` (chart_build.py is kept intact/dormant)."""
    specialist = PricingSpecialist(viewer_krg)
    specialist._reset_state()
    specialist._tenant_frame = pd.DataFrame({
        "period_start": pd.to_datetime(
            ["2026-04-06", "2026-04-13", "2026-04-20"]
        ),
        "own_value": [1.0, 1.1, 1.2],
        "peer_benchmark": [0.95, 0.98, 1.0],
    })
    specialist._tenant_frame = pd.DataFrame({"x": [1]})
    specialist._lake_frame   = pd.DataFrame({"x": [1]})
    specialist._merge_attempted = True
    specialist._emit_args = {
        "headline": "Dairy own price runs higher.",
        # A malformed chart_intent that WOULD have errored under the
        # old build path — must be ignored, not surfaced as a caveat.
        "chart_intent": {
            "kind": "kpi_callout",
            "value": "period_start",   # ← datetime, not numeric
            "title": "x",
        },
        "claims": [],
        "caveats": [],
    }
    response = specialist._finalize_from_emit(converged=True, turns=4)
    assert response.chart is None
    assert not any(
        "chart" in c.lower() or "non-numeric" in c.lower()
        for c in response.caveats
    )


def test_business_fallback_used_for_narration() -> None:
    """Fix 9e: when sanitize_prose detects narration mechanics
    ("system issue", "retry with corrected parameters"), the prose
    is replaced with ``business_fallback()`` — single canonical
    business-language string."""
    from src.agents.lake_tools import business_fallback, sanitize_prose
    narration = (
        "Unable to retrieve complete peer data due to a system "
        "issue filtering by peer relationship; I'll retry with "
        "corrected parameters."
    )
    assert sanitize_prose(narration) == business_fallback()


def test_no_mechanics_terms_in_business_fallback() -> None:
    """Fix 9e: the business-language fallback string contains zero
    forbidden mechanics terms ("validator", "draft", "merge spec",
    "retry with corrected parameters", "tool error", "system
    issue", "precondition", "force-accept", "claim disposition")."""
    from src.agents.lake_tools import (
        _FORBIDDEN_MECHANICS_TERMS,
        business_fallback,
    )
    prose = business_fallback().lower()
    for term in _FORBIDDEN_MECHANICS_TERMS:
        assert term.lower() not in prose, (
            f"Mechanics term {term!r} found in business fallback: {prose!r}"
        )


def test_no_mechanics_terms_in_assembled_response_prose(viewer_krg) -> None:
    """Fix 9e end-to-end: assemble a synthetic AgentResponse via
    the all-stripped path and confirm no mechanics term reaches
    ``response.prose``."""
    from src.agents.lake_tools import _FORBIDDEN_MECHANICS_TERMS
    specialist = PricingSpecialist(viewer_krg)
    specialist._reset_state()
    specialist._tenant_frame = pd.DataFrame({
        "category": ["MEAT"], "own_value": [1000.0],
        "peer_benchmark": [1.06], "gap": [998.94],
    })
    specialist._emit_args = {
        "headline": "",
        "chart_intent": {"kind": "kpi_callout", "value": "own_value"},
        "claims": [
            {
                "text_span": "wrong total 3.94M",
                "value": 3940000.0,
                "source": {
                    "type": "CellLookup",
                    "row_filter": {"category": "MEAT"},
                    "column": "own_value",
                },
            },
        ],
        "caveats": [],
    }
    response = specialist._finalize_from_emit(converged=True, turns=4)
    prose_lower = response.prose.lower()
    for term in _FORBIDDEN_MECHANICS_TERMS:
        assert term.lower() not in prose_lower, (
            f"Mechanics term {term!r} found in response.prose: {response.prose!r}"
        )


# ---------------------------------------------------------------------
# Wave 3 Stage 6.5 Fix 10 — auto-invoke build_merge + dual-frame routing
# ---------------------------------------------------------------------


_LAKE_CAT_MANIFEST = {
    "dimensions": ["peer_relationship", "category", "subcategory",
                    "derived_zone", "period_start", "grain"],
    "metrics": ["txn_count", "price_index", "revenue_index",
                "units_index", "basket_penetration_share",
                "promo_active_share", "wow_delta"],
}

_LAKE_TRADE_MANIFEST = {
    "dimensions": ["peer_relationship", "derived_zone", "category"],
    "metrics": ["store_count", "cell_units", "cell_revenue",
                "share_of_zone", "zone_category_volume_index", "txn_count"],
}


def test_untagged_peer_claim_resolves_against_lake_frame() -> None:
    """Fix 10c — dual-frame path: a claim with no ``frame`` field but
    naming a column that only exists in the lake frame resolves via
    the frame walk. No model-supplied frame tag required."""
    from src.agents.claims import CellLookup, Claim, validate_claims
    tenant = pd.DataFrame({
        "category": ["DAIRY"], "own_revenue": [625779.0],
    })
    lake = pd.DataFrame({
        "category": ["DAIRY"], "price_index": [1.06],
    })
    claim = Claim(
        text_span="peer price_index of 1.06",
        value=1.06,
        source=CellLookup(
            row_filter={"category": "DAIRY"},
            column="price_index",
            # No `frame` field — relies on the walk to find lake.
        ),
    )
    report = validate_claims(
        "segment peers index 1.06 above baseline.",
        [claim],
        result=tenant,
        frames={"tenant": tenant, "lake": lake},
    )
    assert report.claim_dispositions[0].status == "passed"


def test_untagged_claim_prefers_result_frame_when_present() -> None:
    """Fix 10c — when the column exists in the ``result`` frame, the
    resolver picks ``result`` first (clean-merge path behavior is
    unchanged; the frame walk is a fallback)."""
    from src.agents.claims import CellLookup, Claim, validate_claims
    merged = pd.DataFrame({
        "category": ["DAIRY"], "own_value": [3.50],
        "peer_benchmark": [1.06], "gap": [2.44],
    })
    tenant = pd.DataFrame({"category": ["DAIRY"], "own_value": [9.99]})
    lake = pd.DataFrame({"category": ["DAIRY"], "peer_benchmark": [9.99]})
    # `own_value` is in BOTH merged and tenant — but the result is
    # merged, so the lookup resolves there (value=3.50, not 9.99).
    claim = Claim(
        text_span="3.50",
        value=3.50,
        source=CellLookup(
            row_filter={"category": "DAIRY"},
            column="own_value",
        ),
    )
    report = validate_claims(
        "your dairy price 3.50.",
        [claim],
        result=merged,
        frames={"merged": merged, "tenant": tenant, "lake": lake},
    )
    assert report.claim_dispositions[0].status == "passed"


# ---------------------------------------------------------------------
# Wave 3 Stage 6.5 Fix 11 — surface lake aggregates + list-valued
# (OR-clause) row filters in CellLookup
# ---------------------------------------------------------------------


def test_list_valued_row_filter_resolves_via_isin() -> None:
    """Fix 11b: a CellLookup with a list-valued row_filter entry
    (the model's IN-clause shape — ``{"derived_zone": ["Z02","Z03"]}``
    meaning "Z02 OR Z03") resolves via ``.isin(v)`` instead of
    crashing with pandas' ``Lengths must match to compare``."""
    from src.agents.claims import CellLookup, aggregate_column
    df = pd.DataFrame({
        "derived_zone": ["Z01", "Z02", "Z03", "Z04"],
        "category": ["MEAT"] * 4,
        "share_of_zone": [0.10, 0.42, 0.41, 0.20],
    })
    claim = CellLookup(
        row_filter={"derived_zone": ["Z02", "Z03"], "category": "MEAT"},
        column="share_of_zone",
        agg="mean",
    )
    out = claim.resolve(df)
    # Z02 + Z03 mean = (0.42 + 0.41) / 2 = 0.415
    expected = aggregate_column(
        df[df["derived_zone"].isin(["Z02", "Z03"])],
        "share_of_zone",
        "mean",
    )
    assert out == expected


def test_fix9c_gate_counts_list_valued_multi_row_correctly(viewer_krg) -> None:
    """Fix 11b: the 9c multi-row gate (in Specialist._count_filter_matches)
    counts list-valued matches via .isin instead of silently returning
    None when the resolver would crash. A list-valued filter matching
    multiple rows without ``agg`` must be REJECTED at emit (not
    silently passed and then crashed at validation)."""
    specialist = PricingSpecialist(viewer_krg)
    specialist._reset_state()
    specialist._tenant_frame = pd.DataFrame({
        "derived_zone": ["Z01", "Z02", "Z03"],
        "category": ["MEAT", "MEAT", "MEAT"],
        "share_of_zone": [0.10, 0.42, 0.41],
    })
    # Multi-row list-valued filter without agg → must reject.
    args = {
        "headline": "x",
        "chart_intent": {"kind": "kpi_callout", "value": "share_of_zone"},
        "claims": [
            {
                "text_span": "Z02–Z03 share is 0.4",
                "value": 0.4,
                "source": {
                    "type": "CellLookup",
                    "row_filter": {"derived_zone": ["Z02", "Z03"]},
                    "column": "share_of_zone",
                    # No agg — must reject.
                },
            },
        ],
    }
    with pytest.raises(LakeToolError) as exc:
        specialist._dispatch_tool("emit_response", args)
    assert "matches 2 rows" in str(exc.value)


def test_apply_row_filter_scalar_vs_list_consistent() -> None:
    """Helper sanity: ``_apply_row_filter`` produces the same subset
    whether a single-element list ``[X]`` or a scalar ``X`` is used.
    The gate and resolver use this helper identically; they cannot
    diverge on shape."""
    from src.agents.claims import _apply_row_filter
    df = pd.DataFrame({
        "category": ["BABY", "MEAT", "MEAT", "DAIRY"],
        "price_index": [1.05, 1.02, 1.03, 1.06],
    })
    scalar = _apply_row_filter(df, {"category": "MEAT"})
    one_list = _apply_row_filter(df, {"category": ["MEAT"]})
    assert len(scalar) == len(one_list) == 2
    assert scalar["price_index"].tolist() == one_list["price_index"].tolist()



# ---------------------------------------------------------------------
# Wave 3 Stage 6.5 Fix 13 — ValueRef source shape + claims-path
# TypeError on datetime columns
# ---------------------------------------------------------------------


def test_value_ref_claim_passes_byte_identical(viewer_krg) -> None:
    """Fix 13a: a ValueRef claim resolves to the EXACT aggregate from
    the same code path the validator uses; ``claim.value`` is
    overridden at validation time so the comparison is trivially
    equal. Status is ``passed``, NOT ``normalized``."""
    from src.agents.claims import (
        Claim, ValueRef, aggregate_column, validate_claims,
    )
    # Multi-row frame mimicking lake_category_metrics shape.
    lake = pd.DataFrame({
        "category": ["MEAT"] * 4 + ["BABY"] * 3,
        "derived_zone": ["Z01", "Z02", "Z03", "Z04", "Z01", "Z02", "Z03"],
        "units_index": [0.9229, 0.9230, 0.9228, 0.9229, 0.9231, 0.9232, 0.9230],
    })
    # The model authors a claim with ValueRef (the address shape)
    # and a placeholder ``value`` — server will override.
    claim = Claim(
        text_span="peer baseline of 0.92",   # model wrote rounded text
        value=0.92,                          # placeholder — overridden
        source=ValueRef(
            by="category", value="MEAT", metric="units_index",
            frame="lake",
        ),
    )
    report = validate_claims(
        prose="MEAT peer baseline of 0.92 indicates parity.",
        claims=[claim],
        result=lake,
        frames={"lake": lake},
    )
    d = report.claim_dispositions[0]
    assert d.status == "passed"
    # ValueRef set claim.value to the resolved aggregate (not 0.92).
    expected = aggregate_column(
        lake[lake["category"] == "MEAT"], "units_index", "mean",
    )
    assert claim.value == expected


def test_datetime_column_claim_strips_cleanly_no_typeerror() -> None:
    """Fix 13b: a CellLookup on a datetime column raises ``TypeError``
    inside ``_resolve_in`` / ``aggregate_column``. The validator's
    per-claim catch tuple now includes ``TypeError``, AND the cast
    sites re-raise as ``LookupError``. Either way: clean strip,
    no crash propagates."""
    from src.agents.claims import CellLookup, Claim, validate_claims
    df = pd.DataFrame({
        "category": ["MEAT", "BABY"],
        "period_start": pd.to_datetime(["2026-04-13", "2026-04-20"]),
    })
    # Single-row match (won't hit aggregate_column).
    claim = Claim(
        text_span="MEAT decline started week of 2026-04-13",
        value=20260413.0,    # not even meaningful
        source=CellLookup(
            row_filter={"category": "MEAT"},
            column="period_start",
        ),
    )
    # Must NOT raise TypeError.
    report = validate_claims(
        prose="MEAT decline started week of 2026-04-13.",
        claims=[claim],
        result=df,
    )
    # Claim strips cleanly.
    assert report.claim_dispositions[0].status == "stripped"


def test_datetime_column_claim_aggregate_strips_cleanly() -> None:
    """Same shape but multi-row → exercises the aggregate_column
    path's TypeError handling."""
    from src.agents.claims import CellLookup, Claim, validate_claims
    df = pd.DataFrame({
        "category": ["MEAT", "MEAT", "BABY"],
        "period_start": pd.to_datetime(
            ["2026-04-13", "2026-04-20", "2026-04-13"],
        ),
    })
    claim = Claim(
        text_span="MEAT period_start mean is X",
        value=0.0,
        source=CellLookup(
            row_filter={"category": "MEAT"},
            column="period_start",
            agg="mean",   # forces multi-row path
        ),
    )
    report = validate_claims(
        prose="x",
        claims=[claim],
        result=df,
    )
    assert report.claim_dispositions[0].status == "stripped"
    # The reason should mention the datetime / non-numeric column.
    reason = (report.claim_dispositions[0].reason or "").lower()
    assert "numeric" in reason or "float" in reason or "dtype" in reason


def test_value_ref_falls_through_to_strip_when_dim_missing() -> None:
    """Fix 13a: ValueRef can't fabricate; if the (by, value, metric)
    triple doesn't resolve in the named frame, it raises LookupError
    just like CellLookup → claim strips. Grounding intact."""
    from src.agents.claims import Claim, ValueRef, validate_claims
    lake = pd.DataFrame({
        "category": ["MEAT"],
        "units_index": [0.9229],
    })
    claim = Claim(
        text_span="DAIRY peer index is X",
        value=0.92,
        source=ValueRef(
            by="category", value="DAIRY",   # not in frame
            metric="units_index", frame="lake",
        ),
    )
    report = validate_claims(
        "DAIRY peer index.",
        [claim],
        result=lake,
        frames={"lake": lake},
    )
    assert report.claim_dispositions[0].status == "stripped"


def test_pricing_literal_value_path_unregressed() -> None:
    """Fix 13a is additive — the literal CellLookup + ``value=<float>``
    path that pricing uses (P1, P3 — exact-precision verbatim copy)
    still resolves to ``passed`` when the model writes the exact
    value."""
    from src.agents.claims import CellLookup, Claim, aggregate_column, validate_claims
    df = pd.DataFrame({
        "category": ["MEAT"] * 4,
        "price_index": [1.0258, 1.0258, 1.0258, 1.0258],
    })
    # Model wrote the exact full-precision value (pricing pattern).
    true = aggregate_column(df, "price_index", "mean")
    claim = Claim(
        text_span=f"peer index of {true}",
        value=true,
        source=CellLookup(
            row_filter={"category": "MEAT"},
            column="price_index", agg="mean",
        ),
    )
    report = validate_claims(
        f"peer index of {true}.",
        [claim],
        result=df,
    )
    assert report.claim_dispositions[0].status == "passed"


# ---------------------------------------------------------------------
# Wave 3 Stage 6.5 Fix 14 — auto-add peer_benchmark to series for
# cross-merchant chart kinds
# ---------------------------------------------------------------------


def test_chart_reconciler_adds_peer_benchmark_to_cross_merchant_comparison() -> None:
    """Fix 14: when the model authors a cross-merchant chart with
    only ``own_value`` in the series, and the merged frame carries
    ``peer_benchmark``, the reconciler auto-adds peer_benchmark so
    the rendered chart shows BOTH series side-by-side. Server-side
    enforcement of the comparison the chart kind is named after."""
    from src.agents.chart_build import _reconcile_intent
    result = pd.DataFrame({
        "category": ["DAIRY", "MEAT"],
        "own_value": [3.50, 6.36],
        "peer_benchmark": [1.00, 1.03],
    })
    intent = {
        "kind": "cross_merchant_comparison",
        "x": "category",
        "series": ["own_value"],   # ← model omitted peer
        "y_format": "index",
        "title": "x",
    }
    reconciled, notes = _reconcile_intent(intent, result)
    assert reconciled["series"] == ["own_value", "peer_benchmark"]
    assert any("peer_benchmark" in n for n in notes)


def test_chart_reconciler_preserves_explicit_peer_series() -> None:
    """Fix 14 doesn't double-add — if the model already named
    ``peer_benchmark`` in series, leave the series alone."""
    from src.agents.chart_build import _reconcile_intent
    result = pd.DataFrame({
        "category": ["DAIRY"],
        "own_value": [3.50],
        "peer_benchmark": [1.00],
    })
    intent = {
        "kind": "cross_merchant_comparison",
        "x": "category",
        "series": ["own_value", "peer_benchmark"],
        "y_format": "index",
    }
    reconciled, _ = _reconcile_intent(intent, result)
    # Still exactly own_value + peer_benchmark, no duplication.
    assert reconciled["series"] == ["own_value", "peer_benchmark"]


def test_chart_reconciler_skips_peer_add_when_column_missing() -> None:
    """Fix 14 only adds peer_benchmark when it's actually in the
    merged frame. Single-source paths (Advisor on payment_mix, or
    dual-frame branches without peer_benchmark in the chosen frame)
    don't get a fake peer column added."""
    from src.agents.chart_build import _reconcile_intent
    # Tenant-only frame; no peer_benchmark.
    result = pd.DataFrame({
        "category": ["DAIRY"],
        "own_value": [3.50],
    })
    intent = {
        "kind": "cross_merchant_comparison",
        "x": "category",
        "series": ["own_value"],
        "y_format": "currency",
    }
    reconciled, _ = _reconcile_intent(intent, result)
    assert reconciled["series"] == ["own_value"]


def test_chart_reconciler_skips_peer_add_on_non_comparison_kinds() -> None:
    """Fix 14 only fires on cross-merchant kinds (cross_merchant_comparison,
    time_series_vs_peers, small_multiples). Other kinds (kpi_callout,
    table_drilldown, scatter_quadrant) don't get unprompted peer
    series — they have their own semantics."""
    from src.agents.chart_build import _reconcile_intent
    result = pd.DataFrame({
        "category": ["DAIRY"],
        "own_value": [3.50],
        "peer_benchmark": [1.00],
    })
    # kpi_callout — doesn't use 'series' field; reconciler shouldn't
    # add anything peer-related.
    intent = {
        "kind": "kpi_callout",
        "value": "own_value",
        "title": "x",
    }
    reconciled, _ = _reconcile_intent(intent, result)
    assert "series" not in reconciled
    # scatter_quadrant uses x + y, not series; same expectation.
    intent2 = {
        "kind": "scatter_quadrant",
        "x": "own_value",
        "y": "peer_benchmark",
    }
    reconciled2, _ = _reconcile_intent(intent2, result)
    assert "series" not in reconciled2


def test_chart_reconciler_peer_add_fires_on_time_series_vs_peers() -> None:
    """Fix 14: time_series_vs_peers is the canonical own-vs-peer
    time chart. Same auto-add behavior as cross_merchant_comparison."""
    from src.agents.chart_build import _reconcile_intent
    result = pd.DataFrame({
        "period_start": pd.to_datetime(["2026-04-06", "2026-04-13"]),
        "own_value": [1.04, 1.05],
        "peer_benchmark": [1.00, 1.00],
    })
    intent = {
        "kind": "time_series_vs_peers",
        "x": "period_start",
        "series": ["own_value"],
        "y_format": "index",
    }
    reconciled, notes = _reconcile_intent(intent, result)
    assert "peer_benchmark" in reconciled["series"]


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
        "headline": "Peer contactless share is 0.62.",
        "merge": {},
        "chart_intent": {"kind": "kpi_callout", "value": "contactless_share"},
        "claims": [],
    })
    assert out == {"ok": True}
