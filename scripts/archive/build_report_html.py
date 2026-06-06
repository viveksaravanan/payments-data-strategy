"""Inline vendored libraries + JSON payload into the v2.5 report HTML.

The HTML at `docs/report.html` carries sentinel-marked regions for
each inlinable asset:

    /* @@PLOTLY_BUNDLE_START@@ */ ... /* @@PLOTLY_BUNDLE_END@@ */
    /* @@LEAFLET_JS_START@@ */    ... /* @@LEAFLET_JS_END@@ */
    /* @@LEAFLET_CSS_START@@ */   ... /* @@LEAFLET_CSS_END@@ */
    window.REPORT_DATA = /* @@REPORT_DATA_START@@ */ null /* @@REPORT_DATA_END@@ */;

This script replaces the content between each sentinel pair with the
corresponding asset. Sentinels themselves are preserved so the script is
idempotent — re-running it produces the same file. Author edits to the
template (HTML / CSS / app JS) live outside the sentinels and survive
any number of rebuilds.

Inputs:

  - vendor/plotly-basic.min.js   (Plotly basic 2.x)
  - vendor/leaflet.js            (Leaflet 1.9.x JS)
  - vendor/leaflet.css           (Leaflet 1.9.x CSS)
  - docs/report_data.json        (output of generate_report_data.py)

Usage::

    uv run python scripts/build_report_html.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML_PATH    = ROOT / "docs" / "report.html"
PAYLOAD_PATH = ROOT / "docs" / "report_data.json"
PLOTLY_PATH  = ROOT / "vendor" / "plotly-basic.min.js"
LEAFLET_JS_PATH  = ROOT / "vendor" / "leaflet.js"
LEAFLET_CSS_PATH = ROOT / "vendor" / "leaflet.css"


def _sentinel_re(name: str) -> re.Pattern[str]:
    """Build a regex that matches `/* @@<name>_START@@ */ ... /* @@<name>_END@@ */`."""
    return re.compile(
        r"(/\* @@" + name + r"_START@@ \*/)(.*?)(/\* @@" + name + r"_END@@ \*/)",
        re.DOTALL,
    )


REGIONS: list[tuple[str, Path, str]] = [
    ("PLOTLY_BUNDLE", PLOTLY_PATH, "code"),
    ("LEAFLET_JS",    LEAFLET_JS_PATH, "code"),
    ("LEAFLET_CSS",   LEAFLET_CSS_PATH, "code"),
    ("REPORT_DATA",   PAYLOAD_PATH,    "json"),
]


def _read(p: Path) -> str:
    if not p.exists():
        raise SystemExit(
            f"Missing input: {p.relative_to(ROOT)}\n"
            f"  Run `make report` first to emit the JSON payload, and ensure "
            f"`vendor/*.{{js,css}}` are present (see README)."
        )
    return p.read_text()


def main() -> None:
    html = _read(HTML_PATH)
    bytes_in: dict[str, int] = {}

    for name, path, kind in REGIONS:
        rx = _sentinel_re(name)
        if not rx.search(html):
            raise SystemExit(f"Sentinels for {name} not found in {HTML_PATH.name}")
        body = _read(path)
        if kind == "json":
            # Re-serialize compactly so the payload is one line.
            body = json.dumps(json.loads(body), separators=(",", ":"))
            wrapper_l, wrapper_r = "", ""
        else:
            wrapper_l, wrapper_r = "\n", "\n"
        bytes_in[name] = len(body)
        html = rx.sub(
            lambda m, b=body, l=wrapper_l, r=wrapper_r:
                m.group(1) + l + b + r + m.group(3),
            html,
        )

    HTML_PATH.write_text(html)
    size_kb = HTML_PATH.stat().st_size / 1024
    inputs = " + ".join(f"{n}={bytes_in[n]:,}B" for n, _, _ in REGIONS)
    print(
        f"[build-report] inlined {inputs} → "
        f"{HTML_PATH.relative_to(ROOT)} ({size_kb:.0f} KB)"
    )


if __name__ == "__main__":
    main()
