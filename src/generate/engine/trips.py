"""Layer 4 — Trips (D15 + D15b).

Two halves:

* **4a temporal placement (D15)** — turn each card's per-segment trip
  budget into dated, timed transactions: active-weeks + Dirichlet
  spread; day-of-week weights (D15.2); pay-cycle overlay (D15.3);
  daypart curves (D15.4); cohort first-appearance (D15.5).
* **4b store resolution (D15b)** — within a banner, gravity-based
  store choice ``P(s) ∝ A_s / (d + d₀)^β`` (D13.4). Banner choice
  for grocery is ``P(banner) ∝ loyalty_weight × gravity_pull`` (D16.1
  composed with D13.4).

The anomaly multiplier hook (D15.6, scoped to zone × store × time-
window × category) is wired here as a first-class input; specific
anomalies A1-A3 are authored at Stage 4.8 / D20.

Stage 4.4 implements; Stage 3 ships a stub.
"""

# Stage 4.4 will implement.
