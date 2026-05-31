"""L03 — Zones derived from coordinates (Wave 2 §3 / D23.2).

Three independent checks:

(a) ``derive_zone_for_store`` signature physically rejects forbidden
    columns. Passing a frame containing ``zone_id`` raises —
    that's the §1-violation-in-disguise guard.
(b) Coordinate-only clustering produces 8 stable derived zones for
    the 29 Wave 1 stores; labels are positional (Z01..Z08), no
    correspondence to planted naming.
(c) **Validation (separate from build):** the derived clusters
    correspond to Wave 1's 8 planted zones; behavioral character
    correlates with planted affluence. The planted columns are read
    ONLY inside these validation tests — never by the build code.

The L01 static-scan test confirms the build never reaches around
``observable_guard`` for the forbidden columns.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.lake.observable_guard import load_table
from src.lake.zones import (
    N_ZONES,
    derive_zone_character,
    derive_zone_for_store,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_RAW = REPO_ROOT / "data" / "raw"


# ----- Fixtures ------------------------------------------------------------

@pytest.fixture(scope="module")
def stores_obs() -> pd.DataFrame:
    """Stores loaded via observable_guard — coordinate columns only."""
    return load_table(
        "stores",
        columns=["store_id", "banner_code", "latitude", "longitude"],
    )


@pytest.fixture(scope="module")
def derived_zones(stores_obs) -> pd.DataFrame:
    return derive_zone_for_store(
        stores_obs[["store_id", "latitude", "longitude"]]
    )


@pytest.fixture(scope="module")
def stores_with_zone(stores_obs, derived_zones) -> pd.DataFrame:
    return stores_obs.merge(derived_zones, on="store_id")


# ----- (a) Signature guards reject the §1-violation-in-disguise input ------

def test_derive_zone_rejects_zone_id_column() -> None:
    """Passing stores with zone_id in the frame raises — preventing
    accidental label-reading even if the caller has access."""
    df = pd.DataFrame({
        "store_id": ["KRG-NC-0001"],
        "latitude": [35.2],
        "longitude": [-80.8],
        "zone_id": ["center_city"],
    })
    with pytest.raises(ValueError, match="forbidden column"):
        derive_zone_for_store(df)


def test_derive_zone_rejects_home_zone_column() -> None:
    df = pd.DataFrame({
        "store_id": ["KRG-NC-0001"],
        "latitude": [35.2],
        "longitude": [-80.8],
        "home_zone": ["center_city"],
    })
    with pytest.raises(ValueError, match="forbidden column"):
        derive_zone_for_store(df)


def test_derive_zone_requires_coordinates() -> None:
    df = pd.DataFrame({"store_id": ["KRG-NC-0001"]})
    with pytest.raises(ValueError, match="requires"):
        derive_zone_for_store(df)


# ----- (b) Coordinate clustering produces stable Z01..Z08 labels -----------

def test_derive_zone_produces_8_clusters(derived_zones) -> None:
    n_unique = derived_zones["derived_zone"].nunique()
    assert n_unique == N_ZONES
    assert set(derived_zones["derived_zone"].unique()) == {
        f"Z{i:02d}" for i in range(1, N_ZONES + 1)
    }


def test_derive_zone_covers_all_stores(derived_zones) -> None:
    assert len(derived_zones) == 29
    assert derived_zones["store_id"].is_unique


def test_derive_zone_is_deterministic(stores_obs) -> None:
    a = derive_zone_for_store(stores_obs[["store_id", "latitude", "longitude"]])
    b = derive_zone_for_store(stores_obs[["store_id", "latitude", "longitude"]])
    pd.testing.assert_frame_equal(a, b)


def test_derived_labels_are_positional_not_planted(derived_zones, stores_obs) -> None:
    """Labels are Z01..Z08 in order of cluster latitude (northernmost
    first). This is positional, has no relation to the planted zone
    naming (center_city, dilworth, etc.) — confirms the labels aren't
    silently leaking the planted scheme."""
    merged = derived_zones.merge(
        stores_obs[["store_id", "latitude"]], on="store_id"
    )
    cluster_lats = merged.groupby("derived_zone")["latitude"].mean()
    # Northernmost (highest lat) should be Z01.
    expected_order = cluster_lats.sort_values(ascending=False).index.tolist()
    actual_order = [f"Z{i:02d}" for i in range(1, N_ZONES + 1)]
    assert expected_order == actual_order, (
        f"Z01..Z08 should track northernmost→southernmost cluster centroids; "
        f"got order: {expected_order}"
    )


# ----- (c) VALIDATION (test-only reads planted, not the build code) --------

def _load_planted_zone_id() -> pd.DataFrame:
    """Read stores.zone_id directly for the validation check. This is
    the §1-validated invariant: the planted column is read ONLY here,
    in the test, never in the build code."""
    return pd.read_parquet(DATA_RAW / "stores.parquet")[["store_id", "zone_id"]]


def test_VALIDATION_derived_clusters_correspond_to_wave1_zones(
    derived_zones,
) -> None:
    """The D23.1 free check: coordinate-based clustering on 29 stores
    placed at 8 Wave 1 zone centroids ± 0.02° jitter should recover
    those 8 zones cleanly. The correspondence is a *result* — proof
    that generation is coherent — NOT an input.

    'Purity': for each derived cluster, the fraction of its stores
    that share the same Wave 1 zone_id. Mean purity ≈ 1.0 means
    perfect correspondence."""
    planted = _load_planted_zone_id()
    j = derived_zones.merge(planted, on="store_id")
    purities = []
    for derived, grp in j.groupby("derived_zone"):
        dominant = grp["zone_id"].value_counts().max()
        purities.append(dominant / len(grp))
    mean_purity = float(np.mean(purities))
    print(f"\nL03 mean derived-cluster purity vs Wave 1 zones: {mean_purity:.3f}")
    assert mean_purity >= 0.85, (
        f"Derived clusters should recover Wave 1 zones with high within-"
        f"cluster purity (generation coherence); got mean purity {mean_purity:.3f}"
    )


def test_VALIDATION_derived_clusters_recover_most_planted_zones(
    derived_zones,
) -> None:
    """Correspondence check: derived clusters should recover MOST of
    the planted zones. Honest k-means result on 29 stores with
    imbalanced Wave 1 cluster sizes (dilworth=1, noda=3, matthews=7,
    etc.) — small clusters can be absorbed into neighbors while
    large ones split. ≥6/8 planted zones covered is the realistic
    expectation; the dominant-purity test above (mean ≥ 0.85)
    establishes the within-cluster spatial coherence.

    A 'perfect 1-to-1 mapping' assertion is intentionally avoided
    here — pursuing it would push toward initializing k-means with
    the planted centroids, which would be the §1-violation-in-
    disguise. The honest result is: k-means recovers spatial
    structure with high fidelity within each derived cluster, but
    cannot guarantee 1-to-1 with imbalanced ground-truth."""
    planted = _load_planted_zone_id()
    j = derived_zones.merge(planted, on="store_id")
    derived_to_planted: dict[str, str] = {}
    for derived, grp in j.groupby("derived_zone"):
        derived_to_planted[derived] = grp["zone_id"].mode().iloc[0]
    n_unique = len(set(derived_to_planted.values()))
    print(
        f"\nL03 derived→planted mapping recovers {n_unique}/8 Wave 1 zones: "
        f"{derived_to_planted}"
    )
    assert n_unique >= 6, (
        f"Derived clusters should recover at least 6/8 Wave 1 zones; "
        f"got {n_unique}: {derived_to_planted}"
    )


# ----- (c) Behavioral character + correlation ------------------------------

def test_derive_zone_character_columns(stores_with_zone) -> None:
    """Character frame has the four observable behavioral columns."""
    df = derive_zone_character(stores_with_zone)
    required = {
        "derived_zone", "avg_basket_units", "avg_subtotal",
        "premium_banner_share", "value_banner_share", "promo_unit_share",
        "n_txns",
    }
    assert required.issubset(df.columns)
    assert len(df) == N_ZONES


def test_zone_character_metrics_in_realistic_ranges(stores_with_zone) -> None:
    df = derive_zone_character(stores_with_zone)
    # Sanity bands at full scale (Wave 1 DQ Report magnitudes carry).
    assert (df["avg_basket_units"] > 0).all()
    assert (df["avg_subtotal"] > 0).all()
    assert (df["promo_unit_share"] >= 0).all()
    assert (df["promo_unit_share"] <= 1).all()
    # Shares per zone are in [0, 1].
    assert (df["premium_banner_share"].between(0, 1)).all()
    assert (df["value_banner_share"].between(0, 1)).all()


def test_VALIDATION_character_correlates_with_planted_affluence(
    stores_with_zone, derived_zones,
) -> None:
    """D23.1 free check: derived behavioral character should correlate
    with the planted zone.affluence. The planted column is read ONLY
    here in the validation test, never by the build code."""
    char = derive_zone_character(stores_with_zone)

    # Map derived zone → dominant Wave 1 zone_id (correspondence
    # established in the test above).
    planted_store_zone = pd.read_parquet(DATA_RAW / "stores.parquet")[
        ["store_id", "zone_id"]
    ]
    derived_to_planted = (
        derived_zones.merge(planted_store_zone, on="store_id")
        .groupby("derived_zone")["zone_id"]
        .agg(lambda s: s.mode().iloc[0])
        .to_dict()
    )

    # Read the planted zone affluence from the Wave 1 config (test-only).
    # The Wave 1 metro.yaml carries affluence per zone_id.
    import yaml
    metro = yaml.safe_load(
        (REPO_ROOT / "src" / "generate" / "config" / "metro.yaml").read_text()
    )
    affluence_by_zone_id = {z["id"]: z["affluence"] for z in metro["zones"]}

    char = char.assign(
        planted_zone_id=char["derived_zone"].map(derived_to_planted),
        planted_affluence=lambda d: d["planted_zone_id"].map(affluence_by_zone_id),
    )

    # Premium banner share should rise with affluence (Acme placement
    # is biased toward affluent zones per D13.2).
    corr_premium = char["premium_banner_share"].corr(char["planted_affluence"])
    # Value banner share should fall with affluence (WDX placement bias).
    corr_value = char["value_banner_share"].corr(char["planted_affluence"])
    # Avg subtotal should rise modestly with affluence (basket-size mult +
    # tender mix → marginal premium).
    corr_subtotal = char["avg_subtotal"].corr(char["planted_affluence"])

    print(
        f"\nL03 derived-character vs planted-affluence correlations: "
        f"premium_share {corr_premium:+.3f}  value_share {corr_value:+.3f}  "
        f"avg_subtotal {corr_subtotal:+.3f}"
    )
    # Direction matters more than magnitude here (8-zone sample).
    assert corr_premium > 0.30, (
        f"premium_banner_share should correlate positively with affluence; "
        f"got r={corr_premium:.3f}"
    )
    assert corr_value < -0.30, (
        f"value_banner_share should correlate negatively with affluence; "
        f"got r={corr_value:.3f}"
    )


# ----- §1 invariant: the L01 scan must still pass ---------------------------

def test_zones_module_does_not_bypass_observable_guard() -> None:
    """Re-runs the L01 AST scan on zones.py specifically. Equivalent
    coverage: zones.py reads data only via observable_guard.load_table."""
    import ast
    src = (REPO_ROOT / "src" / "lake" / "zones.py").read_text()
    tree = ast.parse(src)
    direct_reads = []

    class Finder(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:
            attr = (
                node.func.attr if isinstance(node.func, ast.Attribute)
                else node.func.id if isinstance(node.func, ast.Name)
                else None
            )
            if attr in {"read_parquet", "read_table"}:
                direct_reads.append((node.lineno, attr))
            self.generic_visit(node)

    Finder().visit(tree)
    assert not direct_reads, (
        f"src/lake/zones.py contains direct Parquet reads — must use "
        f"observable_guard: {direct_reads}"
    )
