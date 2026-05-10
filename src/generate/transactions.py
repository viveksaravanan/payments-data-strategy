"""Unified Layer 4 transaction generator.

Replaces the v2 ``base.py`` per-merchant loop with a customer-centric
flow. The generator runs once over the 10,000-customer panel and emits
all transactions for all five merchants. Cross-merchant coupling
(grocer choice for splitters / three-chain shoppers) is naturally
expressed at the customer level, which the per-merchant model couldn't
do cleanly.

The entry point is ``generate_all(...)``. It returns combined
transactions and line-item DataFrames that can be sorted by
``txn_id`` and written directly to ``data/raw/``.

Implementation maps to ``V2_5_DATA_DESIGN.md`` Layer 4 Steps 4a–4l:

  - Step 4a: trip frequency — ``_sample_trip_count_grocery``,
    ``_sample_trip_count_qsr``, ``_sample_trip_count_retail``.
  - Step 4b: active-weeks variance — ``_distribute_trip_dates``.
  - Step 4d/4e: per-trip merchant choice — ``_pick_grocery_chain``.
  - Step 4f: within-chain store choice — ``_pick_store``.
  - Step 4g: per-trip time — day weights + hour sampling.
  - Step 4h: terminal assignment — per-store terminal pool.
  - Step 4i: basket archetype — ``_sample_archetype``.
  - Step 4j: basket size — ``_sample_basket_size``.
  - Step 4k: per-line generation — ``_generate_line_items`` (qty per
    category, 85% promo apply, no unit_price noise, tax computed).
  - Step 4l: payment generation — ``_sample_payment``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

import numpy as np
import pandas as pd

from . import parameters as P, promotions as promo_module, tax as tax_module
from .anomalies import (
    avocado_multiplier,
    pasta_multiplier,
    uc_trip_keep_probability,
)

# Affinity rule type: takes a basket (list of SKUs) and an rng, returns
# a possibly-extended basket.
AffinityFn = Callable[[list[str], np.random.Generator], list[str]]


@dataclass
class MerchantData:
    """Bundle of static per-merchant inputs to the generator."""
    merchant_id: str
    segment: str                # 'grocery' | 'qsr' | 'off_price_retail'
    catalog: pd.DataFrame       # tenant_products shape
    stores: pd.DataFrame        # tenant_stores shape
    promotions: pd.DataFrame    # tenant_promotions shape
    affinity_fn: AffinityFn     # closure that adds companion SKUs to a basket


# ---------------------------------------------------------------------------
# Step 4a — trip frequency
# ---------------------------------------------------------------------------

def _sample_in_range(
    rng: np.random.Generator, lo: int, hi: int
) -> int:
    """Sample an integer in [lo, hi] from a triangular distribution with
    mode at lo. The design's "Sample within range" leaves the shape
    unspecified; mode-at-lo skews toward the lower end of the range,
    which lands the panel volume inside the Step 6 target of
    180k–250k transactions. Uniform sampling overshoots ~5%.
    """
    if hi <= lo:
        return lo
    return max(lo, min(hi, int(round(rng.triangular(lo, lo, hi)))))


def _sample_trip_count_grocery(
    rng: np.random.Generator,
    behavioral_segment: str,
    affinity: str,
) -> int:
    """Total grocery trips over the 90-day window for one customer."""
    if affinity == "lapsed_light":
        cum = 0.0
        u = rng.random()
        for prob, (lo, hi) in P.LAPSED_TRIP_BUCKETS:
            cum += prob
            if u < cum:
                return _sample_in_range(rng, lo, hi)
        return 0
    lo, hi = P.TRIP_FREQUENCY_GROCERY[(behavioral_segment, affinity)]
    return _sample_in_range(rng, lo, hi)


def _sample_trip_count_bucketed(
    rng: np.random.Generator, buckets: list[tuple[float, tuple[int, int]]]
) -> int:
    """Generic helper for QSR / retail two-bucket distributions."""
    cum = 0.0
    u = rng.random()
    for prob, (lo, hi) in buckets:
        cum += prob
        if u < cum:
            return _sample_in_range(rng, lo, hi)
    lo, hi = buckets[-1][1]
    return _sample_in_range(rng, lo, hi)


# ---------------------------------------------------------------------------
# Step 4b — active-weeks variance
# ---------------------------------------------------------------------------

def _distribute_trip_dates(
    rng: np.random.Generator,
    n_trips: int,
    earliest_offset: int,   # earliest valid day-offset for this customer (signup-respecting)
    days: int,
    pay_cycle_mult: np.ndarray,  # per-day calendar multiplier
    week_starts: np.ndarray,     # day-offsets where each week begins
) -> np.ndarray:
    """Distribute `n_trips` over the window per Step 4b.

    1. Sample number of active weeks per ``ACTIVE_WEEKS_DIST``.
    2. Pick which weeks are active.
    3. Assign each trip to an active week via Dirichlet weights (gives
       natural per-week burstiness).
    4. Within each chosen week, pick a specific day weighted by the
       calendar multiplier (pay-cycle bumps, etc.) and respecting the
       earliest-allowed-offset for this customer.
    """
    if n_trips <= 0:
        return np.empty(0, dtype=int)

    # Step 4b.1: active weeks count.
    keys = np.asarray(list(P.ACTIVE_WEEKS_DIST.keys()))
    probs = np.asarray(list(P.ACTIVE_WEEKS_DIST.values()), dtype=float)
    n_active = int(rng.choice(keys, p=probs))
    # Cannot have more active weeks than trips (or weeks in the window).
    n_active = min(n_active, n_trips, P.TOTAL_WEEKS)

    # Step 4b.2: which weeks are active.
    active_idx = np.sort(rng.choice(P.TOTAL_WEEKS, size=n_active, replace=False))

    # Step 4b.3: trip-to-week assignment. Each active week gets at least
    # 1 trip (so the customer is genuinely "active" in those weeks per
    # the design's intent). Remaining trips distributed via multinomial
    # over Dirichlet weights for natural per-week burstiness.
    base = np.ones(n_active, dtype=int)
    remaining = n_trips - n_active
    if remaining > 0:
        week_w = rng.dirichlet([2.0] * n_active)
        extra = rng.multinomial(remaining, week_w)
        per_week = base + extra
    else:
        per_week = base

    # Step 4b.4: pick a specific day within each chosen week, weighted by
    # the calendar multiplier and respecting earliest_offset.
    out = np.empty(n_trips, dtype=int)
    write = 0
    for w_pos, count in enumerate(per_week):
        if count == 0:
            continue
        week_start = int(week_starts[active_idx[w_pos]])
        candidates = np.arange(week_start, min(week_start + 7, days))
        candidates = candidates[candidates >= earliest_offset]
        if len(candidates) == 0:
            # Fall back to any valid day in the window.
            candidates = np.arange(max(earliest_offset, 0), days)
            if len(candidates) == 0:
                out[write:write + count] = max(earliest_offset, 0)
                write += count
                continue
        weights = pay_cycle_mult[candidates]
        weights = weights / weights.sum()
        sampled = rng.choice(candidates, size=count, p=weights)
        out[write:write + count] = sampled
        write += count
    return out[:write]


# ---------------------------------------------------------------------------
# Step 4d / 4e — per-trip merchant choice
# ---------------------------------------------------------------------------

def _grocery_choice_distribution(
    affinity: str, primary: str, secondary: str | None, day_of_week: int
) -> tuple[list[str], list[float]]:
    """Return (chain_list, prob_list) for sampling the grocer for one
    grocery trip. `chain_list` always covers all three grocers — the
    'second'/'third' positions for loyalist / three-chain are filled
    deterministically alphabetically among non-primary."""
    grocers = ["KRG", "ACM", "WDX"]
    others = sorted(g for g in grocers if g != primary)

    if affinity == "loyalist":
        return [primary, others[0], others[1]], [
            P.LOYALIST_CHAIN_CHOICE["primary"],
            P.LOYALIST_CHAIN_CHOICE["second"],
            P.LOYALIST_CHAIN_CHOICE["third"],
        ]
    if affinity == "splitter":
        sec = secondary or others[0]
        third = next(g for g in grocers if g not in {primary, sec})
        # Step 4d: bias by day-of-week.
        if day_of_week in (5, 6):  # Sat, Sun
            mix = P.SPLITTER_CHAIN_WEEKEND
            return [primary, sec, third], [
                mix["primary"], mix["secondary"], 0.0,
            ]
        if day_of_week in (1, 2, 3):  # Tue, Wed, Thu
            mix = P.SPLITTER_CHAIN_WEEKDAY
            return [primary, sec, third], [
                mix["primary"], mix["secondary"], 0.0,
            ]
        mix = P.SPLITTER_CHAIN_DEFAULT
        return [primary, sec, third], [
            mix["primary"], mix["secondary"], mix["third"],
        ]
    if affinity == "three_chain":
        sec = secondary or others[0]
        third = next(g for g in grocers if g not in {primary, sec})
        return [primary, sec, third], [
            P.THREE_CHAIN_CHAIN_CHOICE["primary"],
            P.THREE_CHAIN_CHAIN_CHOICE["second"],
            P.THREE_CHAIN_CHAIN_CHOICE["third"],
        ]
    if affinity == "lapsed_light":
        # Scattered roughly proportional to store-count; primary slightly
        # favored because it's their nominal home chain.
        sec = others[0]
        third = others[1]
        return [primary, sec, third], [0.50, 0.30, 0.20]
    raise ValueError(f"Unknown affinity {affinity!r}")


# ---------------------------------------------------------------------------
# Step 4f — within-chain store choice
# ---------------------------------------------------------------------------

def _pick_store_index(rng: np.random.Generator, n_stores: int) -> int:
    """Closest / second-closest / other per Step 4f. Phase 4 stub: store
    'closeness' is a per-customer fixed shuffle (not actual distance);
    Phase 5+ could add real distance ranking."""
    if n_stores <= 1:
        return 0
    if n_stores == 2:
        # Collapse the second-closest and "other" probabilities onto the
        # only available second store.
        u = rng.random()
        return 0 if u < 0.70 else 1
    p_close, p_second, _p_other = P.STORE_CHOICE_PROBS
    u = rng.random()
    if u < p_close:
        return 0
    if u < p_close + p_second:
        return 1
    return int(rng.integers(2, n_stores))


# ---------------------------------------------------------------------------
# Step 4i / 4j — basket archetype + size
# ---------------------------------------------------------------------------

def _sample_archetype(rng: np.random.Generator) -> str:
    keys = list(P.ARCHETYPE_SHARES.keys())
    probs = list(P.ARCHETYPE_SHARES.values())
    return str(rng.choice(keys, p=probs))


def _sample_basket_size(
    rng: np.random.Generator,
    segment: str,
    behavioral_segment: str,
    archetype: str | None,
) -> int:
    """Triangular sample within (lo, mean, hi) for the appropriate cell."""
    if segment == "qsr":
        lo, mid, hi = P.BASKET_SIZE_QSR
    elif segment == "off_price_retail":
        lo, mid, hi = P.BASKET_SIZE_RETAIL
    else:  # grocery
        lo, mid, hi = P.BASKET_SIZE_GROCERY[(behavioral_segment, archetype or "fill_in")]
    raw = rng.triangular(lo, mid, hi)
    return max(1, int(round(raw)))


# ---------------------------------------------------------------------------
# Step 4k — per-line generation helpers
# ---------------------------------------------------------------------------

def _build_category_pools(catalog: pd.DataFrame) -> dict[str, np.ndarray]:
    """category → array of row indices into the catalog."""
    cat_arr = catalog["category"].to_numpy()
    return {
        c: np.where(cat_arr == c)[0]
        for c in np.unique(cat_arr)
    }


def _build_pasta_mask(catalog: pd.DataFrame) -> np.ndarray:
    """Per-catalog-row boolean: True where ``subcategory == 'pasta'``."""
    return (catalog["subcategory"] == "pasta").to_numpy()


def _build_avocado_mask(catalog: pd.DataFrame) -> np.ndarray:
    """Per-catalog-row boolean: True where the canonical product name
    contains ``"avocado"`` (case-insensitive). Used by the Plaza Midwood
    avocado spike to bias PRODUCE SKU selection."""
    return catalog["name"].str.contains(
        "avocado", case=False, na=False, regex=False
    ).to_numpy()


def _sample_sku_index_in_pool(
    rng: np.random.Generator,
    pool: np.ndarray,
    category: str,
    pasta_mask: np.ndarray,
    avocado_mask: np.ndarray,
    pasta_mult: float,
    avocado_mult: float,
) -> int:
    """Pick a catalog row index from `pool` (the rows belonging to
    `category`). Defaults to uniform; if either the pasta or avocado
    multiplier is non-1.0 AND the relevant subset has rows in this
    pool, sampling becomes weight-multiplicative on that subset.
    """
    n = pool.shape[0]
    apply_pasta = pasta_mult != 1.0 and category == "PANTRY"
    apply_avo = avocado_mult != 1.0 and category == "PRODUCE"
    if not (apply_pasta or apply_avo):
        return int(pool[int(rng.integers(0, n))])

    weights = np.ones(n, dtype=float)
    if apply_pasta:
        # Build per-pool boolean, then scale.
        pool_pasta = pasta_mask[pool]
        if pool_pasta.any():
            weights[pool_pasta] *= pasta_mult
    if apply_avo:
        pool_avo = avocado_mask[pool]
        if pool_avo.any():
            weights[pool_avo] *= avocado_mult
    total = weights.sum()
    if total <= 0:
        return int(pool[int(rng.integers(0, n))])
    probs = weights / total
    pos = int(rng.choice(n, p=probs))
    return int(pool[pos])


def _sample_qty(rng: np.random.Generator, category: str) -> int:
    dist = P.QTY_DISTRIBUTION.get(category)
    if dist is None:
        return 1
    keys = list(dist.keys())
    probs = list(dist.values())
    return int(rng.choice(keys, p=probs))


def _sample_category_for_grocery(
    rng: np.random.Generator,
    archetype: str,
    available_cats: list[str],
) -> str:
    weights = P.ARCHETYPE_CATEGORY_WEIGHTS[archetype]
    keys: list[str] = []
    probs: list[float] = []
    for c in available_cats:
        w = weights.get(c, 0.0)
        if w > 0:
            keys.append(c)
            probs.append(w)
    if not keys:  # extreme fallback
        return available_cats[0]
    arr = np.asarray(probs, dtype=float)
    arr = arr / arr.sum()
    return str(rng.choice(keys, p=arr))


# ---------------------------------------------------------------------------
# Step 4l — payment generation
# ---------------------------------------------------------------------------

def _sample_payment_type(rng: np.random.Generator, payment_mix: dict[str, float]) -> str:
    keys = list(payment_mix.keys())
    probs = list(payment_mix.values())
    return str(rng.choice(keys, p=probs))


def _sample_card_network(rng: np.random.Generator, payment_type: str) -> str:
    table = P.CREDIT_NETWORKS if payment_type == "credit" else P.DEBIT_NETWORKS
    keys = list(table.keys())
    probs = list(table.values())
    return str(rng.choice(keys, p=probs))


def _sample_entry_mode(rng: np.random.Generator, segment: str) -> str:
    table = P.ENTRY_MODE_BY_SEGMENT[segment]
    keys = list(table.keys())
    probs = list(table.values())
    return str(rng.choice(keys, p=probs))


def _sample_wallet(
    rng: np.random.Generator, entry_mode: str, has_mobile_wallet: int
) -> str | None:
    if entry_mode != "contactless" or has_mobile_wallet != 1:
        return None
    if rng.random() >= P.WALLET_USE_PROB:
        return None
    keys = list(P.WALLET_PROVIDER_DIST.keys())
    probs = list(P.WALLET_PROVIDER_DIST.values())
    return str(rng.choice(keys, p=probs))


def _sample_connectivity(rng: np.random.Generator) -> str:
    keys = list(P.CONNECTIVITY_DISTRIBUTION.keys())
    probs = list(P.CONNECTIVITY_DISTRIBUTION.values())
    return str(rng.choice(keys, p=probs))


# ---------------------------------------------------------------------------
# Time-of-day sampling (Step 4g)
# ---------------------------------------------------------------------------

def _hour_weights(segment: str) -> np.ndarray:
    """Per-segment hour-of-day weights — peaks per Step 4g."""
    w = np.full(24, 0.5)
    w[0:6] = 0.05
    w[6:10] = 0.4
    w[10:22] = 1.0
    w[22:24] = 0.3
    for h in P.PEAK_HOURS_BY_SEGMENT[segment]:
        w[h] = 3.0
    return w / w.sum()


# ---------------------------------------------------------------------------
# Per-store terminal pools (Step 4h)
# ---------------------------------------------------------------------------

def _build_terminal_pools(
    stores: pd.DataFrame, rng: np.random.Generator
) -> dict[str, list[str]]:
    """For each store, sample a terminal-count in [4, 8] and return the
    fixed list of terminal_ids (`<store_id>-T01..-TN`)."""
    lo, hi = P.TERMINALS_PER_STORE_RANGE
    out: dict[str, list[str]] = {}
    for store_id in stores["store_id"]:
        n = int(rng.integers(lo, hi + 1))
        out[str(store_id)] = [f"{store_id}-T{i + 1:02d}" for i in range(n)]
    return out


# ---------------------------------------------------------------------------
# Calendar multiplier (Step 4g calendar effects + Step 4b pay-cycle bumps)
# ---------------------------------------------------------------------------

def _build_calendar_multiplier(segment: str) -> np.ndarray:
    """Per-day multiplier: peak-day bonus + pay-cycle bump."""
    peak = set(P.PEAK_DAYS_BY_SEGMENT[segment])
    out = np.empty(P.DAYS, dtype=float)
    for d in range(P.DAYS):
        dt = P.START_DATE + timedelta(days=d)
        m = P.PEAK_DAY_MULT if dt.weekday() in peak else P.NONPEAK_DAY_MULT
        if dt.day in P.PAY_CYCLE_DAYS:
            m *= P.PAY_CYCLE_MULTIPLIER
        out[d] = m
    return out


def _week_starts() -> np.ndarray:
    """Day-offsets of each of the 13 week starts. Weeks tile from day 0;
    the last week may be short if days % 7 != 0 (it doesn't here:
    90 / 7 = 12 remainder 6, so weeks 0–12 cover 91 days; we clip)."""
    return np.arange(P.TOTAL_WEEKS) * 7


# ---------------------------------------------------------------------------
# Promo lookup (Step 4k step 6)
# ---------------------------------------------------------------------------

def _build_promo_lookup_per_merchant(
    merchant_data: dict[str, MerchantData],
) -> dict[str, dict[int, dict[str, tuple[str, float]]]]:
    """{merchant_id → {day_offset → {sku → (promo_id, discount_pct)}}}."""
    out: dict[str, dict[int, dict[str, tuple[str, float]]]] = {}
    for key, md in merchant_data.items():
        out[md.merchant_id] = promo_module.build_promo_lookup(
            md.promotions, P.DAYS, P.START_DATE
        )
    return out


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def generate_all(
    customers: pd.DataFrame,
    merchant_data: dict[str, MerchantData],
    rng: np.random.Generator,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Generate transactions and line items for all merchants per Layer 4.

    Returns (transactions_df, items_df). DataFrames are concatenations
    across merchants but otherwise match the per-merchant shapes from
    Phase 3.
    """
    # Reference data per merchant.
    by_merchant_id = {md.merchant_id: md for md in merchant_data.values()}
    grocer_ids = [k for k, md in by_merchant_id.items() if md.segment == "grocery"]
    qsr_id = next(
        (k for k, md in by_merchant_id.items() if md.segment == "qsr"), None
    )
    retail_id = next(
        (k for k, md in by_merchant_id.items()
         if md.segment == "off_price_retail"),
        None,
    )

    # Per-merchant pre-computed structures.
    cat_pools = {mid: _build_category_pools(md.catalog) for mid, md in by_merchant_id.items()}
    sku_arrays = {mid: md.catalog["sku"].to_numpy() for mid, md in by_merchant_id.items()}
    sku_to_price = {
        mid: dict(zip(md.catalog["sku"].tolist(), md.catalog["base_price"].tolist()))
        for mid, md in by_merchant_id.items()
    }
    sku_to_category = {
        mid: dict(zip(md.catalog["sku"].tolist(), md.catalog["category"].tolist()))
        for mid, md in by_merchant_id.items()
    }
    store_pools = {
        mid: _build_terminal_pools(md.stores, rng)
        for mid, md in by_merchant_id.items()
    }

    # Anomaly SKU masks: per-merchant boolean arrays aligned with the
    # catalog row order (matches `cat_pools` index references). Used by
    # `_generate_line_items` to apply per-trip multiplicative bias on
    # avocado / pasta SKU selection inside PRODUCE / PANTRY.
    pasta_masks = {mid: _build_pasta_mask(md.catalog) for mid, md in by_merchant_id.items()}
    avocado_masks = {mid: _build_avocado_mask(md.catalog) for mid, md in by_merchant_id.items()}

    # Per-store neighborhood (used by anomaly hooks to scope the
    # University City decline and Plaza Midwood avocado spike).
    store_neighborhood = {
        store_id: neighborhood
        for md in by_merchant_id.values()
        for store_id, neighborhood
        in zip(md.stores["store_id"].astype(str).tolist(),
               md.stores["neighborhood"].tolist())
    }
    # Per-customer, per-grocer fixed store ranking. The "closest" is the
    # first store in the shuffle. Phase 4 stub: random shuffle. Phase 5+
    # can replace with actual distance ranking from home_zip5 to store
    # lat/long.
    rng_shuffle = np.random.default_rng(int(rng.integers(0, 2**31 - 1)))
    customer_store_rankings: dict[tuple[str, str], np.ndarray] = {}
    for mid, md in by_merchant_id.items():
        store_ids = md.stores["store_id"].to_numpy()
        for cust_id in customers["customer_id"]:
            ranking = rng_shuffle.permutation(store_ids)
            customer_store_rankings[(str(cust_id), mid)] = ranking

    promo_lookup = _build_promo_lookup_per_merchant(by_merchant_id)
    cal_mult = {seg: _build_calendar_multiplier(seg) for seg in {
        "grocery", "qsr", "off_price_retail",
    }}
    hour_w = {seg: _hour_weights(seg) for seg in {"grocery", "qsr", "off_price_retail"}}
    week_starts = _week_starts()

    # Containers for output rows.
    txn_rows: list[dict] = []
    item_rows: list[dict] = []
    counters: dict[str, int] = {mid: 0 for mid in by_merchant_id}

    # Iterate per customer. For each customer:
    #  1. sample grocery trip count and dates (active-weeks variance)
    #  2. for each grocery trip, pick chain → store → time → archetype →
    #     basket → items → payment
    #  3. sample QSR trip count and dates; same treatment
    #  4. sample retail trip count and dates; same treatment
    customers_arr = customers.reset_index(drop=True)
    for ci, c in customers_arr.iterrows():
        customer_id = str(c["customer_id"])
        affinity = str(c["grocer_affinity_type"])
        primary = str(c["primary_grocer"])
        secondary = (
            str(c["secondary_grocer"])
            if pd.notna(c["secondary_grocer"]) else None
        )
        segment_b = str(c["behavioral_segment"])
        has_wallet = int(c["has_mobile_wallet"])
        signup = c["signup_date"]
        signup_date = (
            date.fromisoformat(str(signup)) if signup else P.START_DATE
        )
        earliest = max(0, (signup_date - P.START_DATE).days)

        # ---- Grocery trips ----
        n_grocery = _sample_trip_count_grocery(rng, segment_b, affinity)
        grocery_dates = _distribute_trip_dates(
            rng, n_grocery, earliest, P.DAYS,
            cal_mult["grocery"], week_starts,
        )
        for day_offset in grocery_dates:
            chains, probs = _grocery_choice_distribution(
                affinity, primary, secondary,
                (P.START_DATE + timedelta(days=int(day_offset))).weekday(),
            )
            chosen = str(rng.choice(chains, p=probs))
            md = by_merchant_id[chosen]
            _emit_trip(
                rng=rng,
                day_offset=int(day_offset),
                merchant=md,
                customer_id=customer_id,
                customer_segment=segment_b,
                has_wallet=has_wallet,
                customer_store_ranking=customer_store_rankings[(customer_id, chosen)],
                terminal_pools=store_pools[chosen],
                store_neighborhood=store_neighborhood,
                hour_w=hour_w[md.segment],
                cat_pools=cat_pools[chosen],
                sku_arr=sku_arrays[chosen],
                sku_to_price=sku_to_price[chosen],
                sku_to_category=sku_to_category[chosen],
                pasta_mask=pasta_masks[chosen],
                avocado_mask=avocado_masks[chosen],
                affinity_fn=md.affinity_fn,
                promo_lookup_for_day=promo_lookup[chosen].get(int(day_offset), {}),
                txn_rows=txn_rows,
                item_rows=item_rows,
                counters=counters,
                archetype=_sample_archetype(rng),
            )

        # ---- QSR trips ----
        if qsr_id is not None:
            n_qsr = _sample_trip_count_bucketed(rng, P.QSR_TRIP_BUCKETS)
            qsr_dates = _distribute_trip_dates(
                rng, n_qsr, earliest, P.DAYS,
                cal_mult["qsr"], week_starts,
            )
            md = by_merchant_id[qsr_id]
            for day_offset in qsr_dates:
                _emit_trip(
                    rng=rng,
                    day_offset=int(day_offset),
                    merchant=md,
                    customer_id=customer_id,
                    customer_segment=segment_b,
                    has_wallet=has_wallet,
                    customer_store_ranking=customer_store_rankings[(customer_id, qsr_id)],
                    terminal_pools=store_pools[qsr_id],
                    store_neighborhood=store_neighborhood,
                    hour_w=hour_w[md.segment],
                    cat_pools=cat_pools[qsr_id],
                    sku_arr=sku_arrays[qsr_id],
                    sku_to_price=sku_to_price[qsr_id],
                    sku_to_category=sku_to_category[qsr_id],
                    pasta_mask=pasta_masks[qsr_id],
                    avocado_mask=avocado_masks[qsr_id],
                    affinity_fn=md.affinity_fn,
                    promo_lookup_for_day=promo_lookup[qsr_id].get(int(day_offset), {}),
                    txn_rows=txn_rows,
                    item_rows=item_rows,
                    counters=counters,
                    archetype=None,
                )

        # ---- Retail trips ----
        if retail_id is not None:
            n_ret = _sample_trip_count_bucketed(rng, P.RETAIL_TRIP_BUCKETS)
            retail_dates = _distribute_trip_dates(
                rng, n_ret, earliest, P.DAYS,
                cal_mult["off_price_retail"], week_starts,
            )
            md = by_merchant_id[retail_id]
            for day_offset in retail_dates:
                _emit_trip(
                    rng=rng,
                    day_offset=int(day_offset),
                    merchant=md,
                    customer_id=customer_id,
                    customer_segment=segment_b,
                    has_wallet=has_wallet,
                    customer_store_ranking=customer_store_rankings[(customer_id, retail_id)],
                    terminal_pools=store_pools[retail_id],
                    store_neighborhood=store_neighborhood,
                    hour_w=hour_w[md.segment],
                    cat_pools=cat_pools[retail_id],
                    sku_arr=sku_arrays[retail_id],
                    sku_to_price=sku_to_price[retail_id],
                    sku_to_category=sku_to_category[retail_id],
                    pasta_mask=pasta_masks[retail_id],
                    avocado_mask=avocado_masks[retail_id],
                    affinity_fn=md.affinity_fn,
                    promo_lookup_for_day=promo_lookup[retail_id].get(int(day_offset), {}),
                    txn_rows=txn_rows,
                    item_rows=item_rows,
                    counters=counters,
                    archetype=None,
                )

    transactions_df = pd.DataFrame(txn_rows)
    items_df = pd.DataFrame(item_rows)
    return transactions_df, items_df


# ---------------------------------------------------------------------------
# Per-trip emission
# ---------------------------------------------------------------------------

def _emit_trip(
    *,
    rng: np.random.Generator,
    day_offset: int,
    merchant: MerchantData,
    customer_id: str,
    customer_segment: str,
    has_wallet: int,
    customer_store_ranking: np.ndarray,
    terminal_pools: dict[str, list[str]],
    store_neighborhood: dict[str, str],
    hour_w: np.ndarray,
    cat_pools: dict[str, np.ndarray],
    sku_arr: np.ndarray,
    sku_to_price: dict[str, float],
    sku_to_category: dict[str, str],
    pasta_mask: np.ndarray,
    avocado_mask: np.ndarray,
    affinity_fn: AffinityFn,
    promo_lookup_for_day: dict[str, tuple[str, float]],
    txn_rows: list[dict],
    item_rows: list[dict],
    counters: dict[str, int],
    archetype: str | None,
) -> None:
    """Emit a single transaction + its line items into the output buffers."""

    # ---- Time ----
    hour = int(rng.choice(24, p=hour_w))
    minute = int(rng.integers(0, 60))
    second = int(rng.integers(0, 60))
    txn_date = P.START_DATE + timedelta(days=day_offset)
    txn_ts = f"{txn_date.isoformat()} {hour:02d}:{minute:02d}:{second:02d}"

    # ---- Store + terminal ----
    store_idx = _pick_store_index(rng, len(customer_store_ranking))
    store_id = str(customer_store_ranking[store_idx])
    neighborhood = store_neighborhood.get(store_id, "")

    # ---- Anomaly: University City decline (drop the trip if the
    # store/date/merchant is in the affected window). ----
    keep_prob = uc_trip_keep_probability(
        merchant.merchant_id, neighborhood, txn_date
    )
    if keep_prob < 1.0 and rng.random() >= keep_prob:
        return

    terminals = terminal_pools[store_id]
    terminal_id = str(rng.choice(terminals))

    # ---- Basket size ----
    basket_size = _sample_basket_size(
        rng, merchant.segment, customer_segment, archetype
    )

    # ---- Anomaly: per-trip pasta / avocado SKU multipliers ----
    pasta_mult = pasta_multiplier(merchant.merchant_id, txn_date)
    avo_mult = avocado_multiplier(
        merchant.merchant_id, neighborhood, txn_date
    )

    # ---- Sample line items ----
    items = _generate_line_items(
        rng=rng,
        merchant_id=merchant.merchant_id,
        segment=merchant.segment,
        archetype=archetype,
        basket_size=basket_size,
        cat_pools=cat_pools,
        sku_arr=sku_arr,
        sku_to_price=sku_to_price,
        sku_to_category=sku_to_category,
        pasta_mask=pasta_mask,
        avocado_mask=avocado_mask,
        pasta_mult=pasta_mult,
        avocado_mult=avo_mult,
        affinity_fn=affinity_fn,
        promo_lookup_for_day=promo_lookup_for_day,
    )
    if not items:
        return

    # ---- Payment ----
    payment_type = _sample_payment_type(rng, _payment_mix(merchant.merchant_id))
    card_network = _sample_card_network(rng, payment_type)
    entry_mode = _sample_entry_mode(rng, merchant.segment)
    wallet_type = _sample_wallet(rng, entry_mode, has_wallet)
    connectivity = _sample_connectivity(rng)

    # ---- Build txn_id ----
    counters[merchant.merchant_id] = counters.get(merchant.merchant_id, 0) + 1
    txn_id = f"{merchant.merchant_id}-{counters[merchant.merchant_id] - 1:07d}"

    # ---- Aggregate rollups ----
    subtotal = round(sum(it["line_total"] for it in items), 2)
    tax_total = round(sum(it["tax"] for it in items), 2)
    txn_total = round(subtotal + tax_total, 2)

    txn_rows.append({
        "txn_id":            txn_id,
        "merchant_id":       merchant.merchant_id,
        "customer_id":       customer_id,
        "store_id":          store_id,
        "terminal_id":       terminal_id,
        "txn_ts":            txn_ts,
        "payment_type":      payment_type,
        "card_network":      card_network,
        "entry_mode":        entry_mode,
        "wallet_type":       wallet_type,
        "connectivity_type": connectivity,
        "subtotal":          subtotal,
        "tax_total":         tax_total,
        "txn_total":         txn_total,
    })
    for line_id, it in enumerate(items, start=1):
        item_rows.append({
            "txn_id":     txn_id,
            "line_id":    line_id,
            "sku":        it["sku"],
            "qty":        it["qty"],
            "unit_price": it["unit_price"],
            "discount":   it["discount"],
            "tax":        it["tax"],
            "line_total": it["line_total"],
            "promo_id":   it["promo_id"],
        })


# ---------------------------------------------------------------------------
# Per-line item sampling — Step 4k
# ---------------------------------------------------------------------------

def _payment_mix(merchant_id: str) -> dict[str, float]:
    for cfg in P.MERCHANT_CONFIGS.values():
        if cfg["merchant_id"] == merchant_id:
            return cfg["payment_mix"]
    raise KeyError(merchant_id)


def _generate_line_items(
    *,
    rng: np.random.Generator,
    merchant_id: str,
    segment: str,
    archetype: str | None,
    basket_size: int,
    cat_pools: dict[str, np.ndarray],
    sku_arr: np.ndarray,
    sku_to_price: dict[str, float],
    sku_to_category: dict[str, str],
    pasta_mask: np.ndarray,
    avocado_mask: np.ndarray,
    pasta_mult: float,
    avocado_mult: float,
    affinity_fn: AffinityFn,
    promo_lookup_for_day: dict[str, tuple[str, float]],
) -> list[dict]:
    """Build the line-item list for one transaction per Step 4k."""

    # Step 4k.1: sample categories, then SKU within category.
    available_cats = list(cat_pools.keys())
    chosen_skus: list[str] = []
    chosen_set: set[str] = set()  # avoid duplicate SKU within basket (Step 4k.3)
    attempts = 0
    while len(chosen_skus) < basket_size and attempts < basket_size * 4:
        attempts += 1
        if segment == "grocery" and archetype is not None:
            cat = _sample_category_for_grocery(rng, archetype, available_cats)
        else:
            # Uniform over categories present in the catalog.
            cat = str(rng.choice(available_cats))
        pool = cat_pools[cat]
        sku_idx = _sample_sku_index_in_pool(
            rng, pool, cat, pasta_mask, avocado_mask, pasta_mult, avocado_mult,
        )
        sku = str(sku_arr[sku_idx])
        if sku in chosen_set:
            continue
        chosen_skus.append(sku)
        chosen_set.add(sku)

    # Step 4k.2 surface: apply affinity rules.
    chosen_skus = affinity_fn(chosen_skus, rng)

    # Step 4k.4–4k.8: per-line qty / promo / line_total / tax.
    out: list[dict] = []
    for sku in chosen_skus:
        category = sku_to_category[sku]
        qty = _sample_qty(rng, category)
        unit_price = float(sku_to_price[sku])  # Step 4k.5: no noise in v2.5

        promo_id: str | None = None
        discount = 0.0
        if sku in promo_lookup_for_day:
            cand_promo_id, cand_discount_pct = promo_lookup_for_day[sku]
            if rng.random() < P.DISCOUNT_APPLY_PROB:
                promo_id = cand_promo_id
                discount = round(unit_price * qty * cand_discount_pct, 2)

        line_total = round(unit_price * qty - discount, 2)
        tax = round(line_total * tax_module.tax_rate(category), 2)
        out.append({
            "sku":        sku,
            "qty":        qty,
            "unit_price": round(unit_price, 2),
            "discount":   discount,
            "tax":        tax,
            "line_total": line_total,
            "promo_id":   promo_id,
        })
    return out
