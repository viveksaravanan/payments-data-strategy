"""Wave 3.5 Stage B acceptance — the `query_lake_sql` tool (§13.B).

Two blocks:

* **Validation / rejection** — needs no built lake; the contract checks
  (`_check_aggregating`, `_check_tables`, single-statement, viewer name)
  run before any viewer data is touched.
* **Execution / scoping / suppression** — reads the materialized
  `data/lake/items/` pair; skipped when the lake hasn't been built.
"""
from __future__ import annotations

import duckdb
import pytest

from src.agents.context import MerchantContext
from src.agents.lake_tools import LakeToolError, TOOLS_SPECIALIST, query_lake_sql
from src.agents.pricing import PricingSpecialist
from src.lake.lake_sql import DATA_LAKE_ITEMS, LAKE_K_FLOOR


def _built() -> bool:
    return (DATA_LAKE_ITEMS / "KRG" / "lake_transactions.parquet").exists()


needs_lake = pytest.mark.skipif(
    not _built(), reason="line-item lake not built (run `make lake-items`)"
)


@pytest.fixture
def viewer_krg() -> MerchantContext:
    return MerchantContext.for_merchant("KRG")


# ----- tool registration -------------------------------------------------

def test_query_lake_sql_registered() -> None:
    names = {t["name"] for t in TOOLS_SPECIALIST}
    assert "query_lake_sql" in names


# ----- validation / rejection (no data needed) --------------------------

def test_select_star_rejected() -> None:
    with pytest.raises(LakeToolError, match=r"SELECT \*"):
        query_lake_sql("KRG", "SELECT * FROM lake_transactions")


def test_raw_columns_rejected() -> None:
    # Non-aggregating projection would leak raw line rows.
    with pytest.raises(LakeToolError, match="aggregat"):
        query_lake_sql("KRG", "SELECT category, unit_price FROM lake_transactions")


def test_cte_passthrough_rejected() -> None:
    # Aggregation buried in a CTE the outer query just selects from would
    # make the injected count tally CTE rows, not base lines (§7.3).
    with pytest.raises(LakeToolError):
        query_lake_sql(
            "KRG",
            "WITH x AS (SELECT category, AVG(unit_price) p FROM lake_transactions "
            "GROUP BY category) SELECT category, p FROM x",
        )


def test_read_parquet_reacharound_rejected() -> None:
    with pytest.raises(LakeToolError, match="not allowed"):
        query_lake_sql(
            "KRG",
            "SELECT category, COUNT(*) FROM "
            "read_parquet('data/raw/transaction_items.parquet') GROUP BY category",
        )


def test_unknown_table_rejected() -> None:
    with pytest.raises(LakeToolError, match="Unknown table"):
        query_lake_sql("KRG", "SELECT banner_code, COUNT(*) FROM transactions GROUP BY banner_code")


def test_multiple_statements_rejected() -> None:
    with pytest.raises(LakeToolError):
        query_lake_sql(
            "KRG",
            "SELECT category, COUNT(*) FROM lake_transactions GROUP BY category; DROP TABLE x",
        )


def test_union_rejected() -> None:
    with pytest.raises(LakeToolError):
        query_lake_sql(
            "KRG",
            "SELECT category, COUNT(*) FROM lake_transactions GROUP BY category "
            "UNION ALL SELECT category, COUNT(*) FROM lake_transactions GROUP BY category",
        )


def test_invalid_viewer_rejected() -> None:
    with pytest.raises(LakeToolError, match="Unknown viewer"):
        query_lake_sql("ZZZ", "SELECT category, COUNT(*) FROM lake_transactions GROUP BY category")


# ----- execution / scoping / suppression (needs built lake) -------------

@needs_lake
def test_grouped_query_executes_real_dollars() -> None:
    p = query_lake_sql(
        "KRG",
        "SELECT category, AVG(unit_price) AS asp FROM lake_transactions "
        "WHERE peer_relationship='peer' GROUP BY category",
    )
    assert set(p["columns"]) == {"category", "asp"}
    assert "_k" not in p["columns"]
    assert p["row_count"] > 0
    asp = {r[0]: r[1] for r in p["rows"]}
    assert 2.0 < asp["Milk"] < 6.0  # real dollars, not an index near 1.0


@needs_lake
def test_scoping_switches_on_viewer() -> None:
    """datamodel-v2: both grocery and QSR viewers have 2 same-segment
    peers, and the peer sets differ by viewer — the lake resolves to the
    viewer's own materialized pair."""
    sql = "SELECT COUNT(*) AS n FROM lake_transactions WHERE peer_relationship='peer'"
    krg = query_lake_sql("KRG", sql)   # grocery peers (ACM, WDX)
    tbl = query_lake_sql("TBL", sql)   # qsr peers (BKG, CFA)
    assert krg["rows"][0][0] > 0
    assert tbl["rows"][0][0] > 0
    # Different segments → different peer-line volumes.
    assert krg["rows"][0][0] != tbl["rows"][0][0]


@needs_lake
def test_count_injection_nested_join_neighborhood() -> None:
    # The CTE + JOIN-to-lake_stores + GROUP BY neighborhood case — the
    # count must be computed at the returned (neighborhood) grain.
    p = query_lake_sql(
        "KRG",
        "WITH base AS (SELECT lake_store_id, unit_price FROM lake_transactions "
        "WHERE peer_relationship='peer' AND category='Milk') "
        "SELECT s.neighborhood, AVG(b.unit_price) AS asp FROM base b "
        "JOIN lake_stores s USING (lake_store_id) GROUP BY s.neighborhood",
    )
    assert set(p["columns"]) == {"neighborhood", "asp"}
    assert p["row_count"] > 0
    assert p["suppressed"] == 0  # neighborhoods are well above k=50 at full scale


@needs_lake
def test_k_floor_suppresses_thin_groups_and_strips_k() -> None:
    # Grouping by basket → per-basket groups; those under k=50 lines drop.
    p = query_lake_sql(
        "KRG",
        "SELECT lake_txn_id, SUM(line_total) AS tot FROM lake_transactions GROUP BY lake_txn_id",
    )
    assert "_k" not in p["columns"]
    assert p["suppressed"] > 0


@needs_lake
def test_group_by_all_accepted_and_counts_correctly() -> None:
    # GROUP BY ALL has empty group_expressions but
    # aggregate_handling=FORCE_AGGREGATES — must be accepted, and the
    # injected COUNT(*) must group at the same grain as an explicit
    # GROUP BY category.
    p_all = query_lake_sql(
        "KRG",
        "SELECT category, AVG(unit_price) AS asp FROM lake_transactions "
        "WHERE peer_relationship='peer' GROUP BY ALL",
    )
    p_explicit = query_lake_sql(
        "KRG",
        "SELECT category, AVG(unit_price) AS asp FROM lake_transactions "
        "WHERE peer_relationship='peer' GROUP BY category",
    )
    assert p_all["row_count"] == p_explicit["row_count"] > 0
    assert "_k" not in p_all["columns"]
    # same per-category means either way
    assert {r[0]: round(r[1], 6) for r in p_all["rows"]} == \
        {r[0]: round(r[1], 6) for r in p_explicit["rows"]}


@needs_lake
def test_whole_table_aggregate_ok() -> None:
    p = query_lake_sql(
        "KRG",
        "SELECT AVG(unit_price) AS asp, COUNT(DISTINCT lake_txn_id) AS txns FROM lake_transactions",
    )
    assert p["row_count"] == 1
    assert "_k" not in p["columns"]


@needs_lake
def test_payload_shape_matches_query_tenant() -> None:
    p = query_lake_sql(
        "KRG",
        "SELECT payment_type, COUNT(DISTINCT lake_txn_id) AS txns FROM lake_transactions "
        "WHERE peer_relationship='peer' GROUP BY payment_type",
    )
    for key in ("rows", "columns", "row_count", "truncated", "frame", "sql", "suppressed"):
        assert key in p


@needs_lake
def test_dispatch_sets_lake_frame_and_logs_surface(viewer_krg) -> None:
    specialist = PricingSpecialist(viewer_krg)
    specialist._reset_state()
    payload = specialist._dispatch_tool(
        "query_lake_sql",
        {"sql": "SELECT category, AVG(unit_price) AS asp FROM lake_transactions "
                "WHERE peer_relationship='peer' GROUP BY category"},
    )
    assert specialist._lake_frame is not None
    assert len(specialist._lake_frame) == payload["row_count"]
    surfaces = [e["surface"] for e in specialist._sql_log]
    assert "lake_sql" in surfaces


# ----- datamodel-v2: the 6-merchant panel is valid across both guards ----

def test_valid_merchants_tracks_config_in_both_guards() -> None:
    """Regression: the runtime privacy guards hardcoded the old 5-merchant
    set (with TJX, missing BKG/CFA), which rejected BKG/CFA viewers."""
    from pathlib import Path

    from src.generate.config.loader import load_config
    from src.lake.isolation import VALID_MERCHANTS as ISO_VM
    from src.lake.lake_sql import VALID_MERCHANTS as LAKE_VM

    root = Path(__file__).resolve().parents[2] / "src" / "generate" / "config"
    banners = {m["banner_code"] for m in load_config(root).merchants.values()}
    assert banners == {"KRG", "ACM", "WDX", "TBL", "BKG", "CFA"}
    assert set(LAKE_VM) == banners          # lake_sql guard is config-derived
    assert ISO_VM == banners                # isolation guard is config-derived
    assert "TJX" not in LAKE_VM and "TJX" not in ISO_VM


def test_bkg_cfa_tenant_predicate_accepted() -> None:
    from src.lake.isolation import check_tenant_predicate
    for viewer in ("BKG", "CFA"):
        check_tenant_predicate(
            f"SELECT txn_id FROM transactions WHERE banner_code = '{viewer}'", viewer)


@needs_lake
def test_bkg_cfa_lake_queries_succeed() -> None:
    """The v2 QSR banners must be accepted by query_lake_sql (they were
    rejected as 'Unknown viewer' while VALID_MERCHANTS was stale)."""
    for viewer in ("BKG", "CFA"):
        p = query_lake_sql(
            viewer,
            "SELECT category, AVG(unit_price) AS asp FROM lake_transactions "
            "WHERE peer_relationship = 'peer' GROUP BY category",
        )
        assert p["row_count"] > 0
