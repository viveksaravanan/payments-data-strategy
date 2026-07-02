"""Tests for events dormancy (datamodel-v2 — Decision B).

Promotions + planted anomalies are DISABLED but dormant this pass: the
event framework in src/generate/engine/events.py is kept intact (for a
later anomaly wave) but is NOT wired into run_all. The pipeline emits
empty `promotions` + `anomalies_groundtruth` tables so the observable-
guard allowlist and the answer-key physical separation stay valid.

The detailed promo-schedule / anomaly-magnitude / A1-filter tests from
Wave 1 are retired with the wiring — those exercised machinery that no
longer runs. When planted anomalies return (with detection tied to
anomalies_groundtruth), the schedule tests come back with them.
"""
from __future__ import annotations

import importlib

import pandas as pd

from src.generate.engine.run_all import (
    ANOMALIES_ENABLED, PROMOS_ENABLED, build_all,
)


def test_flags_dormant() -> None:
    assert PROMOS_ENABLED is False
    assert ANOMALIES_ENABLED is False


def test_pipeline_emits_empty_promotions_and_anomalies() -> None:
    tables = build_all(scale=800)
    assert len(tables["promotions"]) == 0
    assert len(tables["anomalies_groundtruth"]) == 0
    # No line carries a promo (dormant) — discount/promo_id inert.
    ti = tables["transaction_items"]
    assert ti["promo_id"].isna().all()
    assert float(ti["discount"].abs().sum()) == 0.0


def test_events_framework_kept_importable() -> None:
    """The dormant event framework is retained (not deleted) for a
    later wave — it must still import."""
    mod = importlib.import_module("src.generate.engine.events")
    # A representative builder is still present, just uninvoked.
    assert hasattr(mod, "build_promo_schedule")
    assert hasattr(mod, "build_anomaly_schedule")
