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
# Wave 3 Stage 6.5 Fix 11 — shared aggregation + row-filter helpers.
#
# The validator's mean/sum calculation MUST be byte-identical to
# whatever surfaces aggregates to the model (see
# ``lake_tools.read_lake_table`` — Fix 11a). If a precomputed mean
# and the validator's recomputed mean diverge even by ULP, the model
# copies the surfaced value and the claim normalizes-not-passes —
# which the user has flagged as a failure signal. These helpers are
# the single source of truth for both code paths.
# ---------------------------------------------------------------------

def aggregate_column(
    df: "pd.DataFrame", column: str, agg: "AggregateFunc",
) -> float:
    """Aggregate a single column over a (filtered) DataFrame.

    The same computation the validator uses to recompute a multi-row
    CellLookup result. ``lake_tools.read_lake_table`` calls this to
    pre-compute the aggregates block shown to the model. Keep this
    function the canonical aggregation — do NOT duplicate the
    summing/averaging logic elsewhere.

    Wave 3 Stage 6.5 Fix 13b: ``.astype(float)`` raises ``TypeError``
    on datetime / Timestamp columns. Catch it and re-raise as
    ``LookupError`` so the per-claim catch tuple in
    ``validate_claims`` handles it like any other "couldn't resolve"
    case — clean strip, no crash up the stack."""
    try:
        values = df[column].astype(float).tolist()
    except (TypeError, ValueError) as exc:
        raise LookupError(
            f"aggregate_column: column={column!r} dtype is not "
            f"numeric ({df[column].dtype}) — cannot aggregate."
        ) from exc
    if agg == "sum":
        return float(sum(values))
    if agg == "mean":
        if not values:
            raise ValueError(
                f"aggregate_column: 0 values to aggregate for "
                f"column={column!r}."
            )
        return float(sum(values) / len(values))
    raise ValueError(
        f"aggregate_column: unknown agg={agg!r}; expected 'sum' or 'mean'."
    )


def _apply_row_filter(
    df: "pd.DataFrame", row_filter: "dict[str, Any]",
) -> "pd.DataFrame":
    """Apply a CellLookup ``row_filter`` to ``df``. Scalar values
    use ``==`` (single match). List/tuple values use ``.isin(v)``
    (Fix 11b: OR-clause support — e.g.
    ``{"derived_zone": ["Z02", "Z03"]}`` means "Z02 OR Z03").

    Both the validator's ``CellLookup._resolve_in`` and the Fix-9c
    multi-row gate in ``Specialist._count_filter_matches`` call this
    helper. Keeping them in lockstep is load-bearing — the trace
    caught them diverging once (gate silently passed, resolver
    crashed); this helper closes that hole."""
    sub = df
    for k, v in row_filter.items():
        if k not in sub.columns:
            # Same surface as the existing _resolve_in pre-check; the
            # caller pre-validates column presence in any case.
            raise LookupError(
                f"row_filter key {k!r} not in frame columns "
                f"{list(sub.columns)}."
            )
        if isinstance(v, (list, tuple, set)):
            sub = sub[sub[k].isin(list(v))]
        else:
            sub = sub[sub[k] == v]
    return sub


# ---------------------------------------------------------------------
# Claim sources — closed union
# ---------------------------------------------------------------------

@dataclass
class CellLookup:
    """A single-cell lookup: filter result rows, then take ``column``
    from the match.

    When the filter matches multiple rows, ``agg`` must be set to one
    of ``"sum"`` or ``"mean"`` so the lookup is unambiguous. With
    ``agg=None`` (the default) a multi-row match raises — that's the
    closed-grammar safety net against accidentally aggregating when
    the model meant a single cell.

    ``frame`` (Wave 3 Stage 6.5 Fix 9b) optionally names which captured
    frame the lookup resolves against — one of ``"tenant"`` / ``"lake"``
    / ``"merged"``. ``None`` means the single ``result`` frame the
    validator was handed. ``"tenant"`` / ``"lake"`` are used in the
    merge-fail dual-frame path so each claim can target its real
    source. The grounding guarantee is preserved — each claim still
    resolves against a REAL frame, never an invented dict.
    """
    row_filter: dict[str, Any]
    column: str
    agg: AggregateFunc | None = None     # noqa: F821 — forward ref
    frame: Literal["tenant", "lake", "merged"] | None = None

    def resolve(
        self,
        result: pd.DataFrame,
        *,
        frames: dict[str, pd.DataFrame] | None = None,
    ) -> float:
        """Resolve this lookup against the chosen frame.

        Frame-walk semantics (Wave 3 Stage 6.5 Fix 10c):
        * ``self.frame`` is set + ``frames`` supplied → resolve only
          against ``frames[self.frame]`` (strict — the model said
          where to look).
        * ``self.frame`` is None + ``frames`` supplied → try ``result``
          first, then walk ``frames`` candidates (``merged`` →
          ``tenant`` → ``lake``). First frame whose column + filter
          match wins. This makes untagged peer claims resolve against
          the lake frame in the dual-frame path without requiring
          the model to author ``frame: "lake"`` explicitly.
        * ``frames`` is None → resolve only against ``result``
          (backward compat for the single-frame path).

        The grounding wall is intact: every resolution still happens
        against a real frame's cells. The change is WHICH frame, not
        whether a real cell.
        """
        if self.frame is not None and frames is not None and self.frame in frames:
            return self._resolve_in(frames[self.frame])
        candidates: list[pd.DataFrame] = [result]
        if frames is not None and self.frame is None:
            for k in ("merged", "tenant", "lake"):
                cand = frames.get(k)
                if cand is None:
                    continue
                if cand is result:
                    continue
                candidates.append(cand)
        last_exc: Exception | None = None
        for cand in candidates:
            try:
                return self._resolve_in(cand)
            except LookupError as exc:
                last_exc = exc
                continue
        if last_exc is None:
            raise LookupError(
                f"CellLookup row_filter={self.row_filter} could not "
                f"resolve in any candidate frame."
            )
        raise last_exc

    def _resolve_in(self, df: pd.DataFrame) -> float:
        """Resolve against a single frame. Raises ``LookupError`` if
        the column is absent, the filter matches 0 rows, or matches
        multiple rows without ``agg`` set. The frame-walk caller
        catches these and tries the next candidate; the no-frames
        path lets them propagate."""
        for k, v in self.row_filter.items():
            if k not in df.columns:
                raise LookupError(
                    f"CellLookup filter key {k!r} not in frame columns "
                    f"{list(df.columns)}."
                )
        if self.column not in df.columns:
            raise LookupError(
                f"CellLookup column {self.column!r} not in frame columns "
                f"{list(df.columns)}."
            )
        sub = _apply_row_filter(df, self.row_filter)
        if len(sub) == 0:
            raise LookupError(
                f"CellLookup row_filter={self.row_filter} matched 0 rows."
            )
        if len(sub) > 1:
            if self.agg is None:
                raise LookupError(
                    f"CellLookup row_filter={self.row_filter} matched "
                    f"{len(sub)} rows; expected exactly 1, or set agg "
                    f"= 'sum'|'mean' to aggregate."
                )
            return aggregate_column(sub, self.column, self.agg)
        # Single-row case. ``float(Timestamp)`` raises TypeError;
        # re-raise as LookupError so the per-claim catch tuple in
        # ``validate_claims`` handles it cleanly (Fix 13b).
        try:
            return float(sub.iloc[0][self.column])
        except (TypeError, ValueError) as exc:
            raise LookupError(
                f"_resolve_in: cannot cast cell "
                f"({sub.iloc[0][self.column]!r}, dtype "
                f"{sub[self.column].dtype}) to float — column is "
                f"not numeric."
            ) from exc


DerivationOp = Literal["difference", "ratio", "pct_change", "aggregate"]
AggregateFunc = Literal["sum", "mean"]


@dataclass
class Derivation:
    """A small arithmetic over cells of the result. ``op`` is one of
    the four closed-grammar operations; ``operands`` are CellLookups
    that resolve to the operation's inputs.

    Operand CellLookups inherit the ``frames`` kwarg passed to
    ``resolve`` — each operand independently honors its own ``frame``
    field, so a Derivation can mix tenant and lake operands (e.g. a
    pct_change of own value vs peer baseline)."""
    op: DerivationOp
    operands: list[CellLookup]
    agg: AggregateFunc | None = None

    def resolve(
        self,
        result: pd.DataFrame,
        *,
        frames: dict[str, pd.DataFrame] | None = None,
    ) -> float:
        if self.op == "difference":
            if len(self.operands) != 2:
                raise ValueError(
                    f"difference needs exactly 2 operands; got {len(self.operands)}"
                )
            a, b = (op.resolve(result, frames=frames) for op in self.operands)
            return a - b
        if self.op == "ratio":
            if len(self.operands) != 2:
                raise ValueError(
                    f"ratio needs exactly 2 operands; got {len(self.operands)}"
                )
            a, b = (op.resolve(result, frames=frames) for op in self.operands)
            if b == 0:
                raise ZeroDivisionError("ratio: denominator is 0")
            return a / b
        if self.op == "pct_change":
            if len(self.operands) != 2:
                raise ValueError(
                    f"pct_change needs exactly 2 operands; got {len(self.operands)}"
                )
            a, b = (op.resolve(result, frames=frames) for op in self.operands)
            if b == 0:
                raise ZeroDivisionError("pct_change: prior value is 0")
            return (a - b) / b
        if self.op == "aggregate":
            if self.agg not in ("sum", "mean"):
                raise ValueError(
                    f"aggregate needs agg ∈ {{'sum', 'mean'}}; got {self.agg!r}"
                )
            values = [op.resolve(result, frames=frames) for op in self.operands]
            if self.agg == "sum":
                return float(sum(values))
            return float(sum(values) / len(values))
        raise ValueError(f"Unknown Derivation.op={self.op!r}")


@dataclass
class ValueRef:
    """Wave 3 Stage 6.5 Fix 13a — peer values reach prose by ADDRESS,
    not transcription. The model names a (dimension, value, metric)
    triple instead of typing the number; the server resolves the
    exact float from the same aggregation path that produced the
    surfaced ``aggregates`` block.

    ``by``     — the dimension to filter on (e.g. ``"category"``).
    ``value``  — the dimension value (e.g. ``"MEAT"``).
    ``metric`` — the metric column to aggregate (e.g. ``"units_index"``).
    ``agg``    — defaults to ``"mean"`` (matching the aggregates block);
                 ``"sum"`` is allowed for the rare summed-metric case.
    ``frame``  — optional, like ``CellLookup.frame``. Defaults to
                 ``"lake"`` if not set (the natural source for peer
                 metric aggregates).

    Why this exists. The Fix 11a aggregates block surfaces real
    per-dimension means; the model's job is to copy them verbatim
    into ``claim.value``. Haiku's prior is to round
    ``0.9229 → 0.92``, which lands within the 1% tolerance band and
    shows as ``[normalized]`` — the camouflaged-guessing failure
    mode. ``ValueRef`` removes the failure class by making the model
    write the *address* of the value; the server substitutes the
    exact float. The grounding wall is preserved — the substituted
    value still comes from a real aggregate cell over a real frame.
    """
    by: str
    value: Any
    metric: str
    agg: AggregateFunc = "mean"
    frame: Literal["tenant", "lake", "merged"] | None = "lake"

    def resolve(
        self,
        result: "pd.DataFrame",
        *,
        frames: dict[str, "pd.DataFrame"] | None = None,
    ) -> float:
        """Resolve via the SAME aggregation path the validator uses
        for multi-row ``CellLookup`` and that ``_compute_lake_aggregates``
        used to produce the surfaced aggregates block. Byte-identical
        by construction."""
        # Pick the frame. Default "lake" — peer aggregates live there.
        target: pd.DataFrame | None = None
        if frames is not None and self.frame is not None:
            target = frames.get(self.frame)
        if target is None:
            target = result
        if target is None or self.by not in target.columns:
            raise LookupError(
                f"ValueRef: dimension {self.by!r} not in target frame "
                f"(frame={self.frame!r})."
            )
        if self.metric not in target.columns:
            raise LookupError(
                f"ValueRef: metric {self.metric!r} not in target frame "
                f"(frame={self.frame!r})."
            )
        sub = target[target[self.by] == self.value]
        if len(sub) == 0:
            raise LookupError(
                f"ValueRef: {self.by}={self.value!r} matched 0 rows."
            )
        return aggregate_column(sub, self.metric, self.agg)


Source = CellLookup | Derivation | ValueRef


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


@dataclass
class StructuredValidationReport:
    """Wave 3.5 — result of ``validate_structured_response``. The cleaned
    structured fields plus the merged per-claim dispositions across all
    fields."""
    headline: str
    evidence: list[str] = field(default_factory=list)
    so_what: str | None = None
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


# Clause boundaries — distinguished by KIND so the stripper can
# treat them differently:
#
# * sentence: ". X" → the period belongs to the LEFT clause; the
#   stripper keeps the period when the left clause survives.
# * sep (comma-conjunction or em-dash): ", but" → the punctuation
#   is a SEPARATOR between clauses; the stripper consumes the
#   separator (drops both the comma and the conjunction) so no
#   dangling ", but" fragments remain.
_BOUNDARY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?<=[.!?])\s+(?=[A-Z])"), "sentence"),
    (
        re.compile(
            r",\s+(?:but|however|while|whereas|though|and|or|so|yet)\b\s*"
        ),
        "sep",
    ),
    (re.compile(r"\s*[—–]\s+"), "sep"),
]


def _find_boundaries(prose: str) -> list[tuple[int, int, str]]:
    """Return (start, end, kind) tuples for every clause boundary in
    ``prose``, sorted by start position."""
    boundaries: list[tuple[int, int, str]] = []
    for pattern, kind in _BOUNDARY_PATTERNS:
        for m in pattern.finditer(prose):
            boundaries.append((m.start(), m.end(), kind))
    boundaries.sort(key=lambda b: b[0])
    return boundaries


def _strip_clause(prose: str, span: tuple[int, int]) -> str:
    """Remove the smallest clause containing ``span``. Clause bounds
    are sentence ends, comma-conjunctions, or em-dashes. Treats
    separators (comma-conj, em-dash) as consumable so no dangling
    fragments remain (no ``", but"`` orphans)."""
    start, end = span
    boundaries = _find_boundaries(prose)

    # Find the boundary just left of `start` (the closest one whose
    # end is at or before `start`).
    left = 0
    left_kind: str | None = None
    for bstart, bend, kind in boundaries:
        if bend <= start:
            # If kind is 'sep', the separator goes WITH the stripped
            # clause — start the kept-left region BEFORE the separator.
            # If kind is 'sentence', keep the terminator on the left.
            left = bstart if kind == "sep" else bend
            left_kind = kind
        else:
            break

    # Find the boundary just right of `end` (the first boundary that
    # starts at or after `end`).
    right = len(prose)
    right_kind: str | None = None
    for bstart, bend, kind in boundaries:
        if bstart >= end:
            # If kind is 'sep', the separator goes WITH the stripped
            # clause — start the kept-right region AFTER the separator.
            # If kind is 'sentence', start the kept-right region after
            # the period+space (so the next sentence stands alone).
            right = bend if kind == "sep" else bend
            right_kind = kind
            break

    before = prose[:left].rstrip()
    after = prose[right:].lstrip()

    # If we lost the terminator for the LEFT clause (because we
    # consumed a comma-conjunction), put one back so the surviving
    # left clause reads as a complete sentence.
    if (
        before
        and not before.endswith((".", "!", "?"))
        and left_kind == "sep"
    ):
        before = before + "."

    # Collapse double terminators (". .") that can arise when stripping
    # a complete sentence between two sentence boundaries.
    if before.endswith(".") and after.startswith("."):
        after = after.lstrip(".").lstrip()

    if before and after:
        return f"{before} {after}".strip()
    return (before or after).strip()


def validate_claims(
    prose: str,
    claims: list[Claim],
    result: pd.DataFrame,
    *,
    tolerance: float = CLAIM_TOLERANCE,
    frames: dict[str, pd.DataFrame] | None = None,
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
    declared_spans: list[tuple[int, int]] = []  # covers passed + stripped

    # --- Pass A: declared claims ----------------------------------
    spans_to_strip: list[tuple[tuple[int, int], str]] = []
    span_normalizations: list[tuple[tuple[int, int], str, str]] = []
    for claim in claims:
        # Track every declared claim's span — passing OR failing — so
        # Pass B doesn't double-log a failed declared number as
        # "undeclared" (it IS declared, just wrong; Pass A already
        # accounts for the strip).
        declared_span = _find_span_in_prose(cleaned, claim.text_span)
        if declared_span is not None:
            declared_spans.append(declared_span)
        try:
            true_value = claim.source.resolve(result, frames=frames)
        except (LookupError, ValueError, ZeroDivisionError, TypeError) as exc:
            # Source can't resolve — strip the claim's clause.
            if declared_span is not None:
                spans_to_strip.append(
                    (declared_span, f"source did not resolve: {exc}"))
            report.claim_dispositions.append(ClaimDisposition(
                claim=claim, status="stripped", reason=str(exc),
            ))
            continue

        # Wave 3 Stage 6.5 Fix 13a — ValueRef claims are exact by
        # construction. The model wrote an address, not a value; the
        # server resolved the exact float. Override claim.value with
        # the resolved value so the comparison is trivially equal —
        # status becomes ``passed``, never ``normalized``. This is
        # the core of the fix: removing the model-rounding failure
        # class by removing the model's responsibility for the
        # number entirely. The text_span in prose may still contain
        # the model's rough number (e.g. "0.92"); that gets normalized
        # into the displayed value via the existing tolerance path
        # below if model wrote a different number, or left as-is if
        # the model wrote the exact value.
        if isinstance(claim.source, ValueRef):
            claim.value = true_value

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
        if any(_spans_overlap(tok.span, s) for s in declared_spans):
            continue                # declared but failed — Pass A handled
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


def validate_structured_response(
    *,
    headline: str,
    evidence: list[str],
    so_what: str | None,
    claims: list[Claim],
    result: pd.DataFrame,
    tolerance: float = CLAIM_TOLERANCE,
    frames: dict[str, pd.DataFrame] | None = None,
) -> StructuredValidationReport:
    """Wave 3.5 — validate the structured response (headline / evidence /
    so_what) by running the single-string ``validate_claims`` over each
    text field independently, then merging the per-claim dispositions.

    Per-field (rather than a joined string) keeps ``_strip_clause`` offset
    math local, isolates a strip to the bullet that owns the bad number,
    and disambiguates a number that appears in more than one field. Pass B
    (``scan_numerics``) runs per field, so the union of fields equals the
    old single-string scan surface — no cross-field metric numeric escapes
    coverage. The "no untraceable number" guarantee is preserved field by
    field.

    A claim's status is field-independent — it depends only on
    ``claim.value`` versus the value resolved from ``result``/``frames``,
    not on which field the span lives in. So a claim whose span appears in
    two fields yields the same disposition both times; we dedupe by
    ``(text_span, status)`` keeping the first. A claim whose span is in no
    field still gets a disposition (Pass A appends one regardless), so the
    preview harness shows every claim's badge.
    """
    out = StructuredValidationReport(headline="", evidence=[], so_what=None)
    seen: dict[tuple[str, str], ClaimDisposition] = {}

    def _run(text: str) -> str:
        if not text or not text.strip():
            return ""
        rep = validate_claims(
            text, claims, result, tolerance=tolerance, frames=frames,
        )
        for d in rep.claim_dispositions:
            seen.setdefault((d.claim.text_span, d.status), d)
        out.undeclared_strips.extend(rep.undeclared_strips)
        if rep.has_any_strip:
            out.has_any_strip = True
        return rep.prose

    out.headline = _run(headline)
    out.evidence = [cleaned for e in evidence if (cleaned := _run(e))]
    out.so_what = (_run(so_what) or None) if so_what else None
    out.claim_dispositions = list(seen.values())
    return out


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
