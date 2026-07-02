# Data-Quality Report — datamodel-v2

**Scale:** 5,000 cards (3.2% pilot — target 155,000)
**Transactions:** 132,849  |  **Line items:** 866,736
**Window:** 2026-03-01 → 2026-05-29

Grocery (KRG/ACM/WDX) + QSR (TBL/BKG/CFA); 38 stores; flat shelf-price; promotions + anomalies dormant. Measured value shown next to each band; targets trace to §A9/§B7/§C9/Appendix D.

## T1 — Volume
- **Total txns:** 132,849 (target ~142,903) — ✓ in band
- **grocery:** 76,750 (target ~86,129) — ✓ in band
- **qsr:** 56,099 (target ~56,774) — ✓ in band

## T2 — Basket / check
- **Grocery AOV:** $54.29 (band $45-60, §A9.5) — ✓ in band
- **QSR check (blended):** $11.22 (band $8-14) — ✓ in band
- **QSR check ordering:** CFA $13.55 > BK $9.91 > TB $8.52 (§B2) — ✓

## T3 — Per-store AUV (annualized, scale-adjusted) & merchant split
- **KRG AUV:** $44.3M/yr (target ~$45M) — ✓ in band
- **ACM AUV:** $34.1M/yr (target ~$32M) — ✓ in band
- **WDX AUV:** $21.9M/yr (target ~$22M) — ✓ in band
- **CFA AUV:** $7.8M/yr (target ~$8.0M) — ✓ in band
- **TBL AUV:** $2.2M/yr (target ~$2.1M) — ✓ in band
- **BKG AUV:** $1.6M/yr (target ~$1.6M) — ✓ in band
- **Grocery $ split:** KRG 50.7 / ACM 32.5 / WDX 16.7 (target 52/31/17) — ✓
- **CFA vs TB+BK:** CFA $370,298 vs $259,383 = 1.43× — ✓ CFA out-earns

## §A4 — Department sales mix (grocery $)
- **Center-store (Dry+Snacks+Bev):** 38.8% (target ~38) — ✓ in band
- **Meat & Seafood:** 13.7% (target ~13) — ✓ in band
- **Produce:** 11.7% (target ~11) — ✓ in band
- **Dairy & Eggs:** 8.1% (target ~8) — ✓ in band

## §A12 — Private-label share (measured from basket selection)
- **KRG:** 27.7% (target ~27) — ✓ in band
- **ACM:** 19.8% (target ~19) — ✓ in band
- **WDX:** 26.2% (target ~25) — ✓ in band
- Ordering KRG > WDX > ACM (real-chain PL-program strength × affluence selection).

## §A13 — Fresh/premium mix rises with affluence
- **Fresh $ share low→mid→high:** 32.8 → 36.5 → 42.0% — ✓ smooth monotonic gradient

## T4/T5 — Timing & dayparts
- **Grocery weekend/weekday ratio:** 1.247 (band 1.15-1.40) — ✓ in band
- **CFA Sunday txns:** 0 — ✓ hard zero (closed)
- **TBL late-night (9pm+):** 18.8% (band 15-23) — ✓ in band
- **BKG breakfast (6-10am):** 20.3% (band 12-28) — ✓ in band

## T7/T8 — Population & participation
- **Cards:** 5,000
- **Both-segment share:** 44.9% (target ~46) — ✓ in band
- **Grocery-active:** 81.7% (~82) | **QSR-active:** 63.2% (~64)
- **T9 loyalty concentration:** 78.4% (band 68-82) — ✓ in band

## T11 — Affinity lift + QSR combo-attach
- **Pasta→Pasta Sauce:** 2.40× — ✓ (≥1.8)
- **Cereal→2% Reduced-Fat Milk:** 2.51× — ✓ (≥1.8)
- **Potato & Tortilla Chips→Salsa & Dips:** 2.73× — ✓ (≥1.8)
- **QSR drink|entrée attach:** CFA 0.69 > BK 0.61 > TB 0.56 — ✓

## T12 — Heavy-tail basket
- **Top-20% grocery basket unit share:** 41.9% (band 40-60) — ✓ in band

## T13 — Payment mix
- **Contactless:** 55.8% (band 45-60) — ✓ in band
- **Wallet-at-tap:** 17.5% (band 13-22) — ✓ in band
- **Grocery debit WDX 49.3% > ACM 45.6%:** ✓

## T14 — Pricing (flat shelf-price)
- **Flat pricing (unit_price == shelf_price):** 0 mismatches — ✓
- **No banner cheapest >70%:** KRG 25%, ACM 8%, WDX 68% — ✓

## T15/T16 — Promotions & anomalies (DORMANT)
- **Promotions rows:** 0 — ✓ dormant
- **Anomalies_groundtruth rows:** 0 — ✓ dormant
- Framework kept dormant (Decision B); returns in a later anomaly wave.

## T17 — Both-segment cell readiness (3.2% pilot)
- matthews        : 441 both-segment cards
- eastway         : 357 both-segment cards
- ballantyne      : 294 both-segment cards
- university_city : 273 both-segment cards
- dilworth        : 263 both-segment cards
- noda            : 231 both-segment cards
- center_city     : 208 both-segment cards
- cabarrus_edge   : 179 both-segment cards
**8/8 zones** survive k=5; **8/8** populated (pilot; cells multiply ~31× at full scale).

## T18 — Reproducibility
- Verified content-identical (transactions + transaction_items) across two `build_all(scale=500)` runs by `test_T18_reproducibility_content_identical`.
- Catalog authoring (`make catalog`) is byte-identical on rebuild (hash/index-derived, no RNG).

## Totals (annualized, full-population projection)
- **Grocery:** $524M/yr (target ~$518M)  |  **QSR:** $79M/yr (target ~$80M)
- Window totals scale from the 3.2% pilot sample.
