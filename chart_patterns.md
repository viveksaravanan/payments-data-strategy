# Chart patterns

The 9 chart patterns the v3 dashboard and agents follow. This file is
the standalone reference duplicated from `V3_QUESTIONS.md` Section 4 —
Phase 4 implementation (chart helpers) and Phase 5 implementation
(agent prompts) reference this file directly rather than paging
through `V3_QUESTIONS.md`.

**Purpose.** The 9 patterns are the contract between the dashboard
renderer and the agents:

- The dashboard implements one helper per pattern. Each helper
  takes a pattern name, the chart data, and a computed takeaway
  subtitle, and renders the chart.
- The agents (in their system prompts) select one of these 9
  patterns when answering both suggested and free-form questions,
  populate the data and subtitle, and emit a structured response.
- Agents never invent a chart type outside the 9. If no pattern
  fits, the agent answers text-only.

`V3_QUESTIONS.md` has the question-side detail — the 12 suggested
questions, the cuts, the rubric assessments. This file has the
visualization-side detail — the patterns and their encoding rules.
If the two files drift, `V3_QUESTIONS.md` is the source of truth.

---

## Pattern 1: Time-series-vs-peers

**Question shapes it fits:** trend questions, "is this me or
market" disambiguation, momentum over a window.

**Chart type:** line chart with multiple series — own + per-peer
(or own + peer aggregate).

**Encoding rules:**
- Own merchant: brand color, solid line.
- Peers: gray family — peer_a dashed, peer_b dotted, peer aggregate
  solid gray.
- X-axis: time (weeks by default; days on drill-down).
- Y-axis: metric. Normalize to baseline = 100 by default so
  cross-grocer scale doesn't dominate; absolute-value toggle
  available.

**Takeaway subtitle template:**
*"Your {metric} {direction} {pct}% by {trough_or_peak_date}; your
peers {co-moved / diverged} ({pct_per_peer})."*

**Interactivity defaults:**
- Hover: tooltip with absolute value, ratio to baseline, grocer
  label.
- Click week: drill to daily breakdown for that week (still
  Pattern 1, finer granularity).
- "Ask the agent about this" button surfaces the metric + the
  notable week.

**Used by suggested questions:** A1.

**Library:** Plotly.

---

## Pattern 2: Cross-merchant comparison, single dimension

**Question shapes it fits:** "how do I compare on [metric]" with a
single grouping dimension (per peer, per store, per category).
Used by P2 in a two-panel layout (staple tier vs non-food tier) and
by D3 in a diverging-bar layout (basket-mix over/under-indexing).
Also available for free-form answers where heatmaps (Pattern 3)
are too heavy for a one-dimensional comparison.

**Chart type:** horizontal grouped bar chart.

**Encoding rules:**
- Y-axis: the comparison dimension (categories, peers, etc.).
- X-axis: metric value.
- Own merchant: brand color; peers: gray family (per-peer
  variations).
- Reference line (peer-average or zero) drawn faintly when relevant.

**Takeaway subtitle template:**
*"You {direction} peers on {metric} by {amount}."*

**Interactivity defaults:**
- Hover: raw value + comparison reference.
- Click bar: drill to whichever pattern best fits the underlying
  detail (often Pattern 1 if temporal, Pattern 9 if enumerated).

**Used by suggested questions:** P2 (in a two-panel layout), D3
(with diverging encoding).

**Library:** Plotly.

---

## Pattern 3: Cross-merchant heatmap

**Question shapes it fits:** two-dimensional comparison (category ×
peer, SKU × peer, neighborhood × week). The diverging-color
encoding makes outliers visually instant.

**Chart type:** heatmap grid with diverging color scale.

**Encoding rules:**
- Rows and columns: the two comparison dimensions.
- Cell value: numeric gap or index. Cell text overlays the value.
- Color: diverging red-white-blue (or equivalent), white at zero.
  Saturation indicates magnitude.
- Axis labels in gray; cell text in dark contrast color.

**Takeaway subtitle template:**
*"You're {direction} peers on {top_dimension} by {pct}%; gap is
widest in {cell_label}."*

**Interactivity defaults:**
- Hover cell: raw values for both dimensions, k-anonymity status.
- Click cell: drill to Pattern 1 (time-series for that cell) or
  Pattern 4 (scatter context).

**Used by suggested questions:** P1.

**Library:** Plotly.

---

## Pattern 4: Scatter with peer context

**Question shapes it fits:** two-axis trade-offs (gap × volume,
own-share × peer-share). Quadrants tell the story.

**Chart type:** scatter plot, optional 45° parity line, optional
quadrant gridlines.

**Encoding rules:**
- X-axis and Y-axis: the two relationship dimensions.
- Point size: a third dimension (volume, revenue) for emphasis.
- Point color: own merchant brand color; reference lines gray.
- Quadrant labels in light gray text, when used.

**Takeaway subtitle template:**
*"{top_item} sits at {coord}; {N} categories sit off the parity
line by more than {threshold}."*

**Interactivity defaults:**
- Hover point: full numeric breakdown for that point.
- Click point: drill to Pattern 1 (temporal context) or Pattern 3
  (heatmap context).

**Used by suggested questions:** P3, D4.

**Library:** Plotly.

---

## Pattern 5: Decomposition / waterfall

**Question shapes it fits:** "what drives the gap" — breaks a
composite metric into its driver contributions.

**Chart type:** waterfall bar chart with connecting bars between
driver bars.

**Encoding rules:**
- X-axis: drivers (named categories of contribution).
- Y-axis: contribution to the total gap (positive or negative).
- Positive bars (own ahead) in brand color; negative bars (peer
  ahead) in diverging red.
- Connecting bars (thin gray) show cumulative running total.

**Takeaway subtitle template:**
*"Of the {total_gap}, {dominant_driver} contributes {pct}pp; the
other drivers are within noise."*

**Interactivity defaults:**
- Hover bar: contribution value + raw own/peer numbers for that
  driver.
- Click bar: drill to Pattern 1 for that driver's 90-day
  trajectory.

**Used by suggested questions:** D7.

**Library:** Plotly.

---

## Pattern 6: Geographic map

**Question shapes it fits:** spatial / locational questions —
per-neighborhood performance, store-vs-customer mismatch, expansion
scoring.

**Chart type:** Folium map of Charlotte metro with neighborhood
polygons (choropleth) and store markers (optional layers).

**Encoding rules:**
- Polygons: colored by the question's metric (diverging if
  performance-vs-baseline, sequential if a score).
- Own store markers: brand color, distinctive shape.
- Peer store markers: gray, distinct shape when shown.
- Optional customer-home density layer: sequential color scale.

**Takeaway subtitle template:**
*"{N} {neighborhoods} {direction}; {top_neighborhood} is the
{worst / best / strongest}."*

**Interactivity defaults:**
- Hover polygon: neighborhood name + the relevant metric values.
- Click polygon: drill to Pattern 1 (time-series for that
  neighborhood) or Pattern 9 (detail table for that neighborhood).
- Layer toggles: own stores, peer stores, customer density.

**Used by suggested questions:** T1, T2, T4.

**Library:** Folium.

---

## Pattern 7: Small-multiples

**Question shapes it fits:** per-dimension comparison across many
slices (per-store mini-trends, per-category mini-views) where a
single chart would be too dense. Available for free-form answers.

**Chart type:** grid of mini-panels, each a Pattern 1 or Pattern 2
miniature, with a consistent y-scale across panels.

**Encoding rules:**
- Each panel: same chart type, same scale, varying by a single
  identifier.
- Panel layout: consistent grid (e.g., 4 cols × N rows).
- Own merchant: brand color across all panels; peers: gray.

**Takeaway subtitle template:**
*"{N} of {M} {dim} show {signal_direction}."*

**Interactivity defaults:**
- Hover panel: panel-specific detail.
- Click panel: drill to that subset in the primary pattern (e.g.,
  full-size Pattern 1 for that one identifier).

**Used by suggested questions:** none in the final 12; available
for free-form.

**Library:** Plotly.

---

## Pattern 8: Single-number callout / KPI

**Question shapes it fits:** at-a-glance status — the dashboard
KPI strip. Not a chart per se; the strip is the entry point that
leads merchants into the primary suggested questions.

**Chart type:** large numeric callout with optional sparkline +
delta arrow.

**Encoding rules:**
- Number: large, brand color, the headline value.
- Sparkline: small, gray, optional, shows 90-day trajectory.
- Delta arrow: red (down), green (up), gray (flat); paired with
  the percentage change vs baseline.

**Takeaway subtitle template:**
*"{Metric}: {value} ({direction} {pct} vs {baseline})."*

**Interactivity defaults:**
- Hover: explanation of the metric and baseline.
- Click: drill to the primary suggested question that anchors on
  this KPI (e.g., a "University City" KPI clicked drills to A1's
  time-series with neighborhood pre-filtered).

**Used by suggested questions:** Dashboard KPI strip (not anchored
to a specific question in the final 12; serves as navigation into
them).

**Library:** Streamlit native.

---

## Pattern 9: Table-with-drilldown

**Question shapes it fits:** enumeration ("which X show Y"),
ranking, store-level or SKU-level lists where the merchant needs to
scan a sorted list of items.

**Chart type:** sortable table; each row clickable to drill into
another chart pattern.

**Encoding rules:**
- Columns: identifying fields + the comparison metrics + an
  optional peer-context column.
- Sortable on every numeric column.
- Conditional row highlighting for the most-flagged rows.
- Color cues on ratio columns (diverging).

**Takeaway subtitle template:**
*"{N} {items} flagged; sort by {column} to prioritize."*

**Interactivity defaults:**
- Hover row: full numeric breakdown.
- Click row: drill to Pattern 1 (time-series for that item) or
  Pattern 6 (map for that location).

**Used by suggested questions:** A2, A3.

**Library:** Streamlit native.

---

For the questions that anchor on each pattern — what they ask, the
SQL shape behind them, the therefore-test, the rubric assessment —
see `V3_QUESTIONS.md` Section 3.
