# V4 Pre-Design Audit

Critical read of the v3 codebase against the v4 goals, intended to
inform `V4_DESIGN.md`. Read-only audit — no code changes here.

**Branch:** `main` at `b1dc3d7`
**Audit date:** 2026-05-26
**Inputs reviewed:**
`V3_AUDIT.md` §"Known architectural debt for v4" + §"Phase 5.5.2";
`docs/V3_AGENTS_DESIGN.md` §10 decisions and §12 Phase 5.7;
`chart_patterns.md`; `src/agents/` (all 5 prompts, all 4 specialists,
orchestrator, tools, context, specialist); `src/dashboard/` (app,
chat, views, agents, data, chart_patterns, chart_takeaways,
questions); `src/lake/` (views); sample cassettes.

---

## 1. Executive summary

**What v3 should preserve.**

- The cassette infrastructure (`tests/cassettes/baseline/` × 12,
  `comparisons/` × 12) is the single most valuable artifact for v4 —
  it's the only way to grade prompt or compute-layer changes without
  "vibes-based" review.
- The contract enforcement (Headline → Evidence → Therefore →
  Caveats) is working and shipping. Don't redesign it; harden it.
- `MerchantContext` is a clean isolation boundary (`src/agents/context.py`).
  The LLM cannot supply a merchant_id; the binding is fixed.
- `tools.py` SQL guards (`is_safe_select`, `has_merchant_predicate`,
  `_validate_lake_query`) are battle-tested and small. Keep verbatim.
- Segment-conditional orchestrator routing
  (`src/agents/orchestrator.py:51`) is correct and tested. Preserve.

**Biggest architectural issue.**

The chart layer and the agent layer compute findings independently
from the same data. `src/dashboard/chart_takeaways.py:631` invokes
`D.<helper>` (`src/dashboard/data.py`) and produces a directional
takeaway string from structured fields the helper already returns
(`top_category`, `max_above`, `dominant_driver`, etc.). The agent
ignores all of this and runs its own SQL through
`tools.py::query_tenant` / `query_lake` (`src/agents/tools.py:415`,
`:492`). Phase 5.5.2 reverted three iterations of trying to bridge
the gap via prompt-side injection (V3_AUDIT.md:1287–1360). The right
fix is to lift the synthesis the chart already does into a typed
`Finding` that the agent reads as a tool. **The data.py helpers
already return Finding-shaped dicts** — what's missing is exposing
them through the agent's tool surface and standardising the shape.

**Riskiest part of the refactor.**

Over-determining the agent. Today the specialist's voice comes from
its freedom to call SQL, look at columns it cares about, and
synthesise. If `read_finding(qid)` becomes the only tool, the agent
becomes a JSON-to-prose template renderer and the demand for "ask
anything" free-form questions collapses to canned qids only. The
compute layer must coexist with `query_tenant` / `query_lake`; the
agent must be able to *supplement* the Finding with its own queries
when the user asks something the Finding doesn't cover.

**Rough sequencing recommendation (detailed in §7).**

1. Phase 1 — build `compute_finding()` for 10 highest-value qids.
2. Phase 2 — wire `read_finding` tool into the specialist tool layer.
3. Phase 3 — plumb `CardContext` through dispatch.
4. Phase 4 — port the remaining 20 qids, retire `chart_takeaways.py`,
   collapse the 30 `_render_<qid>` functions into one generic.
5. Phase 5 — split `chart_patterns.py` into a package; clean
   directories.
6. Phase 6 — write 5 few-shot examples per specialist from real
   cassettes (after the tool shape stabilises).

The user listed (1) compute, (2) chart simplification, (3) examples,
(4) card context, (5) directories. I reorder card-context ahead of
chart simplification (it's more user-visible) and few-shot examples
to the end (they should reflect the new tool shape, not get written
twice).

---

## 2. The unified-compute question

This is the central v4 architectural shift. The deepest section.

### 2.1 Current state — where SQL is generated in v3

Four modules produce SQL today. They overlap on the same underlying
tables and answer the same business questions but never share code.

**Module A — `src/dashboard/data.py` (4,202 LOC, ~30 cached helpers).**

Each helper is shaped:

```python
def <metric>(merchant_id, filters=None) -> dict:
    return _<metric>_cached(merchant_id, _filters_key(filters or {}))

@st.cache_data(ttl=3600)
def _<metric>_cached(merchant_id, key) -> dict:
    filters = _unpack_filters_key(key)
    # SQL...
    return {chart_inputs..., named_entities..., direction, magnitude}
```

The return dict carries both the raw chart inputs (`rows`, `cells`,
`series`) **and** the Finding metadata the chart caption needs
(`top_category`, `max_above`, `bottom_pp`, `dominant_driver`,
`growing_category`, `market_signal`). Examples:

- `category_peer_pricing_gaps` (data.py:775) returns `rows / cols /
  cells` for the heatmap **plus** `max_above`, `max_below` for the
  caption.
- `uc_decline_trajectory` (data.py:318) returns `weeks / own /
  peer_a / peer_b` for Pattern 1 **plus** `trough_week`,
  `own_pct_drop`, `peer_a_pct_drop`, `peer_b_pct_drop`,
  `market_signal`.
- `revenue_change_decomposition_own` (data.py:3839) returns
  `drivers` (the waterfall bars) **plus** `total_change_pct`,
  `dominant_driver`, `dominant_pp`, `tied_with`.

In other words, **the Finding is already being computed**. It's
just dict-typed and accessible only from inside the dashboard
process.

**Module B — `src/dashboard/chart_takeaways.py` (665 LOC, 30
takeaway functions + `compute_takeaway(qid, …)`).**

Each takeaway function calls `D.<helper>(...)` and string-formats
the dict's Finding fields into the directional caption. No new SQL
runs here. This module is a pure *synthesis layer* over data.py.
It exists today **only** to render UI captions (Phase 5.5.2 reverted
the injection that made it serve the agent as well —
`chart_takeaways.py:13–28`).

**Module C — `src/agents/tools.py::query_tenant` / `query_lake`
(694 LOC; SQL is LLM-generated, modules just enforce guards).**

The agent writes its own SQL each turn. The runner enforces
SELECT-only, requires `WHERE merchant_id = '<viewer>'` for tenant
queries, wraps lake queries in two CTEs that compute the v2.5
virtual lake from tenant tables (`tools.py:519`), and suppresses
sub-k=5 rows when a count column is present (`tools.py:454`). The
agent has full SQL freedom within those guards.

**Module D — `src/lake/views.py` (CTE bodies for the virtual lake).**

Static SQL strings (`lake_transactions_sql`, `lake_stores_sql`)
that the agent's lake tool wraps around the user's SELECT. No
overlap with data.py.

**The duplication.** Modules A and C answer the same questions.
For every qid (P1, A2, T-D3, etc.) the chart caption and the
agent's prose draw from different SQL. The chart helper computes a
weekly trajectory; the agent computes a 45-day-vs-45-day mean. Both
are correct interpretations of the same panel. The user sees them
disagree (V3_AUDIT.md:1083–1097, the actual diagnostic narrative).

### 2.2 Proposed v4 unified layer

The structure the v3 audit's "Known architectural debt for v4"
section sketches at a high level (V3_AUDIT.md:1262–1278) holds up.
Here's a concrete first cut:

```python
# src/findings/finding.py
@dataclass(frozen=True)
class Finding:
    qid: str                       # "P1", "A2", "T-D3", "R-P1"
    chart_pattern: str             # "heatmap" | "scatter" | "waterfall" | ...
    window: dict                   # {start, end, baseline_start, baseline_end}
    filters_applied: dict          # echoes the FilterSet used

    # The chart's raw inputs — what data.py already produces.
    chart_payload: dict            # rows/cells/series/polygons/etc.

    # The Finding the agent narrates from.
    headline: dict                 # {entity, value, unit, direction, comparator}
    evidence: list[dict]           # ordered, ranked rows for Evidence bullets
    named_entities: dict           # {top, bottom, peer_signal, ...}
    qualitative: dict              # {magnitude: "modest"|"sharp"|..., signal: str}
    caveats: list[str]             # k=5 suppression, missing weeks, etc.

# src/findings/compute.py
def compute_finding(qid: str, merchant_id: str,
                    filters: dict | None = None) -> Finding | None:
    """Single source of truth. Used by:
       - chart renderer (consumes chart_payload + named_entities)
       - agent's read_finding tool (consumes headline + evidence + caveats)
       - chart caption (consumes named_entities + qualitative)
    """
    ...
```

The agent gets a new tool:

```python
# src/agents/tools.py
READ_FINDING_TOOL = {
    "name": "read_finding",
    "description": "Read the structured finding for a known card / question. "
                   "Returns headline, evidence rows, caveats, and the window "
                   "the chart uses. Use this when the user clicked Ask about "
                   "this from a dashboard card or chose a suggested question.",
    "input_schema": {"type": "object", "properties": {
        "qid": {"type": "string"},
    }, "required": ["qid"]},
}
```

The specialist's tool loop receives a Finding (when a qid is in
scope) and **augments** with `query_tenant` / `query_lake` for
follow-ups the Finding doesn't cover (drilldowns to specific SKUs,
custom windows). The Finding's numbers are authoritative for the
Headline and Evidence bullets that map to it; the agent can add
extra bullets from its own queries with clearly distinct framing.

### 2.3 Feasibility — how cleanly does the current code separate?

**Cleanly, but not trivially.** The good news:

- Data.py helpers already return Finding-shaped dicts. The work is
  mostly *renaming* and *typing*, not recomputing.
- `chart_takeaways.py` is a proof of concept: it already shows you
  can synthesise a directional narrative from `D.<helper>`'s output
  with no extra SQL.
- The chart renderer's contract already accepts a `takeaway` string
  parameter (`chart_patterns.py:139`, `:283`, every render_* call
  site in chat.py). Switching `takeaway=CT.compute_takeaway(...)` to
  `takeaway=finding.qualitative["caption"]` is a one-line change per
  call site.

The hard news:

- **30 qids × 30 takeaway functions** all need to land on a uniform
  Finding shape. Today each `_takeaway_*` returns a string built
  from different keys of the helper's dict. There's no shared
  contract. v4 has to define it once and force every helper to fit.
- Some helpers produce *multiple* findings (D7's
  `revenue_gap_decomposition` returns one decomposition per peer in
  `per_peer`; T-A3's `day_daypart_heatmap` has both `weakest` and
  `strongest`). The Finding type needs to support multi-headline
  responses or the caller has to pick one.
- `tenant_transactions` is unaliased in some helpers
  (`_ticket_band_distribution_cached` at data.py:4035) and aliased
  as `t.` in others. Filter helpers (`_txn_where`,
  `_own_filters_sql`) assume the alias. The cleanup is straightforward
  but every helper has to be touched.

**Entanglements that make the refactor harder than it looks:**

- `data.py` mixes filter helpers, merchant metadata, KPI strip
  logic, lake cache wrappers, and per-qid helpers in a single 4,202
  LOC file. Splitting this into `findings/` per-qid modules will
  surface circular-import issues with `chart_patterns.py` (which
  imports `data`) and `chat.py` (which imports both).
- `chat.py::_render_<qid>` (30 functions, chat.py:122–755) is the
  current dispatch table. Each function fetches data, computes
  takeaway, calls renderer. In v4 this collapses to one
  `render_qid_card(finding)` function — but the 30 functions
  currently bake in title strings and conditional behaviour
  (`_render_a1` adapts for grocer-with-no-UC-stores; `_render_t4`
  customises tooltips). That conditional logic has to migrate into
  Finding's chart_payload or into a small per-qid view config.

### 2.4 Migration path

**Incremental, qid-by-qid. Not a flag day.**

Phase 1 (compute layer): port the highest-leverage qids first —
the ones with the most cassette evidence of chart-prose
contradiction. Based on Phase 5 commit narrative, those are P1,
A1, A2, D7, T-D3, R-D3 (and the share-trajectory pair T-D2/R-D2
which use the same helper). 10 qids covers the demo arc; the other
20 follow in Phase 4.

Phase 2 (tool wiring): add the `read_finding` tool. The specialist
signature gains an optional `qid` kwarg. When `qid` is set and the
Finding exists, the specialist's prompt includes a one-line note:
*"A structured finding is available for this question — call
read_finding to get the canonical numbers."* For free-form
orchestrated questions, `qid=None`, no Finding tool, behavior is
unchanged.

Phase 3 (card context): cards already know their semantic identity
(KPI strip's revenue card → could map to a synthetic "KPI_REV" qid
in compute_finding). Add the wiring.

Phase 4: port the remaining 20 qids, retire `chart_takeaways.py`,
collapse `chat.py::_render_<qid>` to one generic.

**This works because the agent's free-form path is unchanged
throughout.** The unified-compute story is opt-in at the qid level.
Cards and suggested questions get it; free-form text still falls
through to `query_tenant` / `query_lake`.

### 2.5 Risks

1. **Finding shape becomes a leaky abstraction.** Some qids
   genuinely need multiple findings (D7 has one per peer, T-A3 has
   weakest + strongest). If the shape doesn't accommodate that,
   the agent gets the wrong number. Mitigation: prototype against
   D7 and T-A3 first; only commit the shape after both fit.

2. **The agent learns to ignore the Finding and re-query anyway.**
   Phase 5.1.9 saw this — the injected takeaway didn't fully stop
   the agent from running its own SQL. The fix wasn't perfect.
   Mitigation: prompt explicitly says "use read_finding's numbers
   in your Headline and Evidence; only call query_tenant /
   query_lake for follow-ups not covered by the Finding."

3. **Free-form orchestrated questions never benefit.** Without a
   qid, no Finding. The chart-vs-prose disagreement risk persists
   for free-form. Mitigation: acceptable for v4. Free-form is the
   minority of usage; cards + suggested questions are the demo arc.

4. **Filter state propagation.** Today `_render_<qid>` reads
   `st.session_state.filters_by_merchant[merchant_id]` and passes
   to the helper. The agent currently knows nothing about active
   filters. If `read_finding` reflects the dashboard's filters, the
   agent's narrative changes when the user toggles a store filter.
   That's probably correct, but it's a behavioural change that
   wants explicit confirmation.

5. **Cassette regressions.** All 12 baseline cassettes were
   recorded against the v3 query_tenant/query_lake path. After v4
   the same questions go through read_finding. The cassettes will
   not replay byte-identical. Plan to re-record after Phase 4.

---

## 3. Chart and chart-patterns audit

### 3.1 Inventory

`src/dashboard/chart_patterns.py` is **1,844 LOC**, organised as:

- 9 named patterns from `chart_patterns.md` mapped to render functions
- 6 public `render_*` entry points: `render_time_series_vs_peers`,
  `render_time_series_own_multi`, `render_cross_merchant_comparison`,
  `render_heatmap`, `render_horizontal_bars_own`,
  `render_horizontal_bars_grouped`, `render_scatter_with_peers`,
  `render_waterfall`, `render_neighborhood_map`,
  `render_table_with_drilldown`, `render_kpi_callout`
- ~10 private `_render_*` sub-helpers (mode-specific variants of
  the public renderers)
- `render_ask_about_this` — the affordance button
- `_render_card_header` — wires title + takeaway + affordance icon
  uniformly for every chart
- `_render_card_footnote`, `render_empty_state`,
  `_render_sparkline`, `format_takeaway` — small utilities
- ~250 LOC of geographic data (`_ZIP_CENTROIDS`,
  `_NEIGHBORHOOD_ZIPS`, `_hex_vertices`, `_neighborhood_polygons`,
  `neighborhood_polygon`, `neighborhood_centroid`,
  `_POLYGON_CACHE`) used only by `render_neighborhood_map`
- Color and layout constants (ACCENT, PEER_A, DIVERGING_*,
  HOVERLABEL, K5_SUPPRESSION_FOOTNOTE) — explicitly duplicated from
  `styling.py` with a `# Phase 4 close-out can consider consolidating`
  TODO at chart_patterns.py:34

### 3.2 Duplication assessment

The chart layer has *real* variety driving real duplication, plus
some unnecessary duplication.

**Real variety, justified duplication:**

- 9 chart patterns × 2-3 modes each (e.g. heatmap has
  `cross_merchant_diverging` / `own_only_diverging` /
  `own_only_sequential`). The modes have different colour scales,
  different cell formatting, different colorbar settings. Three
  separate sub-helpers (chart_patterns.py:678, :755, :828) is
  *slightly* shorter than one parameterised helper would be.

- Waterfall renders are technically one mode but support two
  semantic shapes (`cross_merchant` for D7 gap decomposition,
  `own_vs_own_baseline` for T-D3/R-D3 revenue-change). Same renderer
  body, different titles and tooltip framings.

**Unnecessary duplication / sprawl:**

- Color constants (ACCENT, PEER_A, etc.) at chart_patterns.py:33–69
  duplicate values in `styling.py`. The comment admits it. CSS vars
  don't reach Plotly so this can't be eliminated entirely, but the
  duplication should be one-way (Python source of truth, CSS reads
  from there at build time) instead of two-way drift.

- `_render_card_header` is threaded through *every* `render_*`
  function via a uniform `ask_about_this=None` kwarg. That's 16
  call sites passing the kwarg through (chart_patterns.py grep
  count: 57 references). The pattern is repetitive but
  not actually bad — it gives uniform affordance position. It just
  reads as boilerplate.

- The neighborhood map machinery (250 LOC) is its own subdomain —
  hex hull geometry, ZIP centroid table, polygon cache — that lives
  inside chart_patterns.py for proximity to render_neighborhood_map.
  It's a clean split candidate (own module).

- KPI callout (`render_kpi_callout`) + sparkline + flag colour map
  are conceptually a different family from "charts". They're
  Streamlit-native widgets, not Plotly figures. They could live in
  a `kpi.py` module.

### 3.3 Pattern consolidation opportunities

A clean redesign would split the layer into shape-of-data
families:

```
src/dashboard/chart_patterns/
    __init__.py            # re-exports for back-compat
    header.py              # _render_card_header + render_ask_about_this
    bars.py                # cross-merchant + own + grouped
    heatmaps.py            # 3 modes
    scatter.py             # with-peers + parity line
    waterfall.py           # cross-merchant + own-vs-baseline modes
    map.py                 # render_neighborhood_map + geo data
    table.py               # render_table_with_drilldown
    kpi.py                 # render_kpi_callout + sparkline
    constants.py           # colors, hoverlabel, footnote strings
    empty.py               # render_empty_state + format_takeaway
```

Each file ~150–300 LOC. The package's `__init__.py` re-exports
public names so callers (chat.py, views.py) don't have to change.

### 3.4 Honest take — is "got out of hand" correct?

**Half right.** The variety is real and justified — 9 patterns
with 2–3 modes each is what a dashboard like this needs. The
*organisation* is the problem. One file at 1,844 LOC, with map
geometry next to KPI callouts next to waterfalls, makes it hard to
add a new chart confidently because you don't know what concept
your function should live near. The `_render_card_header`
affordance plumbing is repetitive but is *not* the right thing to
remove — the unified affordance position is an explicit UI
guarantee.

The right verdict: **chart_patterns.py needs to become a package,
not a single file. The patterns themselves are right.**

---

## 4. Agent prompt audit

### 4.1 Per-prompt structure

All four specialist prompts follow this skeleton:

1. Persona + viewer line (`{{viewer_id}}` substitution)
2. Scope (in / out)
3. *(Anomaly + Demand only)* Early-stop rule
4. Efficiency (target tool-call count)
5. *(Demand only)* Finding products by name + literal date window
6. *(Pricing only)* Worked example with SQL
7. No-peer / no-data case (TBL / TJX handling)
8. Tools list
9. Final response (what NOT / TO include)
10. Number grounding (CRITICAL)
11. Output format (Headline / Evidence / Therefore / Caveats)
12. Full example response
13. No clarifying questions
14. Formatting rules
15. Rules (numbered list)
16. *(Anomaly only)* Anomaly knowledge base appendix

### 4.2 Inconsistencies across specialists

- **MAX_TURNS drift in prompts vs code.**
  - `pricing.md:252` says "Up to 5 model turns total"
  - `trade.md:228` says "Up to 5 model turns total"
  - `anomaly.md:225` says "Up to 6 model turns total"
  - `demand.md:248` says "Up to 6 model turns total"
  - **Actual `MAX_TURNS = 10`** in all four specialist subclasses
    (`pricing.py:21`, etc.). Phase 5.1.9 bumped to 10; the prompts
    were never updated. Stale.

- **Early-stop rule** present in anomaly.md and demand.md but
  missing from pricing.md and trade.md. The reason in code (the
  `Early-stop rule` section) appears to be a hedge against the
  Anomaly Knowledge Base prompting exhaustive walk-throughs; same
  applies in spirit to Trade. Inconsistency, probably not a bug.

- **Worked example with SQL** only in pricing.md (`pricing.md:43`).
  Demand has "Finding products by name" with a SQL block
  (`demand.md:24`) which is a different kind of guidance. Trade and
  Anomaly have no SQL examples at all.

- **Anomaly knowledge base** (`anomaly.md:231`) lists the three
  planted anomalies. This is unique to anomaly.md. Correct — only
  anomaly needs it — but it's a section pattern the other prompts
  don't use.

### 4.3 Sprawl assessment

The prompts each carry ~120 LOC of near-identical boilerplate.
Specifically:

| Section | Pricing | Anomaly | Demand | Trade | Verdict |
|---|---|---|---|---|---|
| Number grounding | ✓ | ✓ | ✓ | ✓ | Load-bearing today; will be partially redundant once `compute_finding()` guarantees grounding by construction. v4 can collapse to a 5-line "use read_finding's numbers" rule. |
| Final response (DO/DON'T) | ✓ | ✓ | ✓ | ✓ | Load-bearing. Same for all 4. Move to shared partial. |
| Output format (4 subsections) | ✓ | ✓ | ✓ | ✓ | Load-bearing. Identical structure across 4. Move to shared partial. Only the *examples* under each subsection should be per-specialist. |
| Full example response | ✓ | ✓ | ✓ | ✓ | Load-bearing — this *is* the few-shot. One per specialist. Keep, expand to 5 each (per v4 goal 3). |
| No clarifying questions | ✓ | ✓ | ✓ | ✓ | Load-bearing. Identical across 4. Shared partial. |
| Formatting rules ($ escape, peer_a not bolded, etc.) | ✓ | ✓ | ✓ | ✓ | Load-bearing — Streamlit markdown quirks. Identical. Shared partial. |
| Rules (numbered list) | 9 | 8 | 8 | 8 | First 7 rules are identical across all four. Privacy/k=5 rule is identical. Only the domain-specific rules differ (pricing has "lake bins txn_total"; demand has "round dollar uplift"; trade has "no peer lat/lng"). Shared base + per-specialist append. |

The user's prompt should support a *partial / include* mechanic so
the shared content can be edited once. Today the prompts are
single .md files with `{{viewer_*}}` substitution done by string
replace; there's no include mechanism. v4 should add one (5 lines of
code; reads partial files at module load and substitutes
`{{include:final_response.md}}` style placeholders).

**Cleanup targets (residue from earlier phases):**

- The MAX_TURNS line is stale (see 4.2).
- The "Mathematical sanity checks before responding" block in the
  Number grounding section was added when share-delta hallucinations
  were rampant. With `compute_finding()` returning verified shares,
  most of these checks become redundant — the relevant constraint
  is encoded in the helper.
- Each prompt has a parenthetical mentioning "the runner trims to
  20 in the LLM payload with a 'showing top X of N' note". This is
  load-bearing (it tells the agent why some queries return
  truncated results). Keep.
- Pricing prompt's Worked Example SQL block (pricing.md:45–67) is
  load-bearing today because it shows the agent the canonical
  pricing comparison pattern. Once `compute_finding("P1", ...)`
  exists and returns the canonical comparison, this example
  becomes redundant for P1 specifically — but is still useful for
  free-form pricing questions where there's no Finding.

### 4.4 Few-shot example plan

The v3 design doc (`V3_AGENTS_DESIGN.md:435–449`) planned 5
examples per specialist for Phase 5.3, deferred to v4. The 12
baseline cassettes in `tests/cassettes/baseline/` are the raw
material. Each cassette contains the question, the tool calls
the agent made, and the prose response — already in the format an
example needs.

Cassette inventory:

| Specialist | Cassettes shipped | Coverage |
|---|---|---|
| pricing | P1_KRG, P3_KRG, R-P2_TJX | heatmap, scatter, table-no-peer |
| anomaly | A2_KRG, A3_KRG, T-A2_TBL | per-store table, per-cat table, per-SKU TBL |
| demand | D3_KRG, T-D2_TBL, R-D2_TJX | mix diverging, share trajectory ×2 |
| trade | T1_KRG, T2_KRG, T4_KRG | map ×3 |

**For each specialist, the 5 examples should cover:** primary
chart pattern (heatmap/scatter/waterfall/table/map), a no-peer /
no-data edge case (TBL or TJX), a free-form follow-up where the
Finding doesn't cover the user's question, a multi-pattern
question (where the agent picks the right Finding), and a clean
on-script case that demonstrates the contract shape.

**High-leverage examples per specialist (don't write them yet —
write after Phase 2 tool shape stabilises):**

- **Pricing (5):**
  - P1 heatmap with k=5 suppression visible (the P1_KRG cassette,
    cleaned up)
  - P3 scatter with quadrant framing in the Therefore (P3_KRG)
  - TBL pricing question that hits the no-peer response shape
  - Pricing follow-up: "what specific SKUs drive the dairy gap?"
    (shows the agent supplementing read_finding with query_tenant)
  - Free-form pricing question with no qid (shows fallback to
    today's tool loop)

- **Demand (5):**
  - D3 basket-mix diverging (D3_KRG, cleaned up)
  - D7 revenue-gap waterfall (new; D7 cassette not in baseline but
    should be — captures the multi-peer case)
  - The planted slowing-ice-cream scenario (high-value, demo-defining
    per orchestrator.md:9)
  - T-D2 share trajectory for TBL (T-D2_TBL)
  - Campaign attribution (pasta-promo, planted in the data)

- **Anomaly (5):**
  - A1 UC market-wide decline (the flagship; cassette doesn't exist
    yet — should be recorded)
  - A2 per-store table with peer-corroboration column (A2_KRG)
  - Plaza Midwood avocado spike (planted single-store; demonstrates
    the "name the merchant only by role" privacy rule from
    anomaly.md:235)
  - Pasta-promo underperformance (planted; demonstrates campaign
    framing without naming the owner)
  - TBL no-peer anomaly response (T-A2_TBL is a SKU anomaly, but a
    store-level no-peer case is also worth showing)

- **Trade (5):**
  - T1 neighborhood performance map (T1_KRG)
  - T2 customer home density with under-served takeaway (T2_KRG)
  - T4 expansion-opportunity scoring (T4_KRG)
  - TBL trade-area question (cross-segment lake fallback per
    trade.md:31 — no existing cassette; record one)
  - Per-store performance variance follow-up (drilldown question
    showing the trade specialist using query_tenant alongside the
    map Finding)

**Where examples should sit in the prompt.** Today each prompt has
one "Full example response" near the bottom (`pricing.md:206`,
etc.). With 5 examples this would balloon the prompt; better is a
dedicated `## Examples` section near the bottom with each example
named and 6–8 lines (the question, the prose, the caveats fence).
Skip the SQL — read_finding makes the queries implicit.

**Quality criterion for good examples:** the example shows the
*shape* of a good response, not just one merchant's numbers. A
reader (or the LLM) should be able to substitute different
entities/percentages and the example would still read as correct.
The Phase 5.1.6 demand and trade worked examples already model
this well; replicate that style.

---

## 5. Card context flow (UI-to-agent wiring)

### 5.1 Current state

The dashboard has **16 cards** that carry an `ask_about_this`
affordance dict (`src/dashboard/views.py` grep count). Schema:

```python
ask_about_this = {
    "key":        f"ask_about_<card_id>_{merchant_id}",   # unique button key
    "specialist": "pricing" | "anomaly" | "demand" | "trade",
    "prefill":    "Templated question text…",
}
```

The cards are split across views:

- KPI strip (5 cards): Revenue, Transactions, Avg basket, Unique
  customers, Anomaly count — each anchored to a specialist
  (`views.py:644`).
- Performance section (3): Revenue trajectory, Transaction
  trajectory, Hour × DOW heatmap (`views.py:544`).
- Geography section (2): Neighborhood performance map, Store
  performance distribution (`views.py:409`).
- Catalog section (2): Category mix, SKU performance with
  top/bottom toggle (`views.py:227`).
- Customers section (3): New vs returning, Transactions per
  customer, Customer home geography (`views.py:80`).
- Plus pattern-card affordances inside `chart_patterns.py`
  (`_render_card_header` threads the kwarg through every render
  function).

**What happens on click:**

`render_ask_about_this` (`chart_patterns.py:1701`) is a single
button. On click:

1. Sets `st.session_state.chat_input_prefill = prefill`
2. Opens the chat drawer to side-mode if it was closed
3. Triggers `st.rerun()`

The rerun's chat panel reads `chat_input_prefill` and seeds the
text area (`chat.py:1170`). The user has to click **Send** to
dispatch — the affordance pre-fills text but doesn't submit.

**The `specialist` parameter is no longer applied.** The comment
at `chart_patterns.py:1716` explicitly says the previous behaviour
of also setting `state.active_agent = specialist` was removed
because "the orchestrator routes the question text to the
appropriate specialist at query time, so forcing a UI specialist
switch on affordance click felt presumptuous." The parameter stays
in the signature for backward compatibility but is dead code.

### 5.2 Gap analysis

The card knows a lot more than the prefill text:

| What the card knows | What reaches the agent today |
|---|---|
| Card identity (e.g. "kpi_revenue", "geo_neighborhood_map") | Nothing — only the prefill text |
| The metric value displayed (revenue = $1.2M, delta = +8.3%) | Nothing |
| The chart's qid / pattern (where there is one — KPI cards don't have qids; geography/catalog/customers cards each correspond to a data.py helper but no qid) | Nothing |
| The active filter state (date range, stores) | Nothing — orchestrator drops filters |
| Which dashboard section the user was looking at | Nothing |

Concrete consequences:

1. The agent re-discovers the metric from scratch. The card may
   show "revenue this week = $1.2M" computed by
   `D.kpi_strip(...)`. The agent computes its own value via a
   different SQL window and may report "$1.18M for the most recent
   7 days" — directionally aligned but numerically different.

2. The user clicked from a specific card showing a specific cohort.
   The agent's response doesn't acknowledge the visual context.
   Example: card 5.2 "Transactions per customer" shows the 11+
   cohort makes up 19% of customers but 41% of revenue. User
   clicks Ask. Today's agent receives "What does my customer
   frequency distribution tell me about loyalty?" — generic. The
   agent could be told "the user just saw a card showing 11+ cohort
   = 19% / 41%; focus your answer on this cohort or the long
   tail."

3. No way to tell the agent the user was on the dashboard's *map*
   vs *table* view of the same data. (Today this doesn't matter
   much because the prefill text differs per card — but it's a
   missed opportunity for structured context.)

### 5.3 Implementation sketch

A `CardContext` dataclass plumbed through dispatch:

```python
# src/dashboard/card_context.py
@dataclass(frozen=True)
class CardContext:
    card_id:       str            # "kpi_revenue", "card_5_2_freq", ...
    section:       str            # "kpi" | "performance" | "geography" | ...
    qid:           str | None     # set when the card maps to a Finding qid
    metric_name:   str | None     # "Revenue this week"
    metric_value:  Any | None     # 1_234_567 (the raw value)
    metric_format: str | None     # "currency" | "count" | "pct"
    delta_value:   float | None   # 0.083 (the displayed delta)
    filter_state:  dict           # the active dashboard filters
```

`render_ask_about_this` gains a `card_context: CardContext` param.
On click it stores both the prefill text AND the card context in
session_state:

```python
state.chat_input_prefill = prefill
state.chat_input_card_context = card_context
```

When the user clicks Send, the form submit (`chat.py:1202`) reads
both and includes the card_context in the pending dispatch:

```python
state.pending_dispatch = {
    "kind":         "free",
    "question":     free_q.strip(),
    "card_context": state.chat_input_card_context,
}
```

`dispatch_orchestrated` (`src/dashboard/agents.py:226`) gains an
optional `card_context` kwarg. The Orchestrator passes it to the
specialist:

```python
def ask(self, question, *, progress=None, on_token=None, card_context=None):
    decision = route(question, self.context)
    spec = _build_specialist(decision.primary, self.context)
    spec_resp = spec.answer(
        question, progress=progress, on_token=on_token,
        card_context=card_context,
    )
```

In `Specialist.answer` (`specialist.py:144`), when `card_context` is
present, the specialist prepends a structured system note to the
initial user message:

```python
if card_context:
    note = (
        f"[Card context] User clicked Ask about this from the "
        f"{card_context.section} section's '{card_context.card_id}' card "
        f"showing {card_context.metric_name} = "
        f"{_format(card_context.metric_value, card_context.metric_format)}"
        f"{' (Δ ' + _format_delta(card_context.delta_value) + ')' if ...}. "
        f"Active filters: {card_context.filter_state}. "
        + (f"Call read_finding('{card_context.qid}') for the canonical numbers."
           if card_context.qid else "")
    )
    messages = [{"role": "user", "content": note + "\n\n" + question}]
```

**Cleanest abstraction.** A structured `CardContext` type, not a
free-text injection. Keeps the metric value, filter state, and qid
available to the agent as data, not as a sentence in the prompt
the agent has to parse. The prompt-side note happens at dispatch
time, derived from CardContext, but the source of truth is the
typed object.

**Should card context override or augment orchestrator routing?**
Open question for §8. Today the orchestrator routes from question
text alone. If the user clicked from a Pricing P1 card, sending
that to the Anomaly specialist is unusual but not necessarily
wrong (maybe the user wants to know if the pricing gap is anomalous
this week). Recommend: card_context informs routing as a soft
signal (the router prompt mentions it) but doesn't bypass the
router. The specialist still receives card_context regardless of
who's chosen.

---

## 6. Directory structure assessment

Flagging, not prescribing. v4 design decides moves.

### 6.1 Files past their original scope

- **`src/dashboard/data.py` (4,202 LOC)** — started as cached query
  helpers (file docstring still claims this), now contains 30+
  per-qid SQL helpers, KPI strip, lake cache wrappers, merchant
  metadata, filter helpers, panel-date constants, anomaly window
  constants, ticket band definitions for TJX, day-of-week ordering
  for heatmaps, and reverse-mapping helpers. The natural splits
  are clear: filter helpers + connection (~150 LOC), merchant
  metadata + constants (~100 LOC), per-qid helpers (organised by
  domain, ~3,500 LOC), KPI strip (~250 LOC), lake cache
  back-compat shims (~50 LOC).

- **`src/dashboard/chart_patterns.py` (1,844 LOC)** — see §3.3 for
  the split candidates. The neighborhood map subdomain is the
  most obvious extraction.

- **`src/dashboard/chat.py` (1,261 LOC)** — has the chat panel UI
  (~700 LOC) and the 30 `_render_<qid>` functions (~500 LOC). The
  `_render_<qid>` functions are pure dispatch glue (fetch data,
  compute takeaway, call renderer). Once `compute_finding()` lands
  they collapse to one generic `render_qid_card(qid, finding)`.

- **`src/dashboard/styling.py` (799 LOC)** — CSS injection. Large
  but not mixed-concern. Reasonable as-is.

### 6.2 Modules with unclear boundaries

The "where should a new function go?" test:

- A new question that needs a chart helper → `data.py` (clear)
- A new chart caption → `chart_takeaways.py` (clear)
- A new dispatch wiring for a new qid → `chat.py::_render_<qid>`
  registry (clear today; gone in v4)
- A new chart pattern → `chart_patterns.py` (clear)
- A new agent tool → `src/agents/tools.py` (clear)
- A new specialist subclass → `src/agents/<name>.py` (clear)

But:

- A new function that takes a Finding and renders it → ???. Today
  this would land in `chart_takeaways.py` if it's caption-only or
  in `chat.py::_render_<qid>` if it's dispatch. v4 needs a clear
  home (probably `src/findings/render.py` or similar).

- A new affordance helper → `chart_patterns.py::render_ask_about_this`
  today, but the affordance is conceptually a *header* widget, not
  a chart pattern. Split candidate.

### 6.3 Naming confusion

- `src/dashboard/agents.py` (the dispatcher) vs `src/agents/`
  (the specialist classes) — searching for "agents" returns both;
  the dispatcher's name doesn't say "dispatcher". Rename candidate:
  `src/dashboard/dispatch.py`.

- `chart_patterns.py` (the renderers) vs `chart_takeaways.py`
  (the synthesis) vs `chart_patterns.md` (the spec). Three
  "chart_*" things, easy to confuse.

- Specialist construction is duplicated:
  `src/dashboard/agents.py::_run_specialist` (lines 167–182) and
  `src/agents/orchestrator.py::_build_specialist` (lines 281–294)
  have identical if/elif ladders.

### 6.4 Dead / transitional code v4 can delete

- The `specialist` parameter on `render_ask_about_this` is
  documented as no longer applied (`chart_patterns.py:1716`).
  Delete the parameter (with care — 16 callers pass it).

- `chart_takeaways.py` is the most obvious large delete candidate
  once `compute_finding()` ships. Per Phase 5.5.2, it exists today
  only to provide UI captions; v4 replaces it.

- `data.py` has 4 "Phase 4.4e removed X" comments (lines 184,
  204, 209, 218). These are documentation of past removals; the
  code is already gone. Delete the comments — they're now noise.

- `tools.py:19` says `"The runner in advisor.py injects..."`. The
  legacy advisor.py was archived to `docs/archive/legacy_agent/`.
  This docstring is stale.

- `app.py:71` `state.setdefault("active_agent", "pricing")` —
  Phase 4.5 removed the affordance's effect on active_agent. Worth
  checking if `active_agent` session state is still actually
  consumed anywhere (it appears chat.py:1109 still uses it for the
  selectbox).

- `app.py:75-77` "deprecated" `chat_expanded` session-state key
  with a comment saying it's kept for backward compat. With no
  external code reading it, delete.

- Old TODOs scattered with phase numbers (e.g. `chart_patterns.py:34`
  "Phase 4 close-out can consider consolidating"). Either resolve
  in v4 or delete.

---

## 7. v4 phase sequencing recommendation

The user's goal order was: (1) unified SQL, (2) chart logic,
(3) few-shot examples, (4) card context, (5) directory cleanup.

I recommend reordering to put card context ahead of chart logic
(it's more user-visible) and few-shot examples last (they should
reflect the new tool shape).

### Phase 1 — `compute_finding()` foundation

**Goal.** Build the unified compute layer with the 10
highest-leverage qids ported.

**Scope.**
- New `src/findings/` package: `finding.py` (the dataclass),
  `compute.py` (the `compute_finding(qid, merchant_id, filters)`
  entry point), `registry.py` (qid → compute function table).
- Port these qids first (selection rationale: most-used in cassettes
  + the ones with the loudest chart-prose contradiction history
  from Phase 5):
  - P1 (heatmap), P3 (scatter), D3 (diverging bars), D4 (scatter),
    D7 (waterfall), A1 (time series), A2 (table), T1 (map),
    T-D3 / R-D3 (shared waterfall helper).
- Leave the other 20 qids on `chart_takeaways.py` for now (Phase 4
  migrates them).
- One `tests/test_compute_finding.py` covers the 10 ported qids:
  each compute call should be a pure function of (qid, merchant_id,
  filters), reproducible, returns a complete Finding.

**Estimated effort.** 3–4 days. The "1–2 day" estimate in
V3_AUDIT.md:1275 underestimated by missing the agent and UI
integration that still has to happen in subsequent phases.

**Dependencies.** None — greenfield module.

**Risks.** Finding shape too rigid for multi-headline qids (D7
per-peer; T-A3 weakest+strongest). Mitigation: prototype the shape
against D7 and T-A3 first; only commit the shape after both fit.

### Phase 2 — Wire `read_finding` tool into the specialist tool layer

**Goal.** Agents can call `read_finding(qid)` and get the canonical
numbers for any ported qid.

**Scope.**
- Add `READ_FINDING_TOOL` schema to `src/agents/tools.py`.
- Add `read_finding(qid)` implementation that calls
  `compute_finding(qid, merchant_id, filters)` (filter awareness
  comes from MerchantContext, threaded in).
- Add `TOOLS_SPECIALIST_V4` constant that includes `READ_FINDING_TOOL`.
- `Specialist.answer` signature gains optional `qid` and `filters`
  kwargs. When `qid` is set and a Finding exists, prepend a
  one-line system note to the initial user message.
- `src/dashboard/agents.py::_run_specialist` and
  `dispatch_orchestrated` thread the qid + filters through.
- Add `MerchantContext.read_finding(qid)` wrapper.
- Update specialist prompts: add a "read_finding tool" section
  describing when to use it (when a qid is in scope) and how to
  combine it with query_tenant/query_lake follow-ups.

**Estimated effort.** 2 days.

**Dependencies.** Phase 1.

**Risks.** Agent learns to ignore `read_finding` and re-query
anyway (Phase 5.1.9 saw this). Mitigation: prompt explicitly says
"use read_finding's numbers in Headline and Evidence; only call
query_tenant / query_lake for follow-ups not covered by the
Finding." Add a contract test that asserts Headline numbers match
the Finding for the 10 ported qids.

### Phase 3 — Card context wiring

**Goal.** Cards pass `CardContext` to the agent when the affordance
is clicked.

**Scope.**
- New `src/dashboard/card_context.py` with the `CardContext`
  dataclass.
- `render_ask_about_this` gains a `card_context: CardContext`
  param.
- Click handler stores both `chat_input_prefill` AND
  `chat_input_card_context` in session_state.
- Form submit (`chat.py:1202`) includes `card_context` in the
  pending dispatch.
- `dispatch_orchestrated` and `Orchestrator.ask` accept
  `card_context`.
- `Specialist.answer` formats the structured note from
  `card_context` when present.
- Every `ask_about_this={...}` dict in views.py + chart_patterns.py
  is updated to construct a CardContext alongside (or instead of)
  the dict.

**Estimated effort.** 1 day (mostly threading).

**Dependencies.** Phase 2 (so the agent has somewhere structured to
put the metric value).

**Risks.** Low. Mostly threading + 16 call-site updates.

### Phase 4 — Migrate remaining qids + retire chart_takeaways.py

**Goal.** All 30 qids served by `compute_finding()`. The 30
`chat.py::_render_<qid>` functions collapse to one generic.

**Scope.**
- Port the remaining 20 qids into `src/findings/`.
- Delete `src/dashboard/chart_takeaways.py` (the module the user
  thinks "got out of hand" for narrative reasons — once Findings
  carry the takeaway, this file's reason to exist evaporates).
- Replace the 30 `_render_<qid>` functions in `chat.py` with a
  single `render_qid_card(qid, merchant_id, filters)` that calls
  `compute_finding` and dispatches to the right
  `CP.render_<pattern>` based on `finding.chart_pattern`.
- Re-record cassettes against the new path. The 12 baseline
  cassettes are recorded against v3's `query_tenant` /
  `query_lake` flow; they will not byte-replay after Phase 2.

**Estimated effort.** 2–3 days (per-qid porting is mechanical but
20 of them).

**Dependencies.** Phase 2.

**Risks.** Visual regressions across the 20 less-used qids.
Mitigation: cassette comparison run after each ported qid; manual
qa against the running dashboard for each chart.

### Phase 5 — Chart patterns consolidation + directory cleanup

**Goal.** Split `chart_patterns.py` into a package; clean dead
code; rename `src/dashboard/agents.py` → `dispatch.py`.

**Scope.**
- Split `chart_patterns.py` into the `chart_patterns/` package
  described in §3.3.
- Move the neighborhood-map data + geometry to
  `chart_patterns/map.py`.
- Consolidate the 3 heatmap renderers if shape allows (likely 2
  shared, 1 specialised — exact split is design work).
- Delete the stale comments listed in §6.4.
- Rename `src/dashboard/agents.py` → `src/dashboard/dispatch.py`;
  update callers.
- Consolidate the duplicated specialist-construction switches
  (`src/dashboard/agents.py:167–182` and
  `src/agents/orchestrator.py:281–294`) into one
  `src/agents/factory.py::build_specialist(agent_id, ctx)`.

**Estimated effort.** 2 days. Pure refactor; no behavioural change.

**Dependencies.** None — can run in parallel with Phase 4.

**Risks.** Low (cosmetic refactor). Cassettes catch any
behavioural drift.

### Phase 6 — Few-shot examples + prompt cleanup

**Goal.** 5 examples per specialist drawn from real cassettes;
shared-partial mechanism for the duplicated prompt boilerplate.

**Scope.**
- Add a 5-line "include partial" preprocessor to the prompt
  loader. Move the 4 sections that are identical across all 4
  specialists (Final response, Number grounding, No clarifying
  questions, Formatting rules) into `src/agents/prompts/partials/`.
- Fix the stale MAX_TURNS lines (pricing.md:252 etc. → "Up to 10").
- Write 20 examples (5 per specialist) per the §4.4 plan. Use the
  comparison cassettes as raw material; rewrite to reflect the
  read_finding tool shape from Phase 2.
- Trim the Number-grounding "Mathematical sanity checks" subblock
  (most are made redundant by compute_finding's guaranteed
  correctness for ported qids).

**Estimated effort.** 1–2 days code + 4–6 hours of example
writing (the design doc's original estimate at
V3_AGENTS_DESIGN.md:489).

**Dependencies.** Phase 2 (so examples reflect the new tool shape;
otherwise they'd need rewriting).

**Risks.** Low. Cassettes + manual review catch drift.

### Total

12–15 days of focused work. The "1–2 day" estimate in V3_AUDIT.md
for the unified-compute refactor undercounts by missing the agent
integration (Phase 2), card-context wiring (Phase 3), qid migration
(Phase 4), and example rewrite (Phase 6). Compute layer + plumbing
+ examples = the whole v4 substance.

---

## 8. Open questions for the user

These need user input before V4_DESIGN.md can lock.

1. **Free-form chart support.** When the orchestrator picks up a
   free-form question with no qid, today the agent generates a
   chart via `make_chart` (Plotly Figure cached on the specialist).
   In v4 with `compute_finding(qid, ...)`, what happens for
   off-script questions? Options: (a) free-form questions never
   get charts beyond what `make_chart` produces (status quo);
   (b) the agent tries to fit free-form questions to an existing
   qid where it can ("you asked about pricing; rendering the P1
   heatmap"); (c) free-form questions get *no* canned chart and
   the agent must explicitly call `make_chart`. Recommend (a); (b)
   risks misrepresenting the user's question; (c) reduces visual
   richness.

2. **Card context routing override.** If a user clicks the Ask
   about this affordance on the Pricing P1 heatmap, should the
   question always go to PricingSpecialist (bypassing the router)?
   Or should the router still classify and the card_context just
   enrich? My read of the existing code's comment
   (`chart_patterns.py:1716`) is the user deliberately wanted the
   router to keep deciding. Confirm? Or should v4 add an
   `assert_specialist` field to CardContext that bypasses routing?

3. **Filter propagation under card_context.** Cards reflect the
   dashboard's current filter state. If the user has filtered to 2
   stores and clicks Ask, the agent's `read_finding` should
   respect those filters — otherwise the agent's narrative
   diverges from the visible card. The orchestrated path today
   drops filters entirely. Confirm: card_context-carrying paths
   should propagate filters into compute_finding; free-form
   questions (no card_context) ignore filters as today?

4. **Re-record cassettes vs migrate.** Phase 4 changes the agent's
   tool surface, so the 12 baseline cassettes will not replay
   byte-identical. Two options: (a) re-record all 12 against the
   new path, treating Phase 4 as a fresh baseline; (b) keep the
   v3 baseline cassettes archived as the "v3 quality bar" and add
   a parallel v4 cassette set. (a) is simpler; (b) preserves
   regression-grading continuity if v4 quality is contested.

5. **Per-merchant Finding behaviour.** Today some qids only make
   sense for some segments (D7 is grocer-only because it needs
   peer-segment comparisons; T-A3 is TBL-specific QSR daypart).
   What should `compute_finding("D7", merchant_id="TBL", ...)`
   return? Options: (a) raise (qid not valid for this merchant);
   (b) return None with a clear "not applicable" reason; (c)
   return a degraded Finding with `caveats=["no peer segment for
   this viewer"]` and best-effort own-data fields. The current
   chart helpers each handle this inconsistently. v4 needs a
   single answer.

6. **Filter-state representation in CardContext.** The dashboard's
   filter dict has `{date_start, date_end, stores, categories}`.
   Categories don't have a UI today (per `app.py:184` comment).
   Should CardContext carry the full dict (forward-looking) or
   only what the UI exposes today (date + stores)? Recommend full
   dict — it's free and forward-compatible.

7. **Methodology report (out of scope per user, flagging anyway).**
   `scripts/build_report_html.py` is open in the user's IDE. The
   report likely consumes the same data.py helpers compute_finding
   would replace. If the methodology report's html generation uses
   `D.<helper>` directly, Phase 4's helper restructuring will
   touch it. Worth confirming before Phase 4 lands.
