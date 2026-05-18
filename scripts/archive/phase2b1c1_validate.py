"""Phase 2B.1 + 2C.1 retest — anomaly rewrite + demand convergence fix.

5 live LLM calls:
  1. Anomaly KRG smoke — "What unusual patterns am I seeing in the last 30 days?"
  2. Anomaly TJX no-peer — "Any unusual store-level patterns?"
  3. Anomaly targeted — "Why did my Plaza Midwood store have a huge avocado bump in April?"
     Verify Haiku investigates only Plaza Midwood / avocado, not all three.
  4. Demand orchestrated — "Slowing ice cream — what should I do?"
  5. Demand KRG smoke regression — "Which SKUs are slow movers I should consider clearing?"

Convergence target: 5/5. Latency target: <20s/call. Cost target: <$0.05/call.
"""
from __future__ import annotations

import os
import re
import time

from src.agents.anomaly import AnomalyDetectionSpecialist
from src.agents.context import MerchantContext
from src.agents.demand import DemandForecastingSpecialist
from src.agents.orchestrator import Orchestrator

NAME_BY_ID = {
    "KRG": "Kroger", "ACM": "Acme", "WDX": "Winn-Dixie",
    "TBL": "Taco Bell", "TJX": "TJ Maxx",
}
REAL_NAMES = list(NAME_BY_ID.values())


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
                findings["tenant_predicate_missing"].append(q[:80])
        elif entry.get("tool") == "lake":
            if re.search(r"\btenant_\w+\b", q, re.IGNORECASE):
                findings["lake_tenant_leak"].append(q[:80])
    return findings


def run_specialist(label, spec_cls, viewer, question):
    ctx = MerchantContext.for_merchant(viewer)
    spec = spec_cls(ctx)
    t0 = time.monotonic()
    sr = spec.answer(question)
    elapsed = time.monotonic() - t0
    rd = sr.to_dict()
    rd["_meta"] = {
        "label": label, "viewer": viewer, "question": question,
        "elapsed": round(elapsed, 2), "turns": sr.turns,
        "converged": sr.converged,
        "in_tok": sr.input_tokens, "out_tok": sr.output_tokens,
        "cost": round(sr.cost_usd, 4),
        "n_queries": len(sr.sql),
    }
    return rd


def run_orchestrator(label, viewer, question):
    ctx = MerchantContext.for_merchant(viewer)
    orch = Orchestrator(ctx)
    t0 = time.monotonic()
    oresp = orch.ask(question)
    elapsed = time.monotonic() - t0
    rd = oresp.to_dict()
    tel = rd.get("telemetry") or {}
    rd["_meta"] = {
        "label": label, "viewer": viewer, "question": question,
        "elapsed": round(elapsed, 2),
        "turns": tel.get("turns", 0),
        "converged": tel.get("converged", True),
        "cost": round(float(tel.get("cost_usd", 0.0)), 4),
        "routing": rd.get("routing"),
        "n_queries": len(rd.get("sql") or []),
    }
    return rd


def report(rd, *, expected_phrase=None, expected_only_anomaly=None,
           forbidden_terms=None):
    m = rd["_meta"]
    print(f"\n--- {m['label']} ({m['viewer']}) ---")
    print(f"Q: {m['question']}")
    print(f"Turns: {m['turns']}  Queries: {m.get('n_queries', 0)}  "
          f"Elapsed: {m['elapsed']}s  Cost: ${m['cost']:.4f}  "
          f"Converged: {m['converged']}")
    if m.get("routing"):
        r = m["routing"]
        print(f"Routing: → {r.get('primary')}  via_fallback={r.get('via_fallback')}")
    prose = rd.get("prose") or ""
    print(f"\nProse:\n{prose}\n")
    if rd.get("caveats"):
        print(f"Caveats: {rd.get('caveats')}\n")
    tbl = rd.get("table")
    if tbl is not None and not tbl.empty:
        print(f"Table ({len(tbl)} rows): {list(tbl.columns)}")

    # Targeted-anomaly check: must mention the expected anomaly term but
    # NOT mention the other two (i.e. didn't run a checklist).
    if expected_only_anomaly is not None:
        present = expected_only_anomaly.lower() in prose.lower()
        forbidden_seen = [t for t in (forbidden_terms or [])
                          if t.lower() in prose.lower()]
        print(f"Expected anomaly mention ({expected_only_anomaly!r}): "
              f"{'YES' if present else 'NO'}")
        print(f"Other-anomaly contamination: "
              f"{forbidden_seen if forbidden_seen else 'none'}")
        m["targeted_ok"] = (present and not forbidden_seen)

    if expected_phrase is not None:
        present = expected_phrase in prose
        print(f"Expected phrase '{expected_phrase}': {'YES' if present else 'NO'}")
        m["phrase_ok"] = present

    leaks = name_leak(rd, m["viewer"])
    sqlf = sql_audit(rd, m["viewer"])
    audit_ok = (not leaks
                and not sqlf["tenant_predicate_missing"]
                and not sqlf["lake_tenant_leak"])
    m["audit_ok"] = audit_ok
    print(f"Audit: {'OK' if audit_ok else 'FAIL ' + str(leaks) + ' ' + str(sqlf)}")


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set.")
        return 1

    print("\n=== Phase 2B.1+2C.1 retest — 5 live LLM calls ===\n")
    results = []

    # 1. Anomaly KRG smoke — must converge
    r = run_specialist(
        "ANOMALY KRG smoke",
        AnomalyDetectionSpecialist, "KRG",
        "What unusual patterns am I seeing in the last 30 days?",
    )
    report(r)
    results.append(r)
    time.sleep(2)

    # 2. Anomaly TJX no-peer — must emit the phrase and converge
    r = run_specialist(
        "ANOMALY TJX no-peer",
        AnomalyDetectionSpecialist, "TJX",
        "Any unusual store-level patterns?",
    )
    report(r, expected_phrase="No segment peers available for this response.")
    results.append(r)
    time.sleep(2)

    # 3. Anomaly targeted — Plaza Midwood avocado.
    #    Verify Haiku investigates ONLY this one, not all three.
    r = run_specialist(
        "ANOMALY targeted (avocado)",
        AnomalyDetectionSpecialist, "KRG",
        "Why did my Plaza Midwood store have a huge avocado bump in April?",
    )
    report(r,
           expected_only_anomaly="avocado",
           forbidden_terms=["University City", "pasta"])
    results.append(r)
    time.sleep(2)

    # 4. Demand orchestrated — slowing ice cream — must converge
    r = run_orchestrator(
        "DEMAND orch ice cream",
        "KRG",
        "Slowing ice cream — what should I do?",
    )
    report(r)
    results.append(r)
    time.sleep(2)

    # 5. Demand KRG smoke regression — must still converge
    r = run_specialist(
        "DEMAND KRG smoke",
        DemandForecastingSpecialist, "KRG",
        "Which SKUs are slow movers I should consider clearing?",
    )
    report(r)
    results.append(r)

    # Summary
    n = len(results)
    metas = [x["_meta"] for x in results]
    converged = sum(1 for m in metas if m["converged"])
    avg_turns   = sum(m["turns"]   for m in metas) / n
    avg_elapsed = sum(m["elapsed"] for m in metas) / n
    avg_cost    = sum(m["cost"]    for m in metas) / n
    total_cost  = sum(m["cost"]    for m in metas)
    all_audit_ok = all(m.get("audit_ok") for m in metas)

    print("\n\n=== Summary ===")
    print(f"  Convergence:   {converged}/{n}  (target 5/5)")
    print(f"  Avg turns:     {avg_turns:.2f}  (target 3-4)")
    print(f"  Avg latency:   {avg_elapsed:.2f}s  (target <20s)")
    print(f"  Avg cost/q:    ${avg_cost:.4f}    (target <$0.05)")
    print(f"  Total spend:   ${total_cost:.4f}")
    print(f"  Audits clean:  {'YES' if all_audit_ok else 'NO'}")

    print(f"\n  {'label':<28} {'view':<5} {'q':>2} {'turn':>4} "
          f"{'elapsed':>8} {'cost':>7} conv  audit")
    for m in metas:
        extras = []
        if m.get("phrase_ok") is not None:
            extras.append(f"phrase={'Y' if m['phrase_ok'] else 'N'}")
        if m.get("targeted_ok") is not None:
            extras.append(f"targeted={'Y' if m['targeted_ok'] else 'N'}")
        print(f"  {m['label']:<28} {m['viewer']:<5} "
              f"{m['n_queries']:>2} {m['turns']:>4} "
              f"{m['elapsed']:>7.2f}s ${m['cost']:>6.4f} "
              f"{'Y' if m['converged'] else 'N':<4}  "
              f"{'OK' if m.get('audit_ok') else 'FAIL':<4}  "
              + " ".join(extras))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
