"""HF Spaces entry point. Wraps src/dashboard/app.py.

The Anthropic API key is provided via HF Spaces "Secrets" and appears
in os.environ automatically — no special loading needed.

The payments.db is shipped via Git LFS, so no regeneration or download.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent
sys.path.insert(0, str(REPO_ROOT))

# DB is committed via LFS; verify it exists
DB_PATH = REPO_ROOT / "data" / "payments.db"
if not DB_PATH.exists():
    import streamlit as st
    st.error(
        f"Database not found at {DB_PATH}. "
        "Check that Git LFS pulled the file correctly during Space build."
    )
    st.stop()

# Import dashboard — module-level Streamlit code runs on import
from src.dashboard import app  # noqa: F401, E402
