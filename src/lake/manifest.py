"""Wave 2 §5 grain manifest (D23.7).

A small machine-readable per-table specification of the lake's
shape — finest grain, dimensions, published metrics, AND what each
table does NOT carry. Wave 3 agents read it before composing a
plan so they know whether the question is answerable at the
available grain or whether they should decline gracefully.

Example consumer pattern (Wave 3):

    spec = manifest_for("lake_category_metrics")
    if "subcategory" not in spec["dimensions"]:
        return "Lake does not publish subcategory-level peer data."

The manifest reflects the post-scope agent surface — it lists
``peer_relationship`` where the underlying lake stores ``banner_code``,
because §5 ``scope.py`` strips identity before the table reaches the
agent.

D23.7: "publish a small machine-readable manifest per table — finest
grain, dimensions, and **what it does NOT carry** (e.g. 'no peer SKU',
'weekly not daily') — so Wave 3 agents know their limits and can
decline gracefully."
"""
from __future__ import annotations

from typing import Any


# Five lake tables — kept aligned with ``src.lake.build``.
_MANIFEST: dict[str, dict[str, Any]] = {
    "lake_category_metrics": {
        "finest_grain": "peer × category × subcategory × derived_zone × week",
        "dimensions": [
            "peer_relationship",   # segment_peer | cross_segment
            "category",
            "subcategory",          # NULL at cat_week / cat_month grains
            "derived_zone",
            "period_start",
            "grain",                # subcat_week | cat_week | cat_month
        ],
        "metrics": [
            "txn_count",
            "price_index",
            "revenue_index",
            "units_index",
            "basket_penetration_share",
            "promo_active_share",
            "wow_delta",
        ],
        "excludes": [
            "no peer SKU (own side reaches SKU via the tenant surface)",
            "no peer store_id",
            "no per-customer rows",
            "no daily grain (week is finest temporal)",
        ],
        "k_floor": 50,
        "ladder": "subcat_week → cat_week → cat_month → suppress",
    },
    "lake_payment_mix": {
        "finest_grain": "peer × derived_zone × month",
        "dimensions": [
            "peer_relationship",
            "derived_zone",
            "month_start",
        ],
        "metrics": [
            "txn_count",
            "contactless_share", "chip_share", "swipe_share", "manual_share",
            "credit_share", "debit_share",
            "visa_share", "mc_share", "amex_share", "discover_share",
            "wallet_share",
            "apple_share_within_wallet",
            "google_share_within_wallet",
            "samsung_share_within_wallet",
            "wifi_share", "ethernet_share", "cellular_share",
        ],
        "excludes": [
            "no per-customer rows",
            "no weekly grain (month is finest)",
            "no per-store breakdown",
        ],
        "k_floor": 50,
        "ladder": "single grain — ladder unused at full scale (~13.8k txn/cell)",
    },
    "lake_segment_mix": {
        "finest_grain": "peer × behavioral_segment × derived_zone",
        "dimensions": [
            "peer_relationship",
            "behavioral_segment",   # premium_loyalist | frequent_value | occasional_premium | occasional
            "derived_zone",
        ],
        "metrics": [
            "n_cards",
            "share_of_zone_at_banner",
            "median_basket",
            "median_freq",
            "txn_count",
        ],
        "excludes": [
            "no time grain (window-level only)",
            "no per-customer rows",
            "no peer SKU",
            "segments are DERIVED behaviorally — not the planted loyalty_type",
        ],
        "k_floor": 50,
        "ladder": "single grain",
    },
    "lake_trade_area": {
        "finest_grain": "peer × derived_zone × category",
        "dimensions": [
            "peer_relationship",
            "derived_zone",
            "category",
        ],
        "metrics": [
            "store_count",
            "cell_units",
            "cell_revenue",
            "share_of_zone",
            "zone_category_volume_index",
            "txn_count",
        ],
        "excludes": [
            "no time grain (window-level only)",
            "no peer subcategory",
            "no per-customer rows",
        ],
        "k_floor": 50,
        "ladder": "single grain — zone-level cells comfortably above k",
    },
    "lake_cross_merchant_cohorts": {
        # No peer_relationship: cohorts are aggregated across all banners
        # touched. Identity is stripped at the source by the cohort
        # builder.
        "finest_grain": "derived_zone × cohort_combination",
        "dimensions": [
            "derived_zone",
            "cohort_combination",   # grocery_only | grocery_qsr | all_three | ...
        ],
        "metrics": [
            "cohort_size",
            "median_combined_spend",
            "p25_combined_spend",
            "p75_combined_spend",
            "median_total_txns",
            "frequency_band",
            "txn_count",
        ],
        "excludes": [
            "NO raw mean spend (D24.2 concentration risk — median + IQR only)",
            "no per-customer rows",
            "no per-merchant breakdown of spend",
            "no time grain (window-level only)",
        ],
        "k_floor": 50,
        "ladder": "single grain — cohort cells clear k by ~10× per T17",
    },
}


# ----- Public accessors -------------------------------------------------

def list_tables() -> list[str]:
    """Names of all lake tables documented by the manifest."""
    return sorted(_MANIFEST.keys())


def manifest_for(table: str) -> dict[str, Any]:
    """Return the manifest spec for one table.

    Raises ``KeyError`` if the table isn't in the lake — agents
    catching this know to decline rather than guess.
    """
    if table not in _MANIFEST:
        raise KeyError(
            f"No manifest for {table!r}. Known lake tables: "
            f"{list_tables()}."
        )
    # Return a copy so callers can't mutate the source.
    spec = _MANIFEST[table]
    return {
        **spec,
        "dimensions": list(spec["dimensions"]),
        "metrics": list(spec["metrics"]),
        "excludes": list(spec["excludes"]),
    }


def full_manifest() -> dict[str, dict[str, Any]]:
    """Return manifests for every lake table. Used by the Wave 3
    agent system prompt to enumerate available surfaces."""
    return {t: manifest_for(t) for t in list_tables()}
