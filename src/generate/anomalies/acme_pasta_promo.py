"""Anomaly: coordinated pasta promos with divergent outcomes.

Three pinned tenant_promotions rows (one per grocer) and a per-grocer
basket-level pasta multiplier active during the promo window:

    | Merchant   | Window           | Discount | SKU target | Basket lift |
    |------------|------------------|---------:|-----------:|------------:|
    | Kroger     | Apr 15 – Apr 21  | 25%      | up to 25   | 2.2× (lift) |
    | Acme       | Apr 19 – Apr 25  | 20%      | up to 20   | 0.8× (fail) |
    | Winn-Dixie | Apr 22 – Apr 28  | 15%      | up to 12   | 1.4× (lift) |

The promo provides a price discount when a pasta SKU is purchased
during the window. The basket multiplier biases SKU selection inside
the PANTRY category so pasta SKUs are chosen at ``multiplier`` times
the baseline rate. Acme's <1.0× multiplier is the planted "failure"
— the promo discount is in place, but pasta sales lag during the
window, demonstrating that promo runs do not mechanically lift
volume.

The generator calls ``pasta_multiplier(merchant_id, txn_date)`` per
trip and applies it to pasta-SKU weights when sampling within
PANTRY. Pinned promos are appended to ``tenant_promotions`` via
``pinned_pasta_promo`` from each grocer's ``build()``; the
sampler-driven random promo count is reduced by 1 per grocer so the
total count per merchant stays at the design target (KRG 25, ACM 20,
WDX 18).
"""
from __future__ import annotations

from datetime import date

import pandas as pd

PASTA_SUBCATEGORY = "pasta"
PASTA_CATEGORY = "PANTRY"

# (start_inclusive, end_inclusive, basket_multiplier, discount_pct,
# sku_count_cap, promo_name)
PASTA_RULES: dict[str, dict] = {
    "KRG": {
        "window":       (date(2026, 4, 15), date(2026, 4, 21)),
        "multiplier":   2.2,
        "discount_pct": 0.25,
        "sku_cap":      25,
        "promo_name":   "Kroger Pasta Sale",
        "promo_type":   "weekly_ad",
    },
    "ACM": {
        "window":       (date(2026, 4, 19), date(2026, 4, 25)),
        "multiplier":   0.8,
        "discount_pct": 0.20,
        "sku_cap":      20,
        "promo_name":   "Acme Spring Pasta Sale",
        "promo_type":   "weekly_ad",
    },
    "WDX": {
        "window":       (date(2026, 4, 22), date(2026, 4, 28)),
        "multiplier":   1.4,
        "discount_pct": 0.15,
        "sku_cap":      12,
        "promo_name":   "Winn-Dixie Pasta Sale",
        "promo_type":   "weekly_ad",
    },
}


def pasta_multiplier(merchant_id: str, txn_date: date) -> float:
    """Multiplier on pasta-SKU selection probability inside PANTRY."""
    rule = PASTA_RULES.get(merchant_id)
    if rule is None:
        return 1.0
    s, e = rule["window"]
    if s <= txn_date <= e:
        return float(rule["multiplier"])
    return 1.0


def pinned_pasta_promo(
    catalog: pd.DataFrame, merchant_id: str, promo_id: str
) -> pd.DataFrame:
    """Build the pinned pasta promo rows for one grocer.

    Returns a DataFrame in ``tenant_promotions`` shape with one row
    per pasta SKU, capped at ``sku_cap``. ``promo_id`` is supplied by
    the caller (so it slots into the merchant's existing
    ``-PROMO-NNNN`` numbering with no collisions).
    """
    rule = PASTA_RULES.get(merchant_id)
    if rule is None:
        return pd.DataFrame()
    pasta_skus = catalog.loc[
        catalog["subcategory"] == PASTA_SUBCATEGORY, "sku"
    ].tolist()
    pasta_skus = pasta_skus[: rule["sku_cap"]]
    s, e = rule["window"]
    rows = [
        {
            "promo_id":     promo_id,
            "merchant_id":  merchant_id,
            "sku":          sku,
            "start_date":   s.isoformat(),
            "end_date":     e.isoformat(),
            "discount_pct": rule["discount_pct"],
            "promo_name":   rule["promo_name"],
            "promo_type":   rule["promo_type"],
        }
        for sku in pasta_skus
    ]
    return pd.DataFrame(rows)
