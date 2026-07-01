"""Layer 4 — Trips (D15 + D15b / datamodel-v2 §C3-§C4).

Two halves composed together:

* **4a temporal placement** — for each card's per-segment trip budget,
  place each trip into a (date, time-of-day) pair.
  - Day-of-week weights per segment (grocery weekend-skewed, QSR
    Fri-Sat-skewed).
  - Pay-cycle overlay (early-month + mid-month lifts).
  - **Seasonality (§C3, new):** a per-day trip-rate multiplier — gentle
    spring drift across the window + concentrated Easter / Memorial Day
    / Cinco / Mother's Day week bumps, read from the global config.
  - **Per-banner dayparts (§B2/§C3):** grocery weekday/weekend curves;
    QSR is now per BANNER — Taco Bell late-night (~18% post-9pm),
    Burger King breakfast-heavy, Chick-fil-A lunch/dinner with **no
    late-night and a hard-zero on Sundays** (closed).
  - Cohort active windows (established / new_in_window / lapsing).

* **4b store resolution (D15b + §C4)** —
  - Gravity ``P(s) ∝ A_s / (d + d₀)^β`` with **A_s from merchant config
    (non-uniform)** — brand pull + assortment breadth. β per segment
    (grocery 2.0, qsr 2.2); d₀ from global config.
  - Banner choice composes brand affinity × gravity for BOTH segments:
    grocery uses ``primary_banner`` × loyalty, QSR uses ``qsr_primary``
    × loyalty (same loyalty-weight table). Within a banner, store choice
    is gravity-weighted.

Off-price was dropped in datamodel-v2. Banner→segment membership is
derived from the merchant config (no hardcoded tuples).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.generate.config.loader import Config
from src.generate.engine.geography import euclidean_degree_distance


# ----- Day-of-week weights ------------------------------------------
# Python weekday(): Mon=0 ... Sun=6.
_DOW_WEIGHTS = {
    "grocery": [1.00, 0.85, 0.90, 0.95, 1.05, 1.20, 1.25],  # weekend skew
    "qsr":     [0.90, 0.90, 0.95, 1.00, 1.25, 1.20, 1.05],  # Fri-Sat peak
}


# ----- Pay-cycle overlay --------------------------------------------
_PAY_CYCLE_EARLY_MONTH = set(range(1, 11))       # days 1-10
_PAY_CYCLE_MID_MONTH   = {15, 16, 17}            # mid-month bump
_PAY_CYCLE_EARLY_MULT  = 1.15
_PAY_CYCLE_MID_MULT    = 1.10
_PAY_CYCLE_SEGMENT_GAIN = {"grocery": 1.0, "qsr": 0.4}


# ----- Dayparts (per-segment / per-banner) --------------------------
# 24-vector of hour-of-day weights, from a daypart spec.

def _flat_hour_weights(daypart_shares: dict[range, float]) -> np.ndarray:
    w = np.zeros(24, dtype=float)
    for hours, share in daypart_shares.items():
        per_hour = share / len(list(hours))
        for h in hours:
            w[h % 24] = per_hour
    return w / w.sum()


# Grocery weekday: bimodal lunch + commute.
_GROCERY_WEEKDAY_HOUR_WEIGHTS = _flat_hour_weights({
    range(7, 9): 0.06, range(9, 11): 0.10, range(11, 13): 0.18,
    range(13, 15): 0.10, range(15, 17): 0.14, range(17, 20): 0.30,
    range(20, 23): 0.12,
})
# Grocery weekend: midday hump.
_GROCERY_WEEKEND_HOUR_WEIGHTS = _flat_hour_weights({
    range(8, 10): 0.07, range(10, 12): 0.18, range(12, 15): 0.32,
    range(15, 17): 0.18, range(17, 20): 0.18, range(20, 22): 0.07,
})

# Taco Bell — late-night signature (~19% post-9pm: 0.12 + 0.07).
_TBL_HOUR_WEIGHTS = _flat_hour_weights({
    range(6, 10): 0.07, range(10, 14): 0.31, range(14, 17): 0.13,
    range(17, 21): 0.30, range(21, 24): 0.12, range(0, 3): 0.07,
})
# Burger King — breakfast daypart present (~20% in 6-10am).
_BKG_HOUR_WEIGHTS = _flat_hour_weights({
    range(6, 10): 0.20, range(10, 14): 0.30, range(14, 17): 0.12,
    range(17, 21): 0.28, range(21, 24): 0.10,
})
# Chick-fil-A — lunch/dinner heavy, modest breakfast, NO late night.
_CFA_HOUR_WEIGHTS = _flat_hour_weights({
    range(6, 10): 0.15, range(10, 14): 0.40, range(14, 17): 0.15,
    range(17, 21): 0.30,
})
_QSR_BANNER_HOUR_WEIGHTS = {
    "TBL": _TBL_HOUR_WEIGHTS,
    "BKG": _BKG_HOUR_WEIGHTS,
    "CFA": _CFA_HOUR_WEIGHTS,
}


# ----- Loyalty weights (primary / secondary / third banner) ---------
_LOYALTY_BANNER_WEIGHTS = {
    "loyalist":     (0.88, 0.10, 0.02),
    "splitter":     (0.60, 0.38, 0.02),
    "three_chain":  (0.45, 0.32, 0.23),
    "lapsed_light": (0.70, 0.25, 0.05),
}


# ----- Cohort active windows ----------------------------------------
_COHORT_BOUNDS = {
    "established":   (0, 90),
    "new_in_window": (30, 75),
    "lapsing":       (15, 60),
}


# ----- helpers ------------------------------------------------------

def _segment_banners(cfg: Config) -> dict[str, list[str]]:
    """Banner codes per segment, derived from config (sorted)."""
    out: dict[str, list[str]] = {}
    for m in cfg.merchants.values():
        out.setdefault(m["segment"], []).append(m["banner_code"])
    return {seg: sorted(codes) for seg, codes in out.items()}


def _attractiveness_by_banner(cfg: Config) -> dict[str, float]:
    return {m["banner_code"]: float(m["attractiveness"]) for m in cfg.merchants.values()}


def _build_day_calendar(cfg: Config) -> pd.DataFrame:
    start = pd.Timestamp(cfg.global_["window"]["start_date"])
    days = cfg.global_["window"]["days"]
    dates = pd.date_range(start, periods=days, freq="D")
    return pd.DataFrame({
        "day_index": range(days),
        "date":      dates,
        "weekday":   dates.weekday,
        "dom":       dates.day,
    })


def _seasonality_day_multiplier(cfg: Config, calendar: pd.DataFrame) -> np.ndarray:
    """Per-day trip-rate multiplier (§C3): linear spring drift across
    the window + concentrated event-week bumps. Redistributes a card's
    fixed budget toward event weeks -> visible daily spikes without a
    hardcoded topline change."""
    seas = cfg.global_.get("seasonality") or {}
    days = len(calendar)
    mult = np.ones(days, dtype=float)
    # Spring drift: linear ramp 1.0 -> 1.0 + drift.
    drift = float(seas.get("spring_drift_pct", 0)) / 100.0
    if drift:
        mult *= 1.0 + drift * (np.arange(days) / max(1, days - 1))
    # Event-week trip-rate bumps.
    dates = calendar["date"].dt.date.to_numpy()
    for ev in seas.get("events", []):
        start = pd.Timestamp(ev["start_date"]).date()
        end = pd.Timestamp(ev["end_date"]).date()
        tm = float(ev.get("trip_mult", 1.0))
        in_ev = np.array([start <= d <= end for d in dates])
        mult[in_ev] *= tm
    return mult


def _segment_day_weights(
    cfg: Config, calendar: pd.DataFrame, segment: str, seasonality: np.ndarray,
) -> np.ndarray:
    """Per-day weight: DOW × pay-cycle × seasonality (length-90)."""
    dow_w = np.array(_DOW_WEIGHTS[segment])[calendar["weekday"].to_numpy()]
    gain = _PAY_CYCLE_SEGMENT_GAIN[segment]
    pay_mult = np.ones(len(calendar), dtype=float)
    for i, dom in enumerate(calendar["dom"].to_numpy()):
        if dom in _PAY_CYCLE_EARLY_MONTH:
            pay_mult[i] = 1.0 + (_PAY_CYCLE_EARLY_MULT - 1.0) * gain
        elif dom in _PAY_CYCLE_MID_MONTH:
            pay_mult[i] = 1.0 + (_PAY_CYCLE_MID_MULT - 1.0) * gain
    return dow_w * pay_mult * seasonality


def _cohort_window(rng: np.random.Generator, cohort: str, total_days: int) -> tuple[int, int]:
    if cohort == "established":
        return (0, total_days)
    if cohort == "new_in_window":
        lo, hi = _COHORT_BOUNDS["new_in_window"]
        return (int(rng.integers(lo, hi)), total_days)
    if cohort == "lapsing":
        lo, hi = _COHORT_BOUNDS["lapsing"]
        return (0, int(rng.integers(lo, hi)))
    raise ValueError(f"unknown cohort {cohort!r}")


def _compute_store_gravity(
    cfg: Config,
    stores: pd.DataFrame,
    zones: pd.DataFrame,
    segment: str,
    seg_banners: dict[str, list[str]],
    a_s: dict[str, float],
) -> pd.DataFrame:
    """Per-(zone, store) gravity weight ``A_s / (d + d₀)^β`` with A_s
    from config (non-uniform). Returns long form."""
    beta = cfg.segments[segment]["distance_decay"]["beta"]
    d0 = float(cfg.global_.get("distance_decay_d0", 0.5))
    banners = set(seg_banners[segment])
    seg_stores = stores[stores["banner_code"].isin(banners)].copy()

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
    a_s_arr = pairs["banner_code"].map(a_s).to_numpy()
    pairs["weight"] = a_s_arr / (d + d0) ** beta      # non-uniform A_s
    return pairs[["zone_id", "store_id", "banner_code", "distance", "weight"]]


def _resolve_banner_and_store(
    rng: np.random.Generator,
    budget: int,
    banners: list[str],
    primary: str | None,
    loyalty_type: str,
    banner_pull: dict[str, float],
    by_banner_stores: dict[str, pd.DataFrame],
    fallback_gravity: pd.DataFrame,
) -> tuple[np.ndarray, np.ndarray]:
    """Compose brand affinity (primary × loyalty) × gravity pull to pick
    a banner per trip, then a store within the banner. Shared by grocery
    (primary_banner) and QSR (qsr_primary)."""
    if primary is None or primary not in banners:
        primary = banners[0]
    others = [b for b in banners if b != primary]
    rng.shuffle(others)
    # loyalty weights length matches banner count (pad/truncate).
    lw_full = _LOYALTY_BANNER_WEIGHTS[loyalty_type]
    banner_order = [primary] + others
    loyalty_w = np.array(lw_full[:len(banner_order)], dtype=float)
    if len(loyalty_w) < len(banner_order):
        loyalty_w = np.concatenate([
            loyalty_w, np.full(len(banner_order) - len(loyalty_w), lw_full[-1])
        ])
    gravity_pull = np.array([banner_pull.get(b, 0.0) for b in banner_order], dtype=float)
    composed = loyalty_w * gravity_pull
    composed = composed / composed.sum() if composed.sum() > 0 else \
        loyalty_w / loyalty_w.sum()
    banner_picks = rng.choice(banner_order, size=budget, p=composed)

    store_picks = np.empty(budget, dtype=object)
    for b in banners:
        mask = banner_picks == b
        if not mask.any():
            continue
        df_zb = by_banner_stores.get(b)
        if df_zb is None or len(df_zb) == 0:
            df_zb = fallback_gravity[fallback_gravity["banner_code"] == b]
        w = df_zb["weight"].to_numpy()
        w = w / w.sum()
        s_ids = df_zb["store_id"].to_numpy()
        store_picks[mask] = rng.choice(s_ids, size=int(mask.sum()), p=w)
    return banner_picks, store_picks


# ----- main builder -------------------------------------------------

def build_trips(
    cfg: Config,
    population: pd.DataFrame,
    customers: pd.DataFrame,
    stores: pd.DataFrame,
    zones: pd.DataFrame,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Place every trip in time and space. Returns one row per trip:
    ``[trip_id, card_id, segment, banner_code, store_id, txn_ts]``,
    sorted by trip_id (deterministic under a fixed seed)."""
    calendar = _build_day_calendar(cfg)
    total_days = len(calendar)
    window_start = calendar["date"].iloc[0]
    weekdays_arr = calendar["weekday"].to_numpy()

    seg_banners = _segment_banners(cfg)
    a_s = _attractiveness_by_banner(cfg)
    segments = [s for s in ("grocery", "qsr") if s in seg_banners]

    seasonality = _seasonality_day_multiplier(cfg, calendar)
    seg_day_weights = {
        s: _segment_day_weights(cfg, calendar, s, seasonality) for s in segments
    }
    # CFA closed Sundays (weekday 6) — a hard zero on the day weights.
    cfa_day_weights = seg_day_weights.get("qsr")
    if cfa_day_weights is not None:
        cfa_day_weights = cfa_day_weights.copy()
        cfa_day_weights[weekdays_arr == 6] = 0.0

    seg_gravity = {
        s: _compute_store_gravity(cfg, stores, zones, s, seg_banners, a_s)
        for s in segments
    }
    # Per-segment, per-zone: banner pull (for composition) + per-banner
    # store tables (for within-banner store choice).
    pull_by_zone: dict[str, dict[str, dict[str, float]]] = {}
    stores_by_zone_banner: dict[str, dict[str, dict[str, pd.DataFrame]]] = {}
    for s in segments:
        pull_by_zone[s] = {
            z: grp.groupby("banner_code")["weight"].sum().to_dict()
            for z, grp in seg_gravity[s].groupby("zone_id")
        }
        d: dict[str, dict[str, pd.DataFrame]] = {}
        for (z_id, b_code), grp in seg_gravity[s].groupby(["zone_id", "banner_code"]):
            d.setdefault(z_id, {})[b_code] = grp.reset_index(drop=True)
        stores_by_zone_banner[s] = d

    cust_idx = customers.set_index("card_id")
    pop_idx = population.set_index("card_id")
    card_ids = sorted(population["card_id"].tolist())
    records: list[tuple] = []
    trip_counter = 0

    for card_id in card_ids:
        pop_row = pop_idx.loc[card_id]
        cust_row = cust_idx.loc[card_id]
        home_zone = cust_row["home_zone"]
        loyalty_type = cust_row["loyalty_type"]
        cohort = pop_row["cohort"]
        active_lo, active_hi = _cohort_window(rng, cohort, total_days)
        active_days = np.arange(active_lo, active_hi)
        if len(active_days) == 0:
            continue

        for segment in segments:
            budget = int(pop_row[f"trip_budget_{segment}"])
            if budget == 0:
                continue
            banners = seg_banners[segment]
            primary = (
                cust_row["primary_banner"] if segment == "grocery"
                else cust_row["qsr_primary"]
            )

            # 1) Banner + store per trip.
            banner_picks, store_picks = _resolve_banner_and_store(
                rng, budget, banners, primary, loyalty_type,
                pull_by_zone[segment].get(home_zone, {}),
                stores_by_zone_banner[segment].get(home_zone, {}),
                seg_gravity[segment],
            )

            # 2) Day per trip — base seg weights, but CFA trips use the
            #    Sunday-zeroed weights (closed Sundays, §B2).
            base_w = seg_day_weights[segment][active_days]
            base_norm = base_w / base_w.sum()
            day_indices = np.empty(budget, dtype=int)
            if segment == "qsr":
                is_cfa = banner_picks == "CFA"
                if is_cfa.any():
                    cfa_w = cfa_day_weights[active_days]
                    cfa_w = cfa_w / cfa_w.sum() if cfa_w.sum() > 0 else base_norm
                    day_indices[is_cfa] = rng.choice(
                        active_days, size=int(is_cfa.sum()), p=cfa_w)
                if (~is_cfa).any():
                    day_indices[~is_cfa] = rng.choice(
                        active_days, size=int((~is_cfa).sum()), p=base_norm)
            else:
                day_indices[:] = rng.choice(active_days, size=budget, p=base_norm)

            # 3) Hour per trip — per-banner daypart.
            weekdays = weekdays_arr[day_indices]
            hours = np.empty(budget, dtype=int)
            if segment == "grocery":
                wk = weekdays >= 5
                if wk.any():
                    hours[wk] = rng.choice(
                        24, size=int(wk.sum()), p=_GROCERY_WEEKEND_HOUR_WEIGHTS)
                if (~wk).any():
                    hours[~wk] = rng.choice(
                        24, size=int((~wk).sum()), p=_GROCERY_WEEKDAY_HOUR_WEIGHTS)
            else:
                for b in banners:
                    bmask = banner_picks == b
                    if not bmask.any():
                        continue
                    hours[bmask] = rng.choice(
                        24, size=int(bmask.sum()),
                        p=_QSR_BANNER_HOUR_WEIGHTS[b])
            minutes = rng.integers(0, 60, size=budget)
            seconds = rng.integers(0, 60, size=budget)

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
