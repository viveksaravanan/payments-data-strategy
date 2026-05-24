"""Phase 4.5 — filter wiring smoke test.

Confirms the dashboard's data helpers actually honor the user-set
filter dict end-to-end. This test exists to catch regressions where
a helper is rewritten and silently drops its filter parameter (the
exact bug that motivated the Phase 4.5 wiring effort).
"""
from __future__ import annotations

import pytest

from src.dashboard import data as D


def test_kpi_strip_honors_stores_filter():
    """``kpi_strip`` with a 2-store subset must produce strictly
    smaller revenue and transaction counts than the full panel run.
    Decision 2 re-anchor uses the same week list when no date filter
    is set, so this test isolates the stores-filter wiring."""
    full = D.kpi_strip("KRG")
    subset = ["KRG-NC-0001", "KRG-NC-0002"]
    narrowed = D.kpi_strip("KRG", filters={"stores": subset})

    # Subset of stores → strictly less revenue and transactions
    # (KRG has 30 stores; 2 of them produce far less aggregate volume).
    assert narrowed["revenue"]["value"] < full["revenue"]["value"], (
        f"narrowed revenue ({narrowed['revenue']['value']}) should be "
        f"strictly less than full revenue ({full['revenue']['value']})"
    )
    assert narrowed["transactions"]["value"] < full["transactions"]["value"], (
        f"narrowed transactions ({narrowed['transactions']['value']}) "
        f"should be strictly less than full transactions "
        f"({full['transactions']['value']})"
    )
    # Sparkline non-empty for both paths.
    assert len(narrowed["revenue"]["sparkline"]) > 0
    assert len(full["revenue"]["sparkline"]) == 12  # default 12-week list
