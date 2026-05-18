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
from src.lake.views import K_ANONYMITY_K

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "payments.db"
SCHEMA_PATH = ROOT / "src" / "db" / "schema.sql"

MAX_ROWS = 200

# Phase 2A.5 — payload economy. Even when the query returns more, we
# only serialize this many rows back to the LLM (top-N, with a "showing
# top X of N rows" note). Cuts input-token growth on subsequent turns
# by 50-70% on broad queries. The LLM can refine its query if it needs
# more.
LLM_ROW_BUDGET = 20

# Round floating-point values to this precision when serializing to
# the LLM. The underlying data is unchanged; this just trims tokens.
LLM_FLOAT_PRECISION = 2

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

# Phase 2 — structured chart spec. The LLM passes a richer description
# (kind + title + x labels + named series with values + y format), and
# the specialist builds a Plotly Figure server-side. The figure is
# stored on the specialist for inclusion in the final response; the
# LLM gets back a small acknowledgment dict.
MAKE_CHART_TOOL = {
    "name": "make_chart",
    "description": (
        "Build a comparative Plotly chart for the final response. Call "
        "this AFTER you have the data you want to visualize, and at most "
        "ONCE per response. Pick `kind` based on the data shape: "
        "`grouped_bar` for own-vs-peer comparison across N categories; "
        "`horizontal_bar` for a single ranked list; `line` for time "
        "series; `donut` for category composition. Values must come "
        "from your query results — do not invent numbers."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "kind":  {"type": "string",
                       "enum": ["grouped_bar", "horizontal_bar", "line", "donut"]},
            "title": {"type": "string"},
            "x":     {"type": "array", "items": {"type": "string"},
                       "description": "Category / x-axis labels."},
            "series": {
                "type": "array",
                "description": "One or more named series. For grouped_bar "
                                 "use multiple series; for horizontal_bar / "
                                 "donut use a single series.",
                "items": {
                    "type": "object",
                    "properties": {
                        "name":   {"type": "string"},
                        "values": {"type": "array", "items": {"type": "number"}},
                    },
                    "required": ["name", "values"],
                },
            },
            "y_format": {
                "type": "string",
                "enum":    ["currency", "count", "pct", "float"],
                "description": "Tick / hover formatting. Default float.",
            },
            "x_axis_title": {"type": "string"},
            "y_axis_title": {"type": "string"},
        },
        "required": ["kind", "title", "x", "series"],
    },
}

TOOLS_MERCHANT = [
    SCHEMA_INFO_TOOL,
    QUERY_TENANT_TOOL,
    QUERY_LAKE_TOOL,
    CHART_SPEC_TOOL,
]

# Phase 2 specialist tools — adds make_chart, drops the legacy
# chart_spec (specialists use the richer tool).
TOOLS_SPECIALIST = [
    SCHEMA_INFO_TOOL,
    QUERY_TENANT_TOOL,
    QUERY_LAKE_TOOL,
    MAKE_CHART_TOOL,
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
    """Execute a SELECT and return all rows up to MAX_ROWS. Returns
    `{columns, rows, row_count, truncated}` with full-precision values
    (no LLM-payload optimization applied — that happens in
    `trim_for_llm` separately so the specialist's `_last_table` keeps
    full data for the final response.)
    """
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


def trim_for_llm(result: dict[str, Any]) -> dict[str, Any]:
    """Phase 2A.5: prepare a tool result for the LLM tool_result payload.

    - Cap rows at LLM_ROW_BUDGET (default 20); add a "note" field so
      the model knows there's more if it refines.
    - Round float values to LLM_FLOAT_PRECISION decimals.
    - Drop columns whose values are entirely None / empty (uncommon but
      pure noise when it happens).

    The specialist keeps the full-precision result internally for
    rendering the final table; this trim only affects what the LLM
    sees on the next turn.
    """
    columns = list(result.get("columns") or [])
    rows = result.get("rows") or []
    if not columns or not rows:
        return result

    # Identify columns where every value is None or "" (skip).
    keep_idx = [
        i for i, _ in enumerate(columns)
        if any(r[i] is not None and r[i] != "" for r in rows)
    ]
    if len(keep_idx) < len(columns):
        columns = [columns[i] for i in keep_idx]
        rows = [[r[i] for i in keep_idx] for r in rows]

    # Round floats. Booleans are a subtype of int — keep them as-is.
    rounded_rows: list[list[Any]] = []
    for r in rows:
        out: list[Any] = []
        for v in r:
            if isinstance(v, float):
                out.append(round(v, LLM_FLOAT_PRECISION))
            else:
                out.append(v)
        rounded_rows.append(out)

    total = len(rounded_rows)
    note = None
    if total > LLM_ROW_BUDGET:
        rounded_rows = rounded_rows[:LLM_ROW_BUDGET]
        note = (
            f"showing top {LLM_ROW_BUDGET} of {total} rows; "
            "refine your query (add ORDER BY / LIMIT / WHERE) for "
            "more specific results"
        )

    trimmed: dict[str, Any] = {
        "columns":   columns,
        "rows":      rounded_rows,
        "row_count": len(rounded_rows),
    }
    if note:
        trimmed["note"] = note
    if result.get("truncated"):
        trimmed["truncated_at_max"] = True
    return trimmed


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def schema_info() -> dict[str, Any]:
    return {"ddl": SCHEMA_PATH.read_text()}


# Phase 1.5 (Decision §1.5): tenant tables the agent may reference. The
# runner CTE-wraps the agent's SQL so each name resolves to the
# corresponding `tenant_view_<viewer>_<suffix>` defined in
# `src/db/schema.sql`. The CTE wrap is the primary isolation mechanism;
# the `has_merchant_predicate` regex below remains as a second layer.
_TENANT_TABLES = (
    "tenant_customers",
    "tenant_stores",
    "tenant_products",
    "tenant_transactions",
    "tenant_transaction_items",
    "tenant_promotions",
)
_VALID_VIEWERS = ("KRG", "ACM", "WDX", "TBL", "TJX")


def _tenant_view_cte(viewer: str) -> str:
    """Build the leading CTE block that shadows every `tenant_<table>`
    name with the per-viewer view from `schema.sql`. Prepended to the
    agent's SELECT before execution.
    """
    parts = [
        f"  {tbl} AS (SELECT * FROM tenant_view_{viewer}_{tbl[len('tenant_'):]})"
        for tbl in _TENANT_TABLES
    ]
    return "WITH " + ",\n".join(parts) + "\n"


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
    if current_merchant not in _VALID_VIEWERS:
        raise ValueError(
            f"Unknown current_merchant {current_merchant!r}. "
            f"Expected one of {list(_VALID_VIEWERS)}."
        )
    wrapped = _tenant_view_cte(current_merchant) + query
    return _exec_select(wrapped, db_path or DB_PATH)


# Phase 1.5 (Decision §1.2): names that look like aggregate counts and
# trigger k=5 suppression on lake results. The agents' prompts ask for
# `COUNT(*) AS n` on customer-dimension breakdowns; the looser
# `_count` / `_n` suffix rule catches cases where the agent picks a
# different alias.
_EXACT_COUNT_NAMES = frozenset({"count", "cnt", "n", "row_count", "num_rows"})


def _find_count_column_index(columns: list[str]) -> int | None:
    """Return the index of the first count-like column, or None."""
    for i, col in enumerate(columns):
        low = col.lower()
        if low in _EXACT_COUNT_NAMES:
            return i
        if low.endswith("_count") or low.endswith("_n"):
            return i
    return None


def _maybe_suppress_sub_k(
    result: dict[str, Any], k: int = K_ANONYMITY_K,
) -> dict[str, Any]:
    """If the lake result has a count column, drop rows where that
    count is below `k` and append a `"suppression"` note. No-op if no
    count column is detected (the prompts ask agents to include
    `COUNT(*) AS n` on customer-dimension breakdowns precisely so this
    hook can fire)."""
    cols = result.get("columns") or []
    if not cols:
        return result
    idx = _find_count_column_index(cols)
    if idx is None:
        return result
    rows = result.get("rows") or []
    kept = []
    for r in rows:
        v = r[idx]
        try:
            if v is not None and float(v) >= k:
                kept.append(r)
        except (TypeError, ValueError):
            # Non-numeric column we matched on name — bail safely
            # rather than silently dropping every row.
            return result
    suppressed = len(rows) - len(kept)
    if suppressed == 0:
        return result
    result = dict(result)
    result["rows"] = kept
    result["row_count"] = len(kept)
    result["suppression"] = (
        f"{suppressed} row(s) below k={k} suppressed for privacy "
        f"(count column: '{cols[idx]}')."
    )
    return result


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

    Phase 1.5: when the result contains a count-like column (`n`,
    `count`, `cnt`, `*_count`, `*_n`, etc.), rows below k=5 are
    suppressed and a `"suppression"` note is added to the result.
    Agents are instructed (in their system prompts) to include
    `COUNT(*) AS n` on customer-dimension breakdowns so suppression
    can apply.
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
    # Phase 1.5: the CTE body resolves to a SELECT against the
    # per-viewer materialized table; no `:viewing` bind param and no
    # UDFs are referenced at runtime. The lake-functions registration
    # and the `viewing` param became dead after materialization.
    result = _exec_select(wrapped, db_path or DB_PATH)
    return _maybe_suppress_sub_k(result)


def chart_spec(type: str, x: str, y: str, title: str) -> dict[str, Any]:
    return {"type": type, "x": x, "y": y, "title": title}


# ---------------------------------------------------------------------------
# Phase 2: make_chart
# ---------------------------------------------------------------------------

# Editorial palette — same as docs/report.html and the dashboard.
_CHART_PALETTE = [
    "#0F4C81",  # accent
    "#3A6FA5",
    "#6F8FB8",
    "#C0563F",
    "#5B7B58",
    "#B7791F",
    "#9A8BB5",
    "#3F8B92",
]

_TICK_FORMAT = {
    "currency": {"tickprefix": "$", "tickformat": ",.2f"},
    "count":    {"tickformat": ",.0f"},
    "pct":      {"tickformat": ".1f", "ticksuffix": "%"},
    "float":    {"tickformat": ",.2f"},
}


def make_chart(spec: dict[str, Any]) -> "Any":
    """Build a Plotly Figure from a structured spec. Returns the actual
    `go.Figure` (not a dict); the specialist stores it on the response.

    Spec shape:
        {
            "kind":   "grouped_bar" | "horizontal_bar" | "line" | "donut",
            "title":  str,
            "x":      list[str],
            "series": [{"name": str, "values": list[float]}, ...],
            "y_format":     "currency" | "count" | "pct" | "float",
            "x_axis_title": str,
            "y_axis_title": str,
        }
    """
    import plotly.graph_objects as go  # lazy import — keeps non-dashboard CLI paths light

    kind   = spec["kind"]
    title  = spec.get("title", "")
    x      = list(spec["x"])
    series = spec["series"]
    yfmt   = spec.get("y_format", "float")
    tickfmt = _TICK_FORMAT.get(yfmt, _TICK_FORMAT["float"])

    base_layout = {
        "title": {"text": title, "x": 0, "xanchor": "left",
                   "font": {"size": 13, "color": "#1A1F2E"}},
        "font": {"family": "-apple-system, BlinkMacSystemFont, 'Segoe UI', Inter, Roboto, sans-serif",
                  "size": 12, "color": "#4A5161"},
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "rgba(0,0,0,0)",
        "margin": {"l": 60, "r": 24, "t": 36, "b": 56},
        "hoverlabel": {"bgcolor": "#1A1F2E",
                        "font": {"color": "#fff", "size": 11}},
        "showlegend": len(series) > 1,
        "legend": {"orientation": "h", "y": -0.22, "x": 0,
                    "xanchor": "left", "font": {"size": 11}},
        "height": 300,
    }

    if kind == "donut":
        s = series[0]
        fig = go.Figure(go.Pie(
            labels=x, values=s["values"], hole=0.55,
            marker={"colors": _CHART_PALETTE * 3},
            hovertemplate="<b>%{label}</b><br>%{value:,.2f}<br>%{percent}<extra></extra>",
            textinfo="label+percent",
            textfont={"size": 11},
        ))
        fig.update_layout(**base_layout)
        return fig

    if kind == "line":
        fig = go.Figure()
        for i, s in enumerate(series):
            color = _CHART_PALETTE[i % len(_CHART_PALETTE)]
            fig.add_trace(go.Scatter(
                x=x, y=s["values"], mode="lines+markers",
                name=s["name"],
                line={"color": color, "width": 1.8},
                marker={"size": 6, "color": color},
                hovertemplate=f"<b>{s['name']}</b><br>%{{x}}<br>%{{y:,.2f}}<extra></extra>",
            ))
        fig.update_layout(
            **base_layout,
            xaxis={"title": spec.get("x_axis_title", ""),
                    "gridcolor": "#E2E5EA", "linecolor": "#E2E5EA",
                    "ticks": "outside", "tickcolor": "#E2E5EA"},
            yaxis={"title": spec.get("y_axis_title", ""),
                    "gridcolor": "#E2E5EA", "linecolor": "#E2E5EA",
                    "ticks": "outside", "tickcolor": "#E2E5EA",
                    "rangemode": "tozero", **tickfmt},
        )
        return fig

    # Bar variants
    if kind == "horizontal_bar":
        s = series[0]
        color = _CHART_PALETTE[0]
        fig = go.Figure(go.Bar(
            x=list(reversed(s["values"])),
            y=list(reversed(x)),
            orientation="h",
            marker={"color": color},
            hovertemplate="<b>%{y}</b><br>%{x:,.2f}<extra></extra>",
        ))
        fig.update_layout(
            **base_layout,
            xaxis={"title": spec.get("x_axis_title", ""),
                    "gridcolor": "#E2E5EA", "linecolor": "#E2E5EA",
                    "ticks": "outside", "tickcolor": "#E2E5EA", **tickfmt},
            yaxis={"title": spec.get("y_axis_title", ""),
                    "showgrid": False, "linecolor": "#E2E5EA",
                    "tickfont": {"size": 11}, "automargin": True},
        )
        return fig

    # grouped_bar (default)
    fig = go.Figure()
    for i, s in enumerate(series):
        color = _CHART_PALETTE[i % len(_CHART_PALETTE)]
        fig.add_trace(go.Bar(
            x=x, y=s["values"],
            name=s["name"],
            marker={"color": color},
            hovertemplate=f"<b>{s['name']}</b><br>%{{x}}<br>%{{y:,.2f}}<extra></extra>",
        ))
    fig.update_layout(
        **{**base_layout, "barmode": "group"},
        xaxis={"title": spec.get("x_axis_title", ""),
                "linecolor": "#E2E5EA", "ticks": "outside",
                "tickcolor": "#E2E5EA", "showgrid": False,
                "tickfont": {"size": 10}},
        yaxis={"title": spec.get("y_axis_title", ""),
                "gridcolor": "#E2E5EA", "linecolor": "#E2E5EA",
                "ticks": "outside", "tickcolor": "#E2E5EA",
                "rangemode": "tozero", **tickfmt},
    )
    return fig


def make_chart_ack(spec: dict[str, Any]) -> dict[str, Any]:
    """Lightweight acknowledgment returned to the LLM after a successful
    make_chart call. The actual Figure stays with the specialist; this
    dict is what gets serialized into the tool_result the model sees."""
    return {
        "chart_registered": True,
        "kind":   spec.get("kind"),
        "title":  spec.get("title"),
        "series_count": len(spec.get("series", [])),
        "point_count":  len(spec.get("x", [])),
    }
