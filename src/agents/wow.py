"""Server-side week-over-week "top movers" helper (Phase 4).

The anomaly agent used to hand the model a full week × dimension pivot (e.g. a
429-row category×week frame) and ask it to diff the rows in-head. The model
only ever saw the first ``LLM_ROW_BUDGET`` rows — the rest was truncated — so
it gave up and narrated ("Result was truncated — pulling the last two weeks…").
That truncation is the root cause of the A2/A3 answer-key failures.

This module reduces a weekly-grain frame to its biggest movers **server-side**:

* **Deterministic** — every viewer narrates the same event the same way (no
  more KRG "−16% revenue" vs WDX "−35% units, skipping a week").
* **Grounded** — the returned rows ARE the queried result; every number the
  model then states traces to a cell here, exactly as ``run_lake_sql``'s
  aggregated/k-floored output does. No number comes from "thinking".
* **Reads only observable data** — it operates on the frame the agent already
  queried through the guarded tenant/lake path; it never touches ``data/eval/``.

Canonical delta basis (pins cross-viewer consistency): the most-recent complete
week vs the **mean of the ``BASELINE_WEEKS`` weeks immediately before it**, per
dimension value. The metric is units (the agent's SQL selects ``SUM(qty)``),
matching how the planted anomalies are defined — so "up 180%" means the same
thing across every pill and viewer.
"""
from __future__ import annotations

import pandas as pd

# Delta basis + shaping defaults.
BASELINE_WEEKS = 4
DEFAULT_TOP_N = 3          # up to top_n risers + top_n decliners
DEFAULT_MIN_VOLUME = 0.0   # baseline-mean noise floor (units); tool may raise it

# Column names in the reduced frame the helper returns.
MOVER_COLUMNS = ("recent", "baseline", "delta_pct", "direction")


def _empty(dim_col: str) -> pd.DataFrame:
    return pd.DataFrame(columns=[dim_col, *MOVER_COLUMNS])


def compute_movers(
    df: pd.DataFrame,
    *,
    dim_col: str,
    week_col: str,
    value_col: str,
    count_col: str | None = None,
    k_floor: int = 50,
    min_volume: float = DEFAULT_MIN_VOLUME,
    top_n: int = DEFAULT_TOP_N,
    baseline_weeks: int = BASELINE_WEEKS,
) -> pd.DataFrame:
    """Reduce a tidy ``week × dimension`` frame to its biggest recent-vs-baseline
    movers.

    A dimension value is **dropped** (not merely down-ranked) when:

    * its series is incomplete — missing the recent week or any baseline week
      (a lake dimension whose thin weeks were k-suppressed lands here);
    * ``count_col`` is given and any involved week is below ``k_floor`` lines
      (privacy / thin-base — the "k on both weeks" guarantee);
    * the baseline mean is below ``min_volume`` (tiny-base noise) or zero.

    Returns a DataFrame of at most ``2*top_n`` rows (top_n risers + top_n
    decliners), ordered by ``|delta_pct|`` descending, with columns
    ``[dim_col, 'recent', 'baseline', 'delta_pct', 'direction']``. Empty frame
    when nothing clears the filters (caller treats that as "no comparison").
    """
    if df is None or len(df) == 0:
        return _empty(dim_col)
    for col in (dim_col, week_col, value_col):
        if col not in df.columns:
            raise KeyError(f"compute_movers: column {col!r} not in {list(df.columns)}")

    weeks = sorted(df[week_col].dropna().unique())
    if len(weeks) < baseline_weeks + 1:
        return _empty(dim_col)
    recent_week = weeks[-1]
    base_weeks = weeks[-(baseline_weeks + 1):-1]  # the `baseline_weeks` before recent
    needed = [recent_week, *base_weeks]

    rows: list[dict] = []
    for dim, g in df.groupby(dim_col, sort=True):
        val_by_week = dict(zip(g[week_col], g[value_col]))
        if any(w not in val_by_week for w in needed):
            continue  # incomplete series (or k-suppressed weeks on the lake side)
        if count_col is not None:
            cnt_by_week = dict(zip(g[week_col], g[count_col]))
            if any(float(cnt_by_week.get(w, 0)) < k_floor for w in needed):
                continue  # k on both (all) involved weeks
        recent = float(val_by_week[recent_week])
        baseline = sum(float(val_by_week[w]) for w in base_weeks) / len(base_weeks)
        if baseline <= 0 or baseline < min_volume:
            continue  # noise floor / undefined pct
        delta = (recent - baseline) / baseline
        rows.append({
            dim_col:      dim,
            "recent":     round(recent, 4),
            "baseline":   round(baseline, 4),
            "delta_pct":  round(delta, 4),
            "direction":  "up" if delta > 0 else "down",
        })

    if not rows:
        return _empty(dim_col)
    movers = pd.DataFrame(rows)

    # top_n risers + top_n decliners, each ordered deterministically
    # (delta then dim name for ties, stable sort).
    up = movers[movers["delta_pct"] > 0].sort_values(
        ["delta_pct", dim_col], ascending=[False, True], kind="mergesort").head(top_n)
    down = movers[movers["delta_pct"] < 0].sort_values(
        ["delta_pct", dim_col], ascending=[True, True], kind="mergesort").head(top_n)
    out = pd.concat([up, down], ignore_index=True)
    # Final display order: biggest absolute move first (stable).
    out = out.sort_values(
        "delta_pct", key=lambda s: s.abs(), ascending=False, kind="mergesort",
    ).reset_index(drop=True)
    return out
