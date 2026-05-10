"""Parameterized lake view-builders.

The lake is a virtual layer in v2.5 — there are no physical ``lake_*``
tables in SQLite (Phase 5c removes the v2 ones). Instead, lake access
goes through query functions in ``src.lake.views`` that take a viewing
merchant as input, exclude that merchant's data, pseudonymize peer
merchants per the per-merchant peer mapping (`peer_a`..`peer_d`), and
apply privacy generalizations (ZIP3, time bucketing, txn-total binning,
opaque IDs, no consumer linkage).

Usage::

    from src.lake import get_lake_transactions, get_lake_stores
    df = get_lake_transactions("KRG")  # Kroger's view of the lake
"""
from .views import (  # noqa: F401
    HOUR_BUCKETS,
    K_ANONYMITY_K,
    TOTAL_BINS,
    apply_k_anonymity,
    generate_opaque_id,
    get_lake_stores,
    get_lake_transactions,
    lake_stores_sql,
    lake_transactions_sql,
    register_lake_functions,
    to_hour_bucket,
    to_total_bin,
)
from .peer_mapping import build_peer_mapping, peer_case_sql  # noqa: F401
