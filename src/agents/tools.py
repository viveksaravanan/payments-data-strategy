"""Agent tools.

Three tools (`schema_info`, `query_tenant`, `query_lake`) talk to SQLite; one
(`chart_spec`) is metadata-only. The SQL guard runs before any DB connection
opens — never trust the model to self-restrict.

Tenant isolation is enforced by a literal substring check for
``WHERE merchant_id = '<current_merchant>'`` (or ``"..."`` quoting). Per
PLAN.md §9.2 a regex check is sufficient for the demo.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "payments.db"
SCHEMA_PATH = ROOT / "src" / "db" / "schema.sql"

MAX_ROWS = 200


# ---------------------------------------------------------------------------
# Tool schemas (Anthropic SDK input_schema format)
# ---------------------------------------------------------------------------

SCHEMA_INFO_TOOL = {
    "name": "schema_info",
    "description": (
        "Returns the full DDL for the database — tenant_* and lake_* tables "
        "plus the shared merchants dimension. Call once at the start to ground "
        "your queries."
    ),
    "input_schema": {"type": "object", "properties": {}},
}

QUERY_TENANT_TOOL = {
    "name": "query_tenant",
    "description": (
        "Execute a single read-only SELECT against tenant_* tables. The runner "
        "enforces TWO rules: (1) only single SELECT statements; "
        "(2) the query MUST include WHERE merchant_id = '<current_merchant>' "
        "(quoted, equals literal). Queries that omit the predicate are rejected. "
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
        "Execute a single read-only SELECT against lake_* tables (cross-merchant "
        "anonymized aggregate). K-anonymity is applied; some `home_zip3` values "
        f"are NULL. Returns up to {MAX_ROWS} rows as JSON."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string",
                      "description": "SQL SELECT against lake_* tables."},
        },
        "required": ["query"],
    },
}

CHART_SPEC_TOOL = {
    "name": "chart_spec",
    "description": (
        "Declare a chart for the dashboard to render after the agent's final "
        "answer. Use column names from the LAST query's result."
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

TOOLS_MERCHANT = [SCHEMA_INFO_TOOL, QUERY_TENANT_TOOL, QUERY_LAKE_TOOL, CHART_SPEC_TOOL]
TOOLS_ANALYST = [SCHEMA_INFO_TOOL, QUERY_LAKE_TOOL, CHART_SPEC_TOOL]


# ---------------------------------------------------------------------------
# SQL guard
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


# ---------------------------------------------------------------------------
# DB execution
# ---------------------------------------------------------------------------

def _exec_select(sql: str, db_path: Path) -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        cur = conn.execute(sql)
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


def query_lake(query: str, db_path: Path | None = None) -> dict[str, Any]:
    if not is_safe_select(query):
        raise ValueError("Only a single read-only SELECT statement is allowed.")
    return _exec_select(query, db_path or DB_PATH)


def chart_spec(type: str, x: str, y: str, title: str) -> dict[str, Any]:
    return {"type": type, "x": x, "y": y, "title": title}
