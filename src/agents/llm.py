"""Anthropic client wrapper for Phase 2 specialist agents.

Single shared client with:
  - is_available() check (key present)
  - Retry on transient errors (429 / 503 / connection)
  - Timeout
  - Per-session token + cost telemetry (module-global counter, the
    dashboard footer reads from it)
  - Model constants per the Phase 5.1.8b reversion:
      MODEL_SPECIALIST = "claude-haiku-4-5-20251001"
      MODEL_ROUTER     = "claude-haiku-4-5-20251001"
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Callable

from dotenv import load_dotenv

load_dotenv()


# Model strings.
#
# D25.8 model configuration:
#   * Orchestrator/router: always Haiku (`claude-haiku-4-5-20251001`)
#     — routing is a cheap classification, no reasoning depth needed.
#   * Specialists + Advisor: Haiku by default, Sonnet-switchable via
#     the SPECIALIST_MODEL env var. Wave 3 is the "answer quality
#     matters" wave; the config knob lets you trade cost for
#     reasoning quality on the agents that do the chart-intent +
#     claims + validation-aware prose work, without touching the
#     router. Default Haiku keeps demo cost low; flip to Sonnet
#     when answer quality needs it.
#
# Wave 3 deprecates the v3 fixed-Haiku-for-all configuration; the
# router stays hardcoded but specialists read SPECIALIST_MODEL at
# import time. Allowed values: any model identifier in _PRICING.
MODEL_ROUTER = "claude-haiku-4-5-20251001"
MODEL_SPECIALIST = os.environ.get(
    "SPECIALIST_MODEL", "claude-haiku-4-5-20251001",
)

# Anthropic pricing (per Mtoken). Sonnet 4.6: $3 in / $15 out.
# Haiku 4.5: $1 in / $5 out. Used only for the in-dashboard cost
# footer — not billing. Keyed by model identifier (not by role
# constant) so the right rate is applied regardless of which role
# each model fills.
_PRICING = {
    "claude-sonnet-4-6":          (3.0, 15.0),
    "claude-haiku-4-5-20251001":  (1.0,  5.0),
}

MAX_TIMEOUT_SEC = 60
MAX_RETRIES     = 3
RETRY_BASE_DELAY_SEC = 1.0


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------

@dataclass
class CallTelemetry:
    """Per-call accounting."""
    model:         str
    input_tokens:  int
    output_tokens: int
    elapsed_sec:   float

    @property
    def cost_usd(self) -> float:
        rate_in, rate_out = _PRICING.get(self.model, (0.0, 0.0))
        return (
            self.input_tokens  / 1_000_000 * rate_in +
            self.output_tokens / 1_000_000 * rate_out
        )


# Module-global session totals. Reset via `reset_session_totals()`.
_SESSION_INPUT_TOKENS  = 0
_SESSION_OUTPUT_TOKENS = 0
_SESSION_COST_USD      = 0.0
_SESSION_CALL_COUNT    = 0


def session_totals() -> dict[str, Any]:
    return {
        "calls":         _SESSION_CALL_COUNT,
        "input_tokens":  _SESSION_INPUT_TOKENS,
        "output_tokens": _SESSION_OUTPUT_TOKENS,
        "cost_usd":      round(_SESSION_COST_USD, 4),
    }


def reset_session_totals() -> None:
    global _SESSION_INPUT_TOKENS, _SESSION_OUTPUT_TOKENS, _SESSION_COST_USD, _SESSION_CALL_COUNT
    _SESSION_INPUT_TOKENS = 0
    _SESSION_OUTPUT_TOKENS = 0
    _SESSION_COST_USD = 0.0
    _SESSION_CALL_COUNT = 0


def _record(t: CallTelemetry) -> None:
    global _SESSION_INPUT_TOKENS, _SESSION_OUTPUT_TOKENS, _SESSION_COST_USD, _SESSION_CALL_COUNT
    _SESSION_INPUT_TOKENS  += t.input_tokens
    _SESSION_OUTPUT_TOKENS += t.output_tokens
    _SESSION_COST_USD      += t.cost_usd
    _SESSION_CALL_COUNT    += 1


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class LLMNotConfigured(RuntimeError):
    """Raised when ANTHROPIC_API_KEY is missing. The dashboard catches
    this and falls back to hardcoded handlers silently (no error banner)."""


def is_available() -> bool:
    """True iff an API key is present. The dashboard uses this to
    decide whether to attempt LLM dispatch."""
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


_CLIENT = None


def _client() -> "Any":
    """Lazy singleton. Importing anthropic at module load time would
    require the SDK even in test environments that don't use it."""
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise LLMNotConfigured(
            "ANTHROPIC_API_KEY not set. Add it to .env or unset features "
            "that depend on the LLM."
        )
    import anthropic  # noqa: PLC0415  — lazy
    _CLIENT = anthropic.Anthropic(api_key=key, timeout=MAX_TIMEOUT_SEC)
    return _CLIENT


# ---------------------------------------------------------------------------
# Call
# ---------------------------------------------------------------------------

def call_with_tools(
    model: str,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    max_tokens: int = 2048,
    tool_choice: dict[str, Any] | None = None,
) -> tuple[Any, CallTelemetry]:
    """Wrap `client.messages.create` with retry + telemetry.

    Returns `(response, telemetry)`. The caller drives the tool loop;
    this function is one model invocation. Retries on transient errors
    (rate limit, transient connection) up to MAX_RETRIES.

    ``tool_choice`` is forwarded to the Anthropic SDK when provided —
    e.g. ``{"type": "any"}`` forces the model to call SOME tool every
    turn (preventing free-text exits), or
    ``{"type": "tool", "name": "emit_response"}`` forces a specific
    tool. ``None`` (default) lets the model choose freely.
    """
    c = _client()
    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES):
        t0 = time.monotonic()
        try:
            kwargs: dict[str, Any] = dict(
                model=model, system=system, tools=tools,
                messages=messages, max_tokens=max_tokens,
            )
            if tool_choice is not None:
                kwargs["tool_choice"] = tool_choice
            resp = c.messages.create(**kwargs)
            elapsed = time.monotonic() - t0
            usage = getattr(resp, "usage", None)
            tel = CallTelemetry(
                model=model,
                input_tokens=getattr(usage, "input_tokens", 0)  if usage else 0,
                output_tokens=getattr(usage, "output_tokens", 0) if usage else 0,
                elapsed_sec=elapsed,
            )
            _record(tel)
            return resp, tel
        except Exception as exc:  # noqa: BLE001 — retry envelope
            last_err = exc
            # Detect transient: rate-limit, transient server, or
            # connection-y errors. Class names cover the Anthropic SDK
            # surface without taking a runtime dependency on its types.
            name = type(exc).__name__.lower()
            transient = (
                "ratelimit" in name or
                "503"       in str(exc).lower() or
                "overload"  in name or
                "timeout"   in name or
                "connection" in name
            )
            if not transient or attempt == MAX_RETRIES - 1:
                raise
            time.sleep(RETRY_BASE_DELAY_SEC * (2 ** attempt))
    # Unreachable, but keep mypy happy.
    raise last_err if last_err else RuntimeError("LLM call failed")


def call_with_tools_streaming(
    model: str,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    max_tokens: int = 2048,
    on_text_delta: "Callable[[str], None] | None" = None,
) -> tuple[Any, CallTelemetry]:
    """Streaming variant of `call_with_tools`. Fires `on_text_delta`
    for each text chunk as it arrives, then returns the assembled
    final message (same shape as the non-streaming response).

    The assembled message exposes `.content`, `.stop_reason`, and
    `.usage` exactly like `messages.create`. Telemetry is recorded
    from `final.usage`.

    Tool-use blocks stream too but we don't emit them — only text
    deltas are user-facing during streaming. The final message
    carries the complete tool_use block for the loop to dispatch.
    """
    c = _client()
    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES):
        t0 = time.monotonic()
        try:
            with c.messages.stream(
                model=model, system=system, tools=tools,
                messages=messages, max_tokens=max_tokens,
            ) as stream:
                if on_text_delta is not None:
                    for text in stream.text_stream:
                        if text:
                            on_text_delta(text)
                else:
                    # Drain without callback; final message still
                    # assembles correctly.
                    for _ in stream.text_stream:
                        pass
                final = stream.get_final_message()
            elapsed = time.monotonic() - t0
            usage = getattr(final, "usage", None)
            tel = CallTelemetry(
                model=model,
                input_tokens=getattr(usage, "input_tokens", 0)  if usage else 0,
                output_tokens=getattr(usage, "output_tokens", 0) if usage else 0,
                elapsed_sec=elapsed,
            )
            _record(tel)
            return final, tel
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            name = type(exc).__name__.lower()
            transient = (
                "ratelimit" in name or
                "503"       in str(exc).lower() or
                "overload"  in name or
                "timeout"   in name or
                "connection" in name
            )
            if not transient or attempt == MAX_RETRIES - 1:
                raise
            time.sleep(RETRY_BASE_DELAY_SEC * (2 ** attempt))
    raise last_err if last_err else RuntimeError("LLM streaming call failed")
