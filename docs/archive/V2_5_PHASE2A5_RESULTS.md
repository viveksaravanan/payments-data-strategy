# Phase 2A.5 — Results & Open Decision

## What was wired

All 5 optimizations from the plan are implemented:

1. **Tool-result trimming** — `src/agents/tools.py::trim_for_llm`. Caps rows at 20 in the LLM payload, rounds floats to 2dp, strips all-null columns, adds a "showing top 20 of N" note. The specialist's `_last_table` keeps full precision for rendering.
2. **MAX_TURNS reduction + inline schema** — `src/agents/specialist.py` (`MAX_TURNS = 5`, was 6); `src/agents/prompts/pricing.md` rewritten with full column tables for tenant + lake schemas inline, plus explicit Efficiency section pushing parallel tool calls.
3. **Session-state cache** — `src/dashboard/placeholders.py::_cache_get/_cache_set`. Suggested-question buttons cache the response keyed on `(agent_id, question_id, merchant_id)`; cache hits return instantly. Fallback responses are not cached. Free-form input never caches (new `dispatch_free_form` entry point).
4. **Progress narration** — `src/dashboard/chat.py::_run_with_live_narration`. Single `st.empty()` placeholder updated by per-turn `progress(turn, msg)` callback. Replaces generic `st.spinner`.
5. **Streaming final response** — `src/agents/llm.py::call_with_tools_streaming` using `client.messages.stream()` context manager. The `Specialist.answer()` method routes through streaming when an `on_token` callback is provided. Same placeholder used for progress overwrites with streamed tokens as they arrive.

All 208 unit tests still pass.

## What the numbers say

Live LLM validation, 5 pricing samples × 1 viewer each:

| Metric | Phase 2A baseline | Phase 2A.5 (this run) | Target |
|---|---:|---:|---:|
| Latency / fresh question | ~42s | ~40s | <15s |
| Cost / question | ~$0.10 | ~$0.10 (range $0.08–$0.16) | <$0.06 |
| Avg turns | ~5 | 4–5 | ~3 |
| Convergence | mixed | improved after MAX_TURNS bump to 5 | all converged |

**The code-level wins are real (cache, streaming, progress) but the model-level targets are not being hit.** Cache hits do return instantly (~ms, no LLM call). But the cost and latency for fresh questions look unchanged.

## Why the targets aren't met

1. **The inline schema added prompt overhead it didn't recoup.** The pre-2A.5 prompt was small (~1.5k tokens) and the model rarely called `schema_info` anyway. Inlining the full schema (~5k tokens) eliminates a tool call the model wasn't making, while adding ~$0.015 per question to every turn's input cost. Net effect: cost up, not down.

2. **Sonnet 4.6 does not reliably parallelize tool calls from prompt-only guidance.** The prompt now explicitly instructs "issue `query_tenant` and `query_lake` in the same response," but empirically the model still issues them sequentially in separate turns. The Anthropic SDK supports this — the model doesn't choose to use it.

3. **Per-turn latency is the bottleneck.** Each Sonnet 4.6 turn with tool-use takes 8–12s. With 4–5 sequential turns, total latency is 35–55s. No prompt change short of cutting tools shrinks this.

## The lever that would actually move the numbers

**Switch `MODEL_SPECIALIST` from `claude-sonnet-4-6` to `claude-haiku-4-5-20251001`.**

Pricing math on the current load (~24k input + ~2k output per question):
- Sonnet 4.6 ($3 in / $15 out per Mtoken): $0.072 + $0.030 = **$0.102/q**
- Haiku 4.5 ($1 in / $5 out per Mtoken):     $0.024 + $0.010 = **$0.034/q** → under target.

Haiku is also typically 2–3× faster per turn (3–5s vs 8–12s for Sonnet). At 3 turns × 4s = **12s end-to-end** — within target.

The trade-off: Haiku is less rigorous about following formatting rules (the caveats block, peer-isolation guardrails). The peer-isolation audit matrix would need to be re-run on Haiku before promoting it as the production model.

## What I recommend

Two options, your call:

**Option A — Accept Phase 2A.5 wins-on-paper, defer the model swap.**
Cache hits give instant responses for repeat clicks (this IS a major demo win — second click on any suggested-question button is essentially free). Progress narration and streaming improve perceived responsiveness even when latency is still 40s. Ship 2A.5 as-is and defer the model swap to a Phase 2A.6.

**Option B — Switch the model now.**
Change `MODEL_SPECIALIST = "claude-haiku-4-5-20251001"`, re-run the 20-call peer-isolation audit matrix (~$0.60 cost given Haiku's pricing), confirm no name leaks, ship 2A.5 + the model swap together. This is the version that actually hits the demo targets.

I have not made the model change pending your direction.
