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

**Used by suggested questions:** A1.

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
(with diverging encoding).

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

**Used by suggested questions:** P1.

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

**Used by suggested questions:** D7.

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

**Used by suggested questions:** T1, T2, T4.

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

**Used by suggested questions:** A2, A3.

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
