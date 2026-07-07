"""Wave 2 §2 tenant-isolation guards (D22.4).

Two complementary guards make the dual-path architecture real:

1. **Tenant-surface guards** (for Wave 3 agent queries against own data):
   - ``check_tenant_predicate(sql, viewer)`` verifies the SQL is scoped
     to the viewer's own merchant. Raises ``TenantIsolationError`` if it
     references another merchant's data.
   - ``wrap_tenant_query(sql, viewer)`` CTE-wraps the SQL so unqualified
     tenant-table references resolve only to viewer-scoped CTEs. This is
     defense-in-depth on top of the predicate check.

2. **Lake-builder source-path guard:**
   - ``assert_lake_source_paths(paths)`` rejects any path under
     ``data/eval/``. The anomaly ground truth is forbidden source for
     the lake builder by construction (physical-directory separation
     from Wave 1 SPEC §5).

**Two distinct boundaries (SPEC §0 disambiguation — do not conflate
in one test):**

* The lake builder MAY link card tokens across merchants inside the
  §4.5 cohort build step. ``transactions.customer_token`` is the
  observable cross-merchant linkage key, and the cohort builder reads
  it via the §1 ``observable_guard``. This is ALLOWED — it's the
  trusted boundary, not an isolation breach.
* The lake builder MAY NEVER read ``data/eval/``. Different boundary
  entirely — the answer key, not source data.

An over-broad isolation test that bans all cross-boundary access would
false-positive on (a). The split assertion in
``tests/lake/test_L02_tenant_isolation.py`` keeps these distinct.

The Wave 2 build itself doesn't use the tenant-surface guards (it's
trusted code that reads all merchants to compute peer aggregates).
The tenant-surface guards are forward-facing APIs that Wave 3 agents
will consume. Wave 2 ships them tested and ready.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from src.agents.constants import ANALYSIS_END_ISO, ANALYSIS_START_ISO
from src.generate.config.loader import load_config
from src.lake.observable_guard import DATA_RAW

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_EVAL = REPO_ROOT / "data" / "eval"
_CONFIG_ROOT = REPO_ROOT / "src" / "generate" / "config"

# Merchant ID alphabet — used by the predicate check to recognize
# "another merchant" literals in agent SQL. Derived from config
# (datamodel-v2: KRG/ACM/WDX grocery + TBL/BKG/CFA qsr; TJX/off-price
# gone) so a panel change needs no edit here (config-driven guardrail).
def _valid_merchants() -> set[str]:
    return {m["banner_code"] for m in load_config(_CONFIG_ROOT).merchants.values()}


VALID_MERCHANTS: set[str] = _valid_merchants()

# Tenant tables — the lake builder reads these but doesn't apply tenant
# guards (trusted code path). Wave 3 agents read them via the guards.
TENANT_TABLES: tuple[str, ...] = (
    "transactions", "transaction_items", "products",
    "promotions", "stores", "merchants",
)


class TenantIsolationError(ValueError):
    """Raised when a tenant-surface query violates viewer scoping."""


class LakeSourcePathError(ValueError):
    """Raised when the lake builder tries to read a path outside
    ``data/raw/`` (specifically: ``data/eval/`` is forbidden)."""


# ----- Predicate check ----------------------------------------------------

# Matches either banner_code or merchant_id equality predicate.
# (Both column names are merchant identifiers in the Wave 1 contract;
# transactions uses banner_code, stores/products carry both as synonyms.)
_VIEWER_PREDICATE_RE = re.compile(
    r"\b(banner_code|merchant_id)\s*=\s*['\"]([A-Z]{3})['\"]",
    re.IGNORECASE,
)

# Catch-all scan for any 3-letter uppercase literal — covers IN clauses,
# CASE expressions, JOIN ON predicates, etc. Anything that looks like
# a merchant ID and isn't the viewer is rejected. Broad-but-cheap;
# false positives only on non-merchant 3-letter codes, which the
# Wave 1 schema doesn't use as string literals.
_ANY_MERCHANT_LITERAL_RE = re.compile(r"['\"]([A-Z]{3})['\"]")


def check_tenant_predicate(sql: str, viewer: str) -> None:
    """Validate that ``sql`` is scoped to ``viewer``'s own merchant.

    Two rules:

    1. The SQL must contain an explicit predicate
       ``banner_code = '<viewer>'`` or ``merchant_id = '<viewer>'``.
    2. The SQL must NOT contain any merchant-identifier literal other
       than ``viewer``. References to other merchants — even in IN
       clauses or string operations — are rejected.

    Raises ``TenantIsolationError`` on violation.
    """
    if viewer not in VALID_MERCHANTS:
        raise TenantIsolationError(
            f"Unknown viewer {viewer!r}. Valid merchants: "
            f"{sorted(VALID_MERCHANTS)}."
        )

    # Reject anything that isn't a single SELECT (no semicolons / DDL).
    trimmed = sql.strip().rstrip(";").strip()
    if ";" in trimmed:
        raise TenantIsolationError(
            "Only a single SELECT statement is allowed in tenant queries."
        )
    if not re.match(r"^\s*(WITH|SELECT)\b", trimmed, re.IGNORECASE):
        raise TenantIsolationError(
            "Tenant queries must start with SELECT or WITH … SELECT."
        )

    # Rule 1: reject references to any other merchant identifier
    # (catches IN clauses, JOIN ON literals, anywhere a quoted 3-letter
    # merchant ID appears). Run before Rule 2 so the more specific
    # "references another merchant" error fires when both would apply
    # (e.g. WHERE banner_code IN ('KRG', 'WDX') with viewer='KRG').
    for ident in _ANY_MERCHANT_LITERAL_RE.findall(sql):
        if ident.upper() != viewer and ident.upper() in VALID_MERCHANTS:
            raise TenantIsolationError(
                f"Tenant query references another merchant {ident!r} besides "
                f"viewer {viewer!r}. Peers are reachable only through the "
                f"anonymized lake (Wave 2 §5 dual-path), not via the "
                f"tenant surface."
            )

    # Rule 2: viewer predicate must be present (catches unscoped
    # queries like `SELECT * FROM transactions`).
    has_viewer_predicate = False
    for col, ident in _VIEWER_PREDICATE_RE.findall(sql):
        if ident.upper() == viewer:
            has_viewer_predicate = True
            break
    if not has_viewer_predicate:
        raise TenantIsolationError(
            f"Tenant query for viewer {viewer!r} must contain "
            f"WHERE banner_code = {viewer!r} (or merchant_id). "
            f"Got query without viewer-scoping predicate."
        )


# ----- Query wrap ---------------------------------------------------------

def wrap_tenant_query(sql: str, viewer: str) -> str:
    """Wrap ``sql`` so unqualified references to tenant tables resolve
    to viewer-scoped CTEs. Defense-in-depth on top of
    ``check_tenant_predicate`` — even if a regex bypass slipped through
    the predicate, the CTE wrap structurally limits the result.

    The wrap reads ``data/raw/`` Parquet via DuckDB; works directly
    with ``duckdb.connect().sql(wrapped)``.
    """
    if viewer not in VALID_MERCHANTS:
        raise TenantIsolationError(f"Unknown viewer {viewer!r}.")

    parts: list[str] = []
    for table in TENANT_TABLES:
        path = DATA_RAW / f"{table}.parquet"
        # merchants table doesn't have a merchant filter — it's the
        # dimension; just filter to viewer's row.
        if table == "merchants":
            scope = f"merchant_id = '{viewer}'"
        elif table == "transactions":
            # The one time-bearing tenant table. Pin the analysis window
            # here (server-side, model-independent) so the agent cannot
            # answer on a self-chosen slice; transaction_items inherits
            # the window via its inner join to this CTE.
            scope = (
                f"banner_code = '{viewer}' "
                f"AND txn_ts >= DATE '{ANALYSIS_START_ISO}' "
                f"AND txn_ts < DATE '{ANALYSIS_END_ISO}'"
            )
        elif table == "transaction_items":
            # Items don't carry banner_code; scope via inner join on the
            # already-scoped (and windowed) transactions CTE.
            parts.append(
                f"{table} AS (SELECT i.* FROM "
                f"read_parquet('{path}') i JOIN transactions t "
                f"ON i.txn_id = t.txn_id)"
            )
            continue
        elif table == "promotions":
            scope = f"merchant_id = '{viewer}'"
        else:
            # products, stores carry banner_code (no time column).
            scope = f"banner_code = '{viewer}'"
        parts.append(
            f"{table} AS (SELECT * FROM read_parquet('{path}') WHERE {scope})"
        )

    shadow = ",\n     ".join(parts)
    # If the user's SQL is itself a CTE (starts with `WITH`), merge its CTEs
    # into the shadow WITH list. Naively concatenating would put two `WITH`
    # keywords back-to-back ("WITH <shadow> WITH bk AS …"), which DuckDB
    # rejects with `Parser Error at "WITH"` — so any CTE query (e.g. the
    # demand affinity self-join) would fail. The user's CTEs reference the
    # shadow tables by name and DuckDB resolves later CTEs against earlier
    # ones, so merging keeps the viewer scope + analysis window intact.
    lead_with = re.match(r"(?is)\s*WITH\s+", sql)
    if lead_with:
        user_ctes = sql[lead_with.end():]
        return "WITH " + shadow + ",\n     " + user_ctes
    return "WITH " + shadow + "\n" + sql


# ----- Lake-builder source-path guard ------------------------------------

def assert_lake_source_paths(paths: Iterable[str | Path]) -> None:
    """Assert no input path is under ``data/eval/``.

    The lake builder reads ``data/raw/`` (via the §1 observable_guard).
    ``data/eval/`` holds the anomaly answer key and is forbidden by
    physical-directory separation (Wave 1 SPEC §5).

    This guard does not require paths be under ``data/raw/`` — the lake
    builder is allowed to read configs, catalogs, etc., from elsewhere.
    The only hard ban is on ``data/eval/``.
    """
    eval_resolved = DATA_EVAL.resolve()
    for raw in paths:
        p = Path(raw).resolve()
        try:
            p.relative_to(eval_resolved)
        except ValueError:
            # Not under data/eval/ — allowed.
            continue
        raise LakeSourcePathError(
            f"Lake builder is forbidden from reading {p} — "
            f"data/eval/ holds the anomaly answer key, not lake source "
            f"data (Wave 1 SPEC §5 physical-directory separation)."
        )
