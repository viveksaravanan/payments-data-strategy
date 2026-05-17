# V3 Vision

The rubric for everything that ships in v3. If an artifact — chart, agent
response, KPI, interaction — can't pass all three tests below, it doesn't
ship.

---

## Thesis

> A payments company sitting on baskets + payments + customers across
> multiple merchants can deliver each merchant a dashboard and an AI
> partner that surface cross-merchant insights no one else can produce —
> with the dashboard standing on its own as a real analytics tool, and
> the agent recommending specific actions on top.

This is the only claim v3 needs to make. Every demo beat, every chart,
every agent response is in service of it.

---

## Audience and demo bar

**Two tracks, one product:**

- **3-minute stakeholder pitch.** A decision-maker leaves convinced the
  cross-merchant data position is uniquely valuable and that the
  dashboard+agent combination operationalizes it. They don't need to
  understand the privacy engine — they need to lean forward.
- **10-minute deep dive.** A curious technical viewer drills into the
  charts, asks the agent follow-ups, reads the SQL, and stays convinced.
  Nothing they click reveals a seam.

The product has to support both without modes.

---

## The three rubrics

Every artifact must pass all three. Apply them out loud during reviews.

**1. The merchant-seat test.** *"After seeing this, what would I do
differently tomorrow?"* If the answer is "nothing" or "I'd need three
more questions answered first," the artifact isn't earning its place.

**2. The cross-merchant test.** *"Could a single merchant build this
from their own data alone?"* If yes, the artifact may still belong on
the dashboard — but it's not part of the v3 strategic story. Push it to
secondary; don't lead with it.

**3. The standalone test (charts only).** *"Does this make sense with
no agent prompt, no annotation, no narrator?"* A merchant landing cold
on the dashboard should grok every primary chart in under five seconds.
If they need the agent to interpret what they're seeing, the chart is
incomplete.

---

## Confidence calibration: the agent's posture

The agent recommends actions when warranted, recommends investigations
when not, and is explicit about what it cannot see. This is doctrine,
not style preference.

| Signal strength | Agent's posture |
|---|---|
| **Strong** — clean evidence, large effect, multiple corroborating data points | Recommend a specific action. "Consider X by [horizon]." |
| **Medium** — real signal but ambiguity remains | Recommend an investigation that, if it resolves a stated way, would justify an action. "First check Y. If Y, then consider X." |
| **Weak** — the data hints but can't support a claim | Flag what's notable, explicitly note what would be needed to act, and stop. "This is worth watching; we'd need [data we don't have] to recommend more." |

**The agent never:**
- Recommends actions whose outcome depends on data we don't see (costs,
  margins, customer service issues, internal strategy).
- Implies certainty the data doesn't support. "Customers are switching
  to peers" is a claim; "your dairy attach rate is 8pts below peers"
  is what the data actually says.
- Pads with caveats to perform humility. One honest caveat beats five
  hedges.

This calibration is itself a strategic point in the demo: an AI partner
that knows what it doesn't know is more valuable than one that always
has an opinion.

---

## Worked example: a great chart

**Question:** *"How does my dairy unit pricing compare to peer grocers?"*

This is the anchor question for v3. The chart that answers it is the
gold standard for every other primary chart on the dashboard.

### Spec

**Chart type:** Horizontal grouped bar, one row per top-10 dairy staple
SKU (whole milk, 2% milk, eggs dozen, sharp cheddar 8oz, etc.).

**Encodings:**
- Y-axis: SKU canonical name, sorted by your-merchant price descending.
- X-axis: unit price in dollars.
- Two bars per SKU: **own merchant** (brand color, solid) and **peer
  average** (gray, lighter). Peer range shown as a thin error-bar-style
  whisker through the gray bar (min/max across peer_a and peer_b for a
  grocery viewer).

**Title + takeaway:**
- Title: "Dairy staples: your unit pricing vs. peer grocers"
- Takeaway subtitle: *"You're priced above peers on 7 of 10 dairy
  staples; whole milk and eggs show the widest gap."*
  - The subtitle is computed from the data on each load, not hardcoded.
  - The subtitle is the non-negotiable element. Any primary chart
    without a takeaway sentence under the title fails the standalone
    test.

**Interactions:**
- Hover any bar → tooltip with exact prices, % gap to peer avg, and
  count of peers (k≥5 enforced).
- Click any SKU row → drilldown chart appears below: that SKU's price
  history across the 90-day window, own vs peer avg, with promo windows
  shaded.
- "Ask the agent about this" button on hover of any SKU row, pre-fills
  the agent prompt with the SKU and current view context.
- Time-range filter at chart level (default: last 30 days).
- Category filter (default: DAIRY; swappable to PRODUCE, BAKERY, etc.
  for the same visualization pattern).

**Empty / edge states:**
- If a SKU falls below k=5 in peers → row shown with "insufficient
  peer data" placeholder where the peer bar would be.
- If own merchant doesn't carry a SKU peers do → row omitted, footnote
  "3 peer SKUs not carried at your stores."

### Shape sketch

```
  Dairy staples: your unit pricing vs. peer grocers
  You're priced above peers on 7 of 10 dairy staples; whole milk
  and eggs show the widest gap.                  [last 30 days ▾]

  Whole milk (gallon)     ██████████████  $4.49 (you)
                          ████████░       $4.18 (peer avg, range $4.02–$4.34)

  Eggs (dozen large)      █████████████   $3.99 (you)
                          ████████░       $3.71 (peer avg, range $3.55–$3.88)

  2% milk (gallon)        █████████████   $4.49 (you)
                          ████████░       $4.21 (peer avg)
                                                          [Ask the agent ↗]
  ...
```

### Why this passes all three rubrics

- **Merchant-seat:** I can see exactly which SKUs I'm out of line on,
  by how much, and decide whether to investigate or adjust. Tomorrow's
  action could be: pull cost data on the top three gaps, decide if the
  positioning is intentional.
- **Cross-merchant:** Peer prices are unobservable without our position.
  A merchant could compare to their own historical prices, but not to
  their direct competitors' current prices on the same canonical SKU.
- **Standalone:** The takeaway subtitle delivers the headline finding
  with no narration. A cold viewer understands the chart in five
  seconds.

---

## Worked example: a great agent response

The merchant has clicked into the dairy chart, sees whole milk and eggs
as the widest gaps, and asks the agent:

> *"Why am I so far above peers on whole milk and eggs?"*

### The response

**Headline.** You're priced 7% above peer grocers on whole milk and 8%
above on eggs (dozen) — the two widest staple gaps in your dairy
category. Your dairy basket-attach rate is also 8 points below peers
over the same window, which is consistent with customers substituting
these staples elsewhere.

**Evidence.**
- Whole milk (gallon): you $4.49, peer_a $4.18, peer_b $4.02. You're
  +7.4% vs peer average.
- Eggs (dozen large): you $3.99, peer_a $3.78, peer_b $3.55. You're
  +8.6% vs peer average.
- Dairy basket-attach (share of grocery trips that include any dairy
  line): you 41%, peer average 49% over the last 30 days.
- The price gap has held steady across the 90-day window. The
  attach-rate gap widened from 5pts to 8pts over the same period.

**Therefore (medium-strength signal).**
- *First investigate:* whether the price gap is intentional
  positioning (cost structure, margin strategy) or unintentional
  (stale price list, vendor cost pass-through not yet reflected at
  peers). We can't see your costs or pricing strategy.
- *If unintentional:* a 2-3% reduction on whole milk and eggs would
  close roughly 60% of the index gap and is in line with peer pricing.
  The attach-rate widening suggests price sensitivity in this
  category at your stores specifically.
- *If intentional:* worth knowing the attach-rate cost. Customers
  substituting these staples to peers may also be shifting adjacent
  basket spend.

**Caveats.**
- We see baskets and payments, not your costs or vendor terms. Whether
  a price adjustment is feasible is a decision only you can make.
- "Substitution to peers" is the most plausible explanation for the
  attach-rate gap but not the only one — store-level service issues
  or stock-outs would look the same in this data.

**SQL shown in expander:** two queries, one tenant (your dairy
attach rate by week), one lake (peer prices and attach rates,
peer-pseudonymized). Each labeled clearly.

### Why this passes the rubrics

- **Merchant-seat:** Concrete next actions are named, and the merchant
  can act on either the investigative path or the adjustment path
  tomorrow.
- **Cross-merchant:** The "peer attach rate" comparison is impossible
  without our position. The single-merchant version of this response
  would be "your dairy attach rate is 41%" — true, useless.
- **Calibration:** Medium signal, so the agent recommends an
  investigation that, if resolved, justifies a specific action. It
  names what it can't see (costs, strategy) and offers one alternative
  explanation for the attach-rate gap. No padding, no false certainty.

---

## The gold-standard demo beat

The 30-second sequence that defines what "great" feels like end-to-end.
Every other interaction in the demo should feel like a sibling of this
one.

1. **Merchant lands on the dashboard.** The KPI strip across the top
   shows their dairy revenue index at **94** against a peer baseline of
   100. The index is visually distinct — a single number, clearly
   below par, with a small sparkline showing it's been declining over
   the 90-day window.

2. **Merchant clicks the dairy KPI.** The dairy section expands. The
   primary chart is the staple-price comparison spec'd above. The
   takeaway subtitle reads: *"You're priced above peers on 7 of 10
   dairy staples; whole milk and eggs show the widest gap."* They
   immediately see whole milk and eggs at the top.

3. **Merchant clicks the whole milk row.** Drilldown chart appears
   below: 90-day price history, own line vs peer average line, with
   the two visible peer-promo windows shaded. Their price is flat;
   peers had two 10-day discount windows.

4. **Merchant hits "Ask the agent about this."** The agent prompt
   pre-fills: *"Why am I so far above peers on whole milk and eggs?"*
   They send.

5. **Agent responds.** The response above — headline, evidence,
   therefore tiered by confidence, caveats, SQL expander. The merchant
   reads the *Therefore* section, notes the two-path investigation
   versus action, and now knows what to do this week.

6. **Merchant scrolls back to KPI strip.** Their attention has been
   directed to one specific lever (price adjustments on two SKUs) with
   a quantified impact (60% of the gap), grounded in a comparison no
   one else could have made.

That's the beat. It is built on:
- A KPI that delivers a signal in one glance.
- A chart that delivers a takeaway in one sentence.
- A drilldown that grounds the takeaway in history.
- An agent that converts the observation into an action path.
- Cross-merchant context at every step.

Every other demo question gets designed as a sibling of this. If a
question can't be re-shaped to fit this arc, it doesn't lead the demo
— it goes in secondary views.

---

## Out of scope for v3

Locked. These are off the table regardless of how tempting.

- **No new agents.** v3 polishes the agents already started: advisor,
  anomaly, demand, pricing, trade. Specialists beyond these stay on
  the v4 roadmap.
- **No new merchants.** Five-merchant panel, 10,000-customer panel,
  Charlotte metro. Adding merchants is a v4 conversation.
- **No new privacy mechanisms.** k=5 stays. L-diversity, differential
  privacy beyond stubs, per-session pseudonym randomization — all v4+.
- **No real-time pipeline.** Batch generation, batch load. No Kafka,
  no Flink.
- **No demographics, EBT, declines, or hardware/edge layer.** These
  are v2.5's documented exclusions and remain v3's.
- **No new dependencies beyond what's in `pyproject.toml`.** Streamlit,
  Plotly, Folium, Anthropic SDK. Anything else has to earn its way in
  via a documented decision.

If a v3 idea requires breaking one of these, it's not a v3 idea.

---

## The single rule

> If an artifact can't pass all three rubrics, it doesn't ship in v3.

Point at this line when scope drifts.
