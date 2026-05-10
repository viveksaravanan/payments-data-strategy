"""Phase 6 planted anomalies.

Three signals planted at generation time, each with a corresponding
canned demo question:

  - ``university_city_decline`` — 4-stage traffic ramp on the three
    grocers' University City stores; deepest at Kroger.
  - ``plaza_midwood_avocado`` — 4-day avocado quantity spike at
    Kroger Plaza Midwood, peaking Apr 22.
  - ``acme_pasta_promo`` — three coordinated pasta promos: KRG lift,
    Acme suppression (the failure), Winn-Dixie modest lift.

Each module exposes pure functions used by ``transactions.py`` during
generation; no random state lives in this package.
"""
from .acme_pasta_promo import (  # noqa: F401
    pasta_multiplier,
    pinned_pasta_promo,
)
from .plaza_midwood_avocado import avocado_multiplier  # noqa: F401
from .university_city_decline import uc_trip_keep_probability  # noqa: F401

__all__ = [
    "uc_trip_keep_probability",
    "avocado_multiplier",
    "pasta_multiplier",
    "pinned_pasta_promo",
]
