"""Layer 8 — Promotions & planted anomalies (D20).

Two kinds of events layered onto built data via the D15.6 multiplier
hook:

* **Promos (D20.1)** — segment-specific type menu (weekly ad / TPR /
  BOGO / clearance / LTO / value menu / markdown). ~25-35% grocery
  units on promo. Demand response: promoted SKUs get a basket-
  inclusion + quantity lift during the window, scaled to depth ×
  category elasticity — the piece the v3 baseline lacked. Cross-
  merchant divergence flips gaps week to week.
* **Planted anomalies A1-A3 (D20.2)** — scoped perturbations the
  Anomaly agent must rediscover. A1 localized demand decline,
  A2 category demand spike, A3 competitive share shift. Ground
  truth recorded to ``data/eval/anomalies_groundtruth`` (Stage 5
  contract; never flows to the lake or agents).

Fraud / tampering (A4-A5) explicitly out of scope for v4 per D20.3.

Stage 4.8 implements; Stage 3 ships a stub.
"""

# Stage 4.8 will implement.
