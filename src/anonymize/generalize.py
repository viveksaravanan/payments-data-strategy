"""Generalization helpers used by the lake stage: ZIP3 truncation and hour
bucketing. Keep these as small pure functions so the lake stage remains
straightforward."""
from __future__ import annotations

from datetime import datetime


def truncate_zip(z: str) -> str:
    """Return the first 3 chars of a ZIP5 (or pad if shorter)."""
    s = str(z)
    return s[:3]


def bucket_to_hour(ts: datetime) -> datetime:
    """Truncate a datetime to the start of its hour."""
    return ts.replace(minute=0, second=0, microsecond=0)


def bucket_ts_string(ts_str: str) -> str:
    """Vectorizable equivalent for ``YYYY-MM-DD HH:MM:SS`` strings.

    The lake stage operates on pandas string columns, so this avoids round-
    tripping through ``datetime`` objects for ~100k rows.
    """
    return ts_str[:13] + ":00:00"
