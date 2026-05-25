"""Phase 5.0 — cassette infrastructure smoke tests.

Three tests covering the contract:
  * Every baseline JSON file under ``tests/cassettes/baseline/`` is
    valid JSON with the expected top-level keys.
  * ``replay_cassette`` round-trips a saved cassette back into a
    response dict.
  * ``compare_cassettes`` produces the expected comparison shape
    for Phase 5.5's regression run.

These tests deliberately do NOT call the LLM; cassette recording is
covered by ``scripts/record_baseline_cassettes.py`` and is run
manually (live API spend).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.cassette_helpers import compare_cassettes, replay_cassette


CASSETTE_REQUIRED_KEYS = {
    "qid",
    "specialist",
    "merchant_id",
    "question",
    "tool_calls",
    "response_dict",
    "recorded_at",
    "phase_baseline",
    "commit_sha",
}

RESPONSE_REQUIRED_KEYS = {"agent", "prose", "caveats", "telemetry"}


def test_all_baseline_cassettes_loadable(baseline_cassettes):
    """Every JSON file in tests/cassettes/baseline/ parses cleanly and
    carries the required top-level keys + a well-formed
    ``response_dict``. Empty baseline set passes (fresh checkout
    before recording)."""
    if not baseline_cassettes:
        pytest.skip("No baseline cassettes recorded yet.")

    for name, cassette in baseline_cassettes.items():
        missing = CASSETTE_REQUIRED_KEYS - cassette.keys()
        assert not missing, (
            f"Cassette {name} is missing required keys: {missing}"
        )
        resp = cassette["response_dict"]
        assert isinstance(resp, dict), (
            f"Cassette {name} response_dict is not a dict"
        )
        resp_missing = RESPONSE_REQUIRED_KEYS - resp.keys()
        assert not resp_missing, (
            f"Cassette {name} response_dict missing keys: {resp_missing}"
        )
        # tool_calls is a list (possibly empty) of dicts each carrying
        # tool + query + row_count.
        assert isinstance(cassette["tool_calls"], list)
        for tc in cassette["tool_calls"]:
            assert {"tool", "query", "row_count"}.issubset(tc.keys()), (
                f"Cassette {name} tool_call entry missing fields: {tc}"
            )


def test_replay_returns_response_dict(tmp_path):
    """``replay_cassette`` deserializes a saved cassette back into the
    response_dict suitable for asserting against."""
    cassette = {
        "qid":             "P1",
        "specialist":      "pricing",
        "merchant_id":     "KRG",
        "question":        "dummy",
        "tool_calls":      [],
        "response_dict": {
            "agent":     "Pricing & Benchmarking Agent",
            "prose":     "Your dairy prices sit 2.2% above peer_a.",
            "caveats":   ["Based on the 90-day window."],
            "telemetry": {
                "turns": 3, "input_tokens": 12000,
                "output_tokens": 800, "cost_usd": 0.005,
                "converged": True,
            },
        },
        "recorded_at":     "2026-05-24T15:00:00Z",
        "phase_baseline":  "4.6",
        "commit_sha":      "abcdef0",
    }
    path = tmp_path / "pricing_P1_KRG.json"
    path.write_text(json.dumps(cassette))

    resp = replay_cassette(path)
    assert resp["agent"]  == "Pricing & Benchmarking Agent"
    assert resp["prose"].startswith("Your dairy")
    assert resp["caveats"] == ["Based on the 90-day window."]
    assert resp["telemetry"]["turns"] == 3


def test_compare_generates_comparison_dict(tmp_path):
    """``compare_cassettes`` pairs a baseline cassette with a
    phase5_response and stamps ``grade=None`` for manual grading."""
    baseline = {
        "qid":             "P1",
        "specialist":      "pricing",
        "merchant_id":     "KRG",
        "question":        "How do my prices compare to peers?",
        "tool_calls":      [],
        "response_dict": {
            "agent":     "Pricing & Benchmarking Agent",
            "prose":     "Baseline prose.",
            "caveats":   ["Caveat A"],
            "telemetry": {},
        },
        "recorded_at":     "2026-05-24T15:00:00Z",
        "phase_baseline":  "4.6",
        "commit_sha":      "abcdef0",
    }
    path = tmp_path / "pricing_P1_KRG.json"
    path.write_text(json.dumps(baseline))

    phase5 = {
        "headline":  "Dairy is 2.2% above peer_a.",
        "evidence":  ["KRG dairy $4.21", "peer_a dairy $4.12"],
        "therefore": "Your dairy strip is the widest cross-peer gap.",
        "caveats":   ["Based on the 90-day window."],
    }
    out = compare_cassettes(path, phase5)

    assert out["qid"]         == "P1"
    assert out["specialist"]  == "pricing"
    assert out["merchant_id"] == "KRG"
    assert out["baseline_response"]["prose"]   == "Baseline prose."
    assert out["baseline_response"]["caveats"] == ["Caveat A"]
    assert out["phase5_response"] == phase5
    assert out["grade"] is None
    assert out["notes"] == ""
