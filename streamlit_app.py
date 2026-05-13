"""HF Spaces entry point. Wraps src/dashboard/app.py."""
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

# Verify DB exists (LFS-tracked, should always be present in HF Spaces)
DB_PATH = REPO_ROOT / "data" / "payments.db"
if not DB_PATH.exists():
    st.error(
        f"Database not found at {DB_PATH}. "
        "Check that Git LFS pulled the file correctly during Space build."
    )
    st.stop()

# Run the dashboard module — re-executes on every Streamlit rerun
runpy.run_path(
    str(REPO_ROOT / "src" / "dashboard" / "app.py"),
    run_name="__main__",
)
