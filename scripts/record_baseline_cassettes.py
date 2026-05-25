"""Record the 12 Phase 5 baseline cassettes.

Live API call per cassette — expect ~$0.10–0.30 total spend at
Haiku 4.5 rates. Wall clock ~3–10 minutes depending on tool-loop
depths.

Usage::

    uv run python scripts/record_baseline_cassettes.py

Requires ``ANTHROPIC_API_KEY`` in the environment (or in ``.env``).
The script aborts before making any API calls if the key is missing.

Failure mode: each cassette is recorded independently. If a single
specialist errors, the script logs the failure and continues to the
next cassette. The final summary lists any failures so the user can
re-run just the failing combinations.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

# Allow ``python scripts/record_baseline_cassettes.py`` (no ``-m``).
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

from src.agents import llm as L  # noqa: E402
from tests.cassette_helpers import record_cassette  # noqa: E402


# The 12 baseline combinations. Format: (specialist, qid, merchant_id).
CASSETTES: list[tuple[str, str, str]] = [
    # Pricing — 2 grocer questions + 1 retail
    ("pricing", "P1",   "KRG"),
    ("pricing", "P3",   "KRG"),
    ("pricing", "R-P2", "TJX"),
    # Demand — grocer + QSR + retail
    ("demand",  "D3",   "KRG"),
    ("demand",  "T-D2", "TBL"),
    ("demand",  "R-D2", "TJX"),
    # Anomaly — grocer × 2 + QSR
    ("anomaly", "A2",   "KRG"),
    ("anomaly", "A3",   "KRG"),
    ("anomaly", "T-A2", "TBL"),
    # Trade — three grocer questions covering all 3 trade patterns
    ("trade",   "T1",   "KRG"),
    ("trade",   "T2",   "KRG"),
    ("trade",   "T4",   "KRG"),
]


def main() -> int:
    if not L.is_available():
        print("ERROR: ANTHROPIC_API_KEY not set. Add it to .env first.", file=sys.stderr)
        return 1

    print(f"Recording {len(CASSETTES)} baseline cassettes against current prompts.")
    print(f"Model: {L.MODEL_SPECIALIST}")
    print(f"Output: tests/cassettes/baseline/\n")

    total_cost = 0.0
    failures: list[tuple[tuple[str, str, str], str]] = []
    started = time.time()

    for i, combo in enumerate(CASSETTES, start=1):
        specialist, qid, merchant_id = combo
        label = f"{specialist:8s} {qid:5s} {merchant_id:3s}"
        print(f"  [{i:2d}/{len(CASSETTES)}] {label} ... ", end="", flush=True)
        t0 = time.time()
        try:
            path = record_cassette(specialist, qid, merchant_id)
        except Exception as exc:  # noqa: BLE001
            failures.append((combo, repr(exc)))
            print(f"FAILED ({type(exc).__name__}: {exc})")
            continue

        # Read back the cost we just wrote to roll up the spend total.
        import json
        cassette = json.loads(path.read_text())
        cost = float(cassette["response_dict"]["telemetry"].get("cost_usd", 0.0) or 0.0)
        turns = cassette["response_dict"]["telemetry"].get("turns", 0)
        total_cost += cost
        print(f"OK ({turns} turns, ${cost:.4f}, {time.time() - t0:.1f}s)")

    elapsed = time.time() - started
    print()
    print(f"Done. Recorded {len(CASSETTES) - len(failures)}/{len(CASSETTES)} cassettes")
    print(f"      in {elapsed:.1f}s. Total API spend: ${total_cost:.4f}.")
    if failures:
        print()
        print("Failures (re-run by editing CASSETTES to just this list):")
        for combo, err in failures:
            print(f"  - {combo}: {err}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
