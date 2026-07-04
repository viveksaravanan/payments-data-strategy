"""Wave 3.5 Stage A acceptance — the line-item peer lake.

Covers the §13.A gate: no PII / no real merchant name / no consumer
linkage / no lat-long / no raw timestamp / no planted `zone_id` in the
published frames; viewer-exclusion holds; `peer_relationship` correct
relative to the viewer; routing metadata correct.

The scoping/labeling invariants are tested on a small synthetic global
frame (fast, no rebuild). A second block reads the materialized
`data/lake/items/` output if present and asserts the published schema
is clean — skipped when the lake hasn't been built (so the battery
stays green on a fresh checkout).
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from src.lake.build_line_items import (
    _STORE_PUBLISHED,
    _TXN_PUBLISHED,
    DATA_LAKE_ITEMS,
    VALID_MERCHANTS,
    _salt,
    _token_map,
    scope_lines_for_viewer,
    viewer_metadata,
)

# Identity / planted columns that must NEVER reach a published frame.
FORBIDDEN_PUBLISHED = {
    "banner_code", "merchant_id", "merchant", "name",
    "customer_token", "customer_id",
    "store_id", "txn_id", "line_id",
    "txn_ts", "latitude", "longitude", "zone_id",
}


def _synthetic_global() -> pd.DataFrame:
    """A handful of peer lines spanning grocery (KRG, ACM) and qsr
    (TBL, CFA), with the internal columns the scoper needs."""
    rows = []
    spec = [("KRG", "s_krg"), ("ACM", "s_acm"), ("TBL", "s_tbl"), ("CFA", "s_cfa")]
    for banner, store in spec:
        for i in range(2):
            rows.append({
                "banner_code": banner,
                "lake_txn_id": f"tok_{banner}_{i}",
                "lake_line_id": f"tok_{banner}_{i}-1",
                "lake_store_id": f"stok_{store}",
                "neighborhood": f"Nbhd_{store}",
                "txn_date": dt.date(2026, 4, 1),
                "hour_bucket": 5,
                "department": "Dairy & Eggs",
                "category": "Milk",
                "subcategory": "Whole Milk",
                "unit_price": 3.50,
                "qty": 1,
                "discount": 0.0,
                "line_total": 3.50,
                "payment_type": "debit",
                "card_network": "visa",
                "entry_mode": "contactless",
                "wallet_type": "none",
            })
    return pd.DataFrame(rows)


# ----- published column contract (static — no build needed) -------------

def test_published_column_lists_carry_no_identity() -> None:
    assert FORBIDDEN_PUBLISHED.isdisjoint(_TXN_PUBLISHED)
    assert FORBIDDEN_PUBLISHED.isdisjoint(_STORE_PUBLISHED)


# ----- viewer exclusion + relationship labeling -------------------------

def test_viewer_rows_excluded() -> None:
    g = _synthetic_global()
    lake_txn, lake_stores = scope_lines_for_viewer(g, "KRG")
    # The viewer's own tokens are gone — and no real banner survives to
    # check against, which is itself the point.
    assert "banner_code" not in lake_txn.columns
    assert "banner_code" not in lake_stores.columns
    # KRG had 2 lines; excluded → only ACM/TBL/CFA (6) remain.
    assert len(lake_txn) == 6
    assert not lake_txn["lake_txn_id"].str.contains("KRG").any()


def test_peer_relationship_relative_to_viewer() -> None:
    g = _synthetic_global()
    # Grocery viewer: ACM is a same-segment peer; TBL/CFA are merchants.
    _, krg_stores = scope_lines_for_viewer(g, "KRG")
    rel = dict(zip(krg_stores["lake_store_id"], krg_stores["peer_relationship"]))
    assert rel["stok_s_acm"] == "peer"
    assert rel["stok_s_tbl"] == "merchant"
    assert rel["stok_s_cfa"] == "merchant"

    # For a QSR viewer, CFA is the same-segment peer; the grocers are merchants.
    _, tbl_stores = scope_lines_for_viewer(g, "TBL")
    rel_tbl = dict(zip(tbl_stores["lake_store_id"], tbl_stores["peer_relationship"]))
    assert rel_tbl["stok_s_cfa"] == "peer"
    assert rel_tbl["stok_s_acm"] == "merchant"
    assert rel_tbl["stok_s_krg"] == "merchant"


def test_peer_segment_label_present_on_stores() -> None:
    g = _synthetic_global()
    _, krg_stores = scope_lines_for_viewer(g, "KRG")
    seg = dict(zip(krg_stores["lake_store_id"], krg_stores["peer_segment"]))
    assert seg["stok_s_acm"] == "grocery"
    assert seg["stok_s_tbl"] == "qsr"
    assert seg["stok_s_cfa"] == "qsr"


# ----- routing metadata --------------------------------------------------

@pytest.mark.parametrize(
    "viewer,segment,peers",
    [("KRG", "grocery", 2), ("ACM", "grocery", 2), ("WDX", "grocery", 2),
     ("TBL", "qsr", 2), ("BKG", "qsr", 2), ("CFA", "qsr", 2)],
)
def test_viewer_metadata(viewer: str, segment: str, peers: int) -> None:
    meta = viewer_metadata(viewer)
    assert meta["segment"] == segment
    assert meta["segment_peer_count"] == peers


# ----- determinism: salt + token map -------------------------------------

def test_salt_is_deterministic_and_seed_derived() -> None:
    assert _salt() == _salt()
    assert _salt().startswith("lake_v35:")


def test_token_map_stable() -> None:
    s = pd.Series(["a", "b", "a", "c"])
    salt = _salt()
    assert _token_map(s, salt) == _token_map(s, salt)
    # Non-reversible width.
    assert all(len(v) == 16 for v in _token_map(s, salt).values())


# ----- materialized output (skipped if not built) ------------------------

def _built() -> bool:
    return (DATA_LAKE_ITEMS / "KRG" / "lake_transactions.parquet").exists()


@pytest.mark.skipif(not _built(), reason="line-item lake not built (run `make lake-items`)")
def test_materialized_schema_is_clean() -> None:
    con = duckdb.connect()
    for viewer in VALID_MERCHANTS:
        for tbl in ("lake_transactions", "lake_stores"):
            path = DATA_LAKE_ITEMS / viewer / f"{tbl}.parquet"
            cols = set(
                con.execute(
                    f"DESCRIBE SELECT * FROM read_parquet('{path}')"
                ).df()["column_name"]
            )
            assert FORBIDDEN_PUBLISHED.isdisjoint(cols), (
                f"{viewer}/{tbl} leaked identity columns: "
                f"{FORBIDDEN_PUBLISHED & cols}"
            )
            if tbl == "lake_transactions":
                # Taxonomy is the SHARED functional hierarchy (dept/cat/subcat);
                # a merchant's own labels must never reach the lake.
                assert {"department", "category", "subcategory"} <= cols
                assert not any(c.startswith("merchant_") for c in cols), (
                    f"{viewer}/{tbl} leaked merchant-own taxonomy: "
                    f"{[c for c in cols if c.startswith('merchant_')]}"
                )


@pytest.mark.skipif(not _built(), reason="line-item lake not built (run `make lake-items`)")
def test_materialized_generalization_holds() -> None:
    con = duckdb.connect()
    path = DATA_LAKE_ITEMS / "KRG" / "lake_transactions.parquet"
    # txn_date is date-only (DuckDB reports DATE), hour_bucket in range,
    # peer_relationship is the label vocabulary only.
    schema = con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{path}')"
    ).df().set_index("column_name")["column_type"].to_dict()
    assert schema["txn_date"] == "DATE"
    bounds = con.execute(
        f"SELECT MIN(hour_bucket), MAX(hour_bucket) FROM read_parquet('{path}')"
    ).fetchone()
    assert bounds[0] >= 0 and bounds[1] <= HOUR_BUCKETS_MAX
    rels = set(
        con.execute(
            f"SELECT DISTINCT peer_relationship FROM read_parquet('{path}')"
        ).df()["peer_relationship"]
    )
    assert rels <= {"peer", "merchant"}


HOUR_BUCKETS_MAX = 9
