"""Wave 3.5 §3 line-item peer lake builder.

Replaces the Wave 2 aggregate lake (`src/lake/build.py`) with a raw
line-item lake the agent queries with SQL via `query_lake_sql`. One
**per-viewer** pair of tables is materialized for each of the five
merchants (§5):

* ``lake_transactions`` — one row per peer purchase line, real units.
* ``lake_stores``       — peer store reference (tokenized id + segment
                          + neighborhood).

Per-viewer means: the viewing merchant's own rows are **excluded**
(structural, not a runtime filter — §3.3) and every remaining row is
labeled ``peer`` (same segment as the viewer) or ``merchant``
(different segment) **relative to that viewer** (§3.2).

Privacy posture (deliberately minimal — §7 / §12): identity reduced
to the ``peer_relationship`` label (never a name or pseudonym),
tokenized non-reversible ids, hour-bucketed timestamps, dropped
consumer linkage, no ZIP/lat-long. Real analytical fields
(`unit_price`, `qty`, payment dims, …) pass through **raw**; the
taxonomy (`department`/`category`/`subcategory`) is the shared
*functional* hierarchy resolved from the `products` join (the
cross-merchant comparison key — the merchant's own labels never reach
the lake). Protection at query time is the `k=50` cell floor (§7.1),
not value coarsening.

Reads `data/raw/` ONLY through `observable_guard.load_table` so the
§1 observable invariant holds: planted columns (`zone_id`,
`customers`/`zones` tables) are never touched. Z-code derivation
(`src/lake/zones.py`) is NOT used — `neighborhood` is the sole
published geography (§2.2).

Determinism (§3.3): tokenization salt is derived from
`cfg.global_['seed']` (fixed), so two runs over the same raw data
produce byte-identical Parquet (consistent with T18).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from src.generate.config.loader import load_config
from src.lake.observable_guard import load_table

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_ROOT = REPO_ROOT / "src" / "generate" / "config"
DATA_LAKE_ITEMS = REPO_ROOT / "data" / "lake" / "items"

# Merchant panel — derived from config (datamodel-v2: KRG/ACM/WDX grocery +
# TBL/BKG/CFA qsr; off-price/TJX dropped). Config-driven so a panel change
# doesn't need a code edit here.
def _valid_merchants() -> tuple[str, ...]:
    cfg = load_config(CONFIG_ROOT)
    return tuple(sorted(m["banner_code"] for m in cfg.merchants.values()))


VALID_MERCHANTS: tuple[str, ...] = _valid_merchants()

# Coarse time-of-day buckets per day (§3.3): hour 0-23 → 0..HOUR_BUCKETS-1.
HOUR_BUCKETS = 10

# Token width — 16 hex chars of SHA-256, matching the card_id convention.
_TOKEN_WIDTH = 16

# Published column order is enforced alphabetically by the deterministic
# writer; these lists document the contract and drive the select.
_TXN_PUBLISHED = [
    "lake_txn_id", "lake_line_id", "lake_store_id",
    "txn_date", "hour_bucket", "peer_relationship",
    "department", "category", "subcategory",
    "unit_price", "qty", "discount", "line_total",
    "payment_type", "card_network", "entry_mode", "wallet_type",
]
_STORE_PUBLISHED = [
    "lake_store_id", "peer_relationship", "peer_segment", "neighborhood",
]


def _segment_by_banner() -> dict[str, str]:
    """Map ``banner_code → segment`` from the config (canonical vocab:
    ``grocery`` / ``qsr`` / ``off_price``). Picks up a 6th merchant
    with no code change (D12)."""
    cfg = load_config(CONFIG_ROOT)
    return {m["banner_code"]: m["segment"] for m in cfg.merchants.values()}


def _salt() -> str:
    """Deterministic tokenization salt derived from the generation seed
    (§3.3) — never random, so builds stay byte-identical."""
    cfg = load_config(CONFIG_ROOT)
    seed = cfg.global_["seed"]
    return f"lake_v35:{seed}"


def _token_map(values: pd.Series, salt: str) -> dict[Any, str]:
    """Build a {raw_value → token} map over the *unique* values only
    (so we hash 1.66M txn ids, not 10.76M line rows). Non-reversible:
    SHA-256(salt:value) truncated to 16 hex chars."""
    out: dict[Any, str] = {}
    for v in values.dropna().unique():
        digest = hashlib.sha256(f"{salt}:{v}".encode()).hexdigest()
        out[v] = digest[:_TOKEN_WIDTH]
    return out


def build_global_lines() -> pd.DataFrame:
    """Build the internal enriched per-line frame for ALL merchants.

    Carries the real ``banner_code`` (used to slice + label per viewer)
    plus tokenized ids and generalized fields. This frame is NEVER
    written to disk — `scope_lines_for_viewer` strips identity before
    anything is published.

    Reads only observable columns via `observable_guard.load_table`.
    The 3-table join runs in DuckDB over the registered pandas frames
    (columnar, low peak memory).
    """
    # datamodel-v2: the line carries only `sku` — category/subcategory now
    # resolve via a join to `products` on sku (the functional taxonomy, the
    # shared comparison key). No taxonomy is denormalized onto the line.
    items = load_table(
        "transaction_items",
        ["txn_id", "line_id", "sku",
         "qty", "unit_price", "discount", "line_total"],
    )
    products = load_table(
        "products",
        ["sku", "functional_department", "functional_category", "functional_subcategory"],
    )
    txns = load_table(
        "transactions",
        ["txn_id", "store_id", "banner_code", "txn_ts",
         "tender", "network", "entry_mode", "wallet_provider", "wallet_at_tap"],
    )
    stores = load_table("stores", ["store_id", "neighborhood"])

    con = duckdb.connect()
    con.register("items", items)
    con.register("products", products)
    con.register("txns", txns)
    con.register("stores", stores)
    # txn_date = date only; hour_bucket = coarse 10-bucket time of day.
    # Payment columns renamed to the stable lake names (§3.1 mapping).
    # The published lake `department`/`category`/`subcategory` are sourced
    # from the products join (functional taxonomy) — the shared cross-merchant
    # comparison key. The merchant's own (divergent) labels are never read
    # into the lake.
    enriched = con.execute(
        """
        SELECT
          i.txn_id                                    AS txn_id,
          i.line_id                                   AS line_id,
          t.store_id                                  AS store_id,
          t.banner_code                               AS banner_code,
          CAST(t.txn_ts AS DATE)                      AS txn_date,
          CAST(EXTRACT(hour FROM t.txn_ts) AS INTEGER) * 10 / 24
                                                      AS hour_bucket,
          p.functional_department                     AS department,
          p.functional_category                       AS category,
          p.functional_subcategory                    AS subcategory,
          i.unit_price                                AS unit_price,
          i.qty                                       AS qty,
          i.discount                                  AS discount,
          i.line_total                                AS line_total,
          t.tender                                    AS payment_type,
          t.network                                   AS card_network,
          t.entry_mode                                AS entry_mode,
          CASE WHEN t.wallet_at_tap
               THEN COALESCE(t.wallet_provider, 'none')
               ELSE 'none' END                        AS wallet_type,
          s.neighborhood                              AS neighborhood
        FROM items i
        JOIN products p ON i.sku = p.sku
        JOIN txns   t USING (txn_id)
        JOIN stores s ON t.store_id = s.store_id
        """
    ).df()
    con.close()
    del items, products, txns, stores

    enriched["hour_bucket"] = enriched["hour_bucket"].astype("int8")
    # Store date-only (date32), not a midnight timestamp — the spec
    # contract is "date only"; time-of-day lives solely in hour_bucket.
    enriched["txn_date"] = pd.to_datetime(enriched["txn_date"]).dt.date

    # Tokenize ids. txn_id + store_id get real hashes (small unique sets);
    # the line token is the txn token + the within-txn sequence — already
    # non-reversible (the sequence integer is not sensitive), avoiding
    # 10.76M fresh hashes.
    salt = _salt()
    txn_tok = _token_map(enriched["txn_id"], salt)
    store_tok = _token_map(enriched["store_id"], salt)
    enriched["lake_txn_id"] = enriched["txn_id"].map(txn_tok)
    enriched["lake_store_id"] = enriched["store_id"].map(store_tok)
    enriched["lake_line_id"] = (
        enriched["lake_txn_id"] + "-" + enriched["line_id"].astype(str)
    )
    return enriched


def scope_lines_for_viewer(
    global_lines: pd.DataFrame, viewer: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Produce the published ``(lake_transactions, lake_stores)`` pair
    for ``viewer`` from the enriched global frame:

    * **Viewer exclusion** — drop the viewer's own rows (§3.3).
    * **Relationship relabel** — ``peer`` if same segment as viewer,
      else ``merchant`` (§3.2).
    * **Identity strip** — the real ``banner_code`` / ``store_id`` /
      ``txn_id`` / ``line_id`` never reach the published frames.
    """
    if viewer not in VALID_MERCHANTS:
        raise ValueError(f"Unknown viewer {viewer!r}.")
    seg = _segment_by_banner()
    viewer_segment = seg[viewer]

    peers = global_lines[global_lines["banner_code"] != viewer].copy()
    rel = peers["banner_code"].map(
        lambda b: "peer" if seg.get(b) == viewer_segment else "merchant"
    )
    peers["peer_relationship"] = rel

    lake_transactions = peers[_TXN_PUBLISHED].reset_index(drop=True)

    # Store reference: one row per peer store.
    store_ref = peers.drop_duplicates(subset=["lake_store_id"]).copy()
    store_ref["peer_segment"] = store_ref["banner_code"].map(seg)
    lake_stores = store_ref[_STORE_PUBLISHED].reset_index(drop=True)

    return lake_transactions, lake_stores


def viewer_metadata(viewer: str) -> dict[str, Any]:
    """The §6 routing record: this viewer's segment + how many OTHER
    merchants share it (`segment_peer_count`). The specialist behavior
    branches on this structurally, not via a prompt instruction."""
    seg = _segment_by_banner()
    viewer_segment = seg[viewer]
    peer_count = sum(
        1 for b, s in seg.items() if b != viewer and s == viewer_segment
    )
    return {"segment": viewer_segment, "segment_peer_count": peer_count}


def build_line_item_lake(out_root: Path | str = DATA_LAKE_ITEMS) -> dict[str, Any]:
    """Build all five per-viewer pairs + the routing metadata file.

    Writes ``<out_root>/<VIEWER>/lake_transactions.parquet`` and
    ``lake_stores.parquet`` (deterministic) plus
    ``<out_root>/metadata.json``. Returns the metadata dict.
    """
    from src.storage.duckdb_io import write_parquet

    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    global_lines = build_global_lines()

    metadata: dict[str, Any] = {}
    for viewer in VALID_MERCHANTS:
        lake_txn, lake_stores = scope_lines_for_viewer(global_lines, viewer)
        vdir = out_root / viewer
        vdir.mkdir(parents=True, exist_ok=True)
        write_parquet(
            lake_txn, vdir / "lake_transactions.parquet",
            sort_keys=["lake_txn_id", "lake_line_id"],
        )
        write_parquet(
            lake_stores, vdir / "lake_stores.parquet",
            sort_keys=["lake_store_id"],
        )
        metadata[viewer] = {
            **viewer_metadata(viewer),
            "n_lines": int(len(lake_txn)),
            "n_stores": int(len(lake_stores)),
        }

    (out_root / "metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n"
    )
    return metadata
