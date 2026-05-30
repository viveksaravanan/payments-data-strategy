"""Layer 1 — Geography & Zones (D13).

Reads metro + merchant configs; places 29 stores across 8 zones per
the D13.2 matrix; assigns each store its zone centroid ± jitter.
Defines the gravity geometry consumed by Layer 4b store resolution
(``trips.py``).

Stage 3 ships this as a stub. Stage 4.1 implements ``build_stores``
+ ``home_zone_distribution`` and writes the ``stores`` and
``zones`` Parquet tables.
"""

# Stage 4.1 will implement.
