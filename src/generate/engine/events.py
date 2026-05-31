"""Layer 8 — Promotions & planted anomalies (D20).

Two kinds of perturbations layered onto built data:

* **Promotions (D20.1)** — segment-typed promo schedule (weekly ad,
  TPR, BOGO, clearance, LTO). Each entry is (promo_id, sku, window,
  depth, type). Demand response: promoted SKUs get a basket-inclusion
  lift during their window, scaled to depth × elasticity (the v4
  piece the v3 baseline lacked). Discount applied at the pricing
  layer (D20.1 explicit: promoted SKUs drop in unit_price during
  window). Target ~25-35% of grocery units on promo (T15).

* **Planted anomalies A1-A3 (D20.2)** — scoped perturbations the
  Anomaly agent must rediscover. Per D20.3 fraud/tampering (A4-A5)
  is OUT for v4. Ground truth saved to data/eval/anomalies_
  groundtruth at Stage 5 — never visible to the agent / lake (Wave 2
  enforced via physical-directory separation).

  A1 localized demand decline: trips drop at affected zone stores,
     hardest at WDX (value banner). Window Apr 19 - May 29 (6 weeks).
  A2 category demand spike: PRODUCE spike at one KRG store, 4 days
     (Apr 21-24). The 'avocado' signature from v3.
  A3 competitive share shift: pasta promo divergence — KRG lift,
     ACM failure, WDX modest lift in their respective late-April
     windows. Implemented as promos (positive lift) + a complementary
     demand drop at competing banners during the shift window.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from src.generate.config.loader import Config


# Per-banner weekly-ad share of SKUs per week (D20.1 — 50-150 SKUs
# / week on grocery weekly ad). With ~1075 grocery SKUs, 14% per
# week ≈ 150 SKUs — upper edge of real-world. Combined with
# LIFT_COEFFICIENT=6 below, this lands T15 grocery promo unit
# share at 25-30%.
_WEEKLY_AD_SKU_SHARE = 0.14
# Depth range for weekly ad (D20.1 — 10-25%).
_WEEKLY_AD_DEPTH_RANGE = (0.12, 0.25)

# QSR LTOs — narrow, longer windows.
_QSR_LTO_SHARE = 0.20      # ~12 of 60 SKUs at a time
_QSR_LTO_DEPTH_RANGE = (0.15, 0.30)

# Off-price clearance markdowns — pervasive but small per-SKU.
_OFFPRICE_CLEARANCE_SHARE = 0.15
_OFFPRICE_DEPTH_RANGE = (0.20, 0.45)

# Demand-lift coefficient: lift_factor = LIFT_COEFFICIENT × depth.
# At depth=0.20 this gives a 1.2× boost (basket-pick weight ×2.2),
# matching real-world ~80-120% unit lift on a 20%-off promotional
# SKU. Tuned together with _WEEKLY_AD_SKU_SHARE to land T15 grocery
# promo unit share at 25-35% in the pilot.
LIFT_COEFFICIENT = 6.0


# ============= A1-A3 ANOMALY DEFINITIONS ============================

@dataclass(frozen=True)
class AnomalyDef:
    anomaly_id: str
    type: str         # "demand_decline" | "category_spike" | "share_shift"
    description: str
    start_date: date
    end_date: date
    # Optional scope (some may be None per type):
    zone_id: str | None = None
    banner_code: str | None = None
    store_id: str | None = None
    category: str | None = None
    subcategory: str | None = None
    magnitude: float = 0.0   # interpretation depends on type


def build_anomaly_schedule(cfg: Config, stores: pd.DataFrame) -> pd.DataFrame:
    """Generate the A1-A3 ground truth schedule.

    Each row defines a planted anomaly with the slice it affects and
    the magnitude. Stage 5 saves this to data/eval/anomalies_groundtruth
    (NOT data/raw/) so it's structurally isolated from the lake/agent.
    """
    window_start = pd.Timestamp(cfg.global_["window"]["start_date"]).date()

    rows: list[AnomalyDef] = []

    # ----- A1 — University City + Eastway demand decline -----------
    # Window: Apr 19 - May 29 (D20.2 / from v3). WDX hardest (value
    # banner serves the value zones; competitive pressure or local
    # event drives the decline).
    a1_start = date(2026, 4, 19)
    a1_end = date(2026, 5, 29)
    for zone in ("university_city", "eastway"):
        for banner, mag in (("WDX", 0.40), ("KRG", 0.15), ("ACM", 0.10)):
            rows.append(AnomalyDef(
                anomaly_id=f"A1-{zone}-{banner}",
                type="demand_decline",
                description=f"Localized trip decline at {zone} {banner} stores",
                start_date=a1_start, end_date=a1_end,
                zone_id=zone, banner_code=banner,
                category=None, subcategory=None,
                magnitude=mag,
            ))

    # ----- A2 — Plaza Midwood produce spike (avocado signature) ----
    # 4-day spike at one Kroger NoDa store, PRODUCE/FRUIT.
    # NoDa is the "Plaza Midwood" stand-in in the v4 metro.
    krg_noda = stores[
        (stores["banner_code"] == "KRG") & (stores["zone_id"] == "noda")
    ]
    if len(krg_noda) > 0:
        a2_store_id = krg_noda["store_id"].iloc[0]
        rows.append(AnomalyDef(
            anomaly_id="A2-noda-KRG-produce",
            type="category_spike",
            description="PRODUCE/FRUIT demand spike at KRG NoDa (Apr 21-24)",
            start_date=date(2026, 4, 21), end_date=date(2026, 4, 24),
            zone_id="noda", banner_code="KRG", store_id=a2_store_id,
            category="PRODUCE", subcategory="FRUIT",
            magnitude=3.0,   # 3x inclusion boost during window
        ))

    # ----- A3 — Competitive pasta promo divergence ------------------
    # Three grocers run pasta promos in succession with different
    # depths + outcomes per D20.2.
    pasta_promo_defs = [
        ("KRG", date(2026, 4, 15), date(2026, 4, 21), 0.25, 1.5),   # lift
        ("ACM", date(2026, 4, 19), date(2026, 4, 25), 0.20, 0.85),  # failure (basket mult <1)
        ("WDX", date(2026, 4, 22), date(2026, 4, 28), 0.15, 1.20),  # modest lift
    ]
    for banner, s, e, depth, basket_mult in pasta_promo_defs:
        rows.append(AnomalyDef(
            anomaly_id=f"A3-{banner}-pasta",
            type="share_shift",
            description=f"Competitive pasta promo at {banner} (depth {int(depth*100)}%)",
            start_date=s, end_date=e,
            zone_id=None, banner_code=banner, store_id=None,
            category="PANTRY", subcategory="PASTA",
            magnitude=basket_mult,
        ))

    return pd.DataFrame([
        {
            "anomaly_id": r.anomaly_id, "type": r.type,
            "description": r.description,
            "start_date": r.start_date, "end_date": r.end_date,
            "zone_id": r.zone_id, "banner_code": r.banner_code,
            "store_id": r.store_id,
            "category": r.category, "subcategory": r.subcategory,
            "magnitude": r.magnitude,
        }
        for r in rows
    ])


# ============= PROMO SCHEDULE ========================================

def build_promo_schedule(
    cfg: Config,
    catalog: pd.DataFrame,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Generate the full promo schedule for the 90-day window.

    Returns one row per (promo_id, sku) pair with start/end dates,
    depth, and promo_type. A3 pasta promos are injected at the right
    SKUs to back the anomaly ground truth.
    """
    window_start = pd.Timestamp(cfg.global_["window"]["start_date"]).date()
    window_end = pd.Timestamp(cfg.global_["window"]["end_date"]).date()

    def _cap(d: date) -> date:
        return min(d, window_end)

    rows = []

    # ----- Grocery weekly ads -----
    for banner in ("KRG", "ACM", "WDX"):
        merchant_skus = catalog[catalog["banner_code"] == banner]["sku"].to_numpy()
        for week in range(13):
            promo_start = window_start + timedelta(days=7 * week)
            promo_end = _cap(promo_start + timedelta(days=6))
            n_skus = max(1, int(len(merchant_skus) * _WEEKLY_AD_SKU_SHARE))
            promo_skus = rng.choice(merchant_skus, size=n_skus, replace=False)
            depth = float(rng.uniform(*_WEEKLY_AD_DEPTH_RANGE))
            promo_id = f"{banner}-W{week:02d}"
            for sku in promo_skus:
                rows.append({
                    "promo_id": promo_id, "sku": str(sku),
                    "merchant_id": banner, "promo_type": "weekly_ad",
                    "start_date": promo_start, "end_date": promo_end,
                    "depth_pct": round(depth, 3),
                })

    # ----- QSR LTOs -----
    qsr_skus = catalog[catalog["banner_code"] == "TBL"]["sku"].to_numpy()
    for cycle_start in (date(2026, 3, 8), date(2026, 4, 5),
                       date(2026, 4, 26), date(2026, 5, 17)):
        cycle_end = _cap(cycle_start + timedelta(days=13))
        n_skus = max(1, int(len(qsr_skus) * _QSR_LTO_SHARE))
        sel = rng.choice(qsr_skus, size=n_skus, replace=False)
        depth = float(rng.uniform(*_QSR_LTO_DEPTH_RANGE))
        promo_id = f"TBL-LTO-{cycle_start.month:02d}{cycle_start.day:02d}"
        for sku in sel:
            rows.append({
                "promo_id": promo_id, "sku": str(sku),
                "merchant_id": "TBL", "promo_type": "lto",
                "start_date": cycle_start, "end_date": cycle_end,
                "depth_pct": round(depth, 3),
            })

    # ----- Off-price clearance (rolling, pervasive) -----
    tjx_skus = catalog[catalog["banner_code"] == "TJX"]["sku"].to_numpy()
    for week in range(0, 13, 2):   # every 2 weeks
        cycle_start = window_start + timedelta(days=7 * week)
        cycle_end = _cap(cycle_start + timedelta(days=13))
        n_skus = max(1, int(len(tjx_skus) * _OFFPRICE_CLEARANCE_SHARE))
        sel = rng.choice(tjx_skus, size=n_skus, replace=False)
        depth = float(rng.uniform(*_OFFPRICE_DEPTH_RANGE))
        promo_id = f"TJX-CLR-W{week:02d}"
        for sku in sel:
            rows.append({
                "promo_id": promo_id, "sku": str(sku),
                "merchant_id": "TJX", "promo_type": "clearance",
                "start_date": cycle_start, "end_date": cycle_end,
                "depth_pct": round(depth, 3),
            })

    # ----- A3 pasta promos — explicit per banner with depths -----
    a3_defs = [
        ("KRG", date(2026, 4, 15), date(2026, 4, 21), 0.25),
        ("ACM", date(2026, 4, 19), date(2026, 4, 25), 0.20),
        ("WDX", date(2026, 4, 22), date(2026, 4, 28), 0.15),
    ]
    for banner, s, e, depth in a3_defs:
        pasta_skus = catalog[
            (catalog["banner_code"] == banner)
            & (catalog["subcategory"] == "PASTA")
        ]["sku"].to_numpy()
        for sku in pasta_skus:
            rows.append({
                "promo_id": f"{banner}-A3-PASTA", "sku": str(sku),
                "merchant_id": banner, "promo_type": "pasta_promo",
                "start_date": s, "end_date": e,
                "depth_pct": float(depth),
            })

    return pd.DataFrame(rows)


# ============= APPLY ANOMALIES TO TRIPS =============================

def apply_anomaly_filter(
    trips: pd.DataFrame,
    anomaly_schedule: pd.DataFrame,
    stores: pd.DataFrame,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Drop trips per A1 demand decline. Returns filtered trips frame.

    A2 (category spike) is applied at basket-build time via mission
    weighting — not a trip-count effect. A3 (share shift) is applied
    via the promo demand lift (KRG positive, ACM negative via
    basket_mult), not by dropping trips.
    """
    if len(anomaly_schedule) == 0:
        return trips

    declines = anomaly_schedule[anomaly_schedule["type"] == "demand_decline"]
    if len(declines) == 0:
        return trips

    keep_mask = np.ones(len(trips), dtype=bool)
    trips_ts = pd.to_datetime(trips["txn_ts"])
    trips_date = trips_ts.dt.date.to_numpy()
    store_to_zone = stores.set_index("store_id")["zone_id"].to_dict()
    trips_zones = np.array([store_to_zone.get(s, "") for s in trips["store_id"]])

    for _, anom in declines.iterrows():
        mask = (
            (trips_zones == anom["zone_id"])
            & (trips["banner_code"].to_numpy() == anom["banner_code"])
            & (trips_date >= anom["start_date"])
            & (trips_date <= anom["end_date"])
        )
        n_affected = int(mask.sum())
        if n_affected == 0:
            continue
        # Each affected trip kept with probability (1 - magnitude).
        keep_probs = rng.uniform(size=n_affected)
        affected_keep = keep_probs < (1.0 - anom["magnitude"])
        mask_idx = np.where(mask)[0]
        keep_mask[mask_idx] = affected_keep

    return trips[keep_mask].reset_index(drop=True)


# ============= PROMO LOOKUPS FOR BASKETS + PRICING ==================

def build_promo_lookup(
    promo_schedule: pd.DataFrame,
) -> dict[tuple[date, str], float]:
    """Returns {(date, sku): depth_pct} for fast per-line lookup.

    Built once and reused by basket sampling (demand lift) and
    pricing (discount application).
    """
    if len(promo_schedule) == 0:
        return {}
    lookup: dict[tuple[date, str], float] = {}
    for r in promo_schedule.itertuples(index=False):
        d = r.start_date
        end = r.end_date
        while d <= end:
            lookup[(d, r.sku)] = float(r.depth_pct)
            d = d + timedelta(days=1)
    return lookup


def build_promo_id_lookup(
    promo_schedule: pd.DataFrame,
) -> dict[tuple[date, str], str]:
    """Returns {(date, sku): promo_id} parallel to depth lookup."""
    if len(promo_schedule) == 0:
        return {}
    lookup: dict[tuple[date, str], str] = {}
    for r in promo_schedule.itertuples(index=False):
        d = r.start_date
        end = r.end_date
        while d <= end:
            lookup[(d, r.sku)] = str(r.promo_id)
            d = d + timedelta(days=1)
    return lookup


# ============= A2 + A3 CATEGORY/SKU BOOST LOOKUPS ==================

def build_a2_boost_lookup(
    anomaly_schedule: pd.DataFrame,
) -> dict[tuple[date, str, str], float]:
    """A2: (date, store_id, category) → multiplier on category weight.
    Stage 4.8 only emits FRUIT subcategory boost via A2; basket
    builder applies during PRODUCE category sampling.
    """
    out: dict[tuple[date, str, str], float] = {}
    spikes = anomaly_schedule[anomaly_schedule["type"] == "category_spike"]
    for _, r in spikes.iterrows():
        d = r["start_date"]
        end = r["end_date"]
        while d <= end:
            out[(d, str(r["store_id"]), str(r["category"]))] = float(r["magnitude"])
            d = d + timedelta(days=1)
    return out


def build_a3_basket_mult_lookup(
    anomaly_schedule: pd.DataFrame,
) -> dict[tuple[date, str], float]:
    """A3: (date, banner_code) → pasta basket multiplier (1.0 = neutral)."""
    out: dict[tuple[date, str], float] = {}
    shifts = anomaly_schedule[anomaly_schedule["type"] == "share_shift"]
    for _, r in shifts.iterrows():
        d = r["start_date"]
        end = r["end_date"]
        while d <= end:
            out[(d, str(r["banner_code"]))] = float(r["magnitude"])
            d = d + timedelta(days=1)
    return out
