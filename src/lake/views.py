"""Lake view-builders.

`V2_5_DATA_DESIGN.md` Phase 2 specifies the lake as two logical tables:

  - ``lake_transactions`` — wide, denormalized; one row per
    transaction line item with peer + transaction + product +
    discount-pattern context. 21 columns.
  - ``lake_stores`` — store-level reference for geographic queries.
    6 columns.

**Phase 1.5 (V3_AUDIT.md Decision §1.1) materializes the lake at seed
time** as per-viewer physical tables — `lake_transactions_<viewer>` /
`lake_stores_<viewer>` — built once by ``src/db/seed.py`` using the
``_build_lake_*_sql`` helpers below. Runtime queries read those
materialized tables directly (no per-row Python UDFs). The lake remains
*logically* virtual — defined as a privacy-engine transformation of
tenant data — but the transformation runs once at seed time rather
than per query. Agent-facing SQL contracts (`lake_transactions`,
`lake_stores`) are unchanged: the agent's SQL keeps referencing the
unqualified names, and ``src/agents/tools.py::query_lake`` wraps it in
a CTE that resolves to the per-viewer materialized table.

Privacy semantics are identical to v2.5: the viewing merchant's data
is excluded; peer merchants are pseudonymized via ``peer_id``; opaque
IDs replace ``txn_id`` / ``store_id`` to prevent merchant-prefix
leakage; ZIP5 → ZIP3; full timestamp → ``txn_date`` + 2-hour
``txn_hour_bucket``; ``txn_total`` → 10-bin ``txn_total_bin``;
``customer_id`` is **dropped** ("no consumer linkage").

Helpers in this module:

  - ``to_hour_bucket(ts)``: timestamp → 10-bucket label.
  - ``to_total_bin(amount)``: dollar amount → 10-bin label.
  - ``generate_opaque_id(*parts)``: salted SHA-256 truncated to 16 chars.
  - ``apply_k_anonymity(df, count_col, k)``: cell-suppression utility
    for aggregate queries (k=5 in v2.5).
"""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pandas as pd

from .peer_mapping import peer_case_sql

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = ROOT / "data" / "payments.db"

# k=5 for v2.5 per the design (panel size doesn't support k≥50).
K_ANONYMITY_K = 5

# Salt for opaque IDs. The mapping is per-build deterministic — same
# input always produces the same opaque output within a single
# database build. Rotating in production would tie this to a
# per-build secret.
LAKE_OPAQUE_SALT = "v2.5-lake-opaque-salt"

# 10 time-of-day buckets (V2_5_DATA_DESIGN.md lines 901–913).
HOUR_BUCKETS: list[str] = [
    "early_morning",   # 5–7am
    "morning",         # 7–9am
    "mid_morning",     # 9–11am
    "lunch",           # 11am–1pm
    "afternoon",       # 1–3pm
    "late_afternoon",  # 3–5pm
    "evening",         # 5–7pm
    "dinner",          # 7–9pm
    "late_evening",    # 9–11pm
    "late_night",      # 11pm–5am
]

# 10 transaction-total bins (V2_5_DATA_DESIGN.md lines 916–927).
TOTAL_BINS: list[str] = [
    "$0-5", "$5-10", "$10-20", "$20-35", "$35-50",
    "$50-75", "$75-100", "$100-150", "$150-250", "$250+",
]


# ---------------------------------------------------------------------------
# Helpers (also registered as SQLite functions on each connection)
# ---------------------------------------------------------------------------

def to_hour_bucket(ts: str | None) -> str | None:
    """Map a ``YYYY-MM-DD HH:MM:SS`` string to one of HOUR_BUCKETS.

    Returns None for None input (lets the SQL ``IFNULL``-friendly).
    """
    if ts is None:
        return None
    try:
        hour = int(ts[11:13])
    except (TypeError, ValueError, IndexError):
        return None
    if 5 <= hour < 7:
        return "early_morning"
    if 7 <= hour < 9:
        return "morning"
    if 9 <= hour < 11:
        return "mid_morning"
    if 11 <= hour < 13:
        return "lunch"
    if 13 <= hour < 15:
        return "afternoon"
    if 15 <= hour < 17:
        return "late_afternoon"
    if 17 <= hour < 19:
        return "evening"
    if 19 <= hour < 21:
        return "dinner"
    if 21 <= hour < 23:
        return "late_evening"
    return "late_night"  # 23, 0, 1, 2, 3, 4


def to_total_bin(amount: float | None) -> str | None:
    """Map a dollar amount to one of TOTAL_BINS."""
    if amount is None:
        return None
    a = float(amount)
    if a < 5:
        return "$0-5"
    if a < 10:
        return "$5-10"
    if a < 20:
        return "$10-20"
    if a < 35:
        return "$20-35"
    if a < 50:
        return "$35-50"
    if a < 75:
        return "$50-75"
    if a < 100:
        return "$75-100"
    if a < 150:
        return "$100-150"
    if a < 250:
        return "$150-250"
    return "$250+"


def generate_opaque_id(*parts) -> str:
    """Deterministic 16-char hex id from one or more inputs.

    Uses SHA-256 with a fixed salt; same input(s) always return the
    same output. Used for ``lake_txn_id`` (over (txn_id, line_id)) and
    ``lake_store_id`` (over store_id) so peer merchants joining on
    these IDs see consistent values without exposing the underlying
    merchant-prefixed strings.
    """
    payload = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(
        (LAKE_OPAQUE_SALT + payload).encode("utf-8")
    ).hexdigest()[:16]


# ---------------------------------------------------------------------------
# k=5 cell suppression
# ---------------------------------------------------------------------------

def apply_k_anonymity(
    df: pd.DataFrame,
    count_col: str = "n",
    k: int = K_ANONYMITY_K,
) -> pd.DataFrame:
    """Drop rows where ``count_col < k``. Used by aggregate queries on
    customer-dimension breakdowns (e.g. counts by ZIP3 × neighborhood
    × payment_type) so cells small enough to identify individuals are
    suppressed.

    Returns a new DataFrame; does not mutate the input.
    """
    if count_col not in df.columns:
        raise KeyError(
            f"apply_k_anonymity needs a column named {count_col!r}; "
            f"got {list(df.columns)}"
        )
    return df[df[count_col] >= k].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Connection setup
# ---------------------------------------------------------------------------

def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.execute("PRAGMA foreign_keys = ON")
    register_lake_functions(conn)
    return conn


# ---------------------------------------------------------------------------
# View builders
# ---------------------------------------------------------------------------

# 21 columns per V2_5_DATA_DESIGN.md lines 627–650. Phase 1.5: this
# SELECT runs once per viewer at seed time (`src/db/seed.py`) to build
# the `lake_transactions_<viewer>` physical table. Runtime queries
# read the materialized table directly via `lake_transactions_sql()`.
_LAKE_TXN_SQL_TEMPLATE = """
SELECT
    _opaque_id(t.txn_id, l.line_id)              AS lake_txn_id,
    l.line_id                                    AS line_id,
    {peer_case}                                  AS peer_id,
    m.segment                                    AS peer_segment,
    _opaque_id(t.store_id)                       AS lake_store_id,
    DATE(t.txn_ts)                               AS txn_date,
    _to_hour_bucket(t.txn_ts)                    AS txn_hour_bucket,
    t.payment_type                               AS payment_type,
    t.card_network                               AS card_network,
    t.entry_mode                                 AS entry_mode,
    t.wallet_type                                AS wallet_type,
    t.connectivity_type                          AS connectivity_type,
    _to_total_bin(t.txn_total)                   AS txn_total_bin,
    p.name                                       AS canonical_name,
    p.category                                   AS category,
    p.subcategory                                AS subcategory,
    l.unit_price                                 AS unit_price,
    l.qty                                        AS qty,
    l.discount                                   AS discount,
    l.line_total                                 AS line_total,
    CASE WHEN l.discount > 0
         THEN ROUND(l.discount / (l.unit_price * l.qty), 2)
         ELSE NULL END                           AS discount_pct_applied
FROM tenant_transactions t
JOIN tenant_transaction_items l ON l.txn_id = t.txn_id
JOIN tenant_products p          ON p.sku    = l.sku
JOIN merchants m                ON m.merchant_id = t.merchant_id
WHERE t.merchant_id != :viewing{extra_filter}
"""

# 6 columns per V2_5_DATA_DESIGN.md lines 671–679.
_LAKE_STORES_SQL_TEMPLATE = """
SELECT
    _opaque_id(s.store_id)               AS lake_store_id,
    {peer_case}                          AS peer_id,
    m.segment                            AS peer_segment,
    SUBSTR(s.store_zip5, 1, 3)           AS store_zip3,
    s.neighborhood                       AS neighborhood,
    s.metro_region                       AS metro_region
FROM tenant_stores s
JOIN merchants m ON m.merchant_id = s.merchant_id
WHERE s.merchant_id != :viewing{extra_filter}
"""


# Phase 1.5 (Decision §1.1): the lake is materialized at seed time as
# per-viewer physical tables `lake_transactions_<M>` / `lake_stores_<M>`.
# Runtime queries read those tables directly — no per-row Python UDFs.
# The heavy UDF templates remain as private build helpers used only by
# `src/db/seed.py`. Agent-facing SQL contracts (`lake_transactions`,
# `lake_stores`) are preserved: the agent's SQL is unchanged; the
# runner's CTE wrapper redirects to the materialized table.
_VALID_VIEWERS = ("KRG", "ACM", "WDX", "TBL", "TJX")


def _validate_viewer(viewer: str) -> None:
    if viewer not in _VALID_VIEWERS:
        raise ValueError(
            f"Unknown viewing_merchant_id {viewer!r}. "
            f"Expected one of {list(_VALID_VIEWERS)}."
        )


def _build_lake_transactions_sql(viewing_merchant_id: str) -> str:
    """Full UDF-laden SELECT used by `src/db/seed.py` to materialize
    `lake_transactions_<viewer>` once at seed time. The `:viewing`
    bind parameter is supplied by the caller.

    Not for runtime use — runtime queries go through
    `lake_transactions_sql()` which returns a simple
    `SELECT * FROM lake_transactions_<viewer>` against the
    materialized table.
    """
    case_sql = peer_case_sql(viewing_merchant_id, "t.merchant_id")
    return _LAKE_TXN_SQL_TEMPLATE.format(peer_case=case_sql, extra_filter="")


def _build_lake_stores_sql(viewing_merchant_id: str) -> str:
    """Full UDF-laden SELECT used by `src/db/seed.py` to materialize
    `lake_stores_<viewer>` once at seed time."""
    case_sql = peer_case_sql(viewing_merchant_id, "s.merchant_id")
    return _LAKE_STORES_SQL_TEMPLATE.format(peer_case=case_sql, extra_filter="")


def lake_transactions_sql(viewing_merchant_id: str) -> str:
    """Return the runtime SELECT body for ``lake_transactions`` as
    seen by the agent's `query_lake` CTE wrapper. Phase 1.5 materialized
    the lake at seed time, so this is a simple `SELECT * FROM
    lake_transactions_<viewer>` against the per-viewer physical table.
    """
    _validate_viewer(viewing_merchant_id)
    return f"SELECT * FROM lake_transactions_{viewing_merchant_id}"


def lake_stores_sql(viewing_merchant_id: str) -> str:
    """Return the runtime SELECT body for ``lake_stores`` — simple
    `SELECT * FROM lake_stores_<viewer>` post-materialization."""
    _validate_viewer(viewing_merchant_id)
    return f"SELECT * FROM lake_stores_{viewing_merchant_id}"


def register_lake_functions(conn: sqlite3.Connection) -> None:
    """Register the helper SQLite functions used inside the lake view
    SQL: ``_opaque_id`` (variadic), ``_to_hour_bucket``, ``_to_total_bin``.
    Call before executing any query that references ``lake_transactions``
    or ``lake_stores``.
    """
    conn.create_function("_opaque_id", -1, generate_opaque_id)
    conn.create_function("_to_hour_bucket", 1, to_hour_bucket)
    conn.create_function("_to_total_bin", 1, to_total_bin)


def _validate_filter(sql_filter: str) -> None:
    """Reject filter strings that look like injection attempts. We
    enforce SELECT-only at the runner level too (Phase 5b); this is a
    second line of defense at the view-builder level.
    """
    if sql_filter is None:
        return
    lowered = sql_filter.lower()
    forbidden = (
        ";", "--", "/*", "*/",
        " drop ", " insert ", " update ", " delete ",
        " attach ", " detach ", " alter ", " create ",
        " replace ", " grant ", " revoke ", " truncate ",
        " vacuum ", " pragma ", " exec ", " execute ",
    )
    for token in forbidden:
        if token in lowered or lowered.startswith(token.strip()):
            raise ValueError(
                f"sql_filter rejected: contains forbidden token {token.strip()!r}"
            )


def get_lake_transactions(
    viewing_merchant_id: str,
    *,
    db_path: Path | None = None,
    sql_filter: str | None = None,
) -> pd.DataFrame:
    """Return the lake-transactions wide view from the perspective of
    ``viewing_merchant_id``.

    Phase 1.5 materialized the lake at seed time, so this reads from
    `lake_transactions_<viewer>` directly. The viewing merchant's own
    transactions are excluded; the other four merchants appear
    pseudonymized as ``peer_a``..``peer_d`` per the documented
    mapping. ``customer_id`` is not present — the lake enforces "no
    consumer linkage" per strategy doc §5.2.

    `sql_filter` is appended as an outer ``WHERE (sql_filter)`` clause
    — useful for narrowing to a date or category. **It must reference
    lake output columns (e.g., `txn_date`, `category`, `payment_type`,
    `discount`), not internal tenant aliases** — the lake is
    materialized; the tenant join aliases (`t.`, `l.`, `p.`) that the
    pre-Phase-1.5 template used are no longer in scope.
    """
    _validate_viewer(viewing_merchant_id)
    _validate_filter(sql_filter)
    sql = f"SELECT * FROM lake_transactions_{viewing_merchant_id}"
    if sql_filter:
        sql += f" WHERE ({sql_filter})"
    db = db_path or DEFAULT_DB_PATH
    conn = _connect(db)
    try:
        return pd.read_sql_query(sql, conn)
    finally:
        conn.close()


def get_lake_stores(
    viewing_merchant_id: str,
    *,
    db_path: Path | None = None,
    sql_filter: str | None = None,
) -> pd.DataFrame:
    """Return the lake-stores reference view from the perspective of
    ``viewing_merchant_id``.

    Phase 1.5 materialized: reads from `lake_stores_<viewer>` directly.
    Same exclusion / pseudonymization rules as
    ``get_lake_transactions``. ``sql_filter`` must reference lake
    output columns (`peer_id`, `peer_segment`, `store_zip3`,
    `neighborhood`, `metro_region`), not the pre-Phase-1.5 `s.`-aliased
    tenant column names.
    """
    _validate_viewer(viewing_merchant_id)
    _validate_filter(sql_filter)
    sql = f"SELECT * FROM lake_stores_{viewing_merchant_id}"
    if sql_filter:
        sql += f" WHERE ({sql_filter})"
    db = db_path or DEFAULT_DB_PATH
    conn = _connect(db)
    try:
        return pd.read_sql_query(sql, conn)
    finally:
        conn.close()
