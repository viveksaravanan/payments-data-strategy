"""MerchantContext — single source of truth for "who's viewing" inside
a specialist invocation.

The dashboard creates one `MerchantContext` per (viewer × session) and
passes it to each specialist. Every tool a specialist calls goes
through the context, which bakes the viewing_merchant_id into the call.
The LLM cannot pass a different merchant_id; the tool schemas don't
expose one.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.agents import tools as T
from src.generate.parameters import MERCHANT_CONFIGS

_NAME_BY_ID = {cfg["merchant_id"]: cfg["name"]    for cfg in MERCHANT_CONFIGS.values()}
_SEG_BY_ID  = {cfg["merchant_id"]: cfg["segment"] for cfg in MERCHANT_CONFIGS.values()}


@dataclass(frozen=True)
class MerchantContext:
    """Per-viewer context. Immutable — switching merchants in the
    dashboard creates a fresh instance."""
    viewing_merchant_id:      str
    viewing_merchant_name:    str
    viewing_merchant_segment: str

    # ---- Factory --------------------------------------------------------

    @classmethod
    def for_merchant(cls, merchant_id: str) -> "MerchantContext":
        if merchant_id not in _NAME_BY_ID:
            raise ValueError(
                f"Unknown merchant_id: {merchant_id!r}. "
                f"Expected one of {sorted(_NAME_BY_ID)}."
            )
        return cls(
            viewing_merchant_id=merchant_id,
            viewing_merchant_name=_NAME_BY_ID[merchant_id],
            viewing_merchant_segment=_SEG_BY_ID[merchant_id],
        )

    # ---- Tool wrappers --------------------------------------------------
    #
    # Each wrapper validates + delegates to the underlying tool in
    # `src/agents/tools.py`, which already enforces SELECT-only +
    # merchant-id predicate (tenant) + lake view-builder wrapping (lake).
    # The point of routing through MerchantContext is that the merchant
    # binding is fixed; the LLM cannot supply it as an argument.

    def schema_info(self) -> dict[str, Any]:
        return T.schema_info()

    def query_tenant(self, query: str) -> dict[str, Any]:
        return T.query_tenant(
            query,
            current_merchant=self.viewing_merchant_id,
        )

    def query_lake(self, query: str) -> dict[str, Any]:
        return T.query_lake(
            query,
            viewing_merchant_id=self.viewing_merchant_id,
        )

    def make_chart(self, spec: dict[str, Any]):
        """Build a Plotly Figure from a structured spec. Returns the
        actual `go.Figure`; the specialist caches it for the final
        response."""
        return T.make_chart(spec)
