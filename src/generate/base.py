"""Parameterized basket and transaction generator.

All randomness flows from a single ``numpy.random.Generator``. Bimodal basket
sizing, day-of-week and hour-of-day shaping, pay-cycle bumps, promo-day lift,
payment-type sampling (with EBT eligibility filtering), and merchant-specific
affinity rules are wired here.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable

import numpy as np
import pandas as pd

from . import parameters as P

AffinityFn = Callable[[list[str], np.random.Generator], list[str]]


def _build_day_weights(config: dict) -> np.ndarray:
    """Per-day multiplier across the DAYS-long window."""
    peak_days = set(config["peak_days"])
    promo_days = {d for d in P.PROMO_DAYS}
    promo_lift = config["promo_lift"]

    weights = np.empty(P.DAYS, dtype=float)
    for d in range(P.DAYS):
        dt = P.START_DATE + timedelta(days=d)
        w = P.PEAK_DAY_MULT if dt.weekday() in peak_days else P.NONPEAK_DAY_MULT
        if dt.day in P.PAY_CYCLE_DAYS:
            w *= P.PAY_CYCLE_MULTIPLIER
        if dt.isoformat() in promo_days:
            w *= promo_lift
        weights[d] = w
    return weights


def _build_hour_weights(config: dict) -> np.ndarray:
    """Hour-of-day distribution. Peak hours weighted ~3×; overnight near zero."""
    w = np.full(24, 0.5)
    w[0:6] = 0.05
    w[6:10] = 0.4
    w[10:22] = 1.0
    w[22:24] = 0.3
    for h in config["peak_hours"]:
        w[h] = 3.0
    return w / w.sum()


def _is_promo_day_array() -> np.ndarray:
    out = np.zeros(P.DAYS, dtype=bool)
    for s in P.PROMO_DAYS:
        d = datetime.fromisoformat(s).date()
        if P.START_DATE <= d <= P.END_DATE:
            out[(d - P.START_DATE).days] = True
    return out


def _sample_card_network(rng: np.random.Generator, payment: str) -> str | None:
    if payment == "credit":
        keys = list(P.CREDIT_NETWORKS)
        return str(rng.choice(keys, p=list(P.CREDIT_NETWORKS.values())))
    if payment == "debit":
        keys = list(P.DEBIT_NETWORKS)
        return str(rng.choice(keys, p=list(P.DEBIT_NETWORKS.values())))
    return None


def _sample_entry_mode(rng: np.random.Generator, payment: str) -> str:
    if payment == "cash":
        return "cash"
    if payment == "ebt":
        return str(rng.choice(["chip", "swipe"], p=[0.7, 0.3]))
    keys = list(P.ENTRY_MODE)
    return str(rng.choice(keys, p=list(P.ENTRY_MODE.values())))


def generate_merchant_transactions(
    customers: pd.DataFrame,
    catalog: pd.DataFrame,
    stores: pd.DataFrame,
    config: dict,
    affinity_fn: AffinityFn,
    rng: np.random.Generator,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Generate transactions and line items for one merchant.

    Returns (transactions_df, items_df). Both share the merchant's ``txn_id``
    space (prefixed with ``merchant_id``).
    """
    merchant_id = config["merchant_id"]

    # ---- Pick participating customers ----
    n_part = int(round(P.N_CUSTOMERS * config["participation_rate"]))
    pan_pool = customers["customer_pan"].to_numpy()
    chosen_idx = rng.choice(len(pan_pool), size=n_part, replace=False)
    part_customers = customers.iloc[chosen_idx].reset_index(drop=True)
    pans = part_customers["customer_pan"].to_numpy()
    is_lapser = part_customers["is_lapser"].to_numpy().astype(bool)
    behavioral = part_customers["behavioral_segment"].to_numpy()

    # ---- Per-customer transaction count (Poisson; lapsers clipped to 1-2) ----
    rate = config["txn_per_week"] * (P.DAYS / 7.0)
    n_per_cust = rng.poisson(rate, size=n_part)
    # Stockers slightly higher rate (10%); fillers slightly lower
    stocker_mask = behavioral == "stocker"
    n_per_cust[stocker_mask] = rng.poisson(rate * 1.10, size=int(stocker_mask.sum()))
    n_per_cust[is_lapser] = rng.choice([1, 2], size=int(is_lapser.sum()), p=[0.6, 0.4])
    n_per_cust = np.maximum(n_per_cust, 0)

    n_txns = int(n_per_cust.sum())
    if n_txns == 0:
        return pd.DataFrame(), pd.DataFrame()

    # ---- Customer + behavioral arrays expanded to per-transaction ----
    txn_pan = np.repeat(pans, n_per_cust)
    txn_seg = np.repeat(behavioral, n_per_cust)

    # ---- Day-of-window sampling ----
    day_weights = _build_day_weights(config)
    day_p = day_weights / day_weights.sum()
    day_idx = rng.choice(P.DAYS, size=n_txns, p=day_p)

    # ---- Hour-of-day sampling ----
    hour_p = _build_hour_weights(config)
    hours = rng.choice(24, size=n_txns, p=hour_p)
    minutes = rng.integers(0, 60, size=n_txns)
    seconds = rng.integers(0, 60, size=n_txns)

    # ---- Stores ----
    store_ids = rng.choice(stores["store_id"].to_numpy(), size=n_txns)

    # ---- Payment types ----
    pay_keys = list(config["payment_mix"].keys())
    pay_probs = list(config["payment_mix"].values())
    payment_arr = rng.choice(pay_keys, size=n_txns, p=pay_probs)

    # ---- Card networks, wallet, entry mode (vectorize where easy) ----
    card_networks: list[str | None] = [None] * n_txns
    wallet_types: list[str | None] = [None] * n_txns
    entry_modes: list[str] = [""] * n_txns
    wallet_share = config["wallet_share"]
    for i in range(n_txns):
        p = payment_arr[i]
        card_networks[i] = _sample_card_network(rng, p)
        entry_modes[i] = _sample_entry_mode(rng, p)
        if p in ("credit", "debit") and rng.random() < wallet_share:
            wallet_types[i] = str(rng.choice(P.WALLET_PROVIDERS))

    # ---- Build txn_id array ----
    txn_ids = np.array([f"{merchant_id}-{i:07d}" for i in range(n_txns)])

    # ---- Build timestamps ----
    base_dates = np.array([P.START_DATE + timedelta(days=int(d)) for d in day_idx])
    timestamps = [
        f"{d.isoformat()} {h:02d}:{m:02d}:{s:02d}"
        for d, h, m, s in zip(base_dates, hours, minutes, seconds)
    ]

    # ---- SKU pools (separate EBT-eligible pool when ebt_eligible column present) ----
    sku_arr_full = catalog["sku"].to_numpy()
    price_arr_full = catalog["base_price"].to_numpy()
    if "ebt_eligible" in catalog.columns:
        ebt_mask = catalog["ebt_eligible"].to_numpy() == 1
        sku_arr_ebt = catalog.loc[ebt_mask, "sku"].to_numpy()
        price_arr_ebt = catalog.loc[ebt_mask, "base_price"].to_numpy()
    else:
        sku_arr_ebt = sku_arr_full
        price_arr_ebt = price_arr_full

    sku_to_price = dict(zip(catalog["sku"].tolist(), catalog["base_price"].tolist()))
    is_promo_day = _is_promo_day_array()

    avg_basket = config["avg_basket_size"]

    # ---- Item generation (per-transaction loop; numpy used inside) ----
    all_txn_ids: list[str] = []
    all_line_ids: list[int] = []
    all_skus: list[str] = []
    all_qtys: list[int] = []
    all_unit_prices: list[float] = []
    all_discounts: list[float] = []
    all_line_totals: list[float] = []
    txn_totals = np.zeros(n_txns, dtype=float)

    for i in range(n_txns):
        # Bimodal basket size: 70% filler-style, 30% stocker-style.
        # Stockers skew toward the larger mode (60/40).
        is_stocker_mode = rng.random() < (0.4 if txn_seg[i] == "stocker" else 0.7)
        if is_stocker_mode:
            bs_raw = rng.normal(avg_basket * 0.6, avg_basket * 0.2)
            bs = max(1, int(bs_raw))
        else:
            bs_raw = rng.normal(avg_basket * 2.5, avg_basket * 0.5)
            bs = max(2, int(bs_raw))

        if payment_arr[i] == "ebt":
            pool_sku = sku_arr_ebt
            pool_price = price_arr_ebt
        else:
            pool_sku = sku_arr_full
            pool_price = price_arr_full

        chosen = rng.integers(0, len(pool_sku), size=bs)
        basket: list[str] = list(pool_sku[chosen])
        basket = affinity_fn(basket, rng)

        promo = is_promo_day[day_idx[i]]
        for line_id, sku in enumerate(basket, start=1):
            base_price = sku_to_price[sku]
            unit_price = round(base_price * (1.0 + (rng.random() * 2 - 1) * P.PRICE_NOISE_FRAC), 2)
            qty = 1
            discount = 0.0
            if promo and rng.random() < P.PROMO_DISCOUNT_PROB:
                discount = round(unit_price * qty * P.PROMO_DISCOUNT_RATE, 2)
            line_total = round(unit_price * qty - discount, 2)
            all_txn_ids.append(txn_ids[i])
            all_line_ids.append(line_id)
            all_skus.append(sku)
            all_qtys.append(qty)
            all_unit_prices.append(unit_price)
            all_discounts.append(discount)
            all_line_totals.append(line_total)
            txn_totals[i] += line_total

    items_df = pd.DataFrame({
        "txn_id":     all_txn_ids,
        "line_id":    all_line_ids,
        "sku":        all_skus,
        "qty":        all_qtys,
        "unit_price": all_unit_prices,
        "discount":   all_discounts,
        "line_total": all_line_totals,
    })

    transactions_df = pd.DataFrame({
        "txn_id":       txn_ids,
        "merchant_id":  merchant_id,
        "customer_pan": txn_pan,
        "store_id":     store_ids,
        "txn_ts":       timestamps,
        "payment_type": payment_arr,
        "card_network": card_networks,
        "entry_mode":   entry_modes,
        "wallet_type":  wallet_types,
        "txn_total":    np.round(txn_totals, 2),
    })

    return transactions_df, items_df
