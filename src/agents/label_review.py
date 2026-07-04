"""Post-validation label-review layer (§ label checks).

The grounding wall (`claims.py`) verifies that every number traces to a queried
cell. This layer runs AFTER it and verifies the LABELS around those numbers —
the things the value-check can't see:

1. `direction`  — a directional word (above/below/…) must match sign(own − peer).
2. `share`      — a share fraction printed with a % ("0.5008%") is 50.1%.
3. `round`      — merchant prose shows spoken-style numbers, not 4-decimal ASPs.
4. `register`   — strip model meta-reasoning that leaked into evidence.
5. `nonanswer`  — a "not available" fallback with data in hand is not allowed
                  (handled in specialist.py at the fallback seam, using
                  `nonanswer_reason` here).
Plus a defensive `dayofweek` SQL lint (DuckDB Sunday=0; closed-day sanity).

Design: repair-to-correct where unambiguous, strip only when the correct claim
can't be reconstructed. It NEVER re-queries and NEVER introduces a new number —
share/round only re-render a value already bound to a passing claim; direction
only swaps a word. Product-name / label text with embedded numbers ("2%
Reduced-Fat Milk") is left untouched — the layer acts on numerics that are the
subject of a metric comparison, not digits inside a name.
"""
from __future__ import annotations

import re
from typing import Any

import pandas as pd

# ----- directional vocabulary ------------------------------------------------

_BELOW = {"below", "lower", "under", "cheaper", "undercut", "undercuts",
          "behind", "trails", "trailing", "lags", "lagging", "underprices",
          "under-performing", "underperforming", "underperforms"}
_ABOVE = {"above", "higher", "over", "pricier", "exceeds", "exceed", "ahead",
          "leads", "leading", "premium", "richer", "outprices", "outsells",
          "outsell", "over-performing", "overperforming", "overperforms"}
# Clean antonym swaps (only these words get repaired; others are left as-is).
_SWAP = {
    "below": "above", "above": "below", "lower": "higher", "higher": "lower",
    "under": "over", "over": "under", "cheaper": "pricier", "pricier": "cheaper",
    "behind": "ahead", "ahead": "behind", "trails": "leads", "leads": "trails",
    "lags": "leads", "trailing": "leading", "leading": "trailing",
    "under-performing": "over-performing", "over-performing": "under-performing",
    "underperforming": "overperforming", "overperforming": "underperforming",
}
# Softening words that must go when the gap is actually large (>10%).
_QUALIFIERS = ["about the same", "nearly the same", "roughly the same",
               "nearly matched", "nearly matches", "slightly", "roughly",
               "marginally", "nearly", "a touch", "about even"]
_MAG_THRESHOLD = 0.10
_PEER_TOK = re.compile(r"peer|competitor|rival|market", re.I)
_OWN_TOK = re.compile(r"\byour\b|\bown\b|\byou\b", re.I)

_COMPARATOR = re.compile(r"\b(?:vs\.?|versus|against|compared\s+to|than)\b", re.I)
# A currency/decimal number token (captures the numeric text for float parsing).
_NUM = re.compile(r"-?\$?\s*(\d[\d,]*(?:\.\d+)?)")
# Currency with 3+ decimals → round for display.
_LONG_CURRENCY = re.compile(r"\$\s*(-?)(\d[\d,]*)\.(\d{3,})")


def _to_float(tok: str) -> float:
    return float(tok.replace(",", "").replace("$", "").replace(" ", ""))


def _sentences(text: str) -> list[str]:
    # Keep it simple: split on sentence enders, preserve content.
    parts = re.split(r"(?<=[.!;])\s+", text.strip())
    return [p for p in parts if p]


# ----- 1. direction ----------------------------------------------------------

def _key_maps(claims: list) -> tuple[dict, dict]:
    """Build {dimension-value → own value} and {… → peer value} from single-cell,
    frame-tagged CellLookup claims (own = frame 'tenant', peer = frame 'lake').
    Uses the already-validated `claim.value`, so this needs no frames."""
    own, peer = {}, {}
    for c in claims:
        src = getattr(c, "source", None)
        rf = getattr(src, "row_filter", None)
        fr = getattr(src, "frame", None)
        v = getattr(c, "value", None)
        if not isinstance(rf, dict) or len(rf) != 1 or not isinstance(v, (int, float)):
            continue
        key = str(list(rf.values())[0]).lower()
        if fr == "tenant":
            own[key] = float(v)
        elif fr == "lake":
            peer[key] = float(v)
    return own, peer


def _num_positions(sent: str) -> list[tuple[float, int, int]]:
    out = []
    for m in _NUM.finditer(sent):
        try:
            out.append((_to_float(m.group(1)), m.start(), m.end()))
        except ValueError:
            pass
    return out


def _bind_by_subject(sent: str, positions: list) -> tuple | None:
    """Assign own/peer to two numbers by the subject word nearest each figure
    (`your`/`own`/`you` → own; `peer`/`competitor` → peer). Returns (own, peer)
    only when exactly one of each is identified — else None (caller falls back)."""
    own_vals, peer_vals = [], []
    for val, s, e in positions:
        ctx = sent[max(0, s - 28):e + 12]
        is_peer = bool(_PEER_TOK.search(ctx))
        is_own = bool(_OWN_TOK.search(ctx))
        if is_peer and not is_own:
            peer_vals.append(val)
        elif is_own and not is_peer:
            own_vals.append(val)
    if len(own_vals) == 1 and len(peer_vals) == 1:
        return own_vals[0], peer_vals[0]
    return None


def _subject_of(sent: str, s: int, e: int) -> str:
    """Whose direction is the word at [s,e) describing — 'own' or 'peer'?
    'below peers' → own is below (subject own); 'peer … below your own' →
    subject peer. Look right first (a peer object → own subject), then left."""
    if _PEER_TOK.search(sent[e:e + 16]):
        return "own"
    if _PEER_TOK.search(sent[max(0, s - 32):s]):
        return "peer"
    return "own"


def _num_before(sent: str, pos: int, window: int = 30) -> float | None:
    last = None
    for m in _NUM.finditer(sent[:pos]):
        if m.end() >= pos - window:
            try:
                last = _to_float(m.group(1))
            except ValueError:
                pass
    return last


def _num_after(sent: str, pos: int, window: int = 30) -> float | None:
    m = _NUM.search(sent[pos:pos + window])
    if m:
        try:
            return _to_float(m.group(1))
        except ValueError:
            return None
    return None


def _apply_swaps(sent: str, own: float, peer: float, corr: list[dict], why: str) -> str:
    """Flip each directional word whose polarity contradicts the numbers. Three
    ways to pick the two compared values, most reliable first:
    (1) ARITHMETIC — the word sits between two numbers ('247K below 211K'): the
        word asserts left<rel>right; check directly (no own/peer labels needed).
    (2) 'below/above peers' — the word is followed by a peer noun → own vs peer.
    (3) subject heuristic — otherwise infer subject (own/peer)."""
    fixed = sent
    for word in _BELOW | _ABOVE:
        if word not in _SWAP:
            continue
        m = re.search(rf"(?<![\w-]){re.escape(word)}(?![\w-])", fixed, re.I)
        if not m:
            continue
        left, right = _num_before(fixed, m.start()), _num_after(fixed, m.end())
        if left is not None and right is not None:
            hi, lo = left, right                     # (1) arithmetic
        elif _PEER_TOK.search(fixed[m.end():m.end() + 16]):
            hi, lo = own, peer                       # (2) "below peers"
        else:
            hi, lo = (own, peer) if _subject_of(fixed, m.start(), m.end()) == "own" else (peer, own)
        wrong = (word in _BELOW and hi > lo) or (word in _ABOVE and hi < lo)
        if wrong:
            repl = _SWAP[word]
            new = fixed[:m.start()] + repl + fixed[m.end():]
            corr.append({"check": "direction", "before": sent, "after": new,
                         "reason": f"'{word}' contradicts {why} ({own} vs {peer}) → '{repl}'"})
            fixed = new
    return fixed


def _drop_qualifier(sent: str, own: float, peer: float, corr: list[dict]) -> str:
    """Remove a softening word (slightly/roughly/about the same) when the gap is
    actually large (>10%)."""
    if not peer or abs(own - peer) / abs(peer) <= _MAG_THRESHOLD:
        return sent
    fixed = sent
    for q in _QUALIFIERS:
        m = re.search(rf"\b{re.escape(q)}\b\s*", fixed, re.I)
        if m:
            new = re.sub(r"\s{2,}", " ", fixed[:m.start()] + fixed[m.end():]).strip()
            corr.append({"check": "magnitude", "before": sent, "after": new,
                         "reason": f"'{q}' but gap {abs(own-peer)/abs(peer):.0%} > 10%"})
            fixed = new
    return fixed


def _direction_fix(text: str, claims: list, corr: list[dict]) -> str:
    """Repair directional words + stale magnitude qualifiers in own-vs-peer
    sentences. Binds own/peer by subject proximity (position-independent — handles
    'peer $247K below your own $211K'); falls back to claim-indexed values when a
    sentence names categories with both an own and a peer claim (handles
    qualitative headlines like 'below peers in Personal Care, Salty Snacks')."""
    own_by, peer_by = _key_maps(claims)
    shared = set(own_by) & set(peer_by)
    out = []
    for sent in _sentences(text):
        low = sent.lower()
        has_dir = any(re.search(rf"(?<![\w-]){re.escape(w)}(?![\w-])", low) for w in _SWAP)
        has_qual = any(re.search(rf"\b{re.escape(q)}\b", low) for q in _QUALIFIERS)
        has_peer = bool(_PEER_TOK.search(low))
        if not ((has_dir or has_qual) and has_peer):
            out.append(sent)
            continue
        own = peer = None
        positions = _num_positions(sent)
        if len(positions) >= 2:
            bound = _bind_by_subject(sent, positions)
            if bound:
                own, peer = bound
            elif len(positions) == 2:
                own, peer = positions[0][0], positions[1][0]  # fallback own=first
        if own is not None:
            fixed = _apply_swaps(sent, own, peer, corr, "own vs peer")
            out.append(_drop_qualifier(fixed, own, peer, corr))
            continue
        # Claim-indexed: use categories named in the sentence whose own/peer
        # signs agree (handles a multi-category qualitative headline).
        hits = [k for k in shared if re.search(rf"\b{re.escape(k)}\b", low) and own_by[k] != peer_by[k]]
        signs = {1 if own_by[k] > peer_by[k] else -1 for k in hits}
        if len(signs) == 1:
            s = signs.pop()
            rep_own, rep_peer = (2.0, 1.0) if s == 1 else (1.0, 2.0)
            out.append(_apply_swaps(sent, rep_own, rep_peer, corr, f"{len(hits)} categor(y/ies)"))
            continue
        out.append(sent)
    return " ".join(out)


# ----- 2 + 3. share format + display rounding --------------------------------

def _share_and_round(text: str, claims: list, corr: list[dict]) -> str:
    """(2) A share fraction printed with a % ('0.5008%') → value×100 ('50.1%'),
    bound to a claim whose value ∈ (0,1]. (3) Round display currency with 3+
    decimals to 2 dp. Never touches a bare number that is not `$`-tagged or not
    the fraction of a share claim (so '2%' inside a product name is left alone)."""
    new = text

    # (2) share fraction with % — only when it equals a claim value in (0,1].
    share_vals = sorted({round(float(c.value), 6) for c in claims
                         if isinstance(getattr(c, "value", None), (int, float))
                         and 0 < float(c.value) <= 1}, reverse=True)
    for v in share_vals:
        # match the fraction rendered with a %, e.g. 0.5008% or .5008 %
        pat = re.compile(rf"(?<!\d)0*\.{str(v).split('.')[1]}\s*%") if "." in str(v) else None
        if pat is None:
            continue
        def _rep(m, v=v):
            after = f"{v*100:.1f}%"
            corr.append({"check": "share", "before": m.group(0), "after": after,
                         "reason": f"fraction {v} printed with % → {after}"})
            return after
        new = pat.sub(_rep, new)

    # (3) round long-decimal currency for display.
    def _round_cur(m):
        sign, whole, dec = m.group(1), m.group(2), m.group(3)
        val = float(f"{whole}.{dec}")
        after = f"${sign}{val:.2f}"
        corr.append({"check": "round", "before": m.group(0), "after": after,
                     "reason": "trim to 2 decimals for display"})
        return after
    new = _LONG_CURRENCY.sub(_round_cur, new)

    # tidy "$-0.40" → "-$0.40"; and drop the minus when a direction word carries it.
    new2 = re.sub(r"\$-(\d)", r"-$\1", new)
    if new2 != new:
        corr.append({"check": "round", "before": new, "after": new2,
                     "reason": "normalize negative currency sign"})
        new = new2
    return new


# ----- 4. register / leaked reasoning ----------------------------------------

_LEAK = re.compile(
    r"(actually,?\s+re-?examin|let me (?:re)?(?:calculate|compute|check|pull|look)|"
    r"i can calculate|i can compute|not shown in (?:the )?truncated|"
    r"re-?examining|on second thought|wait,|hmm,|as (?:computed|shown) above,\s*i|"
    r"i'?ll (?:pull|fetch|calculate|compute|look)|pulling the |"
    r"now to (?:find|pull|check|look|calculate)|to find the (?:biggest|top|largest)|"
    r"let me now|going to (?:pull|calculate|check))",
    re.I,
)


# ----- 5. revenue → sales relabel --------------------------------------------

# Whole-word "revenue"/"revenues" only. `\b` won't match inside an identifier
# like `own_revenue` (the `_` is a word char), so SQL aliases in stray prose are
# untouched — this is prose-only.
_REVENUE_RE = re.compile(r"\brevenues?\b", re.I)


def _revenue_to_sales(text: str, corr: list[dict]) -> str:
    def _rep(m):
        return "Sales" if m.group(0)[:1].isupper() else "sales"
    new = _REVENUE_RE.sub(_rep, text)
    if new != text:
        corr.append({"check": "relabel", "before": text, "after": new,
                     "reason": "'revenue' → 'sales' (we see the transaction total, not cost)"})
    return new


def _register_fix(text: str, corr: list[dict]) -> str:
    """Excise sentences that leak the model thinking aloud. Evidence bullets are
    finished statements, never reasoning."""
    kept = []
    for sent in _sentences(text):
        if _LEAK.search(sent):
            corr.append({"check": "register", "before": sent, "after": "",
                         "reason": "leaked model reasoning removed"})
            continue
        kept.append(sent)
    return " ".join(kept).strip()


# ----- public prose entry ----------------------------------------------------

# ----- 6. headline hygiene ---------------------------------------------------

# A bare comma-list of Capitalized items ("NoDa, Dilworth, Center City.") — no verb.
_BARE_LIST_RE = re.compile(
    r"^[A-Z][\w&''.-]*(?:\s+[A-Z][\w&''.-]*)*"
    r"(?:,\s+[A-Z][\w&''.-]*(?:\s+[A-Z][\w&''.-]*)*)+\.?$")


def _is_bad_headline(h: str) -> bool:
    """A headline must be ONE grammatical sentence. Bad = a bare list of proper
    nouns, or an internal sentence break (a mid-headline '. ', the T2/D3 fragment
    case)."""
    h = (h or "").strip()
    if not h:
        return True
    if _BARE_LIST_RE.fullmatch(h):
        return True
    inner = h[:-1] if h[-1:] in ".!?" else h
    return bool(re.search(r"[.!?]\s+\S", inner))  # a second sentence begins


def review_prose(headline: str, evidence: list[str], so_what: str | None,
                 claims: list) -> tuple[str, list[str], str | None, list[dict]]:
    """Apply the prose checks to each field. Returns revised fields + corrections."""
    from src.agents.fallbacks import BUSINESS_FALLBACK
    corr: list[dict] = []

    def _one(s: str | None) -> str | None:
        if not s:
            return s
        s = _register_fix(s, corr)
        s = _direction_fix(s, claims, corr)
        s = _share_and_round(s, claims, corr)
        s = _revenue_to_sales(s, corr)
        return s.strip() or None

    hl = _one(headline)
    ev = [e for e in (_one(x) for x in evidence) if e]
    sw = _one(so_what)
    # If a check (e.g. register) emptied the headline, promote the first
    # surviving evidence point — never restore the excised text.
    if not hl and ev:
        hl, ev = ev[0], ev[1:]
    # Headline hygiene: one grammatical sentence. If the headline is a bare list
    # or a fragment, promote the first clean evidence bullet; else fall back.
    if hl and _is_bad_headline(hl):
        promoted = next((e for e in ev if not _is_bad_headline(e)), None)
        after = promoted if promoted else BUSINESS_FALLBACK
        corr.append({"check": "headline", "before": hl, "after": after,
                     "reason": "malformed headline (bare list / fragment) → "
                               + ("promoted clean evidence" if promoted else "fallback")})
        if promoted:
            ev = [e for e in ev if e != promoted]
        else:
            ev = []
        hl = after
    return (hl or headline), ev, sw, corr


# ----- dayofweek / closed-day lint (SQL surface) -----------------------------

# Merchants closed on a given DuckDB dayofweek() int (Sunday=0 … Saturday=6).
CLOSED_DAY = {"CFA": 0}
_DOW = re.compile(r"dayofweek\s*\([^)]*\)\s*=\s*(\d)", re.I)


def dayofweek_lint(sql_log: list[dict], viewer: str) -> list[str]:
    """Return caveat strings for suspicious day-of-week filters. DuckDB uses
    Sunday=0; a common bug is `=1` intending Sunday (that's Monday). For a
    merchant known to be closed on a day, filtering a DIFFERENT day but talking
    about the closed one is the CFA class."""
    caveats = []
    closed = CLOSED_DAY.get(viewer)
    for s in sql_log:
        for d in _DOW.findall(s.get("query", "")):
            di = int(d)
            if closed is not None and di != closed and di in (1,):
                caveats.append(
                    "Day-of-week note: DuckDB dayofweek() is Sunday=0; a `=1` "
                    "filter is Monday, not Sunday — verify the day constant."
                )
    return list(dict.fromkeys(caveats))


# ----- 5. non-answer-with-data reason ----------------------------------------

def nonanswer_reason(tenant_frame: pd.DataFrame | None,
                     lake_frame: pd.DataFrame | None) -> str | None:
    """If a fallback is about to fire but both frames have data, return a
    SPECIFIC honest message naming what was fetched (never the generic punt).
    Returns None when there isn't enough data to justify overriding the fallback."""
    t = 0 if tenant_frame is None else len(tenant_frame)
    l = 0 if lake_frame is None else len(lake_frame)
    if t > 0 and l > 0:
        return (f"I pulled your own figures ({t} rows) and the peer benchmark "
                f"({l} rows) but couldn't assemble a grounded comparison this "
                f"run. The underlying data is available — try narrowing the "
                f"question to one category or neighborhood.")
    return None
