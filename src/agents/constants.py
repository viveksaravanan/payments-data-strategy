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


# ---------------------------------------------------------------------------
# Known-Value Items (KVI) — pricing gate B
# ---------------------------------------------------------------------------
# Price-visible, traffic-driver subcategories that shoppers comparison-shop
# across banners (milk, eggs, bread, bananas, coffee, ground beef). A
# below-peer price on a KVI is usually a *deliberate* loss-leader to pull the
# trip — so the pricing agent must NOT read "cheaper than peers here" as
# "raise" without flagging that it is a traffic driver. The grain is
# ``functional_subcategory`` (the lake's like-for-like grain), so the list is
# matched against the drilled subcategory, not the category headline.
#
# Grocery-specific by design: QSR banners have no matching functional
# subcategories, so the KVI gate is a graceful no-op for them (nothing to
# match) rather than a special case in the agent code.
#
# Single source of truth: this same tuple is injected into the pricing prompt
# (``{{kvi_subcategories}}`` in ``Specialist._render_prompt``) and is the list
# the Tier-2 ``price_benchmark`` helper will classify against, so the prompt
# and the server-side helper can never drift.
KVI_SUBCATEGORIES = (
    "Whole Milk",
    "2% Reduced-Fat Milk",
    "Skim & Low-Fat Milk",
    "Grade A Eggs",
    "Sandwich Bread",
    "Buns & Rolls",
    "Bananas & Everyday Fruit",
    "Coffee",
    "Ground Beef",
)

# Comma-joined form for splicing into the prompt template.
KVI_SUBCATEGORIES_PROMPT = ", ".join(KVI_SUBCATEGORIES)
