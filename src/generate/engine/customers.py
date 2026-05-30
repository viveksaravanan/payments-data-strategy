"""Layer 3 — Customer durable state (D16).

Sets per-card attributes that hold across all 90 days:

* **Home zone (D13.3)** — drawn from the residential weight column.
* **Affluence** — conditioned on the zone profile mean with within-
  zone Gaussian spread (D13.3 explicit: 'affluence is a skew with
  within-zone variance, not a determinant').
* **Loyalty type (D16.1)** — loyalist / splitter / three_chain /
  lapsed_light at 55 / 30 / 12 / 3 %. Only meaningful for cards
  with grocery activity (Layer 2 flag). The primary banner is
  drawn from the 3 grocers; loyalty weights vs second/third
  banner are consumed by Stage 4.4 trips (D15b).
* **Card identity (D16.3)** — one card token per customer for v4,
  but the schema (``tender, network, wallet_enrolled,
  wallet_provider``) is portfolio-ready. Tender is driven by
  affluence (affluent → credit, value → debit) so the per-
  merchant credit/debit mix emerges at Layer 6 (D7.5 fix).
  Network is conditioned on tender per Fed 2025 Diary anchors.

Preference vector + staple SKUs (D16.2) are deferred to Stage 4.5
where the catalog exists. Tier and trip-budget from Layer 2 are
already on the population frame; this layer joins to it for the
card-id index but doesn't duplicate those columns.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.generate.config.loader import Config


# ----- Loyalty shares (D16.1) ---------------------------------------

_LOYALTY_SHARES = {
    "loyalist":     0.55,
    "splitter":     0.30,
    "three_chain":  0.12,
    "lapsed_light": 0.03,
}

# Grocer banner codes — primary_banner is drawn from these.
_GROCER_BANNERS = ("KRG", "ACM", "WDX")

# Primary-banner shares among grocery-active cards. Roughly
# proportional to store count (each grocer has 5 stores per D13.2
# config) with a small upweight toward Kroger's mainstream
# positioning. Final loyalty × gravity composition happens at
# Stage 4.4 (D15b).
_PRIMARY_BANNER_SHARES = {"KRG": 0.40, "ACM": 0.33, "WDX": 0.27}


# ----- Card identity (D16.3) ----------------------------------------

# Within-tender network shares: Fed 2025 Diary anchors.
_DEBIT_NETWORKS  = {"visa": 0.60, "mc": 0.38, "amex": 0.01, "discover": 0.01}
_CREDIT_NETWORKS = {"visa": 0.50, "mc": 0.25, "amex": 0.19, "discover": 0.06}

# Wallet provider shares among enrolled customers.
_WALLET_PROVIDERS = {"apple": 0.55, "google": 0.30, "samsung": 0.15}

# Wallet-enrollment rate anchor (D16.3 / D18.2 ~45%).
_WALLET_ENROLL_RATE = 0.45

# Tender vs affluence calibration: at affluence=1.0 (mainstream
# suburb), credit share is set just above 50% so the population-
# blended credit/debit hits roughly the Fed 2025 ~35%/30% mix.
# Affluent zones (1.40+) skew credit-heavy; value zones (0.75)
# skew debit-heavy. Slope tuned at Stage 4.6 from 1.6 → 2.5 so the
# emergent per-banner debit lean (D7.5: WDX > ACM) is vivid enough
# to read in the data — at slope 1.6 the gap was only ~3pp after
# gravity-driven cross-shopping smoothing. Slope 2.5 widens the
# per-customer p(credit) spread without shifting population mean
# (still ~54% credit at avg affluence 1.06, on the Fed anchor).
_TENDER_CREDIT_BASE_LOGIT = 0.05   # log-odds at affluence=1.0
_TENDER_CREDIT_SLOPE      = 2.5


def _draw_home_zone(
    rng: np.random.Generator,
    cfg: Config,
    n: int,
) -> np.ndarray:
    ids = [z["id"] for z in cfg.zones]
    weights = np.array([z["residential_weight"] for z in cfg.zones], dtype=float)
    weights = weights / weights.sum()
    idx = rng.choice(len(ids), size=n, p=weights)
    return np.array(ids, dtype=object)[idx]


def _draw_affluence(
    rng: np.random.Generator,
    cfg: Config,
    home_zones: np.ndarray,
) -> np.ndarray:
    """Per-card affluence: Gaussian around zone profile mean with
    a fixed within-zone std (so no zone is monolithic — D13.3).
    Clipped to a plausible range so extreme tails don't break the
    tender / basket layers."""
    zone_aff = {z["id"]: float(z["affluence"]) for z in cfg.zones}
    means = np.array([zone_aff[z] for z in home_zones], dtype=float)
    std = 0.18
    raw = rng.normal(loc=means, scale=std)
    return np.clip(raw, 0.40, 2.00)


def _draw_loyalty_type(rng: np.random.Generator, n: int) -> np.ndarray:
    labels = list(_LOYALTY_SHARES.keys())
    probs = np.array(list(_LOYALTY_SHARES.values()), dtype=float)
    probs = probs / probs.sum()
    idx = rng.choice(len(labels), size=n, p=probs)
    return np.array(labels, dtype=object)[idx]


def _draw_primary_banner(
    rng: np.random.Generator,
    grocery_active_mask: np.ndarray,
) -> np.ndarray:
    """Pick a primary grocer for each grocery-active card. None
    otherwise."""
    n = len(grocery_active_mask)
    out = np.full(n, None, dtype=object)
    idx = np.where(grocery_active_mask)[0]
    if len(idx) == 0:
        return out
    banners = list(_PRIMARY_BANNER_SHARES.keys())
    probs = np.array(list(_PRIMARY_BANNER_SHARES.values()), dtype=float)
    probs = probs / probs.sum()
    pick = rng.choice(len(banners), size=len(idx), p=probs)
    out[idx] = np.array(banners, dtype=object)[pick]
    return out


def _draw_tender(rng: np.random.Generator, affluence: np.ndarray) -> np.ndarray:
    """Affluence → credit propensity. Logistic on (affluence - 1.0).

    Higher affluence → higher credit share. Per-merchant credit/
    debit mix emerges in Layer 6 from which customers shop where.
    This is the D7.5 fix: payment mix is not a per-merchant knob.
    """
    logit = _TENDER_CREDIT_BASE_LOGIT + _TENDER_CREDIT_SLOPE * (affluence - 1.0)
    p_credit = 1.0 / (1.0 + np.exp(-logit))
    u = rng.uniform(size=len(affluence))
    return np.where(u < p_credit, "credit", "debit")


def _draw_network(rng: np.random.Generator, tenders: np.ndarray) -> np.ndarray:
    """Network conditioned on tender. Fed 2025 Diary anchors."""
    out = np.empty(len(tenders), dtype=object)
    for tender, table in (("debit", _DEBIT_NETWORKS), ("credit", _CREDIT_NETWORKS)):
        mask = tenders == tender
        if not mask.any():
            continue
        labels = list(table.keys())
        probs = np.array(list(table.values()), dtype=float)
        probs = probs / probs.sum()
        idx = rng.choice(len(labels), size=int(mask.sum()), p=probs)
        out[mask] = np.array(labels, dtype=object)[idx]
    return out


def _draw_wallet(
    rng: np.random.Generator,
    n: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Returns (wallet_enrolled[bool], wallet_provider[str|None])."""
    enrolled = rng.uniform(size=n) < _WALLET_ENROLL_RATE
    provider = np.full(n, None, dtype=object)
    idx = np.where(enrolled)[0]
    if len(idx) > 0:
        labels = list(_WALLET_PROVIDERS.keys())
        probs = np.array(list(_WALLET_PROVIDERS.values()), dtype=float)
        probs = probs / probs.sum()
        pick = rng.choice(len(labels), size=len(idx), p=probs)
        provider[idx] = np.array(labels, dtype=object)[pick]
    return enrolled, provider


def build_customers(
    cfg: Config,
    population: pd.DataFrame,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Per-card durable state.

    Parameters
    ----------
    population
        Output of Stage 4.2 (one row per card with activity flags).
        Used here for the card_id index and the grocery-activity flag.
    """
    n = len(population)
    # Iterate in card_id order so two runs at the same seed produce
    # identical frames.
    pop_sorted = population.sort_values("card_id", kind="mergesort").reset_index(drop=True)

    home_zone = _draw_home_zone(rng, cfg, n)
    affluence = _draw_affluence(rng, cfg, home_zone)
    loyalty_type = _draw_loyalty_type(rng, n)
    primary_banner = _draw_primary_banner(rng, pop_sorted["active_grocery"].to_numpy())
    tender = _draw_tender(rng, affluence)
    network = _draw_network(rng, tender)
    wallet_enrolled, wallet_provider = _draw_wallet(rng, n)

    df = pd.DataFrame({
        "card_id":          pop_sorted["card_id"].to_numpy(),
        "home_zone":        home_zone,
        "affluence":        affluence,
        "loyalty_type":     loyalty_type,
        "primary_banner":   primary_banner,
        "tender":           tender,
        "network":          network,
        "wallet_enrolled":  wallet_enrolled,
        "wallet_provider":  wallet_provider,
    })
    return df.sort_values("card_id", kind="mergesort").reset_index(drop=True)
