"""Phase 2D pre-demo gate — full 80-test peer-isolation matrix.

5 viewers × 16 suggested questions = 80 LLM calls.

Per-call: dispatch through the same path the dashboard uses
(`placeholders.dispatch`), audit for peer name leaks and SQL scoping,
record telemetry. Sleep 2s between calls to be rate-limit safe.

Writes a per-call JSONL log to `/tmp/phase2d_matrix.jsonl` so a crash
mid-run doesn't lose results. Final summary printed at the end.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from src.dashboard import placeholders as P  # noqa: E402

NAME_BY_ID = {
    "KRG": "Kroger", "ACM": "Acme", "WDX": "Winn-Dixie",
    "TBL": "Taco Bell", "TJX": "TJ Maxx",
}
REAL_NAMES = list(NAME_BY_ID.values())

LOG_PATH = Path("/tmp/phase2d_matrix.jsonl")


def name_leak(resp: dict, viewer: str) -> list[str]:
    own = NAME_BY_ID[viewer]
    hay = (resp.get("prose") or "")
    for c in resp.get("caveats", []) or []:
        hay += "\n" + c
    tbl = resp.get("table")
    if tbl is not None and not tbl.empty:
        hay += "\n" + tbl.to_csv(index=False)
    leaked = []
    for n in REAL_NAMES:
        if n == own:
            continue
        if re.search(rf"\b{re.escape(n)}\b", hay):
            leaked.append(n)
    return leaked


def sql_audit(resp: dict, viewer: str) -> dict:
    findings = {"tenant_predicate_missing": [], "lake_tenant_leak": []}
    for entry in resp.get("sql", []) or []:
        q = entry.get("query", "")
        if entry.get("tool") == "tenant":
            pat = rf"merchant_id\s*=\s*['\"]{re.escape(viewer)}['\"]"
            if not re.search(pat, q, re.IGNORECASE):
                findings["tenant_predicate_missing"].append(q[:120])
        elif entry.get("tool") == "lake":
            if re.search(r"\btenant_\w+\b", q, re.IGNORECASE):
                findings["lake_tenant_leak"].append(q[:120])
    return findings


def build_matrix() -> list[tuple[str, str, str]]:
    """Returns (viewer, agent_id, question_id) for all 80 cells."""
    cells: list[tuple[str, str, str]] = []
    for viewer in NAME_BY_ID:
        for agent_id, pairs in P.questions_for(viewer).items():
            for qid, _qtext in pairs:
                cells.append((viewer, agent_id, qid))
    return cells


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set.")
        return 1

    matrix = build_matrix()
    assert len(matrix) == 80, f"expected 80 cells, got {len(matrix)}"

    # Clear any prior log
    if LOG_PATH.exists():
        LOG_PATH.unlink()

    print(f"\n=== Phase 2D matrix — {len(matrix)} live LLM calls ===\n")
    print(f"Logging per-call results to {LOG_PATH}\n")

    t_start = time.monotonic()
    results: list[dict] = []
    n_leaks = 0
    n_sql_findings = 0
    n_converged = 0
    n_errors = 0

    with LOG_PATH.open("w") as logf:
        for i, (viewer, agent_id, qid) in enumerate(matrix, 1):
            t0 = time.monotonic()
            try:
                resp = P.dispatch(agent_id, qid, viewer)
                elapsed = time.monotonic() - t0
                err = None
            except Exception as exc:  # noqa: BLE001
                resp = {}
                elapsed = time.monotonic() - t0
                err = f"{type(exc).__name__}: {exc}"
                n_errors += 1

            leaks = name_leak(resp, viewer) if not err else []
            sqlf  = sql_audit(resp, viewer) if not err else {
                "tenant_predicate_missing": [], "lake_tenant_leak": [],
            }
            tel = (resp.get("telemetry") or {})
            converged = tel.get("converged", False)
            fallback = resp.get("fallback", False)

            if leaks:
                n_leaks += 1
            if sqlf["tenant_predicate_missing"] or sqlf["lake_tenant_leak"]:
                n_sql_findings += 1
            if converged:
                n_converged += 1

            row = {
                "i": i, "viewer": viewer, "agent_id": agent_id, "qid": qid,
                "elapsed": round(elapsed, 2),
                "turns":   tel.get("turns", 0),
                "in_tok":  tel.get("input_tokens", 0),
                "out_tok": tel.get("output_tokens", 0),
                "cost":    round(float(tel.get("cost_usd", 0.0)), 4),
                "converged": converged,
                "fallback":  fallback,
                "leaks":     leaks,
                "sql_findings_missing": sqlf["tenant_predicate_missing"],
                "sql_findings_leak":    sqlf["lake_tenant_leak"],
                "error": err,
            }
            results.append(row)
            logf.write(json.dumps(row) + "\n")
            logf.flush()

            tag = "ERR" if err else ("FB" if fallback
                       else ("OK" if converged else "PARTIAL"))
            print(f"  [{i:>3}/80] {viewer} {agent_id:<7} {qid:<24} "
                  f"turns={row['turns']} {row['elapsed']:>5.1f}s "
                  f"${row['cost']:.4f} {tag}"
                  + (f" leaks={leaks}" if leaks else "")
                  + (f" sql={sqlf}"
                     if (sqlf['tenant_predicate_missing'] or sqlf['lake_tenant_leak'])
                     else "")
                  + (f" err={err}" if err else ""),
                  flush=True)
            time.sleep(2)  # gentle on rate limiter

    total_elapsed = time.monotonic() - t_start
    n = len(results)
    avg_turns   = sum(r["turns"]   for r in results) / n
    avg_elapsed = sum(r["elapsed"] for r in results) / n
    avg_cost    = sum(r["cost"]    for r in results) / n
    total_cost  = sum(r["cost"]    for r in results)
    n_fallback  = sum(1 for r in results if r["fallback"])

    print("\n\n=== Phase 2D matrix summary ===")
    print(f"  Total cells:    {n}")
    print(f"  Total runtime:  {total_elapsed/60:.1f} min")
    print(f"  Total spend:    ${total_cost:.4f}")
    print(f"  Avg latency:    {avg_elapsed:.2f}s")
    print(f"  Avg cost:       ${avg_cost:.4f}")
    print(f"  Avg turns:      {avg_turns:.2f}")
    print(f"  Convergence:    {n_converged}/{n}  "
          f"({100*n_converged/n:.1f}%)  (gate ≥75/80)")
    print(f"  Fallbacks:      {n_fallback}")
    print(f"  Errors:         {n_errors}")
    print(f"  Peer-name leaks (gate 0): {n_leaks}")
    print(f"  SQL audit findings (gate 0): {n_sql_findings}")

    gate_pass = (
        n_leaks == 0 and
        n_sql_findings == 0 and
        n_converged >= 75 and
        n_errors == 0
    )
    print(f"\n  Pre-demo gate: {'PASS' if gate_pass else 'FAIL'}")

    if not gate_pass:
        print("\n  Failing cells:")
        for r in results:
            if r["leaks"] or r["sql_findings_missing"] or r["sql_findings_leak"]:
                print(f"    PRIVACY: {r['viewer']} {r['agent_id']}/{r['qid']} "
                      f"leaks={r['leaks']} sql={r['sql_findings_missing'] + r['sql_findings_leak']}")
            elif r["error"]:
                print(f"    ERROR:   {r['viewer']} {r['agent_id']}/{r['qid']} -- {r['error']}")
            elif not r["converged"]:
                print(f"    NO-CONV: {r['viewer']} {r['agent_id']}/{r['qid']} "
                      f"turns={r['turns']} cost=${r['cost']:.4f}")
    return 0 if gate_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
