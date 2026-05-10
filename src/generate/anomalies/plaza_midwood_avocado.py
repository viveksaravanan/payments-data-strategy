"""Anomaly: Plaza Midwood Kroger avocado quantity spike (Apr 21–24, 2026).

Four-day pattern; Kroger Plaza Midwood (28205) only. Acme and
Winn-Dixie Plaza Midwood stores show normal levels.

    | Date         | Multiplier on avocado selection |
    |--------------|---------------------------------|
    | Apr 21       | 1.5×                            |
    | Apr 22       | 5.0× (peak)                     |
    | Apr 23       | 3.0×                            |
    | Apr 24       | 1.5×                            |

The transaction generator weights avocado SKUs (subcategory
``fresh_fruit``, name containing ``avocado``) inside the PRODUCE
category by this multiplier when the trip's merchant + neighborhood +
date match. Outside the window, or at non-Plaza-Midwood-Kroger
stores, the multiplier is 1.0 and SKU selection within PRODUCE stays
uniform.
"""
from __future__ import annotations

from datetime import date

KROGER = "KRG"
PLAZA_MIDWOOD_NEIGHBORHOOD = "Plaza Midwood"
AVOCADO_SUBSTRING = "avocado"
AVOCADO_CATEGORY = "PRODUCE"

DAILY_MULTIPLIER: dict[date, float] = {
    date(2026, 4, 21): 1.5,
    date(2026, 4, 22): 5.0,
    date(2026, 4, 23): 3.0,
    date(2026, 4, 24): 1.5,
}


def avocado_multiplier(
    merchant_id: str,
    neighborhood: str,
    txn_date: date,
) -> float:
    """Multiplier on avocado-SKU selection probability inside PRODUCE.

    Returns 1.0 (no anomaly) for any non-Kroger merchant, any
    non-Plaza-Midwood neighborhood, or any date outside the four-day
    pattern.
    """
    if merchant_id != KROGER:
        return 1.0
    if neighborhood != PLAZA_MIDWOOD_NEIGHBORHOOD:
        return 1.0
    return DAILY_MULTIPLIER.get(txn_date, 1.0)
