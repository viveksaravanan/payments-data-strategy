"""Global generation parameters and per-merchant configs.

Source of truth for the generator. Mirrors `DATA.md` §2 and §3.
"""
from datetime import date, timedelta

# Panel size and time window
N_CUSTOMERS = 5_000
DAYS = 90
END_DATE = date(2026, 5, 5)
START_DATE = END_DATE - timedelta(days=DAYS - 1)

# Promotional events (specific dates that lift transaction count)
PROMO_DAYS = ["2026-04-15", "2026-04-22", "2026-05-01"]

# Anomaly injection (Kroger only — see DATA.md §9)
ANOMALY_INJECT = True

# Reproducibility
RANDOM_SEED = 42

# Anonymization (consumed in src/anonymize/)
HASH_SECRET = "demo-only-not-a-real-secret"
K_ANONYMITY_THRESHOLD = 5
ZIP_TRUNCATION = 3
TIME_BUCKET_HOURS = 1

# Behavioral segments
SEGMENT_FILLER_SHARE = 0.70
SEGMENT_STOCKER_SHARE = 0.30
SEGMENT_LAPSER_SHARE = 0.05  # overlapping

# Pay-cycle bumps (1st-3rd, 15th-17th of each month)
PAY_CYCLE_DAYS = {1, 2, 3, 15, 16, 17}
PAY_CYCLE_MULTIPLIER = 1.15

# Day-of-week multipliers
PEAK_DAY_MULT = 1.4
NONPEAK_DAY_MULT = 0.9

# Card-network sub-distributions (within payment_type)
CREDIT_NETWORKS = {"visa": 0.60, "mc": 0.25, "amex": 0.10, "discover": 0.05}
DEBIT_NETWORKS = {"visa": 0.65, "mc": 0.35}

# Mobile wallet providers (uniform when wallet used)
WALLET_PROVIDERS = ["apple", "google", "samsung"]

# Entry mode for card transactions
ENTRY_MODE = {"contactless": 0.65, "chip": 0.30, "swipe": 0.05}

# Per-transaction price noise and promo-day discount knobs
PRICE_NOISE_FRAC = 0.10        # ±10% on unit_price
PROMO_DISCOUNT_RATE = 0.15     # 15% off base for promoted SKUs
PROMO_SKU_FRACTION = 0.15      # fraction of catalog SKUs flagged on-promo per promo day
# Legacy alias (some older code paths may import the old name)
PROMO_DISCOUNT_PROB = PROMO_SKU_FRACTION


MERCHANT_CONFIGS = {
    "kroger": {
        "merchant_id":         "KRG",
        "name":                "Kroger",
        "segment":             "grocery",
        "mcc":                 "5411",
        "n_stores":            25,
        "participation_rate":  0.90,
        "txn_per_week":        1.2,
        "avg_basket_size":     12,
        "avg_ticket":          65.00,
        "promo_lift":          1.6,
        "payment_mix":         {"credit": 0.55, "debit": 0.30, "ebt": 0.10, "cash": 0.05},
        "wallet_share":        0.20,
        "n_skus":              1500,
        "organic_share":       0.18,
        "peak_days":           [5, 6],            # Sat, Sun (Mon=0)
        "peak_hours":          [10, 11, 17, 18, 19],
        # basket-share weights — probability that any given basket-item is from
        # this category. Demand-weighted (not catalog-size-weighted) so small
        # catalogs like PET / BABY don't over-represent in the top-revenue
        # leaderboard. Tuned: BABY/PET pulled down (high unit prices were
        # pushing them into the top-4 by revenue despite low basket share);
        # HOUSEHOLD/PERSONAL trimmed; the slack reallocated to the
        # high-frequency staples (PRODUCE / DAIRY / PANTRY). Sum to 1.00.
        "category_weights": {
            "PRODUCE":   0.15,
            "DAIRY":     0.13,
            "BAKERY":    0.05,
            "MEAT":      0.11,
            "FROZEN":    0.08,
            "PANTRY":    0.18,
            "SNACKS":    0.07,
            "BEVERAGES": 0.10,
            "HOUSEHOLD": 0.05,
            "PERSONAL":  0.04,
            "BABY":      0.02,
            "PET":       0.02,
        },
    },
    "taco_bell": {
        "merchant_id":         "TBL",
        "name":                "Taco Bell",
        "segment":             "qsr",
        "mcc":                 "5814",
        "n_stores":            40,
        "participation_rate":  0.60,
        "txn_per_week":        0.8,
        "avg_basket_size":     3,
        "avg_ticket":          9.00,
        "promo_lift":          1.3,
        "payment_mix":         {"credit": 0.50, "debit": 0.40, "cash": 0.10},
        "wallet_share":        0.30,
        "n_skus":              60,
        "organic_share":       0.0,
        "peak_days":           [4, 5],            # Fri, Sat
        "peak_hours":          [12, 13, 19, 20, 21],
    },
    "tjmaxx": {
        "merchant_id":         "TJX",
        "name":                "TJ Maxx",
        "segment":             "retail_offprice",
        "mcc":                 "5651",
        "n_stores":            15,
        "participation_rate":  0.40,
        "txn_per_week":        0.35,
        "avg_basket_size":     4,
        "avg_ticket":          55.00,
        "promo_lift":          1.2,
        "payment_mix":         {"credit": 0.70, "debit": 0.25, "cash": 0.05},
        "wallet_share":        0.15,
        "n_skus":              200,
        "organic_share":       0.0,
        "peak_days":           [5, 6],            # Sat, Sun
        "peak_hours":          [11, 12, 13, 14, 15],
    },
}
