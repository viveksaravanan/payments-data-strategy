"""Layer 2 — Population shape (D14 / datamodel-v2 §C1).

Assigns each of ~155k cards an activity profile: which segments it
touches and a per-segment trip budget (intensity tier × distribution
around the tier mean). Implements:

* **Participation matrix (§C1)** — 3 archetypes over the two segments
  (off-price dropped): grocery-only 36% / QSR-only 18% / both 46%.
  Grocery-active 82% (~127k), QSR-active 64% (~99k); the 46% "both"
  group powers cross-segment analysis.
* **Intensity tiers (App D)** — per-segment core/regular/occasional
  (heavy/regular/occasional for QSR) with shares + per-tier μ +
  per-tier range. Grocery blended ~1.5/wk, QSR ~1.4/wk; tier means
  calibrated so total window volume tracks the §C9 targets
  (grocery ~2.67M, qsr ~1.76M transactions).
* **Cohort tag (D14.7)** — established / new_in_window / lapsing.
  Layer 3 places first-appearance dates from this; default split is
  85 / 10 / 5 (tuneable).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd

from src.generate.config.loader import Config


# ----- Participation matrix (§C1) -----------------------------------
# Probabilities sum to 1.0. Three archetypes over the two segments
# (off-price dropped in datamodel-v2). grocery-active 82% (~127k),
# qsr-active 64% (~99k), "both" 46% (cross-segment cohort).

_PARTICIPATION_ARCHETYPES = [
    # name, (active_grocery, active_qsr), share
    ("grocery_only", (True,  False), 0.36),
    ("qsr_only",     (False, True),  0.18),
    ("both",         (True,  True),  0.46),
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


# Intensity tiers (App D). Triangular(lo, μ, hi) per tier; μ is the
# mode so the per-tier mean ≈ (lo+μ+hi)/3. Grocery blended ~1.54/wk
# (~19.8 trips/90d) so 127k active × basket ≈ the §C9 grocery volume;
# starting values — the Phase-4/5 calibration nudges these if total
# window volume drifts from the §C9 target.
_GROCERY_TIERS = [
    TierSpec("core",       0.20, 32, 40.0, 48),   # ~3.1/wk
    TierSpec("regular",    0.45, 15, 20.0, 25),   # ~1.56/wk
    TierSpec("occasional", 0.35,  3,  8.0, 13),   # ~0.62/wk
]

# QSR blended ~1.38/wk (~17.8 visits/90d) so 99k active tracks the
# §C9 qsr volume (~1.76M window transactions).
_QSR_TIERS = [
    TierSpec("heavy",      0.12, 32, 44.0, 58),   # ~3.5/wk
    TierSpec("regular",    0.42, 14, 20.0, 26),   # ~1.56/wk
    TierSpec("occasional", 0.46,  3,  9.0, 14),   # ~0.68/wk
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


def build_population(
    cfg: Config,
    rng: np.random.Generator,
    *,
    n_cards: int | None = None,
) -> pd.DataFrame:
    """Build the population frame: one row per card.

    ``n_cards`` overrides ``cfg.global_['population']['target_cards']``.
    Used for pilot-mode runs (~5-10k cards) during Stage 4.5+
    development where full-scale iteration is too slow.
    """
    n = int(n_cards if n_cards is not None else cfg.global_["population"]["target_cards"])

    # 1) Card IDs — deterministic from seed.
    card_ids = _make_card_ids(n, seed=int(cfg.global_["seed"]))

    # 2) Participation archetype per card.
    archetype_names = [a[0] for a in _PARTICIPATION_ARCHETYPES]
    archetype_shares = np.array([a[2] for a in _PARTICIPATION_ARCHETYPES])
    archetype_shares = archetype_shares / archetype_shares.sum()
    archetype_idx = rng.choice(len(_PARTICIPATION_ARCHETYPES), size=n, p=archetype_shares)
    arche = np.array(archetype_names, dtype=object)[archetype_idx]

    # Derive per-segment activity flags.
    flags = np.array([a[1] for a in _PARTICIPATION_ARCHETYPES])  # 3 x 2
    seg_flags = flags[archetype_idx]                              # n x 2
    active_g = seg_flags[:, 0]
    active_q = seg_flags[:, 1]

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

    # 4) Per-segment trip budgets.
    budget_g = np.zeros(n, dtype=int)
    budget_q = np.zeros(n, dtype=int)
    if active_g.any():
        budget_g[active_g] = _sample_tier_budgets(
            rng, tier_g[active_g], _GROCERY_TIERS,
        )
    if active_q.any():
        budget_q[active_q] = _sample_tier_budgets(
            rng, tier_q[active_q], _QSR_TIERS,
        )

    # 5) Cohort tag (D14.7).
    cohort = _sample_categorical(rng, n, _COHORT_NAMES, _COHORT_SHARES)

    df = pd.DataFrame({
        "card_id":               card_ids,
        "participation_archetype": arche,
        "active_grocery":        active_g,
        "active_qsr":            active_q,
        "tier_grocery":          tier_g,
        "tier_qsr":              tier_q,
        "trip_budget_grocery":   budget_g,
        "trip_budget_qsr":       budget_q,
        "cohort":                cohort,
    })

    # Stable sort key so two runs at the same seed serialize identically.
    df = df.sort_values("card_id", kind="mergesort").reset_index(drop=True)
    return df
