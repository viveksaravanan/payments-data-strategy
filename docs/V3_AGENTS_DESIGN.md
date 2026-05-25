# V3_AGENTS_DESIGN.md — Phase 5 spec

**Status:** locked, ready for implementation.
**Author:** [you]
**Date:** 2026-05-25 (revised; original locked 2026-05-24)
**Phase 4.6 baseline commit:** c75642f
**Phase 5.0 cassette baseline commit:** 5c9f899
**Phase 5 target:** prompt + example redesign across all 4 specialists + orchestrator. Plus regression cassette infrastructure (already shipped in 5.0).

**Revision note:** the original locked version proposed tool_use structured output (hybrid contract). After review, tool_use was dropped — it broke streaming UX and over-engineered the structural enforcement. The actual Phase 5 quality lever is the worked examples and sharpened prompts. Contract is enforced by prompt instructions + examples + regression-test checks, not by API schema.

---

## 1. Goals + non-goals

### Goals

1. **Sharpen the chat experience.** Make every specialist response follow a consistent, readable, actionable shape. Demo-defining quality.
2. **Ground prose in chart shape.** Specialists know which chart pattern will render below their prose and frame evidence accordingly.
3. **Segment-aware routing.** Orchestrator picks the right specialist with viewer-segment context, not segment-blind heuristics.
4. **Build a regression test surface.** Cassette-based replay so prompt changes can be validated against baseline responses. (Shipped in Phase 5.0.)
5. **Preserve all working architecture.** Filter wiring, caching, chat panel UX, dashboard layout, streaming response — all stay as-is.

### Non-goals

- No model changes (stay on Haiku 4.5 for v3; revisit in v3.1).
- No automatic filter injection into agent SQL. Agents see full panel context. Honor user's explicit date range mentions only.
- No dedicated UI slot for routing decisions. Inline prose-prepend stays for v3.
- No changes to chart rendering or dashboard layout.
- No expansion of specialist count or scope. 4 specialists, same domains.
- **No tool_use structured output.** Contract is enforced by prompts, examples, and regression-test checks. Streaming UX preserved as-is. (See revision note above.)
- **Prompt caching deferred to Phase 5.7 follow-up commit** (see §12). Keeps quality work and perf work cleanly separated.

---

## 2. Response shape contract

Every specialist response follows this 4-part shape, enforced by the prompt + few-shot examples + regression-test checks:

HEADLINE → EVIDENCE → THEREFORE → CAVEATS

The user sees this rendered as flowing prose. Streaming token-by-token, just like today.

### Headline (1 sentence)

- Lead with the most important finding for THIS question
- Names a specific number from the data
- Frames the comparison: own vs peers, recent vs baseline, this category vs that
- Never starts with "Looking at your data..." or other throat-clearing
- Sentence case, no bold mid-sentence

**Good:** "Your dairy prices sit 4.2% above peer_a but 2.1% below peer_b — mixed positioning across the segment."

**Bad:** "Looking at your data, I can see that dairy pricing has some interesting patterns when compared to your peers." (no number, no specificity, throat-clearing)

### Evidence (3-5 bullet points)

- Each bullet cites an actual number from the agent's tool calls (not memory)
- Numbers reference the specific table / category / window the headline refers to
- Bullets order by importance to the headline (strongest support first)
- Bullets stay under 25 words each
- No "as you can see" or "interestingly" — bullets are facts, not commentary

**Good:**
- Whole milk: $4.89 (you) vs $4.63 (peer_a, -5.6%) vs $5.02 (peer_b, +2.6%)
- Eggs: $5.49 (you) vs $5.21 (peer_a, -5.4%) vs $5.67 (peer_b, +3.2%)
- Butter: $7.99 (you) vs $7.43 (peer_a, -7.5%) vs $8.15 (peer_b, +1.9%)

**Bad:**
- Dairy pricing shows some variation across the category
- Your prices are interesting in this segment
- Peer comparison reveals a mixed pattern

### Therefore (1 sentence, at most 2)

Rendered in prose as a final paragraph, optionally led with `**Therefore:**` as a lightweight visual marker.

**Recommended openers (pick one when it fits the response naturally):**
- "Worth investigating..."
- "The dominant lever is..."
- "Largest opportunity sits in..."
- "Most actionable next look:..."
- "Watch for..."

**Required content:**
- References a specific entity (category, store, neighborhood, SKU) named in the Evidence section
- Names what to investigate next, not what to do

**Forbidden:**
- Verbs: "should", "recommend", "consider", "try", "implement", "deploy", "roll out"
- Generic framings: "you might want to look at", "it could be worth thinking about"
- Multiple recommendations stacked ("worth investigating X, Y, and Z")

**Good:** "The dominant lever is Traffic/store at -5.1pp — worth investigating whether KRG's UC stores are below peer foot-traffic baselines."

**Bad:** "You should consider raising prices on dairy and you might want to look at restocking eggs." (uses "should" and "you might"; stacks two recommendations)

### Caveats (0-3 bullet points, fenced JSON list at end)

- Surfaces data quality issues, sample size limits, window boundaries
- Each caveat ≤ 20 words
- Fenced as ```caveats ["...", "..."]``` at very end of prose
- Caveats parsed via existing `_split_caveats` regex; this contract preserves that
- Caveats should NOT be filler that restates the response shape (e.g., "All comparisons are average unit price..." adds nothing)

**Good:**
- "Based on the 90-day window (Mar 1 – May 29, 2026)."
- "Whole milk SKU mapping confidence: 89% based on canonical_product match."

**Bad:**
- "All comparisons are average unit price per line item across the full transaction window in the panel." (restates the response shape; not a caveat)

---

## 3. Per-pattern response rules

Each suggested-question qid maps to a chart pattern (heatmap, scatter, waterfall, time series, bars, table). The specialist receives the chart pattern as prompt context (per Q1 decision) so prose can ground in chart shape.

### Pattern: Heatmap (e.g., P1, T-A3, R-A3, day×daypart)

- **Headline:** name the strongest cell (highest above peer / lowest below peer / largest deviation)
- **Evidence:** 3-5 cells with their numeric values
- **Therefore:** worth investigating the strongest cell or pattern across cells

**Example (Pricing P1):**
> "Whole milk is your widest peer-gap at +7.3% above peer_a in dairy; produce sits 3.2% below peer_b across the board."

### Pattern: Time series vs peers (e.g., A1 UC decline)

- **Headline:** describe the trajectory (declining / stable / growing) and the peer signal (co-decline / divergent / parallel)
- **Evidence:** 3-5 weeks with own + peer numbers
- **Therefore:** market signal interpretation (market-wide / operational / mixed)

**Example (A1):**
> "Your UC transactions dropped 14% from baseline by week of May 3; peer_a co-declined 11%, peer_b 9% — pattern reads as market-wide UC weakening."

### Pattern: Waterfall (e.g., D7 revenue gap, T-D3 own-vs-baseline)

- **Headline:** name the dominant driver (largest bar) and direction
- **Evidence:** all driver bars with their pp contributions
- **Therefore:** which lever to investigate based on dominant driver

**Example (D7):**
> "vs peer_a, you're 8.4pp behind; Traffic/store contributes -5.1pp (the dominant lever), with Stores -1.8pp and Mix -1.5pp behind."

### Pattern: Scatter with peers / parity line (e.g., P3, D4)

- **Headline:** name the over-/under-performing category (largest distance from parity line)
- **Evidence:** 3-5 categories with own + peer share/price
- **Therefore:** pricing leverage opportunity (above peer + high volume = pricing power; below peer + high volume = growth lever)

### Pattern: Bars own-only (e.g., T-D1, T-P3, R-D1)

- **Headline:** name top 1-3 categories/stores by metric, with the share/value
- **Evidence:** top 5-8 with their values
- **Therefore:** concentration framing (top 3 = X% of revenue)

### Pattern: Table with drilldown (e.g., A2, A3, T-A1, R-A2, R-P2)

- **Headline:** count of flagged rows + dominant direction (n spikes, n drops)
- **Evidence:** top 3 rows by deviation magnitude
- **Therefore:** investigation framing (peer corroboration signal if available)

### Exception handling for missing/unknown patterns

If the specialist receives an unknown `chart_pattern` or no pattern at all (e.g., free-form orchestrated question with no qid):

- Skip pattern-specific framing
- Default to a general "Headline → Evidence → Therefore → Caveats" shape
- Don't emit a chart (specialist still calls `make_chart` only if data warrants it)

This prevents pattern-aware prompts from breaking when used outside the qid-driven dispatch path.

---

## 4. Per-specialist voice

All specialists follow the response contract above. Differences come from domain framing and the data they query.

### Pricing & Benchmarking Agent

- **Voice:** factual, comparison-anchored, peer-relative
- **Frames evidence as:** own number → peer_a delta → peer_b delta
- **Therefore tone:** opportunity framing ("widest gap → worth investigating")
- **Never:** prescribes price changes, MAP enforcement, or competitive-response strategy
- **Domain quirks:** peer_a and peer_b labeled by segment match (grocer→other grocers; TBL/TJX→no same-segment peers)

### Demand Forecasting Agent

- **Voice:** trend-oriented, growth/decline-framed
- **Frames evidence as:** WoW or trajectory % change, SKU-level specifics
- **Therefore tone:** descriptive — names next-most-actionable category or SKU
- **Never:** projects future demand, recommends inventory changes
- **Domain quirks:** time-series-heavy; can name growing vs declining categories side by side

### Anomaly Detection Agent

- **Voice:** investigative, peer-corroboration-aware
- **Frames evidence as:** baseline → recent → deviation %, plus peer signal
- **Therefore tone:** market-wide vs operational vs ambiguous interpretation
- **Never:** mentions fraud, attributes to specific employees, names individuals
- **Domain quirks:** has an in-prompt knowledge base of the 3 planted anomalies; uses peer corroboration column when available

### Trade Area Intelligence Agent

- **Voice:** location-anchored, geo-spatial
- **Frames evidence as:** neighborhood-level own + peer counts
- **Therefore tone:** expansion / underserved framing
- **Never:** recommends specific store openings, comments on real estate
- **Domain quirks:** geo data heavy; uses score-based ranking for expansion opportunities

### No-data response shape (applies to all specialists)

When the requested comparison has no data (e.g., no same-segment peers for TBL pricing question, empty intersection in UC trajectory + non-UC stores filter), the specialist responds with the full contract shape but adapts each section:

- **Headline:** names the ABSENCE. "TBL has no same-segment peers in the panel, so peer-relative pricing comparison isn't available."
- **Evidence:** describes what IS available. "Own pricing across categories: dairy averages $4.23, produce $3.18, etc. Cross-segment peers (grocers) priced significantly higher."
- **Therefore:** points to closest-substitute analysis. "Worth investigating own-pricing trends over time (T-P2) since peer comparison isn't possible here."
- **Caveats:** note missing data explicitly. ["No same-segment peers exist in the panel for TBL.", "Cross-segment comparison may not reflect competitive dynamics."]

The contract shape stays intact. Only the content adapts. This prevents the awkward "your prices vs peer_a... wait, there is no peer_a" failure mode.

---

## 5. Orchestrator routing (segment-conditional)

The orchestrator currently routes via Haiku LLM with keyword fallback. Phase 5 adds segment-conditional logic.

### Routing rules by segment

**Grocer viewers (KRG / ACM / WDX):**
- "How are my prices..." / "category pricing" / "peer comparison" → Pricing
- "Stores running below" / "anomaly" / "spike" / "drop" → Anomaly
- "Category trends" / "growing" / "declining" / "WoW" → Demand
- "Where should I open" / "neighborhood" / "trade area" → Trade
- Ambiguous → Anomaly (grocers' demo arc anchors on decline investigation)

**TBL (QSR with no peers):**
- "Daypart" / "menu" / "ticket band" / "category share" → Demand (TBL's domain)
- "Stores running below" / "anomaly" → Anomaly
- "Store-level performance" / "per-store ticket" → Demand (no peer comparisons available)
- Pricing questions → Pricing (but acknowledge no same-segment peers; uses no-data response shape)
- Ambiguous → Demand (TBL's growth narrative is demand-centric)

**TJX (retail with no peers):**
- "Ticket band" / "price spread" / "high-end" / "low-end" → Pricing
- "Category share" / "growing" / "declining" → Demand
- "Per-store" / "store performance" → Anomaly
- Trade area questions → Trade (but limited; no peer footprint context)
- Ambiguous → Pricing (TJX's growth narrative is pricing-positioning)

### Implementation

Orchestrator prompt expands to include segment context. Routing dataclass gains `viewer_segment` field (optional, defaults to None for backward compatibility).

The router LLM call receives the segment as part of the system prompt. Keyword fallback uses the same `_KEYWORD_RULES` table but with a segment-conditional default at the end (instead of always-demand).

### Routing prose stays inline

Per Q10 decision: routing decisions still prepend to prose as before. Format unchanged:

> *Routed to the **{spec_label}** ({rationale})*.

UX cleanup deferred to v3.1.

---

## 6. Few-shot example structure

Each specialist's system prompt embeds worked examples (20 total across 4 specialists: pricing 5, demand 5, anomaly 5, trade 5 incl. no-data demo).

### Critical sequencing note

Each example must be drafted in detail BEFORE the prompt rewrite in sub-task 4. Drafting an example means writing the actual response shape with real numbers from a recent baseline cassette. The 20 examples ARE the Phase 5 quality lever. Listing topics alone doesn't ship quality — writing the responses does.

**Implementation order:**

1. Sub-task 1 (cassettes) — ✅ shipped in Phase 5.0; produces baseline responses
2. Sub-task 2 (contract instructions added to prompts) — lightweight; Phase 5.1
3. Sub-task 3 (pattern context injection) — Phase 5.2
4. **Pause and draft the 20 examples** — use baseline cassettes as raw material, refine into ideal contract-shape responses. Budget 4-6 hours for this alone, not bundled with prompt edits.
5. Sub-task 4 (prompt rewrites with examples baked in) — Phase 5.3
6. Sub-task 5 (segment-conditional routing) — Phase 5.4
7. Sub-task 6 (regression run + iterate) — Phase 5.5

This means sub-task 4 becomes "fold pre-drafted examples into prompts" rather than "write prompts AND examples simultaneously."

### Example structure per specialist

Each example shows:

1. **Question** — a representative user question
2. **Tool calls** — abbreviated trace of what the specialist queried (1-3 lines)
3. **Response** — the ideal response following the contract (Headline → Evidence → Therefore → Caveats), written as flowing prose
4. **Why this example** — 1 line of meta-explanation visible to the model

The examples per specialist cover the most common question types for that specialist.

### Pricing — 5 examples

1. Heatmap question: "How do my prices compare to peers across categories?" (P1)
2. Scatter question: "Where do I have the most pricing leverage?" (P3)
3. Two-panel question: "How does my staple pricing compare to non-food?" (P2)
4. Ambiguous question: "Are my prices competitive?" (orchestrated)
5. No-peer fallback: TBL or TJX viewer asking about pricing (shows how to handle no same-segment peers using the no-data response shape)

### Demand — 5 examples

1. Category trajectory: "Which categories have grown the most in revenue share over the last 90 days?" (T-D2 / R-D2 — pure trend question, no anomaly bleed)
2. Mix-shift question: "Has my basket mix changed?" (D3 or related)
3. Daypart question: "How are my dayparts performing?" (T-P1)
4. Slowing-product question: "What's slowing this week?" (orchestrated)
5. Growth-driver question: "Which SKUs are driving recent growth?" (orchestrated, no specific qid)

### Anomaly — 5 examples

1. Store-level table: "Which stores are running below baseline?" (A2)
2. Category-level table: "Which categories had spikes or drops?" (A3)
3. Heatmap (day×daypart): "Which day-daypart combos are weakest?" (T-A3, R-A3)
4. Peer corroboration: "Is this anomaly market-wide or operational?" (orchestrated)
5. SKU-level question (TBL): "Which menu items deviated this week?" (T-A2)

### Trade — 5 examples

1. Neighborhood performance map: "Where am I underperforming geographically?" (T1)
2. Customer-home density: "Where do my customers live vs my stores?" (T2)
3. Expansion opportunity: "Where should I consider opening next?" (T4)
4. Cross-segment with peers: "How does my footprint compare to peers in this area?" (T1 grocer with peer overlay)
5. No-data demo: TBL/TJX viewer asking trade-area question, specialist must acknowledge no same-segment peer footprint and pivot to cross-segment or own-baseline framing (explicit demonstration of no-data response shape)

### Why ~5 per specialist (not 9)

- Each specialist handles 3-4 primary chart patterns
- 5 examples cover those patterns + 1-2 buffer for ambiguous orchestrated questions
- More examples = larger prompt = more cost per dispatch
- 9 examples diminishing returns past pattern coverage

---

## 7. Pass/fail criteria

Phase 5 success = "the new responses are at least as good as baseline, and the contract is consistently followed."

### Contract compliance (objective)

Every response must (verified by regression-test regex checks in Phase 5.5):

- Start with a Headline that names a specific number
- Have 3-5 Evidence bullets, each with a number
- Have a Therefore section (1-2 sentences) — detectable by recommended-opener phrases or `**Therefore:**` marker
- End with a Caveats fenced JSON block (0-3 items)
- Not contain throat-clearing ("Looking at your data...", "Interesting question...")
- Not contain forbidden verbs in the Therefore section ("should", "recommend", "consider", etc.)

These are mechanically checkable via regex on the prose. The cassette comparison tests in `tests/test_contract_compliance.py` (added in Phase 5.5) flag violations automatically.

### Quality bar (subjective)

For each cassette comparison:

- **Headline quality:** is the chosen number the most important for the question?
- **Evidence selection:** are the bullets the most relevant numbers?
- **Therefore actionability:** is the investigation framing concrete enough to act on?
- **Caveat completeness:** are real data limitations surfaced?

Grade each as: better / equal / worse vs baseline.

**Pass criterion:** ≥ 8/12 cassettes show "better" responses; remaining ≤ 4 show "equal"; **0 cassettes show "worse"**.

### Who judges what

**Claude Code verifies:**
- Mechanical contract compliance (headline has number, caveats fence present, no forbidden verbs in Therefore, etc.) via regex checks
- All 12 cassettes parse correctly post-changes
- Tests still pass
- No new errors in dispatch path

**YOU verify (budget ~2 hours):**
- Did the response pick the RIGHT number for the Headline?
- Are the Evidence bullets the MOST RELEVANT supporting numbers?
- Is the Therefore concrete enough to actually act on?
- Better / equal / worse grading for each of the 12 cassettes

Phase 5 cannot be auto-graded. The quality judgment is yours. Plan the time before declaring Phase 5 done.

### Hard fails

Any of these = blocking:

- Pattern context injection breaks free-form orchestrated path (no qid)
- Segment-conditional routing routes TBL questions to a peer-comparison response that returns empty
- Streaming visibly breaks (responses no longer appear progressively)
- Token cost increases > 2x baseline
- Caveats fence parsing breaks (existing `_split_caveats` regex starts missing caveats)

---

## 8. Implementation plan

### Sub-task ordering

**Sub-task 1: Cassette infrastructure (foundation)** ✅ Shipped in Phase 5.0 (commit 5c9f899)

- 12 baseline cassettes recorded against Phase 4.6 prompts
- Helper module `tests/cassette_helpers.py` with record / replay / compare
- 3 new tests for infrastructure
- Total cost: $0.5148

**Sub-task 2: Response contract instructions added to specialist prompts (lightweight)**

- Add the §2 contract to each of the 4 specialist prompts. Update the existing "Output format" section with:
  - Headline: 1 sentence, names a number, no throat-clearing
  - Evidence: 3-5 bullets, each with a number
  - Therefore: 1 sentence with recommended openers; forbidden verbs listed
  - Caveats: 0-3 bullets in fenced JSON block (unchanged from today)
- Optionally lead the Therefore paragraph with `**Therefore:**` for visual clarity
- No code changes. Prompt edits only.
- Cassette format unchanged. Streaming unchanged. `_split_caveats` regex unchanged.

**Commit:** `Phase 5.1: response contract instructions added to specialist prompts`

**Sub-task 3: Pattern context injection**

- Build `chart_pattern_for(qid: str) -> str | None` helper (e.g., "P1" → "heatmap")
- Specialist signature gains `chart_pattern: str | None = None`
- `_run_specialist` in agents.py passes pattern through; defaults to None for orchestrated path
- Specialist prompts updated with pattern-conditional sections (6 patterns described per §3)
- Graceful no-op when pattern is None or unknown

**Commit:** `Phase 5.2: chart pattern context injection into specialist prompts`

**Sub-task 4 (preceded by example-drafting pause):**

Before this sub-task: pause and draft the 20 worked examples. Use baseline cassettes from Sub-task 1 as raw material. Refine each into the ideal contract shape per §6. Budget 4-6 hours for example drafting alone.

Then:

- 4 specialist prompts rewritten with:
  - Reinforced response contract section (already added in 5.1)
  - Per-pattern framing rules (already added in 5.2)
  - Pre-drafted worked examples folded in (5 per specialist; 5 for trade including no-data demo)
  - Voice/domain quirks per §4
  - No-data response shape section (applies to all)
- Prompts stay under ~300 lines each

**Commit:** `Phase 5.3: specialist prompt rewrites with pre-drafted examples`

**Sub-task 5: Segment-conditional orchestrator routing**

- Orchestrator prompt expanded with segment-specific routing rules
- Keyword fallback updated with segment-conditional default
- `RoutingDecision` gains optional `viewer_segment` field
- Routing prose unchanged (inline prepend)

**Commit:** `Phase 5.4: segment-conditional orchestrator routing`

**Sub-task 6: Regression run + iterate**

- Re-run all 12 cassettes against new prompts
- Generate comparison files (baseline vs phase5 side-by-side)
- Add `tests/test_contract_compliance.py` with regex-based contract checks (headline-has-number, therefore-section-present, forbidden-verbs-absent, caveats-fence-present)
- User grades each cassette as better / equal / worse
- If pass criteria met (≥8 better, 0 worse), commit cassette comparisons
- If not, iterate on specific specialists' prompts

**Commit:** `Phase 5.5: regression run, baseline comparisons, contract compliance tests`

### Test coverage

- **Existing 215 tests:** all must continue passing (no regressions)
- **New tests:**
  - Pattern context injection tests (~2 tests: with pattern, without)
  - Routing tests (~4 tests: one per segment)
  - Contract compliance tests (~6 tests: one per contract rule)
- **Target: ~227 passing**

### Commit boundaries

Five focused commits (Phase 5.0 already shipped). Each commit independently testable. No mega-commits.

### Estimated time

- Sub-task 1 (cassettes): ✅ done
- Sub-task 2 (contract instructions): 1-2 hours
- Sub-task 3 (pattern injection): 1-2 hours
- **Example drafting pause: 4-6 hours**
- Sub-task 4 (prompt rewrites with examples): 2-3 hours
- Sub-task 5 (routing): 1-2 hours
- Sub-task 6 (regression + iterate): 2-4 hours (including ~2 hours of your manual quality review)

**Total remaining: ~11-19 hours of focused work.** Spread across 2-3 working days.

---

## 9. Risk register

| Risk | Likelihood | Mitigation |
|---|---|---|
| Specialists ignore the response contract under load | Medium | Examples + prompt + regression checks; if a specialist consistently violates, drift back to it in Phase 5.5 iteration |
| Few-shot examples bloat prompt where Haiku context limit is hit | Low | Each prompt < 300 lines; Haiku 4.5 has 200k context, plenty of headroom |
| Pattern context confuses orchestrated path (no qid) | Medium | Explicit no-op when pattern is None; tested in sub-task 3 |
| Cassettes drift over time (deterministic LLM output assumption) | High | Cassettes record at temp=0 if possible; accept some response variance in regression diff |
| Segment-conditional routing breaks orchestrated questions for TBL/TJX | Medium | Test 3 cassettes per segment in sub-task 5; surface back before commit if any segment regresses |
| Worked examples don't generalize (model memorizes vs. learns shape) | Medium | Vary examples across question types and chart patterns; not just literal patterns to copy |
| Caveats parsing breaks under new prompts | Low | `_split_caveats` regex unchanged; new prompts reinforce the same fenced format |

---

## 10. Decisions locked

From the Phase 5 prep audit (commit c75642f + audit report), these are the resolved decisions:

| # | Decision | Choice | Rationale |
|---|---|---|---|
| Q1 | Pattern-awareness | Inject pattern context + graceful exception handling | Single biggest quality lever; cheap to add |
| Q2 | Segment-conditional routing | Add segment-aware logic | TBL/TJX have meaningfully different intents from grocers |
| Q3 | Structured output | **Dropped** — contract enforced by prompts + examples + regression-test checks | Preserves streaming UX; examples are the real quality lever |
| Q4 | Filter awareness | No automatic injection; honor only if user mentions date range in question | Agents should have full context; user overrides only when explicit |
| Model | Stay on Haiku 4.5 | Defer model differentiation to v3.1 | Risk-averse for demo |
| Prompt caching | Defer to Phase 5.7 follow-up commit | Keeps quality work and perf work separate; avoids confounding variables during quality review |
| MAX_TURNS | Standardize to 8 | One number, documented rationale (1 schema + 2 tenant + 2 lake + 1 chart + 2 buffer) | |
| Few-shot count | Pricing 5, Demand 5, Anomaly 5, Trade 5 (incl. no-data demo) = 20 total | Pattern coverage + edge case demonstration | |
| Cassette infrastructure | Lightweight custom JSON format with baseline + comparison sub-formats | Simple, no dependency, fits the test surface needs | |
| Routing UX | Inline prose-prepend stays | Defer cleanup to v3.1; UI risk too high for demo | |
| Streaming | Preserved as-is | Tool_use would have broken streaming; prompt-based contract is sufficient | |

---

## 11. Glossary

- **Specialist:** one of 4 domain-specific agents (pricing, demand, anomaly, trade), each with own system prompt and tool access
- **Orchestrator:** routing layer that picks a specialist for free-form questions
- **qid:** suggested-question ID (e.g., "P1", "A1", "T-D3") that maps to both a chart renderer and a specialist
- **Pattern:** chart shape (heatmap, scatter, waterfall, etc.); Phase 5 injects into specialist prompts so prose grounds in chart shape
- **Cassette:** recorded LLM call (input + tool calls + response) saved to disk for regression testing
- **Comparison cassette:** baseline + phase5 response side-by-side for manual quality grading
- **Contract:** the 4-part response shape (Headline → Evidence → Therefore → Caveats) every response must follow. Enforced by prompts, examples, and regression-test checks
- **Segment:** merchant type (grocer / QSR / retail); used in segment-conditional routing
- **MAX_TURNS:** maximum tool-use cycles a specialist can take before forced final response
- **No-data response shape:** adapted contract for when requested comparison has no data (e.g., TBL pricing question with no same-segment peers)

---

## 12. Phase 5 follow-up: prompt caching (Phase 5.7)

After Phase 5 ships and the new baseline is stable, add prompt caching as a separate focused commit.

**Why deferred:** prompt caching changes the response pipeline (cache_control headers, time-to-first-token differences during streaming). Adding it during Phase 5 means one more variable when debugging quality regressions. Cleanly separating the quality work from the perf work lets each be evaluated independently.

**Phase 5.7 scope:**
- Add `cache_control` directives to static portions of specialist system prompts
- Validate no behavior change against the new cassette baselines
- Measure cost reduction (expected: 30-50% on cached portions)

**Phase 5.7 timing:** after Phase 6 (demo prep) is complete OR before deploy, depending on whether cost reduction matters for production usage. Not required for demo.