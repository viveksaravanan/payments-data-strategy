# Agent Quality Standard

The standing bar every user-facing specialist must clear. **Pricing &
Benchmarking is the reference implementation** — it was rebuilt end-to-end
(the self-tag lake + sortable-gap query + the two gates) and validated
ground-truth-first. This doc distills what generalizes so the other four
specialists (demand, anomaly, trade, advisor) become a *targeted rollout*,
not a rediscovery.

Two buckets: **analytical rules** (what makes an answer insightful, not just
accurate) and **structural rules** (what keeps it grounded and safe). Each
rule is tagged:

- **[shared]** — already enforced in `_shared_answering_rules.md` (injected
  into every specialist) or in engine code, so it holds for all agents today.
- **[promote]** — proven in pricing, should be lifted into the shared rules
  or a shared helper as the other agents adopt it.
- **[pricing]** — specific to pricing's domain; the *pattern* generalizes but
  the mechanism is pricing-shaped.

Applying these to the other agents is tracked in the **per-agent checklist**
at the end — that work is out of scope here; this doc is the specification it
builds against.

---

## Analytical rules

### A1. Control for composition (mix) before you name a level — **[promote]**

A blended metric is not the thing itself. A category-level average selling
price looks like a "price position" but is really *price × assortment* — you
can be "expensive in Beef" purely because you skew to steak. Drill to the
grain where the signal is real, then read the level there.

- **Pricing:** rank the own-vs-peer gap at **subcategory** grain, never
  category. The gap query groups by `(category, subcategory)` so mix is
  separated by construction; Gate A then checks the flagged subcategory's
  siblings — if only one carries the gap, it's a subcategory price story, not
  a category one.
- **Generalizes to:** any averaged/aggregated headline. Demand: a basket-mix
  "over-index" is a share-of-mix claim, not a demand claim — decompose
  traffic × ticket × mix before attributing a revenue gap. Anomaly: a store's
  "drop" blends category shifts — drill to the category driving it. Trade: a
  neighborhood average blends store-level performance.

### A2. Pair the headline metric with its "is it working" counter-metric — **[promote]**

A single number can't tell you whether a position is *working*. Always carry
the counter-metric that says so, and present them side by side.

| Agent | Headline metric | Counter-metric |
|---|---|---|
| Pricing | price gap (own vs peer ASP) | volume (`own_units` vs `peer_units`) |
| Demand | forecast / expected | realized units |
| Anomaly | own drop | peer drop (is it market-wide or operational?) |
| Trade | your share of a zone | draw / where customers actually shop |

- **Pricing:** "cheaper but not moving outsized volume" is margin left on the
  table; "cheaper and moving heavy volume" is the low price doing its job.
  The gap query returns `own_units`/`peer_units` in the same rows so the pair
  is always in hand.
- **A2a — the counter-metric must be *comparable*.** A raw own-vs-peer count is
  apples-to-oranges when the peer number aggregates a *set* of merchants: `peer_units`
  sums **all** peer stores, so comparing it to a single banner's `own_units` understates
  your position (a lone banner rarely out-totals the whole peer set even when it out-sells
  them per store). **Normalize** — per store (`own_units/own_stores` vs
  `peer_units/peer_stores`) is the fair unit the anonymized lake supports; per-merchant
  isn't (peer identity is stripped). This is not cosmetic: on KRG seafood the raw
  comparison (165k vs 144k ≈ "level") **inverts** once normalized (27k vs 16k/store =
  1.7× — the low price *is* winning traffic → hold, not raise). Any agent comparing an own
  count/volume to a combined-peer count/volume must normalize the same way.
- **Generalizes:** never report the headline metric alone when its
  counter-metric changes the interpretation — and never compare an own aggregate to a
  multi-entity peer aggregate without normalizing to a shared per-unit basis.

### A3. Both directions — never report only the favorable side — **[promote]**

If the question is "where am I priced off the market," the answer has a below
side *and* an above side. Reporting only where you're cheap flatters the
merchant and hides the risk.

- **Pricing:** the two flagship demo pills are deliberately paired —
  *furthest below* and *furthest above* — and both resolve off the same
  sortable gap query (ASC vs DESC). Direction is a sort order, not a
  narrative choice.
- **Generalizes:** demand over- **and** under-performing categories; anomaly
  spikes **and** drops; trade over- **and** under-performing neighborhoods.

### A4. Earn the directive with a gate; otherwise use screening language — **[pricing]**

"You're cheaper, so raise your price" is a hypothesis dressed as a finding.
Directive language (*raise, underpriced, leaving margin*) must pass an
explicit check first; if it doesn't, downgrade to screening language (*worth
checking at the shelf, looks like assortment, test incrementally*).

- **Pricing:** two gates before "raise" — **Gate A (mix)**: the gap survives
  the subcategory drill; **Gate B (KVI)**: the subcategory isn't a
  known-value traffic driver where a low price is deliberate. Fail either →
  screening language.
- **Generalizes as a pattern:** every agent that recommends an action should
  name the precondition that action depends on and refuse to assert past it.
  The gate content is domain-specific (demand: is the "underperformance" real
  after mix?; anomaly: is the drop operational, i.e. peers are flat?), but the
  *discipline* — no directive without a passed gate — is universal.

### A5. Size the prize as a ceiling and name the unmeasured lever — **[promote]**

Size opportunity as a **ceiling**, not a forecast, and always state what the
data cannot tell you.

- **Pricing:** the gross prize is ~`per-unit gap × volume`, presented as the
  gap and the volume **side by side** (never multiplied into one figure — that
  product isn't a verifiable claim). Always paired with the honest limit:
  *"realized gain depends on price elasticity, which this data doesn't
  measure."*
- **Generalizes:** every sizing carries its unmeasured lever. Demand: a
  campaign lift ceiling vs. unmeasured cannibalization. Anomaly: a recovery
  size vs. unknown cause. Trade: an expansion ceiling vs. unmeasured
  cannibalization of existing stores. Name it explicitly.

### A6. Select deterministically in the data layer, not by LLM eyeballing — **[promote]**

"Which is the most/least X" must be computed and sorted **in one query**, not
ranked by the model across two separate result frames. Cross-frame eyeballing
is where selection silently goes wrong.

- **Pricing (the reference fix):** the viewer's own rows are present in the
  lake tagged `peer_relationship = 'self'`, so own and peer sit in one frame
  and the gap is `ORDER BY`-able. A **total order** (`gap, own_units,
  subcategory`) pins ties so the top row is identical every run. This is what
  made "furthest below/above" correct *and* deterministic — validation had
  shown the model picking the 2nd-deepest gap and inverting direction when it
  ranked cross-frame by eye.
- **Generalizes:** any "biggest / smallest / most-anomalous / top-drawing"
  selection should be a `ROW_NUMBER`/`ORDER BY` in the query with a total-order
  tiebreak, and the answer reads the top row — the model narrates the
  selection, it doesn't make it.

### A7. Drill the headline to its named drivers — with the full detail the grain allows — **[promote]**

Once the headline entity is picked, answer "which specific things drive it" by
drilling to the finest grain the **owned** data supports, and narrate each driver
with *all* the fields that make it a driver — not just the one in the headline.
Name the honest limit: some detail only exists on the own side.

- **Pricing:** the flagged subcategory is volume-weighted, so its highest-`units`
  SKUs are literally what pull the average to the gap. The drill names the top
  3–5 with **both price and volume** ("Tyson Salmon at $8.11 on 44k units"), and
  may anchor a driver to the **peer subcategory** benchmark (`pct_change` of the
  product's own `asp` vs the lake `peer_asp`) — worded "vs the peer *subcategory*
  average," **never** "vs the same item at peers," because the peer lake has **no
  SKU grain**. That structural asymmetry (own reaches SKU, peer stops at
  subcategory) is stated, not hidden.
- **Generalizes:** every agent should push the drill to its own-side floor and
  narrate the drivers fully. Demand: the specific categories/SKUs moving a mix
  shift, with units. Anomaly: the stores/categories inside a flagged drop, with
  their magnitudes. Trade: the stores inside a flagged neighborhood. Where the
  peer side is coarser than the own side, say so rather than imply a like-for-like
  that doesn't exist.

---

## Structural rules

### S1. Every lake aggregate references `peer_relationship` — **[shared]**

The viewer's own rows are present in the lake (tagged `'self'`), so a bare
`AVG`/`SUM`/`COUNT` over `lake_transactions` blends self into the peer number.
The engine (`lake_sql.py::_check_peer_scoped`) **rejects** any aggregate over
`lake_transactions` that never references `peer_relationship`, and the k=50
floor (`_inject_count`) counts peer rows only. Enforced once, at the single
choke point every specialist routes through — but each prompt states the
requirement so the model writes it right the first time.

### S2. `emit_response` is terminal, never a checkpoint — **[shared]**

The model must not call `emit_response` with a placeholder headline between
query rounds ("recon complete, drilling now"). Emit is the **last** action,
called once, only when every cited number is in hand. (Reinforced by temp 0,
which stops the model re-sampling a mid-flight emit.)

### S3. No untraceable number reaches the user — **[shared]**

The two-pass claims validator (`claims.py`) covers the full surface: Pass A
recomputes each declared claim from its frame; Pass B scans for undeclared
metric numerics and strips their clause. Closed derivation grammar
(`difference | ratio | pct_change | aggregate(sum|mean)`) — no arbitrary model
arithmetic. **Caveat (S3a):** the validator checks the *value*, not the
surrounding *noun* — "your gap is $8.16" when $8.16 is the ASP level is
traceable but wrong. Noun discipline is a prompt concern, caught at review.

### S4. Percentages trace to fractions, rendered ×100 — **[shared]**

A `pct_change` / `ratio` claim resolves to a fraction (e.g. `-0.298`); the
normalizer (`claims.py::_format_normalized`) scales it ×100 for display
(`-29.8%`). This was a cross-agent bug (sub-1% artifacts like `-0.298%`) fixed
at the normalizer, so it holds for every agent.

### S5. Deterministic decoding — **[shared]**

Specialists run at `temperature = 0.0` (`llm.py::call_with_tools`). Combined
with the total-order selection (A6), a fixed question + fixed data yields the
same answer run to run — the property the demo pills depend on.

### S6. Ground-truth-first validation — **[method, promote]**

To validate an agent, **compute the answer from source first** (`data/raw` +
the peer lake), *then* run the agent and diff side by side: did it pick the
same entity, is its number right, do its named details match? This is the
method that caught the pricing selection bug (agent picked Beef Steaks when
Fish & Shellfish was the true furthest-below, and inverted the "above"
direction). Never validate by reading the agent's answer and asking "does this
look plausible" — that is exactly the failure mode that ships wrong answers.
See `scripts/demo_answer_key.py` for the reconciliation harness.

---

## Per-agent application checklist (the rollout backlog)

Pricing is done and is the reference. For each other specialist, walk the
analytical rules and record what its A1 grain / A2 counter-metric / A4 gate /
A5 lever / A7 drill are. The structural rules (S1–S6) already hold for all agents
by construction; the work is analytical.

### Demand Forecasting & Campaign Adjudication
- [ ] **A1 grain:** decompose a revenue/units gap into traffic × ticket × mix
      before attributing it; a basket "over-index" is a mix share, not demand.
- [ ] **A2 counter-metric:** expected vs realized; pair a mix over-index with
      the volume that confirms it's demand, not assortment.
- [ ] **A3:** report over- **and** under-performing categories.
- [ ] **A4 gate:** earn "the campaign worked / this category is a real
      opportunity" — screen out mix and seasonality first.
- [ ] **A5 lever:** campaign-lift ceiling; name unmeasured cannibalization.
- [ ] **A6:** rank the biggest gap categories in-query with a total order.
- [ ] **A7 drill:** name the specific categories/SKUs moving the shift, with units.

### Anomaly Detection (operational only — never fraud/tampering, D20.3)
- [ ] **A1 grain:** drill a store-level drop to the category driving it.
- [ ] **A2 counter-metric:** own drop vs **peer** drop — market-wide vs
      operational is the whole question.
- [ ] **A3:** surface spikes **and** drops.
- [ ] **A4 gate:** "operational issue to investigate" only once peers are
      shown flat (else it's a market move, not your store).
- [ ] **A5 lever:** size the recovery as a ceiling; cause is unmeasured.
- [ ] **A6:** rank most-anomalous stores/categories in-query, total order.
- [ ] **A7 drill:** name the stores/categories inside the flagged drop, with magnitudes.

### Trade Area Intelligence
- [ ] **A1 grain:** a neighborhood average blends its stores — drill.
- [ ] **A2 counter-metric:** share of a zone vs draw (where customers actually
      shop from).
- [ ] **A3:** over- **and** under-performing neighborhoods.
- [ ] **A4 gate:** "expansion opportunity" earns it past existing-store
      cannibalization and coverage.
- [ ] **A5 lever:** expansion ceiling; name unmeasured cannibalization.
- [ ] **A6:** rank neighborhoods in-query with a total order.
- [ ] **A7 drill:** name the stores inside a flagged neighborhood, with magnitudes.

### Conversational Advisor
- [ ] Inherits the structural rules; for any own-vs-peer claim it makes
      (payment mix, segment mix), apply A1/A2/A3 and S1.
- [ ] **A6:** any "which is most X" it answers should be query-ranked, not
      narrated from a scan.
- [ ] **A7 drill:** push a headline to its drivers at the finest grain its data allows.

---

*Reference implementation: `src/agents/prompts/pricing.md` (the two gates + the
sortable-gap query shape), `src/lake/lake_sql.py` (the `peer_relationship`
enforcement + peer-only k-floor), `src/lake/build_line_items.py` (the self-tag
build). Validation method: `scripts/demo_answer_key.py`.*
