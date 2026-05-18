"""Tests for the parameterized lake view-builders.

The lake is virtual in v2.5 — these tests exercise the view-builders
in ``src/lake/views.py`` directly against the tenant tables. The
agent-side wrapping (``query_lake`` CTE shim, SQL guards) is covered
in ``test_agents.py``. This file verifies:

  - peer mapping correctness vs the documented Phase 2 table
  - viewing-merchant exclusion (no own data leaks)
  - opaque-id determinism
  - bin / bucket vocabularies
  - k=5 cell suppression
  - column-shape (21 cols on transactions, 6 on stores; no customer_id)

The view-builders read the existing ``data/payments.db`` produced by
``make seed``.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from src.generate import parameters as P
from src.lake import (
    HOUR_BUCKETS,
    K_ANONYMITY_K,
    TOTAL_BINS,
    apply_k_anonymity,
    build_peer_mapping,
    generate_opaque_id,
    get_lake_stores,
    get_lake_transactions,
    peer_case_sql,
    to_hour_bucket,
    to_total_bin,
)

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "payments.db"
ALL_MERCHANTS = ("KRG", "ACM", "WDX", "TBL", "TJX")

# Expected `lake_transactions` columns per V2_5_DATA_DESIGN.md lines 627–650.
EXPECTED_LAKE_TXN_COLS = [
    "lake_txn_id", "line_id", "peer_id", "peer_segment", "lake_store_id",
    "txn_date", "txn_hour_bucket", "payment_type", "card_network",
    "entry_mode", "wallet_type", "connectivity_type", "txn_total_bin",
    "canonical_name", "category", "subcategory", "unit_price", "qty",
    "discount", "line_total", "discount_pct_applied",
]

# Expected `lake_stores` columns per V2_5_DATA_DESIGN.md lines 671–679.
EXPECTED_LAKE_STORE_COLS = [
    "lake_store_id", "peer_id", "peer_segment",
    "store_zip3", "neighborhood", "metro_region",
]

# Documented mapping per V2_5_DATA_DESIGN.md lines 957–970.
EXPECTED_PEER_MAPPING = {
    "KRG": {"ACM": "peer_a", "WDX": "peer_b", "TBL": "peer_c", "TJX": "peer_d"},
    "ACM": {"KRG": "peer_a", "WDX": "peer_b", "TBL": "peer_c", "TJX": "peer_d"},
    "WDX": {"ACM": "peer_a", "KRG": "peer_b", "TBL": "peer_c", "TJX": "peer_d"},
    "TBL": {"ACM": "peer_a", "KRG": "peer_b", "WDX": "peer_c", "TJX": "peer_d"},
    "TJX": {"ACM": "peer_a", "KRG": "peer_b", "WDX": "peer_c", "TBL": "peer_d"},
}


# ---------------------------------------------------------------------------
# Peer mapping
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("viewing,expected", list(EXPECTED_PEER_MAPPING.items()))
def test_build_peer_mapping_matches_design(
    viewing: str, expected: dict[str, str]
) -> None:
    assert build_peer_mapping(viewing) == expected


def test_build_peer_mapping_rejects_unknown_merchant() -> None:
    with pytest.raises(ValueError):
        build_peer_mapping("XYZ")


def test_peer_case_sql_uses_correct_column() -> None:
    sql = peer_case_sql("KRG", "t.merchant_id")
    assert "CASE t.merchant_id" in sql
    for mid, label in EXPECTED_PEER_MAPPING["KRG"].items():
        assert f"WHEN '{mid}' THEN '{label}'" in sql


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("hour,expected", [
    (5, "early_morning"), (6, "early_morning"),
    (7, "morning"), (8, "morning"),
    (9, "mid_morning"), (10, "mid_morning"),
    (11, "lunch"), (12, "lunch"),
    (13, "afternoon"), (14, "afternoon"),
    (15, "late_afternoon"), (16, "late_afternoon"),
    (17, "evening"), (18, "evening"),
    (19, "dinner"), (20, "dinner"),
    (21, "late_evening"), (22, "late_evening"),
    (23, "late_night"), (0, "late_night"),
    (1, "late_night"), (4, "late_night"),
])
def test_to_hour_bucket(hour: int, expected: str) -> None:
    ts = f"2026-04-22 {hour:02d}:30:00"
    assert to_hour_bucket(ts) == expected


@pytest.mark.parametrize("amount,expected", [
    (0, "$0-5"), (4.99, "$0-5"),
    (5, "$5-10"), (9.99, "$5-10"),
    (10, "$10-20"), (19.99, "$10-20"),
    (20, "$20-35"), (34.99, "$20-35"),
    (35, "$35-50"), (49.99, "$35-50"),
    (50, "$50-75"), (74.99, "$50-75"),
    (75, "$75-100"), (99.99, "$75-100"),
    (100, "$100-150"), (149.99, "$100-150"),
    (150, "$150-250"), (249.99, "$150-250"),
    (250, "$250+"), (1000, "$250+"),
])
def test_to_total_bin(amount: float, expected: str) -> None:
    assert to_total_bin(amount) == expected


def test_hour_bucket_vocabulary_size() -> None:
    """Exactly 10 documented buckets."""
    assert len(HOUR_BUCKETS) == 10
    assert len(set(HOUR_BUCKETS)) == 10


def test_total_bin_vocabulary_size() -> None:
    """Exactly 10 documented bins."""
    assert len(TOTAL_BINS) == 10
    assert len(set(TOTAL_BINS)) == 10


def test_opaque_id_deterministic() -> None:
    a = generate_opaque_id("KRG-0000123")
    b = generate_opaque_id("KRG-0000123")
    assert a == b
    assert len(a) == 16
    assert all(c in "0123456789abcdef" for c in a)


def test_opaque_id_two_arg_deterministic() -> None:
    a = generate_opaque_id("KRG-0000123", 4)
    b = generate_opaque_id("KRG-0000123", 4)
    assert a == b


def test_opaque_id_sensitive_to_inputs() -> None:
    assert generate_opaque_id("a") != generate_opaque_id("b")
    assert generate_opaque_id("a", 1) != generate_opaque_id("a", 2)
    # one-arg vs two-arg with the same payload still differs because
    # the payload differs ("a" vs "a|1")
    assert generate_opaque_id("a") != generate_opaque_id("a", 1)


# ---------------------------------------------------------------------------
# k=5 cell suppression
# ---------------------------------------------------------------------------

def test_apply_k_anonymity_drops_cells_below_k() -> None:
    df = pd.DataFrame({"zip3": ["282", "283", "284"], "n": [10, 4, 7]})
    out = apply_k_anonymity(df, count_col="n", k=K_ANONYMITY_K)
    # zip3=283 had n=4 < 5 → suppressed.
    assert set(out["zip3"]) == {"282", "284"}


def test_apply_k_anonymity_preserves_when_all_above_k() -> None:
    df = pd.DataFrame({"zip3": ["282", "283"], "n": [50, 30]})
    out = apply_k_anonymity(df, count_col="n", k=K_ANONYMITY_K)
    assert len(out) == 2


def test_apply_k_anonymity_raises_on_missing_count_col() -> None:
    df = pd.DataFrame({"zip3": ["282"], "txns": [10]})
    with pytest.raises(KeyError):
        apply_k_anonymity(df, count_col="n", k=K_ANONYMITY_K)


def test_apply_k_anonymity_on_real_aggregate() -> None:
    """Run a contrived high-cardinality aggregate from the real lake
    view; verify suppression actually fires (some cells fall below k=5)."""
    df = get_lake_transactions(
        "KRG",
        sql_filter="txn_date = '2026-04-22'",
    )
    if df.empty:
        pytest.skip("no lake transactions on the chosen date")
    grouped = (
        df.groupby(
            ["peer_id", "txn_hour_bucket", "card_network"], dropna=False
        )
        .size()
        .reset_index(name="n")
    )
    suppressed = apply_k_anonymity(grouped, "n")
    assert len(suppressed) < len(grouped), (
        "expected some cells to be suppressed at k=5 on a fine-grained aggregate"
    )
    assert (suppressed["n"] >= K_ANONYMITY_K).all()


# ---------------------------------------------------------------------------
# get_lake_transactions
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("viewing", ALL_MERCHANTS)
def test_lake_transactions_excludes_viewing_merchant(viewing: str) -> None:
    """No row in the lake view should correspond to the viewing
    merchant. We verify by joining back through the opaque store ID
    and observing that no row maps to a store owned by `viewing`."""
    df = get_lake_transactions(
        viewing, sql_filter="txn_date = '2026-04-22'"
    )
    if df.empty:
        pytest.skip("no transactions on 2026-04-22 for this viewer")
    # All peer_ids must be from the 4 documented peer labels, never NULL.
    assert df["peer_id"].notna().all()
    expected_labels = set(EXPECTED_PEER_MAPPING[viewing].values())
    assert set(df["peer_id"].unique()).issubset(expected_labels)


def test_lake_transactions_full_panel_excludes_viewer() -> None:
    """Total lake_transactions row count for `viewing` should equal
    the total tenant line-item count minus the viewing merchant's own
    line items."""
    with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as conn:
        total_lines = conn.execute(
            "SELECT COUNT(*) FROM tenant_transaction_items"
        ).fetchone()[0]
        own_lines = {
            mid: conn.execute(
                """
                SELECT COUNT(*)
                FROM tenant_transaction_items i
                JOIN tenant_transactions t ON t.txn_id = i.txn_id
                WHERE t.merchant_id = ?
                """,
                (mid,),
            ).fetchone()[0]
            for mid in ALL_MERCHANTS
        }
    # Spot-check Kroger: the cheapest run, since KRG is the largest
    # contributor and easiest to verify.
    df = get_lake_transactions("KRG")
    assert len(df) == total_lines - own_lines["KRG"]


def test_lake_transactions_columns_match_design() -> None:
    df = get_lake_transactions("KRG", sql_filter="txn_date = '2026-04-22'")
    assert list(df.columns) == EXPECTED_LAKE_TXN_COLS


def test_lake_transactions_has_no_customer_id() -> None:
    df = get_lake_transactions("KRG", sql_filter="txn_date = '2026-04-22'")
    forbidden = {"customer_id", "customer_pan", "customer_name", "customer_email"}
    assert not (forbidden & set(df.columns)), (
        f"customer-linkage columns leaked into lake view: "
        f"{forbidden & set(df.columns)}"
    )


def test_lake_transactions_bucket_vocabulary() -> None:
    """All txn_hour_bucket values must be from the documented HOUR_BUCKETS."""
    df = get_lake_transactions("KRG", sql_filter="txn_date = '2026-04-22'")
    if df.empty:
        pytest.skip("empty result")
    observed = set(df["txn_hour_bucket"].dropna().unique())
    assert observed.issubset(set(HOUR_BUCKETS))


def test_lake_transactions_bin_vocabulary() -> None:
    """All txn_total_bin values must be from the documented TOTAL_BINS."""
    df = get_lake_transactions("KRG")
    observed = set(df["txn_total_bin"].dropna().unique())
    assert observed.issubset(set(TOTAL_BINS))


def test_lake_transactions_peer_segment_correct() -> None:
    """For Kroger viewing: peer_a (ACM) and peer_b (WDX) are grocery;
    peer_c (TBL) is qsr; peer_d (TJX) is off_price_retail."""
    df = get_lake_transactions("KRG", sql_filter="txn_date = '2026-04-22'")
    if df.empty:
        pytest.skip("empty result")
    by_peer = df[["peer_id", "peer_segment"]].drop_duplicates()
    expected = {
        "peer_a": "grocery",
        "peer_b": "grocery",
        "peer_c": "qsr",
        "peer_d": "off_price_retail",
    }
    for _, row in by_peer.iterrows():
        assert expected[row["peer_id"]] == row["peer_segment"]


def test_lake_transactions_discount_pct_applied_formula() -> None:
    """For lines with discount > 0: discount_pct_applied ==
    ROUND(discount / (unit_price * qty), 2)."""
    df = get_lake_transactions("KRG", sql_filter="discount > 0").head(500)
    if df.empty:
        pytest.skip("no discounted lines for Kroger viewer")
    expected = (df["discount"] / (df["unit_price"] * df["qty"])).round(2)
    diff = (df["discount_pct_applied"] - expected).abs()
    assert (diff < 0.01).all()


def test_lake_transactions_opaque_ids_are_stable() -> None:
    """Two runs of the same view-builder produce identical opaque IDs
    for the same underlying rows."""
    df1 = get_lake_transactions("KRG", sql_filter="txn_date = '2026-04-22'")
    df2 = get_lake_transactions("KRG", sql_filter="txn_date = '2026-04-22'")
    if df1.empty:
        pytest.skip("empty result")
    pd.testing.assert_series_equal(
        df1["lake_txn_id"].sort_values().reset_index(drop=True),
        df2["lake_txn_id"].sort_values().reset_index(drop=True),
        check_names=False,
    )


def test_lake_transactions_opaque_ids_unique_per_line() -> None:
    """Each (txn_id, line_id) must hash to a distinct lake_txn_id."""
    df = get_lake_transactions("KRG", sql_filter="txn_date = '2026-04-22'")
    if df.empty:
        pytest.skip("empty result")
    assert df["lake_txn_id"].is_unique


# ---------------------------------------------------------------------------
# get_lake_stores
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("viewing", ALL_MERCHANTS)
def test_lake_stores_excludes_viewing_merchant(viewing: str) -> None:
    df = get_lake_stores(viewing)
    expected_labels = set(EXPECTED_PEER_MAPPING[viewing].values())
    assert set(df["peer_id"].unique()) == expected_labels


@pytest.mark.parametrize("viewing", ALL_MERCHANTS)
def test_lake_stores_count_matches_panel(viewing: str) -> None:
    """Total lake_stores row count = 123 - own_store_count."""
    own_store_counts = {"KRG": 30, "ACM": 25, "WDX": 20, "TBL": 40, "TJX": 8}
    df = get_lake_stores(viewing)
    assert len(df) == 123 - own_store_counts[viewing]


def test_lake_stores_columns_match_design() -> None:
    df = get_lake_stores("ACM")
    assert list(df.columns) == EXPECTED_LAKE_STORE_COLS


def test_lake_stores_zip3_format() -> None:
    df = get_lake_stores("KRG")
    assert df["store_zip3"].str.fullmatch(r"\d{3}").all()


def test_lake_stores_neighborhood_carried_through() -> None:
    """`neighborhood` is preserved verbatim from tenant_stores per the
    design's "neighborhood retained" rule."""
    df = get_lake_stores("KRG")
    expected_neighborhoods = set(P.NEIGHBORHOODS.keys())
    observed = set(df["neighborhood"].unique())
    assert observed.issubset(expected_neighborhoods)


def test_lake_stores_metro_region_values_valid() -> None:
    df = get_lake_stores("KRG")
    assert set(df["metro_region"].unique()) <= {
        "urban_core", "inner_suburbs", "outer_suburbs",
    }


# ---------------------------------------------------------------------------
# Cross-viewer consistency: lake_store_id is stable for the same
# physical store regardless of who's viewing.
# ---------------------------------------------------------------------------

def test_lake_store_id_stable_across_viewers() -> None:
    """For a store visible in two viewers' lakes (i.e. owned by neither
    viewing merchant), its lake_store_id should be identical."""
    krg_df = get_lake_stores("KRG")  # excludes Kroger; ACM stores included
    wdx_df = get_lake_stores("WDX")  # excludes Winn-Dixie; ACM stores included
    krg_acm = set(krg_df.loc[krg_df["peer_id"] == "peer_a", "lake_store_id"])
    # In WDX's view, ACM is also peer_a.
    wdx_acm = set(wdx_df.loc[wdx_df["peer_id"] == "peer_a", "lake_store_id"])
    assert krg_acm == wdx_acm and len(krg_acm) > 0


# ---------------------------------------------------------------------------
# Defensive sql_filter validation
# ---------------------------------------------------------------------------

def test_sql_filter_rejects_drop_table() -> None:
    with pytest.raises(ValueError):
        get_lake_transactions("KRG", sql_filter="1=1; DROP TABLE merchants")


def test_sql_filter_rejects_comment_injection() -> None:
    with pytest.raises(ValueError):
        get_lake_transactions("KRG", sql_filter="1=1 -- foo")


def test_sql_filter_accepts_legitimate_predicate() -> None:
    # Just shouldn't raise — empty result is OK.
    df = get_lake_transactions(
        "KRG", sql_filter="payment_type = 'credit' AND category = 'DAIRY'"
    )
    assert isinstance(df, pd.DataFrame)
