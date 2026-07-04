#!/usr/bin/env python3
"""Live validation battery for the 5 specialist agents against v2 data + lake.

For each question: dispatch the real agent (Haiku), then INDEPENDENTLY re-run the
exact SQL the agent executed (`query_tenant` / `query_lake_sql`), recompute every
declared claim against the freshly-fetched frames, and cross-check against both the
number the model stated and the validator's `true_value`. Adds automated semantic
heuristics the numeric validator can't check (peer-filter present, taxonomy alignment,
empty results, QSR-uses-grocery-category, direction-vs-number). Writes a markdown
report; final human judgment reads it.

Run: `uv run python scripts/validate_agents.py [--out docs/AGENT_VALIDATION.md]`
Requires ANTHROPIC_API_KEY in .env. ~14 live Haiku calls (~$1).
"""
from __future__ import annotations

import argparse
import re
import traceback
from pathlib import Path

import pandas as pd

from src.agents import lake_tools as LT
from src.agents import llm as L
from src.agents.dispatch import dispatch_freeform, dispatch_pill

REPO = Path(__file__).resolve().parents[1]

# Grocery functional category/department literals — used to flag a QSR answer that
# hallucinated a grocery bucket.
GROCERY_TERMS = [
    "Milk", "Dairy & Eggs", "Beef", "Produce", "Fresh Fruit", "Cheese", "Yogurt",
    "Bakery", "Meat & Seafood", "Pasta & Sauce", "Frozen", "Poultry", "Seafood",
]

# label, merchant, agent (None => free-form routed), question, qid, kind
BATTERY = [
    ("Pricing (grocery)", "KRG", "pricing",
     "How do my prices compare to peer grocers across categories?", "P1", "compare"),
    ("Demand (grocery)", "KRG", "demand",
     "Which categories over- or under-perform vs peers given my mix?", "D4", "compare"),
    ("Trade (grocery)", "KRG", "trade",
     "Which of my neighborhoods are over- or under-performing?", "T1", "compare"),
    ("Anomaly (grocery)", "KRG", "anomaly",
     "Which SKUs or categories are spiking or dropping unusually versus peers?", "A3", "compare"),
    ("Advisor payment mix (grocery)", "KRG", None,
     "What's my contactless payment mix versus peers?", None, "compare"),
    ("Own-only top categories (KRG)", "KRG", None,
     "What are my top categories by sales?", None, "own_only"),
    ("Own-only top categories (ACM)", "ACM", None,
     "What are my top categories by sales?", None, "own_only"),
    ("Department-grain (grocery)", "KRG", None,
     "How does my Dairy & Eggs department compare to peers?", None, "compare"),
    ("Cannot-answer cohort (grocery)", "KRG", None,
     "Which of my shoppers also buy at a competitor?", None, "cannot_answer"),
    ("Pricing (QSR)", "TBL", "pricing",
     "How does my pricing compare to peers across my menu?", None, "compare_qsr"),
    ("Demand (QSR)", "TBL", "demand",
     "Which menu categories are growing or slowing versus peers?", None, "compare_qsr"),
    ("Anomaly late-night (QSR)", "TBL", "anomaly",
     "Is my late-night business unusual compared with peers?", None, "compare_qsr"),
    ("Advisor payment mix (QSR)", "TBL", None,
     "What's my payment mix versus peers?", None, "compare_qsr"),
    ("Anomaly Sunday zero (CFA)", "CFA", "anomaly",
     "How does my Sunday traffic compare to peers?", None, "compare_qsr"),
]


# Part C — grain diagnostic probes (subcategory + own product-name). Run on Sonnet.
PROBES = [
    ("A own-only subcategory", "KRG", None,
     "Within my Dairy & Eggs department, which subcategories drive the most sales?",
     None, "own_subcat"),
    ("B peer subcategory compare", "KRG", "pricing",
     "How does my pricing compare to peers within Dairy & Eggs, by subcategory?",
     None, "peer_subcat"),
    ("C own-only product name", "KRG", "demand",
     "Which specific products of mine are underperforming and could be markdown candidates?",
     None, "own_product"),
    ("D peer subcategory (QSR)", "TBL", "pricing",
     "How do my menu prices compare to peers by subcategory?", None, "peer_subcat"),
    ("E peer product name (should decline)", "KRG", "pricing",
     "How does my price on specific milk products compare to competitors' specific products?",
     None, "peer_product_decline"),
]

# Grouping/grain columns, finest → coarsest, for "what grain did the agent use".
_GRAIN_COLS = [
    "product_name", "merchant_subcategory", "functional_subcategory", "subcategory",
    "merchant_category", "functional_category", "category", "department",
    "neighborhood", "payment_type", "entry_mode", "tender",
]


def detect_grain(sql: str) -> list[str]:
    s = sql.lower()
    return [c for c in _GRAIN_COLS if re.search(r"\b" + re.escape(c) + r"\b", s)]


def _rerun_frames(merchant: str, resp) -> dict[str, pd.DataFrame]:
    """Independently re-execute the agent's captured SQL and return fresh
    {'tenant': df, 'lake': df} frames."""
    frames: dict[str, pd.DataFrame] = {}
    for s in resp.sql:
        try:
            if s.surface == "tenant":
                frames["tenant"] = LT.query_tenant(merchant, s.query)["frame"]
            elif s.surface.startswith("lake"):
                frames["lake"] = LT.query_lake_sql(merchant, s.query)["frame"]
        except Exception as exc:  # capture — a re-run failure is itself a finding
            frames.setdefault("_errors", []).append(f"{s.surface}: {exc}")
    return frames


def _recompute_claims(resp, frames: dict) -> list[dict]:
    """For each declared claim, resolve it against the freshly re-run frames and
    compare to the model-stated value + validator true_value."""
    disp_by_span = {d.get("text_span"): d for d in resp.claim_dispositions}
    out = []
    tenant = frames.get("tenant", pd.DataFrame())
    lake = frames.get("lake", pd.DataFrame())
    fr = {k: v for k, v in (("tenant", tenant), ("lake", lake)) if not v.empty}
    for cl in resp.claims:
        d = disp_by_span.get(cl.text_span, {})
        row = {"span": cl.text_span, "stated": cl.value,
               "status": d.get("status"), "true_value": d.get("true_value"),
               "recompute": None, "match": None, "err": None}
        try:
            row["recompute"] = float(cl.source.resolve(pd.DataFrame(), frames=fr))
            ref = d.get("true_value")
            ref = ref if ref is not None else cl.value
            row["match"] = (ref is not None and
                            abs(row["recompute"] - ref) <= 1e-6 + 0.01 * max(abs(row["recompute"]), abs(ref)))
        except Exception as exc:
            row["err"] = f"{type(exc).__name__}: {exc}"
        out.append(row)
    return out


def _semantic_flags(label, kind, resp, frames) -> list[str]:
    flags = []
    tenant_sql = " ".join(s.query for s in resp.sql if s.surface == "tenant")
    lake_sql = " ".join(s.query for s in resp.sql if s.surface.startswith("lake"))
    is_qsr = kind == "compare_qsr"

    # Empty results.
    for s in resp.sql:
        if s.row_count == 0:
            flags.append(f"EMPTY RESULT: {s.surface} query returned 0 rows")
    if "_errors" in frames:
        flags.append("RE-RUN ERROR: " + "; ".join(frames["_errors"]))

    if kind in ("compare", "compare_qsr", "department"):
        # Peer query should scope to same-segment peers.
        if lake_sql and "peer_relationship" not in lake_sql:
            flags.append("PEER SCOPE: lake query has no peer_relationship filter "
                         "(may include cross-segment 'merchant' rows)")
        elif lake_sql and "peer_relationship = 'peer'" not in lake_sql.replace('"', "'"):
            flags.append("PEER SCOPE: lake query filters peer_relationship but not = 'peer'")
        # Taxonomy alignment: comparing to peers → own side must use functional_*.
        if lake_sql and tenant_sql and "merchant_category" in tenant_sql:
            flags.append("TAXONOMY MISALIGN: own (tenant) side uses merchant_category "
                         "while comparing to the functional-keyed lake")
        if lake_sql and tenant_sql and ("functional_" not in tenant_sql
                                        and ("category" in tenant_sql or "department" in tenant_sql)):
            flags.append("TAXONOMY: tenant taxonomy column not explicitly functional_* "
                         "(verify it aligns with the lake's functional key)")
    if kind == "own_only":
        if "functional_" in tenant_sql and "merchant_" not in tenant_sql:
            flags.append("OWN-ONLY TAXONOMY: own-only answer used functional_* — "
                         "expected merchant_* (the merchant's own shelf labels)")

    # QSR should not reference grocery category literals.
    if is_qsr:
        hit = [t for t in GROCERY_TERMS if re.search(rf"'{re.escape(t)}'", tenant_sql + lake_sql)]
        if hit:
            flags.append(f"QSR HALLUCINATION: grocery category literal(s) in SQL: {hit}")

    # Direction-vs-number heuristic on the prose.
    for sent in list(resp.evidence) + ([resp.so_what] if resp.so_what else []):
        nums = [float(x) for x in re.findall(r"\$?(\d+\.\d+)", sent)]
        low = sent.lower()
        if len(nums) >= 2 and (" versus " in low or " vs " in low):
            own, peer = nums[0], nums[1]
            says_below = any(w in low for w in ("below peer", "below the peer", "under peer",
                                                "cheaper than peer", "less than peer", "lower than peer"))
            says_above = any(w in low for w in ("above peer", "above the peer", "over peer",
                                                "higher than peer", "more than peer", "richer than peer"))
            # "peers run cheaper" / "peers are cheaper" means own is ABOVE.
            peers_cheaper = "peers run cheaper" in low or "peer runs cheaper" in low or "peers are cheaper" in low
            if says_below and own > peer:
                flags.append(f"DIRECTION: says 'below' but own {own} > peer {peer} — {sent!r}")
            if says_above and own < peer:
                flags.append(f"DIRECTION: says 'above' but own {own} < peer {peer} — {sent!r}")
            if says_below and peers_cheaper:
                flags.append(f"DIRECTION CONTRADICTION: 'below peers' AND 'peers cheaper' — {sent!r}")

    # Cannot-answer probe: should decline, not fabricate a cohort number.
    if kind == "cannot_answer":
        low = (resp.prose or "").lower()
        declines = any(w in low for w in ("isn't available", "not available", "cannot", "can't",
                                          "no consumer linkage", "no linkage", "unable"))
        if not declines:
            flags.append("CANNOT-ANSWER: response did not clearly decline the cohort question")
        if resp.claims:
            flags.append("CANNOT-ANSWER: response carried claims for an unanswerable question")

    # Anomaly must never claim fraud.
    if "anomaly" in label.lower():
        if re.search(r"fraud|tamper|skim|chargeback|theft", (resp.prose or "").lower()):
            flags.append("FRAUD LANGUAGE in an anomaly answer (forbidden)")
    return flags


def _fmt(v):
    return "—" if v is None else (f"{v:.4f}" if isinstance(v, float) else str(v))


def run_one(label, merchant, agent, question, qid, kind) -> dict:
    if agent is None:
        decision, resp = dispatch_freeform(merchant, question)
        routed = getattr(decision, "primary", "?")
    else:
        decision, resp = dispatch_pill(agent, merchant, question, qid=qid)
        routed = agent + " (pill)"
    frames = _rerun_frames(merchant, resp)
    claims = _recompute_claims(resp, frames)
    flags = _semantic_flags(label, kind, resp, frames)
    grain = {s.surface: detect_grain(s.query) for s in resp.sql}
    return {"label": label, "merchant": merchant, "question": question, "kind": kind,
            "routed": routed, "resp": resp, "claims": claims, "flags": flags, "grain": grain}


def render(results) -> str:
    out = ["# Agent validation — live battery vs v2 data + lake\n",
           f"Model: `{L.MODEL_SPECIALIST}` · {len(results)} questions. Each answer's own "
           "SQL was independently re-run and every surviving claim recomputed.\n"]
    n_pass = n_norm = n_strip = n_flag = 0
    for r in results:
        resp = r["resp"]
        out.append(f"\n---\n\n## {r['label']} — `{r['merchant']}`  ·  routed → {r['routed']}\n")
        out.append(f"**Q:** {r['question']}\n")
        out.append(f"**Headline:** {resp.headline}\n")
        if resp.evidence:
            out.append("**Evidence:**\n" + "\n".join(f"- {e}" for e in resp.evidence) + "\n")
        if resp.so_what:
            out.append(f"**So what:** {resp.so_what}\n")
        # claims table
        out.append("\n| status | span | stated | validator_true | my_recompute | match |")
        out.append("|---|---|---|---|---|---|")
        for c in r["claims"]:
            st = c["status"] or "—"
            n_pass += st == "passed"; n_norm += st == "normalized"
            n_strip += st in ("stripped", "stripped_semantic")
            m = "✅" if c["match"] else ("⚠️" if c["match"] is False else "?")
            if c["err"]:
                m = f"ERR {c['err'][:40]}"
            out.append(f"| {st} | {str(c['span'])[:44]} | {_fmt(c['stated'])} | "
                       f"{_fmt(c['true_value'])} | {_fmt(c['recompute'])} | {m} |")
        # grain used per surface
        if r.get("grain"):
            gr = " · ".join(f"{surf}: {', '.join(cols) or '—'}" for surf, cols in r["grain"].items())
            out.append(f"\n**Grain used:** {gr}")
        # SQL
        for s in resp.sql:
            out.append(f"\n<details><summary>SQL [{s.surface}] rows={s.row_count}</summary>\n\n"
                       f"```sql\n{s.query.strip()}\n```\n</details>")
        # label-review corrections (Part A layer)
        corr = getattr(resp, "corrections", []) or []
        if corr:
            out.append("\n**Label-review corrections:**\n" + "\n".join(
                f"- `{x['check']}`: {x['reason']}" for x in corr))
        # flags
        if r["flags"]:
            n_flag += len(r["flags"])
            out.append("\n**⚠ Semantic flags:**\n" + "\n".join(f"- {f}" for f in r["flags"]))
        else:
            out.append("\n**Semantic flags:** none auto-detected.")
        t = resp.telemetry
        if t:
            out.append(f"\n*telemetry: {t.turns} turns · {t.output_tokens} out tok · ${t.cost_usd:.4f}*")
    out.insert(2, f"\n**Scorecard:** claims passed={n_pass} · normalized={n_norm} · "
                  f"stripped={n_strip} · auto-flags={n_flag}\n")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=REPO / "docs" / "AGENT_VALIDATION.md")
    ap.add_argument("--only", type=str, default=None, help="substring filter on label")
    ap.add_argument("--probes", action="store_true", help="run the Part-C grain probes instead of the battery")
    args = ap.parse_args()
    if not L.is_available():
        raise SystemExit("ANTHROPIC_API_KEY not set (.env).")
    results = []
    for row in (PROBES if args.probes else BATTERY):
        if args.only and args.only.lower() not in row[0].lower():
            continue
        print(f"→ {row[0]} ({row[1]})…", flush=True)
        try:
            results.append(run_one(*row))
        except Exception:
            print(f"  FAILED: {row[0]}\n{traceback.format_exc()}", flush=True)
    args.out.write_text(render(results))
    print(f"\nWrote {args.out} ({len(results)} questions).")


if __name__ == "__main__":
    main()
