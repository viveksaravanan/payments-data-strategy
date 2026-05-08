# Agents

Two agents share a single tool-use loop pattern.

- **`advisor.py`** — Merchant Advisor. Tools: `schema_info`, `query_tenant`, `query_lake`, `chart_spec`. Used by Kroger / Taco Bell / TJ Maxx roles in the dashboard. Receives `current_merchant` context.
- **`analyst.py`** — Network Analyst. Tools: `schema_info`, `query_lake`, `chart_spec` only. Used by the Network Analyst role. No tenant access.
- **`forecaster.py`** — (stretch) Demand Forecasting. Build only if everything else is done.

The shared loop is in each agent file rather than abstracted out — the duplication is small and keeps the loop legible.

## Hard rules

- **`query_tenant` enforces tenant isolation.** Every query must include `WHERE merchant_id = '<current_merchant>'` (or join on `merchants` with the same predicate). Queries lacking the predicate are rejected before execution. The check lives in `tools.query_tenant`. If you modify it, update `tests/test_agents.py::test_tenant_isolation`.
- **All SQL tools are SELECT-only.** Reject anything that is not a single SELECT statement before executing — regex check, before any DB connection. Never trust the model to self-restrict.
- **`MAX_TURNS = 6`.** Hard cap. If the loop hasn't terminated, return what the agent has and surface "didn't converge" in the dashboard. Don't raise without adding a regression test.
- **Final answers must include the SQL.** The dashboard renders it in an expander. The agent's answer is not trustworthy without it.

## Style

- **Prompts live in `prompts/*.md`**, never as Python strings. Edit them directly. Load with `Path("prompts/advisor.md").read_text()` at module import. No f-string interpolation in prompts — pass dynamic context (current_merchant, today's date) as a separate user message.
- **Tools are real-Python, not LLM-described.** A tool is a function that the runner invokes when the model emits a `tool_use` block; the runner appends the result as a `tool_result`. No wrapper frameworks.
- **Keep the loop boring.** The hardest debugging in this code happens when the loop does something clever. It shouldn't.

## Models

Default to `claude-opus-4-7`. For unit tests against the real API (rare), use `claude-haiku-4-5` to keep cost down. Most unit tests should mock the client — see `tests/test_agents.py` for the pattern.

## Mock mode

Each agent supports `--mock` flag (or `mock=True` constructor arg). In mock mode, the agent skips API calls and returns canned responses. This is the demo safety net: if the API key is missing or there's an outage at 4:55 PM, the dashboard still works.

The canned responses live as constants in each agent file. Update them when you update the demo questions.
