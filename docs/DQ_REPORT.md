# Wave 1 Data-Quality Report

**Generated at scale:** 5,000 cards (5.0% of full target)
**Transactions:** 82,757  |  **Line items:** 539,640
**Window:** 2026-03-01 → 2026-05-29

Each acceptance invariant below shows the measured value next to its target band.
Bands match SPEC §6 unless noted as pilot-adjusted.

## T1 — Volume
- **Total txns:** 82,757 (target ~83,500, ±10%) — ✓ in band
- **grocery:** 53,674 (target ~54,000) — ✓ in band
- **qsr:** 18,725 (target ~18,250) — ✓ in band
- **off_price:** 10,358 (target ~11,250) — ✓ in band

## T2 — Per-segment AOV
- **Grocery:** $53.64 (band $48-62, D17.4 anchor ~$55) — ✓ in band
- **QSR:** $11.16 (band $9-12) — ✓ in band
- **Off-price:** $41.06 (band $30-50) — ✓ in band

## T3 — Grocery store AUV (annualized)
- **AUV-equivalent:** $15.57M/yr (band $14-18M) — ✓ in band

## T4 — Grocery day-of-week
- **Weekend/weekday ratio:** 1.263 (band 1.20-1.35) — ✓ in band

## T5 — Taco Bell late-night daypart
- **9pm+ share:** 18.7% (band 17-21%, industry avg 4%) — ✓ in band

## T6 — Pay-cycle lift
- **Early-month (1-10):** 1973/day vs flat 1649/day — lift 19.7%
- **Mid-month (15-17):** 1933/day vs flat 1649/day — lift 17.2%

## T7-T8 — Population
- **Total cards:** 5,000
- **Multi-merchant share:** 31.3% (band 25-35%) — ✓ in band
- **All-three share:** 6.0% (band 4-8%) — ✓ in band

## T9 — Grocery loyalty concentration
- **Pop-weighted primary-banner share:** 75.5% (band 70-78%) — ✓ in band

## T11 — Affinity lift (designed + emergent)
- **PASTA→SAUCE:** lift 3.70x (P(B|A) 0.421) — threshold ≥3.0x ✓
- **CHIPS→SALSA:** lift 5.65x (P(B|A) 0.381) — threshold ≥2.5x ✓
- **DIAPERS→WIPES:** lift 7.18x (P(B|A) 0.281) — threshold ≥3.0x ✓
- **MILK→CEREAL:** lift 3.45x (P(B|A) 0.424) — threshold ≥2.0x ✓
- **DAIRY→CEREAL (mission-emergent):** lift 2.09x — threshold ≥1.3x ✓

## T12 — Heavy-tail basket size
- **Top-20% grocery basket unit share:** 45.3% (band 45-55%) — ✓ in band

## T13 — Payment mix
- **Blended contactless:** 53.0% (band 48-55%) — ✓ in band
- **Mobile wallet at-tap:** 16.3% (band 16-20%) — ✓ in band
- **Grocery debit (per-banner emergence):** WDX 48.4% > KRG 45.5% ≈ ACM 45.0% (blended 46.1%)

## T14 — Pricing variation
- **Cheapest banner share:** KRG 27.6%, ACM 5.3%, WDX 67.1% (none >70%) — ✓
- **PL gap blended:** 25.6% (anchor ~25%) — ✓ in band

## T15 — Promo behavior
- **Grocery units on promo:** 24.6% (band 25-35%) — ⚠ below band

## T16 — Planted anomalies (A1-A3)
- **A1 WDX UC+Eastway decline:** before 113/d, during 72/d — drop 36.4% (target 40%)
- **A2 ground truth:** 1 entries
- **A3 ground truth:** 3 entries
- **A1 ground truth:** 6 entries (zone × banner rows)

## T17 — Cross-merchant cell readiness (pilot)
- matthews        : 54 all-three cards
- center_city     : 42 all-three cards
- eastway         : 41 all-three cards
- ballantyne      : 38 all-three cards
- noda            : 37 all-three cards
- university_city : 36 all-three cards
- dilworth        : 30 all-three cards
- cabarrus_edge   : 22 all-three cards

At pilot (5%), 8/8 zones host all-three cards. Full-scale will multiply ~20×.

## T18 — Reproducibility
- **Two engine runs at seed=42 produce content-identical Parquet** (Stage 2 deterministic-write conventions hold).
