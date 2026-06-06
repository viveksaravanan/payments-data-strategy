# SPEC — Report HTML Rebuild (v4 Strategy Report)

**Status:** Draft for build
**Branch target:** `v4` (NO PR, NO merge to main)
**Deliverable:** a single self-contained static HTML report (the public-facing strategy document, e.g. `docs/index.html` / the GitHub-Pages report), rebuilt to describe the **v4 system as it actually is**, reorganized into **three sections**, **simpler** than the current report, with **1–3 charts per section maximum** (no sprawl).

> **Build instruction to Claude Code:** This spec carries the *structure, editorial intent, and design rationale* (the "why," much of which lives only in chat history, not in code). You MUST read the actual v4 source to fill in *precise numbers, parameters, and wiring* — do not invent figures, and do not copy them from the old v2.0 strategy doc, which describes a superseded architecture (see §0). Where this spec and the old report/strategy-doc disagree, the **v4 code is the source of truth**; this spec tells you what to emphasize and how to frame it.

---

## 0. Critical context — what changed, and why the old report is WRONG for v4

The existing report and the project's `Core Data Strategy` doc (v2.0, Feb 2026) describe an **earlier architecture** that has since been replaced. **Do not summarize the old doc.** The report must describe the current v4 system. Key differences the report must get right:

| Topic | OLD (superseded — do NOT describe) | v4 (current — describe THIS) |
|---|---|---|
| Lake form | Pre-aggregated tables at fixed grains; **unitless indices** (price_index ~1.0) | **Line-item lake** — raw peer purchase lines, queried with SQL, **real dollars** |
| Agent lake access | `read_lake_table` (filter-only, closed dimensional vocabulary) | `query_lake_sql` — agent writes SQL, same motion as tenant queries |
| Privacy floor | k≥50 build-time + differential-privacy noise | **k=5 query-time cell suppression, no DP noise** (deliberately minimal demo posture) |
| Geography | Derived k-means "Z-codes" (Z01–Z08) | **Real neighborhood names** (Z-codes removed) |
| Cross-merchant comparison | Merge machinery (build_merge, dual-frame) | Agent runs two queries (own + peer) and compares — no merge layer |
| Identity | (various) | `peer_relationship` label: `peer` (same segment) / `merchant` (cross-segment); never a name |

**Why the change (carry this into the report's framing where relevant):** the aggregate/index lake forced the agents to consume fixed summaries they couldn't compose, which produced direction-only pricing ("you're above an index") instead of actionable dollar comparisons. The line-item + SQL rebuild lets agents compose real comparisons in real units. The privacy posture is *deliberately* minimal for the demonstrator (synthetic data, no real merchant to re-identify); the stronger production posture (k≥50, DP-noised aggregates) is noted as the production target, not what the demo runs.

**Tell Claude Code to read these to ground the numbers:**
- `src/generate/` (config + parameters) — for §1 data-generation assumptions, merchant list, volumes, date range, category taxonomy, planted patterns.
- `src/lake/build_line_items.py`, `src/lake/scope.py`, `observable_guard.py`, the k-floor constant (`LAKE_K_FLOOR`) — for §2 anonymization/lake/dual-path.
- `src/agents/` (orchestrator, specialists, `query_lake_sql`, the response contract / validator / CellLookup / ValueRef, `claims.py`) — for §3 agents + response structure.
- `docs/DECISIONS.md` — for the decision rationale behind each.

---

## 1. Editorial principles (apply to the whole report)

- **Simpler than the current report.** Three sections, clean narrative, no exhaustive tables of every component. Prefer a few clear paragraphs + one strong diagram/chart over dense enumeration.
- **Charts: 1–3 per section, hard maximum.** Each chart must earn its place — illustrate a concept the prose can't convey as well. No decorative charts, no sprawl. If a section's idea is fully carried by prose + one diagram, do not add a second chart.
- **Self-contained static HTML.** Inline CSS, inline/embedded chart assets (Plotly self-contained or static SVG/PNG), no external runtime dependencies beyond CDN for the chart lib if needed. Must render opened directly in a browser and deploy as a static GitHub-Pages file.
- **Consistent visual identity** with the dashboard: palette `#0F4C81` accent / `#F7F8FA` surface / `#1A1F2E` text; system fonts; restrained, professional, "strategy document" tone.
- **Accurate over impressive.** Every number, parameter, and claim must trace to the v4 code. If a figure can't be verified in the code, omit it rather than guess. No leftover v2.0 figures.
- **Honest about the demo posture.** Where privacy is discussed, state plainly that the demonstrator runs a minimal posture on synthetic data and name the production target — this is a credibility feature, not a weakness to hide.

---

## 2. Structure — exactly three sections

### Section 1 — Data Generation: Assumptions, Simulation, and Realism

**Purpose:** explain that the data is synthetic, what assumptions shaped it, how it's simulated, and *what makes the simulation realistic enough to be credible.*

**Content (pull specifics from `src/generate/`):**
- **REQUIRED: read the real v4 numbers from code — do NOT use any figure from chat history or the old v2.0 doc.** The historical demo ran ~236K transactions / 90 days / a small card base; **v4 is deliberately much larger** and those old numbers are STALE. Extract the current values from `src/generate/` config + parameters and state them in the report:
  - **Customer / card base:** number of distinct customers (cards). (The cross-merchant join key is a tokenized customer id; report the count, not the mechanism.)
  - **Stores:** number of stores total and per merchant (and how they map to neighborhoods).
  - **Transactions and line items:** total transaction count and total line-item count (these differ — a transaction has multiple lines; the report should not conflate them, and §2/§3 numbers depend on the distinction).
  - **Date range / duration:** start and end dates, number of days/weeks.
  - **Category taxonomy:** number of categories and subcategories (and a few example SKUs/names if useful for color).
  - **Per-merchant / per-segment volumes:** rough share of volume by merchant and by segment, enough to show the three segments are sized plausibly.

### 1a. Generation decisions that make the simulation realistic (the credibility argument)

This is the heart of Section 1 and the user's explicit ask: enumerate the *decisions taken during data generation* that make the synthetic data realistic and defensible. Pull the real values from `src/generate/`; the items below are the decisions to COVER (verify each against code — any specific number shown here is illustrative/historical and may be stale):

- **Scale chosen for realism, not convenience.** State the real v4 customer/card count, store count, transaction + line-item counts, and time span — and frame *why* that scale matters: a larger base makes aggregate patterns stable and peer comparisons statistically meaningful (the small early dataset couldn't support that). This directly answers "is this realistic."
- **Five real-brand merchants across three distinct segments** (`grocery`: Kroger/Acme/Winn-Dixie; `qsr`: Taco Bell; `off_price`: TJ Maxx) in one metro (Charlotte) — chosen so segments exhibit genuinely different purchase behavior (basket size, frequency, category mix), which is what makes cross-segment vs same-segment comparison meaningful.
- **Per-merchant parameter biases.** Merchants within a segment are generated with *different* pricing/volume/mix biases so they are not clones — this is what makes "your dairy $3.50 vs peer $3.42" a real, non-trivial comparison rather than everyone landing identical. (This is the generation-side guarantee behind the Wave 3.5 homogeneity gate.)
- **Segment-appropriate behavior:** grocery = frequent, larger mixed baskets; QSR = high-frequency, small low-complexity tickets; off-price = lower frequency, variable basket — generated to match how these businesses actually behave.
- **Realistic payment mix:** distribution across credit/debit/EBT/cash, card networks, entry modes, and mobile-wallet adoption, set to plausible real-world shares (report the actual mix parameters).
- **Geographic plausibility:** stores placed in real Charlotte neighborhoods with coordinates; customers associated with stores in a geographically sensible way.
- **Temporal realism:** day-of-week and time-of-day patterns, any seasonality/weekly cycles, and the **deterministic seed** (reproducible generation — same data every run, which is also why tokenization salt is seed-derived).
- **Planted, discoverable patterns:** deliberate signals the agents are meant to find — most notably the **University City traffic decline** anomaly — so the analytics have something real to surface rather than uniform noise. Note these are intentional and where they live.
- **Privacy-respecting generation:** consumer-level linkage is generated but never published to the peer lake; the `observable_guard` enforces that planted profile columns (e.g. affluence/loyalty attributes) are not readable into the lake build. (Detail belongs in §2; mention here only that generation and privacy are designed together.)

- The synthetic premise: 5 merchants across 3 segments — `grocery` (Kroger/KRG, Acme/ACM, Winn-Dixie/WDX), `qsr` (Taco Bell/TBL), `off_price` (TJ Maxx/TJX) — in a single metro (Charlotte). State the real transaction/line/customer volume and date range from the config (the new, larger volume — read it, don't assume).
- **Assumptions taken** (enumerate the real ones from the generator): category/subcategory taxonomy, basket composition, purchase frequency patterns by segment, payment-method mix, store/neighborhood geography, customer base.
- **How it's simulated:** the generation approach at a high level (deterministic seed for reproducibility; per-merchant parameter biases that make merchants genuinely differ; planted patterns — e.g. the University City decline anomaly — that give the agents something real to find).
- **What makes it realistic** (the credibility argument): segment-appropriate behavior (grocery basket vs QSR vs off-price differ in the right ways), realistic payment mix, geographic plausibility, and the per-merchant variation that makes cross-merchant comparison meaningful rather than flat. This is the section that answers "why should I believe this models reality."

**Charts (1–3, choose the strongest 1–2):**
- A category/segment composition view (e.g. basket or revenue mix by segment) showing the three segments behave distinctly — proves the realism claim visually.
- Optionally a time-series showing a planted pattern (e.g. the volume trend with the UC decline visible) — illustrates that the data has real structure to discover.
- Do NOT add a third unless it carries a distinct idea.

### Section 2 — Anonymization & the Lake: Privacy and the Dual Path

**Purpose:** explain how peer data is made safe to share, and show the dual-path isolation (own data vs anonymized peer lake).

**Content (pull specifics from `src/lake/`):**
- **The dual path** — the core concept: each merchant sees its *own* data in full detail (tenant path), and a *separate* peer lake that contains other merchants' data, anonymized. The two never co-mingle; peer data reaches the agent only through the anonymized lake. THIS IS THE KEY DIAGRAM of the section.
- **How the lake is built (v4):** line-item peer data, per-viewer materialized (each merchant gets its own lake copy with itself excluded and peers labeled relative to it), tokenized IDs, generalized geography (ZIP3, real neighborhood retained), hour-bucketed timestamps, consumer linkage dropped entirely.
- **The privacy mechanisms (v4, be precise and honest):**
  - **Identity hidden:** `peer_relationship` label (`peer` = same segment, `merchant` = cross-segment), never a merchant name or pseudonym.
  - **k=5 cell suppression at query time:** any result group backed by fewer than 5 underlying line records is dropped; the agent is told coverage was thin.
  - **Aggregating-only access:** the agent can only retrieve aggregates (AVG/SUM/COUNT/GROUP BY), never raw individual lines — enforced on the parsed SQL.
  - **Viewer exclusion:** a merchant's own rows are structurally absent from its peer lake.
- **The honest posture note:** this is a deliberately minimal demonstrator posture on synthetic data; the production target adds k≥50 and differential-privacy-noised aggregates. State it plainly.

**Charts (1–3, the dual-path diagram is mandatory; ≤1 more):**
- **Dual-path diagram** (required): own/tenant data → full detail to the viewer; peer transactions → anonymization (tokenize / generalize / drop-linkage / k=5) → peer lake → agent. Show the wall between the two paths. This can be a clean SVG schematic rather than a data chart.
- Optionally one small illustration of k=5 suppression or the peer/cross-segment labeling — only if it adds clarity. Keep it to one.

### Section 3 — AI Agents: Orchestration, Specialists, and Validated Responses

**Purpose:** explain how a question becomes a grounded answer — routing, what each specialist does, and how every number is validated before the user sees it.

**Content (pull specifics from `src/agents/`):**
- **Orchestrator + router:** a Haiku router classifies a free-form question to one of `pricing | demand | trade | anomaly | advisor`. **Two dispatch paths:** a clicked suggested-question pill goes *directly* to its mapped specialist (no router call); a free-form question goes through the router; anything that doesn't fit a specialist falls to the **conversational Advisor**. State this clearly — it's the routing model.
- **What each specialist is good at** (one crisp line each):
  - **Pricing** — how your prices compare to peers, category by category, in real dollars; pricing-leverage opportunities.
  - **Demand** — category/basket performance, demand patterns, over/under-performance vs peers.
  - **Trade-area** — neighborhood/geographic performance and expansion opportunity.
  - **Anomaly** — unusual spikes/drops, store-level traffic anomalies (incl. the planted UC decline), with peer baselines.
  - **Conversational Advisor** — free-form natural-language questions; orchestrates/answers when no single specialist fits.
- **Peer availability by segment** (carry the v4 rule): grocers have 2 same-segment peers; TBL/TJX have none, so pricing-type peer comparison correctly declines for them while trend/trade-area can fall to a clearly-labeled cross-segment view.
- **The response structure & validation (the trust story — emphasize this):** every agent answer is a structured response where **each number stated in the prose is validated against the actual query result before the user sees it.** Describe the grounding guarantee in plain terms: the agent reasons over real query results (own via `query_tenant`, peer via `query_lake_sql`); claimed figures are checked (CellLookup/ValueRef) to trace to a real cell within tolerance; numbers that don't trace are stripped, not shown. The model cannot surface a fabricated figure. This is the credibility core of the whole system — give it room.
  - Note (current state): agent responses are **prose + grounded numbers + result table**; charts inside agent answers are a planned fast-follow (not in the current build). Don't depict agent-generated charts as live if they aren't.

**Charts (1–3):**
- A **routing/flow diagram** (recommended, likely the strongest): question → (pill: direct to specialist | free-form: router) → specialist/advisor → query own + query peer → validate claims → grounded answer. One clean schematic that captures orchestration + the validation gate.
- Optionally one illustration of the validation step (e.g. a claim being checked against a result cell → pass/stripped) to make "every number is grounded" concrete. ≤1 more beyond the flow diagram.

---

## 3. What to remove / simplify from the current report

- Remove anything describing the **old architecture** (indices, `read_lake_table`, Z-codes, DP-at-build, merge machinery, k≥50 as the live floor) unless explicitly framed as "production target" in the §2 honest-posture note.
- Collapse exhaustive component tables into narrative + one diagram per concept.
- Remove decorative or redundant charts — enforce the 1–3-per-section ceiling.
- Remove any hardware/terminal/edge-device stack detail from the old strategy doc that isn't part of what this demo actually implements — the report describes the demonstrated v4 system only. No "production vision" coda (see §6).

---

## 4. Build stages

**Stage 0 — Read v4 + inventory (no HTML yet).** Read `src/generate/`, `src/lake/`, `src/agents/`, `docs/DECISIONS.md`. Produce a short fact-sheet of the real numbers/parameters/wiring each section needs (volumes, date range, merchant/segment list, k-floor value, routing set, specialist list, validation mechanism). Confirm against this spec; flag any place the code contradicts the spec (code wins). This prevents stale-figure leakage from the old doc.

**Stage 1 — Section drafts (HTML structure + prose).** Build the three-section HTML skeleton with the verified prose. No charts yet. Confirm narrative is simpler than the old report and accurate to v4.

**Stage 2 — Charts (1–3 per section).** Add the diagrams/charts: §1 segment/realism + optional planted-pattern; §2 dual-path diagram (required) + optional k=5/labeling; §3 routing+validation flow + optional validation illustration. Enforce the ceiling. Diagrams can be clean inline SVG; data charts from real generated data where shown.

**Stage 3 — Polish + self-containment.** Inline CSS, consistent palette/typography, verify it renders standalone and deploys as a static file. Final accuracy pass: every number traces to code; no v2.0 leftovers; honest-posture note present.

---

## 5. Verification gate (what "done" means)

- Exactly three sections, in order: Data Generation / Anonymization & Lake / AI Agents.
- Each section has **1–3 charts, no more**; §2 includes the dual-path diagram; §3 includes the routing+validation flow.
- Every figure traces to v4 code (Stage 0 fact-sheet); **zero** superseded v2.0 figures (no indices, Z-codes, k≥50-as-live, DP-as-live, read_lake_table, merge machinery presented as current).
- Privacy section states the honest minimal-demo posture + production target.
- Agent section makes the **grounding/validation guarantee** clear and prominent.
- Simpler than the current report (narrative + targeted diagrams, not exhaustive tables).
- Self-contained static HTML; renders standalone; deploys as a static page.
- Visual identity matches the dashboard palette/typography.
- Commit to v4, push. NO PR, NO merge to main.

---

## 6. Settled choices

- **No production-vision coda.** Do NOT add a closing section gesturing at the fuller production platform (terminals, streaming, k≥50, DP). The report describes the demonstrated v4 system only. This keeps it focused and avoids re-introducing the old-architecture confusion §0 exists to prevent.
- **Schematics over forced data charts.** The strongest visuals in §2 and §3 are *diagrams* (the dual-path wall, the routing+validation flow), not data plots. Use them. Do NOT force a data chart into a section where a schematic communicates the architecture better. Data charts belong in §1 (the realism argument); §2 and §3 are diagram-led. A section may legitimately have only one strong schematic and no second chart — that satisfies the 1–3 ceiling; do not pad to reach a count.