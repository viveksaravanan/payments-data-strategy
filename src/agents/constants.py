"""Analysis-window constants for the agent layer.

The specialist agents answer over a **single fixed analysis window**, not a
model-chosen one. Before this, each specialist copied date literals out of its
prompt and was trusted to filter its own SQL — which drifted (e.g. one grocer
answered D7 on the last ~4 weeks while its peers used the full 90 days, making
the numbers incomparable). The window is now injected server-side into the
tenant CTE wrap (`src/lake/isolation.py::wrap_tenant_query`, on `txn_ts`) and
the lake view registration (`src/lake/lake_sql.py::_register_viewer_views`, on
`txn_date`), so the model cannot query outside it.

``ANALYSIS_END`` is **exclusive** and drops the partial final week
(2026-05-25 → 2026-05-29), so every pill covers Mar 1 .. May 24 inclusive.

These are hand-mirrored from the data-generation config
(`src/generate/config/global.yaml`: window.start_date 2026-03-01 /
end_date 2026-05-29; the memorial-day event ends 2026-05-25) — there is no
runtime binding between the two, so keep them in sync if the generation
window changes.
"""
from __future__ import annotations

from datetime import date

ANALYSIS_START = date(2026, 3, 1)
ANALYSIS_END = date(2026, 5, 25)  # exclusive — excludes the partial final week

# ISO strings for splicing into SQL (`DATE '2026-03-01'`).
ANALYSIS_START_ISO = ANALYSIS_START.isoformat()
ANALYSIS_END_ISO = ANALYSIS_END.isoformat()
