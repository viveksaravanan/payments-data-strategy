"""Wave 3 §6.5 — agent preview harness (the human-review artifact).

Runs questions through the real agent path against the live
``data/lake/`` + tenant surface, dumps the COMPLETE
``AgentResponse`` (prose + interactive Plotly chart HTML + merged
result + claims with disposition + SQL + routing + telemetry) into
``docs/AGENT_PREVIEW.html``. This is the "are the answers
demo-ready" checkpoint — prose + chart quality is judged here,
not just test pass/fail.

Single-question mode (default):

    uv run python scripts/preview_agent.py \\
        --merchant KRG --question "How does our dairy pricing compare?"

Pill mode:

    uv run python scripts/preview_agent.py --merchant KRG --pill P1

Batch mode — iterates every pill in
``src/dashboard/questions.py`` for the merchant:

    uv run python scripts/preview_agent.py --merchant KRG --batch

Requires ``ANTHROPIC_API_KEY`` to make real LLM calls. Without it,
the script aborts before any work.
"""
from __future__ import annotations

import argparse
import html as html_escape
import json
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import pandas as pd

from src.agents import llm as L
from src.agents.dispatch import dispatch_freeform, dispatch_pill
from src.agents.orchestrator import RoutingDecision
from src.agents.response import AgentResponse
from src.dashboard.questions import (
    questions_for,
    segment_for_merchant,
    QUESTIONS,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "docs" / "AGENT_PREVIEW.html"


# ---------------------------------------------------------------------
# Map qid → agent for the dispatch_pill path. The dashboard registry
# carries (merchant_segment, specialist) keys; the qid prefix encodes
# the agent (P*, D*, A*, T*). Advisor pills don't exist in v3's
# registry — Wave 4 will add them.
# ---------------------------------------------------------------------

QID_AGENT = {
    "P": "pricing",
    "D": "demand",
    "A": "anomaly",
    "T": "trade",
}


def _agent_for_qid(qid: str) -> str:
    prefix = qid[:1]
    if prefix not in QID_AGENT:
        raise ValueError(
            f"Unknown qid prefix {prefix!r} (qid={qid!r}). "
            f"Known: {sorted(QID_AGENT)}."
        )
    return QID_AGENT[prefix]


# ---------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------

def _render_chart_html(resp: AgentResponse) -> str:
    if resp.chart is None:
        return "<em>(no chart)</em>"
    return resp.chart.to_html(
        include_plotlyjs="cdn", full_html=False, default_height=420,
    )


def _render_result_table(df: pd.DataFrame, *, max_rows: int = 12) -> str:
    if df is None or len(df) == 0:
        return "<em>(empty result)</em>"
    truncated = ""
    if len(df) > max_rows:
        truncated = (
            f"<p class=\"hint\"><em>… showing {max_rows} of "
            f"{len(df)} rows.</em></p>"
        )
    return df.head(max_rows).to_html(
        classes="result-table", border=0, index=False,
    ) + truncated


def _render_claims(resp: AgentResponse) -> str:
    if not resp.claims:
        return "<em>(no claims)</em>"
    rows = []
    for c in resp.claims:
        rows.append(
            f"<li><code>{html_escape.escape(c.text_span)}</code> "
            f"= <strong>{c.value}</strong> "
            f"<span class=\"hint\">via "
            f"{type(c.source).__name__}</span></li>"
        )
    return "<ul>" + "".join(rows) + "</ul>"


def _render_sql(resp: AgentResponse) -> str:
    if not resp.sql:
        return "<em>(no SQL)</em>"
    rows = []
    for s in resp.sql:
        rows.append(
            f"<div class=\"sql-block\">"
            f"<span class=\"surface\">{s.surface}</span> "
            f"<span class=\"hint\">({s.row_count} rows)</span>"
            f"<pre>{html_escape.escape(s.query)}</pre>"
            f"</div>"
        )
    return "".join(rows)


def _render_one(
    *,
    label: str,
    question: str,
    routing: RoutingDecision,
    resp: AgentResponse,
) -> str:
    grain_notes_html = (
        "<ul>" + "".join(
            f"<li>{html_escape.escape(g)}</li>"
            for g in resp.grain_notes
        ) + "</ul>"
        if resp.grain_notes else "<em>(none)</em>"
    )
    caveats_html = (
        "<ul>" + "".join(
            f"<li>{html_escape.escape(c)}</li>"
            for c in resp.caveats
        ) + "</ul>"
        if resp.caveats else "<em>(none)</em>"
    )
    tel = resp.telemetry
    telemetry_html = (
        f"model={tel.model} · turns={tel.turns} · "
        f"in={tel.input_tokens} · out={tel.output_tokens} · "
        f"${tel.cost_usd:.4f}"
        if tel else "<em>(none)</em>"
    )
    return f"""
<section class="qa">
  <h2>{html_escape.escape(label)}</h2>
  <p class="question"><strong>Q:</strong> {html_escape.escape(question)}</p>
  <p class="routing"><strong>Routed:</strong> {routing.primary}
     <span class="hint">— {html_escape.escape(routing.rationale)}</span>
  </p>
  <h3>Prose (post-validator)</h3>
  <blockquote class="prose">{html_escape.escape(resp.prose)}</blockquote>
  <h3>Chart</h3>
  <div class="chart">{_render_chart_html(resp)}</div>
  <h3>Merged result</h3>
  {_render_result_table(resp.result)}
  <h3>Claims</h3>
  {_render_claims(resp)}
  <h3>SQL surfaces</h3>
  {_render_sql(resp)}
  <h3>Grain notes (from manifest)</h3>
  {grain_notes_html}
  <h3>Caveats</h3>
  {caveats_html}
  <h3>Telemetry</h3>
  <p class="telemetry">{telemetry_html}</p>
</section>
"""


_PAGE_STYLE = """
body { font-family: -apple-system, BlinkMacSystemFont, system-ui, sans-serif;
       max-width: 1100px; margin: 24px auto; padding: 0 16px;
       color: #1A1F2E; line-height: 1.4; }
h1 { border-bottom: 2px solid #0F4C81; padding-bottom: 4px; }
h2 { color: #0F4C81; margin-top: 32px; }
h3 { color: #4A5161; margin-top: 16px; }
section.qa { border: 1px solid #E5E7EB; padding: 16px 20px;
             margin: 20px 0; border-radius: 6px; background: #FAFBFC; }
blockquote.prose { background: #FFFFFF; border-left: 4px solid #0F4C81;
                   padding: 12px 16px; margin: 8px 0;
                   font-size: 15px; white-space: pre-wrap; }
table.result-table { border-collapse: collapse; margin: 8px 0; font-size: 12px; }
table.result-table th, table.result-table td {
    border: 1px solid #E5E7EB; padding: 4px 8px; text-align: left; }
table.result-table th { background: #F3F4F6; }
.sql-block { background: #FFFFFF; border: 1px solid #E5E7EB;
             padding: 8px 12px; margin: 6px 0; border-radius: 4px; }
.sql-block .surface { font-weight: bold; color: #0F4C81; }
.sql-block pre { margin: 4px 0; white-space: pre-wrap; font-size: 12px;
                 color: #4A5161; }
.hint { color: #9CA3AF; font-size: 12px; }
.question, .routing { margin: 4px 0; }
.telemetry { color: #4A5161; font-family: monospace; font-size: 12px; }
.error { color: #C44536; }
"""


def _wrap_page(body: str, *, merchant: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Wave 3 Agent Preview — {merchant}</title>
  <style>{_PAGE_STYLE}</style>
</head>
<body>
  <h1>Wave 3 Agent Preview — {merchant}</h1>
  <p class="hint">Generated {datetime.now().isoformat(timespec='seconds')}.
     Charts are live Plotly figures; tables and claims are read off the
     merged result; prose is the post-validator output (numbers stripped
     or normalized per §1.4).</p>
  {body}
</body>
</html>
"""


# ---------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------

def _run_freeform(merchant: str, question: str) -> tuple[
    RoutingDecision, AgentResponse,
]:
    return dispatch_freeform(merchant_id=merchant, question=question)


def _run_pill(merchant: str, qid: str) -> tuple[
    RoutingDecision, AgentResponse, str,
]:
    agent_id = _agent_for_qid(qid)
    segment = segment_for_merchant(merchant)
    questions = questions_for(merchant, agent_id)
    match = next((q for q in questions if q["id"] == qid), None)
    if match is None:
        raise ValueError(
            f"Pill {qid!r} not found for segment {segment!r} / "
            f"agent {agent_id!r}. Known pills: "
            f"{[q['id'] for q in questions]}"
        )
    decision, resp = dispatch_pill(
        agent_id=agent_id, merchant_id=merchant,
        question=match["text"], qid=qid,
    )
    return decision, resp, match["text"]


def _iter_all_pills(merchant: str):
    segment = segment_for_merchant(merchant)
    for agent_id, pills in QUESTIONS[segment].items():
        for pill in pills:
            yield pill["id"], pill["text"], agent_id


def _safe_run(label: str, fn) -> str:
    """Wrap a single run; on exception, emit an error section so the
    batch keeps going."""
    try:
        return fn()
    except Exception as exc:                 # noqa: BLE001 — preview robustness
        return (
            f"<section class=\"qa\"><h2>{html_escape.escape(label)}</h2>"
            f"<p class=\"error\">Failed: {html_escape.escape(type(exc).__name__)}: "
            f"{html_escape.escape(str(exc))}</p></section>"
        )


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Wave 3 agent preview harness (writes "
                    "docs/AGENT_PREVIEW.html).",
    )
    parser.add_argument(
        "--merchant", default="KRG",
        help="Viewer merchant code (KRG/ACM/WDX/TBL/TJX).",
    )
    parser.add_argument(
        "--question",
        help="A single free-form question to run through the orchestrator.",
    )
    parser.add_argument(
        "--pill",
        help="A pill qid (e.g. P1) to dispatch directly (skip the router).",
    )
    parser.add_argument(
        "--batch", action="store_true",
        help="Iterate every pill in src/dashboard/questions.py for the "
             "merchant.",
    )
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_OUT,
        help=f"Output HTML path (default {DEFAULT_OUT.relative_to(REPO_ROOT)}).",
    )
    args = parser.parse_args()

    if not L.is_available():
        print(
            "ANTHROPIC_API_KEY not set. The preview harness needs the "
            "real LLM to demonstrate agent quality. Set the key in .env "
            "and re-run.", file=sys.stderr,
        )
        return 2

    sections: list[str] = []
    t0 = time.time()

    if args.batch:
        for qid, text, _agent in _iter_all_pills(args.merchant):
            label = f"{qid} — {text[:80]}"
            print(f"  [{time.time()-t0:6.1f}s] running {qid}…", flush=True)
            def _run(qid=qid, text=text):
                decision, resp, q_text = _run_pill(args.merchant, qid)
                return _render_one(
                    label=label, question=q_text,
                    routing=decision, resp=resp,
                )
            sections.append(_safe_run(label, _run))
    elif args.pill:
        label = f"{args.pill} (pill)"
        def _run():
            decision, resp, q_text = _run_pill(args.merchant, args.pill)
            return _render_one(
                label=label, question=q_text,
                routing=decision, resp=resp,
            )
        sections.append(_safe_run(label, _run))
    elif args.question:
        label = "Free-form"
        def _run():
            decision, resp = _run_freeform(args.merchant, args.question)
            return _render_one(
                label=label, question=args.question,
                routing=decision, resp=resp,
            )
        sections.append(_safe_run(label, _run))
    else:
        print("Specify --question, --pill, or --batch.", file=sys.stderr)
        return 2

    page = _wrap_page("\n".join(sections), merchant=args.merchant)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(page)
    print(f"Wrote {args.out} ({time.time()-t0:.1f}s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
