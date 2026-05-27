# V3 Dashboard Design

The Phase 4 design contract. Companion to `V3_QUESTIONS.md` (the
question-by-question rubric) and `chart_patterns.md` (the 9-pattern
rendering contract).

---

## Section 1 — Document purpose and scope

The design specification for the v3 dashboard implementation in
Phase 4. This document defines what the dashboard is, how it looks
and behaves, and what gets built. It complements but doesn't
replace `V3_QUESTIONS.md` (the question-by-question rubric) or
`chart_patterns.md` (the 9-pattern rendering contract).

**Audience:** Phase 4 implementation work (Claude Code + chat
conversation). Phase 5 (agent prompt updates) and Phase 6 (demo
prep) reference this for visual context.

**Inputs this design rests on:**

- `V3_VISION.md` — the rubric (merchant-seat, cross-merchant,
  standalone tests; the gold-standard demo beat)
- `V3_AUDIT.md` (Phase 1.5 and Phase 1.6 close-outs) — the
  foundation: per-viewer tenant views, materialized lake,
  calibrated synthetic data
- `V3_QUESTIONS.md` — 30 fully-specified suggested questions
  across 4 specialists and 5 merchant viewers
- `chart_patterns.md` — 9 chart patterns serving both dashboard
  cards and agent free-form answers
- `V3_PHASE4_AUDIT.md` — what currently exists in
  `src/dashboard/`, what stays, what gets replaced

**Out of scope for this document:**

- Implementation code (lives in `src/dashboard/`)
- Agent prompt redesign (Phase 5; see future
  `V3_AGENTS_DESIGN.md`)
- Demo script and rehearsal (Phase 6)
- Deployment changes (HF Spaces redeploy happens after Phase 6)

**Scope discipline (the only rule):** this dashboard is the
merchant's view of their own business. Calm, useful, intentional.
The chat panel alongside it provides cross-merchant context and
deeper analysis on demand. The dashboard is the spine; the chat
is the depth.

---

## Section 2 — Page structure

**Overall shape.** Top-of-page header strip → filter row →
two-column layout: left 65% (dashboard column with the 15
cards), right 35% (chat panel). Persistent across the entire
scroll. Streamlit-native layout, no novel framework.

### 2.1 — Header strip

Preserved from v2.5:

- Page title "Merchant dashboard" with subtitle.
- "Acting as" merchant selectbox on the right, with all 5
  merchant viewers (KRG / ACM / WDX / TBL / TJX). Selection
  persists across the session.

The header is intentionally restrained. The merchant selector is
the one control that changes everything else; everything below it
adapts.

### 2.2 — Filter row

Preserved from v2.5 with minor refinement:

- Date range picker (default: trailing 90 days).
- Stores multiselect (per merchant; pre-populated with all
  stores).
- Categories multiselect (per merchant; pre-populated with all
  categories).
- State is keyed by merchant in
  `state.filters_by_merchant[merchant_id]`, so each merchant has
  its own filter dict.

Filter row is hidden when chat is in expand-mode (preserves screen
real estate when the merchant is in deep conversation).

### 2.3 — Dashboard column (left, 65% width)

Five sections stacked vertically:

1. Performance pulse (5 KPI cards in a horizontal row)
2. Performance over time (3 cards in a row)
3. Geography (2 cards in a row)
4. Catalog (2 cards in a row)
5. Customers (3 cards in a row)

Each section has a clear visual header. Cards within a section
use a consistent `.panel-card` wrapper (the existing v2.5
primitive). Section dividers are subtle — a light rule plus a
section title.

Cards within each row size flexibly:

- 5-card row (KPI strip): equal widths, ~20% each.
- 3-card row: equal widths, ~33% each.
- 2-card row: equal widths, ~50% each (some rows asymmetric;
  per-card spec in Section 3 defines widths).

### 2.4 — Chat panel (right, 35% width)

Persistent. Always visible alongside the dashboard. Contents top
to bottom:

- Header: "Ask the data" title, expand toggle, clear-history
  icon.
- Specialist switcher (chips, not dropdown) — pricing / anomaly /
  demand / trade in that order.
- Specialist description (one-line, generic across viewers).
- Three suggested questions for the active specialist, as
  clickable pills.
- Scrollable chat history (fixed height container, ~700px).
- Chat input at the bottom.

The chat panel structure preserves the v2.5 deferred-dispatch
pattern, streaming behavior, and caveats-stripping logic — all of
which work. Visual changes only: replace dropdown with chip
selector, update suggested-question content per `V3_QUESTIONS.md`.

### 2.5 — Expand mode

Preserved from v2.5:

- When the merchant taps the expand icon, the chat panel takes
  full viewport width and the dashboard column is hidden.
- When the merchant taps it again, the split view returns.
- Useful for deep conversations where the merchant wants more
  chat real estate.
- Filter row is also hidden in expand mode.

### 2.6 — Per-merchant isolation

Preserved from v2.5:

- `state.merchant_id` — currently selected merchant.
- `state.filters_by_merchant[mid]` — per-merchant filters.
- `state.chat_messages_by_merchant[mid]` — chronological turns;
  never cross-merchant.
- `state.active_agent` — currently selected specialist (resets to
  "pricing" when switching merchants).
- `state.chat_expanded`, `state.agent_running`,
  `state.pending_dispatch` — UI/flow control flags.

Switching merchants preserves each merchant's individual chat
history and filter state. The dashboard view recomputes for the
new merchant. The active specialist resets to pricing on merchant
switch (context shift means starting fresh).

### 2.7 — Mobile responsiveness

v3 is desktop-first. The 65/35 split assumes ≥1024px viewports.
On mobile (<768px), the right rail collapses to a bottom drawer
or full-screen swap mode. Mobile is *readable* but not *polished*
— the demo experience is desktop. Phase 4 does not invest in
mobile polish.

### 2.8 — Out of scope for this section

- Card-by-card content (covered in Section 3).
- Chat panel content details (covered in Section 4).
- "Ask about this" affordance (covered in Section 5).

---

## Section 3 — The 15 cards

The dashboard contains 15 cards across 5 sections. Each card
answers one merchant question with one chart or KPI. All cards
are own-data only (no cross-merchant overlays on the dashboard
spine — peer context lives in the chat panel).

### 3.1 — Section 1: Performance pulse (5 KPI cards)

Top of the dashboard. A horizontal row of five KPI cards. Each
is Pattern 8 (single-number callout with sparkline and delta
arrow). The merchant glances at this row and reads their health
summary in 3 seconds.

**Row treatment:**

- Equal-width cards across 65% column width. Each card ~13% of
  total page width.
- Card height consistent across all 5 — fixed pixel height
  (~140px) so the row reads as a unified strip.
- Hover treatment: subtle elevation shadow.
- "Ask about this" affordance: hover-revealed button in
  top-right corner.

#### Card 1.1: Revenue this week

- **Question answered:** Am I making money this week?
- **Data source:** `tenant_transactions.txn_total` summed over
  the trailing week, filtered by current selection.
- **Chart pattern:** Pattern 8 (KPI callout).
- **Display:**
  - Large number: total revenue this week, formatted as
    currency ($1.2M, $48K, etc.).
  - Delta below the number: % change vs prior 4-week average
    baseline. Up green, down red, flat gray.
  - Sparkline below the delta: 12-week trailing weekly revenue,
    no axis labels.
- **Per-viewer adaptation:** Same shape for all 5 viewers. TBL
  revenue is smaller in absolute terms ($120K vs $1.2M for
  grocers); the formatting (K vs M) adapts automatically.
- **"Ask about this" routing:** Anomaly specialist. Pre-fill:
  "What's driving the change in my revenue this week?"
- **Implementation:** STAYS from v2.5's `data.kpi_block` with
  minor formatting adjustment.

#### Card 1.2: Transactions this week

- **Question answered:** How busy am I?
- **Data source:** `COUNT(DISTINCT txn_id)` over trailing week.
- **Chart pattern:** Pattern 8.
- **Display:** Large number (transactions, thousands separator),
  delta vs 4-week baseline, 12-week sparkline.
- **Per-viewer adaptation:** TBL is highest-frequency
  (~3,500-4,500/wk); TJX is lowest (~1,000/wk).
- **"Ask about this" routing:** Anomaly specialist. Pre-fill:
  "What's driving the change in my transaction count this week?"
- **Implementation:** STAYS from v2.5.

#### Card 1.3: Avg basket value

- **Question answered:** Are my customers spending more or less
  per visit?
- **Data source:** Revenue ÷ transactions, trailing week.
- **Chart pattern:** Pattern 8.
- **Display:** Average ticket as currency, delta vs 4-week
  baseline, sparkline.
- **Per-viewer adaptation:** This is where viewers diverge
  dramatically. Grocers ~$78, TBL ~$21, TJX ~$350. Label stays
  consistent; number is viewer-specific.
- **"Ask about this" routing:** Demand specialist. Pre-fill:
  "What's changing about my average ticket?"
- **Implementation:** STAYS from v2.5 with label change ("Avg
  transaction" → "Avg basket").

#### Card 1.4: Unique customers this week

- **Question answered:** How many distinct customers came
  through?
- **Data source:** `COUNT(DISTINCT customer_id)` over trailing
  week.
- **Chart pattern:** Pattern 8.
- **Display:** Number of unique customers, delta vs 4-week
  baseline, sparkline.
- **Per-viewer adaptation:** Same shape across viewers.
- **"Ask about this" routing:** Demand specialist. Pre-fill: "Is
  my customer count growing or declining? Why?"
- **Implementation:** STAYS from v2.5.

#### Card 1.5: Anomaly count

- **Question answered:** Is anything weird right now?
- **Data source:** Computed metric — count of stores OR
  categories OR SKUs flagged as deviating from baseline by >15%.
  Computation looks at this week vs trailing 4-week baseline.
- **Chart pattern:** Pattern 8, with stronger visual treatment
  when count > 0.
- **Display:**
  - When 0 flagged: gray number, "All clear" subtitle. Calm.
  - When 1+ flagged: red number, "{N} flagged" subtitle. Drawn
    attention.
  - Sparkline shows 12-week trailing anomaly count.
- **Per-viewer adaptation:** Same shape. The underlying anomaly
  definition (which stores/SKUs are flagged) is viewer-specific.
- **Threshold:** A flagged item is one >15% off baseline.
- **"Ask about this" routing:** Anomaly specialist. Pre-fill:
  "What's flagged this week?"
- **Implementation:** NEW. New query helper. Count stores where
  `this_week_txns / baseline_4wk_avg < 0.85 OR > 1.15`, PLUS
  count categories where the same rule applies on revenue.

### 3.2 — Section 2: Performance over time (3 cards)

Three cards beneath the KPI strip. Each card is the trajectory
across a different dimension — revenue, transactions, and timing.

**Row treatment:**

- Three cards, equal-width across the dashboard column.
- Each card ~22% of total page width.
- Card height consistent across the row.

#### Card 2.1: Revenue trajectory

- **Question answered:** How has my revenue moved over the last
  90 days?
- **Data source:** Weekly revenue from
  `tenant_transactions.txn_total`, grouped by
  week-starting-Sunday over the 90-day window. Own-data only.
- **Chart pattern:** Pattern 1 (time-series-vs-peers) used in
  own-only mode — one line, no peer overlay.
- **Display:**
  - Single line, x = week, y = weekly revenue ($).
  - Own merchant brand color, solid, line markers on data
    points.
  - Subtle shaded band showing the trailing 4-week average
    baseline (lightest gray fill).
  - Title at top, takeaway subtitle below the title.
- **Takeaway template (computed):** "Revenue trending
  {direction} {pct}% over the last 30 days; weekly trajectory
  is {stable / accelerating / decelerating}."
- **Interactivity:**
  - Hover any week: tooltip with week-starting date, revenue,
    ratio to baseline.
  - Click any week: drilldown to that week's daily revenue.
- **Per-viewer adaptation:** Same shape, viewer-specific scale.
- **"Ask about this" routing:** Anomaly specialist with
  pre-fill: "What's behind the revenue trajectory I'm seeing?"
- **Implementation:** REWORKED from v2.5's
  `render_insights_panel` daily-volume sparkline.

#### Card 2.2: Transaction trajectory

- **Question answered:** How has my transaction count moved over
  90 days?
- **Data source:** Weekly transaction count from
  `tenant_transactions`.
- **Chart pattern:** Pattern 1 (own-only).
- **Display:**
  - Single line, x = week, y = weekly transaction count.
  - Own merchant brand color, solid.
  - Shaded baseline band (same as 2.1).
- **Takeaway template (computed):** "Transactions {direction}
  {pct}%; basket value {direction} {pct}% — your topline is
  driven primarily by {more trips / bigger baskets / both /
  neither, mixed}."
- **Interactivity:** Same as 2.1.
- **Per-viewer adaptation:** Same shape, viewer-specific scale.
- **"Ask about this" routing:** Anomaly specialist with
  pre-fill: "What's driving the change in transaction count?"
- **Implementation:** REWORKED from v2.5.

The takeaway sentence combines transaction trajectory *with*
basket-value trajectory to tell a simple decomposition story —
without using a full waterfall (that's the agent's D7). The
merchant gets "your growth is from more trips, not bigger
baskets" at a glance.

#### Card 2.3: Hour × day-of-week traffic heatmap

- **Question answered:** When do my customers shop?
- **Data source:** Transaction count grouped by
  `strftime('%w', txn_ts)` (day of week 0-6) ×
  `SUBSTR(txn_ts, 12, 2)` (hour 00-23), aggregated over trailing
  90 days.
- **Chart pattern:** Pattern 3 (cross-merchant heatmap) used in
  own-only sequential mode — no peer comparison, sequential color
  scale.
- **Display:**
  - 7 × 24 heatmap (7 days down, 24 hours across).
  - Color: sequential from light brand-family at zero to
    saturated brand color at peak.
  - Cell values: transaction count (text overlay), or just color
    encoding with hover for exact numbers.
- **Takeaway template (computed):** "Your peak hour is {day}
  {hour} ({N} transactions); your slowest is {day} {hour} ({N}
  transactions). {Weekday / Weekend} traffic is {X}% higher than
  the inverse."
- **Interactivity:**
  - Hover cell: day, hour, transaction count.
  - Click cell: drilldown to a list of transactions in that
    hour-day across all stores.
- **Per-viewer adaptation:** Critical for TBL (QSR has strong
  daypart patterns — lunch rush, evening dinner) and important
  for grocers (Sunday afternoons, weekday evenings differ). TJX
  has lower traffic and more uniform distribution.
- **"Ask about this" routing:** Trade specialist with pre-fill:
  "What does my hour-by-day pattern tell me about my customer
  base?"
- **Implementation:** STAYS from v2.5's `data.hour_dow_heatmap`.
  Phase 4 adds takeaway computation.

### 3.3 — Section 3: Geography (2 cards)

Two cards. The spatial view of the merchant's footprint — where
stores are, how they're performing, where customers come from.
Both cards are own-data only.

**Row treatment:**

- Two cards side by side.
- Asymmetric widths: Map ~55%, Table ~45%.
- Map is taller (~480px); table fits the same height with
  vertical scroll if rows exceed it.

#### Card 3.1: Neighborhood performance map

- **Question answered:** Which of my neighborhoods are doing
  well, and which need attention?
- **Data source:** Per-neighborhood aggregation of
  `tenant_transactions` joined to `tenant_stores`, computed as
  recent-week traffic vs trailing-4-week-average baseline ratio.
- **Chart pattern:** Pattern 6 (geographic map) used in own-data
  mode.
- **Display:**
  - Folium map of Charlotte metro.
  - Neighborhood polygons colored by performance ratio:
    - Red shades for under-performing (ratio < 0.85).
    - White/neutral for on-trend (0.85 – 1.15).
    - Blue shades for over-performing (ratio > 1.15).
  - Own store markers overlaid as dots in brand color.
  - Map tiles: CartoDB positron (already in use in v2.5).
- **Takeaway template (computed):** "Top 3 neighborhoods: {n1},
  {n2}, {n3}. Weakest: {weakest_n} at {ratio} of baseline."
- **Interactivity:**
  - Hover polygon: neighborhood name, transaction count this
    week, baseline week count, ratio, own store count.
  - Click polygon: drill to that neighborhood's detail
    (card-sized chart shows weekly trajectory for that specific
    neighborhood, with "back to map" link).
  - Hover store marker: store ID, neighborhood, weekly
    transaction count.
- **Per-viewer adaptation:**
  - KRG: 30 stores across 10 neighborhoods (concentrated in
    Dilworth).
  - ACM: 25 stores, 6+ neighborhoods, leans affluent
    (SouthPark/Ballantyne/Dilworth heavier per Phase 1.6).
  - WDX: 20 stores, broader spread, value-oriented
    neighborhoods.
  - TBL: 40 stores, smaller geographic footprint per store but
    more locations.
  - TJX: 8 stores, sparse, mostly in larger commercial
    neighborhoods.
- **"Ask about this" routing:** Trade specialist with pre-fill:
  "Which of my neighborhoods are over- or under-performing, and
  is the issue mine or the market's?" (This is T1.)
- **Implementation:** REWORKED from v2.5's `render_map`. The
  current map plots per-store circles colored by per-store
  transaction volume. v3 needs neighborhood-polygon coloring (a
  different aggregation level) plus per-store overlay markers.
  Neighborhood polygons recommended approach: synthetic convex
  hulls around store clusters per neighborhood. Phase 4 decides
  between synthetic hulls and sourced GeoJSON.

#### Card 3.2: Store performance distribution

- **Question answered:** Which of my stores are over-performing,
  which are quiet?
- **Data source:** Per-store transaction count this week, with
  trailing 4-week baseline. Sortable and flaggable.
- **Chart pattern:** Pattern 9 (table-with-drilldown).
- **Display:**
  - Sortable table.
  - Columns: Store ID, Neighborhood, Baseline weekly txns (4wk
    avg), This week txns, Ratio, Flag.
  - Row highlighting:
    - Red background tint for stores with ratio < 0.85.
    - Blue background tint for stores with ratio > 1.15.
    - Neutral background for stores in the 0.85 – 1.15 range.
  - Default sort: by ratio ascending (worst first).
- **Takeaway template (computed):** "{N} of {M} stores running
  below baseline by >15%; top performer {store} at {ratio} of
  baseline."
- **Interactivity:**
  - Click any column header to re-sort.
  - Click any row: drill to that store's detailed view (weekly
    trajectory chart for that store specifically, with category
    breakdown). "Back to table" link returns.
  - Hover row: tooltip with full numerics including 90-day total.
- **Per-viewer adaptation:**
  - Grocers: 20-30 stores per merchant.
  - TBL: 40 stores per merchant.
  - TJX: 8 stores per merchant (short table, all rows visible
    without scroll).
- **"Ask about this" routing:** Anomaly specialist with
  pre-fill: "Which stores are showing unusual traffic this
  week?" (This is A2 for grocers, T-A1 for TBL, R-A1 for TJX.)
- **Implementation:** STAYS from v2.5's
  `render_store_performance` with adjustments (add baseline
  column, add flag column, default-sort change).

### 3.4 — Section 4: Catalog (2 cards)

Two cards covering what the merchant is selling — categories and
SKUs. Both own-data on the dashboard.

**Row treatment:**

- Two cards side by side.
- Asymmetric widths: Category mix ~40%, SKU performance ~60%.
- Both cards same height (~420px).

#### Card 4.1: Category mix

- **Question answered:** What categories make up my business?
  Where is my revenue concentrated?
- **Data source:** Per-category revenue from
  `tenant_transaction_items` joined to `tenant_products`, summed
  over trailing 90 days. Calculated as share of total own
  revenue.
- **Chart pattern:** Pattern 2 (cross-merchant comparison,
  single dimension) used in own-only mode — single-merchant
  share bar.
- **Display:**
  - Horizontal bar chart.
  - Y-axis: category name, sorted by revenue share descending.
  - X-axis: share of own revenue (percentage).
  - Bars in own brand color, single series.
  - Top 8-10 categories visible; the rest rolled into "Other"
    (gets a lighter gray bar).
  - Bar labels: percentage at the end of each bar.
- **Takeaway template (computed):** "Top 3 categories ({c1},
  {c2}, {c3}) account for {pct}% of revenue. {N} categories make
  up the long tail."
- **Interactivity:**
  - Hover bar: category name, revenue total, share, transaction
    count.
  - Click bar: drilldown to that category's SKU-level breakdown.
- **Per-viewer adaptation:**
  - Grocers: MEAT, PANTRY, PRODUCE, DAIRY, HOUSEHOLD at the top
    (each ~10-18%); per Phase 1.6 these now differ across
    grocers (KRG produce-forward, ACM dairy-forward, WDX
    pantry-forward).
  - TBL: COMBO (~23%), DRINK (~17%), BURR (~14%), SIDE (~14%),
    SPEC (~13%) — more concentrated, fewer categories total.
  - TJX: ACC (~34%), SHO (~13%), WOM (~13%), MEN (~11%), JEW
    (~11%) — heavily skewed to accessories.
- **"Ask about this" routing:** Demand specialist with
  per-viewer pre-fill:
  - Grocers: "Where am I over- or under-indexed in my basket mix
    vs peers?" (D3)
  - TBL: "What does my menu mix look like? Where am I most
    concentrated?" (T-D1)
  - TJX: "What does my category mix look like? Where am I
    concentrated?" (R-D1)
- **Implementation:** REWORKED from v2.5's category mix donut
  (`render_category_mix`). New shape is a horizontal share bar.

#### Card 4.2: SKU performance

- **Question answered:** Which products are top performers?
  Which are quiet?
- **Data source:** Per-SKU transaction-line count and revenue
  from `tenant_transaction_items` joined to `tenant_products`.
  Trailing 30-day window plus baseline 4-week-prior window for
  the deltas.
- **Chart pattern:** Pattern 9 (table-with-drilldown).
- **Display:**
  - Sortable table with toggle at the top: "Top performers" /
    "Underperformers".
  - Top performers view: sorted by current-period revenue
    descending, top 20 rows.
  - Underperformers view: sorted by delta-vs-baseline ascending
    (worst decline first), top 20 rows.
  - Columns: SKU name, Category, Baseline weekly units,
    This-period weekly units, Delta %, Revenue this period.
  - Row highlighting:
    - Top performers view: light brand color for top 5 (visual
      hierarchy).
    - Underperformers view: red background tint for rows with
      delta < -15%.
- **Takeaway template (computed):**
  - Top performers view: "Top SKU: {sku} ({revenue} this
    period). Top 10 SKUs account for {pct}% of revenue."
  - Underperformers view: "{N} SKUs declining >15% vs baseline.
    Largest drop: {sku} at {delta}%."
- **Interactivity:**
  - Toggle between top/bottom views (radio buttons or pill
    toggles at card header).
  - Click any column header to re-sort within the current view.
  - Click any row: SKU-level drill (90-day price + volume
    trajectory for that SKU). "Back to SKU list" link.
- **Per-viewer adaptation:**
  - Grocers: top SKUs are staples (milk, eggs, bread, ground
    beef).
  - TBL: top SKUs are menu items (Cinnamon Twists, Chalupa
    Combo, etc.).
  - TJX: top SKUs are categories like Earrings, Hand cream, Body
    lotion.
- **"Ask about this" routing:**
  - Top performers view: Demand specialist. Pre-fill: "Tell me
    more about my top-performing SKUs and what's driving them".
  - Underperformers view: Anomaly specialist. Pre-fill per
    viewer: Grocers: "Which SKUs are spiking or dropping
    unusually?" (A3); TBL: T-A2 question text; TJX: R-A2
    question text.
- **Implementation:** Consolidates v2.5's "Top 5 SKUs" (top
  performers) with a new "slow-moving SKUs" view
  (underperformers) behind a toggle. Same `data.top_skus` query
  gets extended to support both sort orders, or a new helper
  added.

### 3.5 — Section 5: Customers (3 cards)

Three cards. The merchant's view of their customer base — who's
coming back, how often, where they're from. All own-data.

**Row treatment:**

- Three cards side by side, roughly equal widths (~33% each of
  the dashboard column).
- Card heights consistent — all three cards same fixed height
  (~360px), with the map being the height-constrained one.

#### Card 5.1: New vs returning customers

- **Question answered:** What share of my customers are new vs
  returning this week?
- **Data source:** For this week's unique customers, classify
  each as:
  - **New**: customer's first transaction with this merchant was
    this week.
  - **Returning**: customer had a prior transaction with this
    merchant before this week.
  - Computed from `tenant_view_<viewer>_customers` joined to
    `tenant_transactions` filtered to this week.
- **Chart pattern:** Pattern 2 (cross-merchant comparison, single
  dim) used in own-only mode — diverging treatment with new vs
  returning as the two categories.
- **Display:**
  - Two horizontal bars stacked vertically: "New" and
    "Returning".
  - Bar widths proportional to count.
  - Color: new in lighter brand color, returning in saturated
    brand color (visual hierarchy — returning is "your base").
  - Text annotations: count + percentage on each bar.
  - Below the bars: trend indicator — "Up {pct}% from prior
    4-week avg" or "Stable" or "Down" for each of the two
    segments.
- **Takeaway template (computed):** "{pct}% of this week's
  customers are new; {pct}% are returning. New-customer share is
  {direction} {pct}pp vs prior 4 weeks."
- **Interactivity:**
  - Hover bar: exact counts, percentages, week-over-week trend.
  - Click bar: drilldown to a list of customers in that segment.
- **Per-viewer adaptation:** (Updated Phase 4.5 to reflect
  observed data — the original design predicted TJX would have
  the lowest new-customer share; the calibrated synthetic data
  shows the opposite.)
  - Grocers: ~4% new in any given week (loyalty business).
  - TBL: similar to grocers in absolute share (~4%) despite the
    QSR walk-in framing — the customer panel anchors most TBL
    customers across the 90-day window.
  - TJX: highest new-customer share (~18%). Off-price retail
    customers visit less frequently than grocery customers,
    which paradoxically produces a higher new-customer share per
    week — sparse visits = a larger fraction of any given week's
    customers having their first ever transaction that week. The
    smaller TJX customer base (~960 unique per week vs grocers'
    2.6-3.4K) also makes any week's variation register as a
    bigger percentage swing.
- **"Ask about this" routing:** Demand specialist. Pre-fill:
  "What's the composition of my customer base this week — are
  new customers growing or my base growing?"
- **Implementation:** NEW. New data helper needed:
  `data.new_vs_returning(merchant_id, week_start)`.

#### Card 5.2: Transactions per customer (frequency distribution)

- **Question answered:** How concentrated is my customer
  engagement?
- **Data source:** For all customers in the trailing 90-day
  window, count distinct days of activity per customer. Group
  into buckets: 1 visit, 2-3 visits, 4-6 visits, 7-10 visits,
  11+ visits.
- **Chart pattern:** Pattern 2 (cross-merchant comparison, single
  dim) used in own-only mode.
- **Display:**
  - Horizontal bar chart.
  - Y-axis: visit-count buckets (1, 2-3, 4-6, 7-10, 11+), in
    order.
  - X-axis: count of customers per bucket.
  - Bars in own brand color.
  - Number labels at end of each bar.
- **Takeaway template (computed):** "Your top cohort (11+ visits
  in 90 days) is {pct}% of your customers but generates {pct}%
  of your revenue. {N} customers shopped just once."
- **Interactivity:**
  - Hover bucket: customer count, revenue share for that cohort,
    average transaction value for that cohort.
  - Click bucket: drilldown to a profile of that cohort (basket
    size, top categories, geographic distribution).
- **Per-viewer adaptation:**
  - Grocers: distribution skews toward 4-10 visits (regular
    grocery shopping).
  - TBL: distribution skews toward 7-11+ visits (QSR frequency).
  - TJX: distribution skews toward 1-3 visits (off-price retail
    is less frequent).
- **"Ask about this" routing:** Demand specialist. Pre-fill:
  "What does my customer frequency distribution tell me about
  loyalty?"
- **Implementation:** STAYS from v2.5's `_render_txn_freq`.
  Phase 4 adds takeaway template computation.

#### Card 5.3: Customer home geography

- **Question answered:** Where do my customers come from? Am I
  serving customers near my stores or pulling from farther away?
- **Data source:** `tenant_customers.home_zip5` joined to
  `tenant_transactions` for the trailing 90 days. Aggregate
  count of distinct customers by home neighborhood
  (ZIP-to-neighborhood mapping per existing v2.5 logic).
- **Chart pattern:** Pattern 6 (geographic map) used in own-data
  mode.
- **Display:**
  - Folium map of Charlotte metro.
  - Neighborhood polygons colored by customer-home density
    (sequential color scale, light to dark in brand-family).
  - Own store markers overlaid.
  - Legend showing color-to-count mapping.
- **Takeaway template (computed):** "{pct}% of your customers
  live in neighborhoods where you have at least one store.
  Densest customer area without a nearby store: {neighborhood}
  ({N} customers, {distance} miles to your nearest store)."
- **Interactivity:**
  - Hover polygon: neighborhood, customer count, distance to
    nearest own store.
  - Click polygon: drilldown to a customer-count breakdown for
    that neighborhood.
- **Per-viewer adaptation:**
  - Grocers: customers concentrated in trade areas near stores,
    with some spread.
  - TBL: tighter customer-home concentration (QSR pulls local).
  - TJX: broader customer-home distribution (off-price retail
    customers travel further).
- **Coverage caveat (in card subtitle):** "Shows home location
  for the {pct}% of transactions tied to a known customer."
- **"Ask about this" routing:** Trade specialist. Pre-fill:
  "Where do my customers live relative to my stores?" (This is
  T2 — same question for all viewers.)
- **Implementation:** NEW. The data exists
  (`tenant_customers.home_zip5`) but no visualization currently
  uses it. New data helper:
  `data.customer_home_geography(merchant_id, filters_key)`.
  Polygon coloring uses same approach as Card 3.1.

---

## Section 4 — Chat panel evolution

The chat panel evolves from v2.5 rather than gets rebuilt. The
existing two-rerun deferred-dispatch pattern, streaming,
caveats-stripping, and per-merchant chat isolation all work and
stay. What changes is the visual presentation, the suggested-
question content, and the addition of the "Ask about this"
entry path.

### 4.1 — Panel structure (top to bottom)

1. **Header** (preserved from v2.5)
   - Title: "Ask the data".
   - Expand toggle icon (chat takes full screen).
   - Clear-history icon.

2. **Specialist switcher** (REWORKED from v2.5)
   - Currently: a Streamlit `selectbox` (dropdown).
   - v3: chip-style horizontal pill selector.
   - Order: pricing → anomaly → demand → trade (per
     `V3_QUESTIONS.md`, not v2.5's order).
   - Active specialist highlighted in brand color; inactive in
     light gray.
   - Click chip → updates `state.active_agent`, refreshes the
     suggested questions below.

3. **Specialist description** (REWORKED)
   - One-line description below the chip strip.
   - Generic across all viewers (works for own-data and
     cross-merchant contexts):
     - **Pricing:** "Benchmarks your pricing across categories
       and SKUs, surfaces where you're aligned or out of
       position."
     - **Anomaly:** "Detects unusual patterns in your stores,
       categories, and traffic — flags what changed and helps
       you investigate."
     - **Demand:** "Analyzes your basket composition, category
       momentum, and revenue drivers."
     - **Trade:** "Maps your trade area, customer geography, and
       expansion opportunities."

4. **Suggested questions** (REWORKED — content per viewer)
   - Three clickable pills, full-width, vertical stack.
   - Question text from `V3_QUESTIONS.md` per active specialist
     + viewer:
     - **Grocer viewers (KRG/ACM/WDX):**
       - Pricing pills: P1, P2, P3
       - Anomaly pills: A1, A2, A3
       - Demand pills: D3, D4, D7
       - Trade pills: T1, T2, T4
     - **TBL viewer:**
       - Pricing pills: T-P1, T-P2, T-P3
       - Anomaly pills: T-A1, T-A2, T-A3
       - Demand pills: T-D1, T-D2, T-D3
       - Trade pills: T1, T2, T4 (reused from grocer set)
     - **TJX viewer:**
       - Pricing pills: R-P1, R-P2, R-P3
       - Anomaly pills: R-A1, R-A2, R-A3
       - Demand pills: R-D1, R-D2, R-D3
       - Trade pills: T1, T2, T4 (reused)
   - Click pill → enqueues `pending_dispatch` for the question,
     the chat panel processes it on rerun.
   - Pills disabled while an agent is running.

5. **Chat history** (STAYS from v2.5)
   - Scrollable container
     (`st.container(height=700, border=True)`).
   - Per-merchant history (preserved in
     `state.chat_messages_by_merchant[mid]`).
   - User turns and assistant turns rendered as message bubbles.
   - Streaming behavior preserved.
   - Caveats-stripping heuristic preserved.

6. **Chat input** (STAYS)
   - `st.chat_input("Ask anything…")` at the bottom.
   - Free-form submit routes through orchestrated dispatch
     (Haiku router with keyword fallback).

### 4.2 — Per-merchant chat isolation (preserved)

- Switching merchants preserves each merchant's individual chat
  history.
- Active specialist resets to pricing when switching merchants.
- The chat history scroll position is preserved per merchant in
  `state.chat_scroll_position_by_merchant[mid]` (NEW small
  refinement).

### 4.3 — Streaming behavior (preserved)

The two-rerun deferred-dispatch pattern in `chat.py` stays
intact:

- Button click sets `pending_dispatch` and
  `agent_running = True`, no agent runs yet.
- Streamlit reruns; in the second pass, every control is
  `disabled=is_running`.
- The dispatch fires inside the history container, streams
  tokens via `on_token`, completes.
- A final rerun clears `pending_dispatch` and `agent_running`.

### 4.4 — Expand mode (preserved)

Same as v2.5 — expand icon takes chat to full viewport, expand
again returns to split view.

### 4.5 — Telemetry footer (preserved)

Existing `app._render_telemetry_footer` reads
`src.agents.llm.session_totals()` and shows token usage at the
bottom. Unobtrusive, useful for debugging.

---

## Section 5 — "Ask about this" affordance

This is new — no precedent in v2.5. We're defining the entire
mechanism: how the affordance appears, how it triggers, how
context flows from a dashboard card to the chat panel.

### 5.1 — Visual treatment

Each of the 15 cards has an "Ask about this" affordance in the
card's top-right corner:

- A small icon button (speech-bubble or chat icon) — subtle but
  discoverable.
- Hover state on desktop: button becomes more prominent
  (background fill, slight shadow).
- Always-visible on mobile.
- Tooltip on hover: "Ask the agent about this".
- Placement: top-right of the card header, aligned with the
  title.

### 5.2 — Interaction model

When a merchant taps the affordance on any card:

1. The right-rail chat panel's specialist switcher snaps to the
   appropriate specialist.
2. The chat input gets pre-filled with a context-aware question
   (text only — natural language).
3. The pre-filled question waits for the merchant to confirm
   (Option B — confirm-to-send) — they press Enter or click send
   to fire the question.
4. If the chat panel is collapsed (expand-mode hides the
   dashboard), the dashboard remains visible during transition.

### 5.3 — Decision: confirm-to-send (Option B)

Auto-submit was considered (Option A) but rejected. Reasons:

- Gives merchant control. Pre-filled question is a starting
  point; merchant may want to tweak before sending.
- Avoids accidental fires from misclicked affordance icons.
- Demo still feels snappy because agent's response begins
  streaming within ~1s of Enter.

### 5.4 — State plumbing

The "Ask about this" affordance triggers multiple state
transitions in one click:

1. `state.active_agent` updates to the target specialist.
2. The chat input field gets pre-filled with the templated
   question.
3. The chat panel scrolls into view (if needed in split-mode).
4. The specialist chip switcher's active state updates.

Implementation:

- A button callback that sets multiple session-state values.
- A rerun that picks up the new state and renders accordingly.
- For the chat input pre-fill: a `state.chat_input_prefill`
  value that the chat panel's `st.chat_input` reads on render
  and clears after consumption.

### 5.5 — Per-card "Ask about this" routing table

| Card | Target specialist | Pre-fill question template |
|---|---|---|
| 1.1 Revenue KPI | Anomaly | "What's driving the change in my revenue this week?" |
| 1.2 Transactions KPI | Anomaly | "What's driving the change in my transaction count this week?" |
| 1.3 Avg basket KPI | Demand | "What's changing about my average ticket?" |
| 1.4 Unique customers KPI | Demand | "Is my customer count growing or declining? Why?" |
| 1.5 Anomaly count KPI | Anomaly | "What's flagged this week?" |
| 2.1 Revenue trajectory | Anomaly | "What's behind the revenue trajectory I'm seeing?" |
| 2.2 Transaction trajectory | Anomaly | "What's driving the change in transaction count?" |
| 2.3 Hour×day heatmap | Trade | "What does my hour-by-day pattern tell me about my customer base?" |
| 3.1 Neighborhood map | Trade | "Which of my neighborhoods are over- or under-performing, and is the issue mine or the market's?" |
| 3.2 Store performance | Anomaly | "Which stores are showing unusual traffic this week?" |
| 4.1 Category mix (Grocers) | Demand | "Where am I over- or under-indexed in my basket mix vs peers?" |
| 4.1 Category mix (TBL) | Demand | "What does my menu mix look like? Where am I most concentrated?" |
| 4.1 Category mix (TJX) | Demand | "What does my category mix look like? Where am I concentrated?" |
| 4.2 SKU performance (top view) | Demand | "Tell me more about my top-performing SKUs and what's driving them" |
| 4.2 SKU performance (bottom, Grocers) | Anomaly | "Which SKUs are spiking or dropping unusually?" |
| 4.2 SKU performance (bottom, TBL) | Anomaly | T-A2 question text |
| 4.2 SKU performance (bottom, TJX) | Anomaly | R-A2 question text |
| 5.1 New vs returning | Demand | "What's the composition of my customer base this week — are new customers growing or my base growing?" |
| 5.2 Transactions per customer | Demand | "What does my customer frequency distribution tell me about loyalty?" |
| 5.3 Customer home geography | Trade | "Where do my customers live relative to my stores?" |

### 5.6 — Drilldown vs. "Ask about this"

Two ways a merchant can go deeper:

- **Drilldown (in-card):** Click a chart element (a bar, a
  polygon, a row) → the card content changes to show detail.
  Stays within the dashboard, no agent involvement.
- **Ask about this (to chat):** Click the affordance icon →
  context flows to the chat panel, agent answers in chat.
  Leaves the dashboard, agent engagement.

Both exist on most cards. Drilldowns are own-data inspection;
"Ask about this" is for cross-merchant or causal questions.

### 5.7 — Edge cases

- **Disabled state:** When `agent_running == True`, the "Ask
  about this" affordance on every card is disabled.
- **Affordance on cards with no agent counterpart:** All 15
  cards have the affordance. If a card's natural question has
  weak agent coverage (e.g., the anomaly count KPI for TBL
  routes to anomaly questions that are own-data only), the agent
  still answers using tenant-only computation.
- **Pre-fill on free-form questions:** Merchant can edit the
  pre-filled text before sending. If they fully clear and
  rewrite, the orchestrator routes the rewritten question.

### 5.8 — Out of scope

- Auto-suggesting follow-up "Ask about this" buttons within
  agent responses (Phase 5).
- Pre-filled prompts that include structured chart context (v3
  uses plain-text pre-fill only; future v4+ could add
  chart-context payloads).

---

## Section 6 — Visual language and styling

The styling system extends v2.5's existing primitives. New
encoding conventions are added to support the 9 chart patterns;
the existing CSS variables, panel-card primitive, and Plotly
layout helper stay intact.

### 6.1 — Palette extensions

v2.5's existing palette:

- `--accent: #0F4C81` (own-merchant brand baseline)
- `--accent-soft: #D8E2EE`
- `--surface: #F7F8FA`, `--border: #E2E5EA`
- Per-merchant brand colors: `--c-krg`, `--c-acm`, `--c-wdx`,
  `--c-tbl`, `--c-tjx`
- `--good: #2F855A`, `--bad: #C44536`, `--anomaly: #C44536`

v3 extensions:

**Peer encoding family (NEW):**

- `--peer-a: #6B7280` (medium gray)
- `--peer-b: #9CA3AF` (lighter gray)
- `--peer-aggregate: #4B5563` (darker gray for combined peer
  aggregate)

These are deliberately gray-family to keep own-merchant visually
dominant.

**Diverging palette (NEW):**

- `--diverging-low: #C44536` (red — below baseline)
- `--diverging-mid: #FFFFFF` (white — on-baseline)
- `--diverging-high: #0F4C81` (blue — above baseline)

Used by Pattern 3 (heatmap) and Pattern 5 (waterfall).

**Sequential palette (NEW):**

- Brand-family gradient from `--accent-soft` (lightest) to
  `--accent` (saturated).
- Multi-stop in Plotly:
  `[#FFFFFF, #D8E2EE, #94B0CC, #4B7BA6, #0F4C81]` (5-stop).
- Used by Pattern 6 (map) and Pattern 3 (own-only sequential
  mode).

### 6.2 — Typography (preserved)

System sans-serif from v2.5 stays. No font changes.

### 6.3 — Component primitives

v2.5's primitives:

- `.kpi` — KPI card with `.num`, `.label`, `.delta`, optional
  `.hint`.
- `.panel-card` — generic chart-wrapping card with
  `.panel-title`, `.panel-sub`.

v3 additions:

**Card structure (extension of `.panel-card`):**

- `.panel-card` base unchanged.
- New `.panel-card__header` containing title + "Ask about this"
  affordance (right-aligned).
- New `.panel-card__takeaway` — the computed takeaway subtitle.
- `.panel-card__chart` — chart area.
- `.panel-card__footnote` — for caveats.

**"Ask about this" affordance:**

- New CSS class: `.ask-about`.
- Position: top-right of card header.
- Default state: subtle (40% opacity), 16px icon.
- Hover state (desktop): full opacity, background fill, slight
  shadow.
- Always-visible state (mobile): full opacity.
- Disabled state: full opacity but desaturated (grayscale) when
  `agent_running`.

**Specialist chip selector:**

- New CSS class family: `.specialist-chip`,
  `.specialist-chip--active`, `.specialist-chip--disabled`.
- Horizontal pill layout, 4 chips for the 4 specialists.
- Active chip: brand color background, white text.
- Inactive chips: light gray background, dark text.
- Disabled state during agent run.

### 6.4 — Encoding conventions

**Own merchant encoding:**

- Color: brand color (`--accent` or per-merchant
  `--c-<merchant>`).
- Line style: solid for line charts, full-fill for bars.
- Marker style: circle markers on data points (line charts).
- Z-order: always rendered last so own-merchant sits visually on
  top.

**Peer encoding:**

- Colors: gray family.
- Line style: peer_a dashed, peer_b dotted.
- Z-order: rendered before own-merchant.

**Diverging encoding:**

- Center: white (neutral).
- Direction: red for low/negative, blue for high/positive.
- Center value: zero (or baseline ratio of 1.0).

**Sequential encoding:**

- Light brand-family at low values.
- Saturated brand at high values.
- 5-stop gradient for finer resolution.

**Reference lines and baselines:**

- Always shown as a subtle horizontal/vertical light gray line.
- Labeled with the value.
- Sits behind data — never visually competes.

### 6.5 — Loading and empty states

**Loading:**

- For charts: skeleton-shaped gray placeholder of the card's
  expected dimensions.
- For tables: skeleton table rows (5-10 empty rows).
- Streamlit's built-in spinner suffices for short loads; for
  visible cards, skeleton is preferable.

**Empty:**

- For charts with no data: gray text "No data available" with
  secondary text explaining why.
- For tables with empty filtered results: same treatment in the
  table area.

### 6.6 — k-anonymity suppression

- For cells/rows below k=5 (per Phase 1.5 enforcement): omit
  with a footnote "Some peer cells suppressed for privacy
  (k≥5)".
- Don't show "—" or "N/A" — omit cleanly.

### 6.7 — Visual hierarchy across the dashboard

- **Section 1 (KPIs):** Heaviest — large numbers, merchant's
  first read.
- **Section 2 (Trajectories):** Medium — three cards across,
  takeaway subtitle prominent.
- **Section 3 (Geography):** Medium — map dominates Card 3.1,
  table is secondary.
- **Section 4 (Catalog):** Medium — bars and tables.
- **Section 5 (Customers):** Lighter — three smaller cards.

Sections 1-2 are at the top because they're the most-consulted
views. Section 5 is at the bottom because customer questions are
more for monthly/quarterly review than weekly.

### 6.8 — Mobile responsiveness (re-confirming)

v3 is desktop-first. Below 1024px, the right rail becomes a
bottom drawer triggered by an icon. At <768px, the dashboard
column reflows to single-column. Not polished, just readable.

### 6.9 — Implementation notes (for Phase 4)

- `styling.py` gets ~50 LOC of additions: chip selector styles,
  "Ask about this" affordance styles, peer-encoding CSS
  variables, diverging-encoding CSS variables.
- The Plotly layout helper (`_plotly_layout`) stays. The
  per-pattern helpers (Phase 4 work) accept color arguments and
  use the new CSS variables when called from the dashboard.
- For Folium maps (Pattern 6), use the sequential gradient
  defined here. Coordinated color application across all map
  cards (3.1, 5.3).

---

## Section 7 — placeholders.py refactor scope

`placeholders.py` is the 1,441-LOC file the audit flagged as
monolithic — three logically separate concerns mixed together.
Phase 4 splits it.

### 7.1 — Current state (per audit)

`src/dashboard/placeholders.py` (1,441 LOC) holds three
concerns:

1. **Suggested-question registry** (~200 LOC) —
   `QUESTIONS_GROCERY`, `QUESTIONS_QSR`, `QUESTIONS_RETAIL`,
   plus the `questions_for(merchant_id)` accessor.
2. **Hardcoded handler bodies** (~900 LOC) — 16 mock-mode
   functions: `h_pricing_*`, `h_anomaly_*`, `h_demand_*`,
   `h_trade_*`.
3. **LLM dispatch + orchestration** (~300 LOC) —
   `_llm_dispatch`, `dispatch`, `dispatch_orchestrated`,
   `_keyword_route_for_fallback`, the Haiku-router logic, and
   session-state caching.

Plus shared metadata: `AGENT_LABELS`, `AGENT_DESCRIPTIONS`,
per-question→handler mapping.

### 7.2 — Target state (Phase 4)

Two new files; original file deleted.

#### `src/dashboard/questions.py` (NEW, ~150 LOC)

The question registry, driven by `V3_QUESTIONS.md` content.

Contents:

- Per-viewer per-specialist suggested-question definitions.
- The `questions_for(merchant_id, specialist)` accessor.
- Metadata per question: question ID, question text, target
  specialist, expected chart pattern.

Question registry shape:

```python
QUESTIONS = {
    "GROCER": {
        "pricing": [
            {"id": "P1", "text": "How do my prices compare to peer grocers across categories?", "pattern": "pattern_3_heatmap"},
            {"id": "P2", "text": "How does my pricing positioning compare across staple categories vs non-food categories?", "pattern": "pattern_2_comparison"},
            {"id": "P3", "text": "Which categories show the biggest pricing-leverage opportunity?", "pattern": "pattern_4_scatter"},
        ],
        "anomaly": [...],
        "demand": [...],
        "trade": [...],
    },
    "QSR": {  # TBL
        "pricing": [{"id": "T-P1", ...}, {"id": "T-P2", ...}, {"id": "T-P3", ...}],
        "anomaly": [{"id": "T-A1", ...}, ...],
        "demand": [{"id": "T-D1", ...}, ...],
        "trade": [{"id": "T1", ...}, {"id": "T2", ...}, {"id": "T4", ...}],
    },
    "RETAIL": {  # TJX
        "pricing": [{"id": "R-P1", ...}, ...],
        "anomaly": [{"id": "R-A1", ...}, ...],
        "demand": [{"id": "R-D1", ...}, ...],
        "trade": [{"id": "T1", ...}, {"id": "T2", ...}, {"id": "T4", ...}],
    },
}
```

Helper: `segment_for_merchant(merchant_id)` returns "GROCER" /
"QSR" / "RETAIL" so `questions_for` can route correctly.

#### `src/dashboard/agents.py` (NEW, ~300 LOC)

The LLM dispatch and orchestration logic from `placeholders.py`:

- `dispatch(specialist_id, question_id, merchant_id,
  progress_callback, on_token_callback) → response_dict` —
  handles a clicked suggested question.
- `dispatch_orchestrated(merchant_id, question_text, ...) →
  response_dict` — handles a free-form question, routes via
  Haiku router.
- `_run_specialist` (renamed from `_llm_dispatch`) — inner
  LLM-runner that constructs a `MerchantContext` and invokes
  the right specialist.
- `_keyword_route_for_fallback` — keyword fallback when the
  Haiku router fails.
- Session-state caching for reentrancy.

`AGENT_LABELS` and `AGENT_DESCRIPTIONS` move here.

#### `src/dashboard/placeholders.py` (DELETED)

All handler bodies deleted. Mock fallback dropped entirely.

### 7.3 — What happens when the LLM call fails?

If the agent's LLM call fails (API error, timeout, network), the
chat shows an honest error: "Couldn't reach the agent. Please try
again in a moment." No silent fallback to canned prose.

A stakeholder seeing canned mock prose during a live demo is
worse than seeing an error and retrying.

### 7.4 — The dispatch contract stays stable

The function signature `dispatch(specialist_id, question_id,
merchant_id, progress_callback, on_token_callback) →
response_dict` is what `chat.py` calls. Phase 4 preserves this
contract — `chat.py` updates its imports
(`from src.dashboard.placeholders import dispatch` →
`from src.dashboard.agents import dispatch`) but its logic
doesn't change.

Same for `dispatch_orchestrated`.

### 7.5 — Other dashboard code updates needed

- **`chat.py`** updates imports from `placeholders` to `agents`
  and `questions`.
- **`app.py`** updates any imports from `placeholders` (likely
  `AGENT_DESCRIPTIONS` access).
- **`views.py`** doesn't import from `placeholders` — no
  changes.
- **`data.py`** doesn't import from `placeholders` — no changes.

### 7.6 — Telemetry footer

`app._render_telemetry_footer` reads
`src.agents.llm.session_totals()` — this is `src.agents`, not
`src.dashboard.placeholders`, and unchanged.

### 7.7 — Implementation order

The refactor is best done as one of the first sub-steps of
Phase 4, before chart work:

1. Create `src/dashboard/questions.py` with the question
   registry.
2. Create `src/dashboard/agents.py` with the dispatch logic.
3. Update `chat.py` imports.
4. Update `app.py` imports.
5. Delete `placeholders.py`.
6. Run tests — confirm all 212 tests still pass.
7. Smoke-test the dashboard locally.

One commit's worth of work. Roughly half a day to do carefully.

### 7.8 — What's not in this refactor

- The Haiku orchestrator's routing logic stays as-is.
- The `MerchantContext` construction logic stays in
  `_run_specialist`.
- Session-state caching stays.

### 7.9 — Out of scope for this refactor

- Agent prompt updates (Phase 5).
- New specialist implementations.
- Changes to the agent tool interface (`query_tenant`,
  `query_lake` from Phase 1.5).

---

## Section 8 — Implementation sequencing within Phase 4

Six sub-phases, sequenced:

### 8.1 — 4.0 Pre-work: placeholders.py refactor (1 commit, ~0.5 day)

Per Section 7. Split `placeholders.py` into `questions.py` +
`agents.py`, delete the handler bodies, update imports. Run
tests, smoke-test the dashboard.

Output: cleaner code structure ready for chart work. No visible
UI changes.

Commit: `Phase 4.0: refactor placeholders.py into questions.py +
agents.py; drop mock fallback`

### 8.2 — 4.1 Checkpoint A: A1 end-to-end (1-2 commits, ~1 day)

Build the University City decline question (A1) from data query
to rendered chart with takeaway subtitle and "Ask about this"
affordance. This is the template for every subsequent question +
card.

Sub-steps:

- Create `src/dashboard/chart_patterns.py` (single module with
  one render function per pattern).
- Implement Pattern 1 (`render_time_series_vs_peers`).
- Implement the takeaway-subtitle templating helper
  (f-string-based, takes pattern template + computed values).
- Create the data query for A1.
- Wire A1 as the first suggested question under the Anomaly
  specialist for grocer viewers.
- Implement the "Ask about this" affordance plumbing
  (`state.chat_input_prefill`, the chip-switcher snap, the
  disabled-while-running state).
- Smoke-test in Streamlit.

End state of Checkpoint A: open the dashboard as KRG, click
"What's driving the transaction drop at my University City
stores?", see the Pattern 1 chart render with the takeaway
subtitle "Your UC transactions dropped 46% from baseline by week
of Apr 27; peers also declined (31-33%). The pattern is
market-wide." Click "Ask about this" on any dashboard card →
chat input pre-fills with templated question, specialist
switcher snaps, awaits confirm.

Commit: `Phase 4.1: Checkpoint A — A1 end-to-end with Pattern 1,
takeaway template, and Ask-about-this affordance`

**Pause point:** smoke-test, review in chat together, lock the
shape before proceeding.

### 8.3 — 4.2 Checkpoint B: remaining 11 grocer questions (3-4 commits, ~3-4 days)

Build the remaining 11 grocer questions (P1, P2, P3, A2, A3, D3,
D4, D7, T1, T2, T4) using the pattern helpers, in rough order of
pattern complexity:

**Sub-step 4.2a — Pattern 2 questions (P2, D3):**

- Implement Pattern 2 (`render_cross_merchant_comparison`).
- Build P2 (staple vs non-food two-panel) and D3 (diverging
  basket-mix bars).
- Verify takeaway templates compute correctly.

**Sub-step 4.2b — Pattern 3 questions (P1):**

- Implement Pattern 3 (`render_heatmap`) with diverging color
  mode.
- Build P1 (category × peer heatmap).
- Pattern 3 has both diverging (cross-merchant) and sequential
  (own-only) modes — implement diverging first, sequential later
  when own-only cards (Card 2.3 heatmap or T-A3) need it.

**Sub-step 4.2c — Pattern 4 questions (P3, D4):**

- Implement Pattern 4 (`render_scatter_with_peers`).
- Build P3 (volume-weighted pricing scatter) and D4
  (price-volume relationship scatter).

**Sub-step 4.2d — Pattern 5 questions (D7):**

- Implement Pattern 5 (`render_waterfall`) with cross-merchant
  and own-vs-own-baseline modes.
- Build D7 (revenue gap decomposition vs peer).

**Sub-step 4.2e — Pattern 6 questions (T1, T2, T4):**

- Implement Pattern 6 (`render_geographic_map`) — Folium-based.
- Build T1, T2, T4 — three Trade questions all on Folium.
- Heaviest single sub-step.
- Pause for performance check — Folium with overlays at 5
  grocers × 30 stores = 150 markers.

**Sub-step 4.2f — Pattern 9 questions (A2, A3):**

- Implement Pattern 9 (`render_table_with_drilldown`) —
  sortable Streamlit table with row-click drilldown.
- Build A2 (per-store divergence) and A3 (single-store spike
  detection).

Each sub-step is its own commit. Smoke-test after each.

End state of Checkpoint B (grocer side): all 12 grocer questions
render in their specialist chat panels for KRG/ACM/WDX viewers.
Takeaway subtitles compute correctly. "Ask about this" pre-fills
work. Drilldowns work where defined.

### 8.4 — 4.3 Checkpoint B continued: TBL and TJX questions (2 commits, ~2-3 days)

Build the 18 net-new TBL/TJX questions using the existing pattern
helpers in own-only modes:

- TBL pricing: T-P1, T-P2, T-P3 (own-only Pattern 1, Pattern 1,
  Pattern 2).
- TBL anomaly: T-A1, T-A2, T-A3 (Pattern 9, Pattern 9, Pattern 3
  own-only diverging).
- TBL demand: T-D1, T-D2, T-D3 (Pattern 2, Pattern 1, Pattern 5
  own-vs-own baseline).
- TJX pricing: R-P1, R-P2, R-P3 (Pattern 1, Pattern 9, Pattern
  2).
- TJX anomaly: R-A1, R-A2, R-A3 (Pattern 9, Pattern 9, Pattern 3
  own-only diverging).
- TJX demand: R-D1, R-D2, R-D3 (Pattern 2, Pattern 1, Pattern 5
  own-vs-own baseline).

Per-viewer question routing: `questions.py`'s registry returns
the right set based on `segment_for_merchant(merchant_id)`.
Pattern helpers themselves don't need per-viewer logic.

Pattern 3 own-only diverging mode (T-A3, R-A3) is the genuinely
new pattern application. Implement the own-only mode in the
Pattern 3 helper.

Pattern 5 own-vs-own baseline mode (T-D3, R-D3) is similar —
extension of the cross-merchant waterfall.

Commits: ~2 (one for TBL questions, one for TJX questions).

End state of Checkpoint B (full): all 12 grocer + 9 TBL + 9 TJX
questions render. Plus the trade reuse (T1/T2/T4) for TBL and
TJX.

**Pause point:** smoke-test all viewers, review together, lock
the chat-side experience before moving to dashboard chrome.

### 8.5 — 4.4 Dashboard chrome and KPI strip (5 commits, ~1.5 days)

Build the dashboard-side spine — the 15 cards from Section 3.

**Sub-step 4.4a — KPI strip (Section 1, 5 cards):**

- Card 1.1-1.4: STAYS from v2.5 with minor adjustments.
- Card 1.5 Anomaly count: NEW.
- Implement Pattern 8 (KPI callout) helper.

**Sub-step 4.4b — Performance over time (Section 2, 3 cards):**

- Card 2.1, 2.2: REWORKED from v2.5; use Pattern 1 own-only.
- Card 2.3: STAYS from v2.5; uses Pattern 3 own-only sequential
  mode.

**Sub-step 4.4c — Geography (Section 3, 2 cards):**

- Card 3.1: REWORKED from v2.5; needs new data helper and
  polygons.
- Card 3.2: STAYS from v2.5 with minor adjustments.

**Sub-step 4.4d — Catalog (Section 4, 2 cards):**

- Card 4.1: REWORKED from v2.5 donut to horizontal share bar.
- Card 4.2: Consolidates v2.5 top-5 + new underperformers.

**Sub-step 4.4e — Customers (Section 5, 3 cards):**

- Card 5.1: NEW data helper, NEW chart.
- Card 5.2: STAYS from v2.5 with takeaway template added.
- Card 5.3: NEW data helper, NEW Folium map.

Each sub-step is one commit. Smoke-test after each.

### 8.6 — 4.5 Visual consistency pass (1-2 commits, ~1 day)

After all cards render:

- Confirm palette application consistent across all 15 cards.
- Confirm own/peer encoding rules apply correctly.
- Confirm loading states render predictably.
- Confirm empty states are clean.
- Confirm "Ask about this" affordances appear consistently.
- Confirm k=5 suppression works.
- Spacing, typography, hover treatments, mobile-readable
  behavior.

Commits: 1-2 for styling refinements.

### 8.7 — 4.6 Phase 4 close-out (1 commit, ~0.5 day)

Final integration:

- Smoke-test all 5 viewers end-to-end.
- Verify each viewer's 12 suggested questions render correctly.
- Verify "Ask about this" routes correctly.
- Verify agent responses stream cleanly.
- Confirm 212 tests still pass.
- Update `V3_AUDIT.md` with Phase 4 close-out.

Commit: `Phase 4 close-out: dashboard redesign complete, 15
cards across 5 sections, 30 suggested questions across 5
viewers`

### 8.8 — Phase 4 total estimate

Roughly 10-15 commits across 7-9 days of work. Bulk is sub-step
4.2 (pattern helpers + grocer questions) and 4.4 (dashboard
cards). Anything materially less than 7 days suggests we're
cutting corners; anything materially more than 9 days suggests
scope creep.

### 8.9 — Risks during Phase 4

- **Folium performance with overlays** (4.2e and 4.4c, 4.4e).
  Three Trade questions + two map cards on the dashboard = 5
  map renders per viewer. If Folium slows down, fall back to
  Plotly's geo capabilities or reduce overlay complexity.
- **Pattern 3 mode handling** — diverging vs sequential vs
  own-only diverging. One mode parameter; not a separate
  function per mode.
- **Per-viewer question routing in `questions.py`**. Easy to
  miss-key a question; testing should verify each viewer ×
  specialist combination returns the right 3 questions.
- **"Ask about this" state plumbing**. The affordance has to
  update `state.active_agent`, set `state.chat_input_prefill`,
  and trigger a chat-panel scroll. Three state updates per
  click. Test each card's affordance end-to-end during the
  visual consistency pass.

### 8.10 — Phase 4 deploys after Phase 6

Per locked decision, no HF Spaces redeploy after Phase 4. Local
verification only. The next deploy happens after Phase 6 (demo
prep).

---

## Section 9 — Out of scope for Phase 4

### 9.1 — Deferred to Phase 5 (agent prompt redesign)

- **Specialist agent prompt rewrites** to use `V3_QUESTIONS.md`
  framing (Headline → Evidence → Therefore → Caveats response
  shape). Phase 4 wires up the chat plumbing and question
  routing; Phase 5 makes agent responses themselves match v3
  voice and structure.
- **Pattern-aware agent responses**. The pattern dispatcher in
  `chart_patterns.py` is invoked from the dashboard side in
  Phase 4. For free-form questions, the agent doesn't yet emit
  a pattern selection in its response — that's a Phase 5
  contract update.
- **Viewer-aware orchestrator behavior**. When a TBL or TJX
  viewer asks a free-form pricing question, the orchestrator
  currently routes to the pricing specialist, which uses the
  v2.5 prompt. Phase 5 updates the specialist prompts to
  acknowledge "no same-segment peer" gracefully.
- **Generic agent descriptions polish**. Phase 4 introduces
  generic descriptions per Section 4; Phase 5 may tune them
  based on how they read once the redesigned prompts land.

### 9.2 — Deferred to Phase 6 (demo script + dry runs)

- **Demo narrative arc**. The University City decline beat is
  the anchor; the full demo flow is a Phase 6 artifact.
- **Stakeholder rehearsal**. Live testing the dashboard with
  someone outside the build process.
- **Demo-mode toggles**. Any UI affordances specifically for
  demo settings.

### 9.3 — Deferred to after Phase 6

- **HF Spaces redeploy**. Local verification only during Phase
  4; deploy is held until the full v3 experience is demo-ready.
- **LFS-tracked DB regeneration**. Current DB stems from Phase
  1.6 calibration. If Phase 4 surfaces a need to recalibrate
  further, that's a sub-task captured for the post-Phase-6
  deploy prep.

### 9.4 — Explicitly not in scope, period

- **New specialists or new agents**. Same 5 agents (orchestrator
  + 4 specialists). No new agent personalities, no new tools,
  no new SDK changes.
- **New data dependencies**. The materialized lake from Phase
  1.5, the tenant views, and the calibrated data from Phase 1.6
  are the data foundation. Phase 4 does not add new tables,
  columns, or external data sources.
- **Authentication, user accounts, multi-user**. v3 is a
  single-user demo product.
- **Mobile polish**. v3 is desktop-first per Section 2.
- **Internationalization**. English only. Charlotte metro only.
- **Theme customization**. Single brand theme. No light/dark
  mode toggle.
- **Export functionality**. No "Download chart" buttons, no CSV
  export, no PDF generation.
- **Real-time updates**. No live data feed; static snapshot at
  last DB build time.

### 9.5 — Documentation deferred until needed

- **API documentation for pattern helpers**. The 9 pattern
  functions in `chart_patterns.py` are documented inline with
  docstrings and `chart_patterns.md`.
- **End-user merchant documentation**. v3 is internal demo
  content; no user guides, no onboarding flow, no help docs.
