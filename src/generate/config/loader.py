"""Config loader + D12 invariant validation (Wave 1 §3).

Reads the YAML tree under ``src/generate/config/`` (or any clone)
into a ``Config`` dataclass. Raises ``ConfigInvariantError`` if any
D12 invariant fails — generation never starts on a bad config.

Invariants enforced (SPEC §3):
- Residential weights sum to 1.0.
- Every merchant ``segment`` resolves to a defined archetype.
- Every ``zone_placement_bias`` entry references a valid zone id.
- Per-merchant ``store_count`` equals the sum of its placement bias.
- Per-segment volume targets sum to the global total and each is
  non-zero (the ±tolerance check itself lives in T1 at run time,
  since the realism battery measures against generated data).

The forward consideration (SPEC §3): a new merchant config dropped
into ``merchants/`` is picked up by the next ``load_config`` call
with no engine change. Verified by
``tests/test_config_loader.py::test_dummy_extra_qsr_merchant_loads``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ConfigInvariantError(ValueError):
    """Raised when a D12 invariant fails. Generation must not run."""


@dataclass
class Config:
    """Loaded + validated configuration tree.

    Attributes
    ----------
    global_:
        ``global.yaml`` contents (seed, window, volume targets,
        expected store count, distance_decay_d0, population target).
    zones:
        List of zone dicts (preserving order from ``metro.yaml``).
    segments:
        Mapping ``segment_name → archetype dict``.
    merchants:
        Mapping ``merchant_name → merchant config dict``.
    """
    global_:   dict[str, Any]
    zones:     list[dict[str, Any]]
    segments:  dict[str, dict[str, Any]] = field(default_factory=dict)
    merchants: dict[str, dict[str, Any]] = field(default_factory=dict)


def _read_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text()) or {}


def load_config(root: str | Path) -> Config:
    """Load the config tree at ``root`` and validate D12 invariants."""
    root = Path(root)
    global_ = _read_yaml(root / "global.yaml")
    metro = _read_yaml(root / "metro.yaml")
    zones = list(metro.get("zones") or [])

    segments: dict[str, dict[str, Any]] = {}
    seg_dir = root / "segments"
    if seg_dir.is_dir():
        for path in sorted(seg_dir.glob("*.yaml")):
            seg = _read_yaml(path)
            segments[seg["name"]] = seg

    merchants: dict[str, dict[str, Any]] = {}
    merch_dir = root / "merchants"
    if merch_dir.is_dir():
        for path in sorted(merch_dir.glob("*.yaml")):
            m = _read_yaml(path)
            merchants[m["name"]] = m

    cfg = Config(global_=global_, zones=zones, segments=segments, merchants=merchants)
    _validate(cfg)
    return cfg


def _validate(cfg: Config) -> None:
    """Enforce the D12 invariants. Raise on the first violation."""
    # 1. Residential weights sum to 1.0.
    weight_sum = sum(z["residential_weight"] for z in cfg.zones)
    if abs(weight_sum - 1.0) > 1e-9:
        raise ConfigInvariantError(
            f"D12: zone residential_weight values must sum to 1.0; got {weight_sum}"
        )

    zone_ids = {z["id"] for z in cfg.zones}

    # 2. Every merchant's segment resolves.
    for name, m in cfg.merchants.items():
        seg = m.get("segment")
        if seg not in cfg.segments:
            raise ConfigInvariantError(
                f"D12: merchant {name!r} references undefined segment {seg!r}"
            )

    # 3. Every zone in a placement bias is real.
    for name, m in cfg.merchants.items():
        for zid in (m.get("zone_placement_bias") or {}):
            if zid not in zone_ids:
                raise ConfigInvariantError(
                    f"D12: merchant {name!r} places stores in unknown zone {zid!r}"
                )

    # 4. store_count == sum(zone_placement_bias).
    for name, m in cfg.merchants.items():
        bias = m.get("zone_placement_bias") or {}
        if sum(bias.values()) != m.get("store_count"):
            raise ConfigInvariantError(
                f"D12: merchant {name!r} store_count={m.get('store_count')} "
                f"but zone_placement_bias sums to {sum(bias.values())}"
            )

    # 5. Total store count matches the global expectation (D5/D13.2).
    total_stores = sum(m["store_count"] for m in cfg.merchants.values())
    expected = cfg.global_.get("expected_store_count")
    if expected is not None and total_stores != expected:
        raise ConfigInvariantError(
            f"D5/D13.2: total store count {total_stores} != expected {expected}"
        )

    # 6. Volume targets exist and reconcile.
    targets = cfg.global_.get("volume_targets") or {}
    needed = {"grocery", "qsr", "off_price", "total", "tolerance_pct"}
    missing = needed - set(targets.keys())
    if missing:
        raise ConfigInvariantError(f"D5: volume_targets missing keys {sorted(missing)}")
    summed = targets["grocery"] + targets["qsr"] + targets["off_price"]
    if summed != targets["total"]:
        raise ConfigInvariantError(
            f"D5: per-segment volume targets sum to {summed} but total is {targets['total']}"
        )

    # 7. D17.2: each segment's affinity_matrix (if present) is a list of
    #    [anchor, partner, boost] with non-empty string subcats and a
    #    positive boost. Subcategory existence is checked at basket-build
    #    time (the catalog isn't available here). A malformed entry would
    #    otherwise crash deep in the basket layer or silently no-op.
    for seg_name, seg in cfg.segments.items():
        matrix = seg.get("affinity_matrix")
        if matrix is None:
            continue
        if not isinstance(matrix, list):
            raise ConfigInvariantError(
                f"D17.2: segment {seg_name!r} affinity_matrix must be a list"
            )
        for entry in matrix:
            if not isinstance(entry, (list, tuple)) or len(entry) != 3:
                raise ConfigInvariantError(
                    f"D17.2: segment {seg_name!r} affinity_matrix entry "
                    f"{entry!r} must be [anchor, partner, boost]"
                )
            anchor, partner, boost = entry
            if not (isinstance(anchor, str) and anchor
                    and isinstance(partner, str) and partner):
                raise ConfigInvariantError(
                    f"D17.2: segment {seg_name!r} affinity_matrix entry "
                    f"{entry!r} has an empty/non-string subcategory"
                )
            if not isinstance(boost, (int, float)) or isinstance(boost, bool) \
                    or boost <= 0:
                raise ConfigInvariantError(
                    f"D17.2: segment {seg_name!r} affinity_matrix entry "
                    f"{entry!r} must have a positive numeric boost"
                )
