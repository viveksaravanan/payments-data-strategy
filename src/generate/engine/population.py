"""Layer 2 — Population shape (D14).

Assigns each of ~100k cards an activity profile: which segments it
touches and a per-segment trip budget (D14.3 intensity tier ×
distribution around the tier mean). Implements:

* **Participation matrix (D14.4)** — 7 archetypes summing to 100k,
  with ~32% multi-merchant and ~6% all-three.
* **Intensity tiers (D14.3)** — per-segment core/regular/occasional
  (heavy/regular/occasional for QSR; enthusiast/regular/occasional
  for off-price) with shares + per-tier μ + per-tier range.
* **Cohort tag (D14.7)** — established / new_in_window / lapsing.
  Layer 3 places first-appearance dates from this; default split is
  85 / 10 / 5 (not a ratified D14 number; tuneable in config later).

QSR tier shares adjusted to 10/40/50 (from D14.3's 15/35/50) per
AskUserQuestion resolution in the Stage 4.2 build session — the
ratified means × original shares overshoot the D5 365k target by
~17%. See ``tests/test_engine_population.py::test_qsr_tier_shares``
for the docstring.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd

from src.generate.config.loader import Config


# ----- Participation matrix (D14.4) ---------------------------------
# Probabilities sum to 1.0. The 7 archetypes determine which segments
# each card touches.

_PARTICIPATION_ARCHETYPES = [
    # name, (active_grocery, active_qsr, active_off_price), share
    ("grocery_only",       (True,  False, False), 0.37),
    ("retail_only",        (False, False, True),  0.18),
    ("qsr_only",           (False, True,  False), 0.13),
    ("grocery_qsr",        (True,  True,  False), 0.13),
    ("grocery_retail",     (True,  False, True),  0.09),
    ("grocery_qsr_retail", (True,  True,  True),  0.06),
    ("qsr_retail",         (False, True,  True),  0.04),
]


# ----- Intensity tiers (D14.3) --------------------------------------
# Per-segment tier specs: (name, share, lo, mu, hi)

@dataclass(frozen=True)
class TierSpec:
    name: str
    share: float
    lo: int
    mu: float
    hi: int


_GROCERY_TIERS = [
    TierSpec("core",       0.20, 28, 34.0, 40),
    TierSpec("regular",    0.45, 14, 18.0, 22),
    TierSpec("occasional", 0.35,  3,  6.0, 10),
]

# QSR shares 10/40/50 — see module docstring + the test.
_QSR_TIERS = [
    TierSpec("heavy",      0.10, 30, 40.0, 55),
    TierSpec("regular",    0.40,  8, 11.0, 15),
    TierSpec("occasional", 0.50,  2,  4.0,  6),
]

# Off-price tier hi bounds widened from D14.3's stated 20/8/3 to
# 24/10/4 — see AskUserQuestion in the Stage 4.2 build session.
# D14.3's triangular(lo, μ, hi) shape can't reach the stated means
# within the original ranges (max mean for [4,8] is 6.67, can't hit 7).
# Widening hi by ~25-33% keeps μ as ratified and lets the
# distribution actually achieve it. Shares (12/33/55) and means
# (16/7/2.5) are untouched.
_OFF_PRICE_TIERS = [
    TierSpec("enthusiast", 0.12, 10, 16.0, 24),
    TierSpec("regular",    0.33,  4,  7.0, 10),
    TierSpec("occasional", 0.55,  1,  2.5,  4),
]

# Default cohort split (D14.7). Not strictly ratified; tunable.
_COHORT_NAMES  = ("established", "new_in_window", "lapsing")
_COHORT_SHARES = (0.85,          0.10,            0.05)


def _make_card_ids(n: int, seed: int) -> list[str]:
    """16-char hex card IDs, stable for ``(seed, index)`` pairs.

    Matches the v3 customer_id convention (sha256[:16]) so the
    downstream `customer_token` column in transactions can carry
    forward without surprises.
    """
    salt = f"v4-card-{seed}".encode()
    out: list[str] = []
    for i in range(n):
        h = hashlib.sha256(salt + i.to_bytes(8, "big")).hexdigest()
        out.append(h[:16])
    return out


def _sample_categorical(
    rng: np.random.Generator,
    n: int,
    labels: Iterable[str],
    probs: Iterable[float],
) -> np.ndarray:
    """Vectorized categorical draw returning an array of label strings."""
    labels = list(labels)
    probs = np.asarray(list(probs), dtype=float)
    probs = probs / probs.sum()
    idx = rng.choice(len(labels), size=n, p=probs)
    arr = np.empty(n, dtype=object)
    for i, lab in enumerate(labels):
        arr[idx == i] = lab
    return arr


def _sample_tier_budgets(
    rng: np.random.Generator,
    tier_assignments: np.ndarray,
    tiers: list[TierSpec],
) -> np.ndarray:
    """Per-card trip budget given each card's tier assignment.

    Per-tier sampling: triangular(lo, mu, hi). The triangular
    distribution's mean is (lo + mu + hi)/3, so picking mode = μ
    keeps the per-tier mean ≈ μ as D14.3 intends. Rounded to int
    and clipped to [lo, hi] (triangular is already in-range, but
    we round so the rounding doesn't push us out).
    """
    out = np.zeros(len(tier_assignments), dtype=int)
    for t in tiers:
        mask = tier_assignments == t.name
        n = int(mask.sum())
        if n == 0:
            continue
        draws = rng.triangular(t.lo, t.mu, t.hi, size=n)
        out[mask] = np.clip(np.round(draws).astype(int), t.lo, t.hi)
    return out


def build_population(cfg: Config, rng: np.random.Generator) -> pd.DataFrame:
    """Build the population frame: one row per card."""
    n = int(cfg.global_["population"]["target_cards"])

    # 1) Card IDs — deterministic from seed.
    card_ids = _make_card_ids(n, seed=int(cfg.global_["seed"]))

    # 2) Participation archetype per card.
    archetype_names = [a[0] for a in _PARTICIPATION_ARCHETYPES]
    archetype_shares = np.array([a[2] for a in _PARTICIPATION_ARCHETYPES])
    archetype_shares = archetype_shares / archetype_shares.sum()
    archetype_idx = rng.choice(len(_PARTICIPATION_ARCHETYPES), size=n, p=archetype_shares)
    arche = np.array(archetype_names, dtype=object)[archetype_idx]

    # Derive per-segment activity flags.
    flags = np.array([a[1] for a in _PARTICIPATION_ARCHETYPES])  # 7 x 3
    seg_flags = flags[archetype_idx]                              # n x 3
    active_g = seg_flags[:, 0]
    active_q = seg_flags[:, 1]
    active_r = seg_flags[:, 2]

    # 3) Per-segment tier assignment, conditional on being active.
    def _assign_tier(active_mask: np.ndarray, tiers: list[TierSpec]) -> np.ndarray:
        out = np.full(n, None, dtype=object)
        idx = np.where(active_mask)[0]
        if len(idx) == 0:
            return out
        out[idx] = _sample_categorical(
            rng, len(idx),
            labels=[t.name for t in tiers],
            probs=[t.share for t in tiers],
        )
        return out

    tier_g = _assign_tier(active_g, _GROCERY_TIERS)
    tier_q = _assign_tier(active_q, _QSR_TIERS)
    tier_r = _assign_tier(active_r, _OFF_PRICE_TIERS)

    # 4) Per-segment trip budgets.
    budget_g = np.zeros(n, dtype=int)
    budget_q = np.zeros(n, dtype=int)
    budget_r = np.zeros(n, dtype=int)
    if active_g.any():
        budget_g[active_g] = _sample_tier_budgets(
            rng, tier_g[active_g], _GROCERY_TIERS,
        )
    if active_q.any():
        budget_q[active_q] = _sample_tier_budgets(
            rng, tier_q[active_q], _QSR_TIERS,
        )
    if active_r.any():
        budget_r[active_r] = _sample_tier_budgets(
            rng, tier_r[active_r], _OFF_PRICE_TIERS,
        )

    # 5) Cohort tag (D14.7).
    cohort = _sample_categorical(rng, n, _COHORT_NAMES, _COHORT_SHARES)

    df = pd.DataFrame({
        "card_id":               card_ids,
        "participation_archetype": arche,
        "active_grocery":        active_g,
        "active_qsr":            active_q,
        "active_off_price":      active_r,
        "tier_grocery":          tier_g,
        "tier_qsr":              tier_q,
        "tier_off_price":        tier_r,
        "trip_budget_grocery":   budget_g,
        "trip_budget_qsr":       budget_q,
        "trip_budget_off_price": budget_r,
        "cohort":                cohort,
    })

    # Stable sort key so two runs at the same seed serialize identically.
    df = df.sort_values("card_id", kind="mergesort").reset_index(drop=True)
    return df
