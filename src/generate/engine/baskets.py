"""Layer 5 — Baskets & affinity (D17).

Per trip: sample a mission (D17.1) conditioned on the customer's
preference vector × daypart × archetype; fill the basket by drawing
categories from the mission distribution, intersected with customer
preference and store assortment; apply the complementary-affinity
boost (D17.2) for designed pairs; bias item selection toward the
customer's staple SKUs (D17.3); size the basket by archetype
(D17.4). Emergent co-occurrence is the goal — discoverable via lift.

Stage 4.5 implements; Stage 3 ships a stub.
"""

# Stage 4.5 will implement.
