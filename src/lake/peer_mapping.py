"""Per-merchant peer mapping.

For each viewing merchant, the lake re-labels other merchants as
``peer_a``..``peer_d`` per the locked Phase 2 mapping
(``V2_5_DATA_DESIGN.md`` lines 957–970). Same-segment peers come first
(``peer_a``/``peer_b``), then cross-segment peers alphabetically by
``merchant_id``.

The canonical table:

    | Viewing as | peer_a | peer_b | peer_c | peer_d |
    |---|---|---|---|---|
    | Kroger     | Acme   | Winn-Dixie | Taco Bell | TJ Maxx |
    | Acme       | Kroger | Winn-Dixie | Taco Bell | TJ Maxx |
    | Winn-Dixie | Acme   | Kroger     | Taco Bell | TJ Maxx |
    | Taco Bell  | Acme   | Kroger     | Winn-Dixie | TJ Maxx |
    | TJ Maxx    | Acme   | Kroger     | Winn-Dixie | Taco Bell |

The mapping itself is stored in ``parameters.PEER_MAPPING`` so the
generator and the lake both read the same source of truth. This module
adds a SQL-side helper that builds a CASE expression so view-builders
can translate ``merchants.merchant_id`` → ``peer_id`` inside the SQL.
"""
from __future__ import annotations

from src.generate import parameters as P


def build_peer_mapping(viewing_merchant_id: str) -> dict[str, str]:
    """Return ``{peer_merchant_id: peer_label}`` for the four peers of
    `viewing_merchant_id`. Raises if the merchant is unknown."""
    if viewing_merchant_id not in P.PEER_MAPPING:
        raise ValueError(
            f"Unknown viewing merchant {viewing_merchant_id!r}. "
            f"Expected one of {sorted(P.PEER_MAPPING)}."
        )
    order = P.PEER_MAPPING[viewing_merchant_id]
    return {mid: f"peer_{chr(ord('a') + i)}" for i, mid in enumerate(order)}


def peer_case_sql(viewing_merchant_id: str, column: str) -> str:
    """Build a SQL ``CASE`` expression that maps `column` (a
    ``merchant_id`` column reference) to its peer label for the given
    viewing merchant.

    The viewing merchant itself is NOT included in the CASE — the
    surrounding query is expected to exclude rows where
    ``merchant_id = :viewing``. If a row leaks past that filter, the
    CASE returns NULL, which lets test code catch the bug.
    """
    mapping = build_peer_mapping(viewing_merchant_id)
    parts = [f"CASE {column}"]
    for mid, label in mapping.items():
        parts.append(f"  WHEN '{mid}' THEN '{label}'")
    parts.append("END")
    return "\n".join(parts)
