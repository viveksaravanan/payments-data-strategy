# Specialist Agents — Demo Script

A 5-minute walk-through of the four LLM-backed specialist agents plus the conversational advisor (orchestrator) in the merchant dashboard. Optimized for an executive audience who has seen the dashboard once before.

**Setup:** `make demo` (seeds + launches the dashboard on `:8501`). Confirm the merchant selector defaults to **Kroger**.

---

## 5-minute flow

| Time | Step | What to click | What it shows |
|---:|---|---|---|
| 0:00 | Open the dashboard | — | Land on Kroger view. KPI row, map, charts populate from real synthetic data. |
| 0:30 | **Pricing — opener** | Pricing & Benchmarking → *"How am I priced on dairy vs peers?"* | A specialist running real SQL on Kroger's own tenant data + the peer-pseudonymized lake. ~10–20s. |
| 1:00 | **Cross-merchant isolation moment** | Switch merchant → Acme → same Pricing button | Same question, different viewer. Peer mapping inverts: what was `peer_a` for Kroger ($7.34) becomes Acme's own price; Kroger now appears as `peer_a` from Acme's view. |
| 1:45 | **Trade Area — the spatial read** | Trade Area Intelligence → *"Which neighborhoods are underserved by my chain?"* | Concord + Huntersville flagged. Shows the lake's neighborhood-density view used for siting reads. |
| 2:30 | **Demand — flagship slow-mover scenario** | Free-form input → type *"Slowing ice cream — what should I do?"* | The orchestrator routes to Demand Forecasting via LLM. Returns the slow-movers (Vanilla, Mint Chocolate Chip), the accelerating premium flavors, and a recommendation. ~12s. |
| 3:30 | **Anomaly — broad scan** | Anomaly Detection → *"Anything unusual recently?"* | Surfaces a traffic-decline pattern. Cites the May time window and explains the operational driver (UNC Charlotte semester end). No fraud claims. |
| 4:30 | **Observability moment** | Point at the *Session telemetry* footer in the right column | Total LLM calls, total in/out tokens, total cost. Hover any chat-entry timestamp for per-question telemetry. Numbers are real — there is no mock layer. |

The "click sequence" is intentionally:  Pricing → Pricing (other merchant) → Trade → **Demand (flagship)** → Anomaly → telemetry. Demand goes second-to-last on purpose: it's the most narratively rich response (slow-movers + recommendations + cohort), and it lands cleanly because we route to it through the orchestrator (LLM router → demand specialist) which proves the routing layer works.

---

## Curated question per specialist (the one to use if time is tight)

| Specialist | Question | Why this one |
|---|---|---|
| **Pricing & Benchmarking** | *"How am I priced on dairy vs peers?"* (KRG) | Returns a 30-row peer-pivot table + percentage-framed prose. Demonstrates SKU-level peer matching across grocers. |
| **Trade Area Intelligence** | *"Which neighborhoods are underserved by my chain?"* (KRG) | Returns named neighborhoods + peer-store counts. The cleanest spatial-reasoning output. |
| **Demand Forecasting** | *"Slowing ice cream — what should I do?"* (KRG, free-form) | The flagship — proves the orchestrator routes free-form correctly, and Demand returns slow-mover SKUs by name + percentage decline + recommendation. |
| **Anomaly Detection** | *"Anything unusual recently?"* (KRG) | Broad scan that returns the strongest signal (recent traffic dip) with a calibrated operational explanation. Avoid deeply specific historical questions in the demo (see Known Constraints). |
| **Conversational Advisor (orchestrator)** | Use the free-form input box for any of the above. Show the *"Routed to the X Agent (rationale)"* prefix that appears in every response. | Demonstrates the LLM-router layer (Haiku 4.5) that classifies intent and dispatches. ~$0.005 router hop + specialist cost. |

---

## Cross-merchant peer-isolation moment (detailed)

This is the "trust" moment — proves the privacy posture is real, not asserted. Two clicks, ~30 seconds.

1. **Kroger view → Pricing → "How am I priced on dairy vs peers?"**
   - Headline mentions Kroger's average dairy unit price.
   - Per-SKU table shows `Yours / peer_a / peer_b` columns.
   - Babybel example: Kroger **$6.86**, peer_a **$7.34**, peer_b **$6.76**.

2. **Switch merchant dropdown → Acme → same Pricing button.**
   - Headline mentions Acme's average dairy unit price.
   - Babybel example: Acme **$7.34**, peer_a **$6.86**, peer_b **$6.76**.
   - **The same physical number ($7.34) is the viewer's own price for Acme and `peer_a` for Kroger.** Peer pseudonymization is per-viewer; no merchant ever sees its own data labeled as a peer.

Talking point: "The lake is virtual — it's recomputed on every query with the viewing merchant excluded. There is no `lake.csv` anywhere. The pseudonymization is a CTE at query time, scoped to whoever's asking."

---

## "Slowing ice cream" walkthrough (the flagship demand narrative)

This is the scenario that captures *why* a payments company surfaces operational insight at all: not just "what's the data" but "what should the merchant DO."

**Setup:** Kroger view, free-form input.

**Type:** `Slowing ice cream — what should I do?`

**What happens:**

1. Orchestrator (Haiku router, ~$0.0005) reads the question. Classifies intent as `demand` ("Slow-mover identification with promo-cohort and campaign-uplift follow-on is a demand-forecasting question.").
2. Demand specialist receives the verbatim question. It searches by `tenant_products.name LIKE '%ice cream%'`, runs week-over-week qty on the last 7 days vs prior 7 days.
3. Single SQL query, ~12s end-to-end.

**Expected response:**
> **Headline:** Three core ice cream SKUs are decelerating week-over-week: Vanilla (−14.9%), Mint Chocolate Chip (−12.7%), and Strawberry (−5.2%) are all losing velocity heading into the weekend.
>
> - **Vanilla ice cream (1.5 qt)** dropped 10 units WoW (67 → 57), a 15% decline — your single largest ice cream mover, now slowing hardest.
> - **Mint chocolate chip (1.5 qt)** lost 10 units (79 → 69), down 13% — second highest volume, trending down.
> - **Positive note**: Cookies & cream, Rocky road, and Neapolitan are accelerating (+14–20%), suggesting consumers may be trading up to premium/variety flavors.
>
> **Recommendation:** Consider a targeted email or digital promotion on Vanilla and Mint Chocolate Chip — pair them with the accelerating premium flavors as a bundled deal, or use a modest discount to recover lost velocity before the weekend demand window closes.

Talking points (in order of weight):
- "The agent didn't need to be told that ice cream is in `subcategory='ice_cream'` under `category='FROZEN'`. It searched by product name. This generalizes — try 'slowing avocados' or 'slowing yogurt' and the pattern works."
- "The data window is sealed (Mar 1 – May 29, 2026) and the agent uses those literal dates, not `now()`. The story is reproducible."
- "The recommendation isn't a list of every possible action — it's two SKUs and a tactic. Calibrated to what an operator can do this week."

---

## Known constraints (transparency — say these out loud during the demo)

1. **Deeply specific historical anomaly questions may not converge.** The Anomaly Detection Agent uses an "investigate the strongest signal in the data" approach for broad questions. If you ask something like *"Why did avocado spike at Plaza Midwood on April 22?"* — which requires 4+ chained queries to drill into a single SKU at a single store on a single date — the agent may hit its 6-turn budget without writing a final answer. **For the demo, prefer broad anomaly questions** ("anything unusual recently?", "is this happening to peers too?"). Drill-downs are queued as a post-demo improvement.

2. **TJX has no same-segment peers in the panel — so peer-comparison questions return own-merchant-only analysis.** TJ Maxx is the only off-price-retail merchant in the demo. Click any Pricing question on TJX and the agent responds with: *"No segment peers available for this response."* followed by an own-merchant pricing landscape. This is **correct behavior** — it would be a privacy violation to expose grocery-segment peers as a fake comparison. Mention this if a viewer asks "what happens if there's no peer set."

3. **The session telemetry footer is real, not stubbed.** The numbers in the right-column footer aggregate every LLM call made in the current session, including the orchestrator's router hop. Per-question cost is on hover over each chat entry's timestamp.

4. **Cache: clicking the same suggested-question twice returns instantly.** The second click on any button is <50ms with no LLM call (`st.session_state["llm_cache"]` keyed on `(agent_id, question_id, merchant_id)`). The cost shown on hover reflects the original LLM cost; subsequent clicks are free. Free-form input is never cached — the user might be asking a contextually different question with similar wording.

---

## Observability moment

Right column, scroll to the bottom of the chat panel after running 3–4 questions:

```
SESSION TELEMETRY
8 LLM calls · 47,213 in · 3,884 out · ~$0.0666
```

- "8 LLM calls" includes the orchestrator router hops for free-form input.
- Tokens are real Anthropic billing units.
- Cost is approximate (rounded to 4 decimal places); production should use Anthropic's billing API.

Per-question cost: hover the timestamp on any chat entry. Shows `~$0.0142 · 4,332 in / 281 out tokens · 3 turn(s)`.

---

## Model + cost summary (talking points)

- **Specialist model:** Claude Haiku 4.5 (`claude-haiku-4-5-20251001`). 3× cheaper, 2–3× faster per turn than Sonnet 4.6.
- **Router model:** also Haiku 4.5. ~$0.0005 per routing decision.
- **Per-question cost:** averaged ~$0.025–0.035 in validation; well under the $0.06 target.
- **Per-question latency:** averaged ~12–20s; under the 20s target for live demo.
- **Session-level cost for a 5-minute demo with 5–7 questions:** ~$0.15–0.25.

This is the right operating point for an embedded BI surface — cheap enough to put behind every merchant tile, fast enough that the human stays in the loop.

---

## What's deferred (post-demo roadmap)

- **Consumer Segmentation specialist.** v2.5 lake drops `customer_id` by design (no cross-merchant consumer linkage). Deferred to v3 when demographic / loyalty enrichment lands.
- **Payment Optimization specialist.** The v2.5 schema lacks the auth-rate / decline-code / cost-per-rail fields needed for meaningful payment recommendations. Schema extension is a v2.6 item.
- **Deeply-specific anomaly drilldown convergence.** Anomaly agent's early-stop rule trades thoroughness for convergence on broad questions. A "drilldown mode" with extended turn budget would land here.
- **Programmatic no-peer detection via response metadata.** Today, the no-peer case is detected by a literal phrase in the prose. A structured `no_peer: true` flag in the response would let the dashboard render it as a styled banner.
