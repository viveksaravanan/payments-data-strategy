"""Stage 5 tests — grain manifest (SPEC §5, D23.7).

L11 acceptance:

* Every lake table has a manifest entry.
* Each entry declares ``finest_grain``, ``dimensions``, ``metrics``,
  ``excludes``, ``k_floor``, ``ladder``.
* The manifest reflects the post-scope agent surface
  (``peer_relationship`` where the raw lake stores ``banner_code``).
* The honest exclusions are present (e.g. "no peer SKU",
  "NO raw mean spend").
* ``manifest_for`` returns a defensive copy — callers can't mutate
  the source.
"""
from __future__ import annotations

import pytest

from src.lake.manifest import full_manifest, list_tables, manifest_for


EXPECTED_TABLES = {
    "lake_category_metrics",
    "lake_payment_mix",
    "lake_segment_mix",
    "lake_trade_area",
    "lake_cross_merchant_cohorts",
}

EXPECTED_KEYS = {
    "finest_grain", "dimensions", "metrics", "excludes",
    "k_floor", "ladder",
}


def test_list_tables_covers_all_five() -> None:
    assert set(list_tables()) == EXPECTED_TABLES


def test_every_table_has_required_keys() -> None:
    for name in list_tables():
        spec = manifest_for(name)
        missing = EXPECTED_KEYS - set(spec.keys())
        assert not missing, f"{name} missing manifest keys: {missing}"


def test_k_floor_is_50_everywhere() -> None:
    """L11 / L2: every published cell ≥ k=50 (D21 bar)."""
    for name in list_tables():
        spec = manifest_for(name)
        assert spec["k_floor"] == 50, (
            f"{name} declares k_floor={spec['k_floor']} (expected 50)"
        )


def test_banner_keyed_tables_publish_peer_relationship() -> None:
    """The manifest reflects the post-scope agent surface — not the
    raw lake. Banner-keyed tables show ``peer_relationship``, not
    ``banner_code``."""
    banner_keyed = {
        "lake_category_metrics",
        "lake_payment_mix",
        "lake_segment_mix",
        "lake_trade_area",
    }
    for name in banner_keyed:
        spec = manifest_for(name)
        dims = spec["dimensions"]
        assert "peer_relationship" in dims, (
            f"{name} should declare peer_relationship in dimensions"
        )
        assert "banner_code" not in dims, (
            f"{name} should NOT expose banner_code on the agent surface"
        )
        assert "merchant_id" not in dims, (
            f"{name} should NOT expose merchant_id on the agent surface"
        )


def test_cohorts_omit_peer_relationship() -> None:
    """The cohort table is aggregated across all banners by
    construction; ``peer_relationship`` doesn't apply."""
    spec = manifest_for("lake_cross_merchant_cohorts")
    assert "peer_relationship" not in spec["dimensions"]


def test_cohorts_declare_no_raw_mean_exclusion() -> None:
    """D24.2: cohort spend is median/IQR, never raw mean. The
    exclusion must be stated explicitly so Wave 3 agents don't
    request a mean."""
    spec = manifest_for("lake_cross_merchant_cohorts")
    joined = " ".join(spec["excludes"]).lower()
    assert "raw mean" in joined or "no mean" in joined, (
        f"cohort manifest must state the median-only rule; got "
        f"excludes={spec['excludes']}"
    )


def test_category_metrics_declares_subcat_finest_grain() -> None:
    spec = manifest_for("lake_category_metrics")
    assert "subcategory" in spec["dimensions"]
    assert "subcat_week" in spec["ladder"]


def test_category_metrics_declares_no_peer_sku() -> None:
    """SPEC §4.1 / D23.7: 'no peer SKU' is the textbook exclusion to
    declare."""
    spec = manifest_for("lake_category_metrics")
    joined = " ".join(spec["excludes"]).lower()
    assert "sku" in joined


def test_segment_mix_declares_behavioral_not_planted() -> None:
    """The L9 invariant — segments are derived, not planted. The
    manifest must say so to prevent Wave 3 from treating them as
    loyalty_type."""
    spec = manifest_for("lake_segment_mix")
    joined = " ".join(spec["excludes"]).lower()
    assert "derived" in joined or "behavior" in joined


def test_manifest_for_unknown_table_raises() -> None:
    with pytest.raises(KeyError):
        manifest_for("lake_does_not_exist")


def test_manifest_for_returns_defensive_copy() -> None:
    """Callers must not be able to mutate the source manifest."""
    spec = manifest_for("lake_category_metrics")
    spec["dimensions"].append("INJECTED")
    spec["metrics"].append("INJECTED")
    spec["excludes"].append("INJECTED")
    # Re-fetch — INJECTED should NOT be present.
    fresh = manifest_for("lake_category_metrics")
    assert "INJECTED" not in fresh["dimensions"]
    assert "INJECTED" not in fresh["metrics"]
    assert "INJECTED" not in fresh["excludes"]


def test_full_manifest_returns_all_tables() -> None:
    fm = full_manifest()
    assert set(fm.keys()) == EXPECTED_TABLES
    for spec in fm.values():
        assert EXPECTED_KEYS.issubset(set(spec.keys()))
