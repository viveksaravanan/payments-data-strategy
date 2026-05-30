"""Tests for src/generate/config/loader.py (Wave 1 Stage 3).

Covers the D12 invariants enumerated in SPEC §3:
- residential weights sum to 1.0
- every merchant `segment` resolves to a defined archetype
- every `zone_placement_bias` entry references a valid zone id
- store counts match placement-bias sums per merchant
- total store count is 29 (D5 row totals; supersedes the typo'd
  "Total: 24" line per session AskUserQuestion clarification)
- per-segment volume targets are within the D5 ±10% band

Plus the SPEC §3 forward consideration: adding a dummy extra QSR
merchant from config must load and validate without engine changes.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.generate.config.loader import (
    Config,
    ConfigInvariantError,
    load_config,
)

CONFIG_ROOT = Path(__file__).resolve().parents[1] / "src" / "generate" / "config"


@pytest.fixture(scope="module")
def cfg() -> Config:
    return load_config(CONFIG_ROOT)


# ----- Basic loading -------------------------------------------------

def test_loads_global(cfg: Config) -> None:
    assert cfg.global_["seed"] == 42
    assert cfg.global_["window"]["days"] == 90
    assert cfg.global_["expected_store_count"] == 29


def test_loads_eight_zones(cfg: Config) -> None:
    assert len(cfg.zones) == 8
    zone_ids = {z["id"] for z in cfg.zones}
    assert zone_ids == {
        "center_city", "dilworth", "ballantyne", "noda",
        "university_city", "eastway", "matthews", "cabarrus_edge",
    }


def test_loads_three_segments(cfg: Config) -> None:
    assert set(cfg.segments.keys()) == {"grocery", "qsr", "off_price"}


def test_loads_five_merchants(cfg: Config) -> None:
    assert set(cfg.merchants.keys()) == {
        "Kroger", "Acme", "Winn-Dixie", "Taco Bell", "TJ Maxx",
    }


def test_segment_distance_decay_betas(cfg: Config) -> None:
    """D13.4: grocery 2.0, qsr 2.2, off_price 1.3."""
    assert cfg.segments["grocery"]["distance_decay"]["beta"] == 2.0
    assert cfg.segments["qsr"]["distance_decay"]["beta"] == 2.2
    assert cfg.segments["off_price"]["distance_decay"]["beta"] == 1.3


# ----- D12 invariants ------------------------------------------------

def test_residential_weights_sum_to_one(cfg: Config) -> None:
    total = sum(z["residential_weight"] for z in cfg.zones)
    assert total == pytest.approx(1.0, abs=1e-9)


def test_every_merchant_segment_resolves(cfg: Config) -> None:
    for name, m in cfg.merchants.items():
        assert m["segment"] in cfg.segments, \
            f"{name} references undefined segment {m['segment']!r}"


def test_every_zone_placement_references_valid_zone(cfg: Config) -> None:
    zone_ids = {z["id"] for z in cfg.zones}
    for name, m in cfg.merchants.items():
        for zid in m["zone_placement_bias"]:
            assert zid in zone_ids, \
                f"{name} places stores in unknown zone {zid!r}"


def test_store_counts_match_placement_sums(cfg: Config) -> None:
    for name, m in cfg.merchants.items():
        placement_sum = sum(m["zone_placement_bias"].values())
        assert placement_sum == m["store_count"], (
            f"{name}: store_count={m['store_count']} but "
            f"zone_placement_bias sums to {placement_sum}"
        )


def test_total_store_count_is_29(cfg: Config) -> None:
    """Per AskUserQuestion resolution: D5's 'Total: 24' is an
    arithmetic typo; the D13.2 placement matrix sums to 29 and is
    authoritative (5+5+5+9+5)."""
    total = sum(m["store_count"] for m in cfg.merchants.values())
    assert total == 29
    assert total == cfg.global_["expected_store_count"]


def test_volume_targets_satisfy_d5_band(cfg: Config) -> None:
    """D5 band: per-segment totals × tolerance must contain the
    declared per-segment targets. SPEC T1 enforces this at run
    time; here we sanity-check the targets exist and sum."""
    targets = cfg.global_["volume_targets"]
    assert targets["grocery"] == 1_080_000
    assert targets["qsr"] == 365_000
    assert targets["off_price"] == 225_000
    assert targets["total"] == 1_670_000
    assert (
        targets["grocery"] + targets["qsr"] + targets["off_price"]
        == targets["total"]
    )
    assert targets["tolerance_pct"] == 10


# ----- Invariant failure modes (negative tests) ---------------------

def test_load_fails_on_bad_residential_weight_sum(tmp_path: Path) -> None:
    """A config whose residential weights don't sum to 1.0 raises."""
    _clone_config_with_zone_weight_override(tmp_path, override=0.9)
    with pytest.raises(ConfigInvariantError, match="residential_weight"):
        load_config(tmp_path)


def test_load_fails_on_undefined_segment(tmp_path: Path) -> None:
    """A merchant referencing an unknown segment archetype raises."""
    _clone_config_with_merchant_segment_override(tmp_path, segment="bogus")
    with pytest.raises(ConfigInvariantError, match="segment"):
        load_config(tmp_path)


def test_load_fails_on_unknown_zone_in_placement(tmp_path: Path) -> None:
    """A merchant placing a store in an unknown zone raises."""
    _clone_config_with_merchant_zone_override(tmp_path, zone="atlantis")
    with pytest.raises(ConfigInvariantError, match="zone"):
        load_config(tmp_path)


# ----- D12 forward consideration: dummy extra QSR merchant ----------

def test_dummy_extra_qsr_merchant_loads(tmp_path: Path) -> None:
    """SPEC §3 forward consideration: adding a 6th merchant must be
    a config addition only — no engine code changes. Drop in a new
    QSR config and bump the global expected_store_count (config-only
    edits) and confirm the loader picks it up + invariants hold."""
    _clone_config(tmp_path)
    # Add a new QSR merchant.
    new_merchant = tmp_path / "merchants" / "burger_shack.yaml"
    new_merchant.write_text(
        "name: \"Burger Shack\"\n"
        "banner_code: BSH\n"
        "segment: qsr\n"
        "positioning_tier: mainstream\n"
        "store_count: 3\n"
        "zone_placement_bias:\n"
        "  matthews: 2\n"
        "  ballantyne: 1\n"
    )
    # Bump the global expected store count to reflect the new footprint.
    # Touching this is part of the "config-only" change — no engine code
    # is altered.
    global_path = tmp_path / "global.yaml"
    text = global_path.read_text()
    text = text.replace("expected_store_count: 29", "expected_store_count: 32")
    global_path.write_text(text)

    cfg = load_config(tmp_path)
    assert "Burger Shack" in cfg.merchants
    assert cfg.merchants["Burger Shack"]["store_count"] == 3
    assert sum(m["store_count"] for m in cfg.merchants.values()) == 32


# ----- helpers for negative tests -----------------------------------

def _clone_config(dest: Path) -> None:
    """Copy the real config tree to `dest` for mutation in tests."""
    import shutil
    shutil.copytree(CONFIG_ROOT, dest, dirs_exist_ok=True)


def _clone_config_with_zone_weight_override(dest: Path, override: float) -> None:
    _clone_config(dest)
    metro_path = dest / "metro.yaml"
    text = metro_path.read_text()
    # Bend Center City's residential_weight to break the sum invariant.
    text = text.replace("residential_weight: 0.08", f"residential_weight: {override}", 1)
    metro_path.write_text(text)


def _clone_config_with_merchant_segment_override(dest: Path, segment: str) -> None:
    _clone_config(dest)
    kroger_path = dest / "merchants" / "kroger.yaml"
    text = kroger_path.read_text()
    text = text.replace("segment: grocery", f"segment: {segment}", 1)
    kroger_path.write_text(text)


def _clone_config_with_merchant_zone_override(dest: Path, zone: str) -> None:
    _clone_config(dest)
    kroger_path = dest / "merchants" / "kroger.yaml"
    text = kroger_path.read_text()
    text = text.replace("center_city: 1", f"{zone}: 1", 1)
    kroger_path.write_text(text)
