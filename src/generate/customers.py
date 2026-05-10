"""Shared customer panel.

Runs ONCE. Produces 10,000 customers with `customer_id` (16-char SHA-256,
generated directly here — no PAN, no PII), `home_zip5` (Charlotte metro),
`behavioral_segment`, `grocer_affinity_type`, `primary_grocer`,
`secondary_grocer`, `primary_card_type`, `has_mobile_wallet`,
`signup_date`. The cross-merchant invariant: a given physical customer's
`customer_id` is the same value at every merchant.
"""
from __future__ import annotations

import hashlib
from datetime import timedelta

import numpy as np
import pandas as pd

from . import parameters as P


def _customer_id_for_pan(pan: str) -> str:
    """16-char SHA-256 of HASH_SECRET + pan. Same hash function the v2
    anonymize stage used; produced directly here in v2.5 so PAN never
    lives on disk."""
    return hashlib.sha256(
        (P.HASH_SECRET + str(pan)).encode("utf-8")
    ).hexdigest()[:16]


def _assign_affinity(rng: np.random.Generator, n: int) -> np.ndarray:
    """Sample grocer_affinity_type per the (55/30/12/3) shares."""
    keys = list(P.V2_5_GROCER_AFFINITY_SHARES.keys())
    probs = list(P.V2_5_GROCER_AFFINITY_SHARES.values())
    return rng.choice(keys, size=n, p=probs)


def _assign_primary_grocer(rng: np.random.Generator, n: int) -> np.ndarray:
    """Primary grocer ∈ {KRG, ACM, WDX} proportional to store count."""
    keys = list(P.V2_5_PRIMARY_GROCER_SHARES.keys())
    probs = list(P.V2_5_PRIMARY_GROCER_SHARES.values())
    return rng.choice(keys, size=n, p=probs)


def _assign_secondary_grocer(
    primary: np.ndarray,
    affinity: np.ndarray,
    rng: np.random.Generator,
) -> list[str | None]:
    """Splitters and three_chain shoppers get a secondary grocer different
    from primary. Loyalists and lapsed_light leave it null."""
    grocers = list(P.V2_5_PRIMARY_GROCER_SHARES.keys())
    out: list[str | None] = []
    for prim, aff in zip(primary, affinity):
        if aff in ("splitter", "three_chain"):
            choices = [g for g in grocers if g != prim]
            out.append(str(rng.choice(choices)))
        else:
            out.append(None)
    return out


def generate_customers(rng: np.random.Generator) -> pd.DataFrame:
    """Build the shared 10K-customer panel.

    Parameters
    ----------
    rng : numpy.random.Generator
        Seeded generator. Reproducibility is required and tested.
    """
    n = P.V2_5_N_CUSTOMERS

    # ---- Geography: pick a home ZIP from the Charlotte panel uniformly ----
    metro_zips = P.V2_5_METRO_ZIPS
    home_zip5 = rng.choice(metro_zips, size=n)

    # ---- Behavioral / affinity attributes ----
    segment = rng.choice(["filler", "stocker"], size=n, p=[0.70, 0.30])
    affinity = _assign_affinity(rng, n)
    primary_grocer = _assign_primary_grocer(rng, n)
    secondary_grocer = _assign_secondary_grocer(primary_grocer, affinity, rng)

    # ---- Card preferences ----
    primary_card = rng.choice(
        ["credit", "debit", "mixed"], size=n, p=[0.55, 0.35, 0.10]
    )
    has_wallet = rng.choice([0, 1], size=n, p=[0.45, 0.55]).astype(int)

    # ---- Signup dates: random offset up to 5 years before window end ----
    signup_offsets = rng.integers(0, 365 * 5, size=n)
    signup_dates = [
        (P.V2_5_END_DATE - timedelta(days=int(d))).isoformat()
        for d in signup_offsets
    ]

    # ---- customer_id: 16-char SHA-256 of HASH_SECRET + synthetic PAN ----
    # The synthetic PAN is internal and never written to disk; it exists
    # only so the hash domain is stable and matches the v2 derivation
    # (allowing the cross-merchant invariant to be tested end-to-end).
    pan_array = rng.integers(low=10**15, high=10**16, size=n, dtype=np.int64)
    customer_id = [_customer_id_for_pan(str(p)) for p in pan_array]

    return pd.DataFrame({
        "customer_id":           customer_id,
        "home_zip5":             home_zip5,
        "behavioral_segment":    segment,
        "grocer_affinity_type":  affinity,
        "primary_grocer":        primary_grocer,
        "secondary_grocer":      secondary_grocer,
        "primary_card_type":     primary_card,
        "has_mobile_wallet":     has_wallet,
        "signup_date":           signup_dates,
    })
