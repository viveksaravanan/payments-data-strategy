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

**Question:** *"What's driving the transaction drop at my University
City stores?"*

This is the anchor question for v3. The chart that answers it is the
gold standard for every other primary chart on the dashboard.

### Spec

**Chart type:** Weekly line chart, one line per grocer (own + peer_a
+ peer_b). Each line traces weekly transaction count over the 90-day
window, normalized to the pre-decline baseline so cross-grocer scale
differences don't dominate the visual.

**Encodings:**
- X-axis: week (week-starting-Sunday across the 90-day window).
- Y-axis: weekly transaction count, normalized so the baseline week
  (or baseline-window mean) equals 100. Optional toggle: absolute
  counts vs normalized.
- Three lines: **own merchant** (brand color, solid), **peer_a**
  (gray, dashed), **peer_b** (gray, dotted). Optional thin shaded
  band showing the trough window (e.g., week of Apr 27).

**Title + takeaway:**
- Title: "University City transactions: you and grocery peers"
- Takeaway subtitle: *"Your UC transactions dropped 46% from baseline
  by week of Apr 27; your peers also declined (31% and 33%). The
  pattern is market-wide."* — exact numbers computed from data each
  load, not hardcoded.
  - The subtitle is the non-negotiable element. Any primary chart
    without a takeaway sentence under the title fails the standalone
    test.

**Interactions:**
- Hover any week → tooltip with absolute count, ratio to baseline,
  and which grocer is being hovered.
- Click any week → drilldown chart appears below: daily transaction
  count across that week for all three grocers.
- "Ask the agent about this" button on hover, pre-fills the agent
  prompt with something like *"Why is UC down so much?"*
- Time-range filter at chart level (default: full 90-day window so
  baseline → trough → partial-recovery is visible).
- Neighborhood filter (default: University City; swappable to
  Plaza Midwood, Dilworth, etc. for the same visualization pattern).

**Empty / edge states:**
- If a grocer has zero stores in the selected neighborhood → that
  grocer's line is omitted with a footnote: *"peer_b has no UC stores."*
- If a weekly cell for a peer drops below k=5 transactions (small
  store footprint × short window) → that point is suppressed with
  hover note *"peer week suppressed for privacy (k<5)."*

### Note on peer comparison

A v3 design note: with realistic pricing positioning (Phase 1.6
calibration), the peer-average signal collapses to baseline by
construction (premium and value grocers cancel each other in the
average). The actionable signal lives in per-peer comparison —
showing each grocery peer separately rather than averaging them.
Charts that average across peers are valid for some questions
("am I within market range?") but obscure the cross-merchant
story we lead with. Default to per-peer; add peer-average as an
optional comparison.

### Shape sketch

```
  University City transactions: you and grocery peers
  Your UC transactions dropped 46% from baseline by week of Apr 27;
  your peers also declined (31% and 33%). Market-wide.   [90 days ▾]

  index
  (baseline
   = 100)
    100 ┤ ●━━●━━●━━●━━●━━●━━●                          ── you
        │                    ╲                         ── peer_a
     90 ┤      ◆──◆──◆──◆──◆──◆╲                       ── peer_b
        │                       ╲ ╲
     80 ┤  ◇   ◇   ◇   ◇   ◇   ◇ ╲ ╲                   trough
        │                          ╲ ╲     ●───●       ┌─Apr 27─┐
     70 ┤                           ╲ ╲   ╱             ↓ ↓ ↓
        │                            ╲ ◆──◆──◆──◆
     60 ┤                             ●───────────●
        │                                  ◇──◇──◇──◇──◇
     50 ┤                              ●
        └─────┬─────┬─────┬─────┬─────┬─────┬─────┬───
           Mar 1  Mar 15 Apr 1  Apr 15 Apr 27 May 4  May 18
                                                 [Ask the agent ↗]
```

### Why this passes all three rubrics

- **Merchant-seat:** I see exactly when the decline started, how
  deep it went, and that my peers experienced the same. Tomorrow's
  action: investigate non-store-controllable factors (university
  semester, local economic event) rather than store-level fixes.
  Defer rent reduction or staffing changes until I know if it's
  recovering.
- **Cross-merchant:** Per-peer trajectories are unobservable
  without our position. A merchant could only see their own
  decline, not whether peers are co-declining. Peer corroboration
  is the strategic value.
- **Standalone:** The takeaway subtitle delivers the headline
  finding with no narration. A cold viewer understands the chart
  in five seconds.

---

## Worked example: a great agent response

The merchant has clicked into the University City chart, sees the
synchronized decline across all three grocers, and asks the agent:

> *"Why is UC down so much?"*

### The response

**Headline.** Your UC stores dropped 46% from baseline by the week
of Apr 27. Your peers also declined sharply (peer_a -31%, peer_b
-33%), so the pattern is market-wide — not specific to your stores'
operations.

**Evidence.**
- Your UC weekly transactions: ~500 baseline (Mar–early Apr) → 267
  at trough (week of Apr 27) → ~330 by mid-May (partial recovery).
- peer_a UC weekly transactions: ~530 baseline → 400 at trough →
  ~430 by mid-May.
- peer_b UC weekly transactions: ~340 baseline → 280 at trough →
  ~315 by mid-May.
- All three grocers' UC trajectories share a synchronized decline
  starting week of Apr 20, troughing week of Apr 27, partial
  recovery thereafter.

**Therefore (strong signal, since cross-grocer corroboration is
unambiguous).**
- *First check:* is there a known UC-specific event (university
  semester end, major campus event, local economic shock)? The
  timing matches typical end-of-semester at UNC Charlotte (early
  May).
- *If yes:* this is recurring and expected. Plan staffing and
  inventory around the academic calendar; no store-level
  intervention warranted.
- *If no:* investigate further — possible local competitive entry,
  construction, transit changes. Compare prior years' same-week if
  data exists.

**Caveats.**
- We see your transaction counts, not your customer demographics.
  Whether UC customers are students vs residents is something you'd
  know from your loyalty program; we don't.
- "Recovery" is partial as of last available week; full trajectory
  after May 25 not yet observable.

**SQL shown in expander:** two queries — one tenant (your UC weekly
counts) and one lake (peer UC weekly counts, peer-pseudonymized).
Both labeled clearly.

### Why this passes the rubrics

- **Merchant-seat:** Concrete next steps — check the academic-calendar
  context, and defer expensive interventions (staffing, rent
  renegotiation) if this turns out to be recurring.
- **Cross-merchant:** Peer corroboration cannot be built from tenant
  data alone — this is the strategic value. The single-merchant
  version of this response would be "you're down 46%" — alarming,
  unactionable.
- **Calibration:** Strong signal (clean evidence, large effect,
  multiple corroborating data points across three grocers), so the
  agent recommends specific actions. It names what it can't see
  (customer demographics, post-May-25 trajectory).

---

## The gold-standard demo beat

The 30-second sequence that defines what "great" feels like end-to-end.
Every other interaction in the demo should feel like a sibling of this
one.

1. **Merchant lands on the dashboard.** The KPI strip across the top
   shows a per-neighborhood "transaction trend" KPI. **University
   City** is flagged red — its index has dropped sharply over the
   last three weeks, with a small sparkline showing the
   baseline → trough trajectory. Other neighborhoods sit near 100.

2. **Merchant clicks the University City KPI.** The UC section
   expands. The primary chart is the weekly transaction trajectory
   spec'd above — three lines, own + peer_a + peer_b, normalized to
   baseline. The takeaway subtitle reads: *"Your UC transactions
   dropped 46% from baseline by week of Apr 27; your peers also
   declined (31% and 33%). The pattern is market-wide."* They
   immediately see all three lines bottom out together.

3. **Merchant clicks the week of Apr 27.** Drilldown chart appears
   below: daily transaction count across that week for all three
   grocers. Every weekday is down ~40-50% from comparable baseline
   weekdays; weekend stays slightly stronger but is also down.

4. **Merchant hits "Ask the agent about this."** The agent prompt
   pre-fills: *"Why is UC down so much?"* They send.

5. **Agent responds.** The response above — headline, evidence,
   therefore tiered by confidence (strong signal here), caveats,
   SQL expander. The merchant reads the *Therefore* section,
   recognizes UNC Charlotte's end-of-semester as the plausible
   driver, and now knows the next step is to confirm the academic-
   calendar context rather than restructure UC store ops.

6. **Merchant scrolls back to KPI strip.** Their attention has been
   directed to one specific decision (defer expensive store-level
   interventions, plan around the academic calendar instead),
   grounded in cross-grocer corroboration no single-merchant tool
   could have produced.

That's the beat. It is built on:
- A KPI that delivers a signal in one glance.
- A chart that delivers a takeaway in one sentence.
- A drilldown that grounds the takeaway in history.
- An agent that converts the observation into an action path.
- Cross-merchant context at every step.

Every other demo question gets designed as a sibling of this. If a
question can't be re-shaped to fit this arc, it doesn't lead the demo
— it goes in secondary views.

> The pattern in this beat — KPI flag → chart with takeaway → drill
> → ask the agent → agent reframes the observation as a specific
> investigation — is the standard sibling shape for every other
> demo question.

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
- **No demo storyline on the pasta promo divergence.** The anomaly was
  demo'd in early planning; Phase 2 found the signal isn't visible at
  any natural aggregation granularity. The anomaly module stays in
  the generator (no data manipulation) but no demo storyline anchors
  on it.
- **No dairy-pricing worked example.** The original V3_VISION worked
  example anchored on dairy pricing. Phase 2 found the natural dairy
  spread is ±5% per-peer (not the ±7-8% the example assumed). The
  University City decline replaces it as the worked example.

If a v3 idea requires breaking one of these, it's not a v3 idea.

---

## The single rule

> If an artifact can't pass all three rubrics, it doesn't ship in v3.

Point at this line when scope drifts.
