"""Wave 3 §1.4 claims validator (D25.4).

The defensibility mechanism. Every material number in agent prose
must trace to a cell in the result or to a declared arithmetic over
result cells. Strict guarantee, graceful handling.

Two-pass validation:

* **Pass A — declared claims.** Each `Claim` declares the prose
  span carrying a number plus a source (`CellLookup` or
  `Derivation`). The validator recomputes the source from the
  result and compares to the declared value within tolerance.
* **Pass B — undeclared-number scan (SPEC §1.4).** Closes the
  "just don't declare it" bypass: parse prose for **metric-shaped**
  numeric tokens (percentages, currency, multipliers, decimals,
  adjacent-modifier integers); each must be covered by a passing
  Pass-A claim. Uncovered metric numerics fail. **Structural
  integers** (years, entity counts, ordinals) are exempt — the
  scanner distinguishes them at the token level so the guarantee
  does NOT depend on the model declaring counts.

Three tiers:

1. **Traces cleanly** (recomputes within tolerance) → pass.
2. **Within tolerance band** → pass, normalize prose to true cell
   value.
3. **Doesn't trace** (or uncovered metric numeric) → strip the
   whole containing clause cleanly. No dangling fragments. The
   whole response is NOT hard-rejected — only the bad clause is
   removed.

Closed derivation grammar: `difference`, `ratio`, `pct_change`,
`aggregate` (sum/mean). No arbitrary model math.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Literal

import pandas as pd


CLAIM_TOLERANCE = 0.01   # ~1% relative; configurable per-call


# ---------------------------------------------------------------------
# Claim sources — closed union
# ---------------------------------------------------------------------

@dataclass
class CellLookup:
    """A single-cell lookup: filter result rows, then take ``column``
    from the (assumed unique) match."""
    row_filter: dict[str, Any]
    column: str

    def resolve(self, result: pd.DataFrame) -> float:
        df = result
        for k, v in self.row_filter.items():
            df = df[df[k] == v]
        if len(df) == 0:
            raise LookupError(
                f"CellLookup row_filter={self.row_filter} matched 0 rows."
            )
        if len(df) > 1:
            raise LookupError(
                f"CellLookup row_filter={self.row_filter} matched "
                f"{len(df)} rows; expected exactly 1. Use Derivation "
                f"with op='aggregate' if you mean a sum/mean."
            )
        return float(df.iloc[0][self.column])


DerivationOp = Literal["difference", "ratio", "pct_change", "aggregate"]
AggregateFunc = Literal["sum", "mean"]


@dataclass
class Derivation:
    """A small arithmetic over cells of the result. ``op`` is one of
    the four closed-grammar operations; ``operands`` are CellLookups
    that resolve to the operation's inputs."""
    op: DerivationOp
    operands: list[CellLookup]
    agg: AggregateFunc | None = None

    def resolve(self, result: pd.DataFrame) -> float:
        if self.op == "difference":
            if len(self.operands) != 2:
                raise ValueError(
                    f"difference needs exactly 2 operands; got {len(self.operands)}"
                )
            a, b = (op.resolve(result) for op in self.operands)
            return a - b
        if self.op == "ratio":
            if len(self.operands) != 2:
                raise ValueError(
                    f"ratio needs exactly 2 operands; got {len(self.operands)}"
                )
            a, b = (op.resolve(result) for op in self.operands)
            if b == 0:
                raise ZeroDivisionError("ratio: denominator is 0")
            return a / b
        if self.op == "pct_change":
            if len(self.operands) != 2:
                raise ValueError(
                    f"pct_change needs exactly 2 operands; got {len(self.operands)}"
                )
            a, b = (op.resolve(result) for op in self.operands)
            if b == 0:
                raise ZeroDivisionError("pct_change: prior value is 0")
            return (a - b) / b
        if self.op == "aggregate":
            if self.agg not in ("sum", "mean"):
                raise ValueError(
                    f"aggregate needs agg ∈ {{'sum', 'mean'}}; got {self.agg!r}"
                )
            values = [op.resolve(result) for op in self.operands]
            if self.agg == "sum":
                return float(sum(values))
            return float(sum(values) / len(values))
        raise ValueError(f"Unknown Derivation.op={self.op!r}")


Source = CellLookup | Derivation


@dataclass
class Claim:
    """One numeric assertion in prose tied to a result-backed source.

    ``text_span`` is the substring of prose carrying the number. It
    is used both to locate the claim for stripping and to determine
    which prose clause to excise on tier-3 failure.
    """
    text_span: str
    value: float
    source: Source


# ---------------------------------------------------------------------
# Numeric scanner — metric vs structural distinction
# ---------------------------------------------------------------------

@dataclass
class NumericToken:
    """A numeric token found in prose. ``kind`` is the load-bearing
    field: only ``metric`` tokens must trace to a claim (Pass B)."""
    value: float
    span: tuple[int, int]    # (start, end) char offsets in prose
    text: str                # exact substring
    kind: Literal["metric", "structural"]
    modifier_word: str | None = None


# Metric modifier words — when one of these appears within ±2 word
# positions of a numeric (NOT just within ±20 chars), treat the
# numeric as metric even without a sigil.
_METRIC_MODIFIERS = frozenset({
    "index", "indices", "share", "shares", "rate", "rates",
    "pct", "percent", "percentage", "percentages",
    "points", "basis",
    "ratio", "ratios", "multiplier", "multipliers",
    "premium", "discount", "uplift", "lift",
    "delta", "deltas", "change", "growth",
})


# Structural-context nouns — when one of these immediately follows a
# numeric (the "5 merchants" / "12 stores" / "8 weeks" pattern), the
# numeric is structural regardless of any far-away modifier word.
_STRUCTURAL_FOLLOWERS = frozenset({
    "merchant", "merchants", "store", "stores",
    "zone", "zones", "region", "regions",
    "week", "weeks", "month", "months", "day", "days",
    "year", "years", "quarter", "quarters",
    "category", "categories", "subcategory", "subcategories",
    "customer", "customers", "shopper", "shoppers",
    "transaction", "transactions", "txn", "txns",
    "basket", "baskets", "order", "orders",
    "brand", "brands", "banner", "banners",
    "product", "products", "sku", "skus",
    "site", "sites", "location", "locations",
    "period", "periods", "row", "rows",
})


# Structural-context prefixes — "Zone 5", "Q3", "Region 2".
_STRUCTURAL_PREFIXES = frozenset({
    "zone", "z", "region", "q", "quarter", "period", "phase",
    "step", "stage", "tier", "wave",
})


# Regex for a numeric literal — handles integers, decimals, comma
# thousands separators, optional approx markers and unit suffixes.
_NUMERIC_RE = re.compile(
    r"""
    (?<![A-Za-z])                       # not preceded by a letter (avoid 'h264')
    (?:≈|~|about\s+|roughly\s+)?        # optional approx marker (consumed but ignored)
    \$?                                  # optional currency sigil
    (-?\d{1,3}(?:,\d{3})+|-?\d+(?:\.\d+)?)  # the number itself
    (?:\s*%|\s*x|\s*×|\s*bps)?           # optional unit suffix
    """,
    re.VERBOSE | re.IGNORECASE,
)


def _classify_token(
    text: str, prose: str, span: tuple[int, int],
) -> Literal["metric", "structural"]:
    """Decide whether a numeric token is metric (must-trace) or
    structural (exempt).

    Precedence:
    1. Sigil (%, $, x, ×, bps) → metric.
    2. Decimal point → metric (indices, ratios, prices).
    3. Approx marker (≈, ~) immediately preceding → metric.
    4. Followed by a structural noun within ±2 word positions
       (``5 merchants``, ``12 weeks``) → structural.
    5. Preceded by a structural prefix within ±2 word positions
       (``Zone 5``, ``Q3``) → structural.
    6. Adjacent to a metric modifier within ±2 word positions
       (``index 5``, ``5 percentage``) → metric.
    7. Default → structural (bare integers are counts).

    Steps 4-6 use word-level proximity, NOT char-level, so a distant
    modifier ("price index in Zone 5") doesn't promote "5" to metric.
    """
    t = text.strip()
    lower = t.lower()
    # 1. Sigils + suffixes.
    if "%" in t or "$" in t:
        return "metric"
    if lower.endswith(("x", "×", "bps")):
        return "metric"
    # 2. Decimal.
    if "." in t:
        return "metric"
    # 3. Approx marker immediately before.
    start, _ = span
    pre = prose[max(0, start - 12):start]
    if re.search(r"(?:≈|~|about\s+|roughly\s+)\s*$", pre, re.IGNORECASE):
        return "metric"

    # Word-level proximity: take 2 words before and 2 words after.
    left_window = re.findall(r"\b\w+\b", prose[max(0, start - 60):start])[-2:]
    right_window = re.findall(r"\b\w+\b", prose[span[1]:span[1] + 60])[:2]
    left_lower = [w.lower() for w in left_window]
    right_lower = [w.lower() for w in right_window]

    # 4. Followed by a structural noun ("5 merchants", "12 weeks").
    if right_lower and right_lower[0] in _STRUCTURAL_FOLLOWERS:
        return "structural"

    # 5. Preceded by a structural prefix ("Zone 5", "Q3"). For single-
    # letter prefixes like "Q" / "Z", require they appear as the
    # immediate predecessor.
    if left_lower and left_lower[-1] in _STRUCTURAL_PREFIXES:
        return "structural"

    # 6. Adjacent metric modifier within ±2 words.
    for w in (left_lower + right_lower):
        if w in _METRIC_MODIFIERS:
            return "metric"

    # 7. Default — bare integer with no metric markers.
    return "structural"


def scan_numerics(prose: str) -> list[NumericToken]:
    """Find every numeric token in ``prose``; classify each as metric
    or structural. Used by validate_claims Pass B."""
    tokens: list[NumericToken] = []
    for match in _NUMERIC_RE.finditer(prose):
        raw = match.group(0)
        # Strip leading approx markers from the parseable substring.
        cleaned = re.sub(
            r"^(≈|~|about\s+|roughly\s+)\s*", "", raw, flags=re.IGNORECASE
        )
        # Strip trailing unit + leading currency for value parsing.
        value_str = (
            cleaned.replace("$", "")
                  .replace("%", "")
                  .replace("×", "")
                  .replace("bps", "")
                  .strip()
        )
        if value_str.lower().endswith("x"):
            value_str = value_str[:-1].strip()
        value_str = value_str.replace(",", "")
        try:
            value = float(value_str)
        except ValueError:
            continue
        start, end = match.span(0)
        kind = _classify_token(raw, prose, (start, end))
        tokens.append(NumericToken(
            value=value, span=(start, end), text=raw, kind=kind,
        ))
    return tokens


# ---------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------

@dataclass
class ClaimDisposition:
    claim: Claim
    status: Literal["passed", "normalized", "stripped"]
    true_value: float | None = None
    reason: str | None = None


@dataclass
class ValidationReport:
    """Result of ``validate_claims``."""
    prose: str                                        # cleaned prose
    original_prose: str
    claim_dispositions: list[ClaimDisposition] = field(default_factory=list)
    undeclared_strips: list[dict[str, Any]] = field(default_factory=list)
    has_any_strip: bool = False


def _approximately_equal(a: float, b: float, tolerance: float) -> bool:
    """``a`` and ``b`` agree within ``tolerance`` relative to
    ``max(|a|, |b|)``. Handles zero gracefully: if both are zero,
    agree; if one is zero, require absolute distance < ``tolerance``."""
    if math.isnan(a) or math.isnan(b):
        return False
    if a == 0.0 and b == 0.0:
        return True
    scale = max(abs(a), abs(b))
    if scale == 0.0:
        return True
    return abs(a - b) / scale <= tolerance


# Clause boundaries — sentence terminators + comma-clause boundaries +
# conjunctions. Stripping picks the smallest clause around the offending
# span so we keep as much of the sentence as possible.
_CLAUSE_SPLIT_RE = re.compile(
    r"(?<=[.!?])\s+(?=[A-Z])"          # sentence end → next capitalized
    r"|(?:,\s+(?:but|however|while|whereas|though|and|or|so|yet)\b)"
    r"|(?:\s*[—–-]\s+)"                 # em-dash / en-dash clause
)


def _strip_clause(prose: str, span: tuple[int, int]) -> str:
    """Remove the smallest clause containing ``span``. The clause is
    bounded by sentence terminators, comma-conjunctions, or em-dashes.
    Leaves the surrounding prose intact and well-formed (no dangling
    fragments)."""
    start, end = span
    # Find clause boundaries left of start and right of end.
    boundaries = [
        (m.start(), m.end()) for m in _CLAUSE_SPLIT_RE.finditer(prose)
    ]
    left = 0
    right = len(prose)
    for bstart, bend in boundaries:
        if bend <= start:
            left = bend
        elif bstart >= end and right == len(prose):
            right = bstart
            break
    # Sentence-level fallback when no clause boundary is present.
    # If the entire matched region is a single sentence, drop it whole
    # including any trailing sentence terminator (and the space before).
    chunk = prose[left:right]
    # Trim trailing punctuation/spaces from the kept-left side and
    # leading from the kept-right side.
    before = prose[:left].rstrip()
    after = prose[right:].lstrip()
    # Collapse double terminators (e.g. ". .") that can arise.
    if before.endswith(".") and after.startswith("."):
        after = after[1:].lstrip()
    if before and after:
        # Re-stitch with a single space.
        return f"{before} {after}".strip()
    return (before or after).strip()


def validate_claims(
    prose: str,
    claims: list[Claim],
    result: pd.DataFrame,
    *,
    tolerance: float = CLAIM_TOLERANCE,
) -> ValidationReport:
    """Validate every metric numeric in ``prose`` against ``result``
    via the declared ``claims`` list + an undeclared-number scan
    closing the "just don't declare it" bypass.

    Two-pass:

    * Pass A — each ``Claim`` is recomputed from ``result`` via its
      ``source``. Within ``tolerance`` → pass; within tolerance band
      → normalized to the true cell value; doesn't trace → tier 3
      (clause-level strip).
    * Pass B — ``scan_numerics(prose)`` produces every numeric token;
      metric-shaped tokens must be covered by a passing Pass-A claim
      (the claim's text_span overlaps the token's span). Uncovered
      metric numerics → tier 3 (clause-level strip).

    Structural integers (entity counts, years, ordinals) are exempt
    from Pass B per the scanner's classification — they don't need
    a backing claim.

    Returns a ``ValidationReport`` with the cleaned ``prose``, the
    per-claim disposition list, and the list of undeclared-numeric
    strips. The whole response is NEVER hard-rejected — only the
    offending clauses are excised.
    """
    cleaned = prose
    report = ValidationReport(prose=cleaned, original_prose=prose)
    passing_spans: list[tuple[int, int]] = []

    # --- Pass A: declared claims ----------------------------------
    spans_to_strip: list[tuple[tuple[int, int], str]] = []
    span_normalizations: list[tuple[tuple[int, int], str, str]] = []
    for claim in claims:
        try:
            true_value = claim.source.resolve(result)
        except (LookupError, ValueError, ZeroDivisionError) as exc:
            # Source can't resolve — strip the claim's clause.
            span = _find_span_in_prose(cleaned, claim.text_span)
            if span is not None:
                spans_to_strip.append(
                    (span, f"source did not resolve: {exc}"))
            report.claim_dispositions.append(ClaimDisposition(
                claim=claim, status="stripped", reason=str(exc),
            ))
            continue

        if _approximately_equal(claim.value, true_value, tolerance):
            if math.isclose(claim.value, true_value, rel_tol=1e-9):
                report.claim_dispositions.append(ClaimDisposition(
                    claim=claim, status="passed", true_value=true_value,
                ))
            else:
                # Within tolerance but not exact — normalize.
                span = _find_span_in_prose(cleaned, claim.text_span)
                normalized_text = _format_normalized(
                    claim.text_span, claim.value, true_value)
                if span is not None and normalized_text != claim.text_span:
                    span_normalizations.append(
                        (span, normalized_text, claim.text_span))
                report.claim_dispositions.append(ClaimDisposition(
                    claim=claim, status="normalized",
                    true_value=true_value,
                    reason=f"normalized from {claim.value} to {true_value}",
                ))
            if span := _find_span_in_prose(cleaned, claim.text_span):
                passing_spans.append(span)
        else:
            span = _find_span_in_prose(cleaned, claim.text_span)
            if span is not None:
                spans_to_strip.append((
                    span,
                    f"claim {claim.value} does not match resolved "
                    f"{true_value} within tolerance {tolerance}",
                ))
            report.claim_dispositions.append(ClaimDisposition(
                claim=claim, status="stripped",
                true_value=true_value,
                reason=f"value {claim.value} does not match {true_value}",
            ))

    # --- Pass B: undeclared-number scan ---------------------------
    tokens = scan_numerics(cleaned)
    for tok in tokens:
        if tok.kind != "metric":
            continue                # structural integers exempt
        if any(_spans_overlap(tok.span, s) for s in passing_spans):
            continue                # covered by a passing claim
        # Uncovered metric numeric — strip its clause.
        spans_to_strip.append((tok.span, "undeclared metric numeric in prose"))
        report.undeclared_strips.append({
            "value": tok.value, "text": tok.text, "span": tok.span,
        })

    # --- Apply normalizations first (left-to-right, span-preserving),
    # then strips. Sort by span start descending so earlier offsets
    # remain valid as we mutate.
    for span, new_text, old_text in sorted(
        span_normalizations, key=lambda t: t[0][0], reverse=True,
    ):
        s, e = span
        cleaned = cleaned[:s] + new_text + cleaned[e:]

    # Dedupe overlapping strip spans (e.g. a failed declared claim and
    # the Pass-B scan can both flag the same numeric — stripping its
    # clause twice would walk past the intended boundary).
    deduped_strips: list[tuple[tuple[int, int], str]] = []
    for span, reason in sorted(spans_to_strip, key=lambda t: t[0][0],
                               reverse=True):
        if any(_spans_overlap(span, ds[0]) for ds in deduped_strips):
            continue
        deduped_strips.append((span, reason))

    for span, reason in deduped_strips:
        cleaned = _strip_clause(cleaned, span)
        report.has_any_strip = True

    report.prose = cleaned.strip()
    return report


def _find_span_in_prose(prose: str, text_span: str) -> tuple[int, int] | None:
    """Locate ``text_span`` in ``prose``; returns (start, end) or
    None. First exact match used."""
    idx = prose.find(text_span)
    if idx < 0:
        return None
    return (idx, idx + len(text_span))


def _spans_overlap(
    a: tuple[int, int], b: tuple[int, int],
) -> bool:
    return not (a[1] <= b[0] or b[1] <= a[0])


def _format_normalized(
    text_span: str, claimed: float, true_value: float,
) -> str:
    """Replace the numeric inside ``text_span`` with the true value,
    preserving the surrounding text (e.g. "≈6%" → "6.2%").

    Normalization always renders enough precision to FAITHFULLY show
    the true value — even if the model's rounding character is lost.
    Per D25.4 ("normalizes to the true value"), accurate data wins
    over preserved rounding when the two diverge.
    """
    match = _NUMERIC_RE.search(text_span)
    if not match:
        return text_span
    num_start, num_end = match.span(0)
    raw = match.group(0)
    has_pct = "%" in raw
    has_dollar = "$" in raw
    has_x = raw.lower().rstrip().endswith("x") or raw.endswith("×")
    has_bps = raw.lower().endswith("bps")
    # Pick a precision that shows all significant decimals of the
    # true value (up to a sensible cap).
    significant = _decimal_places_for(true_value, cap=4)
    if has_pct:
        rendered = f"{true_value:.{max(significant, 1)}f}%"
    elif has_dollar:
        rendered = f"${true_value:,.{max(significant, 2)}f}"
    elif has_x:
        rendered = f"{true_value:.{max(significant, 1)}f}x"
    elif has_bps:
        rendered = f"{int(round(true_value))}bps"
    else:
        rendered = f"{true_value:.{max(significant, 2)}f}"
    return text_span[:num_start] + rendered + text_span[num_end:]


def _decimal_places_for(value: float, *, cap: int = 4) -> int:
    """How many decimal places does ``value`` need to be faithfully
    displayed? E.g. 1.062 → 3, 1.0 → 0, 0.12345 → cap."""
    if value == 0 or math.isnan(value) or math.isinf(value):
        return 0
    s = f"{value:.{cap}f}".rstrip("0")
    if "." not in s:
        return 0
    return len(s.split(".", 1)[1])
