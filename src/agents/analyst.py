"""Network Analyst agent.

Lake-only. Tools: ``schema_info``, ``query_lake``, ``chart_spec``. Same loop
shape as the advisor, intentionally duplicated rather than abstracted — keeps
both files legible and avoids a shared abstraction the demo doesn't need.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from . import tools as T
from .advisor import AgentResponse, MAX_TURNS, MODEL  # reuse the response shape

# Load `.env` once at import so a bare `NetworkAnalyst()` works in any
# entrypoint without per-caller setup.
load_dotenv()

PROMPT_PATH = Path(__file__).parent / "prompts" / "analyst.md"
SYSTEM_PROMPT = PROMPT_PATH.read_text()


class NetworkAnalyst:
    """Runs the Network Analyst loop. No tenant access."""

    def __init__(
        self,
        mock: bool = False,
        client: Any = None,
        max_turns: int = MAX_TURNS,
    ) -> None:
        self.system_prompt = SYSTEM_PROMPT
        self.tools = T.TOOLS_ANALYST
        self.max_turns = max_turns
        self.mock = mock
        if client is None and not mock:
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                raise RuntimeError(
                    "ANTHROPIC_API_KEY is not set. Add it to .env "
                    "(see .env.example) or pass mock=True for offline use."
                )
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
        self.client = client

    # ---- Public API ----

    def ask(self, question: str) -> AgentResponse:
        if self.mock:
            return self._mock_response(question)
        return self._run_loop(question)

    # ---- Internals ----

    def _dispatch(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "schema_info":
            return T.schema_info()
        if name == "query_lake":
            return T.query_lake(args["query"])
        if name == "chart_spec":
            return T.chart_spec(**args)
        # `query_tenant` is intentionally not mapped — analyst has no tenant access.
        raise ValueError(f"Unknown or forbidden tool for analyst: {name}")

    def _run_loop(self, question: str) -> AgentResponse:
        messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
        sql_log: list[dict[str, Any]] = []
        chart: dict[str, Any] | None = None
        last_table: dict[str, Any] | None = None

        for turn in range(self.max_turns):
            resp = self.client.messages.create(
                model=MODEL,
                system=self.system_prompt,
                tools=self.tools,
                messages=messages,
                max_tokens=2048,
            )
            messages.append({"role": "assistant", "content": resp.content})

            if resp.stop_reason != "tool_use":
                text_parts = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
                return AgentResponse(
                    answer="".join(text_parts),
                    sql=sql_log,
                    chart=chart,
                    last_table=last_table,
                    turns=turn + 1,
                )

            tool_results: list[dict[str, Any]] = []
            for block in resp.content:
                if getattr(block, "type", "") != "tool_use":
                    continue
                try:
                    result = self._dispatch(block.name, dict(block.input or {}))
                    is_error = False
                except Exception as exc:
                    result = {"error": str(exc)}
                    is_error = True

                if block.name == "query_lake" and not is_error:
                    sql_log.append({"tool": "lake", "query": block.input["query"], "rows": result})
                    last_table = result
                elif block.name == "chart_spec" and not is_error:
                    chart = result

                tool_results.append({
                    "type":        "tool_result",
                    "tool_use_id": block.id,
                    "content":     json.dumps(result, default=str),
                    "is_error":    is_error,
                })
            messages.append({"role": "user", "content": tool_results})

        return AgentResponse(
            answer="(agent reached MAX_TURNS without a final answer)",
            sql=sql_log,
            chart=chart,
            last_table=last_table,
            turns=self.max_turns,
        )

    # ---- Mock ----

    def _mock_response(self, question: str) -> AgentResponse:
        sample_sql = (
            "SELECT m.name, COUNT(*) AS txns "
            "FROM lake_transactions t JOIN merchants m USING(merchant_id) "
            "GROUP BY m.merchant_id ORDER BY txns DESC LIMIT 10"
        )
        try:
            rows = T.query_lake(sample_sql)
        except Exception:
            rows = {"columns": ["name", "txns"], "rows": [], "row_count": 0, "truncated": False}

        return AgentResponse(
            answer=(
                f"[mock] Network Analyst answer for: {question!r}. "
                f"Panel-wide transaction split available — see SQL."
            ),
            sql=[{"tool": "lake", "query": sample_sql, "rows": rows}],
            chart=None,
            last_table=rows,
            turns=1,
        )
