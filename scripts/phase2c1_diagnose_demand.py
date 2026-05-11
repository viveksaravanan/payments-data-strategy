"""Diagnose the Demand "slowing ice cream" non-convergence.

Runs ONE call and prints the full tool-call sequence to determine whether
the non-convergence is the same pattern as Anomaly (prescriptive
walkthrough acting as a checklist) or something different.
"""
from __future__ import annotations

import os
import time

from src.agents.context import MerchantContext
from src.agents.demand import DemandForecastingSpecialist


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set.")
        return 1

    ctx = MerchantContext.for_merchant("KRG")
    spec = DemandForecastingSpecialist(ctx)
    question = "Slowing ice cream — what should I do?"

    print(f"\n=== DEMAND diagnosis — KRG ===\n")
    print(f"Q: {question}\n")
    t0 = time.monotonic()
    sr = spec.answer(question)
    elapsed = time.monotonic() - t0

    print(f"Turns:    {sr.turns}")
    print(f"Elapsed:  {elapsed:.2f}s")
    print(f"Cost:     ${sr.cost_usd:.4f}")
    print(f"Converged: {sr.converged}")
    print(f"\nSQL queries issued ({len(sr.sql)}):")
    for i, entry in enumerate(sr.sql, 1):
        print(f"\n  [{i}] tool={entry['tool']}  rows={entry.get('row_count', '?')}")
        print(f"      {entry['query']}")

    print(f"\n--- Prose (raw text from LLM) ---")
    print(sr.prose or "(empty)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
