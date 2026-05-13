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
     Streamlit Cloud need to build it once.

     The build runs in a background daemon thread; the main Streamlit
     script returns in <100 ms and polls `_DB_PATH.exists()` via
     `st.rerun()` every 2 s. This keeps Streamlit's websocket keepalive
     pings responsive throughout the 3–4 minute build — without it, the
     browser disconnects with `ConnectionClosedError (1011)` after ~30 s.
  4. Imports `src.dashboard.app`, which runs at module-import time
     (no `main()` function) and renders the page.
"""
from __future__ import annotations

import os
import sys
import threading
import time
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
# 3. Database bootstrap — background thread + auto-rerun loop.
#
# Why a thread instead of inline build: the generation pipeline is
# CPU-bound (pandas + SQLite inserts) and holds the GIL for 2-4 minutes.
# Streamlit's websocket server runs in the same process and depends on
# the main thread yielding to answer browser keepalive pings. A blocking
# inline build kills the websocket with code 1011 after ~30 s.
#
# A daemon thread does the work; the main script returns in <100 ms and
# reruns every 2 s until the DB appears. Each rerun is a full Streamlit
# script pass — the websocket stays responsive throughout.
#
# State persistence: Streamlit runs the script in a fresh namespace on
# every rerun, so module-level globals do NOT survive between reruns. The
# thread handle and start-time live behind `@st.cache_resource`, which
# caches at the server-process level and DOES persist across reruns and
# across all browser sessions hitting the same container.
# ---------------------------------------------------------------------------
_DB_PATH = _REPO_ROOT / "data" / "payments.db"
_BUILD_LOG = _REPO_ROOT / "data" / ".build.log"


def _build_log(msg: str) -> None:
    """Append a timestamped line to data/.build.log so we can post-mortem
    a stalled build from the filesystem. Cheap and thread-safe enough."""
    try:
        with open(_BUILD_LOG, "a") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
            f.flush()
    except Exception:  # noqa: BLE001
        pass


@st.cache_resource
def _ensure_build_started() -> dict:
    """Process-wide singleton: starts the build thread exactly once per
    server process and returns a shared state dict whose identity
    persists across all script reruns and browser sessions.

    Because this is cached via `st.cache_resource`, the FIRST call (any
    rerun on any session) starts the thread; all subsequent calls return
    the same dict. This is the only correct way to share state across
    Streamlit reruns — module-level globals get reinitialized on every
    rerun, so the previous "_BUILD_STATE + lock" approach spawned a new
    thread every 2 s.
    """
    state: dict = {"started_at": time.time(), "thread": None, "error": None}

    def _run() -> None:
        import traceback
        _build_log("build thread: start")
        try:
            _build_log("build thread: importing generators")
            from src.generate import run_all as _gen
            from src.db import seed as _seed
            _build_log("build thread: calling run_all.main()")
            _gen.main()
            _build_log("build thread: calling db.seed.main()")
            _seed.main()
            _build_log("build thread: complete")
        except Exception as exc:  # noqa: BLE001 — surface to UI via state
            state["error"] = f"{type(exc).__name__}: {exc}"
            _build_log(f"build thread: FAILED — {type(exc).__name__}: {exc}")
            _build_log(traceback.format_exc())

    t = threading.Thread(target=_run, daemon=True)
    state["thread"] = t
    t.start()
    return state


def _render_build_status_and_rerun(state: dict) -> None:
    """Render the building-the-DB status page and schedule a rerun in 2 s.
    Surfaces build errors via `st.error` + `st.stop` so we don't loop
    forever on a dead thread."""
    elapsed = time.time() - state["started_at"]
    t = state["thread"]

    # Dead-thread guard: thread exited but DB never appeared.
    if t is not None and not t.is_alive() and not _DB_PATH.exists():
        msg = state["error"] or "build thread exited without producing the DB"
        st.error(f"Database build failed: {msg}")
        st.stop()

    st.title("Preparing the demo…")
    st.markdown(
        "Building the synthetic transaction database. One-time cost on "
        "first load after the app wakes from sleep — **expected ~3-4 minutes** "
        "on Streamlit Cloud's shared CPU. The page will refresh automatically "
        "every couple of seconds."
    )
    # Cap at 95% until the DB actually appears so the bar doesn't claim
    # done before the seed finishes. 240s = 4 min budget.
    st.progress(min(elapsed / 240.0, 0.95))
    st.caption(f"Elapsed: {elapsed:.0f}s")
    time.sleep(2)
    st.rerun()


if not _DB_PATH.exists():
    _state = _ensure_build_started()   # starts thread once, same dict every rerun
    _render_build_status_and_rerun(_state)  # exits via st.rerun()


# ---------------------------------------------------------------------------
# 4. Render the dashboard.
# `src/dashboard/app.py` runs at module level — importing it triggers
# the Streamlit script. Only reached once `data/payments.db` exists.
# ---------------------------------------------------------------------------
from src.dashboard import app  # noqa: E402, F401
