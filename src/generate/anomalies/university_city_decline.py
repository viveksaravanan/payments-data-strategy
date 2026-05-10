"""Anomaly: University City traffic decline (Apr 12 – May 29, 2026).

Four-stage ramp on the three grocers' University City stores
(neighborhood ``"University City"``):

    | Stage | Window           | Multiplier | Narrative                |
    |-------|------------------|-----------:|--------------------------|
    | 1     | Apr 12 – Apr 18  | 1.10       | Finals stress shopping   |
    | 2     | Apr 19 – Apr 25  | 0.85       | Mixed during move-out    |
    | 3     | Apr 26 – May 2   | 0.55       | Full crash               |
    | 4     | May 3  – May 29  | 0.65       | Summer stable            |

Per-grocer magnitude (locked in the design): Kroger UC takes the full
hit (1.0×); Acme UC takes 80% of the swing; Winn-Dixie UC takes 70%.
The "magnitude" scales the deviation from baseline, so the per-grocer
effective multiplier is::

    effective = 1 - magnitude * (1 - stage_multiplier)

For Kroger at peak (stage 3): 1 - 1.00 * (1 - 0.55) = 0.55× → ~45% drop.
For Acme   at peak: 1 - 0.80 * (1 - 0.55) = 0.64× → ~36% drop.
For WDX    at peak: 1 - 0.70 * (1 - 0.55) = 0.685× → ~32% drop.

The boost stage (1.10×) is implemented as keep-prob 1.0 (no extra
trips are injected); the dominant signal is the dropoff in stages 2-4.

The transaction generator calls ``uc_trip_keep_probability`` after it
picks a store for a grocery trip and draws ``rng.random()`` against
the result; on miss, the trip is dropped before any line items get
generated.
"""
from __future__ import annotations

from datetime import date

UC_NEIGHBORHOOD = "University City"

# Per-stage (start_inclusive, end_inclusive, base_multiplier).
STAGES: list[tuple[date, date, float]] = [
    (date(2026, 4, 12), date(2026, 4, 18), 1.10),  # finals stress
    (date(2026, 4, 19), date(2026, 4, 25), 0.85),  # mixed move-out
    (date(2026, 4, 26), date(2026, 5,  2), 0.55),  # full crash
    (date(2026, 5,  3), date(2026, 5, 29), 0.65),  # summer stable
]

# Per-grocer magnitude (scales the deviation from 1.0).
PER_GROCER_MAGNITUDE: dict[str, float] = {
    "KRG": 1.00,
    "ACM": 0.80,
    "WDX": 0.70,
}


def _stage_multiplier(txn_date: date) -> float | None:
    for s, e, m in STAGES:
        if s <= txn_date <= e:
            return m
    return None


def uc_trip_keep_probability(
    merchant_id: str,
    neighborhood: str,
    txn_date: date,
) -> float:
    """Probability of keeping a grocery trip at this store on this day.

    Returns 1.0 (no anomaly) for non-UC stores, non-grocer merchants,
    and dates outside the affected window. Effective multipliers are
    capped to [0.0, 1.0].
    """
    if neighborhood != UC_NEIGHBORHOOD:
        return 1.0
    if merchant_id not in PER_GROCER_MAGNITUDE:
        return 1.0
    base = _stage_multiplier(txn_date)
    if base is None:
        return 1.0
    effective = 1.0 - PER_GROCER_MAGNITUDE[merchant_id] * (1.0 - base)
    return max(0.0, min(1.0, effective))
