"""HF Spaces entry point. Wraps src/dashboard/app.py.

The Space image ships **code only** — the ~2GB `data/` tree (full-scale
tenant Parquet + the six per-viewer line-item lakes) lives in a companion
HF **Dataset** repo and is downloaded on first boot (see `_ensure_data`
below). This keeps the Space under its Git/LFS cap; regenerate locally,
re-run `make push-data`, and redeploy to refresh.
"""
import os
import sys
import runpy
from pathlib import Path

import streamlit as st

REPO_ROOT = Path(__file__).parent
sys.path.insert(0, str(REPO_ROOT))

# --- Boot-time dataset download ------------------------------------------
# The companion public Dataset repo mirrors what goes under `data/`:
# `raw/*.parquet` + `lake/items/<VIEWER>/*.parquet` (the eval answer key is
# NEVER uploaded). Downloading into REPO_ROOT/data reconstructs the exact
# layout every module expects (data/raw/…, data/lake/items/…), so no path
# code changes. Idempotent: this script re-runs on every Streamlit rerun,
# so we skip the download once the data is present.
DATA_REPO = "viveks2862/payments-data-strategy-data"
DATA_ROOT = REPO_ROOT / "data"


def _ensure_data() -> None:
    if (DATA_ROOT / "raw" / "transactions.parquet").exists():
        return
    from huggingface_hub import snapshot_download  # noqa: PLC0415

    with st.spinner(
        "First boot: downloading the dataset from the Hub (~2 GB, a few "
        "minutes). This happens once per cold start."
    ):
        snapshot_download(
            repo_id=DATA_REPO,
            repo_type="dataset",
            local_dir=str(DATA_ROOT),
        )


try:
    _ensure_data()
except Exception as exc:  # noqa: BLE001 — surface a clear boot error
    st.error(f"Could not load the dataset from the Hub: {exc}")
    st.stop()

# Load Anthropic API key from Streamlit secrets into env (if available).
# Wrapped: accessing st.secrets raises StreamlitSecretNotFoundError when
# there's no .streamlit/secrets.toml (the case locally + on plain Docker).
# On HF Spaces the secrets are materialized, so this branch runs there.
try:
    if "ANTHROPIC_API_KEY" in st.secrets:
        os.environ["ANTHROPIC_API_KEY"] = st.secrets["ANTHROPIC_API_KEY"]
except Exception:
    pass

# Run the dashboard module — re-executes on every Streamlit rerun
runpy.run_path(
    str(REPO_ROOT / "src" / "dashboard" / "app.py"),
    run_name="__main__",
)
