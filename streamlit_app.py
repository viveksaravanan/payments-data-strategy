"""Streamlit Community Cloud entry point.

This shim lives at the repository root so `share.streamlit.io` can point
its build at `streamlit_app.py`. It does four things, in order:

  1. Ensures the repository root is on `sys.path` so `from src.dashboard
     import app` works regardless of working directory.
  2. Promotes `ANTHROPIC_API_KEY` from Streamlit secrets into the
     environment, since `src/agents/llm.py` (and the rest of the agent
     stack) reads the key via `os.environ.get`.
  3. Regenerates `data/payments.db` from the committed catalogs if the
     file is missing — the 362 MB DB is gitignored, so cold starts on
     Streamlit Cloud need to build it once. Runtime is ~2 minutes
     locally; expect 3–4 minutes on Streamlit's shared CPU.
  4. Imports `src.dashboard.app`, which runs at module-import time
     (no `main()` function) and renders the page.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import streamlit as st

# ---------------------------------------------------------------------------
# 1. Path setup — repo root on sys.path so `from src...` imports work.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# 2. Secrets → environment.
# Streamlit Cloud surfaces `[secrets]` from the deployment dashboard as
# `st.secrets`. Locally, this falls back to `.env` (loaded by python-dotenv
# inside src/agents/llm.py) so this shim is a no-op when developing.
# ---------------------------------------------------------------------------
try:
    if "ANTHROPIC_API_KEY" in st.secrets and not os.environ.get("ANTHROPIC_API_KEY"):
        os.environ["ANTHROPIC_API_KEY"] = st.secrets["ANTHROPIC_API_KEY"]
except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
    # No secrets.toml — that's fine locally. Agents will fall through to
    # `.env` or run in mock mode.
    pass
except Exception:  # noqa: BLE001 — never crash the dashboard for telemetry
    pass


# ---------------------------------------------------------------------------
# 3. Database bootstrap.
# `data/payments.db` is gitignored (362 MB). Build it on cold start.
# Reads only from `data/catalogs/*.json` (committed), so this is purely
# deterministic from the seed.
# ---------------------------------------------------------------------------
_DB_PATH = _REPO_ROOT / "data" / "payments.db"


def _build_database() -> None:
    """Run the same generation pipeline as `make seed`:
      uv run python -m src.generate.run_all
      uv run python -m src.db.seed
    Imported in-process here (no subprocesses) so we stay inside the
    Streamlit runtime."""
    from src.generate import run_all as _gen
    from src.db import seed as _seed
    _gen.main()
    _seed.main()


if not _DB_PATH.exists():
    with st.spinner(
        "First run — building the synthetic transaction database "
        "(~2–4 minutes, one-time cost on cold start)…"
    ):
        _build_database()
    # After the spinner closes the rest of the page renders normally;
    # no rerun needed because the dashboard module hasn't been imported
    # yet on this run.


# ---------------------------------------------------------------------------
# 4. Render the dashboard.
# `src/dashboard/app.py` runs at module level — importing it triggers
# the Streamlit script.
# ---------------------------------------------------------------------------
from src.dashboard import app  # noqa: E402, F401
