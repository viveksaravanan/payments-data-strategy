"""Lake package — peer surface for the cross-merchant demo.

Wave 3.5 replaced the Wave 2 aggregate lake with a raw **line-item**
lake queried via SQL (`build_line_items.py` + `lake_sql.py`), so the
Wave 2 builder / manifest / zone-derivation / scope modules were retired
in Stage E (SPEC §11). What remains:

* `observable_guard.py` — §1 invariant accessor. The lake builder may
  read only observable transaction/store columns; planted profile
  columns (loyalty_type, home_zone, zone.affluence, etc.) raise
  `ForbiddenColumnError`. The §1-violation-in-disguise pattern
  (reading planted X, relabeling "derived") is blocked here.
* `isolation.py` — §2 tenant guards. `check_tenant_predicate` rejects
  cross-merchant tenant queries; `wrap_tenant_query` CTE-wraps as
  defense-in-depth; `assert_lake_source_paths` forbids data/eval/
  reads (the anomaly answer key).
* `build_line_items.py` — the per-viewer line-item lake builder
  (Wave 3.5 §13.A); `lake_sql.py` — the `query_lake_sql` engine
  (aggregating-only + k=50 floor).
"""
from src.lake.isolation import (
    LakeSourcePathError,
    TenantIsolationError,
    VALID_MERCHANTS,
    assert_lake_source_paths,
    check_tenant_predicate,
    wrap_tenant_query,
)
from src.lake.observable_guard import (
    ALLOWED_COLUMNS,
    FORBIDDEN_TABLES,
    ForbiddenColumnError,
    load_table,
    read_parquet_relation,
)

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
]
