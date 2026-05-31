"""Wave 1 test bootstrap.

The v3 SQLite autouse fixture is retired with the v3 generator
(Stage 7 of Wave 1). The v4 engine writes Parquet directly; the
data-quality test fixtures (tests/data_quality/conftest.py)
trigger a pilot Parquet build per session as needed.

This file kept for pytest collection consistency.
"""
