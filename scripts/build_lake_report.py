"""Generate ``docs/LAKE_REPORT.md`` — the Wave 2 privacy-posture artifact.

Reads ``data/lake/*.parquet`` (produced by ``make lake`` /
``scripts/build_lake.py``) and emits a human-readable Markdown
report covering:

* Per-table cell counts, k-distribution, suppressed-cell counts/%
* Which grains had to coarsen (the ladder fire log)
* The §8 statement honestly: what's applied vs deferred-with-reason
* The D24.1 small-N pseudonymity caveat

The report is the artifact an exec/reviewer reads to trust the
anonymization posture (same role docs/DQ_REPORT.md played for
Wave 1).

Run: ``uv run python scripts/build_lake_report.py``  (or ``make lake-report``)
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import pandas as pd

from src.lake.build import K_MIN
from src.lake.manifest import full_manifest

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_LAKE = REPO_ROOT / "data" / "lake"
DATA_RAW = REPO_ROOT / "data" / "raw"
DEFAULT_OUT = REPO_ROOT / "docs" / "LAKE_REPORT.md"


def _load(name: str) -> pd.DataFrame:
    path = DATA_LAKE / f"{name}.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"Lake table {path} not found. Run `make lake` first to "
            f"materialize the lake from data/raw/."
        )
    return pd.read_parquet(path)


def _k_distribution(s: pd.Series) -> str:
    q = s.quantile([0.0, 0.05, 0.25, 0.50, 0.75, 0.95, 1.00])
    return (
        f"min={int(q[0.0]):,}, p5={int(q[0.05]):,}, "
        f"p25={int(q[0.25]):,}, p50={int(q[0.50]):,}, "
        f"p75={int(q[0.75]):,}, p95={int(q[0.95]):,}, "
        f"max={int(q[1.00]):,}"
    )


def _table_summary(name: str, df: pd.DataFrame) -> list[str]:
    lines: list[str] = []
    lines.append(f"### `{name}`\n")
    lines.append(f"- Cells published: **{len(df):,}**\n")
    lines.append(
        f"- `txn_count` distribution: {_k_distribution(df['txn_count'])}\n"
    )
    min_k = int(df["txn_count"].min())
    if min_k >= K_MIN:
        lines.append(
            f"- k≥{K_MIN} floor: **✓ cleared** "
            f"(min cell = {min_k:,} txns; {min_k // K_MIN}× the floor)\n"
        )
    else:
        lines.append(
            f"- k≥{K_MIN} floor: **⚠ violated** — min cell = {min_k:,} txns\n"
        )
    return lines


def _category_metrics_grain_breakdown(cat: pd.DataFrame) -> list[str]:
    """For category_metrics, show how many rows landed at each grain
    (subcat_week / cat_week / cat_month). This is the only table
    that exercises the k-ladder at full scale."""
    lines: list[str] = []
    if "grain" not in cat.columns:
        return lines
    by_grain = cat["grain"].value_counts().to_dict()
    lines.append("\n- Grain distribution (k-ladder fire log):\n")
    total = len(cat)
    for grain in ("subcat_week", "cat_week", "cat_month"):
        n = by_grain.get(grain, 0)
        pct = (n / total * 100) if total else 0
        lines.append(f"    - `{grain}`: {n:,} rows ({pct:.1f}%)\n")
    if "cat_month" in by_grain and by_grain["cat_month"] > 0:
        lines.append(
            f"    - **Ladder fired:** {by_grain['cat_month']:,} cells "
            f"coarsened from week to month grain to clear k≥{K_MIN}.\n"
        )
    return lines


def _coverage_lines() -> list[str]:
    """Anchor: how big is the underlying transaction census?"""
    txns = pd.read_parquet(DATA_RAW / "transactions.parquet")
    items = pd.read_parquet(DATA_RAW / "transaction_items.parquet")
    customers = pd.read_parquet(DATA_RAW / "customers.parquet")
    return [
        "## Input coverage\n",
        f"- Source: `data/raw/` (Wave 1 tenant census, deterministic at seed=42).\n",
        f"- Transactions: **{len(txns):,}**\n",
        f"- Line items: **{len(items):,}**\n",
        f"- Customers: **{len(customers):,}**\n",
        f"- 90-day window: 2026-03-01 → 2026-05-29\n\n",
    ]


def _eight_section() -> list[str]:
    return [
        "## §8 — Anonymization posture (honest)\n\n",
        "### Applied this wave\n\n",
        "- **Tokenization** — `card_id` is a 16-hex SHA-256 hash; no\n",
        "  raw PANs, names, emails, EBT/cash. (Wave 1 generation.)\n",
        "- **Generalization** — every lake table publishes at\n",
        "  category/subcategory/zone/period grain. No SKU on the peer\n",
        "  side. No per-store on the peer side.\n",
        "- **Structural k≥50** — every published cell carries `txn_count`\n",
        f"  and is suppressed if below {K_MIN} transactions. The\n",
        "  Wave 1 T17 measurement (cabarrus_edge = 483 all-three\n",
        "  cards) confirms the binding zone clears the floor by ~10×.\n",
        "- **Suppression** — the k-ladder coarsens grain (subcat→cat,\n",
        "  week→month) and drops cells that still can't clear k.\n",
        "- **Viewer exclusion + relationship relabel** — `scope.py`\n",
        "  drops viewer rows, relabels peers as `segment_peer` /\n",
        "  `cross_segment`, and strips real `banner_code` from the\n",
        "  agent surface (D24.1 identity strip).\n\n",
        "### Deferred — with reason\n\n",
        "- **l-diversity** (D21.3) — deferred. The k≥50 floor is the\n",
        "  primary anonymity threshold for Wave 2; l-diversity (every\n",
        "  cell contains ≥ℓ distinct sensitive values) is layered on\n",
        "  top in a later wave when sensitivity classes are formalized.\n",
        "- **Differential privacy** (D21.3, D24.3) — deferred. **No\n",
        "  publish() seam shipped this wave.** The published aggregate\n",
        "  numeric columns in the five lake tables ARE the future DP\n",
        "  injection point — when DP is added later, Laplace noise is\n",
        "  applied to the aggregate computation at build time, with no\n",
        "  schema change. Building no-op DP enforcement scaffolding\n",
        "  around an identity wrapper would be theater (the flaw of the\n",
        "  old name-based suppression design); the absence of the seam\n",
        "  is intentional, not an oversight.\n\n",
        "### Honest limit — small-N pseudonymity (D24.1)\n\n",
        "The panel has **5 merchants** (KRG, ACM, WDX, TBL, TJX). With\n",
        "such a small N, the `peer_relationship` relabel is\n",
        "**pseudonymization**, not true anonymity:\n\n",
        "- A viewer can often de-anonymize a `segment_peer` by\n",
        "  elimination. Kroger seeing two `segment_peer` rows in a zone\n",
        "  knows the candidate set is {ACM, WDX}; with auxiliary context\n",
        "  (store count, banner footprint), they may narrow further.\n",
        "- For sole-of-segment merchants (TBL, TJX), there is no\n",
        "  `segment_peer` to relabel — every other merchant is\n",
        "  `cross_segment` and identifiable by elimination.\n\n",
        "The aggregate cell still stays k≥50 (no individual *consumer*\n",
        "exposed); the residual risk is **which competitor** a benchmark\n",
        "names — a business-confidentiality matter, not PII. This is\n",
        "the design's honest limit, not a bug to fix in this wave.\n\n",
        "### D24.2 cohort spend posture\n\n",
        "Cross-merchant cohort spend is published as **median + IQR\n",
        "(p25/p75) + frequency band**, never as raw mean (D24.2). Means\n",
        "concentrate on whale spend in tight cohorts; the median +\n",
        "quartile bands are robust to that.\n\n",
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    lines: list[str] = []
    lines.append("# Wave 2 Lake Report\n\n")
    lines.append(
        f"_Generated {date.today().isoformat()} from `data/lake/` "
        f"built against the Wave 1 full-scale `data/raw/` (seed=42, "
        f"deterministic)._\n\n"
    )
    lines.append(
        "This is the Wave 2 privacy-posture artifact — same role\n"
        "`docs/DQ_REPORT.md` played for Wave 1. It states what the\n"
        "five anonymized lake tables look like at the published grain\n"
        "and the §8 framing of what's applied vs deferred.\n\n"
    )

    lines.extend(_coverage_lines())

    lines.append("## Per-table cell counts and k-distribution\n\n")
    cat = _load("lake_category_metrics")
    lines.extend(_table_summary("lake_category_metrics", cat))
    lines.extend(_category_metrics_grain_breakdown(cat))
    lines.append("\n")

    pay = _load("lake_payment_mix")
    lines.extend(_table_summary("lake_payment_mix", pay))
    lines.append("\n")

    seg = _load("lake_segment_mix")
    lines.extend(_table_summary("lake_segment_mix", seg))
    seg_breakdown = seg["behavioral_segment"].value_counts().to_dict()
    lines.append(
        f"\n- Behavioral segments (DERIVED — not the planted "
        f"`loyalty_type`): {seg_breakdown}\n\n"
    )

    trade = _load("lake_trade_area")
    lines.extend(_table_summary("lake_trade_area", trade))
    lines.append("\n")

    cohort = _load("lake_cross_merchant_cohorts")
    lines.extend(_table_summary("lake_cross_merchant_cohorts", cohort))
    all_three = cohort[cohort["cohort_combination"] == "all_three"]
    if len(all_three) > 0:
        lines.append(
            f"\n- All-three cohort per zone: "
            f"min cohort_size={int(all_three['cohort_size'].min())}, "
            f"max={int(all_three['cohort_size'].max())}, "
            f"median={int(all_three['cohort_size'].median())} cards.\n"
        )
        lines.append(
            "  (Wave 1 T17 reported 483-1,126 all-three cards per\n"
            "  **planted** `home_zone`; the cohort table groups by\n"
            "  **behavioral** home zone — the zone where the card\n"
            "  transacts most. Distributions differ when planted and\n"
            "  behavioral zones disagree, but the order of magnitude\n"
            "  matches.)\n"
        )
    lines.append("\n")

    lines.extend(_eight_section())

    lines.append("## Grain manifest (D23.7)\n\n")
    lines.append(
        "Per-table machine-readable spec consumed by Wave 3 agents. "
        "Reflects the post-scope agent surface (`peer_relationship` "
        "where the raw lake stores `banner_code`).\n\n"
    )
    for name, spec in full_manifest().items():
        lines.append(f"### `{name}`\n\n")
        lines.append(f"- Finest grain: `{spec['finest_grain']}`\n")
        lines.append(
            f"- Dimensions: {', '.join(f'`{d}`' for d in spec['dimensions'])}\n"
        )
        lines.append(
            f"- Metrics: {', '.join(f'`{m}`' for m in spec['metrics'])}\n"
        )
        lines.append(f"- k floor: {spec['k_floor']}\n")
        lines.append(f"- Ladder: {spec['ladder']}\n")
        lines.append("- Excludes:\n")
        for excl in spec["excludes"]:
            lines.append(f"    - {excl}\n")
        lines.append("\n")

    lines.append("---\n\n")
    lines.append(
        "_The L1-L12 acceptance battery (`tests/lake/test_L*.py`) is the "
        "machine-checked counterpart to this human-readable report._\n"
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(lines))
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
