"""Deterministic PAN -> customer_id hashing.

`customer_id = sha256(HASH_SECRET + pan)[:16]`. Same PAN at every merchant
produces the same `customer_id` — this is the cross-merchant join key in the
lake. The secret comes from `parameters.HASH_SECRET`; rotate it in production.
"""
from __future__ import annotations

import hashlib

from src.generate.parameters import HASH_SECRET


def customer_id_for(pan: str) -> str:
    h = hashlib.sha256((HASH_SECRET + str(pan)).encode("utf-8")).hexdigest()
    return h[:16]
