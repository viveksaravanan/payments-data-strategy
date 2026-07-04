# Dashboard (Wave 4)

The Streamlit dashboard, rewired in Wave 4 to the v4 stack. Same v3 UI,
layout, and styling; the data layer, agent panel, and chart handling changed.

## Run

`streamlit run src/dashboard/app.py` (needs `data/raw/` + `data/lake/items/`
built — `make seed` + `make lake-items`). `ANTHROPIC_API_KEY` enables the
agent chat panel; without it the panel returns an honest error.

## Data layer — DuckDB-on-Parquet (Wave 4 Stage 1)

`data.py::_conn()` returns an **in-memory DuckDB** connection (NOT the retired
v3 SQLite `payments.db`, which is removed). The v4 Parquet is exposed as
**aliasing views** (`_TENANT_VIEWS`) that bridge the v3 query vocabulary onto
the v4 column names so most query SQL is unchanged:

- `tenant_transactions`: `banner_code AS merchant_id`, `subtotal AS txn_total`,
  `customer_token AS customer_id`, `CAST(txn_ts AS DATE) AS txn_date`.
- `tenant_customers`: `card_id AS customer_id`. (v4 has **no ZIP** — `home_zone`
  is a planted zone code, no neighborhood mapping.)
- `tenant_stores` / `tenant_products` / `tenant_transaction_items`: mostly direct;
  `products` has `product_name AS name`. **Own-data taxonomy = MERCHANT labels:**
  `tenant_products` aliases `merchant_category AS category` /
  `merchant_subcategory AS subcategory` (this banner's real shelf labels, the right
  view for own-only cards). The `functional_*` columns are also exposed under their
  own names — any card that compares own vs the peer lake (e.g.
  `category_anomalies`) MUST group its own side on `functional_category`, since the
  lake publishes the shared functional taxonomy as `category`/`subcategory`/`department`.

DuckDB dialect notes vs the old SQLite: `DATE()` and `strftime` work as-is;
`SUBSTR(txn_ts,…)` for the hour → `EXTRACT(hour FROM txn_ts)`; the
`DATE(x,'weekday 0','-6 days')` Monday-of-week idiom → `date_trunc('week', x)`;
DuckDB has **strict GROUP BY** (every selected non-aggregate must be grouped).

**Week-key bucketing — use `strftime`, not a bare VARCHAR cast.**
`date_trunc('week', txn_ts)` on a **TIMESTAMP** column returns a **TIMESTAMP**, so
`CAST(date_trunc('week', txn_ts) AS VARCHAR)` yields `'2026-05-18 00:00:00'` — the
` 00:00:00` suffix means it never matches the Monday-keyed string keys
(the KPI 12-week default list, the weeks from `_recent_baseline_weeks`), silently
zeroing every "recent week" lookup (blank KPIs, all-stores −100% deltas). Bin weeks with
`strftime(date_trunc('week', X), '%Y-%m-%d')`, which returns a date-only
`'YYYY-MM-DD'` for both TIMESTAMP and DATE inputs (and so keeps own `txn_ts` keys
aligned with peer-lake `txn_date` keys).

Caching: each `@st.cache_data` query function takes the **viewer** as an explicit
arg so the cache key varies by merchant (switching the selector must not bleed
one merchant's data to another).

## Peer cards — aggregate same-segment peers (Wave 4 Stage 1)

Only two main-dashboard cards use peer data: **Card 3.1** neighborhood map
(`neighborhood_performance`) and **Card 3.2** store table (`store_anomalies`);
plus the anomaly KPI (`category_anomalies`). They call `_register_lake_views(con,
viewer)` to expose `data/lake/items/<VIEWER>/lake_{transactions,stores}.parquet`
as `lake_transactions` / `lake_stores`, then query a **single aggregate peer**
(`peer_relationship='peer'` — the old `peer_a`/`peer_b` pseudonyms are GONE; the
line-item lake has no per-competitor identity), count transactions via
`COUNT(DISTINCT lake_txn_id)`, and apply the **k=50** floor. Gated on the viewer's
peer count via `has_same_segment_peers()` (datamodel-v2: all six banners —
grocery KRG/ACM/WDX + QSR TBL/BKG/CFA — have 2 same-segment peers, so every
viewer gets a peer overlay; the own-only path is a fallback for any future
single-member segment).

Card 5.3 "customer geography" was **redesigned** to "where customers shop"
(distinct customers by the neighborhood of the stores they transact at) — v4 has
no home address. The v3 "under-served neighborhood" sub-insight is dropped.

## Agent panel — structured, no charts (Wave 4 Stages 2-3)

- **Agent responses are explanation-only — no chart** (Wave 4). The hard-coded
  per-pill chart system (`chat.py::QUESTION_RENDERERS` + `_render_question_chart`
  + the `_render_*` pattern renderers) was **removed**. Charts return in Wave 4.1
  as agent-INVOKED helpers (`chart_build.py`), never hard-coded.
- `agents.py::_response_to_dict()` maps the Wave 3.5 **structured** `AgentResponse`
  (headline / evidence / so_what / derived prose / result→table / telemetry;
  chart always None) onto the dict the renderer consumes. (`AgentResponse` has no
  `to_dict`; the old code calling it was broken.)
- `chat.py::_render_structured_answer()` renders headline (bold) + evidence
  (bullets) + so-what (italic), falling back to the joined `prose` for error /
  legacy responses, then the result table. No chart slot.
- Pills come from `questions.py` keyed by segment (GROCER / QSR). Since every
  datamodel-v2 banner has same-segment peers, the QSR pricing / demand / anomaly
  sets now lead with a peer-comparison pill (parity with the grocer set), so no
  per-viewer pill hiding is needed.

## chart_patterns.py

KEPT — the main dashboard's 5 sections (`views.py`) use its render helpers (KPI
callouts, bars, heatmap, neighborhood map, tables). Only the chat panel's per-pill
chart layer was removed.

## Known follow-ups

- **Dead-code sweep — DONE.** The per-pill-chart helpers were removed in two passes:
  the bulk (`uc_decline_trajectory`, `category_peer_pricing_gaps`,
  `revenue_gap_decomposition`, `expansion_opportunity`, the TBL/TJX trends/heatmap/
  ticket-band helpers) in an earlier cleanup, and the stragglers (`categories_for`,
  `_full_weeks`, the `_TJX_TICKET_BANDS` / `_D_TIE_PP` / SQLite-DOW constants, plus the
  empty T-P/T-D section headers) in the holistic-improvements sweep. Each was verified
  zero-reference before deletion.
- **Streaming — DECIDED, deferred to Wave 4.1.** `specialist.answer`'s `on_token` is an
  *intentional* wire-up point, not dead code: the parameter is threaded (Optional,
  defaulted to `None`) through `dispatch.py` → `orchestrator.py` → `specialist.py:251`
  (documented `# noqa: ARG002` "intentionally unwired pending Wave 4"), and the chat
  panel's `_render_live_turn` already carries the client-side display logic
  (`_streaming_cut_index`). Final-answer token streaming lights up when `specialist.answer`
  emits tokens in Wave 4.1; until then the live **progress-message** path is what renders.
  Kept as scaffolding by design — do not remove.
- **Improvement (deferred):** surface the map's computed `peer_signal`
  (market-wide vs operational) in the Card 3.1 tooltip.
