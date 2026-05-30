"""Layer 4 — Trips (D15 + D15b).

Two halves composed together:

* **4a temporal placement (D15)** — for each card's per-segment trip
  budget, place each trip into a (date, time-of-day) pair.
  - D15.2 day-of-week weights per segment (grocery weekend-skewed,
    QSR Fri-Sat-skewed, off-price Sat-Sun-skewed).
  - D15.3 pay-cycle overlay (early-month days 1-10 lift, mid-month
    15-17 lift).
  - D15.4 daypart curves — Taco Bell's late-night signature
    (9pm+ ≈ 19% per D15.4) is the hard signal T5 checks.
  - D15.5 cohort active windows: established → full 90 days;
    new_in_window → first appearance day-30 through day-75;
    lapsing → trips concentrated early, last appearance day-15
    through day-60.

* **4b store resolution (D15b)** —
  - Within a banner: gravity model ``P(s) ∝ A_s / (d + d₀)^β`` per
    D13.4. A_s is uniform within a segment (format attractiveness
    deferred to a config knob); β is segment-specific (grocery 2.0,
    qsr 2.2, off_price 1.3); d₀ = 0.5 from global config.
  - For grocery, banner choice composes loyalty × gravity:
    ``P(banner b) ∝ loyalty_weight(rank_b) × gravity_pull(b)``
    where rank_b is 1 (primary), 2 (secondary), 3 (third) per
    D16.1's loyalty weight tables. Secondary/third ordering is
    deterministic per card from the shared RNG iteration order.

Anomaly multiplier hook (D15.6): the date-sampling weights accept
a per-(zone × store × time-window × category) multiplier that
Stage 4.8 (D20.2) uses to plant A1-A3. Wired in build_trips's
signature as ``anomaly_hook`` (default None — no perturbation).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.generate.config.loader import Config
from src.generate.engine.geography import euclidean_degree_distance


# ----- D15.2 day-of-week weights ------------------------------------
# Python weekday(): Mon=0 ... Sun=6.

_DOW_WEIGHTS = {
    "grocery":   [1.00, 0.85, 0.90, 0.95, 1.05, 1.20, 1.25],  # Mon→Sun
    "qsr":       [0.90, 0.90, 0.95, 1.00, 1.25, 1.20, 1.05],
    "off_price": [0.90, 0.85, 0.85, 0.90, 1.10, 1.35, 1.20],
}


# ----- D15.3 pay-cycle overlay --------------------------------------

_PAY_CYCLE_EARLY_MONTH = set(range(1, 11))       # days 1-10
_PAY_CYCLE_MID_MONTH   = {15, 16, 17}            # mid-month bump
_PAY_CYCLE_EARLY_MULT  = 1.15                    # ~1.10-1.20 per D15.3
_PAY_CYCLE_MID_MULT    = 1.10
# Per-segment pay-cycle weight (1.0 = full effect; 0 = no effect).
# Grocery strongest, QSR modest, off-price ≈ 0.
_PAY_CYCLE_SEGMENT_GAIN = {
    "grocery":   1.0,
    "qsr":       0.4,
    "off_price": 0.1,
}


# ----- D15.4 daypart curves -----------------------------------------
# Per-segment hour-of-day weight vector (length 24, hour 0-23).
# Weekday vs weekend can vary per segment.

def _flat_hour_weights(daypart_shares: dict[range, float]) -> np.ndarray:
    """Convert a daypart spec (range_of_hours → share) into a 24-vector,
    distributing each daypart's share uniformly across its hours."""
    w = np.zeros(24, dtype=float)
    for hours, share in daypart_shares.items():
        per_hour = share / len(list(hours))
        for h in hours:
            w[h % 24] = per_hour
    return w / w.sum()


# Taco Bell daypart per D15.4 — the late-night signature.
_TBL_DAYPART = {
    range(6, 10):     0.07,  # breakfast
    range(10, 14):    0.31,  # lunch
    range(14, 17):    0.13,  # afternoon
    range(17, 21):    0.30,  # dinner
    range(21, 24):    0.12,  # late evening
    range(0, 3):      0.07,  # after midnight
}
_TBL_HOUR_WEIGHTS = _flat_hour_weights(_TBL_DAYPART)

# Grocery weekday: bimodal lunch + commute. Per D15.4.
_GROCERY_WEEKDAY_DAYPART = {
    range(7, 9):      0.06,
    range(9, 11):     0.10,
    range(11, 13):    0.18,  # lunch peak
    range(13, 15):    0.10,
    range(15, 17):    0.14,
    range(17, 20):    0.30,  # commute peak
    range(20, 23):    0.12,
}
_GROCERY_WEEKDAY_HOUR_WEIGHTS = _flat_hour_weights(_GROCERY_WEEKDAY_DAYPART)

# Grocery weekend: midday hump + 3-5pm bump.
_GROCERY_WEEKEND_DAYPART = {
    range(8, 10):     0.07,
    range(10, 12):    0.18,
    range(12, 15):    0.32,  # midday peak
    range(15, 17):    0.18,  # afternoon bump
    range(17, 20):    0.18,
    range(20, 22):    0.07,
}
_GROCERY_WEEKEND_HOUR_WEIGHTS = _flat_hour_weights(_GROCERY_WEEKEND_DAYPART)

# Off-price: midday/afternoon weighted, lighter weekdays.
_OFF_PRICE_DAYPART = {
    range(10, 12):    0.10,
    range(12, 14):    0.18,
    range(14, 17):    0.32,
    range(17, 19):    0.22,
    range(19, 21):    0.12,
    range(21, 22):    0.06,
}
_OFF_PRICE_HOUR_WEIGHTS = _flat_hour_weights(_OFF_PRICE_DAYPART)


# ----- D16.1 loyalty weights ----------------------------------------
# Per loyalty type: weight for primary / secondary / third banner.

_LOYALTY_BANNER_WEIGHTS = {
    "loyalist":     (0.88, 0.10, 0.02),
    "splitter":     (0.60, 0.38, 0.02),
    "three_chain":  (0.45, 0.32, 0.23),
    "lapsed_light": (0.70, 0.25, 0.05),
}


# ----- D15.5 cohort active windows ----------------------------------
# day offsets from window_start; for new_in_window, first appearance
# is drawn from [30, 75]; for lapsing, last appearance from [15, 60].
# These bounds match D15.5's "weighted toward later weeks" /
# "concentrated early" descriptions without locking in exact dates.

_COHORT_BOUNDS = {
    "established":   (0, 90),           # full window
    "new_in_window": (30, 75),          # first appearance day range
    "lapsing":       (15, 60),          # last appearance day range
}

_GROCER_BANNERS = ("ACM", "KRG", "WDX")


# ----- helpers ------------------------------------------------------

def _build_day_calendar(cfg: Config) -> pd.DataFrame:
    """One row per day in the window. Holds dayofweek and pay-cycle
    multipliers per segment."""
    start = pd.Timestamp(cfg.global_["window"]["start_date"])
    days = cfg.global_["window"]["days"]
    dates = pd.date_range(start, periods=days, freq="D")
    df = pd.DataFrame({
        "day_index": range(days),
        "date":      dates,
        "weekday":   dates.weekday,
        "dom":       dates.day,
    })
    return df


def _segment_day_weights(cfg: Config, calendar: pd.DataFrame, segment: str) -> np.ndarray:
    """Per-day weight for the segment: DOW × pay-cycle. Returns
    length-90 array, not normalized."""
    dow_w = np.array(_DOW_WEIGHTS[segment])[calendar["weekday"].to_numpy()]
    gain = _PAY_CYCLE_SEGMENT_GAIN[segment]
    pay_mult = np.ones(len(calendar), dtype=float)
    for i, dom in enumerate(calendar["dom"].to_numpy()):
        if dom in _PAY_CYCLE_EARLY_MONTH:
            pay_mult[i] = 1.0 + (_PAY_CYCLE_EARLY_MULT - 1.0) * gain
        elif dom in _PAY_CYCLE_MID_MONTH:
            pay_mult[i] = 1.0 + (_PAY_CYCLE_MID_MULT - 1.0) * gain
    return dow_w * pay_mult


def _cohort_window(rng: np.random.Generator, cohort: str, total_days: int) -> tuple[int, int]:
    """Per-card active window [start_day, end_day) given cohort tag."""
    if cohort == "established":
        return (0, total_days)
    if cohort == "new_in_window":
        lo, hi = _COHORT_BOUNDS["new_in_window"]
        first = int(rng.integers(lo, hi))
        return (first, total_days)
    if cohort == "lapsing":
        lo, hi = _COHORT_BOUNDS["lapsing"]
        last = int(rng.integers(lo, hi))
        return (0, last)
    raise ValueError(f"unknown cohort {cohort!r}")


def _hour_weights_for(segment: str, weekday: int) -> np.ndarray:
    if segment == "qsr":
        return _TBL_HOUR_WEIGHTS
    if segment == "off_price":
        return _OFF_PRICE_HOUR_WEIGHTS
    if segment == "grocery":
        return _GROCERY_WEEKEND_HOUR_WEIGHTS if weekday >= 5 else _GROCERY_WEEKDAY_HOUR_WEIGHTS
    raise ValueError(f"unknown segment {segment!r}")


def _segment_for_banner(banner: str) -> str:
    if banner in _GROCER_BANNERS:
        return "grocery"
    if banner == "TBL":
        return "qsr"
    if banner == "TJX":
        return "off_price"
    raise ValueError(f"unknown banner {banner!r}")


def _compute_store_gravity(
    cfg: Config,
    stores: pd.DataFrame,
    zones: pd.DataFrame,
    segment: str,
) -> pd.DataFrame:
    """For a segment, compute per-(zone, store) gravity weight
    ``A_s / (d + d₀)^β``. Returns long form: zone_id, store_id,
    banner_code, weight.
    """
    beta = cfg.segments[segment]["distance_decay"]["beta"]
    d0 = float(cfg.global_.get("distance_decay_d0", 0.5))

    seg_banners = {
        "grocery":   set(_GROCER_BANNERS),
        "qsr":       {"TBL"},
        "off_price": {"TJX"},
    }[segment]
    seg_stores = stores[stores["banner_code"].isin(seg_banners)].copy()

    # Cartesian product zones × stores. Small enough to be in memory:
    # 8 zones × ~5-29 stores per segment.
    z = zones[["zone_id", "centroid_lat", "centroid_long"]].copy()
    z["_key"] = 1
    s = seg_stores[["store_id", "banner_code", "latitude", "longitude"]].copy()
    s["_key"] = 1
    pairs = z.merge(s, on="_key").drop(columns="_key")
    d = euclidean_degree_distance(
        pairs["centroid_lat"], pairs["centroid_long"],
        pairs["latitude"],     pairs["longitude"],
    )
    pairs["distance"] = d
    pairs["weight"] = 1.0 / (d + d0) ** beta   # uniform A_s = 1
    return pairs[["zone_id", "store_id", "banner_code", "distance", "weight"]]


# ----- main builder -------------------------------------------------

def build_trips(
    cfg: Config,
    population: pd.DataFrame,
    customers: pd.DataFrame,
    stores: pd.DataFrame,
    zones: pd.DataFrame,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Place every trip in time and space.

    Returns a frame with one row per trip and columns
    ``[trip_id, card_id, segment, banner_code, store_id, txn_ts]``.
    Sort order is deterministic (by trip_id) so two runs at the same
    seed produce identical frames.
    """
    calendar = _build_day_calendar(cfg)
    total_days = len(calendar)
    window_start = calendar["date"].iloc[0]

    # Precompute per-segment day weights (length-90 arrays).
    seg_day_weights = {
        s: _segment_day_weights(cfg, calendar, s)
        for s in ("grocery", "qsr", "off_price")
    }

    # Precompute per-segment gravity tables.
    seg_gravity = {
        s: _compute_store_gravity(cfg, stores, zones, s)
        for s in ("grocery", "qsr", "off_price")
    }

    # Helpful indexed views.
    cust_idx = customers.set_index("card_id")
    pop_idx = population.set_index("card_id")
    # Per-segment, per-zone: list of (store_id, weight) for the banner's stores.
    # For grocery, we also need per-banner sums (for banner composition) and
    # within-banner per-store weights.
    grocery_by_zone_banner: dict[tuple[str, str], pd.DataFrame] = {}
    for (z_id, b_code), grp in seg_gravity["grocery"].groupby(["zone_id", "banner_code"]):
        grocery_by_zone_banner[(z_id, b_code)] = grp.reset_index(drop=True)
    grocery_banner_pull_by_zone: dict[str, dict[str, float]] = {}
    for z_id, grp in seg_gravity["grocery"].groupby("zone_id"):
        grocery_banner_pull_by_zone[z_id] = grp.groupby("banner_code")["weight"].sum().to_dict()

    qsr_by_zone: dict[str, pd.DataFrame] = {
        z: g.reset_index(drop=True)
        for z, g in seg_gravity["qsr"].groupby("zone_id")
    }
    off_price_by_zone: dict[str, pd.DataFrame] = {
        z: g.reset_index(drop=True)
        for z, g in seg_gravity["off_price"].groupby("zone_id")
    }

    # Iterate cards in sorted order.
    card_ids = sorted(population["card_id"].tolist())
    records: list[tuple] = []
    trip_counter = 0

    for card_id in card_ids:
        pop_row = pop_idx.loc[card_id]
        cust_row = cust_idx.loc[card_id]
        home_zone = cust_row["home_zone"]
        cohort = pop_row["cohort"]
        active_lo, active_hi = _cohort_window(rng, cohort, total_days)
        active_days = np.arange(active_lo, active_hi)
        if len(active_days) == 0:
            continue

        for segment in ("grocery", "qsr", "off_price"):
            budget = int(pop_row[f"trip_budget_{segment}"])
            if budget == 0:
                continue

            # 1) Day weights masked by cohort window.
            seg_w = seg_day_weights[segment][active_days]
            seg_w = seg_w / seg_w.sum()
            day_indices = rng.choice(active_days, size=budget, p=seg_w)

            # 2) Time-of-day per trip (hour, minute, second).
            weekdays = calendar["weekday"].to_numpy()[day_indices]
            hours = np.empty(budget, dtype=int)
            # Process weekday vs weekend separately for grocery.
            if segment == "grocery":
                wk_mask = weekdays >= 5
                if wk_mask.any():
                    hours[wk_mask] = rng.choice(
                        24, size=int(wk_mask.sum()),
                        p=_GROCERY_WEEKEND_HOUR_WEIGHTS,
                    )
                if (~wk_mask).any():
                    hours[~wk_mask] = rng.choice(
                        24, size=int((~wk_mask).sum()),
                        p=_GROCERY_WEEKDAY_HOUR_WEIGHTS,
                    )
            else:
                weights = _hour_weights_for(segment, 0)
                hours[:] = rng.choice(24, size=budget, p=weights)
            minutes = rng.integers(0, 60, size=budget)
            seconds = rng.integers(0, 60, size=budget)

            # 3) Banner + store resolution.
            if segment == "grocery":
                # Compose loyalty × gravity for banner.
                pull = grocery_banner_pull_by_zone.get(home_zone, {})
                primary = cust_row["primary_banner"]
                others = [b for b in _GROCER_BANNERS if b != primary]
                # Deterministic secondary/third ordering from rng.
                rng.shuffle(others)
                banner_order = [primary, others[0], others[1]]
                loyalty_w = np.array(
                    _LOYALTY_BANNER_WEIGHTS[cust_row["loyalty_type"]], dtype=float
                )
                gravity_pull = np.array([pull.get(b, 0.0) for b in banner_order], dtype=float)
                composed = loyalty_w * gravity_pull
                composed = composed / composed.sum() if composed.sum() > 0 else loyalty_w
                banner_picks = rng.choice(banner_order, size=budget, p=composed)

                # Within-banner store choice.
                store_picks = np.empty(budget, dtype=object)
                for b in _GROCER_BANNERS:
                    mask = banner_picks == b
                    if not mask.any():
                        continue
                    df_zb = grocery_by_zone_banner.get((home_zone, b))
                    if df_zb is None or len(df_zb) == 0:
                        # Fall back to any zone's gravity for this banner.
                        df_zb = seg_gravity["grocery"][seg_gravity["grocery"]["banner_code"] == b]
                    w = df_zb["weight"].to_numpy()
                    w = w / w.sum()
                    s_ids = df_zb["store_id"].to_numpy()
                    store_picks[mask] = rng.choice(s_ids, size=int(mask.sum()), p=w)
            else:
                # Single-banner segment. Pick store via gravity directly.
                lookup = qsr_by_zone if segment == "qsr" else off_price_by_zone
                df_z = lookup.get(home_zone)
                if df_z is None or len(df_z) == 0:
                    df_z = seg_gravity[segment]
                w = df_z["weight"].to_numpy()
                w = w / w.sum()
                s_ids = df_z["store_id"].to_numpy()
                store_picks = rng.choice(s_ids, size=budget, p=w)
                banner = "TBL" if segment == "qsr" else "TJX"
                banner_picks = np.full(budget, banner, dtype=object)

            # 4) Assemble.
            for i in range(budget):
                trip_counter += 1
                ts = (
                    window_start
                    + pd.Timedelta(days=int(day_indices[i]))
                    + pd.Timedelta(
                        hours=int(hours[i]),
                        minutes=int(minutes[i]),
                        seconds=int(seconds[i]),
                    )
                )
                records.append((
                    f"T-{trip_counter:08d}",
                    card_id,
                    segment,
                    str(banner_picks[i]),
                    str(store_picks[i]),
                    ts,
                ))

    df = pd.DataFrame(
        records,
        columns=["trip_id", "card_id", "segment", "banner_code", "store_id", "txn_ts"],
    )
    return df.sort_values("trip_id", kind="mergesort").reset_index(drop=True)
