# Plan

**Status:** v3 shipped. Phases 1.5 through 6 complete — the agent
layer is now an orchestrator routing to four specialists (pricing,
anomaly, demand, trade) over the Headline → Evidence → Therefore →
Caveats response contract, the dashboard has filter wiring and the
fixed-overlay chat panel, and the synthetic data layer is unchanged
from v2.5. The full audit trail with phase close-outs, regression
results, and deferred v4 items lives in `V3_AUDIT.md`.

The build plan that lived here through v2 has been archived to
`docs/archive/PLAN_v2.md`. It described a different system (three
merchants, EBT-at-Kroger, separate `src/anonymize/` stage, Network
Analyst agent) and was superseded by the v2.5 refactor; consult it
only for git-archaeology context.

## Where to read next

- `V3_VISION.md` — the rubric for everything v3. Thesis, the three
  rubric tests every artifact passes, the agent's confidence
  posture, and the gold-standard demo beat. **Start here.**
- `V3_AUDIT.md` — phase-by-phase audit and close-out log for v3
  (foundation hardening through ship). Findings, locked decisions,
  regression results, deferred v4 items.
- `docs/V3_AGENTS_DESIGN.md` — locked agent-layer spec
  (orchestrator + 4 specialists, response contract, MAX_TURNS=10,
  segment-conditional routing).
- `V2_5_DATA_DESIGN.md` — locked source of truth for the data layer
  (panel, schema, generator algorithm, lake design, planted
  anomalies). The data layer hasn't moved between v2.5 and v3;
  this doc is still authoritative.
- `ARCHITECTURE.md` — mapping from the Core Data Strategy document
  to what's implemented vs. deferred.
- `DATA.md` — synthetic data specification (output side).
- `README.md` — top-level overview for first-time readers.
- `docs/archive/` — historical plans (v2 build plan, v3 iteration
  drafts, completed phase artifacts), kept for context.
