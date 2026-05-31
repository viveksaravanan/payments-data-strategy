"""Wave 2 §5 query-time dual-path scoping (D22.1-.3, D24.1).

Given a viewer merchant ``M``, scope a lake-table DataFrame for the
agent surface by:

1. **Viewer exclusion** — drop rows where the underlying merchant is
   ``M``. The viewer's own data comes from the tenant surface
   (§2 ``isolation.py``), not the lake.
2. **Relationship relabel** — add a ``peer_relationship`` column:
   - same segment as ``M`` → ``segment_peer``
   - different segment → ``cross_segment``
3. **Identity strip (D24.1, L5b)** — drop the real ``banner_code``
   column from the returned frame. Inside this function we use it to
   resolve relationship; it MUST NOT exit. The agent surface receives
   only relabeled rows.

**Small-N honesty caveat (D24.1).** With only 5 merchants in the
panel, ``peer_relationship`` is *pseudonymization*, not true
anonymity — a viewer can often infer which competitor a row is by
elimination (Kroger seeing three ``segment_peer`` rows in a zone
knows the candidate set is {ACM, WDX}; with other context they
may narrow further). The aggregate cells stay k≥50 (no *consumer*
exposed), but residual is *which competitor*, a business-
confidentiality matter not PII. This caveat is stated in the
lake-build report (Stage 6).

The Wave 2 lake builder (§4) stores the real ``banner_code`` so
this wrapper can do its job; the wrapper is the contract boundary
where identity gets stripped before reaching Wave 3 agents.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd

from src.generate.config.loader import load_config

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_ROOT = REPO_ROOT / "src" / "generate" / "config"

# Merchant identifiers — single source of truth for the panel.
VALID_MERCHANTS: set[str] = {"KRG", "ACM", "WDX", "TBL", "TJX"}


class UnknownViewerError(ValueError):
    """Raised when ``scope_for_viewer`` is called with a viewer that
    isn't a known merchant in the configured panel."""


class IdentityLeakError(AssertionError):
    """Raised by ``assert_no_identity_leak`` when a frame meant for
    the agent surface still carries a real ``banner_code`` column."""


# Cache the segment-by-banner map; refreshed lazily on first use.
_SEGMENT_BY_BANNER: dict[str, str] | None = None


def _segment_by_banner() -> dict[str, str]:
    global _SEGMENT_BY_BANNER
    if _SEGMENT_BY_BANNER is None:
        cfg = load_config(CONFIG_ROOT)
        _SEGMENT_BY_BANNER = {
            m["banner_code"]: m["segment"]
            for m in cfg.merchants.values()
        }
    return _SEGMENT_BY_BANNER


# ----- Core API ---------------------------------------------------------

def scope_for_viewer(
    lake_df: pd.DataFrame,
    viewer: str,
    *,
    banner_col: str = "banner_code",
) -> pd.DataFrame:
    """Return a scoped view of ``lake_df`` for the given viewer.

    Three transformations applied in order:

    * Rows where ``banner_col == viewer`` are dropped (own data comes
      from the tenant surface, not the lake).
    * A ``peer_relationship`` column is added based on each row's
      banner segment vs. viewer's segment.
    * The ``banner_col`` is dropped (D24.1 identity strip).

    Lake tables without a banner column (``lake_cross_merchant_cohorts``
    is built on cross-merchant cohorts and doesn't carry per-merchant
    rows) are passed through unchanged — the cohort table publishes
    no merchant identity by construction.

    Parameters
    ----------
    lake_df
        A lake table built by ``src.lake.build``.
    viewer
        Banner code of the viewing merchant (one of ``VALID_MERCHANTS``).
    banner_col
        Name of the merchant-identifier column to scope on. Defaults
        to ``"banner_code"`` which all banner-keyed lake tables use.

    Returns
    -------
    pd.DataFrame
        Same row order as input minus viewer rows, with ``banner_col``
        replaced by ``peer_relationship``.

    Raises
    ------
    UnknownViewerError
        If ``viewer`` is not a known merchant.
    """
    if viewer not in VALID_MERCHANTS:
        raise UnknownViewerError(
            f"Unknown viewer {viewer!r}. Valid merchants: "
            f"{sorted(VALID_MERCHANTS)}."
        )

    # Tables without a banner column (e.g. cohorts) are already
    # identity-stripped by construction — pass through.
    if banner_col not in lake_df.columns:
        return lake_df.copy()

    seg_map = _segment_by_banner()
    if viewer not in seg_map:
        raise UnknownViewerError(
            f"Viewer {viewer!r} has no segment configured."
        )
    viewer_segment = seg_map[viewer]

    # 1. Viewer exclusion.
    scoped = lake_df[lake_df[banner_col] != viewer].copy()

    # 2. Relationship relabel.
    scoped["peer_relationship"] = scoped[banner_col].map(
        lambda b: "segment_peer" if seg_map.get(b) == viewer_segment
        else "cross_segment"
    )

    # 3. Identity strip (D24.1).
    scoped = scoped.drop(columns=[banner_col])

    return scoped.reset_index(drop=True)


# ----- L5b helper -------------------------------------------------------

# Columns that count as "real merchant identity". The agent surface
# must not see any of these after scoping.
_IDENTITY_COLUMNS: frozenset[str] = frozenset({
    "banner_code", "merchant_id", "merchant", "name",
})


def assert_no_identity_leak(df: pd.DataFrame) -> None:
    """L5b safety check: assert the frame doesn't carry any real
    merchant identifier column.

    Wave 3 agent code calls this on every lake response before
    presenting to the LLM tool surface. Failure means a scope bug
    let identity through.
    """
    leaks = _IDENTITY_COLUMNS & set(df.columns)
    if leaks:
        raise IdentityLeakError(
            f"Lake response carries real merchant identity columns: "
            f"{sorted(leaks)}. Wave 2 §5 / D24.1: only relabeled peer "
            f"tokens may reach the agent surface."
        )


# ----- Convenience surfaces ---------------------------------------------

def scope_many(
    tables: dict[str, pd.DataFrame],
    viewer: str,
) -> dict[str, pd.DataFrame]:
    """Apply ``scope_for_viewer`` to each value in ``tables``. Used
    by the dashboard / Wave 3 surfaces that load the whole lake and
    serve it per-viewer."""
    return {
        name: scope_for_viewer(df, viewer) for name, df in tables.items()
    }
