#!/usr/bin/env bash
# Full demo from a clean state: wipe data/ and the SQLite file, regenerate
# everything, and launch the Streamlit dashboard. Equivalent to
# `make clean && make seed && make demo`.
#
# Run from the repo root:    bash scripts/demo.sh
# Or invoke executable:       ./scripts/demo.sh
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> [1/3] make clean — wiping data/ and the SQLite database"
make clean

echo "==> [2/3] make seed — generate raw and load SQLite"
make seed

echo "==> [3/3] make demo — launching Streamlit on http://localhost:8501 (Ctrl-C to stop)"
make demo
