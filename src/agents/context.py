"""Wave 3 ``MerchantContext`` — viewer binding for a specialist call.

Immutable per-viewer record. Specialists construct one per session;
``lake_tools`` reads ``viewing_merchant_id`` for every tool call so
the LLM cannot supply a different merchant_id.

The v3 ``MerchantContext`` exposed bound ``query_tenant`` /
``query_lake`` / ``schema_info`` / ``make_chart`` methods, all of
which proxied into ``src.agents.tools`` (now retired). Wave 3
specialists call ``src.agents.lake_tools`` directly with the
viewer pulled from the context — no proxy layer.

Merchant metadata is sourced from the Wave 1 config tree at
``src/generate/config/merchants/``.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.generate.config.loader import load_config


CONFIG_ROOT = Path(__file__).resolve().parents[2] / "src" / "generate" / "config"


def _load_merchant_metadata() -> tuple[dict[str, str], dict[str, str]]:
    """Build ``{banner_code: name}`` and ``{banner_code: segment}``
    dicts from the merchants YAML tree. Called lazily on the first
    ``for_merchant`` call so test fixtures that don't need merchant
    metadata never pay the load cost."""
    cfg = load_config(CONFIG_ROOT)
    name_by_id = {
        m["banner_code"]: m["name"]
        for m in cfg.merchants.values()
    }
    seg_by_id = {
        m["banner_code"]: m["segment"]
        for m in cfg.merchants.values()
    }
    return name_by_id, seg_by_id


_metadata_cache: tuple[dict[str, str], dict[str, str]] | None = None


def _merchant_metadata() -> tuple[dict[str, str], dict[str, str]]:
    global _metadata_cache
    if _metadata_cache is None:
        _metadata_cache = _load_merchant_metadata()
    return _metadata_cache


@dataclass(frozen=True)
class MerchantContext:
    """Per-viewer context. Immutable — switching merchants in the
    dashboard creates a fresh instance."""
    viewing_merchant_id:      str
    viewing_merchant_name:    str
    viewing_merchant_segment: str

    @classmethod
    def for_merchant(cls, merchant_id: str) -> "MerchantContext":
        name_by_id, seg_by_id = _merchant_metadata()
        if merchant_id not in name_by_id:
            raise ValueError(
                f"Unknown merchant_id: {merchant_id!r}. "
                f"Expected one of {sorted(name_by_id)}."
            )
        return cls(
            viewing_merchant_id=merchant_id,
            viewing_merchant_name=name_by_id[merchant_id],
            viewing_merchant_segment=seg_by_id[merchant_id],
        )
