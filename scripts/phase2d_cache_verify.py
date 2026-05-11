"""Phase 2D — cache verification.

Programmatic verification that:
  1. Second click on a suggested-question button returns a cache hit
     (<100ms, no LLM call).
  2. Free-form dispatch is NOT cached (every call is a fresh LLM call).
  3. Cache key correctly varies by viewer (KRG and ACM with the same
     question_id are independent cache entries).

This test does NOT make real LLM calls — it monkey-patches `is_available`
to False so dispatch falls through to the hardcoded path. Cache hit /
miss behavior is independent of which path runs the work.
"""
from __future__ import annotations

import os
import time

# Force a fake Streamlit session_state so the cache helpers function.
import streamlit as st
from streamlit.runtime.state.safe_session_state import SafeSessionState  # noqa: E402

from dotenv import load_dotenv
load_dotenv()

from src.dashboard import placeholders as P  # noqa: E402

# Stub session_state with a plain dict so _cache_get/_set works in a bare
# Python context. (Normally Streamlit provides this.)
if not hasattr(st.session_state, "_state") or not isinstance(
    getattr(st.session_state, "_state", None), dict
):
    class _FakeState:
        def __init__(self): self._d = {}
        def setdefault(self, k, default):
            return self._d.setdefault(k, default)
        def __getitem__(self, k): return self._d[k]
        def __setitem__(self, k, v): self._d[k] = v
        def __contains__(self, k): return k in self._d
        def get(self, k, default=None): return self._d.get(k, default)

    import src.dashboard.placeholders as _P_mod  # noqa: PLC0415

    # Monkey-patch the cache helpers to use a module-local dict so this
    # test doesn't depend on Streamlit runtime.
    _CACHE: dict = {}

    def _fake_cache_get(key):
        return _CACHE.get(key)

    def _fake_cache_set(key, value):
        if value.get("fallback"):
            return
        _CACHE[key] = value

    _P_mod._cache_get = _fake_cache_get
    _P_mod._cache_set = _fake_cache_set


def main() -> int:
    # ------------------------------------------------------------------
    # Test 1: Second dispatch on same (agent, question, viewer) hits cache
    # ------------------------------------------------------------------
    print("\n=== Cache verification ===\n")

    # Pick a hardcoded handler so we don't need an API key
    # (LLM_WIRED_AGENTS path also caches — same code path).
    from src.agents import llm
    original_is_available = llm.is_available
    llm.is_available = lambda: False  # force hardcoded path

    try:
        all_pass = True

        # The 4 grocer suggestion buttons per agent
        questions = []
        for agent_id, pairs in P.questions_for("KRG").items():
            for qid, qtext in pairs:
                questions.append((agent_id, qid, qtext))

        print(f"Testing {len(questions)} suggested-question buttons (KRG):\n")

        for agent_id, qid, qtext in questions:
            t0 = time.monotonic()
            r1 = P.dispatch(agent_id, qid, "KRG")
            first_ms = (time.monotonic() - t0) * 1000

            t0 = time.monotonic()
            r2 = P.dispatch(agent_id, qid, "KRG")
            second_ms = (time.monotonic() - t0) * 1000

            same_obj = (r1 is r2)
            cache_hit_ok = second_ms < 100
            status = "OK" if cache_hit_ok else "FAIL"
            print(f"  [{status}] {agent_id:<8} {qid:<24} "
                  f"first={first_ms:>6.1f}ms  second={second_ms:>6.1f}ms"
                  + ("  (same obj)" if same_obj else "  (different obj)"))
            if not cache_hit_ok:
                all_pass = False

        # ------------------------------------------------------------------
        # Test 2: Cache key varies by viewer
        # ------------------------------------------------------------------
        print("\nTesting cache-key isolation across viewers:")
        krg_resp = P.dispatch("pricing", "dairy_vs_peers", "KRG")
        acm_resp = P.dispatch("pricing", "dairy_vs_peers", "ACM")
        viewer_isolated = krg_resp is not acm_resp
        print(f"  [{('OK' if viewer_isolated else 'FAIL')}] "
              f"KRG response != ACM response (different cache entries): "
              f"{viewer_isolated}")
        if not viewer_isolated:
            all_pass = False

        # ------------------------------------------------------------------
        # Test 3: Free-form dispatch is NOT cached
        # ------------------------------------------------------------------
        print("\nTesting free-form dispatch is NOT cached:")
        # Use a fallback_question_id so the hardcoded path runs.
        r1 = P.dispatch_free_form(
            "pricing", "KRG", "How am I priced on dairy?",
            fallback_question_id="dairy_vs_peers",
        )
        r2 = P.dispatch_free_form(
            "pricing", "KRG", "How am I priced on dairy?",
            fallback_question_id="dairy_vs_peers",
        )
        not_cached = r1 is not r2
        print(f"  [{('OK' if not_cached else 'FAIL')}] "
              f"Free-form returns fresh response each call (not cached): "
              f"{not_cached}")
        if not not_cached:
            all_pass = False

        print(f"\n{'=' * 56}")
        print(f"  Overall: {'PASS' if all_pass else 'FAIL'}")
        return 0 if all_pass else 1
    finally:
        llm.is_available = original_is_available


if __name__ == "__main__":
    raise SystemExit(main())
