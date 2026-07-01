# BUILD SPEC — Data Model v2 (for Claude Code)

Rebuild the data-generation model to the locked design. **Source of truth for every target value: `docs/MERCHANT_PROFILES.md`** (Parts A–C + Appendix D) and `docs/DATA_REALISM_PROPOSAL_v2.md`. This spec is the *how*; those docs are the *what/why*.

## How to run this
- **Plan mode first.** Read this spec + the two design docs, produce a plan, wait for review.
- **Phase by phase.** One phase at a time; commit per phase; pause at each checkpoint marked ⏸ for review.
- **`main` stays frozen.** All work on branch `datamodel-v2`; merge to `main` only at end-of-wave after the full validation suite passes.
- **Two-commit discipline.** Fix, then retire scaffolding in a separate commit.
- Credential note: if a push stalls, verify `git config --global credential.helper osxkeychain`; never embed tokens in the remote URL.

---

## Phase 0 — Start the new version (git)

Pre-step: copy `MERCHANT_PROFILES.md` and `DATA_REALISM_PROPOSAL_v2.md` into `docs/`.

```bash
# from repo root, clean tree
git status
git add docs/MERCHANT_PROFILES.md docs/DATA_REALISM_PROPOSAL_v2.md
git commit -m "docs: lock datamodel-v2 design (merchant profiles + realism proposal)"
git tag pre-datamodel-v2                       # recoverable baseline of current main
git push origin main --tags
git checkout -b datamodel-v2                    # new version working branch
git commit --allow-empty -m "chore: begin datamodel-v2 — realistic panel + generation rebuild"
git push -u origin datamodel-v2
```
⏸ Confirm branch + tag pushed before proceeding.

---

## Guardrails — keep UNCHANGED
- 8-layer causal pipeline; **latent-first, observable-derived**.
- Determinism: single seed → byte-identical Parquet (verify at end).
- Gravity store-choice model (now with non-uniform A_s).
- Config-driven, segment-agnostic engine; `loader.py` invariants.
- Peer lake + k=5 privacy; claims validator; answer-key physical separation.
- DuckDB-on-Parquet storage; deterministic writer.

## Scope of change (summary)
| Area | Change |
|---|---|
| Panel | Drop TJ Maxx; grocery KRG 6 / ACM 5 / WDX 4; QSR TB 9 / BK 8 / CFA 6 |
| Catalog | Static committed artifact; dual taxonomy (merchant + functional); PL as distinct SKUs; real names/descriptions; shelf_price baked |
| Store volume | Non-uniform A_s (brand pull + assortment); calibrate to per-store AUV |
| Population | ~155k cards; participation 36/18/46; retuned intensity tiers |
| Trips | A_s gravity; **add seasonality** (spring drift + Easter + Memorial Day); QSR dayparts (CFA Sunday zero, TB late-night, BK breakfast) |
| Baskets | **Add QSR combo-attach affinity**; national-vs-PL selection by affluence |
| Pricing | **Simplify: realized price = shelf_price** (remove zone effect, time drift, noise) |
| Excluded | Promotions + anomalies **disabled** this pass (keep code dormant, don't delete) |

---

## Phase 1 — Config & panel
**Files:** `config/merchants/*.yaml`, `config/segments/*.yaml`, `config/global.yaml`, `config/loader.py`
- Delete `merchants/tj_maxx.yaml`, `segments/off_price.yaml`.
- Add `merchants/burger_king.yaml`, `merchants/chick_fil_a.yaml`.
- Update store counts + `zone_placement_bias` to the **A11 (grocery)** and **B1 (QSR)** placement grids.
- Add per-merchant `attractiveness` (A_s) and `sku_target` (assortment) from Appendix D.
- `global.yaml`: `target_cards: 155000`; window unchanged (2026-03-01→05-29); per-segment volume targets from C9; `distance_decay.beta` grocery 2.0 / qsr 2.2; add `seasonality` block (spring drift, Easter, Memorial Day).
- `loader.py`: update `expected_store_count` to 38; keep/extend invariants (placement reconciles, A_s present, sku_target present).

**Validation:** loader passes; `expected_store_count == 38`; 6/5/4 grocery + 9/8/6 QSR.
**Commit:** `feat(config): v2 panel — real chains, placement, A_s, assortment targets`
⏸ Review config before building catalog.

---

## Phase 2 — Catalog (static committed artifact)
**Files:** `src/generate/engine/catalog.py` → refactor into an **authoring script** (`scripts/build_catalog.py`, `make catalog`); new `config/catalog/` (controlled vocabularies).
- **Grocery:** curated-combinatorial from controlled vocabularies (department → category → subcategory → {type × size × brand-tier}). Counts: KRG ~1,350 / ACM ~1,250 / WDX ~1,050 (trim tail for smaller banners).
- **QSR:** author menus directly from real items (see B3): TB/BK ~70–90, CFA ~50–70.
- **Dual taxonomy per row:** `merchant_department/category/subcategory` (per-banner labels, divergent) **and** `functional_department/category/subcategory` (normalized, shared). See A12 examples.
- **Private label as distinct SKU records** (not a flag); PL share KRG 27 / ACM 19 / WDX 25%.
- `shelf_price` = base anchor × banner positioning × PL factor (bake in; KVI-tight / specialty-wide preserved).
- **Emit two artifacts:** `data/catalog/products.csv` (observable — columns per A12; **no canonical_id**) and `data/eval/canonical_map.csv` (hidden — `banner, sku_code, canonical_id`).
- Commit `products.csv` to the repo (tiny, reviewable). `canonical_map.csv` lives in the answer-key area, never read by lake/agents.

**Validation:** SKU counts per banner; every row has both taxonomies; PL rows present at target share; `canonical_id` absent from `products.csv`; deterministic (rebuild → identical).
**Commit (two):** `feat(catalog): dual-taxonomy static catalog, PL as SKUs` then `chore(catalog): retire in-pipeline catalog build`
⏸ Review `products.csv` (spreadsheet) before wiring generation.

---

## Phase 3 — Population & latents
**Files:** `engine/population.py`, `engine/customers.py`
- `population.py`: 155k cards; participation archetypes → **grocery-only 36% / QSR-only 18% / both 46%**; intensity tiers per Appendix D (grocery core/regular/occasional; QSR heavy/regular/occasional) with triangular trip budgets.
- `customers.py`: durable latents — `home_zone` (residential-weight draw), `affluence` (Gaussian around zone), grocery loyalty (55/30/12/3) + primary banner (~40/33/27, emergent), QSR brand preference, payment identity (credit-vs-debit logistic in affluence; network Fed Diary; wallet), staples.

**Validation:** card count 155k; participation split; overlap "both" ≈46%; loyalty concentration ≈76%; affluence distribution tracks zones.
**Commit:** `feat(population): 155k shared universe + durable latents`

---

## Phase 4 — Trips (gravity + timing + seasonality)
**Files:** `engine/trips.py`
- Gravity `P(s|z) ∝ A_s/(dist+d0)^β` with **A_s from config (non-uniform)**; assortment breadth feeds A_s (bigger catalog → higher pull); grocery combines gravity × loyalty; QSR gravity × brand preference.
- Timing: DOW (grocery weekend, QSR Fri–Sat), dayparts (**BK breakfast, TB ~18% post-9pm, CFA closed Sundays hard-zero**), pay-cycle bumps.
- **Add seasonality layer:** gradual spring drift (+~4% end-to-end); Easter week (Mar 30–Apr 5) +~25% in baking/ham/eggs/candy; Memorial Day week (May 19–25) +~30% in grilling/snacks/beverages; light Cinco/Mother's Day. Apply as trip-rate + category-weight multipliers.

**Validation:** per-store trip share reproduces ~49/33/18 grocery dollar split direction; CFA Sunday = 0; TB post-9pm ≈18%; BK breakfast present; Easter + Memorial Day spikes visible; footprint tracks residential weight.
**Commit:** `feat(trips): A_s gravity + dayparts + seasonality`

---

## Phase 5 — Baskets (missions + affinity)
**Files:** `engine/baskets.py`
- Missions (grocery: weekly_stockup / meal_tonight / fill_in / breakfast …; QSR: combo / snack / group / breakfast) → category mix + triangular basket size (grocery stockup 15–22 / fill-in 4–7 / quick 2–3; QSR 2–4).
- Grocery affinity matrix (PASTA→SAUCE etc.) — keep.
- **Add QSR combo-attach affinity:** entrée→side→drink.
- **National-vs-PL selection by affluence** → PL share emerges at target.
- Staples included with high probability.

**Validation:** department sales mix (meat ~13% / produce ~11% / dry ~38%); PL share 27/19/25; grocery affinity lift ≥ thresholds; QSR combo-attach materializes; by-merchant category skew (A13); heavy-tail basket sizes.
**Commit:** `feat(baskets): missions + QSR combo-attach + affluence PL selection`

---

## Phase 6 — Payment & flat pricing
**Files:** `engine/payment.py`, `engine/pricing.py`
- `payment.py`: keep entry-mode/wallet/connectivity from identity + segment baseline (contactless ~53%, wallet ~17%).
- `pricing.py`: **simplify — realized line price = catalog `shelf_price`.** Remove zone effect, time drift, line noise. (Reduces surface area.)

**Validation:** payment mix in band; realized prices equal catalog shelf prices exactly; "no banner cheapest >70%" still holds (from shelf prices).
**Commit (two):** `feat(pricing): flat shelf-price model` then `chore(pricing): remove zone/drift/noise modifiers`

---

## Phase 7 — Disable promos/anomalies; wire run_all
**Files:** `engine/events.py`, `engine/run_all.py`
- `events.py`: **disable** promo generation and anomaly planting (config flag off / no-op). Keep code + answer-key framework dormant for a later wave; do not delete.
- `run_all.py`: **read committed `products.csv`** as build input (don't rebuild catalog); chain layers; write `data/raw/*`. Confirm seeds/ordering preserved.
- **Expose catalog as a runtime view** (`products`), loaded from `products.csv` (or convert to Parquet at load). Dashboard/agents **join `transaction_items → products` on `sku_code`** to resolve name/category/subcategory/functional taxonomy/PL — line items alone carry only `sku_code`. Do **not** denormalize catalog fields onto line items (keeps the normalization boundary at the catalog).
- **Access boundary (theme-5 ready) — the clean catalog is an *enrichment*, not merchant-provided.** A terminal/payments company captures for free: merchant/store identity, segment (MCC), timestamp, payment fields, and per line `sku_code` + a short/truncated description string + price + qty. It does **not** receive any merchant's clean item master (names, categories, functional taxonomy, brand, PL) — assume merchants won't share it. So the clean catalog is the *assumed-normalized* layer standing in for the deferred enrichment. Themes 1-4 provide it as-if solved, for **both own-data and peer** views. **Theme 5 removes the clean catalog for own AND peer**, deriving names/categories/functional taxonomy from `sku_code` + raw string. Only merchant/store identity + segment (MCC) stay free — so store- and segment-level analysis is always available; product-level structure must be derived. For v2: keep names clean (no raw strings yet); the seam theme 5 opens is the entire catalog-enrichment step.

**Validation:** no promotions/anomalies tables emitted (or empty); run completes; determinism intact.
**Commit:** `feat(engine): wire v2 pipeline, catalog as input, promos/anomalies dormant`

---

## Phase 8 — Validation suite & DQ report
**Files:** `tests/data_quality/*`, `docs/DQ_REPORT.md`
Update/replace acceptance tests to the v2 targets (bands from A9 / B7 / C9 / Appendix D):
- Per-store AUV: KRG ~$40M / ACM ~$32M / WDX ~$22M; CFA ~$8M / TB ~$2.1M / BK ~$1.6M.
- Grocery dollar split ~49/33/18; **CFA 6 units > TB+BK 17 combined**.
- Department sales mix; PL share 27/19/25; payment mix; affinity lift (grocery + QSR combo-attach).
- Overlap 46% both; loyalty ~76%; trip freq ~1.5/1.4 per wk.
- Seasonality: Easter + Memorial Day spikes; CFA Sunday zero; TB post-9pm ~18%; BK breakfast.
- Totals: 155k cards, ~4.3M txns, ~33M line items, ~$148M window.
- Determinism (byte-identical rebuild).
Publish measured magnitudes next to bands in `docs/DQ_REPORT.md`.

**Commit:** `test(dq): v2 acceptance suite + DQ report`
⏸ Review DQ report; then merge `datamodel-v2` → `main` at end-of-wave.

---

## Definition of done
- All Phase 8 bands pass in CI; DQ report published.
- `products.csv` committed and reviewable; `canonical_map.csv` in answer-key area, unreadable by lake/agents.
- Pricing flat; promotions + anomalies dormant.
- Determinism verified (T18-equivalent).
- `main` updated only after the full suite is green.
