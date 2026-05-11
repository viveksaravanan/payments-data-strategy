"""Phase 2A.5 validation — measure latency, cost, and turn count for
the Pricing Specialist after the 5 optimizations.

Targets (from the optimization plan):
  - <$0.06 cost / question
  - <15s latency / fresh question (ideally 10-12s)
  - average ~3 turns / question (down from ~5)

Runs the same 5-sample pricing matrix used in Phase 2A and prints a
side-by-side comparison vs the Phase 2A baseline.
"""
from __future__ import annotations

import os
import time

from src.agents.context import MerchantContext
from src.agents.pricing import PricingSpecialist
from src.dashboard import placeholders as P

import sys

ALL_SAMPLES = [
    ("KRG", "dairy_vs_peers"),
    ("ACM", "above_market"),
    ("WDX", "below_market"),
    ("TBL", "price_check_qsr"),
    ("TJX", "above_market_tjx"),
]
# Allow `python -m scripts.phase2a5_validate 2` to run only first 2.
n_arg = int(sys.argv[1]) if len(sys.argv) > 1 else len(ALL_SAMPLES)
SAMPLES = ALL_SAMPLES[:n_arg]


def run_one(merchant_id: str, question_id: str) -> dict:
    ctx = MerchantContext.for_merchant(merchant_id)
    spec = PricingSpecialist(ctx)
    question = P._question_text("pricing", question_id, merchant_id)
    t0 = time.monotonic()
    resp = spec.answer(question)
    elapsed = time.monotonic() - t0
    return {
        "viewer":   merchant_id,
        "question": question_id,
        "turns":    resp.turns,
        "elapsed":  round(elapsed, 2),
        "in_tok":   resp.input_tokens,
        "out_tok":  resp.output_tokens,
        "cost":     round(resp.cost_usd, 4),
        "converged": resp.converged,
    }


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set; skipping live validation.")
        return 0

    print(f"\nPhase 2A.5 validation — running {len(SAMPLES)} samples\n")
    print(f"{'viewer':<6} {'question':<22} {'turns':>5} {'elapsed':>8} "
          f"{'in_tok':>7} {'out_tok':>7} {'cost':>7}  converged")
    print("-" * 78)
    rows = []
    for mid, qid in SAMPLES:
        r = run_one(mid, qid)
        rows.append(r)
        print(f"{r['viewer']:<6} {r['question']:<22} {r['turns']:>5} "
              f"{r['elapsed']:>7.2f}s {r['in_tok']:>7} {r['out_tok']:>7} "
              f"${r['cost']:>6.4f}  {r['converged']}")
        time.sleep(2)  # gentle on the rate limiter

    n = len(rows)
    avg_turns   = sum(r["turns"]    for r in rows) / n
    avg_elapsed = sum(r["elapsed"]  for r in rows) / n
    avg_cost    = sum(r["cost"]     for r in rows) / n
    total_cost  = sum(r["cost"]     for r in rows)
    all_conv    = all(r["converged"] for r in rows)

    print("-" * 78)
    print(f"\nAverages:")
    print(f"  turns:    {avg_turns:.2f}  (target ~3, baseline ~5)")
    print(f"  latency:  {avg_elapsed:.2f}s  (target <15s, baseline ~42s)")
    print(f"  cost/q:   ${avg_cost:.4f}  (target <$0.06, baseline ~$0.10)")
    print(f"  total:    ${total_cost:.4f} for {n} samples")
    print(f"  converged: {all_conv}")

    print("\nPass/fail vs targets:")
    print(f"  turns ~3:        {'PASS' if avg_turns <= 3.5 else 'FAIL'}")
    print(f"  latency <15s:    {'PASS' if avg_elapsed < 15 else 'FAIL'}")
    print(f"  cost <$0.06/q:   {'PASS' if avg_cost < 0.06 else 'FAIL'}")
    print(f"  all converged:   {'PASS' if all_conv else 'FAIL'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
