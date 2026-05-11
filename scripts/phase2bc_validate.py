"""Phase 2B/C validation — 3 new specialists + orchestrator.

Test plan (9 live LLM calls total, ~$0.30 budget at Haiku rates):
  1. KRG anomaly smoke   — "What unusual patterns am I seeing in the last 30 days?"
  2. KRG demand smoke    — "Which SKUs are slow movers I should consider clearing?"
  3. KRG trade smoke     — "Which of my stores is underperforming and why?"
  4. TJX pricing no-peer — "How am I priced vs peers?"
  5. TJX anomaly no-peer — "Anything unusual recently?"
  6. Orchestrator route → pricing  ("Am I priced above the market on dairy?")
  7. Orchestrator route → anomaly  ("My University City stores keep dropping. Why?")
  8. Orchestrator route → demand   ("Slowing ice cream — what should I do?")
  9. Orchestrator route → trade    ("Where should I open a new store?")

Reports: routing decisions, per-call telemetry, peer-name leak audit,
SQL audit (tenant-predicate + no-tenant-leak-in-lake).
"""
from __future__ import annotations

import os
import re
import time

from src.agents.anomaly import AnomalyDetectionSpecialist
from src.agents.context import MerchantContext
from src.agents.demand import DemandForecastingSpecialist
from src.agents.orchestrator import Orchestrator
from src.agents.trade import TradeAreaSpecialist

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


def run_specialist(label: str, spec_cls, viewer: str, question: str) -> dict:
    ctx = MerchantContext.for_merchant(viewer)
    spec = spec_cls(ctx)
    t0 = time.monotonic()
    sr = spec.answer(question)
    elapsed = time.monotonic() - t0
    rd = sr.to_dict()
    rd["_meta"] = {
        "label": label, "viewer": viewer, "question": question,
        "elapsed": round(elapsed, 2),
        "turns":   sr.turns,
        "converged": sr.converged,
        "in_tok":  sr.input_tokens,
        "out_tok": sr.output_tokens,
        "cost":    round(sr.cost_usd, 4),
    }
    return rd


def run_orchestrator(label: str, viewer: str, question: str) -> dict:
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
        "turns":   tel.get("turns", 0),
        "converged": tel.get("converged", True),
        "in_tok":  tel.get("input_tokens", 0),
        "out_tok": tel.get("output_tokens", 0),
        "cost":    round(float(tel.get("cost_usd", 0.0)), 4),
        "routing": rd.get("routing"),
    }
    return rd


def print_specialist(rd: dict, expected_anomalies: list[str] | None = None,
                     no_peer_phrase: bool = False) -> None:
    m = rd["_meta"]
    print(f"\n--- {m['label']} ({m['viewer']}) ---")
    print(f"Q: {m['question']}")
    print(f"Turns: {m['turns']}  Elapsed: {m['elapsed']}s  "
          f"Cost: ${m['cost']:.4f}  Converged: {m['converged']}")
    prose = rd.get("prose") or ""
    print(f"\n{prose}\n")
    if rd.get("caveats"):
        print(f"Caveats: {rd.get('caveats')}")
    tbl = rd.get("table")
    if tbl is not None and not tbl.empty:
        print(f"Table ({len(tbl)} rows): {list(tbl.columns)}")
    if expected_anomalies:
        hits = [a for a in expected_anomalies if a.lower() in prose.lower()]
        print(f"Anomaly-mention hits: {hits} (need ≥1)")
    if no_peer_phrase:
        present = "No segment peers available for this response" in prose
        print(f"No-peer phrasing present: {'YES' if present else 'NO'}")
    leaks = name_leak(rd, m["viewer"])
    sqlf = sql_audit(rd, m["viewer"])
    audit_ok = (not leaks and not sqlf["tenant_predicate_missing"]
                and not sqlf["lake_tenant_leak"])
    print(f"Audit: {'OK' if audit_ok else 'FAIL'}"
          + (f"  leaks={leaks}" if leaks else "")
          + (f"  sql={sqlf}" if not audit_ok and (sqlf['tenant_predicate_missing'] or sqlf['lake_tenant_leak']) else ""))
    rd["_meta"]["audit_ok"] = audit_ok


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set.")
        return 1

    print("\n=== Phase 2B/C validation — 9 live LLM calls ===\n")

    all_calls: list[dict] = []

    # 1. KRG anomaly smoke
    rd1 = run_specialist(
        "ANOMALY smoke", AnomalyDetectionSpecialist, "KRG",
        "What unusual patterns am I seeing in the last 30 days?",
    )
    print_specialist(rd1, expected_anomalies=[
        "University City", "Plaza Midwood", "pasta", "avocado",
    ])
    all_calls.append(rd1)
    time.sleep(2)

    # 2. KRG demand smoke
    rd2 = run_specialist(
        "DEMAND smoke", DemandForecastingSpecialist, "KRG",
        "Which SKUs are slow movers I should consider clearing?",
    )
    print_specialist(rd2)
    all_calls.append(rd2)
    time.sleep(2)

    # 3. KRG trade smoke
    rd3 = run_specialist(
        "TRADE smoke", TradeAreaSpecialist, "KRG",
        "Which of my stores is underperforming and why?",
    )
    print_specialist(rd3)
    all_calls.append(rd3)
    time.sleep(2)

    # 4. TJX pricing no-peer
    from src.agents.pricing import PricingSpecialist
    rd4 = run_specialist(
        "PRICING no-peer (TJX)", PricingSpecialist, "TJX",
        "How am I priced vs peers?",
    )
    print_specialist(rd4, no_peer_phrase=True)
    all_calls.append(rd4)
    time.sleep(2)

    # 5. TJX anomaly no-peer
    rd5 = run_specialist(
        "ANOMALY no-peer (TJX)", AnomalyDetectionSpecialist, "TJX",
        "Anything unusual recently?",
    )
    print_specialist(rd5, no_peer_phrase=True)
    all_calls.append(rd5)
    time.sleep(2)

    # 6-9. Orchestrator routing tests
    routing_cases = [
        ("ORCH→pricing", "KRG", "Am I priced above the market on dairy?",        "pricing"),
        ("ORCH→anomaly", "KRG", "My University City stores keep dropping. Why?", "anomaly"),
        ("ORCH→demand",  "KRG", "Slowing ice cream — what should I do?",         "demand"),
        ("ORCH→trade",   "KRG", "Where should I open a new store?",              "trade"),
    ]
    for label, viewer, q, expected_primary in routing_cases:
        rd = run_orchestrator(label, viewer, q)
        m = rd["_meta"]
        routing = m.get("routing") or {}
        primary = routing.get("primary")
        fallback = routing.get("via_fallback")
        match = "✓" if primary == expected_primary else "✗"
        print(f"\n--- {label} ({viewer}) ---")
        print(f"Q: {q}")
        print(f"Routed to: {primary}  (expected: {expected_primary})  "
              f"{match}  via_fallback={fallback}  "
              f"rationale: {routing.get('rationale')}")
        print(f"Turns: {m['turns']}  Elapsed: {m['elapsed']}s  "
              f"Cost: ${m['cost']:.4f}")
        prose = (rd.get("prose") or "")
        print(f"Prose (first 280 chars):\n{prose[:280]}…")
        leaks = name_leak(rd, m["viewer"])
        sqlf = sql_audit(rd, m["viewer"])
        audit_ok = (not leaks and not sqlf["tenant_predicate_missing"]
                    and not sqlf["lake_tenant_leak"])
        print(f"Audit: {'OK' if audit_ok else 'FAIL'}"
              + (f"  leaks={leaks}" if leaks else ""))
        m["audit_ok"] = audit_ok
        m["routed_correctly"] = (primary == expected_primary)
        all_calls.append(rd)
        time.sleep(2)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    n = len(all_calls)
    metas = [c["_meta"] for c in all_calls]
    avg_turns   = sum(m["turns"]   for m in metas) / n
    avg_elapsed = sum(m["elapsed"] for m in metas) / n
    avg_cost    = sum(m["cost"]    for m in metas) / n
    total_cost  = sum(m["cost"]    for m in metas)
    all_audit_ok = all(m.get("audit_ok", False) for m in metas)
    all_converged = all(m.get("converged", True) for m in metas)

    print("\n\n=== Summary ===")
    print(f"  n = {n} live calls")
    print(f"  avg turns:     {avg_turns:.2f}")
    print(f"  avg latency:   {avg_elapsed:.2f}s  (target <20s)")
    print(f"  avg cost/q:    ${avg_cost:.4f}    (target <$0.05)")
    print(f"  total spend:   ${total_cost:.4f}")
    print(f"  all converged: {'YES' if all_converged else 'NO'}")
    print(f"  audits clean:  {'YES' if all_audit_ok else 'NO'}")
    print("\n  Per-call breakdown:")
    print(f"  {'label':<24} {'view':<5} {'turns':>5} {'elapsed':>8} "
          f"{'cost':>8}  conv  audit")
    for m in metas:
        print(f"  {m['label']:<24} {m['viewer']:<5} "
              f"{m['turns']:>5} {m['elapsed']:>7.2f}s "
              f"${m['cost']:>6.4f}  "
              f"{'Y' if m['converged'] else 'N':<4}  "
              f"{'OK' if m.get('audit_ok') else 'FAIL'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
