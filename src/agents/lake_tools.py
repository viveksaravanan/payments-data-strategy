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

from src.lake.isolation import (
    TenantIsolationError,
    check_tenant_predicate,
    wrap_tenant_query,
)
from src.lake.manifest import manifest_for
from src.lake.scope import (
    IdentityLeakError,
    assert_no_identity_leak,
    scope_for_viewer,
)

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
            "Line items — one row per (txn_id, line_id). Carries "
            "category, subcategory, sku, canonical_id, qty, unit_price, "
            "discount, line_total, promo_id. NOTE: NO banner_code on "
            "this table — scope by joining to transactions and using "
            "transactions.banner_code = '<viewer>'."
        ),
        "primary_key": ["txn_id", "line_id"],
        "join_keys": {
            "txn_id":       "→ transactions.txn_id",
            "sku":          "→ products.sku (your SKU's product row)",
            "canonical_id": (
                "→ products.canonical_id (cross-merchant canonical "
                "product key — same canonical_id maps to the same "
                "product at every banner)"
            ),
            "promo_id":     "→ promotions.promo_id (when not null)",
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
            "One row per SKU. Has banner_code; scope by "
            "banner_code = '<viewer>' for own catalogue."
        ),
        "primary_key": ["sku"],
        "join_keys": {
            "canonical_id": (
                "shared cross-merchant key — the same canonical_id "
                "is in every grocer's catalogue at the same "
                "(category, subcategory)"
            ),
        },
    },
    "promotions": {
        "description": "One row per promo. Has merchant_id; scope it.",
        "primary_key": ["promo_id"],
    },
    "merchants": {
        "description": (
            "Reference table of the 5 merchants. merchant_id is the "
            "banner code. Available for joining but rarely needed."
        ),
        "primary_key": ["merchant_id"],
    },
}


_LAKE_TABLES_FOR_SCHEMA = [
    "lake_category_metrics",
    "lake_payment_mix",
    "lake_segment_mix",
    "lake_trade_area",
    "lake_cross_merchant_cohorts",
]


def schema_info() -> dict[str, Any]:
    """Return tenant + lake table schemas with join hints.

    Tenant tables: columns introspected from
    ``data/raw/<table>.parquet`` (no static drift if Wave 1 changes
    the schema); descriptions + join hints from
    ``_TENANT_TABLE_DESCRIPTIONS``.

    Lake tables: dimensions + metrics + excludes + k_floor read
    from the Wave 2 manifest. No fixed column lists for the lake —
    the manifest is the contract.

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

    lake: dict[str, Any] = {}
    for table in _LAKE_TABLES_FOR_SCHEMA:
        try:
            spec = manifest_for(table)
        except KeyError:
            continue
        lake[table] = {
            "finest_grain": spec["finest_grain"],
            "dimensions":   spec["dimensions"],
            "metrics":      spec["metrics"],
            "excludes":     spec["excludes"],
            "k_floor":      spec["k_floor"],
        }

    tips = [
        "Always call schema_info first; it tells you the real column names so you don't burn turns guessing.",
        "transaction_items has NO banner_code — scope it by joining to transactions and filtering transactions.banner_code = '<viewer>'.",
        "Join transaction_items → products via sku for product detail; via canonical_id for cross-merchant.",
        "Lake reads are by table name + filters; no SQL. Filter keys must be in the table's dimensions list.",
        "Lake automatically excludes your own merchant. Real banner_code never reaches you — peer_relationship (segment_peer | cross_segment) is what you see.",
        "WEEK BOUNDARY (load-bearing for merges on period_start): the lake's period_start is Monday of each week, dtype `date`. To produce a tenant-side period_start that joins cleanly, use this SQL pattern: `SELECT DATE_TRUNC('week', t.txn_ts)::DATE AS period_start, ... FROM transactions t WHERE t.banner_code = '<viewer>' GROUP BY DATE_TRUNC('week', t.txn_ts)`. The cast to `::DATE` matches the lake's dtype. The merge layer also auto-coerces date/datetime mismatches as a safety net, so a slightly different cast still works — but the canonical pattern avoids that fallback.",
        "MONTH BOUNDARY: lake_payment_mix.month_start is the 1st of each month, dtype `date`. Tenant equivalent: `DATE_TRUNC('month', t.txn_ts)::DATE AS month_start`.",
        "COMPARABLE UNITS (load-bearing for merges): pick own_value_col and peer_value_col that are in the SAME UNITS. Examples that work: own units (SUM(qty)) vs peer units_index (both are 'units' shape — comparison is meaningful); own avg unit_price (AVG(unit_price)) vs peer price_index. Examples that DON'T work: own revenue (SUM(line_total) in dollars) vs peer revenue_index (a unitless ratio centered ≈1.0) — the merge layer will reject this as a magnitude mismatch.",
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


# ---------------------------------------------------------------------
# read_lake_table
# ---------------------------------------------------------------------

def read_lake_table(
    viewer: str,
    table: str,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read a Wave 2 lake table, scoped to ``viewer``.

    Steps:

    1. Resolve the manifest for ``table`` — ``KeyError`` if the table
       doesn't exist in the lake (mapped to ``LakeToolError`` for the
       model).
    2. Validate every ``filters`` key is in the manifest's
       ``dimensions`` list. An off-grain key (e.g. ``sku`` on
       ``lake_category_metrics``) is rejected with a message quoting
       the relevant ``Excludes`` — so the agent can decline gracefully.
    3. Read ``data/lake/<table>.parquet``; apply equality filters
       (single value or list-membership).
    4. ``scope_for_viewer`` strips the viewer's rows + adds
       ``peer_relationship`` + drops ``banner_code``.
    5. ``assert_no_identity_leak`` — the safety check before
       returning anything to the LLM.

    Returns ``{rows, columns, row_count, truncated,
    manifest: {finest_grain, dimensions, metrics, excludes, k_floor,
    ladder}, table: <name>}``.
    """
    try:
        manifest = manifest_for(table)
    except KeyError as exc:
        raise LakeToolError(
            f"Unknown lake table {table!r}. Known tables: "
            f"{_lake_tables_list()}."
        ) from exc

    filters = filters or {}
    _validate_filter_keys(table, filters, manifest)

    path = DATA_LAKE / f"{table}.parquet"
    if not path.exists():
        raise LakeToolError(
            f"Lake table {table!r} is not materialized at {path}. "
            f"Run `make lake` to build it from data/raw/."
        )
    df_all = pd.read_parquet(path)
    df = _apply_filters(df_all, filters)

    # Scope-and-strip. Cohort table has no banner_code so this is a
    # no-op there (preserves the table's natural shape).
    df = scope_for_viewer(df, viewer)

    try:
        assert_no_identity_leak(df)
    except IdentityLeakError as exc:
        # Should never happen — scope_for_viewer is the contract
        # boundary. If it does, that's a bug, not a model issue.
        raise LakeToolError(
            f"Lake response carried identity columns: {exc}"
        ) from exc

    payload = _df_to_payload(df, table=table)
    payload["manifest"] = {
        "finest_grain": manifest["finest_grain"],
        "dimensions":   manifest["dimensions"],
        "metrics":      manifest["metrics"],
        "excludes":     manifest["excludes"],
        "k_floor":      manifest["k_floor"],
        "ladder":       manifest["ladder"],
    }
    # When filters match 0 rows, the model has been observed to
    # conclude "the dataset isn't populated" — false (the data exists,
    # the filter was wrong). Surface diagnostic guidance: available
    # values per filter dimension so the model can retry with a
    # corrected filter, or report honestly that grain X has no data
    # and use the available alternative.
    if len(df) == 0:
        diagnostics: dict[str, list[Any]] = {}
        for k, v in filters.items():
            try:
                available = sorted(
                    {str(x) for x in df_all[k].dropna().unique().tolist()}
                )
            except KeyError:
                continue
            # Cap to avoid bloating the payload.
            if len(available) > 30:
                available = available[:30] + [f"… ({len(available)} total)"]
            diagnostics[k] = available
        payload["zero_rows_diagnostic"] = {
            "message": (
                "Lake read returned 0 rows. The data exists — your "
                "filter values did not match. Either retry with a "
                "filter value listed below, or report 'no peer data "
                "at this grain' and use the available alternative. "
                "Do NOT conclude 'the dataset isn't populated' — "
                "that is false."
            ),
            "available_values_per_filter": diagnostics,
        }
    return payload


def _validate_filter_keys(
    table: str,
    filters: dict[str, Any],
    manifest: dict[str, Any],
) -> None:
    """Every filter key must be one of the table's published
    dimensions. Off-grain filters get rejected with the relevant
    ``Excludes`` so the agent can either drop the filter or decline."""
    dims = set(manifest["dimensions"])
    bad = [k for k in filters if k not in dims]
    if not bad:
        return
    raise LakeToolError(
        f"Filter keys {bad} are not dimensions of {table!r}. "
        f"Published dimensions: {sorted(dims)}. "
        f"Excludes: {manifest['excludes']}."
    )


def _apply_filters(df: pd.DataFrame, filters: dict[str, Any]) -> pd.DataFrame:
    """Apply equality filters. Each value can be a scalar or a list
    (treated as IN). Missing dimensions raise (would have been caught
    by ``_validate_filter_keys`` upstream)."""
    for k, v in filters.items():
        if isinstance(v, list):
            df = df[df[k].isin(v)]
        else:
            df = df[df[k] == v]
    return df


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


READ_LAKE_TOOL = {
    "name": "read_lake_table",
    "description": (
        "Read a Wave 2 anonymized lake table (peer data, k≥50). "
        "Tables: lake_category_metrics, lake_payment_mix, "
        "lake_segment_mix, lake_trade_area, "
        "lake_cross_merchant_cohorts. Filters must use dimensions "
        "listed in the table's manifest; off-grain filters (e.g. "
        "`sku` on lake_category_metrics) are rejected with the "
        "manifest's Excludes so you know what isn't published. "
        "Your own merchant's rows are stripped automatically; the "
        "real banner_code is replaced with `peer_relationship` "
        "(segment_peer or cross_segment). Cohort table has no "
        "banner column."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "table": {
                "type": "string",
                "enum": [
                    "lake_category_metrics", "lake_payment_mix",
                    "lake_segment_mix", "lake_trade_area",
                    "lake_cross_merchant_cohorts",
                ],
            },
            "filters": {
                "type": "object",
                "description": (
                    "Equality filters keyed by dimension name; values "
                    "can be a string or a list of strings (IN). E.g. "
                    "`{\"category\": \"DAIRY\", \"derived_zone\": "
                    "[\"Z05\", \"Z08\"]}`."
                ),
            },
        },
        "required": ["table"],
    },
}


EMIT_RESPONSE_TOOL = {
    "name": "emit_response",
    "description": (
        "Call this tool ONCE to finish your answer. This is how the "
        "agent loop ends — the structured response you emit here is "
        "what the user sees. You MUST call emit_response exactly "
        "once at the end of every answer; do not emit a free-text "
        "final turn. The validator and chart builder run on the "
        "structured args you pass."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "prose": {
                "type": "string",
                "description": (
                    "2-5 sentence executive-readable answer. Every metric "
                    "number you state here MUST be backed by an entry in "
                    "the `claims` list, OR fall outside the metric/structural "
                    "scanner (years, entity counts like \"5 stores\" are exempt)."
                ),
            },
            "merge": {
                "type": "object",
                "description": (
                    "Join spec for own + peer frames. Leave empty {} when "
                    "answering from a single source (advisor on a lake "
                    "table, or own-only analysis). Required when BOTH "
                    "query_tenant and read_lake_table were called and a "
                    "comparison is needed."
                ),
                "properties": {
                    "on": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Join keys present in both frames.",
                    },
                    "own_value_col": {"type": "string"},
                    "peer_value_col": {"type": "string"},
                    "gap_op": {
                        "type": "string",
                        "enum": ["difference", "ratio"],
                    },
                },
            },
            "chart_intent": {
                "type": "object",
                "description": (
                    "Chart shape — names result columns, never values. "
                    "Per-kind required fields (besides `kind` and "
                    "`title`):\n"
                    "  - time_series_vs_peers: `x` (time col), `series` "
                    "(list of value cols), `y_format`.\n"
                    "  - cross_merchant_comparison: `x` (label col), "
                    "`series` (list of value cols), `y_format`.\n"
                    "  - heatmap: `row`, `col`, `value`.\n"
                    "  - scatter_quadrant: `x`, `y` (and optional "
                    "`label`, `size`).\n"
                    "  - waterfall: `x` (label col), `y` (value col).\n"
                    "  - geo_map: `lat`, `lon`.\n"
                    "  - kpi_callout: `value` (numeric col — uses "
                    "first row).\n"
                    "  - small_multiples: `facet`, `x`, `series`.\n"
                    "  - table_drilldown: `columns` (list of cols)."
                ),
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": [
                            "time_series_vs_peers",
                            "cross_merchant_comparison",
                            "heatmap",
                            "scatter_quadrant",
                            "waterfall",
                            "geo_map",
                            "kpi_callout",
                            "small_multiples",
                            "table_drilldown",
                        ],
                    },
                    "title": {"type": "string"},
                    "takeaway": {"type": "string"},
                    "x": {"type": "string"},
                    "y": {"type": "string"},
                    "series": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "y_format": {
                        "type": "string",
                        "enum": ["index", "currency", "pct",
                                 "count", "raw"],
                    },
                    "row": {"type": "string"},
                    "col": {"type": "string"},
                    "value": {"type": "string"},
                    "label": {"type": "string"},
                    "size": {"type": "string"},
                    "delta": {"type": "string"},
                    "facet": {"type": "string"},
                    "columns": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "lat": {"type": "string"},
                    "lon": {"type": "string"},
                    "palette": {
                        "type": "string",
                        "enum": ["diverging", "sequential"],
                    },
                },
                "required": ["kind"],
            },
            "claims": {
                "type": "array",
                "description": (
                    "Each metric numeric in prose must be backed by a "
                    "claim here. Source is CellLookup (one cell, "
                    "optionally aggregated across matching rows) or "
                    "Derivation (closed grammar: difference, ratio, "
                    "pct_change, aggregate(sum|mean over operand cells))."
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
                                    "enum": ["CellLookup", "Derivation"],
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
        "required": ["prose", "chart_intent", "claims"],
    },
}


TOOLS_SPECIALIST = [
    SCHEMA_INFO_TOOL,
    QUERY_TENANT_TOOL,
    READ_LAKE_TOOL,
    EMIT_RESPONSE_TOOL,
]


def _lake_tables_list() -> list[str]:
    """Lake-table list for the error path (uses manifest as source of
    truth)."""
    from src.lake.manifest import list_tables
    return list_tables()


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
_PROSE_TOOL_BLOB_RE = re.compile(
    r"\s*(?:</?prose>|</?parameter\b[^>]*>|</?invoke\b[^>]*>|<antml[^>]*>).*",
    re.DOTALL | re.IGNORECASE,
)


def sanitize_prose(prose: str) -> str:
    """Strip stray Anthropic XML-style tool-use markup (and any
    trailing chart-intent / parameter blob) from the model's prose
    string. Anthropic's tool input_schema already enforces ``prose``
    is a string, but the model occasionally writes literal
    ``</prose>`` and ``<parameter name="chart_intent">`` inside the
    string field. This sanitizer cleans them before the §1.4
    validator runs (the validator treats XML tags as opaque text
    and would leave them in the delivered prose)."""
    if not prose:
        return prose
    return _PROSE_TOOL_BLOB_RE.sub("", prose).strip()


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
