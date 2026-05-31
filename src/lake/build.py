"""Wave 2 §4 lake table builder (D23.3).

Five pre-aggregated tables, each vetted to k≥50 transactions per
published cell, with enrichment computed at build time:

* ``lake_category_metrics`` — merchant × category (× subcategory where
  ≥k) × zone × period. Pricing, demand, anomaly baselining.
* ``lake_payment_mix`` — merchant × payment-attr × zone (× month).
* ``lake_segment_mix`` — merchant × behavioral-segment × zone.
  Behavioral segments derived from observable transaction patterns.
* ``lake_trade_area`` — zone × category (× merchant).
* ``lake_cross_merchant_cohorts`` — zone × merchant-combination. The
  one table built inside the trusted boundary for cross-merchant
  token linkage; publishes only aggregated counts and **median/banded
  spend (not raw mean — D24.2 concentration risk)**.

All input reads go through ``observable_guard.load_table`` (§1
invariant). ``peer_relationship`` is NOT stored — resolved at query
time per viewer in §5 ``scope.py``.

The **k-guard ladder** (D21.4):

* ``lake_category_metrics`` may fire the ladder at its finest
  subcategory × zone × week grain. Order: subcategory → category;
  then week → month; else suppress. Wave 1's T17 full-scale measured
  ample cells, so most rows land at the finest grain.
* Cohort/zone tables clear k≥50 by 10× at full scale per T17, so
  the ladder is a safety net there.

The build is **trusted code** — it reads all merchants' transactions
to compute peer aggregates. The tenant-isolation guards (§2) apply
to Wave 3 agent queries, not to this builder. The builder is
constrained only by the §1 observable-data invariant (which it
satisfies by routing every read through ``observable_guard``) and the
§2 ``assert_lake_source_paths`` rule (no reads from ``data/eval/``).
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from src.lake.observable_guard import load_table
from src.lake.zones import derive_zone_for_store

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_LAKE = REPO_ROOT / "data" / "lake"

# k-anonymity threshold (D21 / D21.4 — strategy-doc §8 figure).
K_MIN = 50


# =========================================================================
# Shared joins: stores → zones + week/month period derivation
# =========================================================================

def _load_stores_with_zone() -> pd.DataFrame:
    """Load stores via observable_guard and attach derived zones.
    Returns DataFrame with [store_id, banner_code, derived_zone]."""
    stores = load_table(
        "stores",
        columns=["store_id", "banner_code", "latitude", "longitude"],
    )
    zones = derive_zone_for_store(
        stores[["store_id", "latitude", "longitude"]]
    )
    return stores.merge(zones, on="store_id")[
        ["store_id", "banner_code", "derived_zone"]
    ]


def _week_start(ts: pd.Series) -> pd.Series:
    """Bucket a timestamp series into week start (Sunday). Returns a
    pd.Series of python ``date`` objects."""
    return (
        pd.to_datetime(ts)
        .dt.to_period("W-SAT")    # week ending Saturday = starting Sunday
        .apply(lambda p: p.start_time.date())
    )


def _month_start(ts: pd.Series) -> pd.Series:
    return (
        pd.to_datetime(ts)
        .dt.to_period("M")
        .apply(lambda p: p.start_time.date())
    )


# =========================================================================
# 4.1 lake_category_metrics
# =========================================================================

def build_lake_category_metrics() -> pd.DataFrame:
    """The workhorse table. Per-cell enrichments:

    * ``price_index`` = cell mean unit_price ÷ metro-wide mean for
      same (category, subcategory if present, period).
    * ``revenue_index`` = cell revenue ÷ metro mean revenue, same grain.
    * ``units_index`` = cell units ÷ metro mean units.
    * ``basket_penetration_share`` = share of cell-merchant
      transactions in the cell's zone/period that contain the
      category (lifts above 1 = over-indexed category for that
      merchant in that zone).
    * ``promo_active_share`` = share of cell units on promo
      (``promo_id`` not null).
    * ``wow_delta`` = (units this period / units prior period) − 1
      at the same finest grain; ``NaN`` for first period.
    * ``txn_count`` = distinct transactions in the cell (k guard).

    Per-row ``grain`` indicates the lattice level after k-laddering:
    ``"subcat_week"`` (finest), ``"cat_week"``, ``"cat_month"``.
    Cells with txn_count < ``K_MIN`` even at the coarsest grain are
    suppressed (not emitted).
    """
    stores_with_zone = _load_stores_with_zone()
    txns = load_table(
        "transactions",
        columns=["txn_id", "store_id", "banner_code", "txn_ts"],
    )
    items = load_table(
        "transaction_items",
        columns=["txn_id", "category", "subcategory", "qty",
                 "unit_price", "line_total", "promo_id"],
    )

    txns["week_start"] = _week_start(txns["txn_ts"])
    txns["month_start"] = _month_start(txns["txn_ts"])
    txns = txns.merge(
        stores_with_zone[["store_id", "derived_zone"]],
        on="store_id",
    )

    items = items.merge(
        txns[["txn_id", "banner_code", "derived_zone",
              "week_start", "month_start"]],
        on="txn_id",
    )
    items["on_promo"] = items["promo_id"].notna()

    # Total txn count per (banner, zone, period) for the basket-
    # penetration denominator.
    txn_denom_week = (
        txns.groupby(["banner_code", "derived_zone", "week_start"])
        .agg(zone_txn_count_week=("txn_id", "nunique"))
        .reset_index()
    )
    txn_denom_month = (
        txns.groupby(["banner_code", "derived_zone", "month_start"])
        .agg(zone_txn_count_month=("txn_id", "nunique"))
        .reset_index()
    )

    # ---- Finest grain: subcategory × zone × week --------------------
    by_subcat_week = (
        items.groupby([
            "banner_code", "category", "subcategory",
            "derived_zone", "week_start",
        ])
        .agg(
            txn_count=("txn_id", "nunique"),
            units=("qty", "sum"),
            revenue=("line_total", "sum"),
            sum_unit_price_x_qty=("line_total", "sum"),  # = unit_price * qty - discount; close approx
            sum_qty=("qty", "sum"),
            promo_units=("qty", lambda s: s[items.loc[s.index, "on_promo"]].sum()),
        )
        .reset_index()
    )
    by_subcat_week["avg_unit_price"] = (
        by_subcat_week["revenue"] / by_subcat_week["units"]
    )

    # Aggregate up: category × zone × week (drops subcategory).
    by_cat_week = (
        items.groupby([
            "banner_code", "category", "derived_zone", "week_start",
        ])
        .agg(
            txn_count=("txn_id", "nunique"),
            units=("qty", "sum"),
            revenue=("line_total", "sum"),
            promo_units=("qty", lambda s: s[items.loc[s.index, "on_promo"]].sum()),
        )
        .reset_index()
    )
    by_cat_week["avg_unit_price"] = by_cat_week["revenue"] / by_cat_week["units"]
    by_cat_week["subcategory"] = None

    # Aggregate further: category × zone × month.
    by_cat_month = (
        items.groupby([
            "banner_code", "category", "derived_zone", "month_start",
        ])
        .agg(
            txn_count=("txn_id", "nunique"),
            units=("qty", "sum"),
            revenue=("line_total", "sum"),
            promo_units=("qty", lambda s: s[items.loc[s.index, "on_promo"]].sum()),
        )
        .reset_index()
    )
    by_cat_month["avg_unit_price"] = by_cat_month["revenue"] / by_cat_month["units"]
    by_cat_month["subcategory"] = None

    # ---- k-ladder: emit subcat_week where k≥50, else cat_week, else cat_month --
    keep_subcat = by_subcat_week[by_subcat_week["txn_count"] >= K_MIN].copy()
    keep_subcat["grain"] = "subcat_week"
    keep_subcat["period_start"] = keep_subcat["week_start"]

    # cat_week: only for (banner, category, zone, week) combos where the
    # subcategory-level rows didn't already capture enough. Simpler &
    # correct: emit cat_week for every cell ≥ K_MIN; the agent reads
    # whichever grain meets the question. (Both layers are emitted —
    # the agent can choose subcat where present, cat otherwise.)
    keep_cat_week = by_cat_week[by_cat_week["txn_count"] >= K_MIN].copy()
    keep_cat_week["grain"] = "cat_week"
    keep_cat_week["period_start"] = keep_cat_week["week_start"]

    # cat_month: fallback for cells that don't clear k at weekly grain.
    # Emit only the (banner, cat, zone, month) combos that are NOT
    # covered by any weekly row ≥ K_MIN.
    covered_at_week = set(
        zip(
            keep_cat_week["banner_code"],
            keep_cat_week["category"],
            keep_cat_week["derived_zone"],
            pd.to_datetime(keep_cat_week["week_start"]).dt.to_period("M")
            .apply(lambda p: p.start_time.date()),
        )
    )
    by_cat_month_key = list(zip(
        by_cat_month["banner_code"],
        by_cat_month["category"],
        by_cat_month["derived_zone"],
        by_cat_month["month_start"],
    ))
    mask_cover = pd.Series(
        [k in covered_at_week for k in by_cat_month_key],
        index=by_cat_month.index,
    )
    keep_cat_month = by_cat_month[
        (by_cat_month["txn_count"] >= K_MIN) & (~mask_cover)
    ].copy()
    keep_cat_month["grain"] = "cat_month"
    keep_cat_month["period_start"] = keep_cat_month["month_start"]

    # Combine.
    cols = [
        "banner_code", "category", "subcategory", "derived_zone",
        "period_start", "grain", "txn_count", "units", "revenue",
        "avg_unit_price", "promo_units",
    ]
    out = pd.concat(
        [keep_subcat[cols], keep_cat_week[cols], keep_cat_month[cols]],
        ignore_index=True,
    )

    # ---- Enrichment: indices, shares, deltas ---------------------------
    # Metro mean per (category, subcategory/None, period_start, grain) —
    # all merchants pooled.
    metro = (
        out.groupby(["category", "subcategory", "period_start", "grain"],
                    dropna=False)
        .agg(
            metro_avg_unit_price=("avg_unit_price", "mean"),
            metro_revenue=("revenue", "mean"),
            metro_units=("units", "mean"),
        )
        .reset_index()
    )
    out = out.merge(
        metro, on=["category", "subcategory", "period_start", "grain"], how="left",
    )
    out["price_index"] = out["avg_unit_price"] / out["metro_avg_unit_price"]
    out["revenue_index"] = out["revenue"] / out["metro_revenue"]
    out["units_index"] = out["units"] / out["metro_units"]
    out["promo_active_share"] = out["promo_units"] / out["units"]

    # Basket penetration: cell txn_count / total banner-zone-period txns
    # at the matching grain.
    out_week_mask = out["grain"].isin(("subcat_week", "cat_week"))
    out_month_mask = out["grain"] == "cat_month"
    out_week = out.loc[out_week_mask].copy()
    out_month = out.loc[out_month_mask].copy()
    out_week = out_week.merge(
        txn_denom_week.rename(columns={"week_start": "period_start"}),
        on=["banner_code", "derived_zone", "period_start"],
        how="left",
    )
    out_month = out_month.merge(
        txn_denom_month.rename(columns={"month_start": "period_start"}),
        on=["banner_code", "derived_zone", "period_start"],
        how="left",
    )
    out_week["basket_penetration_share"] = (
        out_week["txn_count"] / out_week["zone_txn_count_week"]
    )
    out_month["basket_penetration_share"] = (
        out_month["txn_count"] / out_month["zone_txn_count_month"]
    )
    out = pd.concat(
        [
            out_week.drop(columns=["zone_txn_count_week"]),
            out_month.drop(columns=["zone_txn_count_month"]),
        ],
        ignore_index=True,
    )

    # week-over-week delta at week grain only.
    out["wow_delta"] = np.nan
    week_rows = out["grain"].isin(("subcat_week", "cat_week"))
    sub = out.loc[week_rows].sort_values(
        ["banner_code", "category", "subcategory", "derived_zone", "period_start"]
    )
    grouped = sub.groupby(
        ["banner_code", "category", "subcategory", "derived_zone"],
        dropna=False,
    )
    prev_units = grouped["units"].shift(1)
    wow = (sub["units"] - prev_units) / prev_units
    out.loc[week_rows, "wow_delta"] = wow.values

    # Final column set.
    final_cols = [
        "banner_code", "category", "subcategory", "derived_zone",
        "period_start", "grain", "txn_count",
        "price_index", "revenue_index", "units_index",
        "basket_penetration_share", "promo_active_share", "wow_delta",
    ]
    return out[final_cols].sort_values(
        ["banner_code", "category", "subcategory", "derived_zone",
         "period_start", "grain"],
        kind="mergesort",
    ).reset_index(drop=True)


# =========================================================================
# Placeholder stubs for §§4.2-4.5 — filled in subsequent sub-stage commits
# =========================================================================

def build_lake_payment_mix() -> pd.DataFrame:
    """Stage 4.2 — implemented in the next sub-stage commit."""
    raise NotImplementedError("Stage 4.2 — pending sub-stage commit")


def build_lake_segment_mix() -> pd.DataFrame:
    """Stage 4.3 — implemented in the next sub-stage commit."""
    raise NotImplementedError("Stage 4.3 — pending sub-stage commit")


def build_lake_trade_area() -> pd.DataFrame:
    """Stage 4.4 — implemented in the next sub-stage commit."""
    raise NotImplementedError("Stage 4.4 — pending sub-stage commit")


def build_lake_cross_merchant_cohorts() -> pd.DataFrame:
    """Stage 4.5 — implemented in the next sub-stage commit."""
    raise NotImplementedError("Stage 4.5 — pending sub-stage commit")
