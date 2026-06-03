"""Deterministic fake LLM for specialist tests.

``patch_llm(scripted_responses)`` monkey-patches
``src.agents.llm.call_with_tools`` to emit a scripted sequence of
``(response, telemetry)`` tuples. Each scripted response is a
``ScriptedResponse`` describing what content blocks to emit and the
stop_reason. The fake works with the real Anthropic content-block
shape so the specialist's content extraction and tool dispatch run
unchanged.

Usage in a test:

    with patch_llm(monkeypatch, [
        scripted_tool_use("query_tenant", {"sql": "SELECT ..."}),
        scripted_tool_use("read_lake_table",
                           {"table": "lake_category_metrics",
                            "filters": {"category": "DAIRY"}}),
        scripted_text(\"\"\"Your dairy price index is 1.062 above peers.

```render
{...}
```\"\"\"),
    ]):
        response = specialist.answer("...")
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

from src.agents import llm as L


@dataclass
class _TextBlock:
    type: str = "text"
    text: str = ""


@dataclass
class _ToolUseBlock:
    type: str = "tool_use"
    id: str = "tool_use_id"
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class _FakeResponse:
    content: list[Any]
    stop_reason: str           # "end_turn" or "tool_use"


@dataclass
class ScriptedResponse:
    """One scripted LLM turn. ``content_blocks`` are emitted as the
    response's content array; ``stop_reason`` decides whether the
    specialist loop continues."""
    content_blocks: list[Any]
    stop_reason: str


def scripted_text(text: str) -> ScriptedResponse:
    """A final assistant turn — pure text, stop_reason='end_turn'."""
    return ScriptedResponse(
        content_blocks=[_TextBlock(text=text)],
        stop_reason="end_turn",
    )


def scripted_tool_use(
    name: str, args: dict[str, Any], *, block_id: str = "tu_1",
) -> ScriptedResponse:
    """A tool-using turn — emit one tool_use block, stop_reason='tool_use'."""
    return ScriptedResponse(
        content_blocks=[_ToolUseBlock(id=block_id, name=name, input=args)],
        stop_reason="tool_use",
    )


def scripted_multi_tool_use(
    calls: list[tuple[str, dict[str, Any]]],
) -> ScriptedResponse:
    """A turn that emits multiple tool_use blocks in one assistant message."""
    blocks = [
        _ToolUseBlock(id=f"tu_{i}", name=name, input=args)
        for i, (name, args) in enumerate(calls)
    ]
    return ScriptedResponse(content_blocks=blocks, stop_reason="tool_use")


def scripted_emit_response(
    *,
    headline: str | None = None,
    evidence: list[str] | None = None,
    so_what: str | None = None,
    prose: str | None = None,                  # deprecated alias → headline
    chart_intent: dict[str, Any] | None = None,  # noqa: ARG001 — charts deferred
    claims: list[dict[str, Any]] | None = None,
    merge: dict[str, Any] | None = None,       # noqa: ARG001 — Fix 9 legacy
    caveats: list[str] | None = None,
) -> ScriptedResponse:
    """Helper for the Wave 3.5 normal exit path: model calls
    ``emit_response`` with the structured response args (``headline`` /
    ``evidence`` / ``so_what`` / ``claims`` / ``caveats``). The
    specialist's loop captures the args via ``_emit_args`` and finalizes
    via ``_finalize_from_emit``.

    Back-compat: the legacy ``prose=`` kwarg maps to ``headline`` (old
    fixtures become headline-only answers; the derived ``.prose``
    property reproduces the string so output assertions survive). The
    ``chart_intent=`` and ``merge=`` kwargs are accepted and ignored
    (charts are deferred to Wave 4; the two-query flow has no merge).
    """
    args: dict[str, Any] = {
        "headline": headline if headline is not None else (prose or ""),
        "evidence": evidence if evidence is not None else [],
        "claims": claims if claims is not None else [],
        "caveats": caveats if caveats is not None else [],
    }
    if so_what is not None:
        args["so_what"] = so_what
    return scripted_tool_use("emit_response", args)


def scripted_build_merge(
    *,
    on: list[str],
    own_value_col: str,
    peer_value_col: str,
    gap_op: str = "difference",
) -> ScriptedResponse:
    """Helper for the build_merge tool turn (Wave 3 Stage 6.5
    Fix 9a). Insert this in the script between the
    query_tenant + read_lake_table turns and the emit_response
    turn so the specialist's gate is satisfied and the merged
    frame becomes the result-of-record."""
    return scripted_tool_use("build_merge", {
        "on": on,
        "own_value_col": own_value_col,
        "peer_value_col": peer_value_col,
        "gap_op": gap_op,
    })


@contextmanager
def patch_llm(
    monkeypatch, script: list[ScriptedResponse],
) -> Iterator[None]:
    """Replace ``L.call_with_tools`` with a fake that drains
    ``script`` in order. Each call returns the next scripted response;
    running off the end of the script raises an AssertionError so
    tests fail loudly on unexpected extra turns."""
    iterator = iter(script)

    def _fake_call(*, model, system, tools, messages, max_tokens,
                   tool_choice=None):
        try:
            step = next(iterator)
        except StopIteration:
            raise AssertionError(
                "Fake LLM ran out of scripted responses. The specialist "
                "made more turns than the test anticipated."
            )
        resp = _FakeResponse(
            content=step.content_blocks,
            stop_reason=step.stop_reason,
        )
        tel = L.CallTelemetry(
            model=model,
            input_tokens=10,
            output_tokens=10,
            elapsed_sec=0.001,
        )
        return resp, tel

    monkeypatch.setattr(L, "call_with_tools", _fake_call)
    yield
