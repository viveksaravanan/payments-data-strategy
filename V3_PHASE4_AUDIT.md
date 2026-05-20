# V3 Phase 4 — Dashboard Audit

Audit run 2026-05-19. Read-only inspection of the v2.5 dashboard
in `src/dashboard/` to inform the Phase 4 redesign per the design
conversation locked in chat. `V3_DASHBOARD_DESIGN.md` is drafted
next; this audit is its input.

---

## Section 1: Current dashboard structure

**Entry points.** `streamlit_app.py` (35 lines, repo root) is the HF
Spaces wrapper — pulls `ANTHROPIC_API_KEY` from Streamlit secrets,
verifies the LFS-tracked DB exists, then `runpy.run_path`s into
`src/dashboard/app.py`. Local `make demo` invokes the dashboard
module directly via `uv run streamlit run src/dashboard/app.py`.

**Six dashboard modules** in `src/dashboard/` (3,829 LOC total
including styling CSS):

| File | LOC | Purpose |
|---|---:|---|
| `app.py` | 230 | Page orchestration: header, filter row, two-column split (65/35), expand-to-full-width chat mode |
| `chat.py` | 408 | Right-rail chat panel — specialist selector, suggested-Q buttons, scrollable history, free-form `chat_input` |
| `views.py` | 676 | All dashboard card renderers (KPIs, map, charts, expanders) |
| `data.py` | 778 | `@st.cache_data` query helpers — tenant + lake, filter-aware |
| `placeholders.py` | 1,441 | Suggested-Q registry by segment + 16 hardcoded handler bodies + LLM dispatch + orchestrator routing |
| `styling.py` | 251 | Single CSS injection (`styling.inject()`) |

**Page flow when a merchant lands.**

1. `streamlit_app.py` → `app.py`. `styling.inject()` injects all CSS.
2. Header: `<h1>Merchant dashboard</h1>` + subtitle on the left,
   `Acting as` merchant selectbox on the right (KRG/ACM/WDX/TBL/TJX).
3. Filter row (skipped when chat is expanded): date range,
   stores multiselect, categories multiselect. State is keyed by
   merchant in `st.session_state.filters_by_merchant[mid]` so each
   merchant has its own filter dict.
4. Two-column layout (`st.columns([65, 35])`):
   - Left (dashboard column): KPI row → map+insights (60/40) →
     category mix + store performance (50/50) → payment
     intelligence (4-up) → time patterns → customer engagement
     expander.
   - Right (chat column): `chat.render_chat_panel(mid)` +
     telemetry footer.
5. When `state.chat_expanded == True`, the dashboard column is
   skipped entirely; chat takes the full viewport width.

**Per-merchant session-state isolation:**
- `state.merchant_id` — currently selected merchant.
- `state.filters_by_merchant[mid]` — dict of per-merchant filters.
- `state.chat_messages_by_merchant[mid]` — chronological turns;
  never crosses merchants.
- `state.active_agent` — currently selected specialist (shared
  across merchants by design).
- `state.chat_expanded`, `state.agent_running`, `state.pending_dispatch`
  — UI/flow control flags.

---

## Section 2: Chart inventory

Every chart currently rendered in the left column, in render order.
Mapping column shows the v3 dashboard card per the chat-locked
design (1.1–5.3) and STAYS/REWORKED/NEW/NOT IN V3.

| # | Current chart | Where (`views.py` fn) | Data source | Library | v3 mapping |
|---|---|---|---|---|---|
| 1 | KPI: Revenue (90d) + 30d-vs-prior-30d delta | `render_kpi_row` → `data.kpi_block` | `tenant_transactions.txn_total` (or line-item path when category filter active) | Streamlit HTML+CSS | **1.1 Revenue KPI** — STAYS |
| 2 | KPI: Transactions (90d) + delta | `render_kpi_row` → `data.kpi_block` | `COUNT(DISTINCT t.txn_id)` | Streamlit HTML+CSS | **1.2 Transactions KPI** — STAYS |
| 3 | KPI: Avg transaction + delta | `render_kpi_row` → `data.kpi_block` | revenue ÷ txns | Streamlit HTML+CSS | **1.3 Avg basket KPI** — STAYS (label change "Avg transaction" → "Avg basket") |
| 4 | KPI: Active customers + delta | `render_kpi_row` → `data.kpi_block` | `COUNT(DISTINCT customer_id)` | Streamlit HTML+CSS | **1.4 Unique customers KPI** — STAYS |
| — | _(no anomaly-count KPI today)_ | — | — | — | **1.5 Anomaly count KPI** — NEW |
| 5 | Stores map — own-merchant circles colored by 90-day txn volume | `render_map` → `data.stores_for` | `tenant_stores` + `tenant_transactions` aggregate | Folium + streamlit-folium (CartoDB positron tiles, LinearColormap accent-light → accent) | **3.1 Neighborhood performance map** — REWORKED (today's map is per-store; v3 wants per-neighborhood performance with peer context) |
| 6 | Daily transactions sparkline | `render_insights_panel` → `data.daily_volume` | `tenant_transactions` grouped by `DATE(txn_ts)` | Plotly Scatter with fill-to-zero | **2.2 Transaction trajectory** — REWORKED (no peer overlay today; v3 wants own + peer_a + peer_b) |
| 7 | Top 5 SKUs by revenue (hbar) | `render_insights_panel` → `data.top_skus` | `tenant_transaction_items` joined to `tenant_products` | Plotly Bar (horizontal) | **4.2 SKU performance** — REWORKED (today is top-5 only; v3 wants top/bottom toggle) |
| 8 | Revenue by category (donut) | `render_category_mix` → `data.category_mix` | `tenant_transaction_items × tenant_products` | Plotly Pie (donut, palette of 13) | **4.1 Category mix** — REWORKED (donut today; v3 patterns favor diverging bar — Pattern 2 — when peer comparison is added, or pure share bar for own-only) |
| 9 | Stores by 90-day txns (hbar) | `render_store_performance` → `data.store_performance` | `tenant_stores × tenant_transactions` | Plotly Bar (horizontal) | **3.2 Store performance distribution** — STAYS (close enough; may add peer-baseline overlay or distribution-shape framing) |
| 10 | Payment method donut (credit/debit) | `render_payment_intelligence` → `_render_payment_method` → `data.payment_method_mix` | `tenant_transactions.payment_type` | Plotly Pie (donut) | **NOT IN V3** — entire payment-intelligence section removed |
| 11 | Card network donut (visa/mc/amex/discover) | `_render_card_network` → `data.card_network_mix` | `tenant_transactions.card_network` | Plotly Pie (donut) | **NOT IN V3** |
| 12 | Entry mode over time (stacked area) | `_render_entry_mode_trend` → `data.entry_mode_trend` | `tenant_transactions.entry_mode` × day | Plotly stacked Scatter | **NOT IN V3** |
| 13 | Mobile wallet adoption (KPI + Apple/Google/Samsung hbar) | `_render_wallet_adoption` → `data.wallet_adoption` | `tenant_transactions.wallet_type` filtered to contactless | HTML KPI + Plotly Bar | **NOT IN V3** |
| 14 | Hour × day-of-week heatmap | `render_time_patterns` → `data.hour_dow_heatmap` | `strftime('%w', txn_ts)` × `SUBSTR(txn_ts, 12, 2)` | Plotly Heatmap (white → accent-light → accent) | **2.3 Hour × day-of-week heatmap** — STAYS (own-merchant; v3 doesn't add peer overlay here) |
| 15 | Transactions per customer (hbar in expander) | `render_customer_engagement` → `_render_txn_freq` | `tenant_transactions` grouped by customer count buckets (1, 2-3, 4-6, 7-10, 11+) | Plotly Bar | **5.2 Transactions per customer** — STAYS |
| 16 | Customer recency (active 30d / lapsed pair of numbers) | `_render_recency` | `COUNT(DISTINCT customer_id) WHERE DATE >= 2026-04-30` | Streamlit HTML | **NOT IN V3** |
| 17 | Revenue concentration (top-10/20/50 share hbar) | `_render_revenue_concentration` | Per-customer rev rank with window functions | Plotly Bar | **NOT IN V3** |
| 18 | Top promos by redemption rate (hbar) | `_render_promo_redemption` | `tenant_promotions × tenant_transaction_items` | Plotly Bar | **NOT IN V3** |
| — | _(no revenue-trajectory chart with peer overlay today)_ | — | — | — | **2.1 Revenue trajectory** — NEW |
| — | _(no new-vs-returning customer mix chart)_ | — | — | — | **5.1 New-vs-returning customer mix** — NEW |
| — | _(no customer-home geographic chart)_ | — | — | — | **5.3 Customer home geography** — NEW |

**Mapping summary:** of the 18 currently-rendered charts/KPIs,
**6 STAY** (the 4 KPIs, store hbar, hour×DOW heatmap, transactions-per-
customer); **5 are REWORKED** (the map, sparkline becomes the
transaction trajectory, category mix, SKU top-5 → top/bottom
toggle); **9 are NOT IN V3** (4 payment-intelligence panels +
3 customer-engagement subpanels + the customer-recency stat + the
daily-volume sparkline if we strictly treat "transaction
trajectory" as the v3 replacement). **Four NEW cards** (anomaly KPI,
revenue trajectory with peer overlay, new-vs-returning customer
mix, customer-home geography).

---

## Section 3: Chat panel implementation

**Where it lives.** `chat.render_chat_panel(merchant_id)` is the
sole entry point. Called from two places in `app.py`:
- `chat_expanded == True` → full-width.
- `chat_expanded == False` → rendered into the right column at 35%.

**What it renders, top to bottom (`chat.py:234`–`409`):**
1. Header row: `#### Ask the data` (left), expand toggle ⤢/⤡
   (icon), clear-history 🗑 (icon).
2. Specialist selector — **`st.selectbox`** (dropdown, not chips
   today) with `["demand", "pricing", "anomaly", "trade"]`.
3. Caption with `AGENT_DESCRIPTIONS[active_agent]` — one sentence.
4. `st.markdown("---")` divider.
5. **Three suggested-question buttons** (`questions_for(merchant_id)
   [active_agent]`), full-width `st.button`s styled as gray pills
   by `styling.py`. Click enqueues a `state.pending_dispatch` and
   reruns.
6. Another `---` divider.
7. Scrollable `st.container(height=700, border=True)` holding
   the chat history and the live-streaming bubble.
8. `---` divider, then `st.chat_input("Ask anything…")` at the
   bottom — routes through `placeholders.dispatch_orchestrated`.

**How a question gets routed.**
- Suggested-Q click → `state.pending_dispatch = {"kind": "question", …}`
  → rerun → next pass enters the `pending` branch inside the
  history container → calls `placeholders.dispatch(agent_id, qid,
  merchant_id, progress=…, on_token=…)` → response is appended to
  history.
- Free-form submit → `state.pending_dispatch = {"kind": "free", …}`
  → `placeholders.dispatch_orchestrated(merchant_id, question, …)`
  → routes via Haiku router (with keyword fallback) to one of the
  four specialists, then calls `dispatch_free_form` which invokes
  the `_llm_dispatch` path with the raw question text.

**Two-rerun deferred-dispatch pattern** (`chat.py:319`–`365`): the
button click sets `pending_dispatch` and `agent_running = True`
but doesn't run the agent in that pass. Streamlit reruns; the
second pass renders every control with `disabled=is_running`
(buttons, selectbox, chat_input all greyed) BEFORE the dispatch
fires inside the chat container. This prevents the user from
clicking a different button mid-stream and aborting the in-flight
agent.

**Live streaming.** `_render_live_turn` writes a user bubble then
opens an assistant bubble with `st.empty()` placeholder. Two
callbacks are passed into the runner:
- `on_progress(turn, msg)` — narration before the model starts
  streaming (e.g., "fetching peer prices…").
- `on_token(text)` — incremental render of streamed model output.
  Text is `$`-escaped to prevent LaTeX-math interpretation; a cut
  heuristic (`_streaming_cut_index`) hides the trailing
  ```` ```caveats ```` block while streaming so the JSON tail
  doesn't leak into the bubble.

After the runner returns: placeholder is overwritten with the
final caption + cleaned prose; caveats / chart / table are
rendered as siblings; history is pushed; `pending_dispatch =
None`; `agent_running = False`; another rerun finalizes.

**"Ask about this" from charts** — **not implemented today**. Every
chat invocation flows through the suggested-Q buttons or the
free-form `chat_input`. Dashboard cards have no buttons that
pre-fill the chat with a context-aware question + auto-snap the
specialist switcher.

**Merchant context flow.** `merchant_id` is passed into
`render_chat_panel(mid)`; into every handler via `dispatch(agent_id,
qid, merchant_id, …)`; and into the LLM specialists via
`MerchantContext.for_merchant(merchant_id)` constructed in
`_llm_dispatch`. The agent's `query_tenant` tool then enforces
isolation via the per-viewer tenant view (`tenant_view_<viewer>_*`,
Phase 1.5).

**v3 mapping:**

| v3 design item | Current state |
|---|---|
| Persistent right-rail chat panel | **STAYS** — architecture is already this shape; 65/35 split + expand mode work fine |
| Specialist switcher (chips for pricing/anomaly/demand/trade) | **REWORKED** — currently a `selectbox` dropdown; v3 wants chips. Order today is `demand/pricing/anomaly/trade`; v3 ordering (per `V3_QUESTIONS.md`) is `pricing/anomaly/demand/trade` |
| 3 suggested questions per active specialist | **STAYS** structurally (already 3 per specialist), but **question text is REWORKED** — current questions don't match the 12 finals from `V3_QUESTIONS.md`. Today's text is also segment-overridden (separate sets for grocery / QSR / retail); v3's 12 finals are cross-segment |
| Scrollable chat history | **STAYS** — `st.container(height=700, border=True)` is fine |
| "Ask about this" from any card → routes to specialist + pre-fills context-aware question + auto-snaps switcher | **NEW** — wholly new pattern. Needs a per-card button affordance, a pre-fill mechanism (presumably extends the `pending_dispatch` shape), and a way to programmatically set `state.active_agent` from a button outside the chat panel |

---

## Section 4: Styling, theming, palette

**Theme config** (`.streamlit/config.toml`):
- `primaryColor = "#0F4C81"` (the accent / KRG brand color)
- `backgroundColor = "#FFFFFF"` / `secondaryBackgroundColor = "#F7F8FA"`
- `textColor = "#1A1F2E"` / system sans-serif

**CSS variables in `styling.py`** (single `<style>` block injected
at page top):

```
--accent:       #0F4C81   ← own-merchant brand baseline
--accent-soft:  #D8E2EE
--surface:      #F7F8FA
--border:       #E2E5EA
--text / text-2 / text-muted
--anomaly:      #C44536
--c-krg / c-acm / c-wdx / c-tbl / c-tjx  (per-merchant)
--good:         #2F855A
--bad:          #C44536
```

**Component primitives** (CSS):
- `.kpi` — the 4 KPI cards with `.num` / `.label` / `.delta` /
  optional `.hint`. Solid; consistent rhythm.
- `.panel-card` — generic chart-wrapping card with `.panel-title`
  / `.panel-sub`. Used by every chart panel in `views.py`.
- `.agent-card` — chat suggestion panel (unused at the moment;
  the buttons live in `chat.py` outside an explicit agent-card
  wrapper, but the styling rules target Streamlit's per-widget
  `st-key-*` classes).
- Per-widget styling for `st-key-expand_btn_*` and
  `st-key-clear_btn_*` (the chat header icon buttons).

**Plotly layout helper** (`views._plotly_layout`): standardizes
font, hover styling, grid color, axis styling. Used by every
Plotly chart in `views.py`. Returns a dict that callers `**`-spread
into `fig.update_layout`. Custom overrides per chart.

**Folium pattern** (`views.render_map`): CartoDB positron basemap +
`LinearColormap([ACCENT_LIGHT, ACCENT], vmin, vmax)` for store
markers + tooltips with HTML. Map key is derived from selected
stores tuple so the map only re-renders when the selection
changes.

**Per-merchant brand color usage:** `data.MERCHANT_COLOR` is the
canonical map. Used in the daily-volume sparkline (`render_insights_panel`)
and the store-performance hbar (`render_store_performance`). It is
**not** used in the category-mix donut (which uses a 13-color
categorical palette `_CATEGORY_PALETTE`), the time-patterns
heatmap (which uses an accent-light → accent gradient), or the
KPI cards (which use `--accent` directly).

**v3 encoding gaps:**

| v3 encoding rule | Current state |
|---|---|
| Own merchant: brand color, solid | **Partial** — `MERCHANT_COLOR` exists, used in 2 charts. Other charts use the global `--accent` directly without per-merchant variation, or don't apply own-merchant coloring at all (donut). Needs systematic application to every primary chart |
| Peers: gray family (peer_a darker, peer_b lighter) | **Missing** — no peer overlays exist in the dashboard today. The "peer = gray family" convention has no precedent in the current codebase |
| Diverging encodings (red / white / blue, white at zero) | **Missing** — no diverging palette defined. Pattern 3 (heatmap) and Pattern 5 (waterfall) need this |
| Sequential encodings (light → dark, brand-family) | **Partial** — time-patterns heatmap uses `[ACCENT_LIGHT, ACCENT]` (a 2-stop gradient, sequential). The map uses the same 2-stop colormap. A proper light-to-dark scale needs more stops for the more-saturated v3 charts |

The good news: the design vocabulary is in place — CSS variables,
per-merchant brand colors, panel-card primitive, Plotly layout
helper — so the new encodings can be added without a wholesale
rewrite of the styling layer.

---

## Section 5: What stays, what gets replaced

By file. "Reworked" = file remains but significant chunks change;
"Replaced" = file is rewritten or split.

| File | LOC today | Disposition | Notes |
|---|---:|---|---|
| `__init__.py` | 0 | **STAYS** | Empty module marker |
| `streamlit_app.py` (root) | 35 | **STAYS** | HF Spaces entry point; no v3 changes |
| `.streamlit/config.toml` | 10 | **STAYS** | Theme baseline unchanged |
| `app.py` | 230 | **REWORKED** | Header / merchant selectbox / filter row stay. The left-column card layout is replaced to match v3's grouped sections (1.x KPIs, 2.x trajectories, 3.x geographic, 4.x catalog, 5.x customers). Expand-mode logic stays. Estimate ~100 LOC churn |
| `chat.py` | 408 | **REWORKED** | Two-rerun deferred-dispatch pattern stays. Streaming + caveats stripping stay. Replace the `selectbox` with a chip selector; the chip-click handler adopts the existing `state.active_agent` setter. Add the "ask about this" entry path — a new `pending_dispatch` kind that arrives from outside the chat panel and pre-fills the chat input with merchant + chart context. Estimate ~120 LOC churn |
| `data.py` | 778 | **REWORKED** | Tenant query helpers stay (kpi_block, stores_for, categories_for, daily_volume, top_skus, category_mix, store_performance, customer_engagement, hour_dow_heatmap). Payment intelligence queries (`payment_method_mix`, `card_network_mix`, `entry_mode_trend`, `wallet_adoption`) become dead code — remove (~250 LOC). Add new helpers for the 4 NEW v3 cards (anomaly count, revenue trajectory w/ peer overlay, new-vs-returning, customer-home geography) and per-peer variants for the cards that get peer overlays. Estimate ~250 LOC out, ~250 LOC in |
| `views.py` | 676 | **REPLACED** | Conceptually replaced by one helper per chart pattern (`chart_patterns.md`'s 9 patterns → ~9 helpers) plus thin per-card renderers calling those helpers. Current per-chart function bodies (e.g., `_render_card_network`, `_render_wallet_adoption`, `_render_revenue_concentration`, `_render_promo_redemption`) are not reusable for v3 cards. The `_plotly_layout` helper and the color/format helpers (`_fmt_money`, `_fmt_int`, `_fmt_pct`, `PLOTLY_CONFIG`) stay and likely move into the new pattern-helpers module. Estimate full rewrite, ~500 LOC of new pattern-helper code replacing the current 676 |
| `placeholders.py` | 1,441 | **REPLACED** | The hardcoded handler bodies (~900 LOC across `h_pricing_*`, `h_anomaly_*`, `h_demand_*`, `h_trade_*`) were Phase 1 stubs to make the dashboard functional before LLM specialists existed. Today's flow is: `dispatch` runs the LLM specialist when available, falls back to the hardcoded handler on LLM failure. Phase 4 either (a) keeps hardcoded fallback for the 12 finals (rewrite handlers to match new question shapes) or (b) drops mock fallback entirely. The question-text registry (~200 LOC for QUESTIONS_GROCERY / QSR / RETAIL) is replaced by the 12 finals from `V3_QUESTIONS.md` (single cross-segment set, possibly with segment overrides for TBL/TJX). LLM dispatch + orchestration routing (~300 LOC for `_llm_dispatch`, `dispatch_orchestrated`, `_keyword_route_for_fallback`, session-state cache) stay |
| `styling.py` | 251 | **REWORKED** | CSS variables stay. KPI card + panel-card primitives stay. Add: specialist chip pill styles, "ask about this" affordance, per-peer line/bar gray-family styles, diverging-encoding helpers. Remove: any payment-intelligence-specific selectors (none today — the wallet_adoption custom HTML inlines styles). Estimate ~50 LOC additions |

**Net code shape estimate:** today 3,829 LOC in `src/dashboard/`;
Phase 4 likely lands at ~3,000 LOC (smaller after dropping the
payment-intelligence + customer-engagement-not-in-v3 bodies and
the verbose hardcoded handlers, even with new pattern helpers
and new cards).

---

## Section 6: Risks and unknowns

**Performance.**

- **Folium map re-renders are expensive.** `st_folium` with key
  derived from `tuple(sorted(selected))` already avoids
  unnecessary re-renders; v3's per-neighborhood map needs the
  same care. Adding peer-store overlays to T1/T2/T4 maps will
  triple marker counts — confirm `st_folium` handles ~150 markers
  comfortably (5 grocers × ~30 stores).
- **KPI block runs 3 windows × N queries per call.** Cached via
  `@st.cache_data` keyed on the filter tuple. The 30-day-vs-prior
  delta logic is fast (~50 ms per fetch) — no v3 concern.
- **Lake queries already materialized** (Phase 1.5). Cross-merchant
  queries hit `lake_transactions_<viewer>` physical tables;
  measured 126–298 ms in the Phase 1.5 close-out. Adding peer-
  overlay queries to 4-5 more cards is in budget.
- **Hour × DOW heatmap** scans all transactions per call. Cached.
  At 230K tenant rows it's fast (~80 ms uncached); no concern.

**Coupling that complicates the redesign.**

- **`placeholders.py` is monolithic (1,441 lines).** It holds
  three logically separate concerns: (1) the suggested-question
  registry, (2) the hardcoded handler bodies, (3) LLM dispatch +
  orchestration. Phase 4 should split this — the dispatch +
  orchestration logic belongs in a `src/dashboard/agents.py` (or
  alongside the specialists in `src/agents/`); the question
  registry belongs in a small `src/dashboard/questions.py` driven
  by `V3_QUESTIONS.md`; the handler bodies are likely deleted.
- **`chat.py` depends on `placeholders.questions_for / dispatch /
  dispatch_orchestrated / AGENT_LABELS / AGENT_DESCRIPTIONS`.**
  When `placeholders.py` is split, `chat.py`'s imports update — the
  function contracts (`dispatch(agent_id, qid, merchant_id,
  progress, on_token) -> dict`) should be preserved so the chat
  panel's logic doesn't change.
- **`AGENT_DESCRIPTIONS` text is stale.** Reads like the original
  v2 specialist briefing ("Surfaces slowing SKUs, customers
  likely to respond to promos, and projected uplift" for demand)
  rather than reflecting the 3-final-question scope. Needs
  rewrite to align with `V3_QUESTIONS.md` Section 3.
- **Specialist agent ordering.** `chat.py:297` lists agent ids as
  `["demand", "pricing", "anomaly", "trade"]`; `V3_QUESTIONS.md`
  orders specialists as pricing → anomaly → demand → trade. Either
  align the chat panel ordering or document why the dashboard
  surface ordering differs.

**Unresolved questions about existing functionality.**

- **Mock fallback policy.** Today the LLM dispatch falls back to a
  hardcoded handler when the LLM call fails. The 16 handler
  bodies were Phase 1 work and will be replaced or deleted in
  Phase 4. Decision needed: keep the labeled-fallback pattern
  (rewrite ~12 handler bodies to match the 12 final question
  shapes) or drop mock fallback entirely (LLM-or-error).
- **Suggested-question segment overrides.** Today TBL and TJX
  have own-merchant-only suggested questions because they have
  no same-segment peers in the panel. `V3_QUESTIONS.md`'s 12
  finals are written for grocers (peer comparison is foundational
  to most of them). Phase 4 needs to decide: do TBL/TJX get
  segment-adapted variants, or do they see "this question
  doesn't apply" placeholders for peer-heavy questions?
- **Customer engagement expander vs. v3 5.x cards.** Today's
  customer engagement is an expander (collapsed by default). V3
  promotes customer cards (5.1/5.2/5.3) to primary surface. The
  question is whether the v3 layout still uses an expander
  pattern for the 5.x section or whether they live inline like
  the other cards.

**Anything that might surprise us in Phase 4.**

- **The current map's data shape doesn't match v3 3.1.** Today's
  map plots per-store circles colored by per-store transaction
  volume. V3 3.1 is per-neighborhood performance (a different
  aggregation). `data.stores_for` returns the per-store frame;
  Phase 4 needs a new `data.neighborhood_performance(merchant_id,
  filters_key)` that aggregates by neighborhood + computes
  peer-neighborhood baseline for the cross-merchant context.
- **"Ask about this" requires programmatic specialist switching.**
  The current flow has the merchant click a chip/dropdown to set
  `state.active_agent`. The new pattern needs a card-side button
  to set `state.active_agent` AND inject a pre-filled question
  AND scroll the chat panel into view (in split-mode) or expand
  it (if collapsed). Several state transitions in one user
  gesture; needs careful state-machine design.
- **Telemetry footer assumes `src.agents.llm.session_totals`.**
  `app._render_telemetry_footer` reads `src.agents.llm.session_totals()`
  and renders below the chat panel. The footer is unobtrusive
  and accurate today; v3 likely keeps it. Just noting the
  agent-module dependency exists.
- **The `unsafe_allow_html=True` calls are widespread.** Every
  panel title, every KPI card, every section header uses
  `st.markdown(..., unsafe_allow_html=True)`. This is intentional
  (Streamlit's native markdown is too limited for the executive
  look) but means every new v3 card will need consistent HTML
  structure. The `panel-card` primitive helps; we should keep
  using it rather than introducing new HTML structures per card.
- **Per-merchant chat history survives merchant-switch.** The
  bucket-by-merchant pattern is correct for isolation; just
  confirm v3 UX wants the same — switching merchants doesn't
  reset the conversation, you pick up where you left off in
  each merchant's own history.

---

End of audit.
