"""Winn-Dixie catalog shim — delegates to `catalog_grocery` with the WDX overlay."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import catalog_grocery


def build_catalog(rng: np.random.Generator | None = None) -> pd.DataFrame:
    return catalog_grocery.build_catalog("winn_dixie", rng=rng)


def make_apply_affinity(catalog: pd.DataFrame):
    return catalog_grocery.make_apply_affinity(catalog)
