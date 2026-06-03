"""Build the Wave 3.5 per-viewer line-item peer lake.

Runs `src.lake.build_line_items.build_line_item_lake` against
`data/raw/` and writes five per-viewer pairs to
`data/lake/items/<VIEWER>/` plus a routing `metadata.json`.

Usage: ``uv run python scripts/build_line_items.py``  (or ``make lake-items``)
"""
from __future__ import annotations

import time

from src.lake.build_line_items import DATA_LAKE_ITEMS, build_line_item_lake


def main() -> None:
    t0 = time.time()
    print("Building line-item peer lake (5 per-viewer pairs)…", flush=True)
    metadata = build_line_item_lake()
    for viewer, meta in sorted(metadata.items()):
        print(
            f"  {viewer}: segment={meta['segment']} "
            f"peers={meta['segment_peer_count']} "
            f"lines={meta['n_lines']:,} stores={meta['n_stores']}",
            flush=True,
        )
    print(
        f"Done in {time.time() - t0:.1f}s → {DATA_LAKE_ITEMS}", flush=True,
    )


if __name__ == "__main__":
    main()
