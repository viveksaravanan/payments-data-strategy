"""Phase 2 catalog tests.

Validates the shared base + per-grocer overlay model: per-grocer SKU
counts, cross-grocer canonical-name overlap, and per-tier price ratios
(Acme = Kroger × 1.03 × ±2% in tight tier; × 1.07 in loose; Winn-Dixie
inverse). Reads the SQLite DB produced by `make seed`, so it requires a
fresh seed in CI.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "payments.db"

TIGHT_CATEGORIES = {"DAIRY", "BAKERY", "PRODUCE", "MEAT", "PANTRY", "SNACKS",
                    "BEVERAGES", "FROZEN"}
LOOSE_CATEGORIES = {"HOUSEHOLD", "PERSONAL", "BABY", "PET"}

# Two-tier multipliers per `data/catalogs/overlays/*.json`. Per-SKU ±2%
# noise is applied at catalog-build time.
TIGHT_MULTIPLIERS = {"KRG": 1.00, "ACM": 1.03, "WDX": 0.97}
LOOSE_MULTIPLIERS = {"KRG": 1.00, "ACM": 1.07, "WDX": 0.93}

# Per-grocer SKU-count targets per the design doc, with a ±5% band.
SKU_COUNT_TARGETS = {
    "KRG": (1100, 0.05),
    "ACM": (1000, 0.05),
    "WDX": (880, 0.05),
}


@pytest.fixture(scope="module")
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    yield c
    c.close()


# ---------------------------------------------------------------------------
# SKU counts
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("merchant,target_band", list(SKU_COUNT_TARGETS.items()))
def test_grocer_sku_count_in_band(
    conn: sqlite3.Connection, merchant: str, target_band: tuple[int, float]
) -> None:
    target, tol = target_band
    actual = conn.execute(
        "SELECT COUNT(*) FROM tenant_products WHERE merchant_id = ?",
        (merchant,),
    ).fetchone()[0]
    lo = int(target * (1 - tol))
    hi = int(target * (1 + tol))
    assert lo <= actual <= hi, (
        f"{merchant} SKU count {actual} outside ±{tol:.0%} of target {target} "
        f"(allowed range {lo}..{hi})"
    )


# ---------------------------------------------------------------------------
# Cross-grocer canonical-name overlap
# ---------------------------------------------------------------------------

def test_cross_grocer_skus_share_canonical_name(conn: sqlite3.Connection) -> None:
    """For SKUs whose merchant-prefixed code maps to the same canonical
    code across grocers (e.g. KRG-PRODUCE-0001 and ACM-PRODUCE-0001),
    the `name`, `category`, and `subcategory` fields must match."""
    rows = conn.execute(
        """
        SELECT substr(sku, 5) AS canonical, merchant_id, name, category, subcategory
        FROM tenant_products
        WHERE merchant_id IN ('KRG', 'ACM', 'WDX')
        """
    ).fetchall()

    # Group by canonical suffix; each group should have identical metadata.
    groups: dict[str, list[tuple]] = {}
    for canonical, merchant, name, category, subcategory in rows:
        groups.setdefault(canonical, []).append(
            (merchant, name, category, subcategory)
        )

    multi_grocer = {k: v for k, v in groups.items() if len(v) > 1}
    assert len(multi_grocer) > 800, (
        f"Expected >800 canonical SKUs carried by ≥2 grocers, got "
        f"{len(multi_grocer)}"
    )

    for canonical, entries in multi_grocer.items():
        names = {e[1] for e in entries}
        cats = {e[2] for e in entries}
        subs = {e[3] for e in entries}
        assert len(names) == 1, (
            f"Canonical {canonical}: name disagreement across grocers: {names}"
        )
        assert len(cats) == 1, (
            f"Canonical {canonical}: category disagreement: {cats}"
        )
        assert len(subs) == 1, (
            f"Canonical {canonical}: subcategory disagreement: {subs}"
        )


# ---------------------------------------------------------------------------
# Per-tier price ratios
# ---------------------------------------------------------------------------

def _shared_skus_with_prices(conn: sqlite3.Connection) -> dict[str, dict[str, dict]]:
    """For every canonical SKU carried by Kroger plus at least one of
    {Acme, Winn-Dixie}, return a mapping
    canonical -> {merchant -> {"price": float, "category": str}}.
    """
    rows = conn.execute(
        """
        SELECT substr(sku, 5) AS canonical, merchant_id, base_price, category
        FROM tenant_products
        WHERE merchant_id IN ('KRG', 'ACM', 'WDX')
        """
    ).fetchall()
    out: dict[str, dict[str, dict]] = {}
    for canonical, merchant, price, category in rows:
        out.setdefault(canonical, {})[merchant] = {
            "price": float(price), "category": category
        }
    # Keep only canonical SKUs Kroger carries.
    return {k: v for k, v in out.items() if "KRG" in v}


def _expected_price(kroger_price: float, multiplier: float) -> tuple[float, float]:
    """Return the [lo, hi] band allowing ±2% per-SKU noise on top of the
    multiplier — but compute the band conservatively to account for the
    fact that Kroger also has its own ±2% noise."""
    expected = kroger_price * multiplier
    band = 0.04  # ±2% noise on each side compounds → ~±4% combined
    return expected * (1 - band) - 0.01, expected * (1 + band) + 0.01


def test_acme_price_ratios_match_two_tier_scheme(conn: sqlite3.Connection) -> None:
    skus = _shared_skus_with_prices(conn)
    n_checked_tight = 0
    n_checked_loose = 0
    for canonical, by_m in skus.items():
        if "ACM" not in by_m:
            continue
        kroger_price = by_m["KRG"]["price"]
        acme_price = by_m["ACM"]["price"]
        category = by_m["KRG"]["category"]
        if category in TIGHT_CATEGORIES:
            mult = TIGHT_MULTIPLIERS["ACM"]
            n_checked_tight += 1
        elif category in LOOSE_CATEGORIES:
            mult = LOOSE_MULTIPLIERS["ACM"]
            n_checked_loose += 1
        else:
            pytest.fail(f"Category {category!r} not in tight or loose tier")
        lo, hi = _expected_price(kroger_price, mult)
        assert lo <= acme_price <= hi, (
            f"Acme price {acme_price:.2f} for canonical {canonical} "
            f"({category}) outside expected band [{lo:.2f}, {hi:.2f}] for "
            f"Kroger {kroger_price:.2f} × {mult:.2f}"
        )
    assert n_checked_tight > 500, "Too few tight-tier SKUs checked"
    assert n_checked_loose > 100, "Too few loose-tier SKUs checked"


def test_winn_dixie_price_ratios_match_two_tier_scheme(
    conn: sqlite3.Connection,
) -> None:
    skus = _shared_skus_with_prices(conn)
    for canonical, by_m in skus.items():
        if "WDX" not in by_m:
            continue
        kroger_price = by_m["KRG"]["price"]
        wdx_price = by_m["WDX"]["price"]
        category = by_m["KRG"]["category"]
        if category in TIGHT_CATEGORIES:
            mult = TIGHT_MULTIPLIERS["WDX"]
        elif category in LOOSE_CATEGORIES:
            mult = LOOSE_MULTIPLIERS["WDX"]
        else:
            pytest.fail(f"Category {category!r} not in tight or loose tier")
        lo, hi = _expected_price(kroger_price, mult)
        assert lo <= wdx_price <= hi, (
            f"Winn-Dixie price {wdx_price:.2f} for canonical {canonical} "
            f"({category}) outside expected band [{lo:.2f}, {hi:.2f}] for "
            f"Kroger {kroger_price:.2f} × {mult:.2f}"
        )


def test_no_organic_or_ebt_eligible_in_products(conn: sqlite3.Connection) -> None:
    """Phase 2 dropped both columns from the product schema."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info('tenant_products')")}
    assert "is_organic" not in cols
    assert "ebt_eligible" not in cols
