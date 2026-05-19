"""Generator parameters per `V2_5_DATA_DESIGN.md`.

Phase 4 collapses the v2/v2.5 dual namespace that lived here through
Phases 0–3 — there is now a single canonical set of names. Layer 4
generator constants (trip frequency, active-weeks variance, basket
archetypes, per-category quantity distributions, payment generation)
live alongside the panel constants and per-merchant configs.

If you need to revisit any of these, the design doc section is named
in the comment for the constant.
"""
from __future__ import annotations

from datetime import date

# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------

RANDOM_SEED = 42

# ---------------------------------------------------------------------------
# Hash secret — used by `customers.py` to derive `customer_id` from a
# never-persisted synthetic PAN.
# ---------------------------------------------------------------------------

HASH_SECRET = "demo-only-not-a-real-secret"

# ---------------------------------------------------------------------------
# Panel and time window — Layer 2
# ---------------------------------------------------------------------------

N_CUSTOMERS = 10_000
START_DATE = date(2026, 3, 1)
END_DATE = date(2026, 5, 29)
DAYS = (END_DATE - START_DATE).days + 1  # 90

# Backward-compat aliases retained while the code switches to the
# unprefixed names; remove once nothing imports them.
V2_5_N_CUSTOMERS = N_CUSTOMERS
V2_5_START_DATE = START_DATE
V2_5_END_DATE = END_DATE
V2_5_DAYS = DAYS

# ---------------------------------------------------------------------------
# Charlotte metro neighborhood map — Layer 2
# ---------------------------------------------------------------------------

NEIGHBORHOODS: dict[str, tuple[str, list[str]]] = {
    "Uptown / Center City": ("urban_core",     ["28202"]),
    "Plaza Midwood":        ("urban_core",     ["28205"]),
    "NoDa":                 ("urban_core",     ["28206"]),
    "Dilworth":             ("urban_core",     ["28203"]),
    "SouthPark":            ("inner_suburbs",  ["28210", "28211"]),
    "Ballantyne":           ("inner_suburbs",  ["28277"]),
    "University City":      ("inner_suburbs",  ["28213", "28223"]),
    "Matthews":             ("outer_suburbs",  ["28104", "28105"]),
    "Huntersville":         ("outer_suburbs",  ["28078"]),
    "Pineville":            ("outer_suburbs",  ["28134"]),
    "Concord":              ("outer_suburbs",  ["28025", "28027"]),
    "Mooresville":          ("outer_suburbs",  ["28115", "28117"]),
}
METRO_ZIPS: list[str] = sorted(
    {z for _, zips in NEIGHBORHOODS.values() for z in zips}
)
ZIP5_TO_NEIGHBORHOOD: dict[str, tuple[str, str]] = {
    z: (neighborhood, region)
    for neighborhood, (region, zips) in NEIGHBORHOODS.items()
    for z in zips
}

# Backward-compat aliases.
V2_5_NEIGHBORHOODS = NEIGHBORHOODS
V2_5_METRO_ZIPS = METRO_ZIPS
V2_5_ZIP5_TO_NEIGHBORHOOD = ZIP5_TO_NEIGHBORHOOD

# ---------------------------------------------------------------------------
# Customer affinity model — Layer 2
# ---------------------------------------------------------------------------

GROCER_AFFINITY_SHARES = {
    "loyalist":     0.55,
    "splitter":     0.30,
    "three_chain":  0.12,
    "lapsed_light": 0.03,
}
PRIMARY_GROCER_SHARES = {  # proportional to grocer store count (30/25/20)
    "KRG": 30 / 75,
    "ACM": 25 / 75,
    "WDX": 20 / 75,
}
V2_5_GROCER_AFFINITY_SHARES = GROCER_AFFINITY_SHARES
V2_5_PRIMARY_GROCER_SHARES = PRIMARY_GROCER_SHARES

# ---------------------------------------------------------------------------
# Layer 4 generator constants
# ---------------------------------------------------------------------------

# Step 4a: grocery trip frequency per (segment, affinity).
# Values are inclusive [lo, hi] ranges. Trip count is sampled uniformly.
TRIP_FREQUENCY_GROCERY: dict[tuple[str, str], tuple[int, int]] = {
    ("filler", "loyalist"):     (18, 24),
    ("filler", "splitter"):     (20, 28),
    ("filler", "three_chain"):  (22, 30),
    ("stocker", "loyalist"):    (8, 14),
    ("stocker", "splitter"):    (10, 16),
    ("stocker", "three_chain"): (12, 18),
}

# Step 4a: lapsed customer trip distribution (50/30/20 of the lapsed cohort).
# Each entry: (probability, (lo, hi)) — lo=hi=0 for inactive.
LAPSED_TRIP_BUCKETS = [
    (0.50, (0, 0)),    # truly inactive
    (0.30, (1, 2)),    # brief activity (clustered)
    (0.20, (1, 2)),    # occasional (spread)
]

# Step 4a: QSR (Taco Bell) trip count distribution.
# Phase 1.6 Pass 1: three-bucket gradient — 30% fans (6–12 trips),
# 40% occasional (2–5 trips), 30% rare (0–1 trips). Replaces the
# original bimodal 50/50 to better reflect the realistic spread of
# QSR-shopping intensity in a 10K-customer metro panel.
QSR_TRIP_BUCKETS = [
    (0.30, (6, 12)),
    (0.40, (2,  5)),
    (0.30, (0,  1)),
]

# Step 4a: Retail (TJ Maxx) trip count distribution.
# 30% have 2–6 trips, 70% have 0–1 trips.
RETAIL_TRIP_BUCKETS = [
    (0.30, (2, 6)),
    (0.70, (0, 1)),
]

# Step 4b: per-customer active-weeks variance.
# Of the 13 weeks in the window, sample N active weeks per customer.
ACTIVE_WEEKS_DIST = {9: 0.10, 10: 0.20, 11: 0.35, 12: 0.35}
TOTAL_WEEKS = 13

# Pay-cycle bumps (Step 4g calendar effects) — both this and Step 4b
# active-weeks variance apply per the design.
PAY_CYCLE_DAYS = {1, 2, 3, 15, 16, 17}
PAY_CYCLE_MULTIPLIER = 1.15

# Step 4d/4e: per-trip merchant-choice probabilities for grocery customers.
LOYALIST_CHAIN_CHOICE   = {"primary": 0.94, "second": 0.05, "third": 0.01}
SPLITTER_CHAIN_DEFAULT  = {"primary": 0.65, "secondary": 0.30, "third": 0.05}
SPLITTER_CHAIN_WEEKEND  = {"primary": 0.80, "secondary": 0.20}
SPLITTER_CHAIN_WEEKDAY  = {"primary": 0.50, "secondary": 0.50}
THREE_CHAIN_CHAIN_CHOICE = {"primary": 0.50, "second": 0.30, "third": 0.20}

# Step 4f: within-chain store choice probabilities (closest, second, other).
STORE_CHOICE_PROBS = (0.70, 0.25, 0.05)

# Step 4g: peak day-of-week / hour-of-day per segment.
PEAK_DAYS_BY_SEGMENT: dict[str, list[int]] = {
    "grocery":          [5, 6],     # Sat, Sun (Mon=0)
    "qsr":              [4, 5],     # Fri, Sat
    "off_price_retail": [5, 6],     # Sat, Sun
}
PEAK_DAY_MULT = 1.4
NONPEAK_DAY_MULT = 0.9
PEAK_HOURS_BY_SEGMENT: dict[str, list[int]] = {
    "grocery":          [10, 11, 12, 17, 18, 19],
    "qsr":              [12, 13, 19, 20, 21],
    "off_price_retail": [11, 12, 13, 14, 15, 16, 17, 18, 19],
}

# Step 4h: terminal pool size per store (sampled uniformly per store).
TERMINALS_PER_STORE_RANGE = (4, 8)

# Step 4i: basket archetype shares (grocery only — QSR/retail homogeneous).
ARCHETYPE_SHARES = {"stockup": 0.40, "fill_in": 0.45, "themed": 0.15}

# Step 4i: per-archetype category weighting.
# Stockup biases toward pantry/household/dairy/meat (heavier replenishment);
# fill-in biases toward perishables (produce/dairy/bakery/meat); themed is
# a basket-bias mix toward party/BBQ/special-meal categories.
ARCHETYPE_CATEGORY_WEIGHTS: dict[str, dict[str, float]] = {
    "stockup": {
        "PANTRY":    0.20,
        "HOUSEHOLD": 0.10,
        "DAIRY":     0.13,
        "MEAT":      0.13,
        "FROZEN":    0.10,
        "PRODUCE":   0.08,
        "SNACKS":    0.06,
        "BEVERAGES": 0.08,
        "BAKERY":    0.04,
        "PERSONAL":  0.04,
        "BABY":      0.02,
        "PET":       0.02,
    },
    "fill_in": {
        "PRODUCE":   0.22,
        "DAIRY":     0.18,
        "BAKERY":    0.10,
        "MEAT":      0.13,
        "FROZEN":    0.06,
        "PANTRY":    0.10,
        "SNACKS":    0.07,
        "BEVERAGES": 0.07,
        "HOUSEHOLD": 0.03,
        "PERSONAL":  0.02,
        "BABY":      0.01,
        "PET":       0.01,
    },
    "themed": {
        "MEAT":      0.13,
        "SNACKS":    0.16,
        "BEVERAGES": 0.16,
        "BAKERY":    0.08,
        "PRODUCE":   0.10,
        "DAIRY":     0.09,
        "PANTRY":    0.10,
        "FROZEN":    0.08,
        "HOUSEHOLD": 0.04,
        "PERSONAL":  0.03,
        "BABY":      0.02,
        "PET":       0.01,
    },
}

# Step 4j: average basket size per (segment, behavioral_segment, archetype).
# (lo, mean, hi) — sampled as triangular.
BASKET_SIZE_GROCERY: dict[tuple[str, str], tuple[int, int, int]] = {
    ("filler",  "fill_in"): (3, 6, 10),
    ("filler",  "stockup"): (8, 14, 18),
    ("filler",  "themed"):  (5, 10, 14),
    ("stocker", "fill_in"): (5, 10, 14),
    ("stocker", "stockup"): (18, 28, 40),
    ("stocker", "themed"):  (10, 18, 25),
}
BASKET_SIZE_QSR    = (2, 3, 5)
BASKET_SIZE_RETAIL = (1, 5, 12)

# Step 4k: per-category quantity distributions. Each value maps a
# quantity (int) to its sample probability. Source: design doc Step 4k
# table (lines 691–713).
QTY_DISTRIBUTION: dict[str, dict[int, float]] = {
    # --- Grocery ---
    "PRODUCE":   {1: 0.55, 2: 0.25, 3: 0.12, 4: 0.05, 5: 0.03},
    "DAIRY":     {1: 0.65, 2: 0.25, 3: 0.08, 4: 0.02},
    "BAKERY":    {1: 0.85, 2: 0.13, 3: 0.02},
    "MEAT":      {1: 0.70, 2: 0.20, 3: 0.08, 4: 0.02},
    "FROZEN":    {1: 0.78, 2: 0.16, 3: 0.05, 4: 0.01},
    "PANTRY":    {1: 0.50, 2: 0.25, 3: 0.13, 4: 0.07, 6: 0.05},
    "SNACKS":    {1: 0.65, 2: 0.22, 3: 0.10, 4: 0.03},
    "BEVERAGES": {1: 0.72, 2: 0.20, 3: 0.06, 4: 0.02},
    "HOUSEHOLD": {1: 0.85, 2: 0.13, 3: 0.02},
    "PERSONAL":  {1: 0.88, 2: 0.10, 3: 0.02},
    "BABY":      {1: 0.75, 2: 0.20, 3: 0.04, 4: 0.01},
    "PET":       {1: 0.92, 2: 0.07, 3: 0.01},
    # --- QSR (Taco Bell) ---
    # Design groups TACO/BURR/SPEC under "MAIN"; we map BFAST there too.
    "TACO":      {1: 0.85, 2: 0.13, 3: 0.02},
    "BURR":      {1: 0.85, 2: 0.13, 3: 0.02},
    "SPEC":      {1: 0.85, 2: 0.13, 3: 0.02},
    "BFAST":     {1: 0.85, 2: 0.13, 3: 0.02},
    "SIDE":      {1: 0.75, 2: 0.20, 3: 0.05},
    "DRINK":     {1: 0.65, 2: 0.30, 3: 0.05},
    "COMBO":     {1: 0.90, 2: 0.09, 3: 0.01},
    # --- Retail (TJ Maxx) ---
    # Design groups WOM/MEN/KID/SHO under "APPAREL"; HOM/BTY under "HOME";
    # ACC/JEW under "ACCESSORY".
    "WOM":       {1: 0.92, 2: 0.07, 3: 0.01},
    "MEN":       {1: 0.92, 2: 0.07, 3: 0.01},
    "KID":       {1: 0.92, 2: 0.07, 3: 0.01},
    "SHO":       {1: 0.92, 2: 0.07, 3: 0.01},
    "HOM":       {1: 0.85, 2: 0.13, 3: 0.02},
    "BTY":       {1: 0.85, 2: 0.13, 3: 0.02},
    "ACC":       {1: 0.88, 2: 0.10, 3: 0.02},
    "JEW":       {1: 0.88, 2: 0.10, 3: 0.02},
}

# Step 4k step 6: discount-application probability when an active promo
# covers a (sku, date) pair at this merchant.
DISCOUNT_APPLY_PROB = 0.85

# Step 4l: card-network sub-distributions.
CREDIT_NETWORKS = {"visa": 0.50, "mc": 0.30, "amex": 0.12, "discover": 0.08}
DEBIT_NETWORKS  = {"visa": 0.60, "mc": 0.38, "discover": 0.01, "amex": 0.01}

# Step 4l: per-segment entry-mode distribution.
ENTRY_MODE_BY_SEGMENT: dict[str, dict[str, float]] = {
    "qsr":              {"contactless": 0.70, "chip": 0.22, "swipe": 0.05, "manual": 0.03},
    "grocery":          {"contactless": 0.55, "chip": 0.35, "swipe": 0.09, "manual": 0.01},
    "off_price_retail": {"contactless": 0.45, "chip": 0.45, "swipe": 0.09, "manual": 0.01},
}

# Step 4l: wallet sampling.
WALLET_USE_PROB = 0.70   # if has_mobile_wallet=1 and entry_mode='contactless'
WALLET_PROVIDER_DIST = {"apple": 0.50, "google": 0.30, "samsung": 0.20}

# Step 4l: connectivity_type distribution (uniform across merchants for v2.5).
CONNECTIVITY_DISTRIBUTION = {
    "wifi":         0.65,
    "cellular_4g":  0.25,
    "cellular_5g":  0.08,
    "ethernet":     0.02,
}
V2_5_CONNECTIVITY_DISTRIBUTION = CONNECTIVITY_DISTRIBUTION  # alias

# ---------------------------------------------------------------------------
# Per-merchant configs
# ---------------------------------------------------------------------------

MERCHANT_CONFIGS: dict[str, dict] = {
    "kroger": {
        "merchant_id":  "KRG",
        "name":         "Kroger",
        "segment":      "grocery",
        "mcc":          "5411",
        "n_stores":     30,
        "payment_mix":  {"credit": 0.65, "debit": 0.35},
    },
    "acme": {
        "merchant_id":  "ACM",
        "name":         "Acme",
        "segment":      "grocery",
        "mcc":          "5411",
        "n_stores":     25,
        "payment_mix":  {"credit": 0.65, "debit": 0.35},
    },
    "winn_dixie": {
        "merchant_id":  "WDX",
        "name":         "Winn-Dixie",
        "segment":      "grocery",
        "mcc":          "5411",
        "n_stores":     20,
        "payment_mix":  {"credit": 0.65, "debit": 0.35},
    },
    "taco_bell": {
        "merchant_id":  "TBL",
        "name":         "Taco Bell",
        "segment":      "qsr",
        "mcc":          "5814",
        "n_stores":     40,
        "payment_mix":  {"credit": 0.55, "debit": 0.45},
    },
    "tjmaxx": {
        "merchant_id":  "TJX",
        "name":         "TJ Maxx",
        "segment":      "off_price_retail",
        "mcc":          "5651",
        "n_stores":     8,
        "payment_mix":  {"credit": 0.74, "debit": 0.26},
    },
}
V2_5_MERCHANT_CONFIGS = MERCHANT_CONFIGS  # alias

# ---------------------------------------------------------------------------
# Per-merchant peer mapping — Phase 2 lake (consumed by Phase 5 view-builder)
# ---------------------------------------------------------------------------

PEER_MAPPING: dict[str, list[str]] = {
    "KRG": ["ACM", "WDX", "TBL", "TJX"],
    "ACM": ["KRG", "WDX", "TBL", "TJX"],
    "WDX": ["ACM", "KRG", "TBL", "TJX"],
    "TBL": ["ACM", "KRG", "WDX", "TJX"],
    "TJX": ["ACM", "KRG", "WDX", "TBL"],
}
V2_5_PEER_MAPPING = PEER_MAPPING


def peer_label(viewing_merchant_id: str, peer_merchant_id: str) -> str:
    if viewing_merchant_id == peer_merchant_id:
        raise ValueError(
            f"Viewing merchant cannot be its own peer ({viewing_merchant_id})"
        )
    order = PEER_MAPPING[viewing_merchant_id]
    idx = order.index(peer_merchant_id)
    return f"peer_{chr(ord('a') + idx)}"


v2_5_peer_label = peer_label  # alias
