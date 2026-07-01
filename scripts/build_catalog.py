"""Static catalog authoring script (datamodel-v2, Phase 2).

Curated-combinatorial (§A12): deterministically expands the committed
controlled vocabularies into a per-banner item master and emits TWO
artifacts, preserving the observable/hidden split:

  * ``data/catalog/products.csv``  — OBSERVABLE. One row per (banner, SKU)
    with the dual taxonomy (merchant_* + functional_*), real names, PL as
    distinct SKU records, and a baked ``shelf_price``. **No canonical_id.**
    Read by the pipeline / lake / agents / dashboard.
  * ``data/eval/canonical_map.csv`` — HIDDEN answer key. ``banner_code, sku,
    canonical_id`` only. Lives in the eval area; never read by lake/agents.

The catalog is REFERENCE DATA, not simulation output — authored once and
committed (``make catalog``), then read as a build input by the engine
(Phase 7). Fully deterministic: same vocabularies -> byte-identical CSVs
(no RNG; all variation is hash- or index-derived).

Run: ``python scripts/build_catalog.py`` (or ``make catalog``).
"""
from __future__ import annotations

import csv
import hashlib
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
VOCAB_DIR = REPO_ROOT / "src" / "generate" / "config" / "catalog"
CATALOG_OUT = REPO_ROOT / "data" / "catalog" / "products.csv"
CANON_OUT = REPO_ROOT / "data" / "eval" / "canonical_map.csv"

GROCERS = ("KRG", "ACM", "WDX")

# ---- Grocery assortment sizing (§A8: KRG ~1350 / ACM ~1250 / WDX ~1050) ----
# The controlled vocab holds ~600 canonical concepts; SCALE expands each
# subcategory combinatorially (type x size x brand). Per-(role, banner)
# carriage then differentiates the assortments: premium tail leans ACM,
# value tail leans WDX, and WDX trims mainstream (leanest banner).
SCALE = 2.5
CARRIAGE = {
    #  role         KRG    ACM    WDX
    "core":       {"KRG": 1.00, "ACM": 1.00, "WDX": 1.00},
    "mainstream": {"KRG": 1.00, "ACM": 0.88, "WDX": 0.72},
    "premium":    {"KRG": 0.60, "ACM": 1.00, "WDX": 0.22},
    "value":      {"KRG": 0.60, "ACM": 0.24, "WDX": 1.00},
}

# ---- Pricing levers baked into shelf_price (§A7/§C7; ported from the v1
# transaction-time pricing chain, minus zone/time/noise). ----
# Per-banner functional-department positioning. Each banner is cheap on
# some departments and marks up others, and the cheapest banner FLIPS by
# department -> "no banner cheapest on everything" (T14) holds from shelf
# prices alone.
_DEPT_MULT = {
    "ACM": {  # premium — marks up broadly, organic edge on produce
        "Dairy & Eggs": 1.06, "Produce": 0.96, "Meat & Seafood": 1.05,
        "Dry Grocery": 1.07, "Snacks & Candy": 1.07, "Beverages": 1.06,
        "Frozen": 1.05, "Bakery": 1.05, "Health & Household": 1.05,
        "Baby & Pet": 1.06,
    },
    "KRG": {  # mainstream — cheap on meat + center store + baby/pet
        "Dairy & Eggs": 1.01, "Produce": 1.01, "Meat & Seafood": 0.96,
        "Dry Grocery": 0.97, "Snacks & Candy": 1.00, "Beverages": 0.98,
        "Frozen": 1.00, "Bakery": 1.00, "Health & Household": 1.00,
        "Baby & Pet": 0.98,
    },
    "WDX": {  # value — cheap on staples; at par on produce/meat
        "Dairy & Eggs": 0.95, "Produce": 1.02, "Meat & Seafood": 0.99,
        "Dry Grocery": 0.99, "Snacks & Candy": 0.96, "Beverages": 0.94,
        "Frozen": 0.96, "Bakery": 0.94, "Health & Household": 0.95,
        "Baby & Pet": 1.00,
    },
}
# Private-label discount factor per banner (§A12 tier 2; WDX deepest).
_PL_FACTOR = {"KRG": 0.75, "ACM": 0.85, "WDX": 0.65}
# KVI subcategories: shoppers price-check -> spread dampened toward 1.0.
# Specialty: differentiation amplified -> wider spread. (Flags come off
# the vocab's kvi/specialty booleans.)
_KVI_DAMPEN = 0.35
_SPECIALTY_AMPLIFY = 1.5


def _uni(*parts: str) -> float:
    """Deterministic uniform in [0, 1) from a stable hash of the parts."""
    h = hashlib.sha256("|".join(parts).encode()).digest()
    return int.from_bytes(h[:6], "big") / 2 ** 48


def _competitive_index(banner: str, canonical_id: str) -> float:
    """Deterministic +/-5% per (banner, canonical) so gaps don't fully
    derive from department + tier (ported from v1 pricing)."""
    return 1.0 + (_uni("comp", banner, canonical_id) - 0.5) * 0.10


def _sku_code(banner: str, dep: str, sub_idx: int, seq: int) -> str:
    """Per-banner SKU code with divergent real-world-ish formats (§A12)."""
    if banner == "KRG":
        return f"KRG-{dep[:2].upper()}-{sub_idx:02d}-{seq:04d}"
    if banner == "ACM":
        n = int(_uni("acm-sku", str(sub_idx), str(seq)) * 9_000_000) + 1_000_000
        return f"{n:07d}-{seq % 100:02d}"
    # WDX
    n = int(_uni("wdx-sku", str(sub_idx), str(seq)) * 900_000) + 100_000
    return f"WDX{n:06d}"


def _shelf_price(banner: str, dep: str, anchor: float, spread: float,
                 kvi: bool, specialty: bool, private_label: bool,
                 canonical_id: str) -> float:
    """Baked realized price (§A7/§C7). anchor x within-subcat spread x
    banner-department positioning (KVI-tight / specialty-wide) x PL factor
    x per-SKU competitive index. No zone/time/line modifiers (flat)."""
    base = anchor * spread
    dept_mult = _DEPT_MULT[banner].get(dep, 1.0)
    delta = dept_mult - 1.0
    if kvi:
        delta *= _KVI_DAMPEN
    elif specialty:
        delta *= _SPECIALTY_AMPLIFY
    price = base * (1.0 + delta) * _competitive_index(banner, canonical_id)
    if private_label:
        price *= _PL_FACTOR[banner]
    return round(price, 2)


def _grocery_rows():
    """Generate (products_rows, canonical_rows) for grocery."""
    vocab = yaml.safe_load((VOCAB_DIR / "grocery_vocab.yaml").read_text())
    pl_brands = vocab["pl_brands"]
    brand_pools = vocab["brand_pools"]

    products: list[dict] = []
    canon: list[dict] = []
    seq = {b: 0 for b in GROCERS}          # per-banner running SKU counter
    sub_idx = 0                             # global subcategory index

    for dp in vocab["departments"]:
        fdept = dp["functional_department"]
        mdept = dp["merchant_department"]
        pool = brand_pools[dp["brand_key"]]
        for cat in dp["categories"]:
            fcat = cat["functional_category"]
            mcat = cat["merchant_category"]
            for sc in cat["subcategories"]:
                sub_idx += 1
                fsub = sc["name"]
                anchor = float(sc["anchor"])
                role = sc["role"]
                kvi = bool(sc.get("kvi", False))
                specialty = bool(sc.get("specialty", False))
                pl_share = float(sc.get("pl_share", 0.0))
                noun = sc["noun"]
                types = sc["types"]
                sizes = sc["sizes"]
                count = int(round(sc["count"] * SCALE))
                n_pl = int(round(count * pl_share))
                n_nat = count - n_pl
                T, S, B = len(types), len(sizes), len(pool)

                for i in range(count):
                    typ = types[i % T]
                    size = sizes[(i // T) % S]
                    is_pl = i >= n_nat
                    tier = "PL" if is_pl else "NB"
                    canonical_id = (
                        f"GRO.{fdept[:3].upper()}.{sub_idx:02d}.{i:03d}.{tier}"
                    )
                    # Within-subcategory price spread: deterministic ramp
                    # +/-15% by position so SKUs vary without RNG.
                    spread = 0.85 + 0.30 * (i / max(1, count - 1))
                    for banner in GROCERS:
                        keep = _uni("carry", banner, canonical_id) < \
                            CARRIAGE[role][banner]
                        if not keep:
                            continue
                        seq[banner] += 1
                        if is_pl:
                            brand = pl_brands[banner]
                        else:
                            brand = pool[(i // (T * S)) % B]
                        product_name = f"{brand} {typ} {noun}, {size}"
                        sku = _sku_code(banner, fdept, sub_idx, seq[banner])
                        price = _shelf_price(
                            banner, fdept, anchor, spread, kvi, specialty,
                            is_pl, canonical_id,
                        )
                        products.append({
                            "segment": "grocery",
                            "banner_code": banner,
                            "merchant_id": banner,
                            "sku": sku,
                            "product_name": product_name,
                            "description": f"{product_name} — {fsub}",
                            "brand": brand,
                            "private_label": is_pl,
                            "size": size,
                            "merchant_department": mdept[banner],
                            "merchant_category": mcat[banner],
                            "merchant_subcategory": fsub,
                            "functional_department": fdept,
                            "functional_category": fcat,
                            "functional_subcategory": fsub,
                            "shelf_price": price,
                        })
                        canon.append({
                            "banner_code": banner,
                            "sku": sku,
                            "canonical_id": canonical_id,
                        })
    return products, canon


def _qsr_rows():
    """Generate (products_rows, canonical_rows) for QSR from real menus."""
    path = VOCAB_DIR / "qsr_menus.yaml"
    if not path.exists():
        print(f"  WARNING: {path.name} not found — skipping QSR rows.")
        return [], []
    menus = yaml.safe_load(path.read_text())
    products: list[dict] = []
    canon: list[dict] = []
    for banner in ("TBL", "BKG", "CFA"):     # deterministic banner order
        bdata = menus["banners"][banner]
        seq = 0
        for it in bdata["items"]:
            seq += 1
            sku = f"{banner}-{seq:04d}"
            fcat = it["functional_category"]
            fsub = it["functional_subcategory"]
            mcat = it.get("merchant_category", fcat)
            msub = it.get("merchant_subcategory", fsub)
            products.append({
                "segment": "qsr",
                "banner_code": banner,
                "merchant_id": banner,
                "sku": sku,
                "product_name": it["product_name"],
                "description": f"{it['product_name']} — {fsub}",
                "brand": bdata["name"],
                "private_label": False,      # QSR menu is the menu; no PL
                "size": it.get("size", ""),
                "merchant_department": "Menu",
                "merchant_category": mcat,
                "merchant_subcategory": msub,
                "functional_department": "QSR Menu",
                "functional_category": fcat,
                "functional_subcategory": fsub,
                "shelf_price": round(float(it["shelf_price"]), 2),
            })
            canon.append({
                "banner_code": banner,
                "sku": sku,
                "canonical_id": it["canonical"],
            })
    return products, canon


_PRODUCT_COLUMNS = [
    "segment", "banner_code", "merchant_id", "sku", "product_name",
    "description", "brand", "private_label", "size",
    "merchant_department", "merchant_category", "merchant_subcategory",
    "functional_department", "functional_category", "functional_subcategory",
    "shelf_price",
]
_CANON_COLUMNS = ["banner_code", "sku", "canonical_id"]


def build() -> None:
    g_prod, g_canon = _grocery_rows()
    q_prod, q_canon = _qsr_rows()
    products = g_prod + q_prod
    canon = g_canon + q_canon

    # Deterministic sort: segment, banner, sku.
    products.sort(key=lambda r: (r["segment"], r["banner_code"], r["sku"]))
    canon.sort(key=lambda r: (r["banner_code"], r["sku"]))

    CATALOG_OUT.parent.mkdir(parents=True, exist_ok=True)
    CANON_OUT.parent.mkdir(parents=True, exist_ok=True)

    with CATALOG_OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_PRODUCT_COLUMNS)
        w.writeheader()
        w.writerows(products)
    with CANON_OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_CANON_COLUMNS)
        w.writeheader()
        w.writerows(canon)

    # Report.
    from collections import Counter
    by_banner = Counter(r["banner_code"] for r in products)
    pl_by_banner = Counter(
        r["banner_code"] for r in products if r["private_label"]
    )
    print(f"  wrote {CATALOG_OUT.relative_to(REPO_ROOT)}  ({len(products):,} rows)")
    print(f"  wrote {CANON_OUT.relative_to(REPO_ROOT)}  ({len(canon):,} rows, hidden)")
    print("  SKU counts per banner:")
    for b in ("KRG", "ACM", "WDX", "TBL", "BKG", "CFA"):
        n = by_banner.get(b, 0)
        pl = pl_by_banner.get(b, 0)
        pl_pct = f"{100 * pl / n:.0f}%" if n else "—"
        print(f"    {b}: {n:>5}   (PL rows {pl}, {pl_pct})")


if __name__ == "__main__":
    build()
