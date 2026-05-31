"""Wave 2 lake package — anonymized cross-merchant aggregates.

The Wave 2 lake is the dual-path peer surface: tenants see their own
data at full grain via the tenant predicate (`isolation.py`); peer
data flows through this lake at category/zone/period grain with
viewer exclusion + relationship relabel + identity strip.

Architecture (per `docs/SPEC_wave2_anonymization_lake.md`):

* `observable_guard.py` — §1 invariant accessor. The lake builder may
  read only observable transaction/store columns; planted profile
  columns (loyalty_type, home_zone, zone.affluence, etc.) raise
  `ForbiddenColumnError`. The §1-violation-in-disguise pattern
  (reading planted X, relabeling "derived") is blocked here.
* `isolation.py` — §2 tenant guards. `check_tenant_predicate` rejects
  cross-merchant tenant queries; `wrap_tenant_query` CTE-wraps as
  defense-in-depth; `assert_lake_source_paths` forbids data/eval/
  reads (the anomaly answer key).
* `zones.py` — §3 zone derivation from store coordinates only
  (k-means k=8). Reading stores.zone_id / customers.home_zone is
  forbidden — coordinate-derived clusters that happen to correspond
  to Wave 1's planted zones is the validation result, not the input.
* `build.py` — §4 five-table builder with k≥50 floor + coarsening
  ladder. Reads through `observable_guard`. Each builder is callable
  individually; the orchestration lives in `scripts/build_lake.py`.
* `scope.py` — §5 query-time dual-path scoping. `scope_for_viewer`
  drops viewer rows, adds `peer_relationship` (segment_peer /
  cross_segment), strips `banner_code` (D24.1 identity strip).
  `assert_no_identity_leak` is the L5b safety check.
* `manifest.py` — §5 grain manifest (D23.7) per-table machine-
  readable spec — finest grain, dimensions, metrics, AND honest
  exclusions ("no peer SKU", "no raw mean spend"). Wave 3 agents
  consume it to know their limits.

Wave 2 ships only the lake + scope + manifest. **No agents**
(Wave 3), **no dashboard refactor** (Wave 4), **no l-diversity / DP**
(deferred per D21.3 / D24.3 — aggregates ARE the future DP
injection point, no seam shipped).

The v3 `views.py` (k=5 + flat `peer_a..d`) and `peer_mapping.py`
were retired at Stage 7 — they target the wrong privacy bar and
the wrong relabel scheme for Wave 2.
"""
from src.lake.build import (
    K_MIN,
    build_lake_category_metrics,
    build_lake_cross_merchant_cohorts,
    build_lake_payment_mix,
    build_lake_segment_mix,
    build_lake_trade_area,
)
from src.lake.isolation import (
    LakeSourcePathError,
    TenantIsolationError,
    VALID_MERCHANTS,
    assert_lake_source_paths,
    check_tenant_predicate,
    wrap_tenant_query,
)
from src.lake.manifest import full_manifest, list_tables, manifest_for
from src.lake.observable_guard import (
    ALLOWED_COLUMNS,
    FORBIDDEN_TABLES,
    ForbiddenColumnError,
    load_table,
    read_parquet_relation,
)
from src.lake.scope import (
    IdentityLeakError,
    UnknownViewerError,
    assert_no_identity_leak,
    scope_for_viewer,
    scope_many,
)
from src.lake.zones import derive_zone_character, derive_zone_for_store

__all__ = [
    # observable_guard (§1)
    "ALLOWED_COLUMNS",
    "FORBIDDEN_TABLES",
    "ForbiddenColumnError",
    "load_table",
    "read_parquet_relation",
    # isolation (§2)
    "LakeSourcePathError",
    "TenantIsolationError",
    "VALID_MERCHANTS",
    "assert_lake_source_paths",
    "check_tenant_predicate",
    "wrap_tenant_query",
    # zones (§3)
    "derive_zone_character",
    "derive_zone_for_store",
    # build (§4)
    "K_MIN",
    "build_lake_category_metrics",
    "build_lake_cross_merchant_cohorts",
    "build_lake_payment_mix",
    "build_lake_segment_mix",
    "build_lake_trade_area",
    # scope (§5)
    "IdentityLeakError",
    "UnknownViewerError",
    "assert_no_identity_leak",
    "scope_for_viewer",
    "scope_many",
    # manifest (§5)
    "full_manifest",
    "list_tables",
    "manifest_for",
]
