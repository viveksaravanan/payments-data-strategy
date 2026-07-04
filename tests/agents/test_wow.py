"""Phase 4 — server-side week-over-week 'top movers' helper.

Two layers:
  * `compute_movers` — pure, deterministic reduction of a week×dim frame (no IO).
  * `top_movers` tool — runs a weekly query through the guarded tenant/lake path
    and returns the reduced movers frame (lake-backed; skips if not built).
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.agents import lake_tools as LT
from src.agents.lake_tools import LakeToolError
from src.agents.wow import compute_movers
from src.lake.lake_sql import DATA_LAKE_ITEMS


def _built() -> bool:
    return (DATA_LAKE_ITEMS / "KRG" / "lake_transactions.parquet").exists()


needs_lake = pytest.mark.skipif(
    not _built(), reason="line-item lake not built (run `make lake-items`)"
)


def _weekly(spec: dict[str, list[float]], count: int = 500) -> pd.DataFrame:
    """Build a tidy week×dim frame from {dim: [w0..w4 values]}."""
    rows = []
    for dim, vals in spec.items():
        for wk, v in enumerate(vals):
            rows.append({"cat": dim, "wk": f"2026-{wk:02d}", "units": v, "n": count})
    return pd.DataFrame(rows)


# ----- compute_movers (pure) -------------------------------------------------

def test_movers_rank_riser_and_decliner() -> None:
    # recent (w4) vs mean(w0..w3). Fruit +200%, Milk -60%, Flat unchanged.
    df = _weekly({"Fruit": [100, 100, 100, 100, 300],
                  "Milk":  [100, 100, 100, 100, 40],
                  "Flat":  [100, 100, 100, 100, 100]})
    m = compute_movers(df, dim_col="cat", week_col="wk", value_col="units", count_col="n")
    got = {r["cat"]: (r["direction"], r["delta_pct"]) for _, r in m.iterrows()}
    assert got["Fruit"] == ("up", 2.0)
    assert got["Milk"] == ("down", -0.6)
    # biggest absolute move first
    assert m.iloc[0]["cat"] == "Fruit"


def test_movers_k_floor_drops_thin_weeks() -> None:
    df = _weekly({"Big": [100, 100, 100, 100, 300]}, count=500)
    thin = _weekly({"Thin": [10, 10, 10, 10, 30]}, count=10)  # n=10 < 50
    m = compute_movers(pd.concat([df, thin]), dim_col="cat", week_col="wk",
                       value_col="units", count_col="n")
    assert set(m["cat"]) == {"Big"}  # Thin dropped by the k=50 floor


def test_movers_min_volume_drops_noise() -> None:
    df = _weekly({"Loud": [100, 100, 100, 100, 300], "Quiet": [2, 2, 2, 2, 6]})
    m = compute_movers(df, dim_col="cat", week_col="wk", value_col="units",
                       count_col="n", min_volume=10.0)
    assert set(m["cat"]) == {"Loud"}  # Quiet baseline (2) < min_volume


def test_movers_incomplete_series_dropped() -> None:
    df = _weekly({"Full": [100, 100, 100, 100, 300]})
    partial = pd.DataFrame([{"cat": "Partial", "wk": "2026-04", "units": 500, "n": 500}])
    m = compute_movers(pd.concat([df, partial]), dim_col="cat", week_col="wk",
                       value_col="units", count_col="n")
    assert set(m["cat"]) == {"Full"}  # Partial has no baseline weeks


def test_movers_too_few_weeks_returns_empty() -> None:
    df = _weekly({"X": [100, 200]})  # only 2 weeks, need baseline_weeks+1
    m = compute_movers(df, dim_col="cat", week_col="wk", value_col="units", count_col="n")
    assert m.empty and list(m.columns)[0] == "cat"


def test_movers_deterministic() -> None:
    df = _weekly({"A": [100, 100, 100, 100, 250], "B": [100, 100, 100, 100, 250],
                  "C": [100, 100, 100, 100, 10]})
    a = compute_movers(df, dim_col="cat", week_col="wk", value_col="units", count_col="n")
    b = compute_movers(df, dim_col="cat", week_col="wk", value_col="units", count_col="n")
    assert a.equals(b)
    # tie between A and B (identical delta) breaks by name → A before B
    assert list(a["cat"]).index("A") < list(a["cat"]).index("B")


# ----- top_movers tool (lake-backed) -----------------------------------------

@needs_lake
def test_top_movers_reduces_pivot_and_stays_windowed() -> None:
    sql = ("SELECT date_trunc('week', txn_date) AS wk, category, "
           "SUM(qty) AS units, COUNT(*) AS n FROM lake_transactions "
           "WHERE peer_relationship='peer' GROUP BY wk, category")
    p = LT.top_movers("KRG", sql, source="lake", week_col="wk", dim_col="category",
                      value_col="units", count_col="n", top_n=3)
    assert p["input_rows"] > 50          # the raw pivot is large…
    assert p["row_count"] <= 6           # …reduced to ≤ 2*top_n movers
    assert p["movers_available"] is True
    assert set(["category", "recent", "baseline", "delta_pct", "direction"]).issubset(p["columns"])


@needs_lake
def test_top_movers_bad_source_rejected() -> None:
    with pytest.raises(LakeToolError, match="source"):
        LT.top_movers("KRG", "SELECT 1", source="bogus", week_col="w",
                      dim_col="d", value_col="v")


@needs_lake
def test_top_movers_missing_column_rejected() -> None:
    sql = ("SELECT date_trunc('week', txn_date) AS wk, category, SUM(qty) AS units "
           "FROM lake_transactions WHERE peer_relationship='peer' GROUP BY wk, category")
    with pytest.raises(LakeToolError, match="not in your query result"):
        LT.top_movers("KRG", sql, source="lake", week_col="wk", dim_col="category",
                      value_col="MISSPELLED")


# ----- anomaly specialist wiring + grounding (fake LLM) ----------------------

def test_anomaly_specialist_offers_top_movers() -> None:
    from src.agents.anomaly import AnomalyDetectionSpecialist
    names = {t["name"] for t in AnomalyDetectionSpecialist.TOOLS}
    assert "top_movers" in names
    assert names == {"schema_info", "query_tenant", "query_lake_sql",
                     "top_movers", "emit_response"}


@needs_lake
def test_anomaly_claim_resolves_against_movers_frame(monkeypatch) -> None:
    """A top_movers call captures the reduced frame as the tenant frame; a
    CellLookup on its `delta_pct` cell resolves (grounding preserved)."""
    from tests.agents._fake_llm import patch_llm, scripted_emit_response, scripted_tool_use
    from src.agents.anomaly import AnomalyDetectionSpecialist
    from src.agents.context import MerchantContext

    own = ("SELECT date_trunc('week', t.txn_ts) AS wk, p.functional_category AS category, "
           "SUM(i.qty) AS units, COUNT(*) AS n FROM transaction_items i "
           "JOIN transactions t ON i.txn_id=t.txn_id JOIN products p ON i.sku=p.sku "
           "WHERE t.banner_code='KRG' GROUP BY wk, category")
    frame = LT.top_movers("KRG", own, source="tenant", week_col="wk", dim_col="category",
                          value_col="units", count_col="n", top_n=3)["frame"]
    top = frame.iloc[0]
    cat, dp = top["category"], float(top["delta_pct"])

    emit = scripted_emit_response(
        headline=f"Your {cat} moved {dp:+.0%} versus your prior 4-week average.",
        evidence=[f"{cat} shifted {dp:+.0%} against the prior 4-week mean."],
        claims=[{"text_span": f"{dp:+.0%}", "value": dp,
                 "source": {"type": "CellLookup", "row_filter": {"category": cat},
                            "column": "delta_pct", "frame": "tenant"}}])
    script = [
        scripted_tool_use("top_movers", {"sql": own, "source": "tenant", "week_col": "wk",
                                         "dim_col": "category", "value_col": "units", "count_col": "n"}),
        emit,
    ]
    spec = AnomalyDetectionSpecialist(MerchantContext.for_merchant("KRG"))
    with patch_llm(monkeypatch, script):
        resp = spec.answer("Which categories are dropping unusually?")
    assert any(d.get("status") == "passed" for d in resp.claim_dispositions)
    assert not LT.is_narration(resp.prose)
