"""Wave 2 §3 zones — observable grouping + behavioral character (D23.2).

**The §1-violation-in-disguise constraint.** Zone grouping must be
**genuinely coordinate-based** — clustered on ``(latitude, longitude)``
alone. Reading ``stores.zone_id`` or ``customers.home_zone`` and
relabeling them "derived" is the failure mode this whole module is
built to prevent. The §1 ``observable_guard`` accessor mechanically
forbids reading those columns; this module's signatures only accept
the allowed coordinate columns.

**Tags vs grouping key.** Per the Stage 2 confirmation: ``neighborhood``
and ``metro_region`` are observable geo-derived attributes (reverse-
geocodable from coordinates per §7). They're carried as **tags** on
the zone output for human readability, but the **grouping key** is
the coordinate cluster — not the neighborhood label. "Group by
neighborhood" must not become a backdoor reconstruction of
``zone_id``.

Functions:

* ``derive_zone_for_store(stores)`` — coordinate-based clustering →
  per-store ``derived_zone`` label.
* ``derive_zone_character(transactions, items, stores_with_zone)`` —
  observable behavioral character per derived zone (avg basket size,
  premium-banner volume share, value-banner volume share, price
  posture index, promo sensitivity share). Reads only observable
  facts via ``observable_guard``.

A test-side ``validate_zone_character_correlation`` helper (in
``tests/lake/test_L03_zones.py``) reads the planted profile ONCE,
inside the test, to confirm derived ↔ planted correspondence as a
result. The build code never reads it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.lake.observable_guard import load_table

# The metro design has 8 zones (Wave 1 D13.1). Picking k=8 matches that
# design constant — analogous to "we know we built a metro with 8
# neighborhoods, so cluster the stores into 8 spatial groups." This is
# a design parameter of the metro shape, not a planted-label read.
N_ZONES: int = 8

# Deterministic seed for the clustering — same value as Wave 1's
# generation seed so Wave 2's lake derivation is also reproducible.
_CLUSTER_SEED: int = 42


# ----- Coordinate-based clustering ------------------------------------------

def _kmeans(
    X: np.ndarray,
    k: int,
    *,
    max_iter: int = 100,
    seed: int = _CLUSTER_SEED,
) -> np.ndarray:
    """Deterministic k-means with k-means++ initialization.

    Returns cluster labels of shape ``(n,)``. Reproducible across runs
    at the same seed.

    Hand-rolled to avoid adding scipy/sklearn dependencies for the
    29-point × 8-cluster case the metro needs.
    """
    rng = np.random.default_rng(seed)
    n = X.shape[0]

    # k-means++ init: first centroid random, subsequent ones with
    # probability proportional to squared distance from existing.
    centroids = np.empty((k, X.shape[1]), dtype=float)
    centroids[0] = X[int(rng.integers(n))]
    for i in range(1, k):
        d2 = np.min(
            ((X[:, None, :] - centroids[:i, None, :].swapaxes(0, 1)) ** 2).sum(axis=-1),
            axis=1,
        )
        if d2.sum() == 0:
            # Degenerate: pick any unused point.
            centroids[i] = X[i % n]
            continue
        probs = d2 / d2.sum()
        idx = int(rng.choice(n, p=probs))
        centroids[i] = X[idx]

    # Lloyd's algorithm.
    labels = np.zeros(n, dtype=int)
    for _ in range(max_iter):
        d2 = ((X[:, None, :] - centroids[None, :, :]) ** 2).sum(axis=-1)
        new_labels = d2.argmin(axis=1)
        if np.array_equal(new_labels, labels):
            break
        labels = new_labels
        new_centroids = centroids.copy()
        for j in range(k):
            mask = labels == j
            if mask.any():
                new_centroids[j] = X[mask].mean(axis=0)
        centroids = new_centroids

    return labels


def derive_zone_for_store(stores: pd.DataFrame) -> pd.DataFrame:
    """Derive a per-store zone label from coordinate clustering only.

    Parameters
    ----------
    stores
        DataFrame with at least ``store_id, latitude, longitude``.
        Must NOT contain ``zone_id`` — guarded by the assertion below
        to prevent accidental label-reading (the §1-violation-in-
        disguise pattern).

    Returns
    -------
    DataFrame with columns ``[store_id, derived_zone]``.
    ``derived_zone`` is a stable label of the form ``"Z01"`` … ``"Z08"``.

    Notes
    -----
    Cluster labels are reassigned to ``Z01..Z08`` in *order of cluster
    centroid latitude (then longitude)* — purely positional, no
    relation to the planted zone naming. The §1 validation test reads
    the planted ``zone_id`` once to check correspondence; this build
    function never does.
    """
    required = {"store_id", "latitude", "longitude"}
    missing = required - set(stores.columns)
    if missing:
        raise ValueError(
            f"derive_zone_for_store requires {sorted(required)}; "
            f"missing: {sorted(missing)}"
        )
    forbidden = {"zone_id", "home_zone"}
    leaked = forbidden & set(stores.columns)
    if leaked:
        raise ValueError(
            f"derive_zone_for_store called with forbidden column(s) "
            f"{sorted(leaked)} — Stage 3 clustering MUST be coordinate-based "
            f"only. Pass stores[['store_id', 'latitude', 'longitude']]."
        )

    s = stores.sort_values("store_id").reset_index(drop=True)
    X = s[["latitude", "longitude"]].to_numpy(dtype=float)
    raw_labels = _kmeans(X, k=N_ZONES)

    # Reassign labels Z01..Z08 by centroid latitude order (north→south
    # would be desc, but lat ascending = south→north — pick desc so
    # Z01 is the northernmost cluster). Stable, deterministic, and
    # decoupled from the planted zone naming.
    centroids = np.array([
        X[raw_labels == j].mean(axis=0) if (raw_labels == j).any() else [0.0, 0.0]
        for j in range(N_ZONES)
    ])
    # Sort by (-lat, long): northernmost first; ties broken east→west.
    order = np.lexsort((centroids[:, 1], -centroids[:, 0]))
    remap = {old: new for new, old in enumerate(order)}
    final = np.array([f"Z{(remap[int(lab)] + 1):02d}" for lab in raw_labels])

    return pd.DataFrame({
        "store_id": s["store_id"].to_numpy(),
        "derived_zone": final,
    })


# ----- Observable behavioral character per zone ----------------------------

def derive_zone_character(
    stores_with_zone: pd.DataFrame,
) -> pd.DataFrame:
    """Compute observable behavioral character per derived zone.

    Reads only observable transaction + item facts via
    ``observable_guard``; never touches planted profile columns.

    Parameters
    ----------
    stores_with_zone
        DataFrame with columns ``[store_id, derived_zone, banner_code]``
        — the output of ``derive_zone_for_store`` joined with the
        store→banner mapping.

    Returns
    -------
    DataFrame indexed by ``derived_zone`` with behavioral columns:

    * ``avg_basket_units`` — mean items per transaction in the zone.
    * ``avg_subtotal`` — mean transaction subtotal in the zone.
    * ``premium_banner_share`` — share of zone txns at premium-tier
      banners (ACM).
    * ``value_banner_share`` — share at value-tier banners (WDX).
    * ``promo_unit_share`` — share of zone line-item units on promo.

    These are the four levers a zone has for its observable character.
    The validation test in ``test_L03_zones.py`` correlates them
    against the planted ``zone.affluence`` to confirm coherence — but
    the planted profile is read only inside the test.
    """
    txns = load_table(
        "transactions",
        columns=["txn_id", "store_id", "banner_code", "subtotal", "n_lines"],
    )
    items = load_table(
        "transaction_items",
        columns=["txn_id", "qty", "promo_id"],
    )

    # Join store→zone onto txns.
    txns = txns.merge(
        stores_with_zone[["store_id", "derived_zone"]],
        on="store_id", how="inner",
    )

    # Per-zone txn-level stats.
    by_zone_txn = txns.groupby("derived_zone").agg(
        avg_subtotal=("subtotal", "mean"),
        avg_basket_units=("n_lines", "mean"),
        n_txns=("txn_id", "count"),
    )

    # Premium / value banner shares — ACM is the premium grocer, WDX the
    # value grocer (per D13.2 / Wave 1 merchant configs). Observable from
    # banner_code on transactions.
    banner_counts = (
        txns.groupby(["derived_zone", "banner_code"]).size().unstack(fill_value=0)
    )
    total = banner_counts.sum(axis=1)
    premium = banner_counts.get("ACM", 0) / total
    value = banner_counts.get("WDX", 0) / total

    # Promo unit share — join items onto txns to get zone, then compute
    # share of units on promo.
    items_w_zone = items.merge(
        txns[["txn_id", "derived_zone"]],
        on="txn_id", how="inner",
    )
    promo_units = (
        items_w_zone.assign(on_promo=items_w_zone["promo_id"].notna())
        .groupby("derived_zone")
        .apply(
            lambda d: (d.loc[d["on_promo"], "qty"].sum() / d["qty"].sum())
            if d["qty"].sum() > 0 else 0.0,
            include_groups=False,
        )
        .rename("promo_unit_share")
    )

    out = by_zone_txn.copy()
    out["premium_banner_share"] = premium
    out["value_banner_share"] = value
    out["promo_unit_share"] = promo_units
    return out.reset_index()
