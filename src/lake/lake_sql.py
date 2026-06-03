"""Wave 3.5 §7-§8 `query_lake_sql` enforcement layer.

The runtime guard for the line-item peer lake, parallel to
`isolation.py` (which guards the tenant surface). It mirrors the
`query_tenant` motion but enforces the lake's privacy contract instead
of a viewer predicate:

* **Single SELECT only** — no DDL/DML/multiple statements (§8).
* **Aggregating-only** (§7.2) — the *outermost* SELECT must be the
  result-grain aggregation: it has a `GROUP BY`, or every projected
  expression is aggregate-bearing. A bare `SELECT *` or a
  non-aggregating projection (`SELECT category, unit_price …`) would
  return raw individual lines and is rejected. This same rule rejects
  the aggregate-in-CTE-then-passthrough shape (§7.3), so the injected
  count provably tallies base lines.
* **Table allowlist** — only `lake_transactions` / `lake_stores`
  (plus the query's own CTEs) are readable; `read_parquet`-style table
  functions are rejected so the agent can't reach around the lake to
  the raw census.
* **Count injection + k=5 suppression** (§7.3) — a `COUNT(*) AS _k` is
  spliced into the outermost projection, groups with `_k <
  LAKE_K_FLOOR` are dropped, `_k` is stripped before the agent sees
  the result, and the dropped-group count is surfaced.

All AST work uses DuckDB's own parser (`json_serialize_sql` /
`json_deserialize_sql`) — no new dependency, no regex GROUP-BY
guessing. Viewer scoping is done by registering the viewer's
materialized pair as the `lake_transactions` / `lake_stores` views in
the execution connection.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_LAKE_ITEMS = REPO_ROOT / "data" / "lake" / "items"

# Single configurable privacy constant — raise later without code change.
LAKE_K_FLOOR = 5

# The only table names the agent may reference (besides its own CTEs).
ALLOWED_LAKE_TABLES = frozenset({"lake_transactions", "lake_stores"})

VALID_MERCHANTS = frozenset({"ACM", "KRG", "TBL", "TJX", "WDX"})

# Aggregate function names that make a no-GROUP-BY projection legal as a
# whole-table aggregate. Generous but bounded; anything else must use a
# GROUP BY. `count_star` is how DuckDB names `COUNT(*)`.
_AGG_FUNCS = frozenset({
    "count", "count_star", "sum", "avg", "mean", "min", "max",
    "median", "mode", "stddev", "stddev_pop", "stddev_samp",
    "variance", "var_pop", "var_samp", "product",
    "quantile", "quantile_cont", "quantile_disc",
    "approx_count_distinct", "approx_quantile",
    "first", "last", "arg_min", "arg_max", "bool_and", "bool_or",
})


class LakeSqlError(ValueError):
    """Raised when a `query_lake_sql` statement is malformed or violates
    the aggregating-only / table-allowlist contract. The message is fed
    back to the model so it can correct the query."""


# ----- AST helpers -------------------------------------------------------

def _parse(con: duckdb.DuckDBPyConnection, sql: str) -> dict[str, Any]:
    """Parse ``sql`` to its DuckDB AST. Raises ``LakeSqlError`` on a
    parse error or anything that isn't exactly one statement."""
    try:
        raw = con.execute("SELECT json_serialize_sql(?)", [sql]).fetchone()[0]
    except duckdb.Error as exc:  # pragma: no cover - serializer rarely throws
        raise LakeSqlError(f"Could not parse SQL: {exc}") from exc
    ast = json.loads(raw)
    if ast.get("error"):
        raise LakeSqlError(
            f"SQL parse error: {ast.get('error_message', 'invalid SQL')}."
        )
    statements = ast.get("statements") or []
    if len(statements) != 1:
        raise LakeSqlError(
            "query_lake_sql accepts exactly one SELECT statement "
            f"(got {len(statements)})."
        )
    return ast


def _iter_nodes(obj: Any):
    """Yield every dict in the AST tree (depth-first)."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _iter_nodes(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_nodes(v)


def _has_aggregate(expr: Any) -> bool:
    """True if ``expr``'s subtree contains an aggregate function call —
    so `SUM(x)/COUNT(*)` counts as aggregate-bearing, a bare column
    does not."""
    for node in _iter_nodes(expr):
        if (
            node.get("class") == "FUNCTION"
            and str(node.get("function_name", "")).lower() in _AGG_FUNCS
        ):
            return True
    return False


def _check_aggregating(node: dict[str, Any]) -> None:
    """Enforce §7.2/§7.3: the outermost SELECT is the result-grain
    aggregation. Raises ``LakeSqlError`` otherwise."""
    if node.get("type") != "SELECT_NODE":
        raise LakeSqlError(
            "query_lake_sql accepts a single SELECT (no UNION / set "
            "operations) — wrap the comparison in two separate queries."
        )
    select_list = node.get("select_list") or []
    if any(item.get("type") == "STAR" for item in select_list):
        raise LakeSqlError(
            "`SELECT *` is not allowed — the lake returns aggregates "
            "only. Group by a dimension and select aggregate metrics "
            "(e.g. AVG(unit_price), COUNT(DISTINCT lake_txn_id))."
        )
    if node.get("group_expressions"):
        return  # explicit GROUP BY (also GROUPING SETS / ROLLUP / CUBE)
    if node.get("aggregate_handling") == "FORCE_AGGREGATES":
        return  # GROUP BY ALL — DuckDB groups by the non-aggregate cols
    # No GROUP BY → only a whole-table aggregate is allowed.
    if select_list and all(_has_aggregate(item) for item in select_list):
        return
    raise LakeSqlError(
        "Non-aggregating query rejected: the lake returns aggregates "
        "only, never raw line rows. Add a GROUP BY (and aggregate the "
        "metric columns), or write a whole-table aggregate. Aggregation "
        "must be at the top-level SELECT, not inside a CTE the outer "
        "query merely selects from."
    )


def _check_tables(ast: dict[str, Any]) -> None:
    """Allow only `lake_transactions` / `lake_stores` + the query's own
    CTEs; reject `read_parquet`-style table functions anywhere in the
    tree (so the agent can't reach the raw census)."""
    cte_names: set[str] = set()
    base_tables: set[str] = set()
    for node in _iter_nodes(ast):
        if not isinstance(node, dict):
            continue
        ntype = node.get("type")
        if ntype == "TABLE_FUNCTION":
            fn = (node.get("function") or {}).get("function_name", "a table function")
            raise LakeSqlError(
                f"Table function {fn!r} is not allowed — query only "
                "`lake_transactions` and `lake_stores`."
            )
        if ntype == "BASE_TABLE":
            base_tables.add(str(node.get("table_name", "")))
        # CTE names are the keys of every cte_map encountered.
        cte_map = node.get("cte_map")
        if isinstance(cte_map, dict):
            for entry in cte_map.get("map") or []:
                if isinstance(entry, dict) and "key" in entry:
                    cte_names.add(str(entry["key"]))
    allowed = ALLOWED_LAKE_TABLES | cte_names
    illegal = {t for t in base_tables if t and t not in allowed}
    if illegal:
        raise LakeSqlError(
            f"Unknown table(s) {sorted(illegal)} — the lake exposes only "
            f"{sorted(ALLOWED_LAKE_TABLES)} (plus your own CTEs)."
        )


def _inject_count(con: duckdb.DuckDBPyConnection, ast: dict[str, Any]) -> str:
    """Splice ``COUNT(*) AS _k`` into the outermost SELECT's projection
    and return the rewritten SQL. Robust to any FROM/WHERE/JOIN/CTE
    shape because only the projection is touched."""
    ref = json.loads(
        con.execute("SELECT json_serialize_sql(?)", ["SELECT COUNT(*) AS _k"]).fetchone()[0]
    )
    count_node = ref["statements"][0]["node"]["select_list"][0]
    ast["statements"][0]["node"]["select_list"].append(count_node)
    return con.execute("SELECT json_deserialize_sql(?)", [json.dumps(ast)]).fetchone()[0]


# ----- viewer scoping ----------------------------------------------------

def _register_viewer_views(con: duckdb.DuckDBPyConnection, viewer: str) -> None:
    """Register the viewer's materialized pair as the `lake_transactions`
    / `lake_stores` views, so the agent's unqualified `FROM
    lake_transactions` resolves to that viewer's peers (§8 scoping)."""
    if viewer not in VALID_MERCHANTS:
        raise LakeSqlError(
            f"Unknown viewer {viewer!r}. Valid: {sorted(VALID_MERCHANTS)}."
        )
    vdir = DATA_LAKE_ITEMS / viewer
    for table in ("lake_transactions", "lake_stores"):
        path = vdir / f"{table}.parquet"
        if not path.exists():
            raise LakeSqlError(
                f"Line-item lake not materialized at {path}. Run "
                "`make lake-items` to build it from data/raw/."
            )
        con.execute(
            f"CREATE VIEW {table} AS SELECT * FROM read_parquet('{path}')"
        )


# ----- public entrypoint -------------------------------------------------

def run_lake_sql(viewer: str, sql: str) -> tuple[pd.DataFrame, int]:
    """Validate, scope, count-inject, execute, and k=5-suppress a lake
    query. Returns ``(result_df, n_suppressed)``. The result carries no
    ``_k`` column. Raises ``LakeSqlError`` on a contract violation and
    ``duckdb.Error`` (wrapped by the caller) on execution failure."""
    con = duckdb.connect(":memory:")
    ast = _parse(con, sql)
    node = ast["statements"][0]["node"]
    _check_aggregating(node)
    _check_tables(ast)

    _register_viewer_views(con, viewer)
    injected = _inject_count(con, ast)

    full = con.execute(injected).df()
    if "_k" not in full.columns:  # pragma: no cover - injection guarantees it
        raise LakeSqlError("Internal: count injection failed.")

    kept = full[full["_k"] >= LAKE_K_FLOOR].copy()
    n_suppressed = int(len(full) - len(kept))
    result = kept.drop(columns=["_k"]).reset_index(drop=True)
    return result, n_suppressed
