"""Layer 6 — Payment (D18).

Three per-transaction fields. The big architectural point (D7.5):
entry mode and wallet-at-tap **emerge** from customer state +
segment + daypart — they are not independent per-segment draws as
in v3. Tender/network are already locked at Layer 3 (D16.3 — one
card per customer).

* **D18.1 entry mode** — segment baseline shifted by the customer's
  D16 wallet_enrolled flag. Wallet-enrolled customers contactless-
  lean noticeably; the per-store entry-mode mix then emerges from
  who shops there.
* **D18.2 wallet-at-tap** — only set when entry_mode='contactless'
  AND the customer is wallet-enrolled. Gating probability tuned so
  population-blended mobile-wallet share lands in D18.2's 16-20%
  band. Provider carried over from the customer's enrollment.
* **D18.3 connectivity** — per-segment terminal form factor.
  Countertop-heavy (grocery, off-price) → wifi+ethernet majority.
  QSR (counter+drive-thru) → more cellular.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.generate.config.loader import Config


# ----- D18.1 entry mode baselines ----------------------------------
# Distribution over {contactless, chip, swipe, manual} for a
# non-wallet-enrolled customer at each segment. Wallet-enrolled
# customers shift to contactless by the boost below.

_ENTRY_MODE_BASE: dict[str, dict[str, float]] = {
    "grocery":   {"contactless": 0.45, "chip": 0.45, "swipe": 0.09, "manual": 0.01},
    "qsr":       {"contactless": 0.55, "chip": 0.35, "swipe": 0.07, "manual": 0.03},
    "off_price": {"contactless": 0.40, "chip": 0.49, "swipe": 0.10, "manual": 0.01},
}
# Boost from no-wallet → wallet: contactless gains, chip loses.
_WALLET_CONTACTLESS_BOOST = 0.15


def _entry_mode_distribution(segment: str, wallet_enrolled: bool) -> dict[str, float]:
    base = dict(_ENTRY_MODE_BASE[segment])
    if wallet_enrolled:
        shift = min(_WALLET_CONTACTLESS_BOOST, base["chip"])
        base["contactless"] += shift
        base["chip"] -= shift
    return base


# ----- D18.2 wallet-at-tap -----------------------------------------
# Among wallet-enrolled customers paying contactless, this fraction
# uses the phone/watch tap. Tuned so blended share lands in 16-20%
# of all transactions.
_WALLET_TAP_PROB = 0.60


# ----- D18.3 connectivity by segment -------------------------------

_CONNECTIVITY_BY_SEGMENT: dict[str, dict[str, float]] = {
    "grocery":   {"wifi": 0.60, "ethernet": 0.35, "cellular": 0.05},
    "qsr":       {"wifi": 0.45, "ethernet": 0.20, "cellular": 0.35},
    "off_price": {"wifi": 0.60, "ethernet": 0.35, "cellular": 0.05},
}


def _sample_categorical_batch(
    rng: np.random.Generator,
    n: int,
    labels: list[str],
    probs: np.ndarray,
) -> np.ndarray:
    probs = probs / probs.sum()
    idx = rng.choice(len(labels), size=n, p=probs)
    return np.array(labels, dtype=object)[idx]


def build_payment(
    cfg: Config,
    trips: pd.DataFrame,
    customers: pd.DataFrame,
    stores: pd.DataFrame,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Per-trip payment fields. Returns one row per trip with
    ``[trip_id, entry_mode, wallet_at_tap, wallet_provider,
    connectivity_type]``.
    """
    cust_idx = customers.set_index("card_id")[
        ["wallet_enrolled", "wallet_provider"]
    ]
    # Join customer payment-related fields onto the trip frame.
    joined = trips[["trip_id", "card_id", "segment"]].merge(
        cust_idx.reset_index(), on="card_id", how="left",
    )
    n = len(joined)

    # ----- entry_mode by (segment × wallet_enrolled) group -----
    entry_mode = np.empty(n, dtype=object)
    for segment in ("grocery", "qsr"):        # off-price dropped (datamodel-v2)
        for wallet in (False, True):
            mask = (
                (joined["segment"].to_numpy() == segment)
                & (joined["wallet_enrolled"].to_numpy() == wallet)
            )
            count = int(mask.sum())
            if count == 0:
                continue
            dist = _entry_mode_distribution(segment, wallet)
            labels = list(dist.keys())
            probs = np.array(list(dist.values()), dtype=float)
            entry_mode[mask] = _sample_categorical_batch(rng, count, labels, probs)

    # ----- wallet-at-tap -----
    can_tap = (entry_mode == "contactless") & joined["wallet_enrolled"].to_numpy()
    tap_draw = rng.uniform(size=n)
    wallet_at_tap = can_tap & (tap_draw < _WALLET_TAP_PROB)
    # Wallet provider only when wallet_at_tap; else null.
    wallet_provider = np.where(
        wallet_at_tap, joined["wallet_provider"].to_numpy(), None,
    )

    # ----- connectivity by segment -----
    connectivity = np.empty(n, dtype=object)
    for segment in ("grocery", "qsr"):        # off-price dropped (datamodel-v2)
        mask = joined["segment"].to_numpy() == segment
        count = int(mask.sum())
        if count == 0:
            continue
        dist = _CONNECTIVITY_BY_SEGMENT[segment]
        labels = list(dist.keys())
        probs = np.array(list(dist.values()), dtype=float)
        connectivity[mask] = _sample_categorical_batch(rng, count, labels, probs)

    df = pd.DataFrame({
        "trip_id":           joined["trip_id"].to_numpy(),
        "entry_mode":        entry_mode,
        "wallet_at_tap":     wallet_at_tap,
        "wallet_provider":   wallet_provider,
        "connectivity_type": connectivity,
    })
    return df.sort_values("trip_id", kind="mergesort").reset_index(drop=True)
