"""Phase 2D.1 resumable peer-isolation matrix.

CSV-backed result store at `tests/peer_isolation_results.csv`. Each row
is one (viewer, agent_id, question_id) cell; the runner skips cells
already present in the CSV. This makes a stall or interrupt a non-event
— re-running picks up where it left off.

Usage:
  phase2d1_matrix.py                  # full 80-cell matrix (resumable)
  phase2d1_matrix.py --leak-only      # 10-cell anomaly leak test only
  phase2d1_matrix.py --reset          # clear CSV before running
  phase2d1_matrix.py --reset-agent X  # delete agent X rows before running
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from src.dashboard import placeholders as P  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "tests" / "peer_isolation_results.csv"

# Map ID → name; the set of "other merchant names" that must NOT appear
# in a non-owning viewer's response.
NAME_BY_ID = {
    "KRG": "Kroger", "ACM": "Acme", "WDX": "Winn-Dixie",
    "TBL": "Taco Bell", "TJX": "TJ Maxx",
}
REAL_NAMES = list(NAME_BY_ID.values())

# Cells used for the targeted leak test — anomalies most at risk
# because the knowledge base previously named merchants. 5 viewers ×
# 2 questions.
LEAK_TEST_CELLS: list[tuple[str, str, str]] = [
    (viewer, "anomaly", qid)
    for viewer in NAME_BY_ID
    for qid in ("plaza_avocado", "anything_unusual")
]

CSV_COLUMNS = [
    "viewer", "agent_id", "qid",
    "elapsed_sec", "turns", "in_tok", "out_tok", "cost_usd",
    "converged", "fallback",
    "leaks_json", "sql_predicate_missing_count", "sql_tenant_leak_count",
    "error", "prose_head_200",
]


# ---------------------------------------------------------------------------
# Audits
# ---------------------------------------------------------------------------

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
    missing, leak = [], []
    for entry in resp.get("sql", []) or []:
        q = entry.get("query", "")
        if entry.get("tool") == "tenant":
            pat = rf"merchant_id\s*=\s*['\"]{re.escape(viewer)}['\"]"
            if not re.search(pat, q, re.IGNORECASE):
                missing.append(q[:120])
        elif entry.get("tool") == "lake":
            if re.search(r"\btenant_\w+\b", q, re.IGNORECASE):
                leak.append(q[:120])
    return {"missing": missing, "leak": leak}


# ---------------------------------------------------------------------------
# CSV persistence
# ---------------------------------------------------------------------------

def load_done() -> set[tuple[str, str, str]]:
    done: set[tuple[str, str, str]] = set()
    if not CSV_PATH.exists():
        return done
    with CSV_PATH.open() as f:
        for row in csv.DictReader(f):
            done.add((row["viewer"], row["agent_id"], row["qid"]))
    return done


def append_row(row: dict) -> None:
    write_header = not CSV_PATH.exists() or CSV_PATH.stat().st_size == 0
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CSV_PATH.open("a") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if write_header:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in CSV_COLUMNS})


def reset_csv(agent_filter: str | None = None) -> int:
    """Reset the CSV. With agent_filter, delete only those agent's rows."""
    if not CSV_PATH.exists():
        return 0
    if agent_filter is None:
        n = sum(1 for _ in CSV_PATH.open()) - 1  # minus header
        CSV_PATH.unlink()
        return max(n, 0)
    # Filter mode: rewrite CSV without the matching agent's rows
    kept = []
    with CSV_PATH.open() as f:
        for row in csv.DictReader(f):
            if row["agent_id"] != agent_filter:
                kept.append(row)
    deleted = (sum(1 for _ in CSV_PATH.open()) - 1) - len(kept)
    with CSV_PATH.open("w") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        w.writeheader()
        for row in kept:
            w.writerow({k: row.get(k, "") for k in CSV_COLUMNS})
    return deleted


# ---------------------------------------------------------------------------
# Run one cell
# ---------------------------------------------------------------------------

def run_cell(viewer: str, agent_id: str, qid: str) -> dict:
    t0 = time.monotonic()
    err = None
    try:
        resp = P.dispatch(agent_id, qid, viewer)
    except Exception as exc:  # noqa: BLE001
        resp = {}
        err = f"{type(exc).__name__}: {exc}"
    elapsed = time.monotonic() - t0

    leaks = name_leak(resp, viewer) if not err else []
    sqlf  = sql_audit(resp, viewer) if not err else {"missing": [], "leak": []}
    tel = resp.get("telemetry") or {}

    return {
        "viewer": viewer, "agent_id": agent_id, "qid": qid,
        "elapsed_sec": round(elapsed, 2),
        "turns":   tel.get("turns", 0),
        "in_tok":  tel.get("input_tokens", 0),
        "out_tok": tel.get("output_tokens", 0),
        "cost_usd": round(float(tel.get("cost_usd", 0.0)), 4),
        "converged": str(bool(tel.get("converged", False))).lower(),
        "fallback":  str(bool(resp.get("fallback", False))).lower(),
        "leaks_json": json.dumps(leaks),
        "sql_predicate_missing_count": len(sqlf["missing"]),
        "sql_tenant_leak_count":       len(sqlf["leak"]),
        "error": err or "",
        "prose_head_200": (resp.get("prose") or "")[:200].replace("\n", " "),
    }


# ---------------------------------------------------------------------------
# Matrix construction
# ---------------------------------------------------------------------------

def build_full_matrix() -> list[tuple[str, str, str]]:
    cells: list[tuple[str, str, str]] = []
    for viewer in NAME_BY_ID:
        for agent_id, pairs in P.questions_for(viewer).items():
            for qid, _qtext in pairs:
                cells.append((viewer, agent_id, qid))
    return cells


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def run(cells: list[tuple[str, str, str]], *, label: str,
        stop_on_leak: bool = False) -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set.")
        return 1

    done = load_done()
    skipped = sum(1 for c in cells if c in done)
    todo = [c for c in cells if c not in done]
    print(f"\n=== {label} ===")
    print(f"Total cells:  {len(cells)}")
    print(f"Already done: {skipped} (resumed from {CSV_PATH})")
    print(f"To run:       {len(todo)}\n")

    if not todo:
        print("Nothing to do; running summary on existing CSV.")

    t_start = time.monotonic()
    for i, (viewer, agent_id, qid) in enumerate(todo, 1):
        row = run_cell(viewer, agent_id, qid)
        append_row(row)
        leaks = json.loads(row["leaks_json"])
        sql_missing = int(row["sql_predicate_missing_count"])
        sql_leak    = int(row["sql_tenant_leak_count"])
        if row["error"]:
            tag = "ERR"
        elif leaks:
            tag = f"LEAK={leaks}"
        elif sql_missing or sql_leak:
            tag = f"SQL_FINDING m={sql_missing} l={sql_leak}"
        elif row["fallback"] == "true":
            tag = "FB"
        elif row["converged"] == "true":
            tag = "OK"
        else:
            tag = "PARTIAL"
        print(f"  [{i:>3}/{len(todo)}] {viewer} {agent_id:<7} {qid:<24} "
              f"turns={row['turns']} {row['elapsed_sec']:>5.1f}s "
              f"${row['cost_usd']:.4f} {tag}", flush=True)

        if stop_on_leak and leaks:
            print(f"\n!! LEAK DETECTED on cell {viewer}/{agent_id}/{qid}: "
                  f"{leaks}. Stopping per --stop-on-leak.")
            return 2
        time.sleep(2)

    # ---- summary -----------------------------------------------------
    return summary(cells, label, time.monotonic() - t_start)


def summary(cells: list[tuple[str, str, str]], label: str,
            elapsed: float) -> int:
    by_key = {(r["viewer"], r["agent_id"], r["qid"]): r
              for r in csv.DictReader(CSV_PATH.open())}
    rows = [by_key[c] for c in cells if c in by_key]
    n = len(rows)
    if n == 0:
        print("\n(no rows in CSV for this cell set)")
        return 1
    n_leaks = sum(1 for r in rows if json.loads(r["leaks_json"]))
    n_sql_findings = sum(1 for r in rows
                         if int(r["sql_predicate_missing_count"])
                         or int(r["sql_tenant_leak_count"]))
    n_converged = sum(1 for r in rows if r["converged"] == "true")
    n_errors = sum(1 for r in rows if r["error"])
    n_fallback = sum(1 for r in rows if r["fallback"] == "true")
    total_cost   = sum(float(r["cost_usd"])    for r in rows)
    avg_cost     = total_cost / n
    avg_elapsed  = sum(float(r["elapsed_sec"]) for r in rows) / n
    avg_turns    = sum(int(float(r["turns"]))  for r in rows) / n

    print(f"\n\n=== {label} summary ===")
    print(f"  Total cells:     {n}")
    print(f"  Runtime:         {elapsed/60:.1f} min")
    print(f"  Total spend:     ${total_cost:.4f}")
    print(f"  Avg latency:     {avg_elapsed:.2f}s")
    print(f"  Avg cost:        ${avg_cost:.4f}")
    print(f"  Avg turns:       {avg_turns:.2f}")
    print(f"  Convergence:     {n_converged}/{n}  "
          f"({100*n_converged/n:.1f}%)")
    print(f"  Fallbacks:       {n_fallback}")
    print(f"  Errors:          {n_errors}")
    print(f"  Peer-name leaks: {n_leaks}")
    print(f"  SQL findings:    {n_sql_findings}")

    gate_pass = (n_leaks == 0 and n_sql_findings == 0
                 and n_converged >= 75 and n_errors == 0)
    if n == 80:
        print(f"\n  Gate (0 leaks, 0 SQL findings, ≥75/80 conv, 0 errors): "
              f"{'PASS' if gate_pass else 'FAIL'}")
    if n_leaks or n_sql_findings or n_errors or n < n_converged:
        print("\n  Issue cells:")
        for r in rows:
            issues = []
            if json.loads(r["leaks_json"]):
                issues.append(f"leaks={json.loads(r['leaks_json'])}")
            if int(r["sql_predicate_missing_count"]) or int(r["sql_tenant_leak_count"]):
                issues.append(f"sql_m={r['sql_predicate_missing_count']} sql_l={r['sql_tenant_leak_count']}")
            if r["error"]:
                issues.append(f"err={r['error']}")
            if r["converged"] != "true":
                issues.append("not-converged")
            if issues:
                print(f"    {r['viewer']:<4} {r['agent_id']:<7} {r['qid']:<24} "
                      f"-> {', '.join(issues)}")
    return 0 if (n < 80 or gate_pass) else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--leak-only", action="store_true",
                        help="Run only the 10-cell anomaly leak test.")
    parser.add_argument("--reset", action="store_true",
                        help="Delete the CSV before running.")
    parser.add_argument("--reset-agent", default=None,
                        help="Delete rows for this agent before running.")
    args = parser.parse_args()

    if args.reset:
        n = reset_csv()
        print(f"Cleared {n} rows from {CSV_PATH}")
    if args.reset_agent:
        n = reset_csv(agent_filter=args.reset_agent)
        print(f"Cleared {n} {args.reset_agent} rows from {CSV_PATH}")

    if args.leak_only:
        return run(LEAK_TEST_CELLS,
                   label="Targeted anomaly leak test (10 cells)",
                   stop_on_leak=True)

    return run(build_full_matrix(),
               label="Phase 2D.1 full matrix (80 cells)")


if __name__ == "__main__":
    sys.exit(main())
