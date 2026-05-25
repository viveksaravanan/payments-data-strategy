# Agent regression cassettes

Captures of agent responses used by Phase 5 to compare baseline
(current prompts) vs. new prompts. Without these baselines, "is
the new response better?" becomes vibes-based.

## Layout

```
tests/cassettes/
├── baseline/                       # one file per (specialist, qid, merchant)
│   ├── pricing_P1_KRG.json
│   ├── pricing_P3_KRG.json
│   ├── pricing_R-P2_TJX.json
│   ├── demand_D3_KRG.json
│   ├── demand_T-D2_TBL.json
│   ├── demand_R-D2_TJX.json
│   ├── anomaly_A2_KRG.json
│   ├── anomaly_A3_KRG.json
│   ├── anomaly_T-A2_TBL.json
│   ├── trade_T1_KRG.json
│   ├── trade_T2_KRG.json
│   └── trade_T4_KRG.json
└── comparisons/                    # populated by Phase 5.5 regression run
    └── .gitkeep
```

## Coverage rationale (12 baselines)

| Specialist | Coverage |
|---|---|
| Pricing | 2 grocer questions (heatmap + scatter) + 1 TJX (no-peer fallback for retail) |
| Demand | 1 grocer + 1 TBL (QSR) + 1 TJX (retail) |
| Anomaly | 2 grocer (store + category tables) + 1 TBL (SKU-level) |
| Trade | 3 grocer questions (T1 / T2 / T4 — all three trade patterns) |

## Baseline format

`baseline/{specialist}_{qid}_{merchant}.json`:

```json
{
  "qid": "P1",
  "specialist": "pricing",
  "merchant_id": "KRG",
  "question": "How do my prices compare to peer grocers across categories?",
  "tool_calls": [
    {"tool": "tenant", "query": "SELECT ...", "row_count": 12},
    {"tool": "lake",   "query": "SELECT ...", "row_count": 24}
  ],
  "response_dict": {
    "agent":     "Pricing & Benchmarking Agent",
    "prose":     "<verbatim agent output>",
    "caveats":   ["..."],
    "telemetry": {
      "turns": 3,
      "input_tokens": 12000,
      "output_tokens": 800,
      "cost_usd": 0.005,
      "converged": true
    }
  },
  "recorded_at":    "2026-05-24T15:00:00Z",
  "phase_baseline": "4.6",
  "commit_sha":     "c75642f"
}
```

* `prose` is the verbatim user-visible string with caveats already
  stripped (the existing `_split_caveats` regex extracts caveats
  before the specialist returns).
* `tool_calls` mirrors `SpecialistResponse.sql` — best-effort log of
  the `query_tenant` / `query_lake` invocations the specialist made.
  Helpers like `make_chart` don't surface a SQL log, so they're
  invisible here.
* `table` and `chart` fields are NOT persisted — they aren't
  JSON-serializable and can be regenerated from `tool_calls` if
  needed.

## Comparison format (Phase 5.5 populates)

`comparisons/{specialist}_{qid}_{merchant}.json`:

```json
{
  "qid": "P1",
  "specialist": "pricing",
  "merchant_id": "KRG",
  "question": "...",
  "baseline_response": {
    "prose":   "...",
    "caveats": [...]
  },
  "phase5_response": {
    "headline":  "...",
    "evidence":  [...],
    "therefore": "...",
    "caveats":   [...]
  },
  "grade": null,
  "notes": ""
}
```

The reviewer fills `grade` (`"better"` / `"equal"` / `"worse"`) and
optional `notes` during Phase 5.5.

## Regenerating

```bash
uv run python scripts/record_baseline_cassettes.py
```

Requires `ANTHROPIC_API_KEY` in the environment (or `.env`).
Recording 12 cassettes costs roughly $0.10–0.30 in API spend at
Haiku 4.5 rates; expect ~3–10 minutes wall-clock depending on
specialist turn budgets.

## When to regenerate

- After Phase 4.6-era prompt changes land that affect baseline
  responses
- Before kicking off Phase 5.1+ work (baseline must reflect the
  pre-change state)
- **Never during Phase 5** — the baseline is the comparison point

## Schema versioning

Each cassette stamps `phase_baseline` (currently `"4.6"`) so
Phase 5.5 can verify it's comparing against the right snapshot.
Bumping this value is a breaking change.
