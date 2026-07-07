"""L4 — Tenant-isolation tests (Wave 2 §2 / D22.4).

Three independent checks:

(a) ``check_tenant_predicate`` rejects unscoped tenant queries and
    queries that reference another merchant.
(b) ``wrap_tenant_query`` produces SQL that, executed against the
    real ``data/raw/`` Parquet, returns ONLY the viewer's rows.
(c) ``assert_lake_source_paths`` rejects any ``data/eval/`` path.

**Explicit boundary split (SPEC §0 disambiguation).** Two distinct
test groups:

- ``test_cohort_cross_merchant_token_linkage_IS_ALLOWED`` confirms
  the lake builder CAN read ``transactions.customer_token`` across
  merchants via the §1 ``observable_guard``. The §4.5 cohort step
  needs this; it's the trusted boundary, not an isolation breach.
- ``test_lake_eval_path_FORBIDDEN`` confirms the lake builder cannot
  read ``data/eval/``. Different boundary entirely.

An over-broad isolation test that bans all cross-boundary access
would false-positive on the legitimate cohort path. The split keeps
them distinct.
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from src.lake.isolation import (
    LakeSourcePathError,
    TenantIsolationError,
    assert_lake_source_paths,
    check_tenant_predicate,
    wrap_tenant_query,
)
from src.lake.observable_guard import DATA_RAW, load_table

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_EVAL = REPO_ROOT / "data" / "eval"


# ----- (a) Predicate check ---------------------------------------------------

def test_query_with_viewer_predicate_accepted() -> None:
    sql = "SELECT * FROM transactions WHERE banner_code = 'KRG'"
    check_tenant_predicate(sql, "KRG")  # no raise


def test_query_with_viewer_predicate_via_merchant_id_accepted() -> None:
    """Both banner_code and merchant_id are the merchant identifier
    in the Wave 1 schema; both should satisfy the predicate."""
    sql = "SELECT * FROM stores WHERE merchant_id = 'ACM'"
    check_tenant_predicate(sql, "ACM")


def test_query_missing_viewer_predicate_rejected() -> None:
    sql = "SELECT * FROM transactions"
    with pytest.raises(TenantIsolationError, match="viewer-scoping predicate"):
        check_tenant_predicate(sql, "KRG")


def test_query_with_wrong_viewer_rejected() -> None:
    """SQL says banner_code = 'ACM' but viewer is Kroger."""
    sql = "SELECT * FROM transactions WHERE banner_code = 'ACM'"
    with pytest.raises(TenantIsolationError):
        check_tenant_predicate(sql, "KRG")


def test_query_referencing_other_merchant_rejected() -> None:
    """Viewer is Kroger but query also references Acme — peer data
    must come from the lake, not the tenant surface."""
    sql = (
        "SELECT * FROM transactions "
        "WHERE banner_code = 'KRG' OR banner_code = 'ACM'"
    )
    with pytest.raises(TenantIsolationError, match="references another merchant"):
        check_tenant_predicate(sql, "KRG")


def test_query_with_in_clause_other_merchants_rejected() -> None:
    sql = "SELECT * FROM transactions WHERE banner_code IN ('KRG', 'WDX')"
    with pytest.raises(TenantIsolationError, match="references another merchant"):
        check_tenant_predicate(sql, "KRG")


def test_unknown_viewer_rejected() -> None:
    with pytest.raises(TenantIsolationError, match="Unknown viewer"):
        check_tenant_predicate("SELECT 1", "ZZZ")


def test_multi_statement_rejected() -> None:
    sql = "SELECT * FROM transactions WHERE banner_code = 'KRG'; DROP TABLE x"
    with pytest.raises(TenantIsolationError):
        check_tenant_predicate(sql, "KRG")


def test_non_select_rejected() -> None:
    sql = "DROP TABLE transactions"
    with pytest.raises(TenantIsolationError):
        check_tenant_predicate(sql, "KRG")


# ----- (b) Query wrap returns only viewer rows ------------------------------

def test_wrap_produces_viewer_scoped_query() -> None:
    sql = "SELECT COUNT(*) AS n FROM transactions"
    wrapped = wrap_tenant_query(sql, "KRG")
    n = duckdb.connect().sql(wrapped).fetchone()[0]
    # Kroger txn count from Wave 1 full-scale (1.66M total split across
    # 3 grocers + qsr + retail). Just assert > 0 and reasonable.
    assert n > 0
    # Sanity: Kroger should be < total transactions.
    total = duckdb.connect().sql(
        f"SELECT COUNT(*) FROM read_parquet('{DATA_RAW}/transactions.parquet')"
    ).fetchone()[0]
    assert n < total


def test_wrap_excludes_other_merchants_in_query_result() -> None:
    """Even if the inner SQL tries to count by banner, the CTE wrap
    scopes upstream to Kroger so other banners can't appear."""
    sql = "SELECT banner_code, COUNT(*) AS n FROM transactions GROUP BY banner_code"
    wrapped = wrap_tenant_query(sql, "KRG")
    df = duckdb.connect().sql(wrapped).df()
    assert set(df["banner_code"]) == {"KRG"}


def test_wrap_scopes_transaction_items_via_join() -> None:
    """transaction_items has no banner_code — it must be scoped via
    an inner join on the already-scoped transactions CTE."""
    sql = (
        "SELECT COUNT(*) AS n FROM transaction_items i "
        "JOIN transactions t ON i.txn_id = t.txn_id"
    )
    wrapped = wrap_tenant_query(sql, "ACM")
    n = duckdb.connect().sql(wrapped).fetchone()[0]
    assert n > 0


def test_wrap_merges_user_leading_with_cte() -> None:
    """A user query that is itself a CTE (starts with `WITH`) must execute.

    Regression: the wrapper prepends its own `WITH <shadow>`; naive
    concatenation put two `WITH` keywords back-to-back and DuckDB rejected
    it (`Parser Error at "WITH"`), so any CTE query — e.g. the demand
    affinity self-join, whose prompt example starts with `WITH bk AS (…)` —
    failed. The wrapper now merges the user's CTEs into the shadow list."""
    sql = (
        "WITH bk AS ("
        "  SELECT DISTINCT t.txn_id, p.merchant_subcategory AS g"
        "  FROM transaction_items i"
        "  JOIN transactions t ON i.txn_id = t.txn_id"
        "  JOIN products p ON i.sku = p.sku"
        "  WHERE t.banner_code = 'KRG'),"
        "base AS (SELECT g, COUNT(*) AS a_baskets FROM bk GROUP BY 1),"
        "pair AS (SELECT a.g AS item_a, b.g AS item_b, COUNT(*) AS both_ct"
        "         FROM bk a JOIN bk b ON a.txn_id = b.txn_id AND a.g <> b.g"
        "         GROUP BY 1, 2)"
        "SELECT pair.item_a, pair.item_b, pair.both_ct, base.a_baskets,"
        "       pair.both_ct * 1.0 / base.a_baskets AS attach_rate "
        "FROM pair JOIN base ON pair.item_a = base.g "
        "WHERE pair.both_ct >= 200 ORDER BY attach_rate DESC LIMIT 3"
    )
    wrapped = wrap_tenant_query(sql, "KRG")
    df = duckdb.connect().sql(wrapped).df()  # no ParserException
    assert len(df) > 0
    assert (df["attach_rate"] > 0).all()


def test_wrap_user_leading_with_cte_still_viewer_scoped() -> None:
    """The merged CTE must not widen scope: a leading-`WITH` query that
    surfaces banner_code from inside its CTE still sees only the viewer,
    because the user's CTE reads the viewer-scoped `transactions` shadow."""
    sql = (
        "WITH mine AS (SELECT banner_code FROM transactions) "
        "SELECT banner_code, COUNT(*) AS n FROM mine GROUP BY banner_code"
    )
    wrapped = wrap_tenant_query(sql, "WDX")
    df = duckdb.connect().sql(wrapped).df()
    assert set(df["banner_code"]) == {"WDX"}


def test_wrap_unknown_viewer_rejected() -> None:
    with pytest.raises(TenantIsolationError):
        wrap_tenant_query("SELECT 1", "ZZZ")


# ----- (c) data/eval/ path guard --------------------------------------------

def test_data_eval_path_forbidden() -> None:
    """SPLIT-BOUNDARY (1/2): lake builder cannot read data/eval/."""
    eval_file = DATA_EVAL / "anomalies_groundtruth.parquet"
    with pytest.raises(LakeSourcePathError, match="data/eval"):
        assert_lake_source_paths([eval_file])


def test_data_raw_path_allowed() -> None:
    """data/raw/ is the lake's intended source."""
    raw_file = DATA_RAW / "transactions.parquet"
    assert_lake_source_paths([raw_file])  # no raise


def test_non_data_path_allowed() -> None:
    """The lake builder is allowed to read configs, catalogs, etc.,
    from outside data/. The guard only bans data/eval/."""
    assert_lake_source_paths([REPO_ROOT / "src" / "generate" / "config"])


def test_mixed_paths_one_eval_rejects() -> None:
    """Any single data/eval/ path in a batch causes the whole assertion
    to fail."""
    paths = [
        DATA_RAW / "transactions.parquet",
        DATA_EVAL / "anomalies_groundtruth.parquet",
    ]
    with pytest.raises(LakeSourcePathError):
        assert_lake_source_paths(paths)


# ----- BOUNDARY SPLIT (2/2): cohort cross-merchant linkage IS allowed -----

def test_cohort_cross_merchant_token_linkage_IS_ALLOWED() -> None:
    """SPLIT-BOUNDARY (2/2): the §4.5 cohort builder MUST be able to
    link card tokens across merchants. transactions.customer_token is
    the observable cross-merchant linkage key. Reading it from the
    transactions table via observable_guard is the trusted boundary —
    not an isolation breach.

    This test confirms the API allows it; it explicitly is NOT covered
    by check_tenant_predicate (the predicate-check API is for Wave 3
    agent queries against the tenant surface, not for the trusted
    lake-builder cohort step)."""
    # Cohort builder loads transactions (observable) and groups by
    # customer_token across ALL merchants.
    df = load_table("transactions", columns=["customer_token", "banner_code"])
    by_card_segments = df.groupby("customer_token")["banner_code"].nunique()
    # Wave 1 T8 measured ~32% multi-merchant at full scale.
    multi_merchant_cards = (by_card_segments >= 2).sum()
    assert multi_merchant_cards > 1000, (
        "cohort linkage should be possible; many multi-merchant cards "
        "exist in the data"
    )
    # The point: this WORKS. No isolation error was raised. The lake
    # builder can legitimately link tokens across merchants inside the
    # §4.5 cohort step.
