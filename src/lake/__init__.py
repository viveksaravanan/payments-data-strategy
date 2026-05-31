"""Wave 2 lake package.

Module layout is being built incrementally per
``docs/SPEC_wave2_anonymization_lake.md`` §§1-7. The public Wave 2 API
re-exports (``build_lake``, ``scope_for_viewer``, ``manifest``) are
populated at Stage 7. Until then, lake consumers import directly from
the per-stage modules:

* ``src.lake.observable_guard`` — §1 invariant accessor (Stage 1)
* ``src.lake.isolation`` — §2 tenant guards (Stage 2)
* ``src.lake.zones`` — §3 zone derivation (Stage 3)
* ``src.lake.build`` — §4 five-table builder (Stage 4)
* ``src.lake.scope`` — §5 query-time viewer scoping (Stage 5)
* ``src.lake.manifest`` — §5 grain metadata (Stage 5)

The legacy v2.5 ``views.py`` + ``peer_mapping.py`` (k=5, flat
``peer_a``..``peer_d``) are retired at Stage 7 — they target the wrong
threshold and wrong relabel scheme for Wave 2.
"""
