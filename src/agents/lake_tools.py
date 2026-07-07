"""Wave 3 §2 lake tools — the two tool surfaces specialists call.

* `query_tenant(viewer, sql)` — runs viewer-scoped SQL against the
  tenant census (`data/raw/`) via DuckDB + Wave 2 isolation guards
  (`check_tenant_predicate` + `wrap_tenant_query`). Returns the
  result as a `{rows, columns, row_count}` dict.
* `read_lake_table(viewer, table, filters)` — reads from
  `data/lake/<table>.parquet`, validates filters against the
  manifest dimensions, applies `scope_for_viewer` (drops the viewer's
  rows + adds `peer_relationship` + strips `banner_code`), asserts
  no identity leak, returns the scoped frame + manifest excludes.

Specialists call these via the bounded tool loop in
`src/agents/specialist.py`. The Wave 2 ``observable_guard`` is the
write-time invariant; this module is the runtime, viewer-aware
read surface.

Two tool schemas — ``QUERY_TENANT_TOOL`` and ``READ_LAKE_TOOL`` —
are exposed for the Anthropic SDK tool list. They're combined into
``TOOLS_SPECIALIST`` for convenience.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from src.agents.wow import compute_movers
from src.lake.isolation import (
    TenantIsolationError,
    check_tenant_predicate,
    wrap_tenant_query,
)
from src.lake.lake_sql import LakeSqlError, run_lake_sql

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_RAW = REPO_ROOT / "data" / "raw"
DATA_LAKE = REPO_ROOT / "data" / "lake"


# Hard cap on rows returned to the LLM per tool call. The full result
# survives in the specialist's state for the chart/merge; the LLM only
# needs a sample to ground its prose.
LLM_ROW_BUDGET = 50


class LakeToolError(ValueError):
    """Raised when a lake-tool call is malformed or violates a grain
    boundary; the message is fed back to the model so it can retry
    or decline gracefully."""


# ---------------------------------------------------------------------
# query_tenant
# ---------------------------------------------------------------------

_TENANT_TABLE_DESCRIPTIONS: dict[str, dict[str, Any]] = {
    "transactions": {
        "description": (
            "One row per transaction. Every row has banner_code; "
            "every tenant query MUST include WHERE banner_code = "
            "'<viewer>'."
        ),
        "primary_key": ["txn_id"],
        "join_keys": {
            "store_id":      "→ stores.store_id (which store rang the txn)",
            "customer_token": "→ customers.card_id (hashed card id)",
        },
    },
    "transaction_items": {
        "description": (
            "Line items — one row per (txn_id, line_id). Carries ONLY "
            "sku, qty, unit_price, discount, line_total, promo_id. "
            "Product NAME + all taxonomy are NOT on the line — you MUST "
            "JOIN to products on sku to resolve them. NOTE: NO banner_code "
            "on this table — scope by joining to transactions and using "
            "transactions.banner_code = '<viewer>'."
        ),
        "primary_key": ["txn_id", "line_id"],
        "join_keys": {
            "txn_id":   "→ transactions.txn_id",
            "sku":      "→ products.sku. For answers about YOUR OWN data, "
                        "group by merchant_department/category/subcategory "
                        "(your real shelf labels). For any comparison to "
                        "PEERS, group by functional_department/category/"
                        "subcategory so it aligns with the lake. Also "
                        "resolves product_name, private_label, shelf_price.",
            "promo_id": "→ promotions.promo_id (when not null; promotions dormant)",
        },
    },
    "stores": {
        "description": (
            "One row per store. Has banner_code; scope by "
            "banner_code = '<viewer>'."
        ),
        "primary_key": ["store_id"],
    },
    "products": {
        "description": (
            "One row per SKU (the item master). DUAL taxonomy, and which "
            "one you use depends on the question: merchant_department/"
            "category/subcategory are THIS banner's own shelf labels — use "
            "them when the answer is solely about your own data (e.g. your "
            "own sales mix or top categories). functional_department/"
            "category/subcategory are the SHARED, normalized labels — use "
            "them for ANY comparison to peers, so your numbers line up with "
            "the lake (which publishes functional taxonomy). Also "
            "product_name, brand, size, private_label, shelf_price. Has "
            "banner_code; scope by banner_code = '<viewer>' for own "
            "catalogue. There is NO cross-merchant product id."
        ),
        "primary_key": ["sku"],
    },
    "promotions": {
        "description": "One row per promo. Has merchant_id; scope it.",
        "primary_key": ["promo_id"],
    },
    "merchants": {
        "description": (
            "Reference table of the 6 merchants (grocery KRG/ACM/WDX; "
            "QSR TBL/BKG/CFA). merchant_id is the banner code. Available "
            "for joining but rarely needed."
        ),
        "primary_key": ["merchant_id"],
    },
}


def _lake_items_schema() -> dict[str, Any]:
    """Introspect the Wave 3.5 line-item lake schema from any viewer's
    materialized pair — the column schema is identical across viewers.
    Returns ``{table: {description, columns}}`` for ``query_lake_sql``."""
    import pyarrow.parquet as pq

    from src.lake.lake_sql import DATA_LAKE_ITEMS

    out: dict[str, Any] = {}
    if not DATA_LAKE_ITEMS.exists():
        return out
    viewer_dirs = sorted(p for p in DATA_LAKE_ITEMS.iterdir() if p.is_dir())
    if not viewer_dirs:
        return out
    vdir = viewer_dirs[0]
    descriptions = {
        "lake_transactions": (
            "Peer purchase lines — resolves to YOUR peer set, your own "
            "rows absent. Aggregating SQL only (query_lake_sql). Its "
            "department/category/subcategory are the SHARED functional "
            "taxonomy (never a competitor's own labels) — the comparison "
            "key that lines up with your own functional_* columns."
        ),
        "lake_stores": (
            "Peer store reference; JOIN via lake_store_id. Carries "
            "neighborhood, peer_relationship, peer_segment."
        ),
    }
    for table in ("lake_transactions", "lake_stores"):
        path = vdir / f"{table}.parquet"
        if not path.exists():
            continue
        schema = pq.read_schema(path)
        out[table] = {
            "description": descriptions.get(table, ""),
            "columns": [
                {"name": f.name, "type": str(f.type)} for f in schema
            ],
        }
    return out


def schema_info() -> dict[str, Any]:
    """Return tenant + lake table schemas with join hints.

    Tenant tables: columns introspected from
    ``data/raw/<table>.parquet`` (no static drift if Wave 1 changes
    the schema); descriptions + join hints from
    ``_TENANT_TABLE_DESCRIPTIONS``.

    Lake tables (Wave 3.5): the two line-item tables, introspected from
    a viewer's materialized pair — queried with aggregating SQL via
    ``query_lake_sql``.

    Always-on, no input, cheap.
    """
    import pyarrow.parquet as pq

    tenant: dict[str, Any] = {}
    for name, info in _TENANT_TABLE_DESCRIPTIONS.items():
        path = DATA_RAW / f"{name}.parquet"
        if not path.exists():
            continue
        schema = pq.read_schema(path)
        columns = [
            {"name": f.name, "type": str(f.type)}
            for f in schema
        ]
        tenant[name] = {
            "description": info["description"],
            "primary_key": info.get("primary_key", []),
            "join_keys": info.get("join_keys", {}),
            "columns": columns,
        }

    lake = _lake_items_schema()

    tips = [
        "Always call schema_info first; it tells you the real column names so you don't burn turns guessing.",
        "transaction_items has NO banner_code — scope it by joining to transactions and filtering transactions.banner_code = '<viewer>'.",
        "Join transaction_items → products on sku to resolve taxonomy + product_name (the line carries only sku). TAXONOMY RULE: for answers about your OWN data, group by products.merchant_category/subcategory (your real shelf labels); for any comparison to PEERS, group by products.functional_category/subcategory/department so it aligns with the lake. There is no cross-merchant product id.",
        "Peer data: query lake_transactions / lake_stores with aggregating SQL via query_lake_sql. peer_relationship = 'peer' (same segment) | 'merchant' (different segment) | 'self' (YOUR OWN rows — present so you can compute an own-vs-peer gap in ONE sortable query). IMPORTANT: any peer average/count MUST constrain peer_relationship (WHERE peer_relationship = 'peer', or AVG(...) FILTER (WHERE peer_relationship = 'peer')) so your 'self' rows don't contaminate the peer number — an unfiltered aggregate over lake_transactions is REJECTED. The k=50 floor counts peer rows only.",
        "neighborhood lives on lake_stores, not lake_transactions — JOIN lake_stores USING (lake_store_id) to group by neighborhood.",
        "k=50 floor: groups backed by fewer than 50 lines are dropped and counted in `suppressed`. For transaction-level shares use COUNT(DISTINCT lake_txn_id); the line-count floor is the suppression gate only.",
        "DuckDB day-of-week: dayofweek() returns Sunday=0, Monday=1, ... Saturday=6 (NOT Sunday=1). Filter Sundays with dayofweek(txn_ts)=0 (own) / dayofweek(txn_date)=0 (lake). A wrong constant silently returns a different day.",
        "Shares/percentages are fractions in [0,1]: a claim value is 0.52 (not 52) and a 9% drop is -0.09; render as '52%' in prose but never write '0.52%'.",
    ]

    return {
        "tenant": tenant,
        "lake":   lake,
        "tips":   tips,
    }


def query_tenant(viewer: str, sql: str) -> dict[str, Any]:
    """Run a viewer-scoped SQL query against the tenant census.

    Wave 2 isolation guards (``check_tenant_predicate`` +
    ``wrap_tenant_query``) enforce that the query is scoped to the
    viewer's own merchant before it reaches DuckDB. Cross-merchant
    references in the SQL are rejected with ``TenantIsolationError``.

    Returns ``{rows: list[list], columns: list[str], row_count: int,
    truncated: bool, sql: str}``. ``rows`` is at most
    ``LLM_ROW_BUDGET`` long; ``truncated`` flags when the full result
    was larger. The specialist's state holds the full frame for
    downstream merge/chart.
    """
    try:
        check_tenant_predicate(sql, viewer)
    except TenantIsolationError as exc:
        raise LakeToolError(
            f"Tenant query rejected: {exc}"
        ) from exc

    wrapped = wrap_tenant_query(sql, viewer)
    try:
        con = duckdb.connect(":memory:")
        df = con.execute(wrapped).df()
    except duckdb.Error as exc:
        raise LakeToolError(f"DuckDB execution failed: {exc}") from exc

    return _df_to_payload(df, sql=sql)


def query_lake_sql(viewer: str, sql: str) -> dict[str, Any]:
    """Run an aggregating SQL query against the viewer's line-item peer
    lake (Wave 3.5 §8).

    Mirrors ``query_tenant`` so the grounding path handles its results
    unchanged. ``lake_transactions`` / ``lake_stores`` resolve to the
    viewer's materialized pair; the query must be a single aggregating
    SELECT (raw-row selects rejected); a per-group count floor (k=50) is
    applied and the dropped-group count is surfaced as ``suppressed``.

    Returns the same ``_df_to_payload`` shape ``query_tenant`` returns
    (so CellLookup / ValueRef / the §1.4 validator work unchanged), plus
    a ``suppressed`` count.
    """
    try:
        df, n_suppressed = run_lake_sql(viewer, sql)
    except LakeSqlError as exc:
        raise LakeToolError(f"Lake query rejected: {exc}") from exc
    except duckdb.Error as exc:
        raise LakeToolError(f"DuckDB execution failed: {exc}") from exc

    return _df_to_payload(df, sql=sql, suppressed=n_suppressed)


def top_movers(
    viewer: str,
    sql: str,
    *,
    source: str,
    dim_col: str,
    week_col: str,
    value_col: str,
    count_col: str | None = None,
    top_n: int = 3,
    min_volume: float = 0.0,
) -> dict[str, Any]:
    """Run a weekly-grain query and reduce it to its top recent-vs-baseline
    movers server-side (Phase 4 — the A2/A3 truncation fix).

    ``source`` routes the SQL through the SAME guarded path the model would use
    directly — ``"tenant"`` → ``query_tenant`` (viewer scope + window),
    ``"lake"`` → ``query_lake_sql`` (aggregating-only + k=50 + window) — so
    isolation, the k floor, and the analysis window all still apply. The full
    result frame is handed to ``wow.compute_movers``; the returned ``frame`` is
    the reduced movers table (≤ ``2*top_n`` rows), so the model reasons over ~5
    rows instead of hundreds and its claims resolve against real cells.

    Returns the standard ``_df_to_payload`` shape plus ``source``,
    ``input_rows`` (pre-reduction), ``movers_available`` (False when nothing
    cleared the completeness / k / volume filters), and any ``suppressed`` count
    forwarded from the lake query.
    """
    if source == "tenant":
        base = query_tenant(viewer, sql)
    elif source == "lake":
        base = query_lake_sql(viewer, sql)
    else:
        raise LakeToolError(
            f"top_movers: `source` must be 'tenant' or 'lake', got {source!r}."
        )

    full = base["frame"]
    missing = [c for c in (dim_col, week_col, value_col) if c not in full.columns]
    if count_col is not None and count_col not in full.columns:
        missing.append(count_col)
    if missing:
        raise LakeToolError(
            f"top_movers: column(s) {missing} not in your query result "
            f"{list(full.columns)} — alias them or fix the SELECT."
        )

    movers = compute_movers(
        full,
        dim_col=dim_col, week_col=week_col, value_col=value_col,
        count_col=count_col, top_n=top_n, min_volume=min_volume,
    )
    payload = _df_to_payload(
        movers, sql=sql, source=source,
        input_rows=int(base["row_count"]),
        movers_available=bool(len(movers)),
        suppressed=int(base.get("suppressed", 0)),
    )
    return payload


# ---------------------------------------------------------------------
# Payload helper — bound rows returned to the LLM, keep full frame.
# ---------------------------------------------------------------------

def _df_to_payload(df: pd.DataFrame, **extra: Any) -> dict[str, Any]:
    """Render a DataFrame as a JSON-safe payload. Rows are capped at
    ``LLM_ROW_BUDGET``; ``truncated`` is set when the full frame is
    larger. The full frame is returned via the ``frame`` key so the
    specialist can hold it in state."""
    rows = df.head(LLM_ROW_BUDGET).values.tolist()
    return {
        "rows":      rows,
        "columns":   list(df.columns),
        "row_count": len(df),
        "truncated": len(df) > LLM_ROW_BUDGET,
        "frame":     df,
        **extra,
    }


# ---------------------------------------------------------------------
# Tool schemas — for the Anthropic SDK
# ---------------------------------------------------------------------

SCHEMA_INFO_TOOL = {
    "name": "schema_info",
    "description": (
        "Return the column lists + join hints for tenant tables "
        "(`data/raw/`) and the lake table manifests. Call this ONCE "
        "at the start of every answer — it costs nothing, prevents "
        "guessing column names, and tells you which keys to join "
        "on. Without it, your SQL will reference columns that don't "
        "exist and you'll burn tool turns failing."
    ),
    "input_schema": {
        "type": "object",
        "properties": {},
    },
}


QUERY_TENANT_TOOL = {
    "name": "query_tenant",
    "description": (
        "Run a SQL query against your own merchant's data (tenant "
        "census, full grain — transactions, transaction_items, "
        "stores, products, promotions). Every query must include a "
        "viewer-scoping predicate (`WHERE banner_code = '<viewer>'` "
        "or `merchant_id = '<viewer>'`). Cross-merchant references "
        "are rejected. Use this for your OWN-side data; peer data "
        "comes from `read_lake_table`."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "sql": {
                "type": "string",
                "description": (
                    "A single SELECT statement scoped to the viewer "
                    "merchant. Tables: transactions, transaction_items, "
                    "stores, products, promotions, merchants."
                ),
            },
        },
        "required": ["sql"],
    },
}


QUERY_LAKE_SQL_TOOL = {
    "name": "query_lake_sql",
    "description": (
        "Run an aggregating SQL query against PEER merchants' data "
        "(the anonymized line-item lake, real dollars). Same motion as "
        "`query_tenant` but for peers: write `FROM lake_transactions` "
        "(one row per peer purchase line) and/or `JOIN lake_stores "
        "USING (lake_store_id)` — they resolve to your peer set "
        "automatically. Your OWN rows are present too, tagged "
        "`peer_relationship = 'self'`, so an own-vs-peer gap is sortable in "
        "ONE query — but every peer aggregate MUST constrain "
        "`peer_relationship = 'peer'` (or use a FILTER); a bare "
        "AVG/SUM/COUNT over lake_transactions blends your own rows in and is "
        "REJECTED. The k=50 floor counts peer rows only. "
        "Identity is reduced to `peer_relationship` ('self' = your own rows, "
        "'peer' = same segment as you, 'merchant' = different segment) — "
        "never a name. "
        "MUST be aggregating: GROUP BY a dimension and select aggregate "
        "metrics (AVG(unit_price), SUM(line_total), COUNT(DISTINCT "
        "lake_txn_id) for transaction counts). Raw-row selects "
        "(`SELECT *`) are rejected. Groups backed by fewer than 50 lines "
        "are dropped for privacy; the count is returned as `suppressed`. "
        "Columns: lake_txn_id, lake_line_id, lake_store_id, txn_date, "
        "hour_bucket, peer_relationship, category, subcategory, "
        "unit_price, qty, discount, line_total, payment_type, "
        "card_network, entry_mode, wallet_type; lake_stores adds "
        "peer_segment, neighborhood."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "sql": {
                "type": "string",
                "description": (
                    "A single aggregating SELECT over lake_transactions "
                    "/ lake_stores. Must GROUP BY a dimension or be a "
                    "whole-table aggregate."
                ),
            },
        },
        "required": ["sql"],
    },
}


# Wave 3.5 §2 decision 6 / §11.2 — charts are held for Wave 4. The
# chart layer (chart_build.py, ChartIntent) is kept intact but DORMANT:
# the model can no longer author a chart_intent (removed from the emit
# schema), and the specialist's build path is gated behind this flag.
# Wave 4 flips it to True and re-adds the chart_intent schema field.
CHARTS_ENABLED = False


EMIT_RESPONSE_TOOL = {
    "name": "emit_response",
    "description": (
        "Call this tool ONCE to finish your answer. This is how the "
        "agent loop ends — the structured response you emit here is "
        "what the user sees. You MUST call emit_response exactly "
        "once at the end of every answer; do not emit a free-text "
        "final turn.\n"
        "\n"
        "Your answer is structured into named fields: a one-sentence "
        "`headline` (the finding that matters), an `evidence` list of "
        "2-4 supporting points each grounding a specific number, an "
        "optional `so_what` (the action or implication), and `caveats`. "
        "Every metric number in ANY text field must be backed by an "
        "entry in `claims`, which the validator recomputes against your "
        "query results. Do NOT end any field with a question or narrate "
        "what you would do next — commit to the answer you have."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "headline": {
                "type": "string",
                "description": (
                    "The single most important finding, as ONE complete "
                    "sentence that answers the question. Every metric "
                    "number here must be backed by a `claims` entry. Never "
                    "a question, never a description of what you would do."
                ),
            },
            "evidence": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "2-4 supporting points. Each is one sentence grounding "
                    "a specific number (each number backed by a `claims` "
                    "entry whose `text_span` is a substring of that "
                    "sentence). Omit for a headline-only definitional answer."
                ),
            },
            "so_what": {
                "type": "string",
                "description": (
                    "Optional — the recommended action or implication, one "
                    "sentence. Omit when there is no actionable so-what."
                ),
            },
            "claims": {
                "type": "array",
                "description": (
                    "Each metric numeric in prose must be backed by a "
                    "claim here. Three source shapes:\n"
                    "  * CellLookup — one cell, optionally aggregated "
                    "across matching rows.\n"
                    "  * Derivation — closed grammar: difference, "
                    "ratio, pct_change, aggregate(sum|mean over "
                    "operand cells).\n"
                    "  * ValueRef (PREFERRED for peer metric "
                    "aggregates) — the address shape: `{type: "
                    "\"ValueRef\", by: \"subcategory\", value: \"Ground Beef\", "
                    "metric: \"units_index\"}` resolves to the EXACT "
                    "mean from the same aggregates block "
                    "read_lake_table surfaced. Use this for any peer "
                    "claim that names a value from the `aggregates` "
                    "block — the server substitutes the exact float, "
                    "so the claim is byte-identical to the validator's "
                    "recompute and lands [passed] (never [normalized] "
                    "due to rounding)."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "text_span": {"type": "string"},
                        "value": {"type": "number"},
                        "source": {
                            "type": "object",
                            "properties": {
                                "type": {
                                    "type": "string",
                                    "enum": [
                                        "CellLookup",
                                        "Derivation",
                                        "ValueRef",
                                    ],
                                },
                                "row_filter": {"type": "object"},
                                "column": {"type": "string"},
                                "agg": {
                                    "type": "string",
                                    "enum": ["sum", "mean"],
                                },
                                "op": {
                                    "type": "string",
                                    "enum": ["difference", "ratio",
                                             "pct_change", "aggregate"],
                                },
                                "operands": {
                                    "type": "array",
                                    "items": {"type": "object"},
                                },
                                "frame": {
                                    "type": "string",
                                    "enum": ["tenant", "lake", "merged"],
                                    "description": (
                                        "Which frame the claim resolves "
                                        "against. Use 'merged' (default) "
                                        "after a successful build_merge; "
                                        "use 'tenant' or 'lake' in the "
                                        "merge-fail path when both real "
                                        "frames are returned unmerged."
                                    ),
                                },
                                "by": {
                                    "type": "string",
                                    "description": (
                                        "ValueRef only: the dimension to "
                                        "filter on (e.g. 'category', "
                                        "'derived_zone')."
                                    ),
                                },
                                "value": {
                                    "description": (
                                        "ValueRef only: the dimension "
                                        "value (e.g. 'Ground Beef', a neighborhood)."
                                    ),
                                },
                                "metric": {
                                    "type": "string",
                                    "description": (
                                        "ValueRef only: the metric "
                                        "column to aggregate (e.g. "
                                        "'units_index', 'price_index')."
                                    ),
                                },
                            },
                        },
                    },
                    "required": ["text_span", "value", "source"],
                },
            },
            "caveats": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Short clarifying notes (peer set, window, etc).",
            },
        },
        "required": ["headline", "claims"],
    },
}


TOP_MOVERS_TOOL = {
    "name": "top_movers",
    "description": (
        "Reduce a WEEKLY-GRAIN query to its biggest week-over-week movers, "
        "computed server-side — use this instead of pulling a full week×category "
        "or week×store pivot and diffing it yourself (those pivots have hundreds "
        "of rows and get truncated). You write a weekly-grain aggregating query "
        "(one row per week × dimension, selecting the units metric and a line "
        "count); it runs through the same guarded path as `query_tenant` / "
        "`query_lake_sql` (viewer scope, k=50, analysis window all still apply), "
        "and returns only the top risers and decliners by percentage change of "
        "the most-recent complete week vs the mean of the prior 4 weeks. Call it "
        "once with source='tenant' for your own movers and once with "
        "source='lake' for peer movers, then compare. Each returned row has your "
        "dimension column plus `recent`, `baseline`, `delta_pct` (e.g. 1.8 = "
        "+180%), and `direction`. If `movers_available` is false, no dimension "
        "cleared the floor — say so honestly rather than inventing a mover."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "sql": {
                "type": "string",
                "description": (
                    "A weekly-grain aggregating SELECT: one row per week × "
                    "dimension. Group by `date_trunc('week', <ts>)` and the "
                    "dimension; select the week, the dimension, a units metric "
                    "(e.g. SUM(qty)), and a line count (e.g. COUNT(*))."
                ),
            },
            "source": {
                "type": "string",
                "enum": ["tenant", "lake"],
                "description": "'tenant' for your own data, 'lake' for peers.",
            },
            "week_col":  {"type": "string", "description": "Name of the week column in your result."},
            "dim_col":   {"type": "string", "description": "Name of the dimension column (e.g. 'category', 'store_id')."},
            "value_col": {"type": "string", "description": "Name of the units/metric column the % change is computed on."},
            "count_col": {"type": "string", "description": "Optional line-count column; when given, weeks below k=50 are dropped from a dimension's comparison."},
            "top_n":     {"type": "integer", "description": "How many risers AND decliners to return (default 3)."},
        },
        "required": ["sql", "source", "week_col", "dim_col", "value_col"],
    },
}


TOOLS_SPECIALIST = [
    SCHEMA_INFO_TOOL,
    QUERY_TENANT_TOOL,
    QUERY_LAKE_SQL_TOOL,
    EMIT_RESPONSE_TOOL,
]

# Anomaly detection additionally gets the server-side week-over-week reducer so
# it never hands the model a truncated weekly pivot (Phase 4).
TOOLS_ANOMALY = [
    SCHEMA_INFO_TOOL,
    QUERY_TENANT_TOOL,
    QUERY_LAKE_SQL_TOOL,
    TOP_MOVERS_TOOL,
    EMIT_RESPONSE_TOOL,
]


# ---------------------------------------------------------------------
# Render block parsing — model's final response
# ---------------------------------------------------------------------

# Strays from Anthropic's XML-style tool-use surface that the model
# sometimes double-encodes into the `prose` string field of
# emit_response. Anthropic already enforces prose is a string; this
# strips any literal `</prose>`, `<parameter ...>`, `<invoke ...>`,
# or `<antml ...>` markers (plus everything after — typically a
# trailing chart_intent JSON blob) so they don't end up in the
# delivered prose. See Wave 3 Stage 6.5 follow-up #5 inspection
# notes for A3's batch-7 emission blob that prompted this.
# Bare opening <prose> tag — Haiku occasionally wraps its prose in
# <prose>...</prose>. Strip just the opening tag (not the content)
# before the trailing-junk pass below; otherwise the trailing-junk
# regex eats from the opening <prose> to end-of-string and the
# user sees an empty answer (Wave 3 Stage 6.5 follow-up #8 — root
# cause of 6/12 empty-prose blocks in round-3 batch).
_PROSE_OPENING_TAG_RE = re.compile(
    r"^\s*<prose>\s*", re.IGNORECASE,
)

# Trailing-junk regex: matches a closing tool-use marker (or a stray
# OPENING tool-use marker like <parameter>, <invoke>, <antml>) and
# eats everything after. We deliberately do NOT include opening
# <prose> here — see above.
_PROSE_TOOL_BLOB_RE = re.compile(
    r"\s*(?:</prose>|</?parameter\b[^>]*>|</?invoke\b[^>]*>|<antml[^>]*>).*",
    re.DOTALL | re.IGNORECASE,
)


# Wave 3 Stage 6.5 follow-up #8 — internal-error / planning narration
# detector. Catches semantic leaks like "system issue filtering by …",
# "I'll retry with corrected parameters", "let me pull peer data".
# These are Haiku's plumbing voice and the prompt's Rule 2c forbids
# them; this is the backstop when the prompt isn't enough.
_INTERNAL_NARRATION_PATTERNS = [
    r"system issue",
    r"system error",
    r"tool error",
    r"query failed",
    r"retry with corrected parameters?",
    r"corrected parameters?",
    r"peer benchmark fetch",
    r"\blet me (?:pull|fetch|query|grab|retrieve|try|run|rewrite|reformulate|rework|adjust)\b",
    # SQL-mechanics leak — the model narrating a query error / rewrite mid-answer
    # (never legitimate merchant prose). Kept narrow to avoid stripping business text.
    r"\bcte\b",
    r"\bsubquery\b",
    r"\bsql\b[^.]*?\b(?:parsing|syntax|parse|execution)\s+error",
    r"query surface",
    r"(?:isn'?t|is not|not)\s+supported",
    r"\bi(?:'ll| will) need to\b",
    r"\bi would need to\b",
    r"\bi need to (?:pull|fetch|query|grab|retrieve|compare|check)\b",
    r"to (?:provide|give you) a full[^.]*?\bi(?:'ll| will)\b",
    r"to answer this (?:question )?properly,? i (?:need|have) to",
    r"i(?:'ve| have) fetched but",
    r"unable to retrieve complete peer data",
    # Wave 3.5 — punt-with-a-question / offer-to-continue. A finished
    # answer commits; it never asks the user what to do next or ends
    # on a question mark.
    r"\bwould you like me to\b",
    r"\bdo you want me to\b",
    r"\bshall i\b",
    r"\?\s*$",
    # Phase 1.1 — mid-loop planning / scratchpad that leaks when the model stops
    # with a text block instead of calling emit_response. Starts-with planning
    # verbs, in-flight status, and trailing ellipsis.
    r"^\s*(?:let me|let's|i'?ll|i will|now\b|first,? i|okay)\b",
    r"\bretrying\b",
    r"^\s*placeholder\b",
    r"no anomalies found yet",
    r"result was truncated",
    r"pulling the last",
    r"let me compute\b",
    r"\bcompute\b[^.]*\bnow\b",
    r"^\s*checking\b",
    r"…\s*$",
    r"\.\.\.\s*$",
]
_INTERNAL_NARRATION_RE = re.compile(
    "|".join(_INTERNAL_NARRATION_PATTERNS),
    re.IGNORECASE,
)


def is_narration(text: str | None) -> bool:
    """True if ``text`` reads as mid-loop scratchpad / planning rather than a
    finished answer. Shared by ``sanitize_prose`` (whole-field replace) and the
    emit-time narration guard in the specialist (reject → retry)."""
    return bool(text and _INTERNAL_NARRATION_RE.search(text))

# Wave 3 Stage 6.5 Fix 9e — single source for any user-facing
# "answer couldn't be substantiated" prose. Every fallback path
# (narration sanitizer, all-stripped synthesizer, force-accept
# floor, wall-clock ceiling) routes through ``business_fallback()``
# so a future regression can't reintroduce mechanics-talk
# ("validator", "draft", "merge spec", "retry with corrected
# parameters", etc.).
from src.agents.fallbacks import BUSINESS_FALLBACK as _BUSINESS_FALLBACK
from src.agents.fallbacks import COMPARISON_UNAVAILABLE  # re-exported for callers

# Mechanics terms that must NEVER reach user-facing prose. A
# regression test scans every assembled AgentResponse.prose for
# these and expects zero hits.
_FORBIDDEN_MECHANICS_TERMS = frozenset({
    "validator", "draft", "merge spec",
    "retry with corrected parameters", "corrected parameters",
    "tool error", "system issue", "precondition", "force-accept",
    "claim disposition", "peer benchmark fetch",
})


def business_fallback() -> str:
    """Single canonical failure-fallback prose. All paths that need
    a 'we couldn't substantiate' fallback route through this — the
    narration sanitizer, the all-stripped synthesizer, the
    force-accept floor, the wall-clock ceiling."""
    return _BUSINESS_FALLBACK


def sanitize_prose(prose: str) -> str:
    """Strip stray Anthropic XML-style tool-use markup (and any
    trailing chart-intent / parameter blob) from the model's prose
    string, then detect and neutralize internal-error / planning
    narration that leaks Haiku's plumbing voice into the user-facing
    answer (Wave 3 Stage 6.5 follow-up #8).

    The XML pass runs first (handles ``</prose>``, ``<parameter
    name="chart_intent">``, etc.). The narration pass runs second:
    if any of the forbidden phrases from Rule 2c appears in the
    sanitized prose, the entire prose is replaced with the neutral
    business-framed fallback. We replace (not partial-strip) because
    these narrations tend to dominate the paragraph; a partial
    excise leaves dangling clauses worse than the original.
    """
    if not prose:
        return prose
    # Strip a bare opening <prose> tag first so the trailing-junk
    # regex doesn't eat the legitimate content after it.
    cleaned = _PROSE_OPENING_TAG_RE.sub("", prose)
    cleaned = _PROSE_TOOL_BLOB_RE.sub("", cleaned).strip()
    if cleaned and _INTERNAL_NARRATION_RE.search(cleaned):
        return business_fallback()
    return cleaned


_RENDER_RE = re.compile(
    r"```render\s*\n(.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)

_CAVEATS_RE = re.compile(
    r"```caveats\s*\n(.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)


def parse_render_block(text: str) -> dict[str, Any] | None:
    """Extract the `render` fenced JSON from a final-turn assistant
    response. The model is instructed to emit:

        ```render
        {
          "merge":         {"on": [...], "own_value_col": "...", ...},
          "chart_intent":  {"kind": "...", "x": "...", "series": [...]},
          "claims":        [...]
        }
        ```

    Returns the parsed dict or None if absent / malformed.
    """
    m = _RENDER_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def parse_caveats_block(text: str) -> list[str]:
    """Extract a fenced ``caveats`` JSON list. Returns [] on
    absent/malformed."""
    m = _CAVEATS_RE.search(text)
    if not m:
        return []
    try:
        val = json.loads(m.group(1))
        if isinstance(val, list):
            return [str(c).strip() for c in val if c]
    except json.JSONDecodeError:
        pass
    return []


def strip_render_and_caveats_blocks(text: str) -> str:
    """Remove the trailing ``render`` and ``caveats`` fenced blocks
    from prose so what reaches the user is the narrative text only."""
    cleaned = _RENDER_RE.sub("", text)
    cleaned = _CAVEATS_RE.sub("", cleaned)
    return cleaned.strip()
