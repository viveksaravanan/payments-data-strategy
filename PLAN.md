# Plan

**Status:** v3 in progress. Phase 1.5 (foundation hardening) completed;
Phase 2 (data verification) and Phase 3 (polish) ahead.

The build plan that lived here through v2 has been archived to
`docs/archive/PLAN_v2.md`. It described a different system (three
merchants, EBT-at-Kroger, separate `src/anonymize/` stage, Network
Analyst agent) and was superseded by the v2.5 refactor; consult it
only for git-archaeology context.

## Where to read next

- `V3_VISION.md` — the rubric for everything v3. Thesis, the three
  rubric tests every artifact passes, the agent's confidence
  posture, and the gold-standard demo beat. **Start here.**
- `V3_AUDIT.md` — Phase 1 audit of the codebase against the v3
  rubric. Findings, recommendations, and the six locked decisions
  Phase 1.5 implemented.
- `V2_5_DATA_DESIGN.md` — locked source of truth for the data layer
  (panel, schema, generator algorithm, lake design, planted
  anomalies). The data layer hasn't moved between v2.5 and v3;
  this doc is still authoritative.
- `ARCHITECTURE.md` — mapping from the Core Data Strategy document
  to what's implemented vs. deferred.
- `DATA.md` — synthetic data specification (output side).
- `README.md` — top-level overview for first-time readers.
- `docs/archive/PLAN_v2.md` — the historical v2 build plan, kept
  for context.
