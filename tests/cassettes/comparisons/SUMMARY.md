# Phase 5 regression summary

Generated: 2026-05-25T23:15:48Z
Phase 5 commit: `8bf021b`
Baseline phase: 4.6 (cassettes recorded against commit `480c590`)
Total wall clock: 410.5s
Total API spend (phase5 re-run): $0.9078

## Contract compliance (mechanical checks)

| Cassette | Headline # | 3-5 bullets | Therefore | Approved opener | No forbidden verbs | Caveats parsed | No throat-clearing |
|---|---|---|---|---|---|---|---|
| anomaly_A2_KRG | ✅ | ❌ | ❌ | ❌ | ✅ | ✅ | ✅ |
| anomaly_A3_KRG | ✅ | ✅ | ✅ | ❌ | ✅ | ✅ | ❌ |
| anomaly_T-A2_TBL | ✅ | ❌ | ❌ | ❌ | ✅ | ✅ | ✅ |
| demand_D3_KRG | ✅ | ❌ | ❌ | ❌ | ✅ | ✅ | ✅ |
| demand_R-D2_TJX | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |
| demand_T-D2_TBL | ✅ | ❌ | ✅ | ✅ | ✅ | ✅ | ❌ |
| pricing_P1_KRG | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| pricing_P3_KRG | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| pricing_R-P2_TJX | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| trade_T1_KRG | ❌ | ❌ | ✅ | ✅ | ✅ | ✅ | ❌ |
| trade_T2_KRG | ✅ | ❌ | ❌ | ❌ | ✅ | ✅ | ✅ |
| trade_T4_KRG | ❌ | ❌ | ✅ | ✅ | ✅ | ✅ | ❌ |

**Full-pass: 3/12 cassettes pass all 7 checks.**

## Cost comparison

- Total Phase 5 cost: **$0.9078**
- Total baseline cost: **$0.5148**
- Delta: **+76.3%**

Per-cassette cost deltas:

| Cassette | Baseline | Phase 5 | Δ |
|---|---:|---:|---:|
| anomaly_A2_KRG | $0.0506 | $0.1356 | +168% |
| anomaly_A3_KRG | $0.0669 | $0.1361 | +103% |
| anomaly_T-A2_TBL | $0.0535 | $0.1315 | +146% |
| demand_D3_KRG | $0.0387 | $0.1123 | +191% |
| demand_R-D2_TJX | $0.0574 | $0.0183 | -68% |
| demand_T-D2_TBL | $0.0324 | $0.0182 | -44% |
| pricing_P1_KRG | $0.0413 | $0.0344 | -17% |
| pricing_P3_KRG | $0.0419 | $0.0421 | +0% |
| pricing_R-P2_TJX | $0.0310 | $0.0239 | -23% |
| trade_T1_KRG | $0.0510 | $0.0962 | +89% |
| trade_T2_KRG | $0.0348 | $0.1024 | +194% |
| trade_T4_KRG | $0.0152 | $0.0568 | +274% |

## Turn count comparison

- Total Phase 5 turns: **82**
- Total baseline turns: **62**

Per-cassette turn deltas:

| Cassette | Baseline | Phase 5 | Δ |
|---|---:|---:|---:|
| anomaly_A2_KRG | 6 | 10 | +4 |
| anomaly_A3_KRG | 7 | 10 | +3 |
| anomaly_T-A2_TBL | 6 | 10 | +4 |
| demand_D3_KRG | 6 | 10 | +4 |
| demand_R-D2_TJX | 6 | 2 | -4 |
| demand_T-D2_TBL | 4 | 2 | -2 |
| pricing_P1_KRG | 5 | 4 | -1 |
| pricing_P3_KRG | 5 | 5 | +0 |
| pricing_R-P2_TJX | 4 | 3 | -1 |
| trade_T1_KRG | 6 | 9 | +3 |
| trade_T2_KRG | 4 | 10 | +6 |
| trade_T4_KRG | 3 | 7 | +4 |

## Convergence

- Phase 5: **8/12** cassettes converged
- Non-converged (hit MAX_TURNS): anomaly_A2_KRG, anomaly_T-A2_TBL, demand_D3_KRG, trade_T2_KRG

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
