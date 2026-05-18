"""Phase 2D.1 — demo-rehearsal verification.

Runs the 5 demo-critical flows through the exact dispatch entry points
the dashboard's chat panel uses:
  - `placeholders.dispatch(agent_id, qid, viewer)` for suggested buttons
  - `placeholders.dispatch_orchestrated(merchant_id, raw_question)` for free-form

This is byte-identical to what the dashboard executes on a button/Ask
click; the dashboard just wraps it in a Streamlit placeholder + chat-
history push. The output here is what would be rendered as `entry["prose"]`
in `_render_chat_entry`.

For each flow we capture:
  - First-click latency, turns, cost, convergence
  - Cache hit on second click (must be < 100ms, same response object)
  - Per-call telemetry shape (drives the per-question hover tooltip)
  - First 600 chars of prose for visual review
  - Peer-name leak audit + SQL audit
"""
from __future__ import annotations

import re
import time

from dotenv import load_dotenv

load_dotenv()

# Stub session_state cache (same pattern as scripts/phase2d_cache_verify).
import src.dashboard.placeholders as P_mod  # noqa: E402

_CACHE: dict = {}
def _fake_cache_get(key): return _CACHE.get(key)
def _fake_cache_set(key, value):
    if value.get("fallback"): return
    _CACHE[key] = value
P_mod._cache_get = _fake_cache_get
P_mod._cache_set = _fake_cache_set

from src.dashboard import placeholders as P  # noqa: E402


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
    return [n for n in REAL_NAMES
            if n != own and re.search(rf"\b{re.escape(n)}\b", hay)]


def sql_clean(resp: dict, viewer: str) -> bool:
    for entry in resp.get("sql", []) or []:
        q = entry.get("query", "")
        if entry.get("tool") == "tenant":
            if not re.search(rf"merchant_id\s*=\s*['\"]{viewer}['\"]", q, re.IGNORECASE):
                return False
        elif entry.get("tool") == "lake":
            if re.search(r"\btenant_\w+\b", q, re.IGNORECASE):
                return False
    return True


def run_flow(label: str, viewer: str, *, agent_id: str | None = None,
             qid: str | None = None, free_form_question: str | None = None) -> dict:
    print(f"\n{'=' * 78}\n{label}\n{'=' * 78}")
    print(f"  Viewer: {viewer}")

    if free_form_question:
        print(f"  Free-form: {free_form_question!r}")
        t0 = time.monotonic()
        r1 = P.dispatch_orchestrated(viewer, free_form_question)
        t_first = time.monotonic() - t0

        # Free-form is NOT cached — second call should NOT be instant.
        # We don't measure cache hit here.
        cached_via_session = False
    else:
        print(f"  Button: {agent_id} / {qid}")
        t0 = time.monotonic()
        r1 = P.dispatch(agent_id, qid, viewer)
        t_first = time.monotonic() - t0

        # Suggested-question dispatch — second click should hit cache.
        t0 = time.monotonic()
        r2 = P.dispatch(agent_id, qid, viewer)
        t_second_ms = (time.monotonic() - t0) * 1000
        cached_via_session = (r1 is r2)

    tel = r1.get("telemetry") or {}
    converged = tel.get("converged", False)
    cost = float(tel.get("cost_usd", 0.0))
    turns = int(tel.get("turns", 0))
    in_tok = int(tel.get("input_tokens", 0))
    out_tok = int(tel.get("output_tokens", 0))
    leaks = name_leak(r1, viewer)
    sql_audit_ok = sql_clean(r1, viewer)

    # Verify telemetry hover-tooltip shape (the dashboard reads these fields)
    has_hover_data = all(k in tel for k in ("cost_usd", "input_tokens",
                                            "output_tokens", "turns"))

    print(f"\n  Agent label:   {r1.get('agent')}")
    print(f"  First call:    {t_first:.2f}s  turns={turns}  cost=${cost:.4f}  "
          f"tokens={in_tok} in / {out_tok} out")
    print(f"  Converged:     {converged}")
    if not free_form_question:
        print(f"  Cache hit (2nd click): "
              f"{t_second_ms:.1f}ms  same_obj={cached_via_session}")
    else:
        print(f"  Free-form path — orchestrator routed to: "
              f"{(r1.get('routing') or {}).get('primary')}")
    print(f"  Privacy:       leaks={leaks}  sql_clean={sql_audit_ok}")
    print(f"  Hover-tooltip telemetry present: {has_hover_data}")

    print(f"\n  --- Response prose (first 600 chars) ---")
    prose = (r1.get("prose") or "")
    print(prose[:600] + ("..." if len(prose) > 600 else ""))

    if r1.get("caveats"):
        print(f"\n  --- Caveats (rendered as separate block) ---")
        for c in r1["caveats"][:5]:
            print(f"    • {c}")

    tbl = r1.get("table")
    if tbl is not None and not tbl.empty:
        print(f"\n  --- Table ({len(tbl)} rows) ---")
        print(tbl.head(5).to_string(index=False, max_cols=6))

    print(f"\n  Pass criteria:")
    pass_converged = converged
    pass_privacy   = not leaks and sql_audit_ok
    pass_telemetry = has_hover_data
    pass_cache     = cached_via_session if not free_form_question else True
    print(f"    converged:       {'YES' if pass_converged else 'NO'}")
    print(f"    privacy clean:   {'YES' if pass_privacy else 'NO'}")
    print(f"    telemetry shape: {'YES' if pass_telemetry else 'NO'}")
    if not free_form_question:
        print(f"    cache hit:       {'YES' if pass_cache else 'NO'}")
    overall = pass_converged and pass_privacy and pass_telemetry and pass_cache
    print(f"  Verdict: {'PASS' if overall else 'FAIL'}")
    return {
        "label": label, "pass": overall,
        "converged": converged, "privacy": pass_privacy,
        "telemetry": pass_telemetry, "cache": pass_cache,
        "elapsed": round(t_first, 2), "cost": round(cost, 4), "turns": turns,
        "prose_preview": prose[:280],
    }


def main() -> int:
    print("\nPhase 2D.1 — demo-rehearsal verification (5 flows)\n")
    print("All flows go through `placeholders.dispatch` / `dispatch_orchestrated`")
    print("— the exact entry points used by the dashboard's chat panel.\n")

    results = []
    results.append(run_flow(
        "FLOW 1 — KRG Pricing (peer dairy comparison)",
        "KRG", agent_id="pricing", qid="dairy_vs_peers",
    ))
    time.sleep(2)

    results.append(run_flow(
        "FLOW 2 — ACM Pricing (cross-merchant peer mapping inverts)",
        "ACM", agent_id="pricing", qid="dairy_vs_peers",
    ))
    time.sleep(2)

    results.append(run_flow(
        "FLOW 3 — KRG Trade (underserved neighborhoods)",
        "KRG", agent_id="trade", qid="underserved",
    ))
    time.sleep(2)

    results.append(run_flow(
        "FLOW 4 — KRG Demand (free-form, orchestrator route)",
        "KRG", free_form_question="Slowing ice cream — what should I do?",
    ))
    time.sleep(2)

    results.append(run_flow(
        "FLOW 5 — KRG Anomaly (broad scan)",
        "KRG", agent_id="anomaly", qid="anything_unusual",
    ))

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print(f"\n{'=' * 78}\nDEMO REHEARSAL SUMMARY\n{'=' * 78}")
    total_cost = sum(r["cost"] for r in results)
    n_pass = sum(1 for r in results if r["pass"])
    print(f"\n  Flows passed: {n_pass}/{len(results)}")
    print(f"  Total cost:   ${total_cost:.4f}")
    print()
    print(f"  {'flow':<55} {'pass':<5} {'turns':>5} {'time':>7} {'$':>7}")
    for r in results:
        print(f"  {r['label']:<55} "
              f"{'YES' if r['pass'] else 'NO':<5} "
              f"{r['turns']:>5} {r['elapsed']:>6.2f}s ${r['cost']:>6.4f}")
    return 0 if n_pass == len(results) else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
