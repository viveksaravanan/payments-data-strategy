# Phase 5 regression summary

Generated: 2026-05-26T00:56:42Z
Phase 5 commit: `12ea5fb`
Baseline phase: 4.6 (cassettes recorded against commit `480c590`)
Total wall clock: 286.6s
Total API spend (phase5 re-run): $0.5710

## Contract compliance (mechanical checks)

| Cassette | Headline # | 3-5 bullets | Therefore | Approved opener | No forbidden verbs | Caveats parsed | No throat-clearing |
|---|---|---|---|---|---|---|---|
| anomaly_A2_KRG | ❌ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| anomaly_A3_KRG | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| anomaly_T-A2_TBL | ✅ | ❌ | ✅ | ✅ | ✅ | ✅ | ✅ |
| demand_D3_KRG | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| demand_R-D2_TJX | ❌ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |
| demand_T-D2_TBL | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| pricing_P1_KRG | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| pricing_P3_KRG | ❌ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| pricing_R-P2_TJX | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| trade_T1_KRG | ❌ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| trade_T2_KRG | ❌ | ✅ | ✅ | ❌ | ✅ | ✅ | ✅ |
| trade_T4_KRG | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

**Full-pass: 6/12 cassettes pass all 7 checks.**

## Cost comparison

- Total Phase 5 cost: **$0.5710**
- Total baseline cost: **$0.5148**
- Delta: **+10.9%**

Per-cassette cost deltas:

| Cassette | Baseline | Phase 5 | Δ |
|---|---:|---:|---:|
| anomaly_A2_KRG | $0.0506 | $0.0649 | +28% |
| anomaly_A3_KRG | $0.0669 | $0.0678 | +1% |
| anomaly_T-A2_TBL | $0.0535 | $0.0704 | +32% |
| demand_D3_KRG | $0.0387 | $0.1025 | +165% |
| demand_R-D2_TJX | $0.0574 | $0.0153 | -73% |
| demand_T-D2_TBL | $0.0324 | $0.0373 | +15% |
| pricing_P1_KRG | $0.0413 | $0.0377 | -9% |
| pricing_P3_KRG | $0.0419 | $0.0304 | -27% |
| pricing_R-P2_TJX | $0.0310 | $0.0280 | -10% |
| trade_T1_KRG | $0.0510 | $0.0354 | -31% |
| trade_T2_KRG | $0.0348 | $0.0373 | +7% |
| trade_T4_KRG | $0.0152 | $0.0441 | +190% |

## Turn count comparison

- Total Phase 5 turns: **60**
- Total baseline turns: **62**

Per-cassette turn deltas:

| Cassette | Baseline | Phase 5 | Δ |
|---|---:|---:|---:|
| anomaly_A2_KRG | 6 | 6 | +0 |
| anomaly_A3_KRG | 7 | 6 | -1 |
| anomaly_T-A2_TBL | 6 | 6 | +0 |
| demand_D3_KRG | 6 | 10 | +4 |
| demand_R-D2_TJX | 6 | 2 | -4 |
| demand_T-D2_TBL | 4 | 4 | +0 |
| pricing_P1_KRG | 5 | 5 | +0 |
| pricing_P3_KRG | 5 | 4 | -1 |
| pricing_R-P2_TJX | 4 | 4 | +0 |
| trade_T1_KRG | 6 | 4 | -2 |
| trade_T2_KRG | 4 | 4 | +0 |
| trade_T4_KRG | 3 | 5 | +2 |

## Convergence

- Phase 5: **12/12** cassettes converged

## Next step

Grade each comparison file manually:

1. Open `tests/cassettes/comparisons/<cassette>.json`
2. Compare `baseline_response.prose` vs `phase5_response.prose`
3. Set the `grade` field to `"better"`, `"equal"`, or `"worse"`
4. Optionally add notes

Pass criterion (revised after Phase 5.3 deferral):

- ≥ 4-6 cassettes graded `better`
- ≥ 6-8 cassettes graded `equal`
- 0 cassettes graded `worse`
