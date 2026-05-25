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
import re
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
    ``phase5_response``, and stamps ``grade=None`` + ``notes=""``
    for the reviewer to fill in.

    The ``phase5_response`` can be either the raw dict from
    ``_run_specialist`` (in which case prose/caveats/telemetry are
    extracted into a compact form) or a pre-shaped dict with just
    those keys. Pre-existing baseline-only tests pass the latter;
    the regression script passes the former.
    """
    raw = json.loads(Path(baseline_path).read_text())
    baseline_resp = raw.get("response_dict", {})
    base_tel     = baseline_resp.get("telemetry") or {}

    # Normalize phase5_response into the comparison shape.
    phase5_compact = {
        "prose":   phase5_response.get("prose", ""),
        "caveats": list(phase5_response.get("caveats") or []),
        "telemetry": phase5_response.get("telemetry") or {},
    }

    return {
        "qid":               raw.get("qid"),
        "specialist":        raw.get("specialist"),
        "merchant_id":       raw.get("merchant_id"),
        "question":          raw.get("question"),
        "baseline_response": {
            "prose":     baseline_resp.get("prose", ""),
            "caveats":   list(baseline_resp.get("caveats") or []),
            "telemetry": dict(base_tel),
        },
        "phase5_response":     phase5_compact,
        "contract_compliance": check_contract_compliance(phase5_compact),
        "grade":               None,
        "notes":               "",
    }


# ---------------------------------------------------------------------------
# Contract-compliance regex checks (Phase 5.5)
#
# These are *mechanical* checks against the response contract from
# V3_AGENTS_DESIGN.md §2 (Headline → Evidence → Therefore → Caveats).
# They catch obvious shape violations cheaply; the user's manual grade
# captures everything regex can't see (quality of evidence, framing
# fit, recommendation strength).
# ---------------------------------------------------------------------------

# Therefore-opener phrases approved by the contract (§2 Therefore).
_APPROVED_THEREFORE_OPENERS = (
    "worth investigating",
    "the dominant lever",
    "largest opportunity sits in",
    "most actionable next look",
    "watch for",
)

# Forbidden verbs in the Therefore section (§2 Therefore).
_FORBIDDEN_THEREFORE_VERBS = (
    "should", "recommend", "consider", "try",
    "implement", "deploy", "roll out",
)

# Throat-clearing openers the Headline must NOT start with (§2 Headline).
_THROAT_CLEARING = (
    "looking at", "here's what", "interesting question",
    "let me", "based on the data i gathered",
    "now i", "now let me", "excellent", "perfect", "great",
)

_HEADLINE_NUMBER_RE   = re.compile(r"\d")
_BULLET_RE            = re.compile(r"^\s*[-*]\s", re.MULTILINE)
_THEREFORE_MARKER_RE  = re.compile(r"\*\*Therefore:?\*\*", re.IGNORECASE)


def _extract_therefore(prose: str) -> str:
    """Return the Therefore paragraph (text after ``**Therefore:**``)
    or an empty string if no marker is present."""
    m = _THEREFORE_MARKER_RE.search(prose)
    if not m:
        return ""
    return prose[m.end():].strip()


def _extract_headline(prose: str) -> str:
    """Return the first non-empty paragraph of the prose."""
    for chunk in prose.split("\n\n"):
        s = chunk.strip()
        if s and not s.startswith(("-", "*")):
            return s
    return ""


def check_contract_compliance(response: dict[str, Any]) -> dict[str, bool]:
    """Run the 7 mechanical contract checks on a response dict.

    Returns a ``{check_name: bool}`` dict. Used by the regression
    script to capture each comparison's structural compliance
    without requiring manual grading."""
    prose   = response.get("prose", "") or ""
    caveats = response.get("caveats")

    headline = _extract_headline(prose)
    therefore_body = _extract_therefore(prose).lower()
    bullet_count = len(_BULLET_RE.findall(prose))
    headline_l = headline.lower().lstrip()

    return {
        "headline_has_number":
            bool(_HEADLINE_NUMBER_RE.search(headline)),
        "evidence_3_to_5_bullets":
            3 <= bullet_count <= 5,
        "therefore_section_present":
            bool(_THEREFORE_MARKER_RE.search(prose)),
        "approved_therefore_opener":
            any(o in therefore_body for o in _APPROVED_THEREFORE_OPENERS),
        "no_forbidden_verbs":
            not any(v in therefore_body for v in _FORBIDDEN_THEREFORE_VERBS),
        "caveats_fence_parsed":
            isinstance(caveats, list),
        "no_throat_clearing":
            not any(headline_l.startswith(s) for s in _THROAT_CLEARING),
    }
