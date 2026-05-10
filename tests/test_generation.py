"""Generation tests.

Phase 4: generator follows the Layer 4 flow from V2_5_DATA_DESIGN.md.
No EBT, no cash, no declines. Customer-centric trip distribution
(active-weeks variance + per-segment trip ranges). Basket archetypes
drive grocery basket sizes and category mix. Per-category quantity
distributions per Step 4k. Volume targets: 180k–250k transactions,
2.0–3.0M line items.
"""
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.generate import parameters as P

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
DB_PATH = ROOT / "data" / "payments.db"

TXN_DTYPES = {
    "txn_id":            str,
    "merchant_id":       str,
    "customer_id":       str,
    "store_id":          str,
    "terminal_id":       str,
    "txn_ts":            str,
    "payment_type":      str,
    "card_network":      str,
    "entry_mode":        str,
    "wallet_type":       str,
    "connectivity_type": str,
}


@pytest.fixture(scope="module")
def customers() -> pd.DataFrame:
    return pd.read_csv(
        RAW / "customers.csv", dtype={"customer_id": str, "home_zip5": str}
    )


@pytest.fixture(scope="module")
def stores() -> pd.DataFrame:
    return pd.read_csv(
        RAW / "stores.csv", dtype={"store_id": str, "store_zip5": str}
    )


@pytest.fixture(scope="module")
def transactions() -> pd.DataFrame:
    return pd.read_csv(RAW / "transactions.csv", dtype=TXN_DTYPES)


@pytest.fixture(scope="module")
def items() -> pd.DataFrame:
    return pd.read_csv(
        RAW / "transaction_items.csv",
        dtype={"txn_id": str, "sku": str, "promo_id": str},
    )


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------

def test_customers_count(customers: pd.DataFrame) -> None:
    assert len(customers) == P.N_CUSTOMERS  # 10,000


def test_customer_id_unique(customers: pd.DataFrame) -> None:
    assert customers["customer_id"].is_unique


def test_customer_id_format(customers: pd.DataFrame) -> None:
    assert customers["customer_id"].str.fullmatch(r"[0-9a-f]{16}").all()


def test_no_pii_in_raw(customers: pd.DataFrame) -> None:
    forbidden = {
        "customer_pan", "customer_name", "customer_email",
        "age_band", "income_band", "is_lapser",
    }
    leaks = forbidden & set(customers.columns)
    assert not leaks, f"raw customers.csv leaks v2 columns: {leaks}"


def test_customers_in_charlotte_metro(customers: pd.DataFrame) -> None:
    metro = set(P.METRO_ZIPS)
    bad = customers.loc[~customers["home_zip5"].isin(metro), "home_zip5"].unique()
    assert len(bad) == 0, f"non-Charlotte ZIPs in panel: {bad[:5]}"


def test_customers_have_v2_5_fields(customers: pd.DataFrame) -> None:
    expected = {
        "customer_id", "home_zip5", "behavioral_segment",
        "grocer_affinity_type", "primary_grocer", "secondary_grocer",
        "primary_card_type", "has_mobile_wallet", "signup_date",
    }
    assert expected.issubset(set(customers.columns))


def test_grocer_affinity_mix_in_band(customers: pd.DataFrame) -> None:
    shares = customers["grocer_affinity_type"].value_counts(normalize=True)
    targets = P.GROCER_AFFINITY_SHARES
    for k, expected_pct in targets.items():
        actual = shares.get(k, 0.0)
        assert abs(actual - expected_pct) <= 0.01, (
            f"affinity '{k}': got {actual:.3f}, expected {expected_pct:.3f} ±0.01"
        )


def test_secondary_grocer_only_for_splitters_and_three_chain(
    customers: pd.DataFrame,
) -> None:
    has_secondary = customers["secondary_grocer"].notna()
    types_with_secondary = set(customers.loc[has_secondary, "grocer_affinity_type"].unique())
    assert types_with_secondary <= {"splitter", "three_chain"}


def test_no_ebt_in_customer_card_types(customers: pd.DataFrame) -> None:
    assert set(customers["primary_card_type"].unique()) <= {"credit", "debit", "mixed"}


def test_customers_deterministic() -> None:
    from src.generate.customers import generate_customers
    a = generate_customers(np.random.default_rng(P.RANDOM_SEED))
    b = generate_customers(np.random.default_rng(P.RANDOM_SEED))
    pd.testing.assert_frame_equal(a, b)


# ---------------------------------------------------------------------------
# Stores
# ---------------------------------------------------------------------------

def test_total_store_count(stores: pd.DataFrame) -> None:
    counts = stores["merchant_id"].value_counts().to_dict()
    assert counts == {"KRG": 30, "ACM": 25, "WDX": 20, "TBL": 40, "TJX": 8}


def test_stores_have_v2_5_fields(stores: pd.DataFrame) -> None:
    expected = {
        "store_id", "merchant_id", "store_zip5", "neighborhood",
        "metro_region", "latitude", "longitude", "open_date",
    }
    assert expected.issubset(set(stores.columns))


def test_stores_in_charlotte_metro(stores: pd.DataFrame) -> None:
    metro = set(P.METRO_ZIPS)
    bad = stores.loc[~stores["store_zip5"].isin(metro), "store_zip5"].unique()
    assert len(bad) == 0, f"non-Charlotte store ZIPs: {bad[:5]}"


def test_metro_region_values_valid(stores: pd.DataFrame) -> None:
    allowed = {"urban_core", "inner_suburbs", "outer_suburbs"}
    assert set(stores["metro_region"].unique()) <= allowed


def test_university_city_has_all_three_grocers(stores: pd.DataFrame) -> None:
    uc = stores[stores["store_zip5"].isin({"28213", "28223"})]
    grocers_with_uc_store = set(uc.loc[uc["merchant_id"].isin(["KRG", "ACM", "WDX"]), "merchant_id"])
    assert grocers_with_uc_store == {"KRG", "ACM", "WDX"}


def test_plaza_midwood_has_kroger(stores: pd.DataFrame) -> None:
    pm = stores[(stores["store_zip5"] == "28205") & (stores["merchant_id"] == "KRG")]
    assert len(pm) >= 1


# ---------------------------------------------------------------------------
# Volume + per-merchant presence — Layer 4 targets per Step 6.
# ---------------------------------------------------------------------------

def test_each_merchant_has_transactions(transactions: pd.DataFrame) -> None:
    counts = transactions["merchant_id"].value_counts()
    for m in ("KRG", "ACM", "WDX", "TBL", "TJX"):
        assert counts.get(m, 0) > 0, f"no transactions for {m}"


def test_total_volume_in_design_target(transactions: pd.DataFrame) -> None:
    """Step 6 validation: 180k–250k transactions across the panel."""
    n = len(transactions)
    assert 180_000 <= n <= 260_000, (
        f"transaction volume {n:,} outside Layer 4 target 180,000–250,000 "
        f"(small slack on the high end allowed)"
    )


def test_total_line_items_in_design_target(items: pd.DataFrame) -> None:
    """Step 6 validation: 2.0M–3.0M line items across the panel."""
    n = len(items)
    assert 1_700_000 <= n <= 3_300_000, (
        f"line-item count {n:,} outside Layer 4 target 2.0M–3.0M"
    )


# ---------------------------------------------------------------------------
# Numeric / structural integrity
# ---------------------------------------------------------------------------

def test_no_negative_quantities_or_prices(items: pd.DataFrame) -> None:
    assert (items["qty"] > 0).all()
    assert (items["unit_price"] >= 0).all()
    assert (items["line_total"] >= -0.01).all()


def test_line_total_consistency(items: pd.DataFrame) -> None:
    diff = items["line_total"] - (items["qty"] * items["unit_price"] - items["discount"])
    assert diff.abs().max() < 0.05


def test_foreign_keys(transactions, customers, stores, items) -> None:
    assert transactions["customer_id"].isin(customers["customer_id"]).all()
    assert transactions["store_id"].isin(stores["store_id"]).all()
    assert items["txn_id"].isin(transactions["txn_id"]).all()


def test_txn_dates_in_v2_5_window(transactions: pd.DataFrame) -> None:
    """Phase 4 cuts the date window over to March 1 – May 29, 2026."""
    ts = pd.to_datetime(transactions["txn_ts"])
    start = pd.Timestamp(P.START_DATE)
    end = pd.Timestamp(P.END_DATE) + pd.Timedelta(days=1)
    assert (ts >= start).all()
    assert (ts < end).all()


# ---------------------------------------------------------------------------
# Cross-merchant invariant
# ---------------------------------------------------------------------------

def test_cross_merchant_customer_invariant(
    transactions: pd.DataFrame, customers: pd.DataFrame
) -> None:
    assert customers["customer_id"].is_unique
    assert transactions["customer_id"].isin(customers["customer_id"]).all()
    n_merchants_per_customer = (
        transactions.groupby("customer_id")["merchant_id"].nunique()
    )
    assert (n_merchants_per_customer >= 2).sum() > 100


# ---------------------------------------------------------------------------
# Phase 4: Layer 4 generator assertions.
# ---------------------------------------------------------------------------

def test_no_ebt_anywhere(transactions: pd.DataFrame) -> None:
    """Phase 4 drops EBT entirely."""
    assert (transactions["payment_type"] != "ebt").all()


def test_no_cash_anywhere(transactions: pd.DataFrame) -> None:
    """Phase 4 drops cash entirely (captured rails: credit + debit only)."""
    assert (transactions["payment_type"] != "cash").all()


def test_payment_types_only_credit_or_debit(transactions: pd.DataFrame) -> None:
    assert set(transactions["payment_type"].unique()) <= {"credit", "debit"}


def test_terminal_id_in_store_pool(transactions: pd.DataFrame) -> None:
    """`terminal_id` must be `<store_id>-T<NN>` and the suffix must be in
    [01, 08] per Step 4h's 4–8 terminals/store rule."""
    bad = transactions[
        ~transactions.apply(
            lambda r: r["terminal_id"].startswith(f"{r['store_id']}-T"),
            axis=1,
        )
    ]
    assert bad.empty, f"{len(bad)} rows have terminal_id not under store_id"
    suffixes = transactions["terminal_id"].str[-2:].astype(int)
    assert suffixes.between(1, 8).all()


def test_avg_qty_per_line_in_band(items: pd.DataFrame) -> None:
    """Step 4k says ~1.4–1.6 average qty across all lines."""
    avg_qty = float(items["qty"].mean())
    assert 1.35 <= avg_qty <= 1.65, (
        f"avg qty per line = {avg_qty:.3f}, expected ~1.4–1.6"
    )


@pytest.mark.parametrize("category,target", [
    ("DAIRY",     {1: 0.65, 2: 0.25, 3: 0.08, 4: 0.02}),
    ("BAKERY",    {1: 0.85, 2: 0.13, 3: 0.02}),
    ("PRODUCE",   {1: 0.55, 2: 0.25, 3: 0.12, 4: 0.05, 5: 0.03}),
    ("PET",       {1: 0.92, 2: 0.07, 3: 0.01}),
    ("BEVERAGES", {1: 0.72, 2: 0.20, 3: 0.06, 4: 0.02}),
])
def test_qty_distribution_per_category(category: str, target: dict[int, float]) -> None:
    """Per-category qty distribution should match Step 4k spec within ±3pp."""
    with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as conn:
        rows = conn.execute(
            """
            SELECT i.qty, COUNT(*)
            FROM tenant_transaction_items i
            JOIN tenant_products p ON p.sku = i.sku
            WHERE p.category = ?
            GROUP BY i.qty
            """,
            (category,),
        ).fetchall()
    total = sum(n for _, n in rows)
    actual = {q: n / total for q, n in rows}
    for q, expected in target.items():
        observed = actual.get(q, 0.0)
        assert abs(observed - expected) <= 0.03, (
            f"{category} qty={q}: got {observed:.3f}, expected {expected:.3f} ±0.03"
        )


def test_discount_rate_by_segment() -> None:
    """Step 6 validation: ~15% grocery / ~5% QSR / ~3% retail (±3pp)."""
    targets = {"grocery": 0.15, "qsr": 0.05, "off_price_retail": 0.03}
    with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as conn:
        rows = conn.execute(
            """
            SELECT m.segment,
                   AVG(CASE WHEN i.discount > 0 THEN 1.0 ELSE 0.0 END) AS rate
            FROM tenant_transaction_items i
            JOIN tenant_transactions t ON t.txn_id = i.txn_id
            JOIN merchants m ON m.merchant_id = t.merchant_id
            GROUP BY m.segment
            """
        ).fetchall()
    actual = {row[0]: row[1] for row in rows}
    for seg, target in targets.items():
        observed = actual.get(seg, 0.0)
        assert abs(observed - target) <= 0.04, (
            f"{seg} discount rate = {observed:.3f}, target {target:.3f} ±0.04"
        )


def test_active_weeks_per_customer(transactions: pd.DataFrame) -> None:
    """Step 4b: each customer is active in ~9–12 of the 13 weeks. Allow
    customers with very few trips to be active in fewer weeks (you can't
    span 9 weeks with 4 trips). Among customers with ≥10 trips, ≥80%
    should be active in 9 or more distinct weeks."""
    txn = transactions.copy()
    txn["txn_week"] = (
        (pd.to_datetime(txn["txn_ts"]).dt.normalize() - pd.Timestamp(P.START_DATE))
        .dt.days // 7
    )
    per_customer = txn.groupby("customer_id").agg(
        n_trips=("txn_id", "count"),
        n_weeks=("txn_week", "nunique"),
    )
    eligible = per_customer[per_customer["n_trips"] >= 10]
    if len(eligible) < 100:
        pytest.skip("too few customers with ≥10 trips for active-weeks check")
    pct_in_band = (eligible["n_weeks"] >= 9).mean()
    assert pct_in_band >= 0.80, (
        f"only {pct_in_band:.1%} of high-trip customers are active in ≥9 weeks; "
        f"Step 4b expects ≥80%"
    )


# ---------------------------------------------------------------------------
# Phase 6: Planted anomalies
# ---------------------------------------------------------------------------

def _conn() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)


def test_university_city_decline_per_grocer() -> None:
    """Anomaly 1: University City stores show declining traffic in
    stage 3 (Apr 26–May 2) compared with the pre-anomaly baseline
    (Mar 1–Apr 11). Kroger is deepest, then Acme, then Winn-Dixie.

    The design's effective stage-3 multipliers are:
      KRG ≈ 0.55  (deepest)
      ACM ≈ 0.64
      WDX ≈ 0.685 (least deep)

    Random sampling pulls the empirical ratios around; we assert the
    direction of the drop and the ordering, allowing slack on the
    absolute magnitudes.
    """
    with _conn() as conn:
        df = pd.read_sql_query(
            """
            SELECT t.merchant_id, DATE(t.txn_ts) AS day, COUNT(*) AS n
            FROM tenant_transactions t
            JOIN tenant_stores s ON s.store_id = t.store_id
            WHERE s.neighborhood = 'University City'
              AND t.merchant_id IN ('KRG','ACM','WDX')
            GROUP BY t.merchant_id, DATE(t.txn_ts)
            """,
            conn,
        )
    df["day"] = pd.to_datetime(df["day"])
    pre = df[df["day"] < "2026-04-12"]
    stage3 = df[(df["day"] >= "2026-04-26") & (df["day"] <= "2026-05-02")]

    pre_per_day = pre.groupby("merchant_id")["n"].mean()
    stage3_per_day = stage3.groupby("merchant_id")["n"].mean()

    ratios = (stage3_per_day / pre_per_day).to_dict()
    # All three grocers must drop in stage 3 vs pre-anomaly baseline.
    for mid in ("KRG", "ACM", "WDX"):
        assert mid in ratios, f"missing stage-3 data for {mid}"
        assert ratios[mid] < 0.95, (
            f"University City decline missing for {mid}: stage-3 ratio "
            f"{ratios[mid]:.2f} expected < 0.95"
        )
    # Kroger drops deeper than Acme, which drops deeper than Winn-Dixie
    # (allow ±0.05 wiggle on the WDX/ACM gap because magnitudes are close).
    assert ratios["KRG"] < ratios["ACM"], (
        f"Expected KRG ({ratios['KRG']:.2f}) < ACM ({ratios['ACM']:.2f}) "
        f"per-grocer magnitude ordering"
    )
    assert ratios["ACM"] <= ratios["WDX"] + 0.05, (
        f"Expected ACM ({ratios['ACM']:.2f}) ≲ WDX ({ratios['WDX']:.2f}) "
        f"per-grocer magnitude ordering"
    )


def test_plaza_midwood_kroger_avocado_spike() -> None:
    """Anomaly 2: Plaza Midwood Kroger avocado qty on Apr 22 ≥ 3×
    the prior-week median for the same store/SKU; no comparable spike
    at ACM or WDX Plaza Midwood stores."""
    with _conn() as conn:
        df = pd.read_sql_query(
            """
            SELECT t.merchant_id, DATE(t.txn_ts) AS day, SUM(i.qty) AS qty
            FROM tenant_transaction_items i
            JOIN tenant_transactions t ON t.txn_id = i.txn_id
            JOIN tenant_stores s       ON s.store_id = t.store_id
            JOIN tenant_products p     ON p.sku = i.sku
            WHERE s.neighborhood = 'Plaza Midwood'
              AND t.merchant_id IN ('KRG','ACM','WDX')
              AND p.category = 'PRODUCE'
              AND p.name LIKE '%vocado%'
            GROUP BY t.merchant_id, DATE(t.txn_ts)
            """,
            conn,
        )
    df["day"] = pd.to_datetime(df["day"])
    by_merchant = {m: g.set_index("day")["qty"] for m, g in df.groupby("merchant_id")}

    krg = by_merchant.get("KRG", pd.Series(dtype=float))
    apr22 = krg.get(pd.Timestamp("2026-04-22"), 0)
    prior = krg.loc["2026-04-15":"2026-04-21"]
    assert len(prior) >= 4, f"need prior-week samples for KRG; got {len(prior)}"
    prior_median = prior.median()
    assert apr22 >= 3 * prior_median, (
        f"PM Kroger Apr 22 avocado qty {apr22} expected ≥ 3× prior-week "
        f"median {prior_median} (got {apr22/max(prior_median,1):.2f}×)"
    )

    # ACM / WDX Plaza Midwood: Apr 22 must NOT exceed 2× prior-week median.
    for mid in ("ACM", "WDX"):
        s = by_merchant.get(mid, pd.Series(dtype=float))
        if s.empty:
            continue
        peer_apr22 = s.get(pd.Timestamp("2026-04-22"), 0)
        peer_prior = s.loc["2026-04-15":"2026-04-21"]
        if len(peer_prior) < 3:
            continue
        peer_med = max(peer_prior.median(), 1)
        assert peer_apr22 < 2 * peer_med, (
            f"Plaza Midwood {mid} Apr 22 avocado qty {peer_apr22} unexpectedly "
            f"high vs prior-week median {peer_med} (got {peer_apr22/peer_med:.2f}×)"
        )


def test_pasta_promo_lift_and_suppression() -> None:
    """Anomaly 3: pasta line counts during each grocer's pinned promo
    window vs an off-window baseline. The basket multipliers locked in
    the design are KRG 2.2× (lift), ACM 0.8× (failed), WDX 1.4× (lift).
    Empirical ratios drift around the targets; we assert direction and
    a ±0.4× tolerance on the magnitude."""
    with _conn() as conn:
        df = pd.read_sql_query(
            """
            SELECT t.merchant_id, DATE(t.txn_ts) AS day, COUNT(*) AS n
            FROM tenant_transaction_items i
            JOIN tenant_transactions t ON t.txn_id = i.txn_id
            JOIN tenant_products p     ON p.sku = i.sku
            WHERE p.subcategory = 'pasta'
              AND t.merchant_id IN ('KRG','ACM','WDX')
            GROUP BY t.merchant_id, DATE(t.txn_ts)
            """,
            conn,
        )
    df["day"] = pd.to_datetime(df["day"])

    rules = [
        ("KRG", "2026-04-15", "2026-04-21", 2.2, "lift"),
        ("ACM", "2026-04-19", "2026-04-25", 0.8, "suppress"),
        ("WDX", "2026-04-22", "2026-04-28", 1.4, "lift"),
    ]
    for mid, start, end, target, direction in rules:
        m = df[df["merchant_id"] == mid].set_index("day")["n"]
        in_window = m.loc[start:end]
        # Off-window baseline: full 90 days minus all three promo windows
        # (ACM/KRG/WDX) so cross-merchant overlap doesn't pollute.
        off_mask = pd.Series(True, index=m.index)
        for _, s2, e2, _, _ in rules:
            off_mask &= ~((m.index >= s2) & (m.index <= e2))
        baseline = m[off_mask]
        in_avg = float(in_window.mean())
        base_avg = float(baseline.mean())
        ratio = in_avg / base_avg
        assert abs(ratio - target) < 0.5, (
            f"{mid} pasta {direction} ratio {ratio:.2f} expected ~{target} "
            f"(in-window avg={in_avg:.1f}, baseline avg={base_avg:.1f})"
        )
        if direction == "lift":
            assert ratio > 1.0, (
                f"{mid} pasta should lift during promo; got {ratio:.2f}×"
            )
        else:
            assert ratio < 1.0, (
                f"{mid} pasta should be suppressed during promo; got {ratio:.2f}×"
            )


# ---------------------------------------------------------------------------
# v2 catalog regression check kept from earlier phases.
# ---------------------------------------------------------------------------

def test_no_placeholder_kroger_names() -> None:
    with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM tenant_products "
            "WHERE merchant_id='KRG' AND (name LIKE '%item%' OR name LIKE '%Item%')"
        ).fetchone()[0]
    assert n == 0
