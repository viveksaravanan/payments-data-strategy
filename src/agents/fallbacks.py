"""Canonical user-facing fallback strings — defined once, plain and CEO-safe.

The output-safety invariant (Phase 1): the ONLY things that may reach the user are a
validated ``emit_response`` payload or one of these canonical strings — never the model's
mid-loop scratchpad. Keep these free of any mechanics vocabulary
(see ``lake_tools._FORBIDDEN_MECHANICS_TERMS``).
"""
from __future__ import annotations

# The general "couldn't substantiate a grounded comparison" fallback. Every failure path
# (narration sanitizer, all-stripped synthesizer, wall-clock ceiling, turn exhaustion)
# routes through ``lake_tools.business_fallback()``, which returns this string.
BUSINESS_FALLBACK = (
    "A grounded peer comparison wasn't available for this view; "
    "your own figures and the peer benchmark are shown below."
)

# Period-over-period specifically couldn't be built (the anomaly / week-over-week case).
COMPARISON_UNAVAILABLE = (
    "A period-over-period comparison isn't available for this view right now. "
    "Try re-running the question, or ask about current levels instead."
)
