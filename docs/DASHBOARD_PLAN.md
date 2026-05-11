# Merchant Dashboard Rebuild — Plan

This plan lives temporarily at `~/.claude/plans/eventual-pondering-widget.md` during planning. On approval it is moved to `docs/DASHBOARD_PLAN.md`.

**Revision log:** Plan reviewed once; revisions incorporated below — heatmap implementation (HeatMap plugin instead of CircleMarker), KPI delta window (30d vs 30d), repeat-customer metric (avg transactions per customer), free-form input hidden in Phase 1, "Active customers" KPI card wired to expand Customer Insights, TBL/TJX Customer Insights specifics defined, and four fully-written sample placeholder responses for sign-off.

---

## Context

The v2.5 codebase shipped (report complete through Phase H). The next workstream is the merchant dashboard: `src/dashboard/app.py` is currently a 230-line role-picker that hands a single canned question to a `MerchantAdvisor` LLM agent. That format is fine for a stand-alone advisor demo, but it doesn't tell an executive viewer the merchant's story — there are no KPIs, no map, no per-merchant trend, and the chat is reset every interaction.

This rebuild produces an **executive-grade BI dashboard with a persistent agent chat panel**. Phase 1 (this workstream) delivers the entire UI plus hardcoded but data-grounded placeholder responses for 16 specialist-agent questions. Phase 2 (a separate workstream) will wire real LLM-backed agents into the same response surface.

Audience: Verifone leadership. Read in 30 seconds of scanning; supports 10-minute walkthroughs. Visual language matches `docs/report.html` exactly: accent `#0F4C81`, surface `#F7F8FA`, system fonts, card-based layout with 1.5px borders and 6px corners.

---

## Library decisions

- **Charts** — `plotly` (the full pip package). The report inlined `plotly-basic` from `vendor/`; for Streamlit we use the pip install which Streamlit renders natively via `st.plotly_chart()`. Trace types used: `scatter` (sparkline), `pie` (donut), `bar` (horizontal). No new vendoring.
- **Map** — `folium` + `streamlit-folium`. Folium wraps Leaflet, so the underlying tech matches the report's "Leaflet + CartoDB Positron tiles." `folium.Map(tiles="CartoDB Positron")` + `folium.CircleMarker` for stores; `streamlit-folium` renders the map inside Streamlit and survives reruns.
- **New pyproject deps**: add `plotly>=5`, `folium>=0.16`, `streamlit-folium>=0.20`. Three lines in `pyproject.toml`; `uv sync` picks them up.

Alternatives considered: raw Leaflet via `st.components.v1.html` (matches report exactly but needs hand-rolled JS for click events and re-renders awkwardly across Streamlit reruns); native `st.map` / `pydeck` (no CartoDB Positron, different aesthetic). Folium is the standard idiom and stays consistent with the report's visual language.

---

## File layout

Six modules under `src/dashboard/`, each under ~250 lines. Replaces today's monolithic `app.py`.

```
src/dashboard/
├── app.py              # entrypoint, page layout, session-state init                 (~200)
├── styling.py          # injected CSS string matching docs/report.html               (~100)
├── data.py             # cached query helpers (KPI rows, charts, map data, etc.)     (~250)
├── views.py            # dashboard renderers: KPI row, map, charts, customer panel   (~280)
├── chat.py             # agent suggestion cards, history view, free-form input        (~180)
└── placeholders.py     # 16 hardcoded handlers; per-segment adaptation                (~280)
```

Total ~1,300 LoC across six modules.

Existing `src/dashboard/app.py` is **completely rewritten**. The advisor agent (`src/agents/advisor.py`) and its tools (`src/agents/tools.py`) stay untouched — Phase 2 will plug them into the placeholder dispatcher.

---

## Layout (Streamlit columns)

```
┌────────────────────────────────────────────────────────────────┐
│ Header: title + merchant selectbox + filter row                │
│ Filters: date range, store multi-select, category multi-select │
└────────────────────────────────────────────────────────────────┘

┌─ Left col (70%) ─────────────────────┐  ┌─ Right col (30%) ──┐
│                                      │  │                    │
│ KPI row (4 cards)                    │  │ ◆ Demand Forecast  │
│ ─────────────────────                │  │   - q1 · q2 · q3 · │
│                                      │  │   - q4             │
│ ┌─ Map (60%) ──┐ ┌─ Insights (40%) ─┐│  │                    │
│ │ Charlotte    │ │ Daily sparkline  ││  │ ◆ Pricing/Bench    │
│ │ + 2 heatmap  │ │ Top-5 SKUs       ││  │   - q1 · q2 · q3 · │
│ │   toggles    │ │                  ││  │   - q4             │
│ └──────────────┘ └──────────────────┘│  │                    │
│                                      │  │ ◆ Anomaly Detect   │
│ ┌─ Category mix ─┐ ┌─ Store perf ──┐ │  │   - q1 · q2 · q3 · │
│ │ donut          │ │ horizontal bar│ │  │   - q4             │
│ └────────────────┘ └───────────────┘ │  │                    │
│                                      │  │ ◆ Trade Area Intel │
│ [+] Customer Insights (expander)     │  │   - q1 · q2 · q3 · │
│      2×2 grid when expanded          │  │   - q4             │
│                                      │  │                    │
│                                      │  │ ─ Chat history ─   │
│                                      │  │ (per-merchant)     │
│                                      │  │                    │
│                                      │  │ ─ Free-form input ─│
│                                      │  │ → "Phase 2 message"│
└──────────────────────────────────────┘  └────────────────────┘
```

On narrow widths: Streamlit's responsive grid stacks the columns naturally.

---

## State + caching strategy

**`st.session_state`:**
- `merchant_id` — currently selected merchant (from the dropdown).
- `filters[merchant_id]` — dict `{date_start, date_end, stores: list, categories: list}`. Reset to defaults when merchant changes.
- `chat_history[merchant_id]` — list of `{ts, agent, question, response_dict}`. Reset when merchant changes (per spec).

**`@st.cache_data(ttl=3600)`** keyed on `(merchant_id, filters_tuple)`:
- `kpi_block(...)` → 4 stats + WoW deltas
- `daily_volume_series(...)` → 90 rows × 2 cols (date, txns) for sparkline
- `top_skus(...)` → top-N SKUs by revenue
- `category_mix(...)` → revenue per category
- `store_performance(...)` → tenant_stores joined with txn counts
- `customer_insights_block(...)` → 4 sub-blocks for the expander

**`@st.cache_data`** keyed on `merchant_id` only (filters irrelevant):
- `merchant_stores(merchant_id)` → list of `{store_id, lat, lng, neighborhood, n_txns_90d}`
- `peer_density_by_neighborhood(merchant_id, same_segment_only)` → `{neighborhood: peer_store_count}`

**`@st.cache_resource`:**
- `_db_connection()` — single read-only sqlite connection reused across cached queries.

---

## Data sources (reuses existing infrastructure)

All queries hit `data/payments.db` directly via raw SQLite or via the lake view-builders. No agent calls in Phase 1.

| What | How |
|---|---|
| Own-merchant data (KPIs, charts, customer panel, own stores) | Direct `SELECT … FROM tenant_*` with `WHERE merchant_id = ?` |
| Peer pricing for placeholder responses | `src.lake.get_lake_transactions(viewing_merchant_id)` → pandas DF, group by `peer_id` |
| Peer store density for heatmap | `src.lake.get_lake_stores(viewing_merchant_id)` → group by `neighborhood`, optionally filter `peer_segment == own_segment` |
| Peer mapping for placeholder labels | `src.lake.peer_mapping.build_peer_mapping(viewing_merchant_id)` |
| Neighborhood centroids for heatmap | Computed once from `tenant_stores.lat/lng` averaged per neighborhood, cached |
| Anomaly query patterns | Reuse SQL from `scripts/generate_report_data.py::_anomaly_series` (lines 1086–1181) for the three anomaly placeholders |

Date range is fixed by the data (`2026-03-01` → `2026-05-29`, 90 days). "Week-over-week" KPI delta compares the last 7 days vs the prior 7.

---

## KPI cards (4 stats × 30-day delta)

| KPI | Query | 30-day delta |
|---|---|---|
| Revenue (90d) | `SUM(line_total)` from `tenant_transaction_items` joined to `tenant_transactions` filtered by merchant + date range | last-30d revenue / prior-30d revenue − 1 |
| Transactions (90d) | `COUNT(DISTINCT txn_id)` from `tenant_transactions` | last-30d / prior-30d |
| Avg ticket | `AVG(txn_total)` | last-30d / prior-30d |
| Active customers | `COUNT(DISTINCT customer_id)` | last-30d / prior-30d |

Each card: large number + label + 30-day delta arrow ▲/▼ with %. Visual styling matches the report's `.stat-row` cards from `docs/report.html`. Delta label reads "vs prior 30d" so the comparison window is unambiguous.

**At least one KPI is wired for cross-filtering** (per spec): the **Active customers** card is rendered as `st.button` and clicking it sets `st.session_state.customer_insights_open = True`, which auto-expands the Customer Insights expander below. Demonstrates the click-through pattern without requiring all four cards to be wired. The other three cards are passive (visual styling, no click handler) in Phase 1.

---

## Map + heatmaps

Folium map sized 100% width × 480px height inside the left column.

- **Tiles**: `folium.Map(location=[CLT_LAT, CLT_LNG], tiles="CartoDB Positron", zoom_start=10)`.
- **Own-merchant stores**: `folium.CircleMarker` at each `(lat, lng)`, radius 6, color = merchant's accent (`#0F4C81` for KRG, etc. — matching the palette in `docs/report.html`). Tooltip: `store_id · neighborhood · N txns`.
- **Heatmap toggle 1 — "All peers density"**: when on, render `folium.plugins.HeatMap` with one point per peer store coordinate. HeatMap renders a true intensity gradient (Leaflet.heat under the hood) — the gradient peaks where peer stores cluster. Default gradient (blue → green → yellow → red), radius 25px, blur 18px. The heatmap sits **below** the own-store dots so the dots stay crisp on top.
- **Heatmap toggle 2 — "Similar-peer density"**: same as toggle 1 but only same-segment peer coordinates feed the HeatMap. For grocers (KRG/ACM/WDX), 2 same-segment peers contribute their store coords. For TBL/TJX, no same-segment peers — toggle is shown disabled with a tooltip "no same-segment peers in panel."

Both toggles default off. Toggles persist in `st.session_state`. Only one heatmap layer is rendered at a time; flipping toggle 2 on supersedes toggle 1 (with a small caption note).

Peer store coordinates come from `tenant_stores` (filtered to peers, not via the lake) since the heatmap is a spatial visualization tool, not a privacy-protected analytic. The lake's purpose is anonymizing peer rows for queries; rendering peer store density on a basemap doesn't reveal anything beyond what's in `tenant_stores.neighborhood` already. To stay within the lake-only spirit, we resolve neighborhoods through `get_lake_stores(merchant_id)` and then look up centroids by neighborhood — gives the same density without surfacing exact peer lat/lng. (Open design choice; flag during review if you'd prefer the exact-coordinate version.)

---

## Insights panel (right side of above-fold split)

- **Daily volume sparkline** — Plotly scatter, single 90-day line, height 120px. No axes, hover shows date + count.
- **Top 5 SKUs by revenue** — Plotly horizontal bar from `tenant_transaction_items` × `tenant_products`. Limited to 5 rows.

---

## Detail charts row (2-up)

- **Category mix** — Plotly donut from `SUM(line_total) GROUP BY category`. Uses the report's 12-grocery-category color scheme for grocers; QSR/retail merchants get a categorical palette.
- **Store performance** — Plotly horizontal bar, all stores sorted desc by 90-day txn count. Color = merchant accent.

---

## Customer insights (expander, collapsed by default)

Header: **"Customer Insights — From Your Own Data"**. Subtitle: **"These views are unique to your merchant view; they're not visible in peer comparisons."** Subtitle text styled like `.viz-sub` in the report.

2×2 grid when expanded (grocer view — KRG / ACM / WDX):

1. **Customer segment performance** — revenue split by `tenant_customers.grocer_affinity_type`. Horizontal bar (loyalist / splitter / three_chain / lapsed_light).
2. **Total customers + avg transactions per customer** — two stat callouts. **Avg transactions per customer (90d)** replaces the original "repeat rate" — `total_txns / distinct_customers`, gives an executive-readable density number (e.g. "6.8 txns / customer over 90d").
3. **Avg basket size by behavioral_segment** — filler vs stocker. Two-row bar.
4. **Top 5 promos by redemptions** — `SELECT promo_id, promo_name, COUNT(*) AS redemptions FROM tenant_transaction_items × tenant_promotions GROUP BY promo_id ORDER BY redemptions DESC LIMIT 5`.

**For Taco Bell** (per user spec):
1. Customers by visit frequency — histogram of `txn_count` per customer (buckets: 1, 2–4, 5–9, 10+)
2. Total customers + avg transactions per customer (90d)
3. Avg basket size by daypart — bucket `txn_hour_bucket` into morning / lunch / afternoon / dinner / late
4. Top 5 promos by redemptions (same query)

**For TJ Maxx** (per user spec):
1. Customers by visit frequency — same histogram pattern as TBL
2. Total customers + avg transactions per customer (90d)
3. Avg basket size by category — top 5 categories by basket-size mean (apparel, accessories, etc.)
4. Top 5 categories by revenue contribution

---

## Chat panel (right column, 30% width)

### 4 agent suggestion cards (stacked vertically)

Each card: agent icon + name + brief description + 4 `st.button` rows for the suggested questions. Card styling matches `.viz-card` from the report.

**Agents and their 16 suggested questions** (grocer-segment defaults — see "TBL/TJX adaptation" below):

1. **Demand Forecasting Agent**
   - What dairy SKUs are slowing down?
   - Which customers used to buy these regularly?
   - What's the projected uplift from a 30% off promo to those customers?
   - Show campaign attribution for promo [example]
2. **Pricing & Benchmarking Agent**
   - How am I priced on dairy vs peers?
   - Which products am I significantly above market on?
   - Which products am I below market on?
   - Show category share trends in produce
3. **Anomaly Detection Agent**
   - Anything unusual recently?
   - Why are my University City stores declining?
   - Is this happening to peers too?
   - Why did avocado spike at Plaza Midwood on April 22?
4. **Trade Area Intelligence Agent**
   - Where do peer grocers cluster?
   - Which neighborhoods are underserved by my chain?
   - Where should I consider opening a new store?
   - How does my per-store velocity compare in same neighborhoods?

Clicking a button calls `chat.dispatch_question(agent_id, question_id, merchant_id)` which:
1. Resolves the placeholder handler from `placeholders.HANDLERS[(agent_id, question_id)]`.
2. Runs the handler — which queries the DB/lake and returns `{prose, table_df?, chart_spec?}`.
3. Appends `(ts, agent_name, question, response_dict)` to `st.session_state.chat_history[merchant_id]`.
4. Streamlit reruns and the new entry shows in the history pane.

### TBL/TJX adaptation

The 16 questions are grocer-framed (dairy, produce, avocado). For Taco Bell and TJ Maxx, the same 4 agents are shown but with segment-adapted questions:

- **TBL** (QSR, no same-segment peers in panel): questions pivot to menu velocity, drive-through patterns, daypart anomalies, trade-area clustering vs grocers; cross-merchant ones acknowledge "no QSR peers; comparing against grocery foot traffic."
- **TJX** (off-price retail, no same-segment peers): questions pivot to apparel/jewelry pricing, basket composition, geographic clustering.

This keeps the demo coherent for all five merchants without forcing the user to switch to a grocer to see meaningful responses.

### Chat history

Chronological list (newest first). Each entry:
- Header: `[Agent Name · 2 mins ago]`
- Prose body (markdown rendered)
- Optional table (`st.dataframe`) or Plotly chart inline
- Per-merchant; resets when the merchant changes (the dropdown's `on_change` callback clears `chat_history[merchant_id]`).

### Free-form input — hidden in Phase 1

Per the latest revision, no free-form text input in Phase 1. The 16 suggested-question buttons cover the demo surface. Free-form ad-hoc input returns in Phase 2 once real LLM agents are wired — at that point it's a positive feature (not a regression).

---

## Placeholder handler shape (`placeholders.py`)

Each of the 16 entries is a small function. Dict registry:

```python
HANDLERS: dict[tuple[str, str], Callable[[str], dict]] = {
    ("pricing", "dairy_vs_peers"): handle_pricing_dairy_vs_peers,
    ("pricing", "above_market"):    handle_pricing_above_market,
    # ... 14 more
}
```

Each handler:
1. Takes `merchant_id` (the viewer).
2. Runs real queries against `tenant_*` and/or via `get_lake_transactions(merchant_id)`.
3. Computes the result using `build_peer_mapping(merchant_id)` to label peers as `peer_a`/`peer_b`/`peer_c`/`peer_d` correctly per-viewer.
4. Returns `{"agent": "Pricing & Benchmarking Agent", "prose": "...", "table": df?, "chart": spec?}`.

The prose is hardcoded (the Phase 2 LLM workstream will replace it); the data is real. Example shape (for "How am I priced on dairy vs peers?"):

```python
def handle_pricing_dairy_vs_peers(merchant_id: str) -> dict:
    own_avg = _query_own_avg_price("DAIRY", merchant_id)
    peer_df = (get_lake_transactions(merchant_id, sql_filter="category = 'DAIRY'")
               .groupby("peer_id")["unit_price"].mean()
               .round(2))
    # ...build comparison table for top SKUs...
    return {
        "agent": "Pricing & Benchmarking Agent",
        "prose": f"Your average dairy unit price is ${own_avg:.2f}. peer_a is "
                 f"{(peer_df['peer_a']/own_avg-1)*100:+.0f}% relative to you; "
                 f"peer_b is {(peer_df['peer_b']/own_avg-1)*100:+.0f}%.",
        "table": comparison_df,
    }
```

This pattern repeats for 16 entries. Each handler is 10–25 lines.

The three anomaly handlers (`anomaly_unusual`, `anomaly_uc`, `anomaly_avocado`) **reuse the SQL** from `scripts/generate_report_data.py::_anomaly_series` — same University City stage breakdown, same Plaza Midwood avocado curve, same pasta-promo comparison. Importing or copying the SQL strings; the dashboard's tables and charts then mirror the report's anomaly visualizations exactly.

---

## Critical files to modify

| File | Action | Purpose |
|---|---|---|
| `src/dashboard/app.py` | Rewrite | Replace 230-line role-picker with new entrypoint + layout |
| `src/dashboard/styling.py` | Create | CSS injected via `st.markdown(..., unsafe_allow_html=True)` to match `docs/report.html` palette/cards |
| `src/dashboard/data.py` | Create | Cached query helpers (KPI rows, chart series, map data) |
| `src/dashboard/views.py` | Create | Dashboard renderers (KPI row, map, charts, customer insights expander) |
| `src/dashboard/chat.py` | Create | Agent cards, suggested-question buttons, chat history, free-form input |
| `src/dashboard/placeholders.py` | Create | 16 handlers + per-segment adaptation |
| `pyproject.toml` | Add deps | `plotly`, `folium`, `streamlit-folium` |

Existing modules that **stay untouched** (the dashboard reads from them but doesn't change them):
- `src/lake/views.py`, `src/lake/peer_mapping.py`, `src/lake/__init__.py`
- `src/agents/advisor.py`, `src/agents/tools.py` (Phase 2 plugs them in)
- `src/generate/parameters.py` (read for color palette, segment definitions)
- `data/payments.db` and `src/db/seed.py`

---

## Visual style (matches `docs/report.html`)

Inject via `styling.py`:

```css
:root {
  --accent: #0F4C81; --accent-soft: #D8E2EE;
  --surface: #F7F8FA; --border: #E2E5EA;
  --text: #1A1F2E; --text-2: #4A5161; --text-muted: #7B8294;
  --anomaly: #C44536;
  --c-krg: #0F4C81; --c-acm: #3A6FA5; --c-wdx: #6F8FB8;
  --c-tbl: #C0563F; --c-tjx: #5B7B58;
}
.stApp {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Inter, Roboto, sans-serif;
  ...
}
/* card primitives, KPI row, chat-bubble, agent-card styling, etc. */
```

System fonts only — no Google Fonts, matching the report's self-contained guarantee.

---

## Verification

End-to-end smoke test:

```bash
uv sync                              # picks up the new deps
uv run streamlit run src/dashboard/app.py
```

Manual checks:
1. Page loads with no errors. Default merchant = Kroger.
2. KPI row shows 4 cards with real numbers + WoW % deltas.
3. Map renders centered on Charlotte with 30 Kroger stores. Toggling "All peers density" adds gray neighborhood circles; toggling "Similar peers density" filters to the 2 grocer-peers.
4. Sparkline, top-5 SKUs, category donut, and store-performance bar all render.
5. Customer insights expander opens to a 2×2 grid; closes back.
6. Switch merchant to Acme → KPIs update, map switches to Acme stores, chat history clears.
7. Switch merchant to Taco Bell → "Similar peers" toggle is disabled with tooltip; questions show TBL-adapted versions; clicking each of 16 still returns a placeholder response.
8. Click each of the 16 suggested-question buttons → each returns a response with prose + (table or chart). Verify peer labels are `peer_a`/`peer_b`/`peer_c`/`peer_d` (never real merchant names) for cross-merchant questions.
9. Free-form input field → submitting any text shows the Phase 2 stub message verbatim.
10. Compare side-by-side with `docs/report.html`: same accent color, same card padding rhythm, same font stack.

Browser test in Chrome + Safari + Firefox via `localhost:8501`.

---

## Decisions to flag for review

1. **Map library — folium vs raw Leaflet.** Going with `folium + streamlit-folium`. Cleaner Streamlit integration; underlying tech is still Leaflet. If you want exact report parity (raw Leaflet via `st.components.v1.html`), say so during review.
2. **TBL/TJX question adaptation.** Going with per-segment question lists (16 grocer-framed for KRG/ACM/WDX; 16 segment-adapted for TBL/TJX). Alternative would be one universal list with response prose that pivots. The per-segment approach gives a better demo for each merchant.
3. **Cached queries vs live queries.** Going with `@st.cache_data(ttl=3600)` on KPI/chart queries keyed by `(merchant_id, filters)`. The DB is read-only and the panel is fixed-size so caching is safe; if filter UX feels sluggish during review, we can tune.
4. **The advisor agent stays available but unused in Phase 1.** `src/agents/advisor.py` is fully functional — Phase 2 will plug it into `chat.dispatch_question`. Phase 1 placeholders run real queries but the prose is hardcoded.

---

## Sample placeholder responses — 4 drafts for tone review

These are fully written. Numbers below are pulled from real queries against the current panel (Kroger viewer). Other 12 placeholders follow the same shape.

### Pricing & Benchmarking — *"How am I priced on dairy vs peers?"* (Kroger viewer)

> Your average dairy unit price is **$4.02** across all your dairy line items in the 90-day window. peer_a sits ~2% above you (avg $4.11); peer_b is at parity (avg $4.00). The pattern holds per-SKU on top-volume canonical dairy: you're consistently between peer_a (premium) and peer_b (value-positioned) — priced toward the middle of the market on every high-volume dairy SKU we can match across the panel.

**Top dairy SKU comparison (peer prices come from the lake; your prices are exact):**

| Product | Yours | peer_a | peer_b |
|---|---:|---:|---:|
| Babybel mini cheese wheels (12-count) | $6.86 | $7.34 | $6.76 |
| Greek yogurt plain (32 oz) | $5.89 | $6.17 | $5.88 |
| Organic Greek yogurt (32 oz) | $7.08 | $7.29 | $6.77 |
| Salted butter sticks (1 lb) | $5.50 | $5.57 | $5.36 |
| Unsalted butter sticks (1 lb) | $5.58 | $5.66 | $5.42 |

*Query path: own avg from `tenant_transaction_items × tenant_products` where category='DAIRY'; peer avgs from `get_lake_transactions(KRG, sql_filter="category='DAIRY'")` grouped by `peer_id, canonical_name`.*

---

### Anomaly Detection — *"Why are my University City stores declining?"* (Kroger viewer)

> Your two University City stores fell from **23.05** avg daily transactions per store at baseline (Mar 1 – Apr 11) to **14.57** in the Apr 26 – May 2 window — a **0.63×** ratio, the steepest drop among grocers in the panel. Peers in the same neighborhood declined too, but less: peer_a dropped to 0.71× of baseline; peer_b dropped to 0.72×.
>
> The shape is consistent with a campus-driven cycle: stage 1 (Apr 12 – Apr 18) shows a brief lift (finals stress shopping), then stages 2–3 fall hard as students leave for summer. Stage 4 (May 3+) stabilizes at a lower summer-resident baseline. The fact that your decline outpaces both peers suggests your stores are more student-leaning in product mix — single-serving sizes, instant meals, snacks — than the average University City grocer.

**Per-stage avg daily transactions per store** (your 2 stores vs each peer's 3 University City stores):

| Stage | Window | Yours | peer_a | peer_b |
|---|---|---:|---:|---:|
| Baseline | Mar 1 – Apr 11 | 23.05 | 25.76 | 27.42 |
| Stage 1 | Apr 12 – Apr 18 | 24.93 | 27.38 | 29.76 |
| Stage 2 | Apr 19 – Apr 25 | 18.93 | 24.52 | 24.62 |
| Stage 3 | Apr 26 – May 2 | **14.57** | **18.29** | **19.81** |
| Stage 4 | May 3 – May 29 | 16.89 | 21.95 | 23.54 |

*Query path: reuse SQL from `scripts/generate_report_data.py::_anomaly_series` (lines 1086–1183). Re-label KRG/ACM/WDX as own/peer_a/peer_b via `build_peer_mapping(viewer)`.*

---

### Demand Forecasting — *"What dairy SKUs are slowing down?"* (Kroger viewer)

> Six dairy SKUs declined week-over-week (last 7 days vs prior 7 days, in your stores). **Strawberry milk (half gallon)** leads the drop at −17.0%, followed by **Greek yogurt vanilla (32 oz)** at −12.9%. The remaining four are mild declines under 5%. Note: the dashboard's headline KPI deltas compare 30-day windows; this view uses a 7-day window because weekly SKU velocity is the more useful demand signal for restock decisions.

**Top 6 dairy SKUs by quantity decline (last 7d vs prior 7d):**

| SKU | Last 7d qty | Prior 7d qty | Δ |
|---|---:|---:|---:|
| Strawberry milk (half gallon) | 146 | 176 | **−17.0%** |
| Greek yogurt vanilla (32 oz) | 135 | 155 | **−12.9%** |
| Mozzarella shredded (8 oz) | 120 | 126 | −4.8% |
| Skim milk (gallon) | 145 | 151 | −4.0% |
| Pizza crust dough (13.8 oz) | 153 | 157 | −2.5% |
| Almond milk vanilla (half gallon) | 147 | 148 | −0.7% |

*Query path: `tenant_transaction_items × tenant_products` filtered to `merchant_id = 'KRG' AND category = 'DAIRY'`, grouped by SKU and split by 7-day windows. Only SKUs with prior-week volume ≥ 50 included to keep the comparison meaningful.*

---

### Trade Area Intelligence — *"Where should I consider opening a new store?"* (Kroger viewer)

> Two neighborhoods stand out as **underserved**: **Concord** (2 peer grocers, 0 of your stores) and **Huntersville** (1 peer grocer, 0 of your stores). Both are outer-suburban metros with grocery demand established by competitors but no Kroger footprint. The competitive case is strongest in Concord: 2 incumbent peer grocers indicate the trade area is large enough to support more than one chain, and your absence means peers are not splitting share with you today.
>
> The opposite pattern — **Matthews** (6 peer grocers, 2 of your stores) — also surfaces as worth evaluating. Six incumbent peer grocers signal a high-demand market; your 2-store footprint is underweight relative to peers. A 3rd store in Matthews would close the share gap.

**Neighborhood-by-neighborhood snapshot:**

| Neighborhood | Your stores | Peer grocer stores | Read |
|---|---:|---:|---|
| Concord | 0 | 2 | **Underserved, recommend** |
| Huntersville | 0 | 1 | **Underserved, recommend** |
| Matthews | 2 | 6 | **Underweight in high-demand market** |
| Pineville | 2 | 2 | Balanced |
| University City | 2 | 6 | Saturated |
| Dilworth | 8 | 6 | Already well-positioned (overweight) |

*Query path: `tenant_stores` for own footprint; `get_lake_stores(KRG)` filtered to `peer_segment='grocery'`, grouped by `neighborhood`.*

---

## Open items / follow-ups (not in scope)

- **Real LLM agent wiring** — Phase 2 workstream. Plumb `MerchantAdvisor` into `chat.dispatch_question` so the 16 buttons trigger LLM-generated responses instead of hardcoded ones, plus free-form input.
- **Shared anomaly query module** — move SQL strings from `scripts/generate_report_data.py::_anomaly_series` into `src/lake/anomaly_queries.py` so report-data and dashboard import from one source of truth. Phase 2 cleanup, not Phase 1.
- **Click-through cross-filtering from chart elements** — flagged for later iteration. Today, KPI cards aren't fully wired (only Active customers triggers Customer Insights expansion).
- **Per-chart export buttons** — flagged for later iteration.
- **Mobile-specific layout tuning** beyond Streamlit's default responsive grid — Phase 1 is desktop-first.
