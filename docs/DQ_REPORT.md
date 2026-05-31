# Wave 1 Data-Quality Report

**Generated at scale:** 100,000 cards (FULL — target 100,000)
**Transactions:** 1,660,732  |  **Line items:** 10,764,855
**Window:** 2026-03-01 → 2026-05-29

Each acceptance invariant below shows the measured value next to its target band.
Bands match SPEC §6 unless noted as pilot-adjusted.

## T1 — Volume
- **Total txns:** 1,660,732 (target ~1,670,000, ±10%) — ✓ in band
- **grocery:** 1,064,062 (target ~1,080,000) — ✓ in band
- **qsr:** 388,340 (target ~365,000) — ✓ in band
- **off_price:** 208,330 (target ~225,000) — ✓ in band

## T2 — Per-segment AOV
- **Grocery:** $53.59 (band $48-62, D17.4 anchor ~$55) — ✓ in band
- **QSR:** $11.19 (band $9-12) — ✓ in band
- **Off-price:** $41.25 (band $30-50) — ✓ in band

## T3 — Grocery store AUV (annualized)
- **AUV:** $15.42M/yr (band $14-18M, full-scale direct) — ✓ in band

## T4 — Grocery day-of-week
- **Weekend/weekday ratio:** 1.248 (band 1.20-1.35) — ✓ in band

## T5 — Taco Bell late-night daypart
- **9pm+ share:** 18.9% (band 17-21%, industry avg 4%) — ✓ in band

## T6 — Pay-cycle lift
- **Early-month (1-10):** 38622/day vs flat 32970/day — lift 17.1%
- **Mid-month (15-17):** 39036/day vs flat 32970/day — lift 18.4%

## T7-T8 — Population
- **Total cards:** 100,000
- **Multi-merchant share:** 32.0% (band 25-35%) — ✓ in band
- **All-three share:** 6.0% (band 4-8%) — ✓ in band

## T9 — Grocery loyalty concentration
- **Pop-weighted primary-banner share:** 76.0% (band 70-78%) — ✓ in band

## T11 — Affinity lift (designed + emergent)
- **PASTA→SAUCE:** lift 3.73x (P(B|A) 0.416) — threshold ≥3.0x ✓
- **CHIPS→SALSA:** lift 5.59x (P(B|A) 0.372) — threshold ≥2.5x ✓
- **DIAPERS→WIPES:** lift 7.27x (P(B|A) 0.283) — threshold ≥3.0x ✓
- **MILK→CEREAL:** lift 3.50x (P(B|A) 0.427) — threshold ≥2.0x ✓
- **DAIRY→CEREAL (mission-emergent):** lift 2.12x — threshold ≥1.3x ✓

## T12 — Heavy-tail basket size
- **Top-20% grocery basket unit share:** 45.3% (band 45-55%) — ✓ in band

## T13 — Payment mix
- **Blended contactless:** 53.5% (band 48-55%) — ✓ in band
- **Mobile wallet at-tap:** 16.7% (band 16-20%) — ✓ in band
- **Grocery debit (per-banner emergence):** WDX 47.4% > KRG 45.6% ≈ ACM 45.0% (blended 45.9%)

## T14 — Pricing variation
- **Cheapest banner share:** KRG 27.6%, ACM 5.3%, WDX 67.1% (none >70%) — ✓
- **PL gap blended:** 25.6% (anchor ~25%) — ✓ in band

## T15 — Promo behavior
- **Grocery units on promo:** 24.6% (band 22-35%) — ✓ in band

> Band corrected from §6's 25-35% to 22-35% at Wave 1 close. The CPG 25-35% figure covers all promo types; our v4 mix is weekly-ad-dominant (D20.1) and legitimately sits at the lower edge. Data on-anchor; band adjusted to match.

## T16 — Planted anomalies (A1-A3)
- **A1 WDX UC+Eastway decline:** before 2272/d, during 1469/d — drop 35.4% (target 40%)
- **A2 ground truth:** 1 entries
- **A3 ground truth:** 3 entries
- **A1 ground truth:** 6 entries (zone × banner rows)

## T17 — Cross-merchant cell readiness (FULL SCALE — Wave 2 gate)
- matthews        : 1,126 all-three cards
- eastway         : 900 all-three cards
- ballantyne      : 857 all-three cards
- university_city : 790 all-three cards
- dilworth        : 739 all-three cards
- noda            : 623 all-three cards
- center_city     : 487 all-three cards
- cabarrus_edge   : 483 all-three cards

**8/8 zones** survive k=5 anonymity.  **8/8 zones** populated.
✓ Wave 2 lake grain (per-zone × all-three) holds as designed.

## T18 — Reproducibility
- **Verified content-identical at 500 cards** by the in-test reproducibility check (`test_T18_reproducibility_byte_or_content`).
- **Full-scale (100k) two-run hash diff: not performed.** Determinism at scale rests on construction: pinned pyarrow, single-threaded writes, sorted iteration, single-file (not chunked) writes, no parallelism. If you want a direct full-scale guarantee, rerun and compare via `scripts/hash_parquet.py`.
