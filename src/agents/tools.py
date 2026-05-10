"""Agent tools.

Four tools — `schema_info`, `query_tenant`, `query_lake`, `chart_spec`.
SQL guards run before any DB connection opens; never trust the model
to self-restrict.

  - **`query_tenant`** enforces tenant isolation: every query must
    contain ``WHERE merchant_id = '<current_merchant>'`` (or the
    double-quoted equivalent) before any DB connection opens.

  - **`query_lake`** wraps the agent's SELECT in two CTEs that compute
    the v2.5 virtual lake from the tenant tables and bake in the
    viewing merchant. The CTE bodies come from
    ``src.lake.views.lake_transactions_sql`` and ``lake_stores_sql``.
    Direct references to legacy v2 lake table names that aren't part
    of the v2.5 model (`lake_customers`, `lake_transaction_items`)
    and references to `tenant_*` tables are rejected.

The runner in ``advisor.py`` injects ``current_merchant`` /
``viewing_merchant_id`` into the tool calls — neither is in the
LLM-visible tool input schema.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from src.lake import (
    lake_stores_sql,
    lake_transactions_sql,
    register_lake_functions,
)

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "payments.db"
SCHEMA_PATH = ROOT / "src" / "db" / "schema.sql"

MAX_ROWS = 200

# Lake table names the agent is allowed to reference. The runner wraps
# the agent's SQL in CTEs of these names that compute the v2.5 virtual
# lake from the tenant tables.
ALLOWED_LAKE_VIEWS = ("lake_transactions", "lake_stores")

# Legacy v2 lake table names that the agent must NOT reference. These
# tables don't exist in v2.5 (the lake is virtual) — the rejection
# gives a clearer error than "no such table" for anyone copying old
# query patterns.
FORBIDDEN_LAKE_TABLES = ("lake_customers", "lake_transaction_items")


# ---------------------------------------------------------------------------
# Tool schemas (Anthropic SDK input_schema format)
# ---------------------------------------------------------------------------

SCHEMA_INFO_TOOL = {
    "name": "schema_info",
    "description": (
        "Returns the full DDL for the database — tenant_* tables plus "
        "the shared merchants dimension. The lake is virtual: the lake "
        "tools expose two logical tables, `lake_transactions` (21 cols, "
        "one row per peer line item) and `lake_stores` (6 cols, peer "
        "store reference). Call schema_info once at the start to ground "
        "your queries."
    ),
    "input_schema": {"type": "object", "properties": {}},
}

QUERY_TENANT_TOOL = {
    "name": "query_tenant",
    "description": (
        "Execute a single read-only SELECT against tenant_* tables. The "
        "runner enforces TWO rules: (1) only single SELECT statements; "
        "(2) the query MUST include WHERE merchant_id = "
        "'<current_merchant>' (quoted, equals literal). Queries lacking "
        "the predicate are rejected. "
        f"Returns up to {MAX_ROWS} rows as JSON."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string",
                      "description": "SQL SELECT against tenant_* tables."},
        },
        "required": ["query"],
    },
}

QUERY_LAKE_TOOL = {
    "name": "query_lake",
    "description": (
        "Execute a single read-only SELECT against the lake. Two logical "
        "tables are exposed: `lake_transactions` (one row per peer line "
        "item with peer_id / peer_segment / opaque IDs / generalized "
        "timestamps and txn-total bins / canonical product info; "
        "`customer_id` is dropped) and `lake_stores` (peer store "
        "reference at ZIP3 + neighborhood granularity). The viewing "
        "merchant is automatically excluded; peers appear as peer_a / "
        "peer_b / peer_c / peer_d per the documented mapping. "
        "Direct references to tenant_* tables and to physical v2 lake "
        "tables outside the v2.5 virtual model are rejected. "
        f"Returns up to {MAX_ROWS} rows as JSON."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "SQL SELECT against `lake_transactions` and/or "
                    "`lake_stores`."
                ),
            },
        },
        "required": ["query"],
    },
}

CHART_SPEC_TOOL = {
    "name": "chart_spec",
    "description": (
        "Declare a chart for the dashboard to render after the agent's "
        "final answer. Use column names from the LAST query's result."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "type":  {"type": "string", "enum": ["bar", "line"]},
            "x":     {"type": "string"},
            "y":     {"type": "string"},
            "title": {"type": "string"},
        },
        "required": ["type", "x", "y", "title"],
    },
}

TOOLS_MERCHANT = [
    SCHEMA_INFO_TOOL,
    QUERY_TENANT_TOOL,
    QUERY_LAKE_TOOL,
    CHART_SPEC_TOOL,
]


# ---------------------------------------------------------------------------
# SQL guards
# ---------------------------------------------------------------------------

_FORBIDDEN = re.compile(
    r"\b(DROP|INSERT|UPDATE|DELETE|ATTACH|DETACH|ALTER|CREATE|REPLACE|"
    r"GRANT|REVOKE|TRUNCATE|VACUUM|PRAGMA|EXEC|EXECUTE)\b",
    re.IGNORECASE,
)
_LEADING = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)


def is_safe_select(sql: str) -> bool:
    """True if `sql` is a single SELECT (or CTE) free of write keywords."""
    if not sql or not sql.strip():
        return False
    s = sql.strip().rstrip(";").strip()
    if ";" in s:
        return False  # multi-statement
    if _FORBIDDEN.search(s):
        return False
    return bool(_LEADING.match(s))


def has_merchant_predicate(sql: str, merchant_id: str) -> bool:
    """True if `sql` contains a literal ``merchant_id = '<merchant_id>'`` filter."""
    pat = re.compile(
        rf"merchant_id\s*=\s*['\"]{re.escape(merchant_id)}['\"]",
        re.IGNORECASE,
    )
    return bool(pat.search(sql))


def _references(sql: str, names: tuple[str, ...]) -> list[str]:
    """Return the subset of `names` that appear as standalone tokens in
    `sql` (case-insensitive whole-word match)."""
    found: list[str] = []
    for n in names:
        if re.search(rf"\b{re.escape(n)}\b", sql, re.IGNORECASE):
            found.append(n)
    return found


def _validate_lake_query(sql: str) -> None:
    """Reject lake queries that violate the v2.5 separation rules."""
    forbidden = _references(sql, FORBIDDEN_LAKE_TABLES)
    if forbidden:
        raise ValueError(
            f"Lake queries cannot reference physical v2 lake tables that "
            f"aren't part of the v2.5 virtual model: {forbidden}. "
            f"Use lake_transactions and/or lake_stores instead."
        )
    if re.search(r"\btenant_\w+\b", sql, re.IGNORECASE):
        raise ValueError(
            "Lake queries cannot reference tenant_* tables. "
            "Use query_tenant for own-merchant data."
        )
    if not _references(sql, ALLOWED_LAKE_VIEWS):
        raise ValueError(
            "Lake queries must reference at least one of "
            f"{list(ALLOWED_LAKE_VIEWS)}."
        )


# ---------------------------------------------------------------------------
# DB execution
# ---------------------------------------------------------------------------

def _exec_select(
    sql: str,
    db_path: Path,
    *,
    params: dict[str, Any] | tuple | None = None,
    register_lake: bool = False,
) -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        if register_lake:
            register_lake_functions(conn)
        cur = conn.execute(sql, params if params is not None else ())
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(MAX_ROWS + 1)
        truncated = len(rows) > MAX_ROWS
        rows = rows[:MAX_ROWS]
        return {
            "columns":   cols,
            "rows":      [list(r) for r in rows],
            "row_count": len(rows),
            "truncated": truncated,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def schema_info() -> dict[str, Any]:
    return {"ddl": SCHEMA_PATH.read_text()}


def query_tenant(
    query: str,
    current_merchant: str,
    db_path: Path | None = None,
) -> dict[str, Any]:
    if not is_safe_select(query):
        raise ValueError("Only a single read-only SELECT statement is allowed.")
    if not has_merchant_predicate(query, current_merchant):
        raise ValueError(
            f"Tenant queries must include WHERE merchant_id = '{current_merchant}'."
        )
    return _exec_select(query, db_path or DB_PATH)


def query_lake(
    query: str,
    viewing_merchant_id: str,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Run a SELECT against the lake from a viewing merchant's perspective.

    The agent writes ordinary SQL referencing ``lake_transactions`` /
    ``lake_stores``. The runner wraps it in CTEs that compute those
    views from the tenant tables, baking in ``viewing_merchant_id`` so
    the viewer's own data is excluded and peers are pseudonymized.
    References to legacy v2 physical lake table names outside the v2.5
    model and to ``tenant_*`` tables are rejected.
    """
    if not is_safe_select(query):
        raise ValueError("Only a single read-only SELECT statement is allowed.")

    _validate_lake_query(query)
    txn_inner = lake_transactions_sql(viewing_merchant_id)
    stores_inner = lake_stores_sql(viewing_merchant_id)
    wrapped = (
        "WITH lake_transactions AS (\n"
        f"{txn_inner}\n"
        "), lake_stores AS (\n"
        f"{stores_inner}\n"
        ")\n"
        f"{query}"
    )
    return _exec_select(
        wrapped,
        db_path or DB_PATH,
        params={"viewing": viewing_merchant_id},
        register_lake=True,
    )


def chart_spec(type: str, x: str, y: str, title: str) -> dict[str, Any]:
    return {"type": type, "x": x, "y": y, "title": title}
