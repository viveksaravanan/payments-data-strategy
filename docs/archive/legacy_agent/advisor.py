"""Merchant Advisor agent.

Tools: ``schema_info``, ``query_tenant`` (merchant context injected by the
runner), ``query_lake``, ``chart_spec``. Loop is hard-capped at MAX_TURNS = 6.
``mock=True`` short-circuits the API for offline demos.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from src.generate.parameters import MERCHANT_CONFIGS

from . import tools as T

# Load `.env` once at import so a bare `MerchantAdvisor("KRG")` works in any
# entrypoint (CLI, dashboard, tests) without per-caller setup.
load_dotenv()

MAX_TURNS = 6
MODEL = "claude-opus-4-7"

PROMPT_PATH = Path(__file__).parent / "prompts" / "advisor.md"
PROMPT_TEMPLATE = PROMPT_PATH.read_text()

# `merchant_id` -> friendly name. Built from the canonical configs.
_NAME_BY_ID = {cfg["merchant_id"]: cfg["name"] for cfg in MERCHANT_CONFIGS.values()}


@dataclass
class AgentResponse:
    answer: str
    sql: list[dict[str, Any]] = field(default_factory=list)  # [{tool, query, rows}]
    chart: dict[str, Any] | None = None
    last_table: dict[str, Any] | None = None
    turns: int = 0
    converged: bool = True


def _render_prompt(merchant_id: str) -> str:
    name = _NAME_BY_ID.get(merchant_id, merchant_id)
    return (
        PROMPT_TEMPLATE
        .replace("{{current_merchant_id}}", merchant_id)
        .replace("{{current_merchant_name}}", name)
        .replace("{{current_merchant}}", name)
    )


class MerchantAdvisor:
    """Runs the Merchant Advisor loop for a fixed merchant context."""

    def __init__(
        self,
        current_merchant_id: str,
        mock: bool = False,
        client: Any = None,
        max_turns: int = MAX_TURNS,
    ) -> None:
        if current_merchant_id not in _NAME_BY_ID:
            raise ValueError(f"Unknown merchant_id: {current_merchant_id}")
        self.merchant_id = current_merchant_id
        self.merchant_name = _NAME_BY_ID[current_merchant_id]
        self.system_prompt = _render_prompt(current_merchant_id)
        self.tools = T.TOOLS_MERCHANT
        self.max_turns = max_turns
        self.mock = mock
        if client is None and not mock:
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                raise RuntimeError(
                    "ANTHROPIC_API_KEY is not set. Add it to .env "
                    "(see .env.example) or pass mock=True for offline use."
                )
            import anthropic  # imported lazily so the module is usable without the SDK
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
        if name == "query_tenant":
            return T.query_tenant(args["query"], current_merchant=self.merchant_id)
        if name == "query_lake":
            # Phase 5b: pass viewing_merchant_id so the runner CTE-wraps
            # the agent's SQL into the v2.5 virtual lake views and
            # excludes the viewing merchant's own data.
            return T.query_lake(
                args["query"], viewing_merchant_id=self.merchant_id
            )
        if name == "chart_spec":
            return T.chart_spec(**args)
        raise ValueError(f"Unknown tool: {name}")

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

                if block.name == "query_tenant" and not is_error:
                    sql_log.append({"tool": "tenant", "query": block.input["query"], "rows": result})
                    last_table = result
                elif block.name == "query_lake" and not is_error:
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
            converged=False,
        )

    # ---- Mock mode (used when ANTHROPIC_API_KEY is missing or for demo safety) ----

    def _mock_response(self, question: str) -> AgentResponse:
        """Canned end-to-end mock: one tenant query (own DAIRY revenue),
        one lake query (peer DAIRY line counts via the v2.5 view-builder).
        Demonstrates the merchant advisor's full tool surface."""
        tenant_sql = (
            "SELECT p.category, COUNT(*) AS lines, "
            "ROUND(SUM(i.line_total), 2) AS revenue "
            "FROM tenant_transaction_items i "
            "JOIN tenant_transactions t ON t.txn_id = i.txn_id "
            "JOIN tenant_products p ON p.sku = i.sku "
            f"WHERE t.merchant_id = '{self.merchant_id}' "
            "AND p.category = 'DAIRY' "
            "GROUP BY p.category"
        )
        lake_sql = (
            "SELECT peer_id, peer_segment, COUNT(*) AS lines, "
            "ROUND(AVG(unit_price), 2) AS avg_unit_price "
            "FROM lake_transactions "
            "WHERE category = 'DAIRY' "
            "GROUP BY peer_id, peer_segment "
            "ORDER BY peer_id"
        )

        sql_log: list[dict[str, Any]] = []
        last_table: dict[str, Any] | None = None
        own_revenue: float | None = None

        try:
            tenant_rows = T.query_tenant(tenant_sql, current_merchant=self.merchant_id)
            sql_log.append(
                {"tool": "tenant", "query": tenant_sql, "rows": tenant_rows}
            )
            last_table = tenant_rows
            if tenant_rows.get("rows"):
                own_revenue = tenant_rows["rows"][0][2]
        except Exception:  # noqa: BLE001 — mock falls back to placeholder
            tenant_rows = {
                "columns": ["category", "lines", "revenue"],
                "rows": [], "row_count": 0, "truncated": False,
            }

        try:
            lake_rows = T.query_lake(
                lake_sql, viewing_merchant_id=self.merchant_id
            )
            sql_log.append({"tool": "lake", "query": lake_sql, "rows": lake_rows})
            last_table = lake_rows
        except Exception:  # noqa: BLE001
            pass

        rev_str = f"${own_revenue:,.2f}" if own_revenue is not None else "n/a"
        return AgentResponse(
            answer=(
                f"[mock] {self.merchant_name} answer for: {question!r}. "
                f"Own DAIRY revenue over the window: {rev_str}. "
                f"Peer DAIRY line counts and average unit prices from the "
                f"lake (see SQL)."
            ),
            sql=sql_log,
            chart=None,
            last_table=last_table,
            turns=1,
        )
