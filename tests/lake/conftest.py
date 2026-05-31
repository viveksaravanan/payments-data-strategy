"""Shared fixtures for the Wave 2 lake L-battery.

Each lake builder reads the 28MB transactions + 112MB items Parquet
and aggregates over ~10.76M line items. Without sharing, every
test-file fixture rebuilds the lake from scratch — 60-90s × 5
tables × multiple test files ≈ very slow suite.

**Session-scoped fixtures here** materialize each lake table once
per pytest session. Test files declare ``def test_x(category_metrics)``
and pytest reuses the cached frame.

Per-test-file ``@pytest.fixture(scope="module")`` definitions that
predate this conftest will get masked when they import the same
name; for clean migration, test files should drop their local
fixtures and rely on these.
"""
from __future__ import annotations

import pandas as pd
import pytest


@pytest.fixture(scope="session")
def lake_category_metrics() -> pd.DataFrame:
    from src.lake.build import build_lake_category_metrics
    return build_lake_category_metrics()


@pytest.fixture(scope="session")
def lake_payment_mix() -> pd.DataFrame:
    from src.lake.build import build_lake_payment_mix
    return build_lake_payment_mix()


@pytest.fixture(scope="session")
def lake_segment_mix() -> pd.DataFrame:
    from src.lake.build import build_lake_segment_mix
    return build_lake_segment_mix()


@pytest.fixture(scope="session")
def lake_trade_area() -> pd.DataFrame:
    from src.lake.build import build_lake_trade_area
    return build_lake_trade_area()


@pytest.fixture(scope="session")
def lake_cohorts() -> pd.DataFrame:
    from src.lake.build import build_lake_cross_merchant_cohorts
    return build_lake_cross_merchant_cohorts()
