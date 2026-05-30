"""HF Spaces entry point. Wraps src/dashboard/app.py.

Wave 1 (v4): the v3 SQLite cold-boot path (subprocess to
`src.generate.run_all` + `src.db.seed`) has been removed because
`src/db/seed.py` is quarantined and the dashboard hasn't been
rewired to the new DuckDB+Parquet engine yet. The v4 deploy path
is rebuilt in a later wave; for the working v3 deploy, check out
the `v3-final` tag.
"""
import os
import sys
import runpy
from pathlib import Path

import streamlit as st

REPO_ROOT = Path(__file__).parent
sys.path.insert(0, str(REPO_ROOT))

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
