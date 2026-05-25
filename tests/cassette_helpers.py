"""Helpers for the Phase 5 cassette infrastructure.

Three thin functions:

  * ``record_cassette`` — runs a specialist dispatch live (real API
    call) and writes a JSON cassette to the baseline directory.
  * ``replay_cassette`` — loads a saved cassette and returns its
    ``response_dict`` field. No LLM call.
  * ``compare_cassettes`` — assembles a side-by-side dict (baseline
    + phase5 response, ``grade=None``) for manual grading.

The functions are deliberately not abstractions over the agent
dispatch path — they just call ``_run_specialist`` directly so the
recording captures real behavior. Phase 5.5 will use these to drive
the regression run; Phase 5.0 only needs the recording + replay
contract in place.

Cassette format (see ``tests/cassettes/README.md`` for the full
schema)::

    {
        "qid":           "P1",
        "specialist":    "pricing",
        "merchant_id":   "KRG",
        "question":      "How do my prices compare ...",
        "tool_calls":    [{"tool": "tenant", "query": ..., "row_count": ...}, ...],
        "response_dict": {"agent": ..., "prose": ..., "caveats": [...], "telemetry": {...}},
        "recorded_at":   "2026-05-24T15:00:00Z",
        "phase_baseline":"4.6",
        "commit_sha":    "c75642f",
    }
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# Schema version stamped on every baseline cassette. Bumping this is a
# breaking change — Phase 5.5's regression comparison reads it.
PHASE_BASELINE = "4.6"

BASELINE_DIR = Path(__file__).parent / "cassettes" / "baseline"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _git_short_sha() -> str:
    """Best-effort short SHA for stamping cassettes. Returns ``""`` if
    git isn't available (e.g., recording outside a checkout)."""
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).parents[1],
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return out.decode().strip()
    except Exception:  # noqa: BLE001
        return ""


def _strip_unserializable(response_dict: dict[str, Any]) -> dict[str, Any]:
    """Strip ``table`` (DataFrame) and ``chart`` (plotly Figure) fields
    from the response. Cassettes only persist the JSON-clean portion —
    prose, caveats, agent label, telemetry. The full table/chart can
    be regenerated from the SQL in ``tool_calls`` if needed later."""
    keep = {"agent", "prose", "caveats", "telemetry"}
    return {k: v for k, v in response_dict.items() if k in keep}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def record_cassette(
    specialist: str,
    qid: str,
    merchant_id: str,
    *,
    output_dir: Path | None = None,
) -> Path:
    """Run a specialist dispatch (live API call) and write a baseline
    cassette to ``output_dir/{specialist}_{qid}_{merchant}.json``.
    Returns the path written.

    Bypasses ``A.dispatch``'s Streamlit-session cache by calling
    ``_run_specialist`` directly. If the response has an ``error``
    flag, raises ``RuntimeError`` — error responses aren't worth
    cassette-baselining.
    """
    if output_dir is None:
        output_dir = BASELINE_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    from src.dashboard.agents import _question_text_for, _run_specialist

    question = _question_text_for(specialist, qid, merchant_id)
    response_dict = _run_specialist(specialist, qid, merchant_id)

    if response_dict.get("error"):
        raise RuntimeError(
            f"Specialist {specialist} returned an error for "
            f"{qid}/{merchant_id}; cassette not written."
        )

    cassette = {
        "qid":             qid,
        "specialist":      specialist,
        "merchant_id":     merchant_id,
        "question":        question,
        "tool_calls":      list(response_dict.get("sql") or []),
        "response_dict":   _strip_unserializable(response_dict),
        "recorded_at":     datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "phase_baseline":  PHASE_BASELINE,
        "commit_sha":      _git_short_sha(),
    }

    path = output_dir / f"{specialist}_{qid}_{merchant_id}.json"
    path.write_text(json.dumps(cassette, indent=2, ensure_ascii=False) + "\n")
    return path


def replay_cassette(cassette_path: Path) -> dict[str, Any]:
    """Load a cassette and return its ``response_dict``. Does NOT run
    the LLM; just deserializes the saved payload. Suitable for
    asserting against in tests."""
    raw = json.loads(Path(cassette_path).read_text())
    response = raw.get("response_dict")
    if not isinstance(response, dict):
        raise ValueError(
            f"Cassette {cassette_path} has no response_dict field"
        )
    return response


def compare_cassettes(
    baseline_path: Path,
    phase5_response: dict[str, Any],
) -> dict[str, Any]:
    """Build a comparison dict for Phase 5.5 manual grading.

    Reads the baseline cassette, pulls its ``prose`` + ``caveats``
    into ``baseline_response``, pairs them with the supplied
    ``phase5_response`` (which should already be in the new
    tool-output contract shape — headline/evidence/therefore/caveats),
    and stamps ``grade=None`` + ``notes=""`` for the reviewer to fill
    in.
    """
    raw = json.loads(Path(baseline_path).read_text())
    baseline_resp = raw.get("response_dict", {})
    return {
        "qid":               raw.get("qid"),
        "specialist":        raw.get("specialist"),
        "merchant_id":       raw.get("merchant_id"),
        "question":          raw.get("question"),
        "baseline_response": {
            "prose":   baseline_resp.get("prose", ""),
            "caveats": list(baseline_resp.get("caveats") or []),
        },
        "phase5_response":   dict(phase5_response),
        "grade":             None,
        "notes":             "",
    }
