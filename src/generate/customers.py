"""Shared customer panel.

Runs ONCE. Produces 5,000 customers with PII (`customer_name`, `customer_email`,
`home_zip5`), a stable `customer_pan`, and behavioral traits used by every merchant
generator. The cross-merchant invariant: a given customer's `customer_pan` is the
same value at Kroger, Taco Bell, and TJ Maxx.
"""
from datetime import timedelta

import numpy as np
import pandas as pd
from faker import Faker

from . import parameters as P

AGE_BANDS = ["18-24", "25-34", "35-44", "45-54", "55-64", "65+"]
AGE_WEIGHTS = [0.10, 0.20, 0.22, 0.20, 0.15, 0.13]

INCOME_BANDS = ["<35k", "35-75k", "75-125k", "125-200k", "200k+"]
INCOME_WEIGHTS = [0.20, 0.35, 0.25, 0.15, 0.05]

# 25 ZIP3 prefixes spanning a few regions; gives ~200 customers per ZIP3 on average,
# enough density for k-anonymity to mostly hold but produce some suppressed cells.
ZIP3_PREFIXES = [
    "100", "102", "110",                       # NY metro
    "200", "201",                              # DC metro
    "303", "304",                              # GA
    "320", "321",                              # FL
    "401", "402",                              # KY
    "432", "441", "442", "443",                # OH
    "462",                                     # IN
    "480", "482",                              # MI
    "606", "607",                              # IL
    "750", "770",                              # TX
    "850",                                     # AZ
    "900", "902", "941",                       # CA
]


def generate_customers(rng: np.random.Generator) -> pd.DataFrame:
    """Build the shared customer panel.

    Parameters
    ----------
    rng : numpy.random.Generator
        Seeded generator. Faker is reseeded from the same source so names/emails
        are also reproducible.
    """
    n = P.N_CUSTOMERS

    fake = Faker("en_US")
    Faker.seed(int(rng.integers(0, 2**31 - 1)))

    age = rng.choice(AGE_BANDS, size=n, p=AGE_WEIGHTS)
    income = rng.choice(INCOME_BANDS, size=n, p=INCOME_WEIGHTS)
    zip3 = rng.choice(ZIP3_PREFIXES, size=n)
    zip5_suffix = rng.integers(0, 100, size=n)
    zip5 = [f"{z}{s:02d}" for z, s in zip(zip3, zip5_suffix)]

    segment = rng.choice(["filler", "stocker"], size=n,
                         p=[P.SEGMENT_FILLER_SHARE, P.SEGMENT_STOCKER_SHARE])
    is_lapser = (rng.random(n) < P.SEGMENT_LAPSER_SHARE).astype(int)

    primary_card = rng.choice(
        ["credit", "debit", "ebt", "mixed"], size=n,
        p=[0.50, 0.35, 0.05, 0.10],
    )
    has_wallet = rng.choice([0, 1], size=n, p=[0.45, 0.55]).astype(int)

    signup_offsets = rng.integers(0, 365 * 5, size=n)
    signup_dates = [(P.END_DATE - timedelta(days=int(d))).isoformat()
                    for d in signup_offsets]

    # 16-digit synthetic PANs. Sample in [10^15, 10^16) so leading digit is non-zero
    # — keeps 16-character strings stable through CSV round-trips.
    pan_array = rng.integers(low=10**15, high=10**16, size=n, dtype=np.int64)
    pans = [str(p) for p in pan_array]

    names = [fake.name() for _ in range(n)]
    emails = [fake.email() for _ in range(n)]

    return pd.DataFrame({
        "customer_pan":        pans,
        "customer_name":       names,
        "customer_email":      emails,
        "age_band":            age,
        "income_band":         income,
        "home_zip5":           zip5,
        "signup_date":         signup_dates,
        "behavioral_segment":  segment,
        "is_lapser":           is_lapser,
        "primary_card_type":   primary_card,
        "has_mobile_wallet":   has_wallet,
    })
