"""Layer 7 — Catalog & price (D19).

Two responsibilities:

* **Catalog & assortment** — per-merchant SKU lists with
  ``canonical_id`` cross-merchant matching, ``private_label`` flag,
  ``base_price`` anchored to real 2025 category prices (D19.1).
  Grocery ~1,100 SKUs (D17.6); QSR + retail per D19.5.
* **Pricing** — anchor × per-merchant strategy (category role,
  PL vs national, per-SKU competitive index, promo timing — all
  D19.2) × zone effect × promo state × time drift × small noise
  (D19.1, applied in that order). Replaces the v3 ``base × tier
  × ±2%`` formula. Closes the AOV chain (D19.6, with D17 / D5).

Stage 4.7 implements; Stage 3 ships a stub.
"""

# Stage 4.7 will implement.
