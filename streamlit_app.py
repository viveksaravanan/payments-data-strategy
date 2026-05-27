"""HF Spaces entry point. Wraps src/dashboard/app.py.

If data/payments.db is missing (cold container boot), regenerate it
from the committed catalogs by running the two seed modules in-process.
The DB is not tracked in git — it exceeds the HF Spaces free-tier
1 GB repo cap once the v3 per-viewer materialized lake is included.
HF Pro keeps the container warm, so this one-time ~2-minute setup
runs per container instance, not per visitor.
"""
import os
import subprocess
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

DB_PATH = REPO_ROOT / "data" / "payments.db"

if not DB_PATH.exists():
    setup_slot = st.empty()
    with setup_slot.container():
        st.info(
            "First-boot setup: generating the synthetic panel and "
            "building the SQLite database. Takes ~2 minutes and "
            "only runs once per container."
        )
        log_slot = st.empty()
        with st.spinner("Step 1/2 — generating synthetic data…"):
            result = subprocess.run(
                [sys.executable, "-m", "src.generate.run_all"],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                log_slot.code(result.stdout + result.stderr)
                st.error("Data generation failed.")
                st.stop()
        with st.spinner("Step 2/2 — loading SQLite database…"):
            result = subprocess.run(
                [sys.executable, "-m", "src.db.seed"],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                log_slot.code(result.stdout + result.stderr)
                st.error("Database load failed.")
                st.stop()
    setup_slot.empty()
    st.rerun()

# Run the dashboard module — re-executes on every Streamlit rerun
runpy.run_path(
    str(REPO_ROOT / "src" / "dashboard" / "app.py"),
    run_name="__main__",
)
