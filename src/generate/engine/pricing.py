"""Layer 7 — Catalog & price (D19) — pricing side.

Anchor × strategy × zone × time × line noise. The SKU catalog
(structure + anchored base_price) is built in ``catalog.py`` (Stage
4.5). This module computes the **realized** per-line unit_price
from that anchor by walking the D19.1 modifier chain:

  unit_price = base_price
             × per_merchant_category_multiplier  (D19.2 lever 1)
             × private_label_factor              (D19.2 lever 2)
             × per_sku_competitive_index         (D19.2 lever 3)
             × zone_effect                       (D19.1 §3)
             × time_drift                        (D19.1 §5)
             × line_noise                        (D19.1 §6)

T14 in §6 acceptance:
- No single banner cheapest on >70% of comparable SKUs.
- Private-label gap ~25%+ at the population level.
- KVI cross-banner spread tight (<~10%).
- Specialty spread wider.

Promo state (D19.1 §4) is Stage 4.8 — promotions layer in on top of
the unit_price computed here.
"""
from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd

from src.generate.config.loader import Config


# ----- D19.2 lever 1 — per-merchant category multiplier ------------
# Each grocer is aggressive (cheaper) on some categories and
# monetizes (marks up) others, AND the cheapest banner varies by
# category. The pattern drives the "who's cheapest flips by SKU
# type" T14 requirement (no banner cheapest on >70%).
#
# Distribution of category wins (before KVI dampener / specialty
# amplifier + per-SKU competitive index):
#   WDX cheapest: DAIRY, BAKERY, FROZEN, SNACKS, BEVERAGES, HOUSEHOLD
#   KRG cheapest: MEAT, PANTRY, PERSONAL, BABY, PET
#   ACM cheapest: PRODUCE (organic edge, D19.3)
#
# Per-SKU competitive index (±5%) flips within-category orderings
# for individual SKUs, smearing the per-banner share further.

_PER_MERCHANT_CATEGORY_MULT: dict[str, dict[str, float]] = {
    "ACM": {  # premium
        "DAIRY": 1.08, "BAKERY": 1.06, "PRODUCE": 0.94,
        "MEAT": 1.05, "FROZEN": 1.05, "PANTRY": 1.08,
        "SNACKS": 1.08, "BEVERAGES": 1.08, "HOUSEHOLD": 1.05,
        "PERSONAL": 1.06, "BABY": 1.05, "PET": 1.07,
    },
    "KRG": {  # mainstream — cheap on meat + pantry national-brands
        "DAIRY": 1.02, "BAKERY": 1.00, "PRODUCE": 1.01,
        "MEAT": 0.96, "FROZEN": 1.00, "PANTRY": 0.96,
        "SNACKS": 1.00, "BEVERAGES": 0.98, "HOUSEHOLD": 1.00,
        "PERSONAL": 1.00, "BABY": 0.99, "PET": 1.00,
    },
    "WDX": {  # value — cheap on staples; at par on produce/personal/pet
        "DAIRY": 0.95, "BAKERY": 0.94, "PRODUCE": 1.02,
        "MEAT": 0.98, "FROZEN": 0.96, "PANTRY": 0.99,
        "SNACKS": 0.96, "BEVERAGES": 0.94, "HOUSEHOLD": 0.94,
        "PERSONAL": 1.02, "BABY": 1.00, "PET": 1.02,
    },
    # QSR: single banner, no competition, anchors are already menu prices.
    "TBL": {},
    # Off-price: D19.5 — "MSRP-anchor × deep discount (~20-60% below MSRP)".
    # Category anchors stored as MSRP-equivalent; multiplier here is the
    # off-price discount factor that produces the realized retail price.
    # Mean discount ~55%. Per-SKU competitive index (±5%) sits on top.
    "TJX": {
        "WOM": 0.45, "MEN": 0.45, "KID": 0.50,
        "HOM": 0.50, "ACC": 0.50, "SHO": 0.50,
        "BTY": 0.55,
    },
}


# ----- D19.2 category price-sensitivity per subcategory ------------
# KVI (known-value items): shoppers price-check these, so banner-to-
# banner spread is tight — the per-merchant category mult is
# dampened toward 1.0 by ~65%.
# SPECIALTY (premium / specialty / discretionary): per-merchant
# differentiation is amplified by ~50%, producing wider spread
# where the agent's "leverage" analysis lives.

_KVI_SUBCATEGORIES = {
    "MILK", "EGGS", "BREAD", "BUTTER", "FRUIT", "VEGETABLE",
    "CHIPS", "SODA",
}
_SPECIALTY_SUBCATEGORIES = {
    "DELI", "SEAFOOD", "PASTRIES", "FORMULA", "COFFEE",
    "NUTS", "FROZEN_PIZZA", "ICE_CREAM",
}


def _effective_category_mult(
    merchant: str, category: str, subcategory: str,
) -> float:
    """Per-merchant category mult, dampened for KVI / amplified for
    specialty subcategories per D19.2."""
    base = _PER_MERCHANT_CATEGORY_MULT.get(merchant, {}).get(category, 1.0)
    delta = base - 1.0
    if subcategory in _KVI_SUBCATEGORIES:
        return 1.0 + delta * 0.35   # tighten KVI spread
    if subcategory in _SPECIALTY_SUBCATEGORIES:
        return 1.0 + delta * 1.5    # widen specialty spread
    return base


# ----- D19.2 lever 2 — private-label factor per banner -------------
# PL discount is the biggest single lever. WDX leads (~35% off);
# KRG moderate (~25%); ACM modest (~15%) since premium PL is closer
# to national brand quality.

_PL_FACTOR: dict[str, float] = {
    "ACM": 0.85,
    "KRG": 0.75,
    "WDX": 0.65,
    "TBL": 1.00,
    "TJX": 1.00,
}


# ----- D19.2 lever 3 — per-SKU competitive index -------------------
# Deterministic ±5% adjustment per (merchant, canonical_id). Hashed
# so the same SKU at the same merchant always gets the same index,
# but different merchants get different indices for the same canonical
# (so gaps don't fully derive from category + tier).

def _per_sku_competitive_index(merchant: str, canonical_id: str) -> float:
    h = hashlib.sha256(f"compindex|{merchant}|{canonical_id}".encode()).digest()
    # Map first 4 bytes into [-1, 1] uniformly.
    raw = int.from_bytes(h[:4], "big") / 2**32
    return 1.0 + (raw - 0.5) * 0.10   # ±5%


# ----- Zone effect (D19.1 §3) --------------------------------------

def _zone_effect(store_zone_affluence: float) -> float:
    """Affluent stores +~2-4%; value stores ~-2%; mainstream ~0."""
    if store_zone_affluence >= 1.20:
        return 1.03
    if store_zone_affluence >= 1.05:
        return 1.015
    if store_zone_affluence <= 0.85:
        return 0.985
    return 1.00


# ----- Time drift (D19.1 §5) ---------------------------------------

def _time_drift(day_index: int, total_days: int) -> float:
    """Mild ~+1.5% drift from window start to end (grocery inflation
    ~2% YoY prorated). Linear ramp around midpoint."""
    if total_days <= 1:
        return 1.0
    t = day_index / (total_days - 1)
    return 1.0 + 0.015 * (t - 0.5)   # ranges from 0.9925 to 1.0075


# ----- Line noise (D19.1 §6) ---------------------------------------
# ±0.5% per line — kept tiny so it's not a primary driver.
_LINE_NOISE_MAGNITUDE = 0.005


# ----- main builder -------------------------------------------------

def build_priced_items(
    cfg: Config,
    basket_items: pd.DataFrame,
    catalog: pd.DataFrame,
    trips: pd.DataFrame,
    stores: pd.DataFrame,
    zones: pd.DataFrame,
    rng: np.random.Generator,
    *,
    promo_depth_lookup: dict | None = None,
    promo_id_lookup: dict | None = None,
) -> pd.DataFrame:
    """Compute unit_price, discount, line_total, and promo_id per
    basket item.

    Returns a frame with one row per basket line and the schema
    ``[trip_id, line_id, sku, canonical_id, category, subcategory,
    qty, unit_price, discount, promo_id, line_total]``.

    promo_depth_lookup and promo_id_lookup (Stage 4.8 — D20):
    ``{(date, sku) → depth_pct}`` and ``{(date, sku) → promo_id}``.
    When a line's (txn_date, sku) is in the lookup, unit_price is
    reduced by the depth, and promo_id is set on the line.
    """
    promo_depth_lookup = promo_depth_lookup or {}
    promo_id_lookup = promo_id_lookup or {}
    window_start = pd.Timestamp(cfg.global_["window"]["start_date"])
    total_days = int(cfg.global_["window"]["days"])

    # Catalog lookup table (small ~1100 grocery + 60 qsr + 270 op
    # × 5 merchants ≈ 5k rows). Index by sku for fast joins.
    cat = catalog[
        ["sku", "merchant_id", "category", "subcategory",
         "canonical_id", "private_label", "base_price"]
    ].rename(columns={"merchant_id": "banner_code"})

    # Pre-build store → zone affluence lookup.
    zone_aff = {z["id"]: float(z["affluence"]) for z in cfg.zones}
    store_aff = (
        stores[["store_id", "zone_id"]]
        .assign(store_aff=lambda d: d["zone_id"].map(zone_aff))
        [["store_id", "store_aff"]]
    )

    # Join basket items with catalog (banner-aware via SKU prefix).
    items = basket_items.merge(
        cat[["sku", "private_label", "base_price"]],
        on="sku", how="left", suffixes=("", "_cat"),
    )
    # Need banner_code + store_id + txn_ts per item; join from trips.
    items = items.merge(
        trips[["trip_id", "banner_code", "store_id", "txn_ts"]],
        on="trip_id", how="left",
    )
    items = items.merge(store_aff, on="store_id", how="left")

    # Vectorized modifier chain.
    base = items["base_price"].to_numpy()
    banner = items["banner_code"].to_numpy()
    category = items["category"].to_numpy()
    canonical = items["canonical_id"].to_numpy()
    pl = items["private_label"].to_numpy()
    store_aff_arr = items["store_aff"].to_numpy()
    txn_ts = pd.to_datetime(items["txn_ts"])
    day_idx = (txn_ts - window_start).dt.days.to_numpy()

    n = len(items)

    # 1) per-merchant × category mult (with KVI dampener / specialty amplifier)
    subcategory_arr = items["subcategory"].to_numpy()
    pm_cat_mult = np.array([
        _effective_category_mult(banner[i], category[i], subcategory_arr[i])
        for i in range(n)
    ], dtype=float)

    # 2) private-label factor
    pl_mult = np.where(
        pl,
        np.vectorize(_PL_FACTOR.get)(banner, 1.0),
        1.0,
    )

    # 3) per-SKU competitive index (deterministic, vectorized via list comp
    # since it's a hash per row; cost is small at ~700k items pilot, ~17M full).
    comp_idx = np.array([
        _per_sku_competitive_index(banner[i], canonical[i])
        for i in range(n)
    ], dtype=float)

    # 4) zone effect
    zone_mult = np.where(
        store_aff_arr >= 1.20, 1.03,
        np.where(
            store_aff_arr >= 1.05, 1.015,
            np.where(store_aff_arr <= 0.85, 0.985, 1.00),
        ),
    )

    # 5) time drift
    t = day_idx / max(1, total_days - 1)
    drift = 1.0 + 0.015 * (t - 0.5)

    # 6) line noise
    noise = 1.0 + rng.uniform(-_LINE_NOISE_MAGNITUDE, _LINE_NOISE_MAGNITUDE, size=n)

    pre_promo_unit_price = np.round(
        base * pm_cat_mult * pl_mult * comp_idx * zone_mult * drift * noise,
        2,
    )

    # ----- Promo discount + promo_id (Stage 4.8) -----------------
    discount = np.zeros(n, dtype=float)
    promo_id = np.empty(n, dtype=object)
    if promo_depth_lookup:
        trip_dates = txn_ts.dt.date.to_numpy()
        sku_arr = items["sku"].to_numpy()
        for i in range(n):
            key = (trip_dates[i], str(sku_arr[i]))
            depth = promo_depth_lookup.get(key)
            if depth is not None:
                discount[i] = round(pre_promo_unit_price[i] * depth, 2)
                promo_id[i] = promo_id_lookup.get(key)

    unit_price = np.round(pre_promo_unit_price - discount, 2)
    line_total = np.round(unit_price * items["qty"].to_numpy(), 2)

    out = items[
        ["trip_id", "line_id", "sku", "canonical_id", "category",
         "subcategory", "qty"]
    ].copy()
    out["unit_price"] = unit_price
    out["discount"] = discount
    out["promo_id"] = promo_id
    out["line_total"] = line_total
    return out.sort_values(["trip_id", "line_id"], kind="mergesort").reset_index(drop=True)
