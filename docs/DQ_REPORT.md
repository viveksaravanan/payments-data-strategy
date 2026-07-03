# Data-Quality Report — datamodel-v2

**Scale:** 155,000 cards (FULL — target 155,000)
**Transactions:** 4,146,446  |  **Line items:** 27,000,515
**Window:** 2026-03-01 → 2026-05-29

Grocery (KRG/ACM/WDX) + QSR (TBL/BKG/CFA); 38 stores; flat shelf-price; promotions + anomalies dormant. Measured value shown next to each band; targets trace to §A9/§B7/§C9/Appendix D.

## T1 — Volume
- **Total txns:** 4,146,446 (target ~4,430,000) — ✓ in band
- **grocery:** 2,379,105 (target ~2,670,000) — ✓ in band
- **qsr:** 1,767,341 (target ~1,760,000) — ✓ in band

## T2 — Basket / check
- **Grocery AOV:** $54.31 (band $45-60, §A9.5) — ✓ in band
- **QSR check (blended):** $11.17 (band $8-14) — ✓ in band
- **QSR check ordering:** CFA $13.43 > BK $10.01 > TB $8.54 (§B2) — ✓

## T3 — Per-store AUV (annualized, scale-adjusted) & merchant split
- **KRG AUV:** $44.9M/yr (target ~$45M) — ✓ in band
- **ACM AUV:** $32.9M/yr (target ~$32M) — ✓ in band
- **WDX AUV:** $22.6M/yr (target ~$22M) — ✓ in band
- **CFA AUV:** $7.7M/yr (target ~$8.0M) — ✓ in band
- **TBL AUV:** $2.2M/yr (target ~$2.1M) — ✓ in band
- **BKG AUV:** $1.7M/yr (target ~$1.6M) — ✓ in band
- **Grocery $ split:** KRG 51.4 / ACM 31.4 / WDX 17.2 (target 52/31/17) — ✓
- **CFA vs TB+BK:** CFA $11,416,529 vs $8,323,244 = 1.37× — ✓ CFA out-earns

## §A4 — Department sales mix (grocery $)
- **Center-store (Dry+Snacks+Bev):** 38.8% (target ~38) — ✓ in band
- **Meat & Seafood:** 13.7% (target ~13) — ✓ in band
- **Produce:** 11.8% (target ~11) — ✓ in band
- **Dairy & Eggs:** 8.1% (target ~8) — ✓ in band

## §A12 — Private-label share (measured from basket selection)
- **KRG:** 27.6% (target ~27) — ✓ in band
- **ACM:** 19.3% (target ~19) — ✓ in band
- **WDX:** 26.3% (target ~25) — ✓ in band
- Ordering KRG > WDX > ACM (real-chain PL-program strength × affluence selection).

## §A13 — Fresh/premium mix rises with affluence
- **Fresh $ share low→mid→high:** 32.8 → 36.6 → 42.1% — ✓ smooth monotonic gradient

## T4/T5 — Timing & dayparts
- **Grocery weekend/weekday ratio:** 1.253 (band 1.15-1.40) — ✓ in band
- **CFA Sunday txns:** 0 — ✓ hard zero (closed)
- **TBL late-night (9pm+):** 18.9% (band 15-23) — ✓ in band
- **BKG breakfast (6-10am):** 20.0% (band 12-28) — ✓ in band

## T7/T8 — Population & participation
- **Cards:** 155,000
- **Both-segment share:** 46.1% (target ~46) — ✓ in band
- **Grocery-active:** 82.0% (~82) | **QSR-active:** 64.1% (~64)
- **T9 loyalty concentration:** 78.6% (band 68-82) — ✓ in band

## T11 — Affinity lift + QSR combo-attach
- **Pasta→Pasta Sauce:** 2.39× — ✓ (≥1.8)
- **Cereal→2% Reduced-Fat Milk:** 2.53× — ✓ (≥1.8)
- **Potato & Tortilla Chips→Salsa & Dips:** 2.73× — ✓ (≥1.8)
- **QSR drink|entrée attach:** CFA 0.69 > BK 0.63 > TB 0.56 — ✓

## T12 — Heavy-tail basket
- **Top-20% grocery basket unit share:** 41.9% (band 40-60) — ✓ in band

## T13 — Payment mix
- **Contactless:** 56.0% (band 45-60) — ✓ in band
- **Wallet-at-tap:** 17.4% (band 13-22) — ✓ in band
- **Grocery debit WDX 47.0% > ACM 45.5%:** ✓

## T14 — Pricing (flat shelf-price)
- **Flat pricing (unit_price == shelf_price):** 0 mismatches — ✓
- **No banner cheapest >70%:** KRG 25%, ACM 8%, WDX 68% — ✓

## T15/T16 — Promotions & anomalies (DORMANT)
- **Promotions rows:** 0 — ✓ dormant
- **Anomalies_groundtruth rows:** 0 — ✓ dormant
- Framework kept dormant (Decision B); returns in a later anomaly wave.

## T17 — Both-segment cell readiness (FULL)
- matthews        : 13,360 both-segment cards
- eastway         : 11,460 both-segment cards
- ballantyne      : 10,137 both-segment cards
- university_city : 9,244 both-segment cards
- dilworth        : 8,729 both-segment cards
- noda            : 7,076 both-segment cards
- center_city     : 5,737 both-segment cards
- cabarrus_edge   : 5,653 both-segment cards
**8/8 zones** survive k=50; **8/8** populated

## T18 — Reproducibility
- Verified content-identical (transactions + transaction_items) across two `build_all(scale=500)` runs by `test_T18_reproducibility_content_identical`.
- Catalog authoring (`make catalog`) is byte-identical on rebuild (hash/index-derived, no RNG).

## Totals (annualized, full-population projection)
- **Grocery:** $524M/yr (target ~$518M)  |  **QSR:** $80M/yr (target ~$80M)
- Window totals scale from the FULL sample.
