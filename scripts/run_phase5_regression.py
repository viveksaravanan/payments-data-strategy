"""Phase 5.5 regression run.

Re-dispatches every cassette in ``tests/cassettes/baseline/`` against
the current Phase 5 prompts, captures the new response + telemetry,
and writes a comparison file to ``tests/cassettes/comparisons/`` for
manual grading.

Each comparison file holds:
  * The baseline response (prose + caveats + telemetry)
  * The fresh Phase 5 response (same shape)
  * Mechanical contract-compliance checks (7 booleans)
  * ``grade`` and ``notes`` fields the reviewer fills in by hand

After the run completes, also writes
``tests/cassettes/comparisons/SUMMARY.md`` with a rollup table.

Live API spend: ~$0.50–1.00 at Haiku 4.5 rates. Wall clock
~5–10 minutes depending on per-cassette turn counts.

Usage::

    uv run python scripts/run_phase5_regression.py
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Allow ``python scripts/run_phase5_regression.py`` (no ``-m``).
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

from src.agents import llm as L  # noqa: E402
from tests.cassette_helpers import (  # noqa: E402
    BASELINE_DIR, _git_short_sha, _strip_unserializable,
    check_contract_compliance, compare_cassettes,
)

COMPARISONS_DIR = ROOT / "tests" / "cassettes" / "comparisons"


def _phase_label() -> str:
    """Phase tag stamped on comparison files. Bump per regression run."""
    return "5.4"


def main() -> int:
    if not L.is_available():
        print("ERROR: ANTHROPIC_API_KEY not set. Add it to .env first.", file=sys.stderr)
        return 1

    baselines = sorted(BASELINE_DIR.glob("*.json"))
    if not baselines:
        print(f"ERROR: no baseline cassettes in {BASELINE_DIR}", file=sys.stderr)
        return 1

    COMPARISONS_DIR.mkdir(parents=True, exist_ok=True)

    from src.dashboard.agents import _run_specialist

    print(f"Phase 5 regression: re-running {len(baselines)} baseline cassettes "
          f"against {L.MODEL_SPECIALIST}.")
    print(f"Output: {COMPARISONS_DIR}/")
    print()

    results: list[dict] = []
    total_cost = 0.0
    started = time.time()

    for i, baseline_path in enumerate(baselines, start=1):
        meta = json.loads(baseline_path.read_text())
        spec = meta["specialist"]
        qid  = meta["qid"]
        mid  = meta["merchant_id"]
        label = f"{spec:8s} {qid:5s} {mid:3s}"

        print(f"  [{i:2d}/{len(baselines)}] {label} ... ", end="", flush=True)
        t0 = time.time()
        try:
            response = _run_specialist(spec, qid, mid)
        except Exception as exc:  # noqa: BLE001
            print(f"FAILED ({type(exc).__name__}: {exc})")
            results.append({
                "name": baseline_path.stem,
                "failed": True,
                "error": repr(exc),
            })
            continue

        elapsed = time.time() - t0
        phase5 = _strip_unserializable(response)
        # Attach wall-clock time alongside the agent's own telemetry.
        tel = dict(phase5.get("telemetry") or {})
        tel["wall_time_s"] = round(elapsed, 2)
        phase5["telemetry"] = tel

        comparison = compare_cassettes(baseline_path, phase5)
        comparison["phase_baseline"] = "4.6"
        comparison["phase_compare"]  = _phase_label()
        comparison["compared_at"]    = (
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        )
        comparison["commit_sha"]     = _git_short_sha()

        out_path = COMPARISONS_DIR / baseline_path.name
        out_path.write_text(
            json.dumps(comparison, indent=2, ensure_ascii=False) + "\n",
        )

        cost = float(tel.get("cost_usd", 0.0) or 0.0)
        turns = tel.get("turns", 0)
        total_cost += cost
        results.append({
            "name": baseline_path.stem,
            "qid": qid,
            "specialist": spec,
            "merchant_id": mid,
            "baseline_telemetry": meta["response_dict"].get("telemetry") or {},
            "phase5_telemetry":   tel,
            "compliance":         comparison["contract_compliance"],
            "converged":          bool(tel.get("converged", True)),
            "failed":             False,
        })
        print(f"OK ({turns} turns, ${cost:.4f}, {elapsed:.1f}s)")

    elapsed_total = time.time() - started
    print()
    n_ok = sum(1 for r in results if not r.get("failed"))
    print(f"Done. Wrote {n_ok}/{len(baselines)} comparison files "
          f"in {elapsed_total:.1f}s. Total API spend: ${total_cost:.4f}.")

    # Generate SUMMARY.md
    _write_summary(results, total_cost, elapsed_total)
    return 0 if all(not r.get("failed") for r in results) else 2


def _write_summary(results: list[dict], total_cost: float, elapsed: float) -> None:
    """Write the machine-generated comparison rollup."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sha       = _git_short_sha()

    checks = [
        ("Headline #", "headline_has_number"),
        ("3-5 bullets", "evidence_3_to_5_bullets"),
        ("Therefore", "therefore_section_present"),
        ("Approved opener", "approved_therefore_opener"),
        ("No forbidden verbs", "no_forbidden_verbs"),
        ("Caveats parsed", "caveats_fence_parsed"),
        ("No throat-clearing", "no_throat_clearing"),
    ]

    lines: list[str] = []
    lines.append("# Phase 5 regression summary")
    lines.append("")
    lines.append(f"Generated: {timestamp}")
    lines.append(f"Phase 5 commit: `{sha}`")
    lines.append("Baseline phase: 4.6 (cassettes recorded against commit `480c590`)")
    lines.append(f"Total wall clock: {elapsed:.1f}s")
    lines.append(f"Total API spend (phase5 re-run): ${total_cost:.4f}")
    lines.append("")

    # Contract compliance table
    lines.append("## Contract compliance (mechanical checks)")
    lines.append("")
    header = "| Cassette | " + " | ".join(c[0] for c in checks) + " |"
    sep    = "|---|" + "|".join("---" for _ in checks) + "|"
    lines.append(header)
    lines.append(sep)
    n_full_pass = 0
    for r in results:
        if r.get("failed"):
            row = f"| {r['name']} | " + " | ".join("—" for _ in checks) + " |"
            lines.append(row)
            continue
        cells = []
        all_pass = True
        for _, key in checks:
            ok = r["compliance"].get(key, False)
            cells.append("✅" if ok else "❌")
            if not ok:
                all_pass = False
        lines.append(f"| {r['name']} | " + " | ".join(cells) + " |")
        if all_pass:
            n_full_pass += 1

    n_ok = sum(1 for r in results if not r.get("failed"))
    lines.append("")
    lines.append(f"**Full-pass: {n_full_pass}/{n_ok} cassettes pass all 7 checks.**")
    lines.append("")

    # Cost comparison
    lines.append("## Cost comparison")
    lines.append("")
    baseline_cost = sum(
        float((r.get("baseline_telemetry") or {}).get("cost_usd", 0.0) or 0.0)
        for r in results if not r.get("failed")
    )
    delta_pct = (
        ((total_cost - baseline_cost) / baseline_cost * 100)
        if baseline_cost > 0 else 0.0
    )
    lines.append(f"- Total Phase 5 cost: **${total_cost:.4f}**")
    lines.append(f"- Total baseline cost: **${baseline_cost:.4f}**")
    sign = "+" if delta_pct >= 0 else ""
    lines.append(f"- Delta: **{sign}{delta_pct:.1f}%**")
    lines.append("")
    lines.append("Per-cassette cost deltas:")
    lines.append("")
    lines.append("| Cassette | Baseline | Phase 5 | Δ |")
    lines.append("|---|---:|---:|---:|")
    for r in results:
        if r.get("failed"):
            lines.append(f"| {r['name']} | — | — | (failed) |")
            continue
        bc = float((r["baseline_telemetry"] or {}).get("cost_usd", 0.0) or 0.0)
        pc = float((r["phase5_telemetry"] or {}).get("cost_usd", 0.0) or 0.0)
        d = ((pc - bc) / bc * 100) if bc > 0 else 0.0
        s = "+" if d >= 0 else ""
        lines.append(f"| {r['name']} | ${bc:.4f} | ${pc:.4f} | {s}{d:.0f}% |")
    lines.append("")

    # Turn count comparison
    lines.append("## Turn count comparison")
    lines.append("")
    base_turns = sum(
        int((r.get("baseline_telemetry") or {}).get("turns", 0) or 0)
        for r in results if not r.get("failed")
    )
    p5_turns = sum(
        int((r.get("phase5_telemetry") or {}).get("turns", 0) or 0)
        for r in results if not r.get("failed")
    )
    lines.append(f"- Total Phase 5 turns: **{p5_turns}**")
    lines.append(f"- Total baseline turns: **{base_turns}**")
    lines.append("")
    lines.append("Per-cassette turn deltas:")
    lines.append("")
    lines.append("| Cassette | Baseline | Phase 5 | Δ |")
    lines.append("|---|---:|---:|---:|")
    for r in results:
        if r.get("failed"):
            lines.append(f"| {r['name']} | — | — | (failed) |")
            continue
        bt = int((r["baseline_telemetry"] or {}).get("turns", 0) or 0)
        pt = int((r["phase5_telemetry"] or {}).get("turns", 0) or 0)
        delta = pt - bt
        s = "+" if delta >= 0 else ""
        lines.append(f"| {r['name']} | {bt} | {pt} | {s}{delta} |")
    lines.append("")

    # Convergence
    lines.append("## Convergence")
    lines.append("")
    p5_conv = sum(1 for r in results if not r.get("failed") and r.get("converged"))
    n_total = len(results)
    lines.append(f"- Phase 5: **{p5_conv}/{n_total}** cassettes converged")
    nonconv = [
        r["name"] for r in results
        if not r.get("failed") and not r.get("converged")
    ]
    failed = [r["name"] for r in results if r.get("failed")]
    if nonconv:
        lines.append("- Non-converged (hit MAX_TURNS): " + ", ".join(nonconv))
    if failed:
        lines.append("- Failed dispatches: " + ", ".join(failed))
    lines.append("")

    # Next step
    lines.append("## Next step")
    lines.append("")
    lines.append("Grade each comparison file manually:")
    lines.append("")
    lines.append("1. Open `tests/cassettes/comparisons/<cassette>.json`")
    lines.append("2. Compare `baseline_response.prose` vs `phase5_response.prose`")
    lines.append('3. Set the `grade` field to `"better"`, `"equal"`, or `"worse"`')
    lines.append("4. Optionally add notes")
    lines.append("")
    lines.append("Pass criterion (revised after Phase 5.3 deferral):")
    lines.append("")
    lines.append("- ≥ 4-6 cassettes graded `better`")
    lines.append("- ≥ 6-8 cassettes graded `equal`")
    lines.append("- 0 cassettes graded `worse`")

    out_path = COMPARISONS_DIR / "SUMMARY.md"
    out_path.write_text("\n".join(lines) + "\n")
    print(f"Summary: {out_path.relative_to(ROOT)}")


if __name__ == "__main__":
    sys.exit(main())
