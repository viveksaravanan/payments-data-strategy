# SPEC — Wave 4: Dashboard Rewire (DuckDB-on-Parquet + Wave 3.5 agents, charts out of agent responses)

**Status:** Draft for build
**Branch target:** `v4` (continues from closed Wave 3.5 state; NO PR, NO merge to main)
**Scope this wave:** Bring the v3 Streamlit dashboard back to life on the v4 stack — **keeping structure, layout, and styling exactly as-is** — by migrating its data layer from the retired SQLite to DuckDB-on-Parquet, rewiring the agent panel to the Wave 3.5 SQL agents, and **removing charts from agent responses entirely**. The hard-coded per-pill chart system is deleted (not deferred). Re-validate that the dashboard's own charts/KPIs still apply at full scale.

> **Reality check (read first).** The dashboard does **not run today.** `src/dashboard/data.py` imports names removed in Wave 3.5 Stage E (`from src.lake import get_lake_stores, get_lake_transactions`) → `ImportError` at startup. It reads a 2.9 GB **v3-schema SQLite** (`data/payments.db`) whose column names don't match the v4 Parquet; `streamlit_app.py` states it "hasn't been rewired to the new DuckDB+Parquet engine." So "rewire" is a **data-layer migration + schema reconciliation**, not "queries return bigger numbers." This spec is scoped to that reality.

---

## 1. Why this exists

Wave 3.5 replaced the lake and the agent surface and turned the agent-side chart layer off (`chart_build.py` dormant under `CHARTS_ENABLED=False`). The dashboard was left as a v3 SQLite artifact and is now broken. Wave 4 has four jobs:

1. **Migrate** the dashboard's data layer from SQLite (`payments.db`) to **DuckDB-on-Parquet** — the same engine the agents already use (`data/raw/` + `data/lake/items/`) — reconciling the v3→v4 schema differences.
2. **Rewire** the agent panel to the Wave 3.5 SQL agents (`query_tenant` + `query_lake_sql`), fix the broken response glue, and render the **structured** answer (headline / evidence / so-what) + result table.
3. **Remove charts from agent responses.** Whether a pill or a free-form question, the agent gives an explanation only — no chart. Delete the hard-coded per-pill pattern-chart system. (Charts return in Wave 4.1 as **agent-invoked** helpers, never hard-coded.)
4. **Re-validate** the dashboard's own charts/KPIs at full scale and collapse the two peer-bearing cards to the new aggregate-peer model.

**This is not a redesign.** UI structure, layout, and styling stay pixel-for-pixel. Only the data-access layer, the agent glue, and the (removed) per-pill charts change.

---

## 2. Scope boundary (read this first)

**IN scope:**
- Migrate `data.py` from `sqlite3`/`payments.db` to DuckDB reading the v4 Parquet, via **aliasing views** that bridge the v3→v4 column renames (§4). Viewer-keyed caching (§5).
- Reconcile every dashboard query's columns against the v4 Parquet schema (Stage 0, §4).
- Rewire the agent panel to the Wave 3.5 agents; fix the `AgentResponse.to_dict()` break; render the structured answer + table; per-viewer pills (§7).
- **Remove the hard-coded per-pill chart system** (§7); agent responses are explanation-only.
- Collapse the two main-dashboard peer cards (neighborhood map, store table) to the aggregate `peer_relationship='peer'` model with k=5 (§6).
- Redesign Card 5.3 "customer geography" to "where customers shop" (the v4 data has no home address — §6).
- Re-validate the demo walkthrough against the new peer behavior + the no-chart agent responses (§8 Stage 5).

**OUT of scope (deferred to Wave 4.1):**
- **Any chart inside an agent response.** Returns in 4.1 as an **agent-invoked** chart (model authors intent; `chart_build.py` builds it deterministically from result columns). `chart_build.py` stays dormant this wave. The hard-coded per-pill system stays **removed**.
- New agents, production privacy changes, UI redesign, merge to main.

---

## 3. Locked decisions (do not re-litigate)

1. **Same UI, structure, styling.** v3 layout is the target: persistent dashboard (5 sections) + right-side "ask the agent" chat panel; existing KPI cards, map, category/store charts, customer section. Palette `#0F4C81` / `#F7F8FA` / `#1A1F2E`; system fonts; Plotly basic; Folium + CartoDB Positron tiles. The reusable render library `src/dashboard/chart_patterns.py` is **kept** (the 5 sections use it); only the per-pill chart layer in `chat.py` is removed.
2. **DuckDB-on-Parquet** is the dashboard data layer (matches the agents; faster for aggregate queries at full scale; no 2.9 GB SQLite to build/ship). `data/payments.db` is removed.
3. **5 merchants, real segments** (`grocery`: KRG/ACM/WDX; `qsr`: TBL; `off_price`: TJX). The merchant selector switches viewer context AND peer availability — driven by `data/lake/items/metadata.json::segment_peer_count` (grocers = 2, TBL/TJX = 0).
4. **Agent responses are explanation-only this wave.** No chart in the chat panel. Charts return in 4.1 as agent-invoked.
5. **No per-competitor peer identity.** The line-item lake exposes only `peer_relationship` ('peer'/'merchant'); the old `peer_a`/`peer_b` pseudonyms are gone. Every peer element shows a single **aggregate same-segment peer**.
6. **No PR, no merge to main.** v4 branch only.

---

## 4. THE CORE MIGRATION — data layer + schema reconciliation

The dashboard's SQLite is a **v3 schema**; its queries reference columns the v4 Parquet does not have. This reconciliation is the heart of Stage 1 and the gate before any rewiring.

**Confirmed v3-query → v4-Parquet mismatches (non-exhaustive — Stage 0 produces the full map):**

| Dashboard query column (v3 SQLite) | v4 Parquet actually has |
|---|---|
| `tenant_transactions.txn_total` | `transactions.subtotal` |
| `tenant_transactions.merchant_id` | `transactions.banner_code` |
| `tenant_customers.customer_id` | `customers.card_id` |
| `tenant_customers.home_zip5` | `customers.home_zone` (no ZIP in v4) |

**Approach:** register each v4 Parquet as a DuckDB **aliasing view** under the table name the queries already use, renaming columns in the view so most query SQL is unchanged:

```sql
CREATE VIEW tenant_transactions AS
  SELECT txn_id, banner_code AS merchant_id, store_id, txn_ts,
         subtotal AS txn_total, discount_total, n_lines, customer_token, ...
  FROM read_parquet('data/raw/transactions.parquet');
CREATE VIEW tenant_customers AS
  SELECT card_id AS customer_id, affluence, loyalty_type, home_zone, ...
  FROM read_parquet('data/raw/customers.parquet');
-- tenant_stores, tenant_products, tenant_transaction_items similarly.
```

**Stage 0 deliverable (NO CODE; hard gate):** a **column-by-column map** of every column every dashboard query reads → its v4 Parquet source (or "no v4 equivalent"). A missed rename is a silent wrong-number bug. Build the aliasing-view definitions from this map. Confirm `data/payments.db` has no other readers and can be deleted.

**SQL dialect:** the queries use only `DATE()`, `strftime`, `SUBSTR` — all DuckDB-native; the dialect pass is small. Native v4 timestamp types (`txn_ts`) make date logic cleaner.

---

## 5. Tenant KPIs + performance (caching, formatting)

- Most own-merchant queries port unchanged once the aliasing views exist; they return bigger numbers / more rows.
- **Full-scale volume (pinned):** 100,000 customers / 1,660,732 transactions / 10,764,855 line items. Window Mar 1 – May 29, 2026.
- **No scale/blob risk:** every chart aggregates to neighborhood/category/week (Plotly bars/lines/heatmap; Folium neighborhood polygons + ~5–24 store markers). No per-row scatter. The audit is therefore **KPI-formatting only**: confirm card magnitudes/abbreviations ($4.6M etc.) read well and aren't truncated.
- **DuckDB connection lifecycle:** use an in-memory `duckdb.connect()` per query (cheap; register the views once via an `st.cache_resource` connection helper) — do not share one connection across Streamlit reruns/threads.
- **Cache-key correctness (must-have):** every cached query MUST include the active **viewer** in its cache key (each `st.cache_data` function takes the viewer as an explicit argument). A query keyed only on SQL text serves one merchant's data to another when the selector switches — a silent demo-breaker.

---

## 6. Geography, the map, and the peer cards

- The map and any neighborhood breakdown use real `neighborhood` names. **Already aligned:** tenant `stores.neighborhood` and lake `lake_stores.neighborhood` are the same 8 names (Ballantyne, Cabarrus Edge, Center City, Dilworth, Eastway, Matthews, NoDa, University City); no Z-codes anywhere. One-line confirm, no reconciliation work.
- **The two peer cards → aggregate-peer.** Only two main-dashboard elements use peer data: **Card 3.1** neighborhood-performance map (`neighborhood_performance`) and **Card 3.2** store table (`store_anomalies`). Rewire both to query the line-item lake at `data/lake/items/<VIEWER>/lake_transactions.parquet ⋈ lake_stores.parquet` via DuckDB, `peer_relationship='peer'`, single **aggregate** peer (no `peer_a`/`peer_b`), with the **k=5 floor** (`HAVING COUNT(DISTINCT lake_txn_id) >= 5`). Gate on `segment_peer_count` (0 → own-only, no peer overlay). This fixes the KRG/ACM/WDX crash and the TBL/TJX no-peer path.
- **Improvement (in-scope, low-lift):** `neighborhood_performance` already computes a `peer_signal` (market-wide vs operational) that the map never shows. Surface it in the tooltip so "is it me or the market?" is visible.
- **Card 5.3 / customer geography → REDESIGN to "where customers shop".** The v4 data has **no home address** (no ZIP; `home_zone` is a planted zone code with no neighborhood mapping), so the v3 home-ZIP→neighborhood rollup is impossible. Redesign: per-neighborhood count of **distinct customers who transacted at a store there** (`transactions JOIN stores ON store_id`, `GROUP BY stores.neighborhood`, distinct `card_id`). Keeps the section + the customer-geography map. **Caveat to state:** the v3 "under-served neighborhood" sub-insight needed home location and is **dropped** (a customer counts in the neighborhood they shop in, which you already have a store in). The real "demand but no own store" signal lives in the peer lake (peers operate where you don't) — noted for a future peer-aware expansion view, out of scope here.

---

## 7. The agent panel (the "ask the agent" experience)

- The chat panel calls the same specialists + advisor on the Wave 3.5 SQL stack (`query_tenant` + `query_lake_sql`; grounding intact). Chat history, pills, free-form input, "thinking" indicator stay as v3.
- **Fix the broken response glue (must-have).** `src/dashboard/agents.py` calls `AgentResponse.to_dict()`, which does not exist → `AttributeError` on any question. Add `AgentResponse.to_dict()` (or build the dict in dispatch) mapping the structured contract → the renderer's keys: `headline`, `evidence`, `so_what`, `prose` (the derived property), `caveats`, a `table` from `resp.result`, `sql`, `telemetry`.
- **Render the structured answer (must-have).** The v3 renderer reads a single `prose` string; the Wave 3.5 contract is structured. Update `chat.py`'s renderer to show **headline + evidence bullets + so-what** + the result table — mirror `scripts/preview_agent.py::_render_structured_answer`. Keep the graceful no-chart skip (`_render_chart(None)` already returns cleanly).
- **Remove the hard-coded per-pill chart system (must-have).** Delete `chat.py::QUESTION_RENDERERS`, `_render_question_chart`, and the per-pill `_render_*` renderer functions; delete the `data.py` peer functions used ONLY by them (verify each: `uc_decline_trajectory`, `category_peer_pricing_gaps`, `staple_vs_nonfood_pricing`, `category_pricing_leverage`, `basket_mix_vs_peers`, `category_share_vs_peer_share`, `revenue_gap_decomposition`, `expansion_opportunity`, `category_anomalies`). Keep `neighborhood_performance` + `store_anomalies` (they feed the main dashboard, §6). Keep `chart_patterns.py`.
- **Per-viewer peer availability in pills:** drive from `segment_peer_count`. Grocers (KRG/ACM/WDX): full peer comparison; peer pills work. TBL (qsr) / TJX (off_price): hide or relabel peer-pricing pills (don't show a pill that always declines); pricing answers state "no comparable peers," trend/trade answers go cross-segment-labeled — the §6 routing already does this server-side. `questions.py` currently has NO per-viewer logic; add it.
- **Streaming:** `specialist.answer`'s `on_token` is a no-op (Wave 3.5 left `call_with_tools_streaming` unwired); `chat.py` has a dead token accumulator. Optional: wire a basic thinking indicator now; full streaming later.

---

## 8. Build stages

> **Rollback discipline:** the dashboard must be runnable (`streamlit run src/dashboard/app.py`) by the end of Stage 1 and after every stage thereafter. If a stage regresses a section that worked, STOP and report. Each stage is its own commit.

**Stage 0 — Schema reconciliation + inventory (NO CODE; hard gate).**
Produce the column-by-column v3-query → v4-Parquet map (§4) and the aliasing-view definitions. Inventory every chart/KPI/card with its data source + a KEEP / REWIRE-PEER / REDESIGN tag (Card 5.3 = REDESIGN; Cards 3.1/3.2 = REWIRE-PEER; the rest KEEP). Confirm `payments.db` has no other readers. Nothing is built until the map is complete.

**Stage 1 — DuckDB data-layer migration.**
Swap `data.py::_conn()` to DuckDB; create the aliasing views; remove the broken `src.lake` import + the dead `lake_txns_filtered`; viewer-keyed caching; small dialect pass. Delete `payments.db`. **Gate:** `streamlit run` works; own-merchant view correct for all 5 viewers; switching viewers shows the right merchant's data (cache-key check). (The two peer cards may still be own-only here.)

**Stage 2 — Remove the hard-coded pattern-chart system.**
Delete `QUESTION_RENDERERS` + `_render_question_chart` + the per-pill `_render_*` renderers in `chat.py`, and the pattern-only `data.py` peer functions (verify usage first). Keep `chart_patterns.py`. **Gate:** dashboard still runs; chat panel renders text answers with no chart slot/error.

**Stage 3 — Agent-panel rewire.**
Fix `agents.py` `.to_dict()`; render the structured `headline`/`evidence`/`so_what` + table; per-viewer pills via `segment_peer_count`. **Gate:** grocer viewers get real-dollar peer answers; TBL/TJX get correct decline/cross-segment behavior; all responses are prose + structured + table, no chart, no render error.

**Stage 4 — Peer cards + Card 5.3.**
Rewire Card 3.1 map + Card 3.2 table to aggregate-peer from `data/lake/items/` (k=5, `segment_peer_count` gate); surface `peer_signal` in the map tooltip. Redesign Card 5.3 to "where customers shop." **Gate:** map + store table legible for all 5 viewers (peers for grocers, own-only for TBL/TJX); Card 5.3 shows customer draw by store neighborhood.

**Stage 5 — Demo re-validation + defensibility + docs.**
Run the demo flow as each of the 5 viewers. UC decline must remain visible (now via the neighborhood map + the agent's prose answer to "why is University City declining?"). Replace the dead v3 "peer_a inversion" beat with the **real-dollar same-segment peer-compare inversion** (switch KRG→ACM, the real-dollar peer comparison flips). Rewrite the demo script (the archived `docs/archive/DEMO_SCRIPT_AGENTS.md` is v3-flavored). Create `src/dashboard/CLAUDE.md`. Commit to v4, push.

---

## 9. Verification gate (what "done" means)

- Dashboard runs for **all 5 viewers**; UI/layout/styling identical to v3 (no visual regression).
- Data layer is DuckDB-on-Parquet; `payments.db` removed; the broken `src.lake` import gone; the v3→v4 column map applied (every KPI reads the correct v4 value).
- Switching the merchant selector shows the correct merchant's data (no cache-key bleed).
- Map + store table show **aggregate same-segment peers** with k=5 for grocers, own-only for TBL/TJX; map tooltip surfaces the peer signal.
- Card 5.3 shows "where customers shop" (distinct customers by store neighborhood).
- Agent panel: structured answer (headline / evidence / so-what) + result table, **no chart**, no `.to_dict` error; grocers get real-dollar peer answers; TBL/TJX correct; pills reflect peer availability.
- The hard-coded per-pill chart system is removed; `chart_patterns.py` kept; `chart_build.py` untouched (still dormant).
- Planted UC decline still visible (map + prose).
- Demo walkthrough re-validated + rewritten for the new peer behavior and no-chart responses; runs clean as all 5 viewers; nothing visibly indefensible.
- `src/dashboard/CLAUDE.md` created. Commit to v4, push. NO PR, NO merge to main.

---

## 10. Open questions (most are answered; resolve any remaining before Stage 1)

1. **No-peer pills for TBL/TJX:** hide peer-pricing pills entirely (recommended), or relabel and let the agent decline? Pick in Stage 3.
2. **Streaming:** wire a basic thinking indicator now (cheap, good UX) or defer? Recommendation: basic now, full streaming later.
3. *(Answered)* Volume = full scale (100k / 1.66M / 10.7M). Neighborhoods already aligned. UC decline present (~35% WDX). Card 5.3 → redesigned to "where customers shop."

---

## 11. Wave 4.1 fast-follow (explicitly NEXT, not now)

- **Charts return to agent responses as agent-invoked helpers.** Re-enable `chart_build.py` (flip `CHARTS_ENABLED`): the model authors a chart intent and the server builds the figure deterministically from the result frame's columns (no model-supplied figure values). The hard-coded per-pill chart system stays **removed** — charts generalize across pill and free-form questions via the model, not a fixed registry. Prefer a small set of server-side chart templates the model selects from for known response shapes (e.g., the UC-decline time-series) over fully free-form intents.

---

## 12. Explicitly NOT in Wave 4

- No charts in agent responses (Wave 4.1, agent-invoked).
- No hard-coded per-pill charts (removed for good).
- No UI redesign — structure and styling stay as v3.
- No new agents (Payment-Optimization / Segmentation remain deferred; ride through the advisor).
- No production privacy posture change (k=5 line-item lake stays; Wave 3.5 strategy-doc note stands).
- No merge to main.
