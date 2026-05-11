# V2.5 LLM-Backed Agents — Plan

This plan covers Phase 2 of the v2.5 workstream: replacing the dashboard's hardcoded placeholder handlers with real LLM-backed specialist agents. The data architecture (Phases 1–7), the report (Phase H), and the dashboard with placeholders (Phase 1) are complete. This document is the contract for the agent build that comes next.

---

## Context

`src/dashboard/placeholders.py` currently ships 16 hardcoded handlers — one per (agent × question) tuple — each running real SQL against tenant + lake, computing real numbers, and returning a `{agent, prose, table, chart}` dict. The dashboard's chat panel dispatches button clicks to these handlers and renders the response. The prose is hardcoded; the data is real. This gets the demo through a first-pass review.

Phase 2 replaces the hardcoded prose with **real LLM-backed specialist agents** that match or exceed the placeholder quality bar. The agents reason over the same data, can answer the same 16 questions plus arbitrary free-form input, and never hallucinate values. The dispatch contract from the dashboard stays the same — `placeholders.dispatch(agent_id, question_id, merchant_id)` still returns the response dict — but underneath, dispatch routes to LLM-driven specialist code instead of hardcoded handlers.

Strategy doc §10.2 specifies seven specialist agents plus a Conversational Business Advisor that orchestrates them. v2.5's data architecture supports five of the seven; two (Consumer Segmentation, Payment Optimization) hit honest data limits and are deferred. The four buildable specialists plus the orchestrator are the Phase 2 scope.

The single `MerchantAdvisor` in `src/agents/advisor.py` is the prior art — one LLM agent loop with `query_tenant`, `query_lake`, `schema_info`, `chart_spec` tools, hard cap at 6 turns, full SQL surfaced in the response. Phase 2 generalizes that pattern into a base specialist class, adds 4 specialist subclasses, and rewires the advisor as a router that dispatches to specialists.

---

## 1. Honest capability assessment

The strategy doc envisions agents that consume real-time event streams, integrate external signals (weather, events, demographics), and learn from feedback loops. v2.5 has a 90-day batch SQLite dataset, no streaming, no external signals, no feedback loop. Every agent response in Phase 2 must acknowledge this honestly via light caveats; the agents must not over-claim.

| Strategy doc §10.2 agent | What v2.5 supports | What v2.5 doesn't (honest gaps) | Phase 2 scope |
|---|---|---|---|
| **Demand Forecasting** | 90-day own + lake history; SKU/category time series; WoW deltas; promo-window vs baseline lift. Real promo data in `tenant_promotions` for campaign attribution. | No real-time stream; no external signals (weather, calendar, demographics); no projection beyond simple "extend recent trend"; no learning from past forecast accuracy. | **In** — frame as "demand sensing on the 90-day window" + "promo lift attribution from historical campaigns." |
| **Pricing & Benchmarking** | Lake `unit_price` carried exactly; canonical-name product matching across grocery peers; per-SKU peer pricing pivots; segment-aware comparison. | No competitor MAP / list-price feed; no margin data (`unit_cost` not modeled); no elasticity model; no price-test history. Cross-segment pricing comparison (e.g. TBL vs grocery) is meaningless. | **In** — the agent's strongest capability; data quality is highest here. |
| **Consumer Segmentation** | Own customers fully observable (`tenant_customers`). Aggregate behavioral patterns from peer transactions in lake (no peer customer cohorts). | `customer_id` is **dropped from the lake** — peer cohorts not possible at the customer level. The `grocer_affinity_type` and `behavioral_segment` fields are generator constructs, not observable from a real merchant's POS; surfacing them via an LLM agent would misrepresent what's achievable in production. | **Defer.** Customer-level segmentation belongs in a future phase that ingests opted-in demographic / loyalty data. |
| **Trade Area Intelligence** | Lake `lake_stores` with neighborhood + ZIP3 + peer_segment. Peer store density per neighborhood (anonymized). Own store performance + geography. | No demographics (income, household, age) per neighborhood; no foot-traffic / mobility data; no commercial-rent or site-cost layer; no demand-modeled "trade area pull." | **In** — frame at neighborhood granularity; flag the demographics gap as a productionization step. |
| **Payment Optimization** | Own payment_type / card_network / entry_mode / wallet_type / connectivity_type fields; peer mix in lake. | No authorization / decline / chargeback / interchange fields in v2.5 — those are the actual basis for payment optimization. Without auth code or settlement status, the agent can describe "what payment mix you see" but not "how to optimize routing or auth rates." | **Defer.** v2.5 schema is missing the fields this agent needs to do real work. Adding `auth_status`, `response_code`, `interchange_pct` would precede this agent. |
| **Anomaly Detection & Fraud Intelligence** | Three planted anomalies (University City decline, Plaza Midwood avocado spike, coordinated pasta promos) provide ground truth. Cross-merchant baselines via lake. Stage-by-stage windowed comparison. | **Fraud component is not v2.5**: no chargeback, no auth, no velocity-rules infrastructure. Only the "operational anomaly" half of this agent. No real-time alerting; the dashboard's chat panel is a batch / on-demand surface. | **In** — scope to **operational anomaly detection**, not fraud. The agent answers "what's unusual in my data" against the 90-day window and peer baselines. |
| **Conversational Business Advisor** | Inherits everything from the agents it orchestrates. Existing `MerchantAdvisor` is already this pattern, narrowed to a single LLM. | No multi-agent fan-out / synthesis. No learning from which specialist was actually useful. | **In** — refactor to **orchestrator** that routes to one or more specialists and synthesizes. |

**Universal honest-framing caveats** that go in every agent's response when relevant:
- *"Based on the 90-day window (Mar 1 – May 29, 2026)."*
- *"Peer comparison uses pseudonymized labels (`peer_a`..`peer_d`); the underlying merchants are not exposed."*
- *"Peer transaction totals are estimated from binned values; per-line prices are exact."*
- *"No external signals (weather, events, demographics) are factored in."*
- *"Customer-level patterns shown are observable from transactions only; loyalty / demographic enrichment isn't part of this view."*

---

## 2. Agent scope for Phase 2

Build **4 specialists + 1 orchestrator**:

1. **Pricing & Benchmarking Agent** — own pricing positioning, per-SKU peer comparison, segment-aware analysis.
2. **Anomaly Detection Agent** — flags unusual patterns; contextualizes against peer baselines; explains whether a signal is unique to the viewer or market-wide. Scope: operational anomalies only (no fraud).
3. **Demand Forecasting + Campaign Adjudication Agent** — slowing SKUs; lapsed-customer cohorts; projected promo uplift; campaign attribution (in-window vs baseline). Folds in the user-noted ice-cream / slow-mover scenario.
4. **Trade Area Intelligence Agent** — peer-grocer clustering; underserved neighborhoods; new-store siting candidates; per-store velocity vs same-neighborhood peers. Folds in the user-noted same-MCC / same-location / same-size comparison.
5. **Conversational Business Advisor (orchestrator)** — routes free-form input to one or more specialists, synthesizes a combined response when multi-specialist is needed, inherits the merchant context.

**Deferred** (document in plan as roadmap):

- **Consumer Segmentation** — `customer_id` is dropped from the lake by design; v2.5 can do own-merchant customer behavior but cross-merchant cohort segmentation isn't on the table. Plus, the generator's `grocer_affinity_type` and `behavioral_segment` fields are synthetic constructs, not observable in a real production setting. Deferred until v3 adds demographic / loyalty enrichment.
- **Payment Optimization** — needs `auth_status`, `response_code`, `interchange_pct`, chargeback / dispute history. None of these are in v2.5 schema. Deferred until those fields are added.

The four specialists map directly to the existing dashboard chat panel's four agent cards. The dispatcher contract stays unchanged:

```python
# src/dashboard/placeholders.py — Phase 2 keeps this signature
def dispatch(agent_id: str, question_id: str, merchant_id: str) -> dict
```

Phase 2 changes the implementation underneath: `dispatch` resolves to a specialist class which runs an LLM-backed tool loop. The 16 suggested-question buttons keep the same per-segment question lists from Phase 1.

---

## 3. Per-agent design

### 3.1 Pricing & Benchmarking Agent

**Persona.** A pricing analyst at a payments company with full visibility into the merchant's own line-item prices and access to a peer-pseudonymized cross-merchant view. The agent answers per-SKU and per-category pricing questions with concrete dollar figures from real data.

**Scope.**
- Own avg unit price by category / SKU.
- Per-canonical-SKU comparison against same-segment peers.
- "Above market" / "below market" identification on high-volume SKUs.
- Category-share trends (e.g. produce as % of revenue, comparing to peer averages).
- For QSR / off-price retail merchants (no same-segment peers in the panel): fall back to estimated peer ticket positioning via `txn_total_bin` midpoints, with the imprecision caveat surfaced.

**Tools** (existing + 1 new).
- `query_tenant(sql)` — existing; for own pricing data.
- `query_lake(sql)` — existing; for peer pricing data, scoped per viewer.
- `schema_info()` — existing.
- `make_chart(spec)` — new; builds a Plotly figure dict for a comparative bar / grouped-bar chart. Takes `{kind: 'grouped_bar' | 'horizontal_bar' | 'donut' | 'line', x, y, series?, title, x_format?, y_format?}`. Returns a dict the renderer pipes through `st.plotly_chart`.

**Prompt outline** (sketch — final draft lives in `src/agents/prompts/pricing.md`):

```
You are a pricing analyst at a payments company, advising the ops team
at {{viewer_name}}. You see {{viewer_name}}'s own data at full
granularity and an anonymized cross-merchant view where peers appear as
peer_a..peer_d.

# Scope
Per-SKU and per-category pricing comparison, segment-aware. You don't
recommend margin actions, MAP enforcement, or competitive responses —
you describe the pricing landscape and the gaps.

# Tool usage
- query_tenant for own unit_price data — always WHERE merchant_id =
  '{{viewer_id}}'.
- query_lake for peer unit_price data — joins on canonical_name to
  match cross-merchant SKUs.
- Same-segment peers only matter for the first cut; if cross-segment
  peers are the only data available (TBL, TJX), fall back to txn_total_bin
  midpoint averaging and surface the ±$5–25 caveat.
- make_chart for the final comparative figure when the response is
  meaningfully visual.

# Output
1. 1–3 sentence summary referencing real $ numbers.
2. Per-SKU comparison table (max 8 rows).
3. Chart spec if comparing 3+ SKUs or 2+ peers.
4. Caveats: 90-day window, peer pseudonymization, txn_total binning
   imprecision (when relevant).
```

**Test cases.**

| # | Question | Expected behavior |
|---|---|---|
| 1 | "How am I priced on dairy vs peers?" (KRG) | Two queries: own DAIRY avg + lake DAIRY avg by peer_id. Returns prose ("$4.02 vs peer_a $4.11, peer_b $4.00"), top-5 SKU table, grouped-bar chart. |
| 2 | "Which products am I significantly above market on?" (ACM) | Lake query for same-segment peers, identify SKUs where own price > peer mean + 5%. Return ranked table. |
| 3 | "Which products am I below market on?" (WDX) | Mirror of #2; identifies WDX's discount positioning. |
| 4 | "Show category share trends in produce" (KRG) | Own produce % of revenue + peer produce %. Single number comparison + bar chart. |
| 5 | "How is my average ticket positioned vs other peers?" (TBL) | No same-segment peers → fall back to bin-midpoint estimation. Surface the imprecision caveat prominently. |
| 6 | "Compare my pricing on whole milk gallon specifically" (KRG) | Single-SKU query, own avg + peer_a/peer_b values, small comparison table, no chart needed. |

**Response shape.**

```python
{
    "agent": "Pricing & Benchmarking Agent",
    "prose": "Your average dairy unit price is $4.02 ... peer_a sits 2.2% above ...",
    "table": <DataFrame: 5 rows × 4 cols (Product, Yours, peer_a, peer_b)>,
    "chart": <Plotly fig dict: grouped horizontal bar>,
    "caveats": [
        "Based on the 90-day window (Mar 1 – May 29, 2026).",
        "Peer prices are exact per-line unit_price; transaction totals would be binned.",
    ],
}
```

---

### 3.2 Anomaly Detection Agent

**Persona.** An operational analyst who scans the merchant's recent transaction stream for unusual patterns and contextualizes findings against peer baselines. Operational anomalies only — not fraud, not chargebacks.

**Scope.**
- Per-store volume drops or spikes vs the merchant's baseline.
- Per-category demand anomalies (e.g. avocado spike at Plaza Midwood).
- Coordinated peer behavior (e.g. three grocers running pasta promos in overlapping windows).
- Was the anomaly market-wide (peers saw it too) or unique to the viewer? The agent's most important framing job.
- Reuses the three planted-anomaly SQL patterns from `scripts/generate_report_data.py::_anomaly_series`.

**Tools** (existing + 1 new).
- `query_tenant`, `query_lake`, `schema_info`, `make_chart` — same as Pricing.
- `window_baseline(metric, start, end, comparison_start, comparison_end, group_by)` — new helper; computes a metric (e.g. avg daily transactions per store) for two date ranges and returns a comparison ratio. Saves the agent from re-writing windowed SQL on every anomaly query.

**Prompt outline.**

```
You are an operational analyst at a payments company, looking for
unusual patterns in {{viewer_name}}'s recent transactions. Your job is
to flag what's anomalous and answer the contextualizing question: is
this unique to you, or market-wide?

# Scope
Operational anomalies only — store-level volume changes, category
demand spikes, coordinated peer behavior. NOT fraud, NOT chargebacks,
NOT real-time alerting — those aren't in scope for v2.5.

# Known patterns in the panel
The data contains three planted operational anomalies:
- University City stores (a campus-driven decline cycle in late April).
- Plaza Midwood Kroger avocado spike (Apr 22 peak).
- Coordinated pasta promos across all three grocers in mid-late April.

# Tool usage
- query_tenant for own data; use stage-window SQL patterns where the
  question maps to a known anomaly (e.g. University City decline → 5-
  stage breakdown).
- query_lake to confirm whether peers saw the same pattern — the
  market-wide-vs-unique answer requires peer context.
- window_baseline as a shortcut for the common pattern.
- make_chart for the comparison figure.

# Output
1. 1–3 sentence summary with the ratio / magnitude of the anomaly.
2. Stage / window breakdown table with peer columns.
3. Chart: typically a grouped bar (stages × peers) or line (time
   series with markers).
4. Caveat: the planted anomalies are operational, not fraud; v2.5
   doesn't carry auth / decline / chargeback data.
```

**Test cases.**

| # | Question | Expected behavior |
|---|---|---|
| 1 | "Anything unusual recently?" (KRG) | Survey: scan for high-magnitude deviations in last 30d. Returns 2–3 candidates with brief framings. |
| 2 | "Why are my University City stores declining?" (KRG) | Stage breakdown (Baseline → Stage 4), per-store-per-day avgs for own + peer_a + peer_b. Return grouped bar. Note Kroger's 0.63× is steepest. |
| 3 | "Is this happening to peers too?" (KRG, follow-up) | Reframes UC data as a peer-comparison answer: "Yes — peers fell to 0.71× / 0.72×, less than your 0.63×. Market-driven, not unique to you." |
| 4 | "Why did avocado spike at Plaza Midwood on April 22?" (KRG) | Daily qty series Apr 15–26, KRG vs peer_a vs peer_b at Plaza Midwood. Show peak isolated to viewer. |
| 5 | "Are any of my stores slowing down vs the panel?" (any merchant) | Per-store own delta + per-peer delta; flag stores where own ratio < peer median ratio. |
| 6 | "Did pasta promos work for me last month?" (KRG / WDX) | Promo window vs baseline daily lines comparison; report observed ratio (KRG ~2.09×, WDX ~1.26×) and note Acme's promo failed (0.82×). |

**Response shape.** Same as Pricing, with `chart` typically a grouped-bar or line-with-markers.

---

### 3.3 Demand Forecasting + Campaign Adjudication Agent

**Persona.** A demand-sensing analyst who watches SKU velocity, identifies slowing items, surfaces former buyers who lapsed, projects promo uplift, and adjudicates whether past campaigns actually worked.

**Scope.**
- Slowing SKUs (week-over-week negative velocity).
- Slow-mover scenarios (e.g. ice cream not moving) — recommended-target cohort analysis, suggested discount depth from historical promo elasticity.
- Lapsed-buyer cohort identification.
- Projected promo uplift from history (e.g. "what would a 30% off DAIRY promo to lapsed dairy buyers look like").
- Campaign attribution — for a named historical promo, what was the actual lift vs the implied baseline?
- "Slowing ice cream → who used to buy it / what depth promo / projected uplift" is the central scenario for this agent.

**Tools** (existing + 1 new).
- `query_tenant`, `query_lake`, `schema_info`, `make_chart`.
- `wow_delta(merchant_id, category_or_sku, dimension, n_weeks=1)` — new helper; computes week-over-week deltas on revenue / qty / lines for a category or SKU. The most repetitive pattern in this agent's work.

**Prompt outline.**

```
You are a demand-sensing analyst at a payments company, advising
{{viewer_name}}. You scan their SKU velocity, identify slowing items
and lapsed customers, and adjudicate whether past promos worked.

# Scope
- Slowing SKUs (week-over-week within the 90-day window).
- Lapsed-buyer cohorts (customers who bought a category before but not
  in the recent window).
- Projected promo uplift — historical promo lifts as the projection
  basis. No external elasticity model.
- Campaign attribution — in-window vs out-of-window per-SKU lift for
  promos in tenant_promotions.

# Worked scenario: slow-mover ice cream
- Query own tenant for ice cream SKUs (subcategory like '%cream%' under
  category DAIRY or FROZEN).
- Identify SKUs declining 5%+ WoW.
- Find customers who bought these SKUs in the first half of the window
  but not in the last 14 days — the lapsed cohort.
- Look up historical promo lifts in tenant_promotions for similar
  discount depths.
- Surface: "{N} lapsed customers, projected lift {pct%} at 30% off,
  estimated 2–3-week revenue range $X–$Y."

# Tool usage
- query_tenant for own SKU velocity, lapsed cohort, historical promos.
- query_lake only when cross-merchant context matters (e.g. "is this
  category slowing for peers too?").
- wow_delta for the canonical WoW computation.
- make_chart for the SKU velocity bars or the lift histogram.

# Output
1. Summary with the cohort size + projected lift / observed lift.
2. Top-N table (SKUs by WoW decline, or lapsed buyers by spend).
3. Chart: WoW bars or in-window-vs-baseline lift comparison.
4. Caveat: projections use historical promo lifts as the baseline
   without external elasticity modeling.
```

**Test cases.**

| # | Question | Expected behavior |
|---|---|---|
| 1 | "What dairy SKUs are slowing down?" (KRG) | Last-7d vs prior-7d quantity deltas on DAIRY SKUs with prior_week_qty ≥ 50. Top 6 ordered by % decline. |
| 2 | "Which customers used to buy these regularly?" (KRG, follow-up) | Lapsed cohort: bought DAIRY between Mar 1 and May 15, didn't buy DAIRY in last 14d. Return count + share. |
| 3 | "What's the projected uplift from a 30% off promo to those customers?" (KRG) | Historical DAIRY promo lifts averaged; project onto ~lapsed cohort size; return range estimate. Caveat about projection basis. |
| 4 | "Show campaign attribution for promo Spring Pasta Sale" (KRG) | Pull `tenant_promotions` row for matching name; in-window vs out-of-window lines/day; uplift %. |
| 5 | "What menu items are slowing down?" (TBL) | Branches segment — same WoW logic but at category level (no SKU-level dairy framing for QSR). |
| 6 | "Slow-mover ice cream — what should I do?" (KRG, free-form ad hoc) | The flagship slow-mover scenario. Identifies declining SKUs in ice-cream subcategory, lapsed cohort, projected promo lift. End-to-end. |

---

### 3.4 Trade Area Intelligence Agent

**Persona.** A market-analysis specialist who sees the merchant's own store footprint and a peer-pseudonymized neighborhood density view. Identifies underserved markets, competitive clusters, and same-MCC same-neighborhood velocity gaps.

**Scope.**
- Per-neighborhood peer density (same-segment peers vs all peers).
- Underserved neighborhoods (peer presence + zero own stores).
- New-store siting candidates synthesizing density + own coverage.
- Per-store velocity comparison: same-MCC same-location same-size peers, when the data supports it.
- Cross-merchant trade-area "do I sit where peer grocers sit, or in different neighborhoods?"

**Tools.**
- `query_tenant`, `query_lake`, `schema_info`, `make_chart`.
- `peer_store_density(merchant_id, same_segment_only=True)` — new helper; returns the neighborhood-by-peer-store-count table. Saves repeated lake-stores queries.

**Prompt outline.**

```
You are a market / trade-area analyst at a payments company. You see
{{viewer_name}}'s store geography (full lat/lng/neighborhood) and an
anonymized neighborhood-level view of peer stores. Your job is to
answer questions about where peers cluster, where the viewer is
underweight or absent, and where to consider a new store.

# Scope
- Neighborhood-level density, never exact peer lat/lng.
- ZIP3 is the lowest spatial granularity in the lake; full ZIP5 / lat
  / lng exist only in tenant.
- Same-MCC peers come first; cross-MCC peer presence is a secondary
  layer.
- No demographics, no foot-traffic, no rent/site-cost layers in v2.5
  — flag those as productionization steps.

# Tool usage
- query_tenant for own store footprint (full granularity).
- query_lake / lake_stores for peer density (ZIP3 + neighborhood).
- peer_store_density helper as the canonical aggregation.

# Output
1. Summary identifying 1–3 strongest candidate neighborhoods + why.
2. Table: neighborhood × own_stores × peer_stores × read
   ("underserved", "underweight", "balanced", "saturated").
3. Chart: horizontal bar of peer density per neighborhood, or grouped
   bar comparing own vs peer counts by neighborhood.
4. Caveat: trade-area analysis here uses store-count proxies for demand
   density; production would layer in demographics / foot-traffic.
```

**Test cases.**

| # | Question | Expected behavior |
|---|---|---|
| 1 | "Where do peer grocers cluster?" (KRG) | Lake `lake_stores` filtered to grocery; group by neighborhood; ranked. Returns Dilworth / Matthews / University City at the top with 6 peer stores each. |
| 2 | "Which neighborhoods are underserved by my chain?" (KRG) | Set difference: neighborhoods with peer grocers but zero own stores. Returns Concord / Huntersville with peer counts. |
| 3 | "Where should I consider opening a new store?" (ACM) | Synthesis: underserved + underweight neighborhoods, ranked. Top recommendation + reasoning. |
| 4 | "How does my per-store velocity compare in same neighborhoods?" (KRG) | For each neighborhood with own + peer stores, compute own avg-txns-per-store vs peer avg. Flag neighborhoods where viewer is materially behind. |
| 5 | "Where do retail competitors cluster in the metro?" (TJX) | Cross-segment fallback — no same-segment peers, so show density across all peer segments and flag the singleton-segment caveat. |
| 6 | "Show me a head-to-head comparison of my Dilworth stores vs Dilworth peer grocers" (KRG) | Filter both viewer + peers to Dilworth; per-store velocity comparison; flag the same-MCC same-neighborhood scenario explicitly. |

---

### 3.5 Conversational Business Advisor — orchestrator

**Persona.** The merchant-facing front door. Receives all free-form questions; routes to one specialist (most common) or to multiple specialists with synthesis (when the question genuinely spans more than one domain). Inherits the merchant context — every specialist it invokes uses the same `MerchantContext`.

**Scope.**
- Routing logic: LLM-call router with keyword-fallback (see §6).
- Multi-specialist synthesis: rare but supported — e.g. *"How are my University City stores positioned competitively?"* might pull Anomaly (UC decline pattern) + Trade Area (UC store-density) + Pricing (do I price differently at UC stores?).
- Synthesis combines the constituent responses into one coherent answer with each specialist's contribution attributed.

**Tools.** No direct DB tools; the orchestrator's "tools" are the four specialists themselves.

**Test cases.**

| # | Question | Expected behavior |
|---|---|---|
| 1 | "How am I priced on dairy vs peers?" | Single-specialist route → Pricing. |
| 2 | "Why are my University City stores declining?" | Single-specialist route → Anomaly. |
| 3 | "Slowing ice cream — what should I do?" | Single-specialist route → Demand (the flagship slow-mover scenario). |
| 4 | "Are my University City stores priced differently and is that hurting me?" | Multi-specialist: Pricing (UC store pricing vs other own stores + peers) + Anomaly (the UC decline pattern) + synthesis. |
| 5 | "Where should I open a new store and what should I price like there?" | Multi-specialist: Trade Area (siting) + Pricing (existing store-level price positioning if relevant) + synthesis. |
| 6 | Garbage input ("klsdfksdjf") | Router fails → fallback to keyword routing → no match → returns a polite "I don't see a specialist for this. Try one of the suggested questions, or ask in plain English." |

---

## 4. Response format and visualization requirements

The placeholder layer sets a strong quality bar that the LLM agents must match or exceed. **Every Phase 2 response must satisfy:**

**Prose quality.**
- Plain English, not analyst jargon.
- 1–3 sentence headline summary at the top.
- Reference real numbers from queried data — never hallucinate values.
- Comparative framing where possible ("you are 2.2% above peer_a") — relative numbers read better than absolute for an executive audience.
- Honest limitations acknowledged via caveats (separate field in the response dict).
- Markdown formatting allowed (**bold**, `code`, bullet lists, simple tables for tight comparisons).

**Tables.**
- Max 8–10 rows per table; if more data exists, the agent summarizes the rest in prose.
- Well-labeled columns; numbers formatted (`$X.XX`, `X,XXX`, percentages as `+X.X%`).
- Peers always labeled `peer_a` / `peer_b` / `peer_c` / `peer_d` — never the underlying merchant names.

**Visualizations.**

| Data shape | Chart type |
|---|---|
| Time series (daily volume, daily SKU qty) | line or area chart |
| Cross-merchant comparison (own vs peer_a vs peer_b on N SKUs) | grouped horizontal bar |
| Distribution (txns per customer buckets) | horizontal bar |
| Geographic density | heatmap or annotated map |
| Category / composition | bar (preferred) or donut |
| Stage-by-stage anomaly windows | grouped bar with stage on x-axis |

Charts max ~300px tall, inline in the chat panel. Match the dashboard palette (`#0F4C81` accent, `#D8E2EE` light, `#5B7B58` green, `#C0563F` warm). Clear axis labels, brief title. Rendered via Plotly through Streamlit's `st.plotly_chart`.

**Response dict shape** (extends Phase 1 shape with `caveats`):

```python
{
    "agent":    "Pricing & Benchmarking Agent",
    "prose":    "...",                 # required, markdown
    "table":    <pandas DataFrame>,    # optional
    "chart":    <Plotly Figure>,       # optional — a real go.Figure, not a spec
    "caveats":  ["...", "..."],        # optional — list of strings rendered as a muted footnote
    "sql":      [                      # required — every query the agent ran
        {"tool": "tenant", "query": "..."},
        {"tool": "lake",   "query": "..."},
    ],
}
```

The dashboard's `chat._render_chat_entry` renders prose first, then table, then chart, then caveats in a muted footer. SQL goes into an expander beneath the response — the dashboard already has the pattern from the prior advisor; lift directly.

---

## 5. Merchant context isolation (correctness-critical)

The single most important invariant in Phase 2: **every agent response is scoped to the viewing merchant, and no agent ever exposes another merchant's real name.** The existing advisor enforces this through SQL guards at the tool layer; Phase 2 makes that pattern systemic.

### MerchantContext dataclass

```python
# src/agents/context.py

@dataclass
class MerchantContext:
    viewing_merchant_id:      str      # 'KRG' / 'ACM' / 'WDX' / 'TBL' / 'TJX'
    viewing_merchant_name:    str      # 'Kroger' / 'Acme' / ...
    viewing_merchant_segment: str      # 'grocery' / 'qsr' / 'off_price_retail'

    def query_tenant(self, sql: str) -> pd.DataFrame:
        """Wraps src.agents.tools.query_tenant, baking in current_merchant=self.viewing_merchant_id."""

    def query_lake(self, sql: str) -> pd.DataFrame:
        """Wraps src.agents.tools.query_lake, baking in viewing_merchant_id=self.viewing_merchant_id."""

    def peer_label_to_real(self, peer_id: str) -> str:
        """Internal-only — used by SQL audit, never by agent response. The agent never sees the underlying mapping."""

    def make_chart(self, spec: dict) -> go.Figure:
        """Plotly figure builder for the agent."""

    def schema_info(self) -> dict:
        """Returns the DDL — same as the existing tool."""
```

**Construction.** The dashboard creates one `MerchantContext` per (viewer × session) and passes it to `dispatch()`. Switching merchants in the dashboard → fresh context → no leakage across the merchant boundary.

**Tool binding.** The Anthropic SDK takes `tools=[...]` at request time. Each specialist constructs its tool list at agent-instantiation time, bound to the context — the LLM sees tool schemas, but the tool implementations always close over the context's merchant_id. The LLM cannot pass a different merchant_id; the schema doesn't expose one.

### Audit checks (run as tests, not just code review)

1. **SQL audit.** Every tenant query in `sql_log` must contain `merchant_id = '<viewing_merchant_id>'` (or be reject-able by `tools.has_merchant_predicate`). Every lake query must have been run through `tools.query_lake` with `viewing_merchant_id=context.viewing_merchant_id`.
2. **Response audit.** The response dict (prose + table + chart annotations) is regex-scanned for raw merchant names (`Kroger`, `Acme`, `Winn-Dixie`, `Taco Bell`, `TJ Maxx`) — except for the viewer's own name, which is allowed. Any peer leak fails the test.
3. **Cross-viewer correctness.** Run the same question for two different viewers. The "own" data should differ; the peer labels should differ in meaning (Kroger's `peer_a` = Acme; Acme's `peer_a` = Kroger). The peer numbers themselves should be merchant-specific.
4. **Dashboard state.** When the merchant selectbox changes, the chat panel must clear history (already wired in Phase 1) and the next response must scope to the new merchant. Test programmatically by switching contexts and asserting the chat-history dict's keys reset.

These four checks become pytest tests in `tests/test_agents_phase2.py`.

---

## 6. Orchestrator routing logic

The Conversational Business Advisor receives every free-form question and decides which specialist(s) should answer.

### Layered routing

1. **Primary: LLM router call.** Cheap model (Haiku) classifies the question into one of {pricing, anomaly, demand, trade, multi}. Prompt is small (~200 tokens) — system message lists the four specialists with one-line capability summaries; the user's question is the input. Output is JSON: `{"primary": "...", "secondary": ["..."] }`. If `secondary` is non-empty, the question is multi-specialist.

2. **Fallback: keyword routing.** If the router call fails (timeout, JSON parse error, rate limit), drop to the keyword router already shipped in `src/dashboard/chat.py::route_free_form`. Maps to a single specialist via keyword list (pricing keywords → Pricing, anomaly keywords → Anomaly, etc.).

3. **Last-resort default.** No match → return a graceful "I don't see a specialist for this — try a suggested question above, or rephrase."

### Multi-specialist synthesis

When the router returns more than one specialist:

- Each specialist runs **in parallel** (asyncio gather) with the same `MerchantContext`. Each produces its own response dict.
- A **synthesis LLM call** (Sonnet, prompt ~800 tokens) takes the constituent responses and writes a coherent unified prose section + an attribution footer noting which specialists contributed.
- The combined response merges tables (concatenated with a header per source) and stacks charts vertically.
- Cost: 2× specialist calls + 1× synthesis call. Multi-specialist responses are slower (parallel doesn't help latency on the synthesis step) — expect ~10–15s end-to-end vs ~5–7s for single-specialist.

### Routing test cases

| Question | Expected route |
|---|---|
| "How am I priced on dairy vs peers?" | pricing |
| "Why is my revenue declining?" | anomaly |
| "Slow-mover ice cream — what should I do?" | demand |
| "Where should I open a new store?" | trade |
| "Are my University City stores priced differently and is that hurting me?" | multi: pricing + anomaly |
| "Random question with no clear keyword match" | fallback keyword → demand (default) |

---

## 7. Infrastructure plan

### New files

```
src/agents/
├── context.py             # MerchantContext dataclass + tool wrappers
├── llm.py                 # Anthropic client wrapper (retry, timeout, streaming)
├── specialist.py          # Base Specialist class + Response dataclass
├── pricing.py             # PricingSpecialist
├── anomaly.py             # AnomalySpecialist
├── demand.py              # DemandSpecialist
├── trade.py               # TradeSpecialist
├── orchestrator.py        # Refactored advisor → orchestrator + router
└── prompts/
    ├── pricing.md
    ├── anomaly.md
    ├── demand.md
    ├── trade.md
    ├── orchestrator_router.md     # the tiny Haiku router prompt
    └── orchestrator_synthesis.md  # the Sonnet synthesis prompt
```

### Files to update

- `src/agents/advisor.py` — refactor into `orchestrator.py`; the standalone CLI / dashboard entry point still constructs an "advisor" instance, but underneath it's now the orchestrator. Old direct-advisor mock mode is retained as a degraded fallback.
- `src/agents/tools.py` — extend with the helper tool implementations (`window_baseline`, `wow_delta`, `peer_store_density`, `make_chart`). Existing `query_tenant` / `query_lake` are reused.
- `src/dashboard/placeholders.py` — `dispatch(agent_id, question_id, merchant_id)` now constructs a `MerchantContext`, looks up the right specialist, and calls `specialist.answer(question)`. The 16 hardcoded handlers stay around for **mock mode** (when `ANTHROPIC_API_KEY` is missing) — they're the safety net.
- `pyproject.toml` — `anthropic` is already a dep; no new packages.
- `.env` — `ANTHROPIC_API_KEY` consumed via `python-dotenv` (already wired).

### LLM client wrapper (`src/agents/llm.py`)

Single shared Anthropic client with:
- **Retry**: exponential backoff on 429 / 503 / transient connection errors. Max 3 attempts, base delay 1s.
- **Timeout**: 60s per call (Sonnet tool-loop is short; if we exceed 60s something is wrong).
- **Streaming**: yes — Anthropic supports streaming and Streamlit can render incremental text via `st.write_stream`. Phase 2D wires this; Phase 2A–C use non-streaming for simplicity.
- **Model selection**: configurable per call. Default Sonnet (`claude-sonnet-4-6`) for specialists; Haiku (`claude-haiku-4-5`) for the router.
- **Cost telemetry**: log input / output token counts per call to stdout (or a per-session counter). Surfaces in the dashboard footer for demo transparency.

### Error handling

- **API key missing** → fall through to the existing hardcoded placeholders. The dashboard footer surfaces "running in mock mode (no LLM)." No silent failure.
- **API call fails after retries** → return a response with `prose` = *"The specialist agent encountered an error: <reason>. The hardcoded placeholder is shown below as a fallback."* Then call the Phase 1 hardcoded handler for the same (agent_id, question_id) and merge its output.
- **MAX_TURNS reached** → return `prose` = *"I couldn't converge on a full answer in 6 turns; here's the best partial."* with whatever SQL + last_table the agent produced. (Existing pattern from `MerchantAdvisor`.)
- **Tool error** (SQL guard rejection) → surfaced as a `tool_result` with `is_error: true`; the model is expected to retry with corrected SQL.

### Chart generation

`make_chart(spec)` returns a real Plotly `go.Figure`. The agent's tool input is a structured spec:

```python
{
    "kind":   "grouped_bar" | "horizontal_bar" | "line" | "donut" | "heatmap",
    "title":  "Dairy price comparison",
    "x":      "Product",
    "y":      ["Yours", "peer_a", "peer_b"],   # multiple = grouped
    "data":   <list of dicts>,                 # rows from the agent's queries
    "x_fmt":  "category",
    "y_fmt":  "currency",                       # currency | count | pct | float
}
```

The dashboard renders via `st.plotly_chart(fig, use_container_width=True)`. Height capped at 300px in CSS.

---

## 8. Cost and operational considerations

### Per-question cost estimate

Specialist call (Sonnet):
- System prompt: ~1,500 tokens (persona + scope + tool descriptions + worked examples).
- User question + retrieved tool outputs: ~2,000–4,000 tokens (queries + result rows).
- Model output (tool calls + final response): ~800–1,500 tokens.
- Total: ~5,000 input + ~1,200 output tokens.
- Sonnet pricing (~$3 / Minput, ~$15 / Moutput): **$0.015 + $0.018 = ~$0.033 per single-specialist question.**

Router call (Haiku): ~$0.001.

Multi-specialist (2 specialists + synthesis): ~$0.075 / question (3× single-specialist cost).

Estimated demo cost: a 60-minute exec walkthrough with ~20 questions ≈ **$0.70 – $1.50**.

### Caching

- **Suggested-question cache.** Per-(merchant_id × question_id) — the same suggested-question button click for the same viewer always returns the same response. Streamlit `@st.cache_data(ttl=3600)`. Cached responses don't expose merchants to each other (the cache key includes merchant_id).
- **Free-form questions.** Never cached — each ad-hoc question routes fresh, even if textually similar to a prior one (because the demo's value is showing the agent thinking through it).
- **Schema info.** `schema_info()` cached at process startup; doesn't change.
- **Lake query DataFrame.** Already cached at the Phase 1 layer in `data.py`. Specialists hit those caches naturally.

### Rate limits

Anthropic limits: 50 req/min on Sonnet at the default tier. For a demo with one user at a time this is non-binding. If the demo opens to multi-viewer simultaneous sessions, queue requests at the orchestrator level.

### Observability

- **stdout logging** of every LLM call: timestamp, model, input tokens, output tokens, latency.
- **Per-session token counter** in dashboard footer ("This session: 24,500 tokens, ~$0.18").
- **Tool call audit log** in `src/agents/llm.py`: every tool invocation with merchant_id + SQL + row count. Helps the merchant-isolation audit tests.

---

## 9. Phased build plan

Each phase ends in a reviewable artifact. The dashboard works in mock mode after each phase (specialists fall back to Phase 1 hardcoded handlers when not yet implemented).

### Phase 2A — Infrastructure + Pricing & Benchmarking Agent (~3 hours)

**Goal.** Land the agent infrastructure end-to-end with one working specialist.

**Files.**
- New `src/agents/context.py`, `src/agents/llm.py`, `src/agents/specialist.py`.
- New `src/agents/pricing.py`, `src/agents/prompts/pricing.md`.
- Extended `src/agents/tools.py` (`make_chart` + adjustments to tool wrappers to take `MerchantContext`).
- Updated `src/dashboard/placeholders.py::dispatch` — looks up the right specialist, falls back to hardcoded handler if not yet built.
- New `tests/test_agents_phase2.py` — at least one cross-viewer correctness test + one merchant-name leak test.

**Validation.**
- The 4 Pricing-button suggested questions return LLM-generated responses against real data.
- A KRG viewer asking "dairy vs peers" gets a different response than an ACM viewer asking the same — peer mappings differ correctly.
- No real merchant names appear in any response.

**Dependencies.** None.

### Phase 2B — Anomaly Detection + Demand Forecasting (~4 hours)

**Goal.** Two more specialists, including the flagship slow-mover scenario and the planted-anomaly handlers.

**Files.**
- New `src/agents/anomaly.py`, `src/agents/demand.py`.
- New prompts: `src/agents/prompts/anomaly.md`, `src/agents/prompts/demand.md`.
- Extended `src/agents/tools.py` — `window_baseline`, `wow_delta` helpers.
- More tests in `tests/test_agents_phase2.py` covering all three planted anomalies + the slow-mover scenario.

**Validation.**
- "Why are my University City stores declining?" returns a stage-by-stage breakdown matching the report's anomaly_series numbers.
- "Slow-mover ice cream — what should I do?" returns: declining SKUs + lapsed cohort + projected lift, with caveats.
- "Show campaign attribution for promo X" works against an actual promo in `tenant_promotions`.

**Dependencies.** Phase 2A.

### Phase 2C — Trade Area Intelligence + Orchestrator routing (~3 hours)

**Goal.** Fourth specialist + the orchestrator that routes free-form input. Multi-specialist synthesis lands.

**Files.**
- New `src/agents/trade.py` + `src/agents/prompts/trade.md`.
- Refactored `src/agents/advisor.py` → `src/agents/orchestrator.py`.
- New `src/agents/prompts/orchestrator_router.md`, `src/agents/prompts/orchestrator_synthesis.md`.
- Extended `src/agents/tools.py` — `peer_store_density` helper.
- Updated `src/dashboard/chat.py` — free-form input now routes through `orchestrator.dispatch_free_form()` instead of the local keyword router (keyword router stays as fallback).

**Validation.**
- "Where should I open a new store?" returns ranked underserved neighborhoods.
- "How does my per-store velocity compare in same neighborhoods?" works for KRG / ACM / WDX.
- Multi-specialist test: "Are my University City stores priced differently and is that hurting me?" routes to pricing + anomaly and synthesizes.

**Dependencies.** Phases 2A + 2B.

### Phase 2D — Polish (~1–2 hours)

**Goal.** Production-quality UX surface.

- **Streaming.** Wire Anthropic streaming through to `st.write_stream` so prose appears token-by-token. Tables and charts render after the prose completes.
- **Loading states.** While a specialist is running, show a per-agent skeleton in the chat panel (similar to Streamlit's native `st.spinner` but inline). Keep the spinner attached to the question being asked, not the global page.
- **Error handling.** Verify the API-key-missing / API-error fallbacks work end-to-end. Manual test with `ANTHROPIC_API_KEY` unset.
- **Demo script.** Short doc / one-pager: 10 minutes, 6 question walkthrough hitting all four specialists + one multi-specialist synthesis. Lives in `docs/DEMO_SCRIPT_AGENTS.md`.

**Dependencies.** Phases 2A + 2B + 2C.

---

## 10. Validation criteria

The whole Phase 2 build is considered done when these all pass:

1. **All 16 suggested-question buttons + 5 representative free-form questions return non-empty, non-error responses for all 5 merchants.** (16 × 5 = 80 button paths + 5 × 5 = 25 free-form paths = 105 invocations.)
2. **Responses cite real data, never hallucinated.** Spot-checked by comparing prose-quoted numbers against direct SQL queries.
3. **Peer references always use `peer_a` / `peer_b` / `peer_c` / `peer_d`.** Audited via regex scan on every response in the test matrix.
4. **Merchant-context isolation enforced at the tool layer.** Tested: ask the same question for two viewers, confirm the answers are merchant-specific and the SQL execution traces show the correct viewing_merchant_id at the runner.
5. **Responses match or exceed Phase 1 placeholder quality.** Side-by-side check on the 4 sample placeholder responses from `DASHBOARD_PLAN.md` (Pricing dairy vs peers / Anomaly UC decline / Demand dairy slowing / Trade new store). Phase 2 responses should be at least as informative.
6. **Multi-specialist synthesis works.** "Are my University City stores priced differently and is that hurting me?" returns a Pricing + Anomaly synthesis, not just one specialist.
7. **Mock-mode fallback.** With `ANTHROPIC_API_KEY` unset, the dashboard still answers all 16 suggested questions (via Phase 1 hardcoded handlers). Free-form input shows "mock mode" message.
8. **Cost ceiling.** A 60-minute demo session stays under $2 in LLM costs at projected rates.

---

## 11. Open questions

Resolve before Phase 2A starts.

1. **Model selection.** Default Sonnet for specialists, Haiku for the router. Acceptable, or do you want Opus for one or two of the specialists (e.g. Demand Forecasting for the slow-mover scenario where reasoning quality matters most)? Opus is ~5× the cost of Sonnet but produces better synthesis on complex prompts.

2. **Streaming or no streaming for the demo.** Streaming is more impressive in a live walkthrough but adds complexity (the table and chart can't render until all tool calls complete, so the user sees prose stream while tables / charts appear at the end). Non-streaming shows the full response at once, which can feel slower but is more predictable. Recommend: ship Phases 2A–C non-streaming, add streaming in Phase 2D only if the demo timing benefits.

3. **Free-form question caching.** Currently planned as "never cached." If demo replays are common, identical free-form text could be cached for ~10 minutes — but caching the LLM's reasoning across the same text risks looking less "live" if the demo audience asks the same question twice intentionally. Recommend: no caching of free-form; cache only suggested-question buttons.

4. **Synthesis-call timeout.** Multi-specialist responses are slower (~10–15s). Acceptable for the demo? If not, we'd switch to a faster synthesis model or run synthesis as a streaming concatenation rather than a separate LLM call.

5. **Should the Anomaly Detection prompt know about the three planted anomalies by name?** Currently planned: yes, the prompt lists them ("the data contains three planted operational anomalies: ..."). This makes the agent reliably surface them and prevents hallucinated false-positives. Alternative: let the agent discover them organically. Recommend the explicit-listing approach — it's honest about the demo's scope and gives the most consistent walkthroughs. Let me know if you'd prefer organic discovery.

6. **`make_chart` tool format.** Specs above use a structured-dict approach where the agent emits chart parameters and the helper builds the Plotly figure. Alternative: the agent emits raw Plotly JSON (more flexible, more error-prone). Recommend the structured-dict approach — it keeps charts on-brand and prevents the agent from producing visually noisy figures.

7. **Where should the agent observability log land?** Per-session in-dashboard footer is planned ("This session: 24,500 tokens, ~$0.18"). Alternative: write to a JSONL file under `data/agent_telemetry/` for later inspection. Recommend: in-dashboard footer for the demo, JSONL only if you want detailed post-session analysis.

8. **Test coverage for the four-merchant peer-isolation invariant.** Currently planned: a few representative cross-viewer tests in `tests/test_agents_phase2.py`. Want the test suite to exhaustively exercise (5 viewers × 16 questions × peer-name regex)? That's 80 tests; runtime ~10–15 minutes; cost ~$3. Alternative: a representative subset (~12 tests). Recommend the subset.

9. **Should the deferred Consumer Segmentation and Payment Optimization agents still appear in the chat panel UI as a "coming soon" entry?** Currently the chat panel shows 4 agent cards (matches the 4 buildable specialists). Adding two greyed-out cards labeled "Coming in v3" sets honest expectations but adds clutter. Recommend: don't show them — keep the panel focused on what works.

10. **Demo script — should the 10-minute walkthrough live in this repo, or as a slide deck?** A repo-resident `docs/DEMO_SCRIPT_AGENTS.md` is easy to maintain alongside the code; a slide deck looks more polished for stakeholder presentation. Recommend: ship the markdown in the repo; let downstream teams convert to slides if they want.

---

## Reused infrastructure (no new code needed)

| What | Where | Reuse note |
|---|---|---|
| `query_tenant` SQL guard (merchant_id predicate enforcement) | `src/agents/tools.py::has_merchant_predicate` | Already correct; bind into `MerchantContext.query_tenant`. |
| `query_lake` view-builder wrapping (CTE shadowing, peer pseudonymization) | `src/agents/tools.py::query_lake` + `src/lake/views.py` | Already correct; bind into `MerchantContext.query_lake`. |
| Peer mapping (the canonical 5×4 viewer→peer table) | `src/lake/peer_mapping.py::build_peer_mapping` | Used by `MerchantContext` for audit checks; the LLM never sees this. |
| Anomaly SQL patterns | `scripts/generate_report_data.py::_anomaly_series` (lines 1086–1183) | Copied or imported by Anomaly + Demand specialists. Phase 2 cleanup: lift into `src/lake/anomaly_queries.py` (flagged in `DASHBOARD_PLAN.md` as a follow-up). |
| Placeholder handlers (hardcoded fallback) | `src/dashboard/placeholders.py` | Stays around as mock-mode safety net + as the floor for response-quality comparison. |
| Plotly figure rendering | Existing `views.py` chart patterns; `st.plotly_chart` | Specialist `make_chart` output flows through the same rendering path. |
| MerchantAdvisor agent loop | `src/agents/advisor.py::_run_loop` | The base `Specialist` class lifts this loop verbatim — the LLM-call pattern is identical, only the prompt and tool list change per specialist. |
