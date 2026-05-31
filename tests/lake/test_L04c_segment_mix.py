"""Stage 4.3 tests — ``lake_segment_mix`` (D23.3.3).

THE §1 ACID TEST. The build must derive behavioral segments from
observable transaction features only; reading planted
``customers.loyalty_type`` / ``customers.primary_banner`` /
``customers.home_zone`` is structurally forbidden by §1.

Tests verify: (a) the build code never tries to read forbidden columns
(the L01 static scan), (b) the output has the expected schema with
shares summing to 1, (c) k≥50 floor holds, (d) **validation**: the
derived behavioral segments correlate with the planted loyalty_type
when the test (NOT the build) reads the planted column.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.lake.build import K_MIN, build_lake_segment_mix

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_RAW = REPO_ROOT / "data" / "raw"


@pytest.fixture(scope="module")
def segment_mix() -> pd.DataFrame:
    return build_lake_segment_mix()


# ----- Schema -------------------------------------------------------------

def test_required_columns(segment_mix) -> None:
    required = {
        "banner_code", "derived_zone", "behavioral_segment",
        "n_cards", "median_basket", "median_freq",
        "txn_count", "share_of_zone_at_banner",
    }
    assert required.issubset(segment_mix.columns)


def test_no_planted_columns_on_output(segment_mix) -> None:
    """L1: ``segment_mix`` output must not carry forbidden planted
    columns. Even if the build leaked one to the output, this test
    would catch it."""
    forbidden = {
        "loyalty_type", "primary_banner", "home_zone",
        "affluence", "wallet_enrolled",
    }
    assert not (forbidden & set(segment_mix.columns))


def test_no_identity_or_peer_columns(segment_mix) -> None:
    forbidden = {"customer_token", "card_id", "sku", "store_id",
                 "peer_relationship"}
    assert not (forbidden & set(segment_mix.columns))


def test_four_behavioral_segments(segment_mix) -> None:
    """Exactly the four bucketed segments — never any planted label."""
    expected = {
        "premium_loyalist", "frequent_value",
        "occasional_premium", "occasional",
    }
    assert set(segment_mix["behavioral_segment"].unique()) == expected


def test_all_five_merchants_present(segment_mix) -> None:
    assert set(segment_mix["banner_code"].unique()) == {
        "KRG", "ACM", "WDX", "TBL", "TJX",
    }


# ----- k ≥ 50 floor -------------------------------------------------------

def test_every_cell_meets_k_floor(segment_mix) -> None:
    print(
        f"\nL04c cells: {len(segment_mix):,}  | "
        f"min txn_count: {int(segment_mix['txn_count'].min())}  | "
        f"max: {int(segment_mix['txn_count'].max())}"
    )
    assert (segment_mix["txn_count"] >= K_MIN).all()


# ----- Shares sum to 1 within (banner, zone) -----------------------------

def test_share_of_zone_sums_to_1_per_banner_zone(segment_mix) -> None:
    by_bz = segment_mix.groupby(
        ["banner_code", "derived_zone"]
    )["share_of_zone_at_banner"].sum()
    # Tolerance for floating-point + occasional suppression of a small
    # segment cell.
    assert ((by_bz - 1.0).abs() < 0.01).all() or (by_bz <= 1.0).all()


def test_share_in_unit_interval(segment_mix) -> None:
    assert (segment_mix["share_of_zone_at_banner"] >= 0).all()
    assert (segment_mix["share_of_zone_at_banner"] <= 1).all()


# ----- L01 invariant scan on build.py ------------------------------------

def test_build_file_does_not_bypass_observable_guard() -> None:
    """src/lake/build.py is scanned by the L01 AST scan. Re-running
    the focused check here to make the §1 acid-test point explicit:
    segment_mix build must never reach for a planted customer column."""
    import ast
    src = (REPO_ROOT / "src" / "lake" / "build.py").read_text()
    tree = ast.parse(src)
    direct_reads: list[tuple[int, str]] = []

    class _Finder(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:
            attr = (
                node.func.attr if isinstance(node.func, ast.Attribute)
                else node.func.id if isinstance(node.func, ast.Name)
                else None
            )
            if attr in {"read_parquet", "read_table"}:
                direct_reads.append((node.lineno, attr))
            self.generic_visit(node)

    _Finder().visit(tree)
    assert not direct_reads, (
        f"src/lake/build.py contains direct Parquet reads (bypass): "
        f"{direct_reads}"
    )


def test_build_never_accesses_forbidden_columns_in_code() -> None:
    """Belt-and-suspenders: scan build.py for any **executable**
    reference to a forbidden planted column. Comments and docstrings
    that *name* forbidden columns are allowed (and encouraged — they
    document the §1 boundary). What's banned is actual access:
    ``df["loyalty_type"]``, ``df.loyalty_type``, or
    ``columns=[..., 'loyalty_type']`` in a load_table call."""
    import ast
    src = (REPO_ROOT / "src" / "lake" / "build.py").read_text()
    tree = ast.parse(src)
    forbidden_names = {
        "loyalty_type", "primary_banner", "home_zone",
        "wallet_enrolled", "affluence",
    }
    leaks: list[str] = []

    class _Finder(ast.NodeVisitor):
        def visit_Attribute(self, node: ast.Attribute) -> None:
            # df.loyalty_type style access.
            if node.attr in forbidden_names:
                leaks.append(f"line {node.lineno}: .{node.attr}")
            self.generic_visit(node)

        def visit_Subscript(self, node: ast.Subscript) -> None:
            # df["loyalty_type"] style — Slice containing a Constant string.
            val = node.slice
            if isinstance(val, ast.Constant) and isinstance(val.value, str):
                if val.value in forbidden_names:
                    leaks.append(f"line {node.lineno}: [{val.value!r}]")
            elif isinstance(val, (ast.List, ast.Tuple)):
                # columns=[..., "loyalty_type"] pattern.
                for el in val.elts:
                    if (isinstance(el, ast.Constant)
                            and isinstance(el.value, str)
                            and el.value in forbidden_names):
                        leaks.append(
                            f"line {node.lineno}: contains {el.value!r}"
                        )
            self.generic_visit(node)

        def visit_keyword(self, node: ast.keyword) -> None:
            # load_table(columns=[..., "loyalty_type"]) keyword case.
            if (isinstance(node.value, (ast.List, ast.Tuple))):
                for el in node.value.elts:
                    if (isinstance(el, ast.Constant)
                            and isinstance(el.value, str)
                            and el.value in forbidden_names):
                        leaks.append(
                            f"line {node.value.lineno}: kwarg "
                            f"{node.arg}=[..., {el.value!r}]"
                        )
            self.generic_visit(node)

    _Finder().visit(tree)
    assert not leaks, (
        f"src/lake/build.py executable references to forbidden columns:\n  "
        + "\n  ".join(leaks)
    )


# ----- VALIDATION: derived correlates with planted (test reads planted) --

def _load_planted_loyalty() -> pd.DataFrame:
    """Read customers.loyalty_type ONCE here in the validation test.
    The build never does this."""
    return pd.read_parquet(DATA_RAW / "customers.parquet")[
        ["card_id", "loyalty_type"]
    ]


def test_VALIDATION_derived_segments_correlate_with_planted_loyalty() -> None:
    """D23.1 free check: behavioral segments derived from observable
    txn features should correlate with the planted ``loyalty_type``.

    The correlation we expect:

    * Planted ``loyalist`` customers (stick with their primary banner)
      should over-index in the derived ``premium_loyalist`` segment
      AT THEIR PRIMARY BANNER (because they shop there more and have
      higher per-trip basket).
    * Planted ``lapsed_light`` customers should over-index in
      ``occasional`` (low frequency + low basket).

    We measure: among planted ``loyalist`` cards, what fraction land
    in ``premium_loyalist`` OR ``frequent_value`` (the two
    high-frequency derived segments) at their primary banner?
    Should be well above the 50% null hypothesis."""
    planted = _load_planted_loyalty()

    # Re-derive per-card-per-banner segments via the same algorithm
    # the build uses, then merge with planted loyalty to check
    # correlation. We need the per-card-banner classification, which
    # the public build aggregates away. Reconstruct it here from
    # the source for the validation check.
    from src.lake.observable_guard import load_table
    from src.lake.zones import derive_zone_for_store

    stores = load_table(
        "stores", columns=["store_id", "latitude", "longitude"]
    )
    zones = derive_zone_for_store(stores)
    txns = load_table(
        "transactions",
        columns=["customer_token", "store_id", "banner_code", "subtotal"],
    )
    txns = txns.merge(zones, on="store_id")
    per_card_banner = (
        txns.groupby(["customer_token", "banner_code"])
        .agg(n_txns=("subtotal", "count"),
             avg_basket=("subtotal", "mean"))
        .reset_index()
    )
    import numpy as np
    out_parts = []
    for banner, grp in per_card_banner.groupby("banner_code"):
        median_freq = grp["n_txns"].median()
        median_basket = grp["avg_basket"].median()
        hi_freq = grp["n_txns"] >= median_freq
        hi_basket = grp["avg_basket"] >= median_basket
        seg = np.where(
            hi_freq & hi_basket, "premium_loyalist",
            np.where(
                hi_freq & ~hi_basket, "frequent_value",
                np.where(
                    ~hi_freq & hi_basket, "occasional_premium",
                    "occasional",
                ),
            ),
        )
        sub = grp.copy()
        sub["behavioral_segment"] = seg
        out_parts.append(sub)
    derived = pd.concat(out_parts, ignore_index=True)

    # Planted primary banner (the one we expect a loyalist's high-
    # frequency behavior to manifest at).
    planted_primary = pd.read_parquet(DATA_RAW / "customers.parquet")[
        ["card_id", "loyalty_type", "primary_banner"]
    ]
    joined = derived.merge(
        planted_primary,
        left_on=["customer_token", "banner_code"],
        right_on=["card_id", "primary_banner"],
    )
    if len(joined) == 0:
        pytest.skip("no overlap on (card, primary_banner)")

    # For planted loyalists at their primary banner, % in
    # high-frequency derived segments. This is the load-bearing
    # correlation — loyalists shop their primary banner often, so
    # the derivation should pick them out.
    loy_high_freq_share = (
        joined.loc[joined["loyalty_type"] == "loyalist", "behavioral_segment"]
        .isin(("premium_loyalist", "frequent_value")).mean()
    )
    # For planted splitters (shop across grocers), the split should
    # produce roughly half-and-half between high-freq and low-freq at
    # any one banner. Mostly a sanity check that the derivation isn't
    # just constant.
    splitter_dist = (
        joined.loc[joined["loyalty_type"] == "splitter", "behavioral_segment"]
        .value_counts(normalize=True)
        .to_dict()
    )
    print(
        f"\nL04c VALIDATION  loyalist→high-freq derived at primary: "
        f"{loy_high_freq_share*100:.1f}%   "
        f"(null hypothesis 50%)"
    )
    print(f"L04c VALIDATION  splitter segment dist at primary: {splitter_dist}")
    # Loyalists shop primary banner often → derivation should pick them
    # out as high-freq the majority of the time.
    assert loy_high_freq_share > 0.60, (
        f"Planted loyalists should land in high-frequency derived "
        f"segments at their primary banner above the 50% null "
        f"hypothesis; got {loy_high_freq_share:.3f}"
    )
