"""Wave 3 §1.1 unified response contract (D25.1).

Every agent — Pricing, Demand, Trade-Area, Anomaly, Conversational
Advisor — returns one ``AgentResponse``. The fields are the only
public surface the dashboard + Wave 4 ask-AI affordance consume.

The merged ``result`` frame is the **single source of truth** (D25.5):
the chart is built from it (§1.3, ``chart_build.py``), the claims
validator recomputes against it (§1.4, ``claims.py``), and the SQL
log lists the surfaces that fed it. Drift-by-design becomes
structurally impossible — no model-written chart values, no model-
written cell numbers; the model authors **intent and prose**, the
data authors **values**.

The dual-path shape (D22.5) typically reads two upstream frames —
own-tenant (full grain, e.g. SKU) and lake-peer (category/zone grain) —
then merges them into one comparison frame at matching grain:
``own_value`` / ``peer_benchmark`` / computed ``gap``. The merge
step lives in this module so every agent does it the same way.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import pandas as pd


# ---------------------------------------------------------------------
# AgentResponse dataclass — the public contract every agent returns.
# ---------------------------------------------------------------------

@dataclass
class SqlSurface:
    """One SQL read that fed the response. ``surface`` distinguishes
    the tenant predicate path from the lake parquet path; agents
    typically emit one of each."""
    surface: Literal["tenant", "lake"]
    query: str
    row_count: int


@dataclass
class Telemetry:
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    turns: int = 0
    converged: bool = True


@dataclass
class AgentResponse:
    """D25.1 — the unified contract.

    Fields
    ------
    result
        The MERGED comparison frame at matching grain. Single source of
        truth for chart + claims validator (D25.5). Columns at minimum:
        the merge keys + ``own_value`` + ``peer_benchmark`` + ``gap``.
    chart_intent
        Closed-schema dict (validated by ``chart_build.ChartIntent``).
        Names result columns for x/series/y_format; never names values.
    chart
        Plotly figure built deterministically from
        ``(chart_intent, result)`` by ``chart_build.build_chart``.
    prose
        Narrative answer. Validated by ``claims.validate_claims``
        against ``result`` + ``claims`` — every metric numeric in
        prose must be covered by a passing claim (§1.4 two-pass scan).
    claims
        List of ``claims.Claim`` instances. Each declared claim binds
        a numeric in ``prose`` to a cell or derivation over ``result``.
    caveats
        Code-injected when a factual condition holds (e.g. "k-anonymity
        suppression fired for 3 cells"; "peer set is 2 grocers").
    sql
        SQL surfaces that fed ``result`` — both tenant and lake reads.
    grain_notes
        The ``Excludes`` list from the lake manifest for the table the
        agent consumed, so prose knows what it can't claim and the
        dashboard can show it. Drawn from ``src.lake.manifest``.
    telemetry
        Model identifier + token / cost / turn accounting.
    """
    result: pd.DataFrame
    chart_intent: dict[str, Any]
    chart: Any                          # plotly.graph_objects.Figure at runtime
    prose: str
    claims: list[Any]                   # list[claims.Claim] — forward ref
    caveats: list[str] = field(default_factory=list)
    sql: list[SqlSurface] = field(default_factory=list)
    grain_notes: list[str] = field(default_factory=list)
    telemetry: Telemetry | None = None
    # Wave 3 Stage 6.5 follow-up #8 — claim dispositions surfaced for
    # the preview harness (passed / normalized / stripped per claim).
    # The runtime validator already produces these; we attach them
    # to the response so debug surfaces don't need to re-validate.
    # Stored as a list of dicts to avoid a hard dependency from
    # response.py on claims.py.
    claim_dispositions: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------
# Own/peer merge step (D25.5) — the dual-path shape made explicit.
# ---------------------------------------------------------------------

class MergeGrainError(ValueError):
    """Raised when an own/peer merge fails because the join keys are
    not present in one or both frames, or because the frames carry
    incompatible grains (e.g. peer at category, own at SKU with no
    category column to roll up on)."""


class MergeUnitMismatchError(ValueError):
    """Raised when ``own_value`` and ``peer_benchmark`` are in
    incompatible units / orders of magnitude after a merge — e.g.
    raw dollar revenue subtracted from a unitless index. The contract
    assumed the model would self-police column-unit comparability and
    it doesn't reliably. Catching it here lets the specialist surface
    a structured tool error back to the model on the next turn."""


def _maybe_coerce_dates(
    own_df: pd.DataFrame,
    peer_df: pd.DataFrame,
    on: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Coerce date-typed join keys to a common ``datetime64[ns]``
    dtype. Returns possibly-copied frames so the caller's inputs are
    not mutated."""
    own_out = own_df
    peer_out = peer_df
    for col in on:
        if col not in own_df.columns or col not in peer_df.columns:
            continue
        if own_df[col].dtype == peer_df[col].dtype:
            continue
        try:
            own_coerced = pd.to_datetime(own_df[col])
            peer_coerced = pd.to_datetime(peer_df[col])
        except (ValueError, TypeError, OverflowError):
            # Not date-coercible — leave as-is; merge will fail
            # naturally if dtypes are incompatible.
            continue
        if own_out is own_df:
            own_out = own_df.copy()
        if peer_out is peer_df:
            peer_out = peer_df.copy()
        own_out[col] = own_coerced
        peer_out[col] = peer_coerced
    return own_out, peer_out


def check_magnitude_compatibility(
    merged: pd.DataFrame,
    *,
    threshold: float = 100.0,
) -> tuple[bool, dict[str, float]]:
    """Compare ``own_value`` and ``peer_benchmark`` median magnitudes;
    return ``(compatible, diagnostics)``. ``compatible == False`` when
    the ratio between non-zero median |values| exceeds ``threshold``
    — the signal of a unit/scale mismatch (e.g. raw $ vs index).

    Used by ``Specialist._dispatch_tool('emit_response', ...)`` to
    surface a structured tool error rather than computing a
    nonsense ``gap`` (D4's batch-7 ``625779.0 − 1.002976 = 625778``).
    """
    diag: dict[str, float] = {
        "own_median_abs": float("nan"),
        "peer_median_abs": float("nan"),
        "ratio": float("nan"),
        "threshold": threshold,
    }
    if len(merged) == 0 or "own_value" not in merged.columns \
       or "peer_benchmark" not in merged.columns:
        return True, diag
    own_med = float(merged["own_value"].abs().median())
    peer_med = float(merged["peer_benchmark"].abs().median())
    diag["own_median_abs"] = own_med
    diag["peer_median_abs"] = peer_med
    if not (own_med > 0 and peer_med > 0):
        return True, diag
    ratio = max(own_med, peer_med) / min(own_med, peer_med)
    diag["ratio"] = ratio
    return ratio <= threshold, diag


class ViewerScopingError(ValueError):
    """Raised when ``merge_own_and_peer`` is asked to merge an own
    frame that contains rows for merchants other than the viewer,
    or a peer frame that still carries a ``banner_code`` column
    (peer frames must already be ``scope_for_viewer``'d, which strips
    identity per D24.1)."""


def merge_own_and_peer(
    own_df: pd.DataFrame,
    peer_df: pd.DataFrame,
    *,
    on: list[str],
    own_value_col: str,
    peer_value_col: str,
    gap_op: Literal["difference", "ratio"] = "difference",
    viewer: str | None = None,
    own_banner_col: str = "banner_code",
) -> pd.DataFrame:
    """Merge a tenant-side own frame with a lake-side peer frame on
    matching grain. Returns a frame with the join keys plus three
    canonical columns: ``own_value``, ``peer_benchmark``, ``gap``.

    Parameters
    ----------
    own_df
        Tenant-side data at the viewer's full grain. Must contain all
        columns in ``on`` plus ``own_value_col``. If the frame carries
        ``own_banner_col`` (typically ``banner_code``), every row must
        have ``banner_code == viewer`` — otherwise ``ViewerScopingError``.
    peer_df
        Lake-side data at the peer-published grain. Must contain all
        columns in ``on`` plus ``peer_value_col``. Must NOT carry
        ``banner_code`` / ``merchant_id`` / ``merchant`` / ``name``
        (D24.1 identity strip — the peer frame must already have been
        ``scope_for_viewer``'d). Typically also carries
        ``peer_relationship`` for downstream chart styling.
    on
        Join keys. The merge is inner — only rows present in both
        frames at matching grain survive.
    own_value_col
        Column in ``own_df`` that becomes ``own_value`` in the result.
    peer_value_col
        Column in ``peer_df`` that becomes ``peer_benchmark``.
    gap_op
        How to compute ``gap``:
        - ``"difference"`` (default): ``own_value - peer_benchmark``.
        - ``"ratio"``: ``own_value / peer_benchmark`` (NaN if zero).
    viewer
        Optional viewer banner code; when set, ``own_banner_col`` is
        validated against it.
    own_banner_col
        Column name to look for in ``own_df`` for the viewer-scoping
        check. Pass ``None`` to skip if the own frame doesn't carry one
        (e.g. the agent already filtered by viewer in the SQL).

    Returns
    -------
    pd.DataFrame
        Columns: ``on`` + extra peer columns (e.g. ``peer_relationship``)
        + ``own_value`` + ``peer_benchmark`` + ``gap``. Index reset.

    Raises
    ------
    MergeGrainError
        Join keys missing in either frame, or value columns missing.
    ViewerScopingError
        Own frame contains rows for non-viewer merchants, or peer
        frame still carries an identity column.
    """
    _IDENTITY_COLUMNS = frozenset({
        "banner_code", "merchant_id", "merchant", "name",
    })

    # --- Validate inputs ----------------------------------------------
    missing_own = [c for c in on if c not in own_df.columns]
    if missing_own:
        raise MergeGrainError(
            f"Own frame missing join keys: {missing_own}. "
            f"Has columns: {list(own_df.columns)}"
        )
    missing_peer = [c for c in on if c not in peer_df.columns]
    if missing_peer:
        raise MergeGrainError(
            f"Peer frame missing join keys: {missing_peer}. "
            f"Has columns: {list(peer_df.columns)}"
        )
    if own_value_col not in own_df.columns:
        raise MergeGrainError(
            f"Own frame missing value column {own_value_col!r}. "
            f"Has columns: {list(own_df.columns)}"
        )
    if peer_value_col not in peer_df.columns:
        raise MergeGrainError(
            f"Peer frame missing value column {peer_value_col!r}. "
            f"Has columns: {list(peer_df.columns)}"
        )

    # Coerce date-typed join keys to a common dtype. The lake's
    # ``period_start`` round-trips from parquet ``date32[day]`` as
    # pandas ``object`` dtype carrying ``datetime.date`` instances;
    # the tenant's ``DATE_TRUNC('week', txn_ts)`` materializes as
    # ``datetime64[us]`` via DuckDB. pandas merge is type-strict —
    # ``datetime.date(2026,3,1) != Timestamp('2026-03-01')`` — so an
    # un-coerced merge silently produces 0 rows. ``pd.to_datetime``
    # promotes both sides to ``datetime64[ns]`` so equality matches.
    own_df, peer_df = _maybe_coerce_dates(own_df, peer_df, on)

    # Viewer-scoping check on own frame.
    if viewer is not None and own_banner_col in own_df.columns:
        wrong_banner = own_df[own_df[own_banner_col] != viewer]
        if len(wrong_banner) > 0:
            other_banners = sorted(set(wrong_banner[own_banner_col].unique()))
            raise ViewerScopingError(
                f"Own frame for viewer {viewer!r} contains rows for other "
                f"merchants: {other_banners}. The own (tenant) frame must be "
                f"scoped to the viewer via the tenant predicate; peers come "
                f"through the lake."
            )

    # Identity check on peer frame.
    leaked = _IDENTITY_COLUMNS & set(peer_df.columns)
    if leaked:
        raise ViewerScopingError(
            f"Peer frame carries identity columns: {sorted(leaked)}. "
            f"The peer frame must already be scope_for_viewer'd; identity "
            f"is stripped at the contract boundary (D24.1)."
        )

    # --- Project + merge ----------------------------------------------
    own_proj = own_df[[*on, own_value_col]].rename(
        columns={own_value_col: "own_value"}
    )
    # Carry forward any non-key peer columns (e.g. peer_relationship,
    # txn_count) — they're useful for chart styling and grain_notes.
    peer_proj = peer_df.rename(columns={peer_value_col: "peer_benchmark"})

    merged = own_proj.merge(peer_proj, on=on, how="inner")

    # --- Compute gap --------------------------------------------------
    if gap_op == "difference":
        merged["gap"] = merged["own_value"] - merged["peer_benchmark"]
    elif gap_op == "ratio":
        merged["gap"] = merged.apply(
            lambda r: (r["own_value"] / r["peer_benchmark"])
            if r["peer_benchmark"] != 0 else float("nan"),
            axis=1,
        )
    else:                                       # pragma: no cover
        raise ValueError(f"Unknown gap_op {gap_op!r}")

    # --- Unit-compatibility on the gap (Wave 3 Stage 6.5 follow-up #6
    # softening). When own_value and peer_benchmark are in different
    # units / scales, the difference (or ratio) is not directionally
    # meaningful — but the side-by-side comparison still is. NaN the
    # gap column and attach a flag attribute the specialist reads to
    # add a side-by-side caveat. Do NOT raise — different units is a
    # valid, complete result (just not subtractable). The prior
    # behavior (specialist rejecting + the model retrying to find a
    # subtractable pair) caused most of the batch-12 thrash on P2 /
    # D3 / D4 / T4.
    ok, mag_diag = check_magnitude_compatibility(merged)
    gap_is_directional = not ok
    if gap_is_directional:
        merged["gap"] = float("nan")

    result = merged.reset_index(drop=True)
    # Attach as a DataFrame attribute (best-effort — pandas attrs
    # survive a reset_index and a copy). The specialist's
    # ``_finalize_from_emit`` reads ``gap_is_directional`` and adds a
    # caveat. If the attribute is dropped by some pandas op, the
    # specialist can re-run ``check_magnitude_compatibility`` as a
    # safety net.
    result.attrs["gap_is_directional"] = gap_is_directional
    result.attrs["magnitude_diagnostic"] = mag_diag
    return result
