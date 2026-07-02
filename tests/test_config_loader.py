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
    assert cfg.global_["expected_store_count"] == 38


def test_loads_eight_zones(cfg: Config) -> None:
    assert len(cfg.zones) == 8
    zone_ids = {z["id"] for z in cfg.zones}
    assert zone_ids == {
        "center_city", "dilworth", "ballantyne", "noda",
        "university_city", "eastway", "matthews", "cabarrus_edge",
    }


def test_loads_two_segments(cfg: Config) -> None:
    assert set(cfg.segments.keys()) == {"grocery", "qsr"}


def test_loads_six_merchants(cfg: Config) -> None:
    assert set(cfg.merchants.keys()) == {
        "Kroger", "Acme", "Winn-Dixie", "Taco Bell", "Burger King", "Chick-fil-A",
    }


def test_segment_distance_decay_betas(cfg: Config) -> None:
    """§C4: grocery 2.0, qsr 2.2 (off-price dropped)."""
    assert cfg.segments["grocery"]["distance_decay"]["beta"] == 2.0
    assert cfg.segments["qsr"]["distance_decay"]["beta"] == 2.2


def test_every_merchant_has_attractiveness_and_sku_target(cfg: Config) -> None:
    """datamodel-v2 invariant: config-driven A_s + assortment breadth."""
    for name, m in cfg.merchants.items():
        assert isinstance(m["attractiveness"], (int, float)) and m["attractiveness"] > 0
        assert isinstance(m["sku_target"], int) and m["sku_target"] > 0


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


def test_total_store_count_is_38(cfg: Config) -> None:
    """§A11/§B1 placement grids: grocery 6+5+4=15 + QSR 9+8+6=23 = 38."""
    total = sum(m["store_count"] for m in cfg.merchants.values())
    assert total == 38
    assert total == cfg.global_["expected_store_count"]


def test_volume_targets_satisfy_band(cfg: Config) -> None:
    """§C9 window transaction targets — grocery + qsr (off-price dropped)."""
    targets = cfg.global_["volume_targets"]
    assert targets["grocery"] == 2_670_000
    assert targets["qsr"] == 1_760_000
    assert targets["total"] == 4_430_000
    assert "off_price" not in targets
    assert targets["grocery"] + targets["qsr"] == targets["total"]
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


# ----- D17.2 affinity_matrix validation -----------------------------

def test_load_fails_on_negative_affinity_boost(tmp_path: Path) -> None:
    """An affinity_matrix entry with a non-positive boost raises."""
    _clone_config_with_affinity_override(tmp_path, '  - ["Sandwich Bread", "Butter", -2.0]')
    with pytest.raises(ConfigInvariantError, match="affinity_matrix"):
        load_config(tmp_path)


def test_load_fails_on_malformed_affinity_entry(tmp_path: Path) -> None:
    """An affinity_matrix entry that isn't [anchor, partner, boost] raises."""
    _clone_config_with_affinity_override(tmp_path, '  - ["Sandwich Bread", "Butter"]')
    with pytest.raises(ConfigInvariantError, match="affinity_matrix"):
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
        "attractiveness: 0.85\n"
        "sku_target: 70\n"
        "zone_placement_bias:\n"
        "  matthews: 2\n"
        "  ballantyne: 1\n"
    )
    # Bump the global expected store count to reflect the new footprint.
    # Touching this is part of the "config-only" change — no engine code
    # is altered.
    global_path = tmp_path / "global.yaml"
    text = global_path.read_text()
    text = text.replace("expected_store_count: 38", "expected_store_count: 41")
    global_path.write_text(text)

    cfg = load_config(tmp_path)
    assert "Burger Shack" in cfg.merchants
    assert cfg.merchants["Burger Shack"]["store_count"] == 3
    assert sum(m["store_count"] for m in cfg.merchants.values()) == 41


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


def _clone_config_with_affinity_override(dest: Path, replacement: str) -> None:
    """Clone the config and replace the last grocery affinity entry with
    a (typically malformed) ``replacement`` line."""
    _clone_config(dest)
    grocery_path = dest / "segments" / "grocery.yaml"
    text = grocery_path.read_text()
    text = text.replace('  - ["Sandwich Bread", "Butter", 2.5]', replacement, 1)
    grocery_path.write_text(text)
