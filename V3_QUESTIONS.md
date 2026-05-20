# V3 Questions

Phase 3 close-out. The question-level audit that translates Phase 1.6's
calibrated data foundation into the 12 suggested questions the v3
dashboard surfaces, plus the 9 chart patterns those questions (and
free-form ones) live inside of.

---

## Section 1 — Process

Phase 3 drafted candidate questions for the four specialist agents
(pricing, anomaly, demand, trade), tested each against the three
rubrics from `V3_VISION.md` (merchant-seat, cross-merchant,
standalone), and narrowed to the strongest 3 per specialist — 12
finals total. The 12 finals become the suggested questions surfaced
in the dashboard's agent chat panels, and they define the chart
pattern system used to render answers to both suggested and
free-form questions. 22 candidates were considered (the original 20
plus D6 and D7 added later as the demand specialist's coverage was
revisited).

---

## Section 2 — Candidate inventory and cut decisions

22 candidates total. SELECTED entries appear in the final 12 (Section
3); CUT entries record the decision and the reasoning behind it.
Cuts were collective decisions, not arguments won — they document
the shape of the final set, not relative strength.

### Pricing (P1–P5)

| ID | Question | Signal / answer | Viz rec | Status |
|---|---|---|---|---|
| P1 | How do my prices compare to peer grocers across categories? | Per-category, per-peer unit-price index from lake aggregates. | Pattern 3 (heatmap, category × peer) | **SELECTED** |
| P2 | How does my pricing positioning compare across my staple categories vs my non-food categories? | Aggregate per-peer price gap, split by tier (staples vs non-food). | Pattern 2 (two-panel comparison, staple tier vs non-food tier) | **SELECTED** |
| P3 | Which categories show the biggest pricing-leverage opportunity? | Per-category gap vs peers × own volume; scatter quadrants. | Pattern 4 (scatter) | **SELECTED** |
| P4 | How have my prices drifted vs peers over time? | Per-category price index over the 90-day window. | Pattern 1 (time-series) | **CUT** — cut by user preference. |
| P5 | How do my private-label SKUs compare to peers? | Private-label price + share vs peers. | Pattern 3 (heatmap) | **CUT** — cut by user preference; flagged for overlap with P1. |

### Anomaly (A1–A5)

| ID | Question | Signal / answer | Viz rec | Status |
|---|---|---|---|---|
| A1 | Is this trend unique to me, or do peers see it too? | Weekly metric trajectory, own + peers, normalized to baseline. | Pattern 1 (time-series) | **SELECTED** |
| A2 | Which of my stores show abnormal traffic recently? | Per-store baseline vs recent, ranked; peer-neighborhood cross-check. | Pattern 9 (table + drilldown) | **SELECTED** |
| A3 | Which SKUs or categories are spiking or dropping unusually? | Per (SKU/category, week) recent-vs-baseline ratio; sorted by deviation. | Pattern 9 (table + drilldown) | **SELECTED** |
| A4 | When during the week/day do anomalies cluster? | Day-of-week × hour heatmap of anomaly density. | Pattern 3 (heatmap) | **CUT** — cut by user preference; hybrid feel between Anomaly and Trade. |
| A5 | Is this category-level break me or market? | Per-category recent vs baseline, own + peer aggregate. | Pattern 1 (time-series) | **CUT** — duplicative of A2's "is it me or market" mechanic; D3 and D6 cover the category lens better from the demand angle. |

### Demand (D1–D7)

| ID | Question | Signal / answer | Viz rec | Status |
|---|---|---|---|---|
| D1 | Did my recent promo lift sales as expected? | Promo-window volume vs prior baseline, own + category context. | Pattern 1 (time-series) | **CUT** — cut by user preference. |
| D2 | Which products have unexpectedly low attach rates? | Per-SKU/category attach rate vs peer baseline. | Pattern 4 (scatter) | **CUT** — cut by user preference. |
| D3 | What does my basket-mix look like compared to peers? Where am I over-indexed or under-indexed? | Per-category own share vs peer-mean share; diverging deviation. | Pattern 2 (diverging bars) | **SELECTED** |
| D4 | Which categories over- or under-perform vs peers given my mix? | Per-category own share vs peer share; off-diagonal points highlighted. | Pattern 4 (scatter) | **SELECTED** |
| D5 | Which loyal customers drive my repeat-visit rate? | Per-customer visit count + spend, segmented. | Pattern 9 (table) | **CUT** — fails the cross-merchant rubric; tenant-only question with no peer context. |
| D6 | What's driving the decoupling between traffic and revenue? | Time-series of trips vs ticket vs revenue, peer comparison. | Pattern 1 (time-series) | **CUT** — cut in favor of D7; waterfall is more visually instant and the decomposition story is more universal. |
| D7 | What's driving my revenue gap vs peers this period? | Decompose own-vs-peer revenue gap into traffic / basket / ticket / mix drivers. | Pattern 5 (waterfall) | **SELECTED** |

### Trade (T1–T5)

| ID | Question | Signal / answer | Viz rec | Status |
|---|---|---|---|---|
| T1 | Which of my neighborhoods are over- or under-performing? | Per-neighborhood transaction count vs own panel mean, with peer context. | Pattern 6 (map) | **SELECTED** |
| T2 | Where do my customers live relative to my stores? | Customer-home density vs store-location overlay. | Pattern 6 (map) | **SELECTED** |
| T3 | When do customers shop, by neighborhood? | Hour × day heatmap per neighborhood. | Pattern 3 (heatmap) | **CUT** — cut in favor of T4; expansion is a stronger strategic question than tactical hour patterns. |
| T4 | Which neighborhoods show the biggest expansion opportunity? | Underserved-opportunity score per neighborhood (customer activity ÷ local store count). | Pattern 6 (map) | **SELECTED** |
| T5 | Which neighborhoods have the best/worst customer retention? | Per-neighborhood repeat-customer ratio. | Pattern 6 (map) | **CUT** — T1 already covers neighborhood performance from the traffic angle. |

---

## Section 3 — The final 12

The 12 selected questions in delivery order — three per specialist,
sequenced by the specialist sections they live under (pricing,
anomaly, demand, trade).

---

### P1. How do my prices compare to peer grocers across categories?

**Specialist:** pricing

**The question (as a merchant would phrase it):** "Are my prices in
line with the market, or am I drifting in some categories?"

**Signal:** Per-category, per-peer mean unit-price index. Sourced
from `lake_transactions_<viewer>` aggregated by `(category, peer_id)`
on `AVG(unit_price)`, joined against the viewer's own per-category
mean from `tenant_transaction_items`. The cell value is the
percentage gap between own mean and peer mean for that category.

**Therefore-test:** The merchant identifies the 1-3 categories with
the widest gap vs a specific peer and decides whether to investigate
positioning (cost data, intentional strategy) or accept the gap.
Tomorrow's action is naming the categories that warrant a closer
look, not making blanket pricing changes.

**Cross-merchant test:** Lake-only on the peer side, tenant on the
own side. A single merchant cannot construct peer per-category price
levels from their own data; this comparison is impossible without
the cross-merchant position. **Passes.**

**Visualization pattern:** Pattern 3 (cross-merchant heatmap).

**Visualization spec:**
- Chart type: heatmap grid, rows = category, cols = peer_a / peer_b
  (grocery viewer).
- Encoding: cell color = percentage gap (own − peer) / peer.
  Diverging red-white-blue scale, white at zero. Cell text overlays
  the gap percentage.
- Color convention: own merchant doesn't get a column (own is the
  baseline); peer cols rendered in gray-family axis labels.
- Interactivity:
  - Hover: tooltip shows own price, peer price, gap percentage, peer
    line count (k≥5 enforced).
  - Click cell: drill to P2's SKU breakdown for that category × peer
    combination.
  - "Ask the agent about this": pre-fills the agent prompt with the
    category name + the highlighted peer.
- Empty / edge states:
  - If a category falls below k=5 lines at a peer → cell rendered as
    "—" with hover note "insufficient peer data".
  - If a category exists at own merchant but not at any peer → row
    omitted with footnote.

**Takeaway sentence shape (computed from data):**
*"You're priced {N}% above [peer_a / peer_b] in {top_category}; {M}%
below in {bottom_category}."*

**Rubric assessment:** Passes merchant-seat (concrete next investigation
target), cross-merchant (peer price levels unobservable single-merchant),
standalone (heatmap + takeaway readable cold). All three.

---

### P2. How does my pricing positioning compare across my staple categories vs my non-food categories?

**Specialist:** pricing

**The question (as a merchant would phrase it):** "Am I consistent
in my pricing positioning across categories, or do I price softer
on one tier and aggressive on the other?"

**Signal:** Aggregate per-peer price gap, split by tier. Tight tier
(staples — BAKERY, BEVERAGES, DAIRY, FROZEN, MEAT, PANTRY, PRODUCE,
SNACKS) versus loose tier (non-food — BABY, HOUSEHOLD, PERSONAL,
PET). For each tier, compute own-vs-peer percentage gap as a
weighted mean across categories. Two panels side by side, one per
tier. Phase 1.6 Pass 1 calibrated the two tiers to different
spread magnitudes (staples ±5%, non-food ±10%); this question
makes that calibration visible.

**Therefore-test:** Merchant sees whether their pricing posture is
consistent across tiers. If staples are competitive but non-food
shows wider gaps, the strategy may be intentional (staples as
loss-leader, margin from non-food) or unintentional (overlooked
non-food pricing). Tomorrow's action: confirm tier-level strategy
is what's intended.

**Cross-merchant test:** Lake-only on the peer side, tenant on the
own side. **Passes.**

**Visualization pattern:** Pattern 2 (cross-merchant comparison,
single dimension) used in a two-panel layout.

**Visualization spec:**
- Chart type: side-by-side panel comparison. Left panel = tight
  tier (per-category gaps as horizontal bar chart, one bar per
  peer per category). Right panel = loose tier (same shape).
- Encoding: x-axis = own-vs-peer percentage gap. Y-axis = category
  name. Bars per category split or grouped by peer (peer_a, peer_b).
- Color convention: own merchant = brand color anchor; peer bars
  in gray family — peer_a darker gray, peer_b lighter gray.
  Reference line at zero, drawn faintly.
- Interactivity:
  - Hover bar: tier, category, peer name, own price, peer price,
    gap percentage, peer line count.
  - Click bar: drill to P1's heatmap row for that category.
  - "Ask the agent about this": pre-fills with the tier name +
    the most-divergent category.
- Empty / edge states:
  - Category with peer line count below k=5 → bar omitted with
    footnote.

**Takeaway sentence shape (computed from data):**
*"Your staple tier averages {pct}% vs peer_a; non-food tier
averages {pct}%. Your pricing strategy is {symmetric / asymmetric}
across tiers."*

**Rubric assessment:** Passes merchant-seat (tier-level strategy
check), cross-merchant (peer tier-level positioning unobservable
single-merchant), standalone (two-panel layout + takeaway communicates
cold). All three.

---

### P3. Which categories show the biggest pricing-leverage opportunity?

**Specialist:** pricing

**The question (as a merchant would phrase it):** "Which categories
would deliver the most dollar impact if I adjusted pricing?"

**Signal:** Per-category, two axes — x = my unit-price gap vs peers
(percentage), y = my own line volume (count or revenue) in that
category. Quadrants identify categories where pricing × volume puts
the largest dollar-stakes on the table. High-volume + priced-above-
peers categories are the largest cut-candidate set; low-volume +
priced-below-peers indicate room to raise.

**Therefore-test:** The merchant picks the 1-2 categories where a
price adjustment moves the most dollars, given own volume mix.
Tomorrow's action: cost-data deep-dive for those categories.

**Cross-merchant test:** Both. Own volume is tenant; peer prices for
the gap axis are lake. **Passes.**

**Visualization pattern:** Pattern 4 (scatter with peer context).

**Visualization spec:**
- Chart type: scatter, x = gap percentage, y = own line count.
- Encoding: each point a category. Point size proportional to own
  category revenue. Quadrant gridlines drawn at x = 0 and y =
  category-median.
- Color convention: own categories in brand color; quadrant labels
  in light gray text.
- Interactivity:
  - Hover: category name, own price, peer mean price, gap, own line
    count, own revenue.
  - Click point: drill to P1 heatmap row for that category.
  - "Ask the agent about this": pre-fills with category name + the
    quadrant label.
- Empty / edge states:
  - Category with fewer than k=5 lines at the lake → point excluded;
    footnote count.
  - Category absent at own merchant → point excluded.

**Takeaway sentence shape (computed from data):**
*"Your largest priced-above-peers categories are [list];
{top_volume_category} is the highest-volume opportunity."*

**Rubric assessment:** Passes merchant-seat (named action target with
quantified stakes), cross-merchant (gap axis requires peer prices),
standalone (quadrant layout reads cold). All three.

---

### A1. Is this trend unique to me, or do peers see it too?

**Specialist:** anomaly

**The question (as a merchant would phrase it):** "Something's off
this week. Is it just me, or are my peers seeing the same thing?"

**Signal:** Weekly metric (transaction count, revenue, store-level
or neighborhood-level) for own merchant + per peer, over the 90-day
window. Normalized to a pre-decline baseline window (typically the
first 4-6 weeks) so cross-grocer scale differences don't dominate
the visual comparison. Sourced from `tenant_transactions` (own) +
`lake_transactions_<viewer>` (peers).

**Therefore-test:** If peers co-move, the trend is market-wide —
deprioritize store-level intervention, investigate
non-store-controllable factors. If only own merchant moves,
investigate operational causes. The University City decline (see
`V3_VISION.md` worked example) is the gold-standard instance of
this question.

**Cross-merchant test:** Both. Own counts from tenant; peer counts
from lake. **Passes.**

**Visualization pattern:** Pattern 1 (time-series-vs-peers).

**Visualization spec:**
- Chart type: line chart, x = week (week-starting-Sunday),
  y = metric normalized to baseline = 100 (toggleable to absolute
  counts).
- Encoding: own merchant line solid in brand color; peer lines gray
  (peer_a dashed, peer_b dotted).
- Interactivity:
  - Hover: tooltip with absolute count, ratio to baseline, grocer
    label.
  - Click week: drill to daily breakdown for that week, same three
    series.
  - "Ask the agent about this": pre-fills with the metric + the
    trough week (computed).
- Empty / edge states:
  - Peer with zero stores in the slice (e.g., zero UC stores at
    peer_b) → line omitted, footnote.
  - Weekly cell below k=5 at a peer → suppressed point with hover
    note.

**Takeaway sentence shape (computed from data):**
*"Your {metric} dropped {N}% from baseline by week of {trough_date};
your peers also declined ({M}% and {L}%). The pattern is
{market-wide / store-specific}."*

**Rubric assessment:** Passes merchant-seat (the action depends on
co-movement direction), cross-merchant (peer trajectories impossible
from tenant alone), standalone (takeaway tells the story). All
three.

---

### A2. Which of my stores show abnormal traffic recently?

**Specialist:** anomaly

**The question (as a merchant would phrase it):** "Which of my
stores are misbehaving this week?"

**Signal:** Per-store, recent transaction count vs the store's own
trailing baseline (e.g., 4-week mean). Each flagged store annotated
with its neighborhood and the peer-neighborhood baseline ratio for
the same period — so "store down 30% but peers in that neighborhood
also down 25%" reads differently from "store down 30%, neighborhood
peers flat".

**Therefore-test:** Investigate the top 1-3 flagged stores; the
peer-neighborhood column tells the merchant whether to look at the
store (operational) or the neighborhood (market-wide).

**Cross-merchant test:** Both. Own per-store baseline from tenant;
peer-neighborhood comparison from lake. **Passes.**

**Visualization pattern:** Pattern 9 (table + drilldown).

**Visualization spec:**
- Chart type: sortable table.
- Columns: store_id, neighborhood, baseline weekly traffic, recent
  weekly traffic, ratio (recent / baseline), peer-neighborhood
  ratio, deviation flag.
- Encoding: row highlighting for the most-flagged rows (top N by
  absolute deviation). Color cues on the ratio columns (red if
  significantly below 1.0, blue if significantly above).
- Color convention: own metrics in brand color; peer column in gray.
- Interactivity:
  - Hover row: full numeric breakdown.
  - Click row: drill to a Pattern 1 time-series for that store + the
    peer-neighborhood aggregate.
  - "Ask the agent about this": pre-fills with the store_id and
    neighborhood.
- Empty / edge states:
  - Peer-neighborhood with <5 stores total across peers → suppress
    the peer ratio column with hover note.

**Takeaway sentence shape (computed from data):**
*"{N} of your stores show traffic {direction} baseline by >X%;
{N_co_flagged} are co-flagged with neighborhood peers."*

**Rubric assessment:** Passes merchant-seat (specific stores named
with disambiguating context), cross-merchant (peer-neighborhood
benchmark unobservable single-merchant), standalone (table reads
with sort + takeaway). All three.

---

### A3. Which SKUs or categories are spiking or dropping unusually?

**Specialist:** anomaly

**The question (as a merchant would phrase it):** "Is anything
weird with what's selling this week vs what usually sells?"

**Signal:** Per (SKU or category, week), recent volume vs the prior
4-week baseline. Rows ranked by absolute deviation. Optional
peer-category column showing whether peers are seeing the same
spike/drop, sourced from `lake_transactions_<viewer>` at the
category level (SKU-level peer comparison usually exceeds k=5
suppression cells).

**Therefore-test:** Investigate stockouts (sudden drops), promo
overspend (sudden spikes), or seasonal shifts. Peer column tells
whether the issue is supply (own-store) or demand (market).

**Cross-merchant test:** Tenant primary; lake optional for category
corroboration. **Passes** (with the optional peer column carrying
the cross-merchant value; absent that column the question is
tenant-only).

**Visualization pattern:** Pattern 9 (table + drilldown).

**Visualization spec:**
- Chart type: sortable table.
- Columns: category (or SKU), recent volume, baseline volume, ratio,
  peer-category ratio, deviation flag.
- Encoding: ratio columns colored on a diverging scale.
- Color convention: own = brand, peer = gray.
- Interactivity:
  - Hover row: full numeric breakdown.
  - Click row: drill to Pattern 1 time-series for that SKU/category,
    own + peer aggregate over the 90-day window.
  - "Ask the agent about this": pre-fills with the category or SKU
    name.
- Empty / edge states:
  - SKU-level peer ratio below k=5 → suppressed cell with hover note.

**Takeaway sentence shape (computed from data):**
*"{N} categories show volume {direction} baseline by >X%; {top}
is the largest {spike / drop}; {peer_signal} confirms
{market-wide / store-specific}."*

**Rubric assessment:** Passes merchant-seat (named investigation
targets with peer corroboration), cross-merchant (peer column makes
the question diagnostic, not just descriptive), standalone (table
+ takeaway readable cold). All three.

---

### D3. What does my basket-mix look like compared to peers? Where am I over-indexed or under-indexed?

**Specialist:** demand

**The question (as a merchant would phrase it):** "Where is my
basket different from peers? What does my category mix reveal
about my positioning?"

**Signal:** For each category, compute own share of revenue
(percentage of total own revenue) versus peer-mean share. The
difference is the over- or under-index. Phase 1.6 Pass 2's
`MERCHANT_CATEGORY_BIAS` overlay made category-mix differentiation
real, so this question now reveals meaningful fingerprint
differences rather than near-identical mixes.

**Therefore-test:** Merchant sees their basket mix as a strategic
fingerprint. Where they're meaningfully different from peers, the
difference is either intentional positioning or addressable. Action:
confirm the over-indexing reflects strategy, or rebalance assortment
where it doesn't.

**Cross-merchant test:** Lake-only on the peer side, tenant on the
own side. **Passes.**

**Visualization pattern:** Pattern 2 (cross-merchant comparison,
single dimension) with diverging encoding.

**Visualization spec:**
- Chart type: diverging horizontal bar chart, bars centered at zero.
- Encoding: y-axis = category, sorted by absolute deviation
  descending. X-axis = percentage-point difference between own
  share and peer-mean share. Bars extending right = own merchant
  over-indexed; bars extending left = under-indexed.
- Color convention: bars in brand color, lighter shade for
  over-indexed and a complementary color for under-indexed, or a
  single brand-color treatment with directionality conveyed by
  position. Zero line emphasized.
- Interactivity:
  - Hover bar: category, own share, peer-mean share, deviation in
    pp.
  - Click bar: drill to per-peer breakdown (own vs peer_a vs
    peer_b) for that category, or to a Pattern 1 time-series
    showing how the category share has moved over 90 days.
  - "Ask the agent about this": pre-fills with the category + the
    sign of divergence.
- Empty / edge states:
  - Category with peer line count below k=5 → bar omitted with
    footnote.

**Takeaway sentence shape (computed from data):**
*"You're over-indexed on {top_category} (+{pp}pp vs peer-average);
under-indexed on {bottom_category} ({-pp}pp)."*

**Rubric assessment:** Passes merchant-seat (basket fingerprint as
strategic check), cross-merchant (peer basket mix unobservable
single-merchant), standalone (diverging bars + takeaway readable
cold). All three.

---

### D4. Which categories over- or under-perform vs peers given my mix?

**Specialist:** demand

**The question (as a merchant would phrase it):** "Where am I doing
better or worse than peers, controlling for what mix I carry?"

**Signal:** Per-category, x = my share of revenue in that category
(percentage of total own revenue), y = peer share of revenue in
that category (percentage of total peer revenue). The 45° line is
parity; off-diagonal points indicate categories where own merchant
diverges meaningfully from peer mix — either intentional positioning
or addressable gap.

**Therefore-test:** Identify the 1-2 categories furthest from the
parity diagonal; investigate whether the divergence is intentional
(e.g., ACM's deliberate dairy emphasis post Pass 2) or unintentional.

**Cross-merchant test:** Lake-only on the peer side (peer revenue
shares); tenant on own. **Passes.**

**Visualization pattern:** Pattern 4 (scatter with peer context).

**Visualization spec:**
- Chart type: scatter, x = own pct, y = peer-mean pct.
- Encoding: each point a category. Point size proportional to
  absolute revenue (own or category-total, consistent across the
  chart). 45° line for parity, drawn in light gray.
- Color convention: own categories brand color; parity line gray.
- Interactivity:
  - Hover: category name, own share, peer share, gap.
  - Click point: drill to D3 time-series for that category.
  - "Ask the agent about this": pre-fills with the category + the
    sign of the divergence (over / under).
- Empty / edge states:
  - Category with weekly peer line count below k=5 → excluded with
    footnote.

**Takeaway sentence shape (computed from data):**
*"{category} overperforms peers by {Δ}pp share; {category}
underperforms by {Δ}pp."*

**Rubric assessment:** Passes merchant-seat (categories to
investigate named), cross-merchant (peer share unobservable single-
merchant), standalone (parity line + takeaway readable cold). All
three.

---

### D7. What's driving my revenue gap vs peers this period?

**Specialist:** demand

**The question (as a merchant would phrase it):** "If I'm behind
peers on revenue this period, what's the biggest driver?"

**Signal:** Decompose own-vs-peer revenue gap into drivers: traffic
(transaction count), basket size (items per trip), ticket size
(dollars per item), category mix (composition), residual (everything
else). Each driver's contribution to the total gap is computed as a
pp share of the dollar gap. Sourced from `tenant_transactions` +
`tenant_transaction_items` (own) and the corresponding lake tables
(peer). Phase 1.6 Pass 2's basket-size differentiation (ACM 0.90×,
WDX 1.20×) makes the basket-size driver a meaningful contributor;
the pricing driver is calibrated symmetrically and contributes less
to the gap directly but flows through ticket size.

**Therefore-test:** Direct attention to the single biggest driver;
investigate operationally (traffic, ticket) or strategically (mix,
basket). Tomorrow's action: define a sub-investigation for the
dominant driver.

**Cross-merchant test:** Both. Own decomposition from tenant; peer
decomposition from lake. **Passes.**

**Visualization pattern:** Pattern 5 (waterfall / decomposition).

**Visualization spec:**
- Chart type: waterfall bar chart, x = driver categories (traffic /
  avg basket / avg ticket / mix / residual), y = contribution to
  the gap in dollars.
- Encoding: positive bars (own ahead of peer on this driver) in
  brand color; negative bars (peer ahead on this driver) in red /
  diverging palette. Connecting bars between driver bars to show
  cumulative gap.
- Color convention: own > peer = brand color; own < peer = red /
  diverging.
- Interactivity:
  - Hover bar: numeric contribution, raw own + peer values for that
    driver.
  - Click bar: drill to a Pattern 1 time-series of that driver over
    90 days, own vs peer.
  - "Ask the agent about this": pre-fills with the dominant-driver
    name.
- Empty / edge states:
  - Driver with low statistical power (e.g., residual dominating) →
    note flagged in subtitle.

**Takeaway sentence shape (computed from data):**
*"Of the {pct}% revenue gap vs peers, {dominant_driver} contributes
{pct}pp; the other drivers are within noise."*

**Rubric assessment:** Passes merchant-seat (named driver focus),
cross-merchant (peer decomposition impossible single-merchant),
standalone (waterfall + takeaway communicates the headline cold).
All three.

---

### T1. Which of my neighborhoods are over- or under-performing?

**Specialist:** trade

**The question (as a merchant would phrase it):** "Which of my
trade areas are weak, and is the issue mine or the market's?"

**Signal:** Per-neighborhood transaction count or revenue vs my own
panel mean (the across-neighborhood baseline), with an optional
peer-neighborhood comparison from the lake to flag whether the
weakness is shared by peers in the same area. Phase 1.6 Pass 2's
5-neighborhood shared comparison footprint (Dilworth, SouthPark,
University City, Ballantyne, Plaza Midwood, all at ≥2 stores per
grocer) is load-bearing here: peer comparison at the neighborhood
level requires sufficient peer footprint.

**Therefore-test:** Prioritize attention to weak neighborhoods.
Tomorrow's action: investigate the worst-performing neighborhood,
guided by whether peers are co-weak (market) or stable
(operational).

**Cross-merchant test:** Both. Tenant for own per-neighborhood
performance; lake for peer baseline at the neighborhood level.
**Passes.**

**Visualization pattern:** Pattern 6 (geographic map).

**Visualization spec:**
- Chart type: Folium map of Charlotte metro. Neighborhood polygons
  colored by own performance ratio (recent / baseline, or own /
  peer-mean in that neighborhood).
- Encoding: diverging color scale — red = under-performing, blue =
  over-performing, white = on baseline. Store markers (own
  merchant) overlaid as dots.
- Color convention: own polygons in diverging scale; own store
  markers in brand color.
- Interactivity:
  - Hover polygon: neighborhood name, own count, peer count, ratio,
    own store count, peer footprint.
  - Click polygon: drill to a Pattern 1 time-series for that
    neighborhood, own + peers.
  - "Ask the agent about this": pre-fills with the neighborhood
    name.
- Empty / edge states:
  - Neighborhood with fewer than 2 own stores → polygon shaded
    lighter with note ("limited own footprint").
  - Neighborhood with no peer stores → peer comparison column null
    with hover note.

**Takeaway sentence shape (computed from data):**
*"{neighborhood} under-performs by {pct}%; {peer_signal} suggests
{market-wide / operational}."*

**Rubric assessment:** Passes merchant-seat (named neighborhood
+ market/operational disambiguation), cross-merchant
(peer-neighborhood benchmark unobservable single-merchant),
standalone (map + takeaway communicates cold). All three.

---

### T2. Where do my customers live relative to my stores?

**Specialist:** trade

**The question (as a merchant would phrase it):** "Are my customers
coming from the same neighborhoods my stores sit in, or pulling from
further away?"

**Signal:** Customer-home neighborhood density (from
`tenant_customers.home_zip5` → neighborhood) vs own store-location
neighborhoods. Identify mismatches where customers travel from
under-served neighborhoods.

**Therefore-test:** Identify under-served customer origins;
consider expansion, rebalancing, or marketing in those
neighborhoods.

**Cross-merchant test:** Tenant-only. The mismatch question is
about own customer base vs own footprint; lake adds no signal here.
**This is the one final-12 question where the cross-merchant rubric
is weakly passed** — included because it's the foundation for T4,
the explicit expansion question. T2 alone tells the diagnostic
story; T4 acts on it.

**Visualization pattern:** Pattern 6 (geographic map).

**Visualization spec:**
- Chart type: Folium dual-layer map. Layer 1: customer-home density
  (choropleth or heatmap). Layer 2: store markers overlaid.
- Encoding: density layer in a sequential color scale (light → dark
  by customer count); store markers in brand color, sized
  consistently.
- Color convention: density layer sequential; store markers brand.
- Interactivity:
  - Hover polygon: neighborhood name, customer count, distance to
    nearest own store.
  - Click polygon: drill to a customer-count breakdown table for
    that neighborhood.
  - Layer toggle: customer-density only, stores only, both.
- Empty / edge states:
  - Neighborhood with customer count below k=5 → suppressed with
    hover note.

**Takeaway sentence shape (computed from data):**
*"{pct}% of your customers live in neighborhoods without a same-
merchant store; densest under-served area is {neighborhood}."*

**Rubric assessment:** Passes merchant-seat (under-served origins
named), partially passes cross-merchant (the question is tenant-
shaped but feeds T4's cross-merchant follow-up), standalone (dual-
layer map + takeaway reads cold).

---

### T4. Which neighborhoods show the biggest expansion opportunity?

**Specialist:** trade

**The question (as a merchant would phrase it):** "If I were adding
stores, which neighborhoods would deliver the most lift?"

**Signal:** Per-neighborhood underserved-opportunity score, defined
as total transactions originating from customers living in or near
the neighborhood, divided by (my store count in the neighborhood +
1). A higher ratio indicates more transaction activity happening
that I'm not capturing locally — either my customers travel to
shop elsewhere, or peer stores in the neighborhood capture
spend that could be mine. Implementation details left for Phase 4;
the conceptual spec is the demand-side numerator and the
supply-side denominator.

**Therefore-test:** Rank neighborhoods by score; identify top 1-3
for further investigation (real-estate scouting, peer competitive
analysis, demand-side surveys).

**Cross-merchant test:** Both. Tenant for customer origins (the
numerator). Lake context optional: peer store density in those
neighborhoods (denominator-adjacent) tells you whether the gap is
captured by competitors or genuinely unserved. **Passes.**

**Visualization pattern:** Pattern 6 (geographic map).

**Visualization spec:**
- Chart type: Folium choropleth, neighborhood polygons colored by
  underserved-opportunity score.
- Encoding: sequential color scale, light = low score, dark = high
  score. Own store markers overlaid (one symbol); peer store
  markers optional layer (different symbol) for competitive
  context.
- Color convention: own = brand, peer markers gray, score
  sequential.
- Interactivity:
  - Hover polygon: neighborhood, score, numerator (customer
    activity), denominator (own store count + 1), peer store count.
  - Click polygon: drill to a per-neighborhood detail table
    (customers, stores, top transaction merchants for those
    customers).
  - Layer toggle: own stores only, own + peer stores.
- Empty / edge states:
  - Neighborhood with no own-customers → score not computed, polygon
    rendered transparent.

**Takeaway sentence shape (computed from data):**
*"Top expansion opportunity: {neighborhood} (score {N:.1f});
{peer_signal} suggests {peers under-represented / peer-dense}."*

**Rubric assessment:** Passes merchant-seat (ranked expansion
candidates), cross-merchant (peer footprint context for the
competitive read), standalone (map + score legend + takeaway reads
cold). All three.

---

## Section 3B — TBL and TJX question sets

Section 3's 12 finals assume grocer-style cross-merchant peer
comparison (peer_a, peer_b grocers in the lake). TBL (QSR) and TJX
(off-price retail) each sit alone in their segment within the
panel, so peer comparison at the segment level doesn't apply. For
the pricing, anomaly, and demand specialists, TBL and TJX get
own-data variations of the same chart patterns. The trade
specialist's questions are cross-merchant by geography (not
segment), so T1, T2, and T4 are reused verbatim for TBL and TJX
viewers.

Net-new question IDs use a `T-` prefix for TBL and an `R-` prefix
for TJX. Nine net-new questions per viewer (three per specialist
across pricing, anomaly, demand) + three reused trade questions =
12 suggested questions per viewer, parity with grocer viewers.

---

### T-P1. How is my average ticket trending across dayparts?

**Specialist:** pricing (TBL)

**The question (as a merchant would phrase it):** "Is my ticket
size moving up or down at different times of day?"

**Signal:** Own-data weekly average transaction value, broken out
by daypart (morning / lunch / afternoon / evening derived from
`tenant_transactions.txn_ts` hour). Trend computed across the
90-day window with one series per daypart.

**Therefore-test:** Identify dayparts where the average ticket is
drifting up or down. A rising lunch ticket alongside a flat
evening ticket is a pricing-dynamics signal worth understanding.
Tomorrow's action: investigate the drifting daypart for menu mix
changes, price changes, or shift in customer composition.

**Cross-merchant test:** Tenant-only. No same-segment peer in the
panel, so daypart-ticket comparison is own-only by design.

**Visualization pattern:** Pattern 1 (time-series-vs-peers) used in
own-only mode — multiple lines representing the four dayparts, no
peer overlay.

**Visualization spec:**
- Chart type: line chart, x = week (week-starting-Sunday), y =
  mean transaction value per daypart.
- Encoding: four lines, one per daypart (morning, lunch,
  afternoon, evening). Own merchant brand color shared across
  lines; daypart distinguished by line style (solid, dashed,
  dotted, long-dash) plus legend.
- Interactivity:
  - Hover: tooltip with weekly mean ticket, daypart label,
    transaction count.
  - Click week: drilldown to daily ticket-by-daypart for that
    week.
  - "Ask the agent about this": pre-fills with the daypart name +
    the direction of drift.
- Empty / edge states:
  - Daypart with fewer than k=5 transactions per week → suppress
    that point with hover note.

**Takeaway sentence shape (computed from data):**
*"Your {top_daypart} ticket is {direction} {pct}% over 90 days;
{bottom_daypart} is {direction} {pct}%."*

**Rubric assessment:** Passes merchant-seat (named daypart for
follow-up) and standalone (line + takeaway reads cold). Cross-
merchant N/A by design — no same-segment peer in panel.

---

### T-P2. Which menu categories have shifted in price over the last 90 days?

**Specialist:** pricing (TBL)

**The question (as a merchant would phrase it):** "Where in my
menu have prices moved over the last quarter?"

**Signal:** Own-data per-category mean unit price over the 90-day
window. TBL categories: COMBO, BURR, SIDE, DRINK, SPEC, TACO,
BFAST. One trend line per top 6-8 categories by revenue.

**Therefore-test:** Identify categories with material price drift;
verify against menu pricing strategy. Tomorrow's action: pricing
review on the drifted categories.

**Cross-merchant test:** Tenant-only.

**Visualization pattern:** Pattern 1 (own-only mode), one line per
category.

**Visualization spec:**
- Chart type: line chart, x = week, y = mean unit price.
- Encoding: one line per top 6-8 categories. Color: sequential
  brand-family across categories (lightest → darkest by category
  revenue rank).
- Interactivity:
  - Hover: tooltip with weekly mean price, category name, sample
    size.
  - Click line: drilldown to category-level SKU price breakdown.
  - "Ask the agent about this": pre-fills with the category name.
- Empty / edge states:
  - Category with fewer than k=5 lines in a week → suppress that
    point.

**Takeaway sentence shape (computed from data):**
*"{category} prices have {direction} {pct}% over 90 days; the
next-largest shift is in {category} at {pct}%."*

**Rubric assessment:** Passes merchant-seat (category-level
investigation target) and standalone (line chart + takeaway reads
cold). Cross-merchant N/A by design.

---

### T-P3. What's my price distribution across stores? Are any outliers?

**Specialist:** pricing (TBL)

**The question (as a merchant would phrase it):** "Are all of my
stores pricing consistently, or do some run higher or lower
tickets?"

**Signal:** Own-data per-store average transaction value over 90
days. Identifies stores whose average ticket has drifted from the
chain mean, surfacing menu-execution, regional pricing, or data
issues.

**Therefore-test:** Pricing consistency across stores. Outliers
investigated for menu-execution differences, regional pricing
decisions, or data-quality concerns.

**Cross-merchant test:** Tenant-only.

**Visualization pattern:** Pattern 2 (cross-merchant comparison,
single-dimension) used in own-only horizontal-bar mode.

**Visualization spec:**
- Chart type: horizontal bar chart, y = store_id, x = mean
  transaction value over 90 days.
- Encoding: bars sorted descending by mean ticket. Own merchant
  brand color baseline; outliers (>1 standard deviation from the
  chain mean) shaded darker. Reference line drawn at the chain
  mean.
- Interactivity:
  - Hover bar: store_id, neighborhood, mean ticket, transaction
    count.
  - Click bar: drilldown to that store's transaction-level
    breakdown.
  - "Ask the agent about this": pre-fills with the store_id.
- Empty / edge states:
  - Store with fewer than k=5 transactions in the window →
    suppress with footnote.

**Takeaway sentence shape (computed from data):**
*"Your highest-ticket store is {store} at \${value}; lowest is
{store} at \${value}; range is \${range}."*

**Rubric assessment:** Passes merchant-seat (specific outlier
stores named) and standalone (bar + reference line + takeaway
reads cold). Cross-merchant N/A by design.

---

### T-A1. Which of my stores has unusual traffic this week?

**Specialist:** anomaly (TBL)

**The question (as a merchant would phrase it):** "Are any of my
stores running off pattern this week?"

**Signal:** Own-data per-store recent weekly traffic vs that
store's 4-week rolling baseline. Stores whose ratio exceeds a
threshold (e.g., ±15%) are flagged.

**Therefore-test:** Investigate the 1-3 flagged stores;
operational or local-context check (event, road closure, local
competitor, staffing).

**Cross-merchant test:** Tenant-only. No QSR peers in panel for a
neighborhood-baseline comparison.

**Visualization pattern:** Pattern 9 (table + drilldown).

**Visualization spec:**
- Chart type: sortable table.
- Columns: store_id, neighborhood, baseline weekly traffic,
  recent weekly traffic, ratio, deviation flag.
- Encoding: color cues on the ratio column (red if below
  threshold, blue if above). Row highlighting for top N by
  absolute deviation.
- Interactivity:
  - Hover row: full numeric breakdown.
  - Click row: Pattern 1 time-series for that store over 90 days
    plus the rolling baseline reference.
  - "Ask the agent about this": pre-fills with the store_id.

**Takeaway sentence shape (computed from data):**
*"{N} stores show traffic {direction} baseline by >{X}%; top
deviation: {store} at {ratio}."*

**Rubric assessment:** Passes merchant-seat (specific stores
named) and standalone (table + takeaway reads cold). Cross-
merchant N/A by design.

---

### T-A2. Are any menu items spiking or dropping unusually?

**Specialist:** anomaly (TBL)

**The question (as a merchant would phrase it):** "What's selling
unusually well — or badly — this week?"

**Signal:** Own-data per-SKU recent weekly volume vs the prior
4-week baseline. Rows ranked by absolute deviation.

**Therefore-test:** Investigate stockouts (drops), promotional or
social-media spikes, or seasonal shifts. Tomorrow's action: check
supply / promo / posting context for the top flagged SKUs.

**Cross-merchant test:** Tenant-only.

**Visualization pattern:** Pattern 9 (table + drilldown).

**Visualization spec:**
- Chart type: sortable table.
- Columns: SKU, category, recent weekly units, baseline weekly
  units, ratio, deviation flag.
- Encoding: ratio column color-coded on diverging scale.
- Interactivity:
  - Hover row: numeric breakdown + SKU description.
  - Click row: SKU-level time-series over 90 days.
  - "Ask the agent about this": pre-fills with the SKU name.

**Takeaway sentence shape (computed from data):**
*"{N} items show volume {direction} baseline by >{X}%; largest
spike: {sku}; largest drop: {sku}."*

**Rubric assessment:** Passes merchant-seat (named SKUs to
investigate) and standalone (table + takeaway reads cold). Cross-
merchant N/A by design.

---

### T-A3. Which dayparts are running below my own baseline?

**Specialist:** anomaly (TBL)

**The question (as a merchant would phrase it):** "Is there a
recurring weak spot in my day-of-week-by-daypart schedule?"

**Signal:** Own-data day-of-week × daypart heatmap of the recent
week, with cell values expressed as the delta from the merchant's
prior 4-week baseline for the same day×daypart cell. Negative
cells flagged as weak.

**Therefore-test:** Identify when traffic is consistently below
norms — staffing, marketing, or hours-of-operation decisions.

**Cross-merchant test:** Tenant-only. Comparison is current week
vs own baseline; no peer side.

**Visualization pattern:** Pattern 3 (cross-merchant heatmap) used
in **own-only diverging mode** — the diverging encoding compares
current week vs own baseline rather than own vs peer.

**Visualization spec:**
- Chart type: heatmap. Rows = day of week (Mon-Sun), columns =
  daypart (morning, lunch, afternoon, evening).
- Encoding: cell color = ratio (or delta) vs baseline. Diverging
  red-white-blue, white at parity (= 1.0 or 0). Cell text overlay
  shows transaction count + ratio.
- Interactivity:
  - Hover cell: raw current-week count, baseline count, ratio.
  - Click cell: drilldown to that day×daypart's transaction
    breakdown for the recent week.
  - "Ask the agent about this": pre-fills with the weakest cell's
    day + daypart.
- Empty / edge states:
  - Cell with fewer than k=5 transactions in the recent week →
    suppress + hover note.

**Takeaway sentence shape (computed from data):**
*"Your weakest day-daypart this week is {day} {daypart} ({ratio}
of baseline); strongest is {day} {daypart}."*

**Rubric assessment:** Passes merchant-seat (specific weak cell
named) and standalone (heatmap + takeaway reads cold). Cross-
merchant N/A by design.

---

### T-D1. What does my menu mix look like? Where am I most concentrated?

**Specialist:** demand (TBL)

**The question (as a merchant would phrase it):** "What share of
revenue comes from each of my menu categories?"

**Signal:** Own-data per-category share of revenue (or
transaction share) over the filter window. Top categories
surfaced; minor categories rolled up into "Other".

**Therefore-test:** Identify mix concentration. Decide whether
heavy dependence on one or two categories is intentional menu
strategy or a vulnerability worth diversifying against.

**Cross-merchant test:** Tenant-only.

**Visualization pattern:** Pattern 2 (single-dim comparison) used
as a share bar without peer comparison.

**Visualization spec:**
- Chart type: horizontal bar chart, y = category, x = share of
  revenue (percentage).
- Encoding: top 8 categories visible, "Other" rolled up below.
  Own merchant brand color across bars.
- Interactivity:
  - Hover bar: category name, revenue, share.
  - Click bar: category-level SKU drilldown.
  - "Ask the agent about this": pre-fills with the category name.

**Takeaway sentence shape (computed from data):**
*"Top 3 categories ({c1}, {c2}, {c3}) account for {pct}% of
revenue."*

**Rubric assessment:** Passes merchant-seat (concentration check
informs menu-strategy review) and standalone (bar + takeaway reads
cold). Cross-merchant N/A by design.

---

### T-D2. Which categories are gaining or losing share over time?

**Specialist:** demand (TBL)

**The question (as a merchant would phrase it):** "Where is my
menu mix shifting? Anything trending up or down?"

**Signal:** Own-data per-category share of weekly revenue over
the 90-day window. Trend per category.

**Therefore-test:** Identify category momentum. Investigate
declining categories (menu fatigue, supply issues, local
competition) and validate growing ones (menu refresh that's
working).

**Cross-merchant test:** Tenant-only.

**Visualization pattern:** Pattern 1 (own-only mode), one line
per category.

**Visualization spec:**
- Chart type: line chart, x = week, y = category share
  (percentage of weekly revenue).
- Encoding: one line per top 5-6 categories. Color: sequential
  brand-family.
- Interactivity:
  - Hover: category name, week, share, weekly revenue.
  - Click line: category detail view.
  - "Ask the agent about this": pre-fills with the category +
    direction of trend.

**Takeaway sentence shape (computed from data):**
*"{growing_category} share is up {pct}pp over 90 days;
{declining_category} is down {pct}pp."*

**Rubric assessment:** Passes merchant-seat (category momentum
review) and standalone (multi-line chart + takeaway reads cold).
Cross-merchant N/A by design.

---

### T-D3. What's driving my revenue change this week — traffic, ticket, or mix?

**Specialist:** demand (TBL)

**The question (as a merchant would phrase it):** "Revenue moved
this week. What's the actual driver?"

**Signal:** Own-data decomposition of current-week revenue vs the
prior 4-week baseline. Drivers: traffic (transaction count),
ticket (dollars per transaction), mix (category-share shifts),
residual. Each driver's contribution to the change is computed in
dollars.

**Therefore-test:** Focus attention on the dominant driver of
revenue change. Different drivers warrant different
investigations (traffic → marketing/local context; ticket →
pricing/menu; mix → category-specific signals).

**Cross-merchant test:** Tenant-only. Decomposition is own-vs-own-
baseline, no peer comparison.

**Visualization pattern:** Pattern 5 (decomposition / waterfall)
used in **own-vs-own-baseline mode** — drivers are computed against
the merchant's own prior 4-week baseline rather than against a peer
cohort.

**Visualization spec:**
- Chart type: waterfall bar chart. X = drivers (traffic / avg
  ticket / mix / residual). Y = contribution to weekly revenue
  change in dollars.
- Encoding: positive bars (own ahead of baseline on this driver)
  in brand color; negative bars (behind baseline) in diverging
  red. Connecting bars show cumulative running total.
- Interactivity:
  - Hover bar: numeric contribution + raw current and baseline
    values.
  - Click bar: that driver's weekly trend over the 90-day window.
  - "Ask the agent about this": pre-fills with the dominant
    driver name.

**Takeaway sentence shape (computed from data):**
*"Revenue {direction} {pct}% vs baseline; {dominant_driver}
contributes {pct}pp."*

**Rubric assessment:** Passes merchant-seat (named driver focus
for investigation) and standalone (waterfall + takeaway reads
cold). Cross-merchant N/A by design.

---

### Trade specialist for TBL

Trade specialist for TBL reuses T1, T2, and T4 from Section 3
directly. Trade-area questions are cross-merchant via customer
geography (not segment), so they apply to any viewer including
non-grocers. The shared 5-neighborhood footprint (Phase 1.6
calibration) ensures TBL stores overlap with peer stores in enough
neighborhoods to make the comparison meaningful.

---

### R-P1. How is my average ticket trending across categories?

**Specialist:** pricing (TJX)

**The question (as a merchant would phrase it):** "Is my ticket
size moving up or down across product categories?"

**Signal:** Own-data per-category mean transaction value over the
90-day window. TJX categories: ACC (accessories), SHO (shoes),
WOM (women's), MEN (men's), JEW (jewelry), BTY (beauty), HOM, KID.

**Therefore-test:** Ticket trends by category. ACC is TJX's
largest category by revenue; its ticket trajectory is the most
consequential signal. Tomorrow's action: investigate the highest-
movement category for assortment or pricing shifts.

**Cross-merchant test:** Tenant-only.

**Visualization pattern:** Pattern 1 (own-only mode), one line
per category.

**Visualization spec:**
- Chart type: line chart, x = week, y = mean transaction value
  per category.
- Encoding: one line per top 5-6 categories. Color: sequential
  brand-family.
- Interactivity:
  - Hover: weekly mean ticket, transaction count, category name.
  - Click line: category drilldown.
  - "Ask the agent about this": pre-fills with the category.

**Takeaway sentence shape (computed from data):**
*"{category} ticket has {direction} {pct}% over 90 days;
{next_category} is {direction} {pct}%."*

**Rubric assessment:** Passes merchant-seat (category-level
trajectory review) and standalone (line chart + takeaway reads
cold). Cross-merchant N/A by design.

---

### R-P2. Which categories have the widest price spread within them?

**Specialist:** pricing (TJX)

**The question (as a merchant would phrase it):** "Where in my
inventory do prices range from low to high the most?"

**Signal:** Own-data per-category price distribution. Min, median,
and max unit price per category, plus the price-range ratio
(max / min). Categories with wide spreads indicate large
assortment variation (e.g., ACC ranging from costume jewelry to
designer handbags).

**Therefore-test:** Wide-spread categories indicate inventory mix
diversity. Useful for assortment planning, signage / merchandising
decisions, and cross-category comparison of pricing strategy.

**Cross-merchant test:** Tenant-only.

**Visualization pattern:** Pattern 9 (table + drilldown), used with
quantile columns rather than ratio columns.

**Visualization spec:**
- Chart type: sortable table.
- Columns: category, min unit price, median, max, price-range
  ratio (max / min), transaction count.
- Encoding: numeric column sorting; row highlighting for top N by
  ratio.
- Interactivity:
  - Hover row: full numeric breakdown.
  - Click row: SKU-level price distribution for that category
    (histogram).
  - "Ask the agent about this": pre-fills with the category name.

**Takeaway sentence shape (computed from data):**
*"{widest_spread_category} has the widest price spread ({ratio}×
from min to max); {tightest} is narrowest."*

**Rubric assessment:** Passes merchant-seat (assortment-mix review
informed by spread) and standalone (table + takeaway reads cold).
Cross-merchant N/A by design.

---

### R-P3. What's my high-ticket vs low-ticket transaction split?

**Specialist:** pricing (TJX)

**The question (as a merchant would phrase it):** "How does my
revenue concentrate by ticket size — is it Pareto, or more even?"

**Signal:** Own-data transactions distributed across ticket bands
(e.g., \$0–50, \$50–100, \$100–200, \$200–500, \$500+). For each
band, the share of total transactions and the share of total
revenue.

**Therefore-test:** Inspect basket shape. If the top 20% of
transactions drives 80% of revenue (Pareto), TJX's pricing
leverage sits with the high-ticket band; if more even, broader
ticket-band investment is warranted.

**Cross-merchant test:** Tenant-only.

**Visualization pattern:** Pattern 2 (single-dim comparison) used
as ticket-band bar chart.

**Visualization spec:**
- Chart type: horizontal bar chart, y = ticket band (ordered
  ascending), x = two metrics shown — transaction count and
  revenue (grouped bars or dual-axis).
- Encoding: transaction-count bars in own brand color; revenue
  bars in a contrasting brand-family shade.
- Interactivity:
  - Hover bar: ticket band, transaction count, share of
    transactions, revenue, share of revenue.
  - Click bar: drilldown to transaction list within that band.
  - "Ask the agent about this": pre-fills with the ticket band.

**Takeaway sentence shape (computed from data):**
*"Your top ticket band (\${X}-{Y}) accounts for {pct}% of
transactions and {pct}% of revenue."*

**Rubric assessment:** Passes merchant-seat (revenue-concentration
shape informs pricing strategy) and standalone (bar + takeaway
reads cold). Cross-merchant N/A by design.

---

### R-A1. Which of my stores has unusual traffic this week?

**Specialist:** anomaly (TJX)

**The question (as a merchant would phrase it):** "Are any of my
stores running off pattern this week?"

**Signal:** Own-data per-store recent weekly traffic vs that
store's 4-week rolling baseline. TJX has 8 stores, smaller
footprint than TBL, so each flagged store carries proportionally
more weight.

**Therefore-test:** Investigate the 1-2 flagged stores;
operational or local-context check.

**Cross-merchant test:** Tenant-only. No same-segment peers in
panel for neighborhood-baseline comparison.

**Visualization pattern:** Pattern 9 (table + drilldown).

**Visualization spec:**
- Chart type: sortable table.
- Columns: store_id, neighborhood, baseline weekly traffic,
  recent weekly traffic, ratio, deviation flag.
- Encoding: color cues on the ratio column; row highlighting for
  flagged rows.
- Interactivity:
  - Hover row: full numeric breakdown.
  - Click row: Pattern 1 time-series for that store.
  - "Ask the agent about this": pre-fills with the store_id.

**Takeaway sentence shape (computed from data):**
*"{N} stores show traffic {direction} baseline by >{X}%; top
deviation: {store} at {ratio}."*

**Rubric assessment:** Passes merchant-seat (specific stores
named) and standalone (table + takeaway reads cold). Cross-
merchant N/A by design.

---

### R-A2. Are any categories spiking or dropping unusually?

**Specialist:** anomaly (TJX)

**The question (as a merchant would phrase it):** "Is any product
category running way above or below normal?"

**Signal:** Own-data per-category recent weekly volume vs the
prior 4-week baseline. TJX has fewer SKUs per category than TBL
has per category, so category-level anomalies are the more
meaningful aggregation level (per-SKU anomalies would frequently
fall below k=5).

**Therefore-test:** Investigate stockouts, promotional spikes,
seasonal shifts, or assortment changes.

**Cross-merchant test:** Tenant-only.

**Visualization pattern:** Pattern 9 (table + drilldown).

**Visualization spec:**
- Chart type: sortable table.
- Columns: category, recent weekly volume, baseline weekly
  volume, ratio, deviation flag.
- Encoding: ratio column color-coded on diverging scale.
- Interactivity:
  - Hover row: numeric breakdown.
  - Click row: Pattern 1 time-series for that category.
  - "Ask the agent about this": pre-fills with the category name.

**Takeaway sentence shape (computed from data):**
*"{N} categories show volume {direction} baseline by >{X}%;
largest spike: {category}; largest drop: {category}."*

**Rubric assessment:** Passes merchant-seat (named categories) and
standalone (table + takeaway reads cold). Cross-merchant N/A by
design.

---

### R-A3. Which days of the week are running below my baseline?

**Specialist:** anomaly (TJX)

**The question (as a merchant would phrase it):** "Is there a
recurring weak day in my week?"

**Signal:** Own-data day-of-week × week heatmap of the last 4-8
weeks. Cell value = ratio of that day-week's transactions to the
day's prior 4-week baseline. TJX's off-price retail pattern
doesn't carry the QSR daypart structure; day-of-week is the
meaningful temporal axis.

**Therefore-test:** Identify recurring weak days for staffing,
marketing, or hours-of-operation review.

**Cross-merchant test:** Tenant-only.

**Visualization pattern:** Pattern 3 (cross-merchant heatmap) used
in **own-only diverging mode** — diverging encoding compares the
current period vs own baseline rather than own vs peer.

**Visualization spec:**
- Chart type: heatmap. Rows = day of week, columns = week (last
  4-8 weeks).
- Encoding: cell color = ratio to baseline. Diverging red-white-
  blue, white at parity (1.0). Cell text shows ratio.
- Interactivity:
  - Hover cell: day, week, transaction count, baseline count,
    ratio.
  - Click cell: that day-week's transaction breakdown.
  - "Ask the agent about this": pre-fills with the weakest day.
- Empty / edge states:
  - Cell with fewer than k=5 transactions → suppress + hover note.

**Takeaway sentence shape (computed from data):**
*"Weakest day-week this period: {day} week of {date} ({ratio} of
baseline)."*

**Rubric assessment:** Passes merchant-seat (specific weak day-
week named) and standalone (heatmap + takeaway reads cold).
Cross-merchant N/A by design.

---

### R-D1. What does my category mix look like?

**Specialist:** demand (TJX)

**The question (as a merchant would phrase it):** "What share of
revenue comes from each product category?"

**Signal:** Own-data per-category share of revenue. TJX category
set: ACC, SHO, WOM, MEN, JEW, BTY, HOM, KID. Top categories
surfaced; minor categories rolled up into "Other".

**Therefore-test:** Identify mix concentration. Assortment
planning signal — is revenue concentrated in one or two
categories, or distributed?

**Cross-merchant test:** Tenant-only.

**Visualization pattern:** Pattern 2 (single-dim comparison) used
as a share bar.

**Visualization spec:**
- Chart type: horizontal bar chart, y = category, x = share of
  revenue.
- Encoding: top 6-8 categories visible. Own merchant brand color.
- Interactivity:
  - Hover bar: category, revenue, share.
  - Click bar: category SKU-level drilldown.
  - "Ask the agent about this": pre-fills with the category name.

**Takeaway sentence shape (computed from data):**
*"Top 3 categories ({c1}, {c2}, {c3}) account for {pct}% of
revenue."*

**Rubric assessment:** Passes merchant-seat (concentration check)
and standalone (bar + takeaway reads cold). Cross-merchant N/A by
design.

---

### R-D2. Which categories are gaining or losing share over time?

**Specialist:** demand (TJX)

**The question (as a merchant would phrase it):** "Is my category
mix shifting? Where?"

**Signal:** Own-data per-category share of weekly revenue over
the 90-day window. Trend per category.

**Therefore-test:** Identify category momentum. Investigate
declining categories for assortment / inventory issues; validate
rising categories for further investment.

**Cross-merchant test:** Tenant-only.

**Visualization pattern:** Pattern 1 (own-only mode), one line
per category.

**Visualization spec:**
- Chart type: line chart, x = week, y = category share.
- Encoding: one line per top 5-6 categories. Color: sequential
  brand-family.
- Interactivity:
  - Hover: category, week, share, revenue.
  - Click line: category detail.
  - "Ask the agent about this": pre-fills with the category +
    direction.

**Takeaway sentence shape (computed from data):**
*"{growing_category} share is up {pct}pp over 90 days;
{declining_category} is down {pct}pp."*

**Rubric assessment:** Passes merchant-seat (momentum-driven
investigation pointer) and standalone (line + takeaway reads
cold). Cross-merchant N/A by design.

---

### R-D3. What's driving my revenue change this week?

**Specialist:** demand (TJX)

**The question (as a merchant would phrase it):** "Revenue moved.
What's the actual driver — traffic, ticket, or mix?"

**Signal:** Own-data decomposition of current-week revenue vs the
prior 4-week baseline. Drivers: traffic (transaction count),
ticket (dollars per transaction), mix (category-share shifts),
residual. Contributions computed in dollars.

**Therefore-test:** Focus attention on the dominant driver.
Different drivers warrant different investigations.

**Cross-merchant test:** Tenant-only. Own-vs-own-baseline
decomposition.

**Visualization pattern:** Pattern 5 (decomposition / waterfall)
used in **own-vs-own-baseline mode**.

**Visualization spec:**
- Chart type: waterfall bar chart. X = drivers (traffic /
  ticket / mix / residual). Y = contribution to weekly revenue
  change in dollars.
- Encoding: positive bars in brand color; negative bars in
  diverging red. Connecting bars show cumulative running total.
- Interactivity:
  - Hover bar: numeric contribution + raw current and baseline
    values.
  - Click bar: that driver's weekly trend.
  - "Ask the agent about this": pre-fills with the dominant
    driver.

**Takeaway sentence shape (computed from data):**
*"Revenue {direction} {pct}% vs baseline; {dominant_driver}
contributes {pct}pp."*

**Rubric assessment:** Passes merchant-seat (driver focus) and
standalone (waterfall + takeaway reads cold). Cross-merchant N/A
by design.

---

### Trade specialist for TJX

Trade specialist for TJX reuses T1, T2, and T4 from Section 3
directly. Trade-area questions are cross-merchant via customer
geography (not segment), so they apply to any viewer including
non-grocers. The shared 5-neighborhood footprint (Phase 1.6
calibration) ensures TJX stores overlap with peer stores in enough
neighborhoods to make the comparison meaningful.

---

### Cross-cutting notes for Section 3B

The 18 net-new TBL/TJX questions (9 per viewer in pricing,
anomaly, demand) plus the 3 reused trade questions (T1, T2, T4)
give each non-grocer viewer 12 suggested questions total —
structural parity with the grocer viewers (KRG, ACM, WDX). Most
TBL/TJX questions are own-data variations of existing chart
patterns; the trade specialist's questions are cross-merchant via
geography. The new pattern application is T-A3 / R-A3 (Pattern 3
used in own-only diverging mode, comparing current week vs own
baseline rather than across peers) and T-D3 / R-D3 (Pattern 5
used in own-vs-own-baseline mode).

---

## Section 4 — Chart patterns reference

The 9 chart patterns the dashboard and agents follow. Phase 4
implements one helper per pattern; Phase 5 references the pattern
names in agent prompts. This section is the contract between them.

The same 9 patterns are duplicated in `chart_patterns.md` for
standalone reference.

---

### Pattern 1: Time-series-vs-peers

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

**Used by suggested questions:** A1. Also used in own-only mode
(single merchant, multiple series for sub-dimensions) by T-P1,
T-P2, T-D2, R-P1, R-D2.

**Library:** Plotly.

---

### Pattern 2: Cross-merchant comparison, single dimension

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
(with diverging encoding). Also used in own-only mode by T-P3,
T-D1, R-P3, R-D1.

**Library:** Plotly.

---

### Pattern 3: Cross-merchant heatmap

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

**Used by suggested questions:** P1. Also used in own-only
diverging mode (current week vs own baseline rather than own vs
peer) by T-A3 and R-A3.

**Library:** Plotly.

---

### Pattern 4: Scatter with peer context

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

### Pattern 5: Decomposition / waterfall

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

**Used by suggested questions:** D7. Also used in own-vs-own-
baseline mode (drivers computed against own prior baseline rather
than against a peer cohort) by T-D3 and R-D3.

**Library:** Plotly.

---

### Pattern 6: Geographic map

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

**Used by suggested questions:** T1, T2, T4. These three questions
are reused verbatim by TBL and TJX viewers (trade-area comparison
is cross-merchant via geography, not segment).

**Library:** Folium.

---

### Pattern 7: Small-multiples

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

### Pattern 8: Single-number callout / KPI

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

### Pattern 9: Table-with-drilldown

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

**Used by suggested questions:** A2, A3. Also used by T-A1, T-A2,
R-A1, R-A2, R-P2.

**Library:** Streamlit native.

---

## Section 5 — Free-form questions and pattern selection

The 12 suggested questions anchor the demo and the chat panels, but
the agents also handle free-form questions a merchant types in.
When that happens:

1. The agent classifies the question into one of the question
   shapes covered by Section 4's patterns — by reading the merchant
   text and identifying which pattern's "question shapes it fits"
   description matches.
2. The agent writes the SQL to produce the data for that pattern.
3. The agent emits a structured response containing:
   - The textual answer in the standard shape (Headline → Evidence
     → Therefore → Caveats).
   - The selected pattern name (e.g., `"pattern_1_time_series"`).
   - The chart data (rows + column names).
   - The computed takeaway subtitle for the chart.
4. The dashboard's chart renderer takes the pattern name + data +
   subtitle and produces the chart using that pattern's helper
   (implemented in Phase 4).
5. If no pattern in Section 4 fits the question shape, the agent
   answers text-only — no chart. The agent never invents a chart
   type outside the 9 patterns.

This contract is enforced in agent system prompts (Phase 5 work).
For Phase 3, `V3_QUESTIONS.md` and `chart_patterns.md` are the
contract between the renderer (Phase 4) and the agents (Phase 5);
the same 9 patterns serve both suggested and free-form questions.

---

## Section 6 — Phase 3 close-out

Phase 3 produced this document plus `chart_patterns.md` — both at
the repo root. Together they define what the v3 product asks the
data, how it answers, and how those answers render.

**Next phase: Phase 4 — dashboard implementation.** Phase 4
implements the 9 chart pattern helpers (one Python module per
pattern, or one helper per pattern in a shared module — Phase 4
decides), builds the suggested-question surfaces in the chat
panels, and wires the KPI strip into the suggested questions.

**Following: Phase 5 — agent prompt updates.** Phase 5 rewrites the
specialist agent prompts to reference the pattern names, include
the takeaway-subtitle templates, and enforce the "no chart outside
the 9 patterns" rule.

The 12 questions in this document are the design contract for
Phase 4 charts and Phase 5 prompts. Deviations from these specs
during implementation require documented justification — either a
chat-approved change to this doc, or a Phase-4/5 close-out section
explaining why the implementation diverged.

The 9 patterns in `chart_patterns.md` are the same patterns,
duplicated standalone so Phase 4 implementation work can reference
one focused file. If the two files drift, this one (V3_QUESTIONS.md)
is the source of truth.
