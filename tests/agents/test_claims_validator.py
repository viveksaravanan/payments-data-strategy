"""Stage 1c tests — claims validator (SPEC §1.4, D25.4).

The load-bearing wall: every metric numeric in agent prose must
trace to a result cell or declared derivation. Strict guarantee,
graceful handling.

Coverage:

* Each derivation grammar op recomputes correctly on a fixture.
* Disposition matrix (clean / within-tolerance / fabricated declared
  / fabricated undeclared / structural-integer-exempt).
* Clause-level stripping leaves well-formed prose (no dangling
  fragments).
* Metric vs structural scanner classification on boundary cases.
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.agents.claims import (
    CLAIM_TOLERANCE,
    CellLookup,
    Claim,
    Derivation,
    NumericToken,
    scan_numerics,
    validate_claims,
)


# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------

@pytest.fixture
def merged_dairy() -> pd.DataFrame:
    return pd.DataFrame([
        {"category": "DAIRY", "derived_zone": "Z05",
         "own_value": 1.062, "peer_benchmark": 1.00, "gap": 0.062},
        {"category": "DAIRY", "derived_zone": "Z02",
         "own_value": 1.08, "peer_benchmark": 1.02, "gap": 0.06},
    ])


# ---------------------------------------------------------------------
# Derivation grammar — each op recomputes correctly
# ---------------------------------------------------------------------

def test_celllookup_resolves_single_cell(merged_dairy) -> None:
    cl = CellLookup(
        row_filter={"category": "DAIRY", "derived_zone": "Z05"},
        column="own_value",
    )
    assert cl.resolve(merged_dairy) == 1.062


def test_celllookup_no_match_raises(merged_dairy) -> None:
    cl = CellLookup(row_filter={"category": "MEAT"}, column="own_value")
    with pytest.raises(LookupError):
        cl.resolve(merged_dairy)


def test_celllookup_multiple_match_raises(merged_dairy) -> None:
    cl = CellLookup(row_filter={"category": "DAIRY"}, column="own_value")
    with pytest.raises(LookupError):
        cl.resolve(merged_dairy)


def test_difference_recomputes(merged_dairy) -> None:
    d = Derivation(
        op="difference",
        operands=[
            CellLookup({"category": "DAIRY", "derived_zone": "Z05"},
                       "own_value"),
            CellLookup({"category": "DAIRY", "derived_zone": "Z05"},
                       "peer_benchmark"),
        ],
    )
    assert d.resolve(merged_dairy) == pytest.approx(0.062)


def test_ratio_recomputes(merged_dairy) -> None:
    d = Derivation(
        op="ratio",
        operands=[
            CellLookup({"category": "DAIRY", "derived_zone": "Z05"},
                       "own_value"),
            CellLookup({"category": "DAIRY", "derived_zone": "Z05"},
                       "peer_benchmark"),
        ],
    )
    assert d.resolve(merged_dairy) == pytest.approx(1.062)


def test_pct_change_recomputes() -> None:
    df = pd.DataFrame([
        {"week": "W1", "units": 1000},
        {"week": "W2", "units": 1200},
    ])
    d = Derivation(
        op="pct_change",
        operands=[
            CellLookup({"week": "W2"}, "units"),
            CellLookup({"week": "W1"}, "units"),
        ],
    )
    assert d.resolve(df) == pytest.approx(0.20)


def test_aggregate_sum_recomputes(merged_dairy) -> None:
    d = Derivation(
        op="aggregate", agg="sum",
        operands=[
            CellLookup({"category": "DAIRY", "derived_zone": "Z05"}, "gap"),
            CellLookup({"category": "DAIRY", "derived_zone": "Z02"}, "gap"),
        ],
    )
    assert d.resolve(merged_dairy) == pytest.approx(0.122)


def test_aggregate_mean_recomputes(merged_dairy) -> None:
    d = Derivation(
        op="aggregate", agg="mean",
        operands=[
            CellLookup({"category": "DAIRY", "derived_zone": "Z05"}, "gap"),
            CellLookup({"category": "DAIRY", "derived_zone": "Z02"}, "gap"),
        ],
    )
    assert d.resolve(merged_dairy) == pytest.approx(0.061)


def test_out_of_grammar_op_rejected() -> None:
    """Closed grammar — anything other than the four ops fails."""
    d = Derivation(op="exponential", operands=[])  # type: ignore[arg-type]
    df = pd.DataFrame([{"x": 1}])
    with pytest.raises(ValueError):
        d.resolve(df)


# ---------------------------------------------------------------------
# Metric vs structural scanner classification
# ---------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("5%",        "metric"),
    ("12.5%",     "metric"),
    ("$5.99",     "metric"),
    ("$1,234",    "metric"),
    ("2x",        "metric"),
    ("3.5x",      "metric"),
    ("1.12",      "metric"),  # decimal → metric (index/ratio)
    ("0.87",      "metric"),
    ("5",         "structural"),
    ("100",       "structural"),
    ("2026",      "structural"),
    ("12",        "structural"),
])
def test_scanner_classifies_standalone_tokens(text, expected) -> None:
    tokens = scan_numerics(text)
    assert len(tokens) >= 1
    assert tokens[0].kind == expected, (
        f"{text!r} → expected {expected}, got {tokens[0].kind}"
    )


def test_scanner_adjacent_modifier_promotes_to_metric() -> None:
    """Bare integer near a metric modifier word becomes metric."""
    tokens = scan_numerics("index 5")
    assert any(t.kind == "metric" and t.value == 5 for t in tokens)


def test_scanner_5_merchants_stays_structural() -> None:
    """The boundary case the user called out — "5 merchants" must
    survive without a backing claim."""
    tokens = scan_numerics("your 5 merchants in 3 zones over 12 weeks")
    for t in tokens:
        assert t.kind == "structural", (
            f"{t.text!r} → expected structural, got {t.kind}"
        )


def test_scanner_year_stays_structural() -> None:
    tokens = scan_numerics("through 2026 the trend held.")
    years = [t for t in tokens if t.value == 2026]
    assert years
    assert all(t.kind == "structural" for t in years)


def test_scanner_picks_up_approximation_marker() -> None:
    tokens = scan_numerics("Your gap is ≈6% above peers.")
    assert any(t.value == 6 and t.kind == "metric" for t in tokens)


# ---------------------------------------------------------------------
# Validator — disposition matrix
# ---------------------------------------------------------------------

def test_clean_declared_claim_passes(merged_dairy) -> None:
    prose = "Your dairy price index in Zone 5 is 1.062, just above peers."
    claim = Claim(
        text_span="1.062",
        value=1.062,
        source=CellLookup(
            {"category": "DAIRY", "derived_zone": "Z05"},
            "own_value",
        ),
    )
    rep = validate_claims(prose, [claim], merged_dairy)
    assert rep.claim_dispositions[0].status == "passed"
    assert rep.prose == prose
    assert not rep.has_any_strip


def test_within_tolerance_normalizes(merged_dairy) -> None:
    """Prose says ≈1.06, cell is 1.062 — within ~1% tolerance →
    normalize to the true cell value."""
    prose = "Your dairy price index is ≈1.06, slightly above peers."
    claim = Claim(
        text_span="≈1.06",
        value=1.06,
        source=CellLookup(
            {"category": "DAIRY", "derived_zone": "Z05"},
            "own_value",
        ),
    )
    rep = validate_claims(prose, [claim], merged_dairy)
    assert rep.claim_dispositions[0].status == "normalized"
    assert rep.claim_dispositions[0].true_value == 1.062
    assert "1.06" not in rep.prose or "1.062" in rep.prose
    assert not rep.has_any_strip


def test_fabricated_declared_claim_strips_clause(merged_dairy) -> None:
    """Prose declares 3.00 with a source that resolves to 1.062.
    Not within tolerance → strip the containing clause."""
    prose = (
        "Your dairy price index is 3.00, dramatically above peers. "
        "The MEAT category is on baseline."
    )
    claim = Claim(
        text_span="3.00",
        value=3.00,
        source=CellLookup(
            {"category": "DAIRY", "derived_zone": "Z05"},
            "own_value",
        ),
    )
    rep = validate_claims(prose, [claim], merged_dairy)
    assert rep.claim_dispositions[0].status == "stripped"
    # The bad clause is removed; the meat sentence survives.
    assert "3.00" not in rep.prose
    assert "MEAT" in rep.prose
    # No dangling fragment.
    assert "[" not in rep.prose and "(" not in rep.prose
    assert rep.has_any_strip


def test_fabricated_undeclared_metric_strips_clause(merged_dairy) -> None:
    """Prose contains an undeclared 50% — no backing claim, metric-
    shaped → strip its clause."""
    prose = (
        "Your dairy price index is 1.062, just above peers. "
        "Promo penetration is 50%, far above the metro average."
    )
    claim = Claim(
        text_span="1.062",
        value=1.062,
        source=CellLookup(
            {"category": "DAIRY", "derived_zone": "Z05"},
            "own_value",
        ),
    )
    rep = validate_claims(prose, [claim], merged_dairy)
    # First claim passes; undeclared 50% gets stripped.
    assert rep.claim_dispositions[0].status == "passed"
    assert "50%" not in rep.prose
    assert "1.062" in rep.prose      # legit claim survives
    assert rep.undeclared_strips
    assert rep.has_any_strip


def test_structural_integer_in_prose_survives(merged_dairy) -> None:
    """The boundary case the user pinned: "5 stores in Zone 3" must
    NOT be stripped even without a backing claim. The scanner
    classifies it as structural; Pass B exempts it."""
    prose = (
        "Across your 5 stores in Zone 3, the dairy price index is 1.062 "
        "over the 12 weeks of the window."
    )
    claim = Claim(
        text_span="1.062",
        value=1.062,
        source=CellLookup(
            {"category": "DAIRY", "derived_zone": "Z05"},
            "own_value",
        ),
    )
    rep = validate_claims(prose, [claim], merged_dairy)
    # "5 stores", "Zone 3", "12 weeks" all survive.
    assert "5 stores" in rep.prose
    assert "Zone 3" in rep.prose
    assert "12 weeks" in rep.prose
    assert "1.062" in rep.prose
    assert not rep.has_any_strip


def test_source_does_not_resolve_strips_clause(merged_dairy) -> None:
    """Claim's source can't resolve at all (filter matches no rows) →
    strip the clause."""
    prose = (
        "Your produce category index is 2.50, far above peers. "
        "Dairy stays in line."
    )
    claim = Claim(
        text_span="2.50",
        value=2.50,
        source=CellLookup(
            {"category": "PRODUCE", "derived_zone": "Z01"},
            "own_value",
        ),
    )
    rep = validate_claims(prose, [claim], merged_dairy)
    assert rep.claim_dispositions[0].status == "stripped"
    assert "2.50" not in rep.prose
    assert "Dairy" in rep.prose


def test_clause_strip_consumes_leading_comma_conjunction() -> None:
    """Regression — Checkpoint 2 review caught the harness leaving
    ", but" as a dangling fragment when stripping the right-side
    clause of a "X, but Y" sentence. The fix: comma-conjunctions are
    separators that get consumed entirely, not boundaries that get
    kept. Exercised at the ``_strip_clause`` unit level so we don't
    have to set up a full validator fixture."""
    from src.agents.claims import _strip_clause
    import re
    prose = "Your dairy is up 5%, but produce fell 30%. Meat held flat."
    span = re.search(r"30%", prose).span()
    out = _strip_clause(prose, span)
    assert "30%" not in out
    assert ", but" not in out
    assert "Your dairy is up 5%." in out
    assert "Meat held flat." in out


def test_clause_strip_consumes_leading_however_conjunction() -> None:
    """Same regression for 'however' — common in formal prose."""
    from src.agents.claims import _strip_clause
    import re
    prose = "Your dairy is up 5%, however produce fell 30%."
    span = re.search(r"30%", prose).span()
    out = _strip_clause(prose, span)
    assert "30%" not in out
    assert "however" not in out
    assert "Your dairy is up 5%." in out


def test_clause_strip_leaves_no_dangling_fragment(merged_dairy) -> None:
    """User amendment 2: stripping is clause-level, not digit-level.
    The bad number is in the middle of a sentence; the WHOLE sentence
    or clause must be excised — not just the digits."""
    prose = (
        "You're priced 99% above peers in dairy. Your meat category is fine."
    )
    claim = Claim(
        text_span="99%",
        value=0.99,
        source=CellLookup(
            {"category": "DAIRY", "derived_zone": "Z05"},
            "own_value",
        ),
    )
    rep = validate_claims(prose, [claim], merged_dairy)
    # Bad clause removed; no fragment like "You're priced  above peers."
    assert "99%" not in rep.prose
    assert "above peers" not in rep.prose
    assert "[" not in rep.prose
    assert "()" not in rep.prose
    assert "Your meat category is fine" in rep.prose


def test_whole_response_not_rejected_on_strip(merged_dairy) -> None:
    """Even when multiple claims fail, the response is returned with
    the bad clauses excised — never hard-rejected."""
    prose = (
        "Your produce index is 2.50, far above peers. "
        "Your dairy index is 1.062, just above peers. "
        "Your meat index is 99%, also off."
    )
    claims = [
        Claim(text_span="2.50", value=2.50,
              source=CellLookup({"category": "PRODUCE"}, "own_value")),
        Claim(text_span="1.062", value=1.062,
              source=CellLookup(
                  {"category": "DAIRY", "derived_zone": "Z05"},
                  "own_value")),
    ]
    rep = validate_claims(prose, claims, merged_dairy)
    assert "1.062" in rep.prose
    assert "2.50" not in rep.prose
    assert "99%" not in rep.prose
    assert rep.has_any_strip
    # Response shape survives — prose is non-empty after strips.
    assert len(rep.prose) > 0


# ---------------------------------------------------------------------
# Tolerance bounds
# ---------------------------------------------------------------------

def test_tolerance_boundary_just_inside_normalizes(merged_dairy) -> None:
    """1.072 vs true 1.062 → relative gap ≈0.94% — within 1%. Pass
    + normalize."""
    prose = "Your dairy index is 1.072 above peers."
    claim = Claim(
        text_span="1.072", value=1.072,
        source=CellLookup({"category": "DAIRY", "derived_zone": "Z05"},
                          "own_value"),
    )
    rep = validate_claims(prose, [claim], merged_dairy)
    assert rep.claim_dispositions[0].status == "normalized"


def test_tolerance_boundary_just_outside_strips(merged_dairy) -> None:
    """1.20 vs true 1.062 → ~13% off, outside any reasonable tolerance."""
    prose = "Your dairy index is 1.20 above peers."
    claim = Claim(
        text_span="1.20", value=1.20,
        source=CellLookup({"category": "DAIRY", "derived_zone": "Z05"},
                          "own_value"),
    )
    rep = validate_claims(prose, [claim], merged_dairy)
    assert rep.claim_dispositions[0].status == "stripped"


def test_custom_tolerance_passed(merged_dairy) -> None:
    """A wider tolerance accepts a broader rounding."""
    prose = "Your dairy index is 1.10 above peers."
    claim = Claim(
        text_span="1.10", value=1.10,
        source=CellLookup({"category": "DAIRY", "derived_zone": "Z05"},
                          "own_value"),
    )
    rep = validate_claims(prose, [claim], merged_dairy, tolerance=0.10)
    assert rep.claim_dispositions[0].status == "normalized"


def test_pct_change_normalizes_to_scaled_percent() -> None:
    """A ``%`` span backed by a pct_change resolves to a FRACTION
    (-0.268). When the model's rounded value normalizes, the display must
    scale the fraction ×100 — "27%" → "26.8%", never the raw "0.268%".
    Regression for the _format_normalized percent-units bug."""
    frame = pd.DataFrame([{"merchant": "KRG", "own": 11.257, "peer": 15.386}])
    claim = Claim(
        text_span="27% below peers", value=-0.27,
        source=Derivation(op="pct_change", operands=[
            CellLookup({"merchant": "KRG"}, "own"),
            CellLookup({"merchant": "KRG"}, "peer"),
        ]),
    )
    rep = validate_claims("Steak is 27% below peers.", [claim], frame,
                          frames={"tenant": frame})
    assert rep.claim_dispositions[0].status == "normalized"
    # Resolved fraction is -0.268 → must render "-26.8%", never "-0.268%".
    assert "26.8%" in rep.prose
    assert "0.268%" not in rep.prose


def test_ratio_share_normalizes_to_scaled_percent() -> None:
    """A share written as a ``%`` traces to a fraction (ratio) cell;
    normalized display scales ×100 (0.465 → "46.5%", not "0.5%")."""
    frame = pd.DataFrame([{"merchant": "KRG", "steak": 46.5, "total": 100.0}])
    claim = Claim(
        text_span="46% of units", value=0.463,
        source=Derivation(op="ratio", operands=[
            CellLookup({"merchant": "KRG"}, "steak"),
            CellLookup({"merchant": "KRG"}, "total"),
        ]),
    )
    rep = validate_claims("Steak is 46% of units.", [claim], frame,
                          frames={"tenant": frame})
    assert rep.claim_dispositions[0].status == "normalized"
    assert "46.5%" in rep.prose


def test_large_count_renders_as_clean_integer() -> None:
    """A plain per-store/volume figure (>=100) must render as a clean
    comma-integer ("27,442"), never a spurious-precision float
    ("27442.3333") a model computed from a units/stores ratio. Regression
    for the per-store-volume decimal artifact; contrast: prices/ratios
    (<100) keep decimals."""
    from src.agents.claims import _format_normalized
    # Large count → comma integer, no decimals.
    out = _format_normalized("27442.3333 units per store", 27000.0, 27442.3333)
    assert "27,442" in out and "27442.3333" not in out and ".33" not in out
    # A small ratio/price keeps its decimals (magnitude < 100).
    assert _format_normalized("$4.1", 4.1, 4.08) == "$4.08"
    assert "6.8" in _format_normalized("6.8x", 6.8, 6.81)


# ---------------------------------------------------------------------
# Wave 3.5 — validate_structured_response (per-field validation)
# ---------------------------------------------------------------------

def test_structured_per_field_strip_isolated(merged_dairy) -> None:
    """A metric numeric in one evidence bullet with no backing claim is
    stripped from THAT bullet only; the other bullets and the headline
    survive intact. Per-field validation keeps the strip local."""
    from src.agents.claims import validate_structured_response
    report = validate_structured_response(
        headline="Your dairy index is 1.062 above peers.",
        evidence=[
            "The peer benchmark sits at 1.0.",
            "A fabricated 9.99 figure with no claim.",
        ],
        so_what=None,
        claims=[
            Claim(text_span="1.062",
                  value=1.062,
                  source=CellLookup({"category": "DAIRY", "derived_zone": "Z05"}, "own_value")),
            Claim(text_span="1.0",
                  value=1.00,
                  source=CellLookup({"category": "DAIRY", "derived_zone": "Z05"}, "peer_benchmark")),
        ],
        result=merged_dairy,
    )
    # Headline + first bullet keep their grounded numbers.
    assert "1.062" in report.headline
    assert any("1.0" in e for e in report.evidence)
    # The fabricated 9.99 was stripped — no bullet carries it.
    assert not any("9.99" in e for e in report.evidence)


def test_structured_headline_only_degenerate(merged_dairy) -> None:
    """A headline-only answer (no evidence / so_what) validates cleanly:
    the grounded headline survives, evidence stays empty, no fallback."""
    from src.agents.claims import validate_structured_response
    report = validate_structured_response(
        headline="Your dairy index is 1.062 above peers.",
        evidence=[],
        so_what=None,
        claims=[
            Claim(text_span="1.062",
                  value=1.062,
                  source=CellLookup({"category": "DAIRY", "derived_zone": "Z05"}, "own_value")),
        ],
        result=merged_dairy,
    )
    assert "1.062" in report.headline
    assert report.evidence == []
    assert report.so_what is None
    # The claim passed.
    assert any(d.status == "passed" for d in report.claim_dispositions)


def test_structured_so_what_validated(merged_dairy) -> None:
    """An untraceable number in so_what is stripped just like any other
    field (Pass B is span-local per field)."""
    from src.agents.claims import validate_structured_response
    report = validate_structured_response(
        headline="Your dairy index is 1.062 above peers.",
        evidence=[],
        so_what="Lift price by a fabricated 42% next quarter.",
        claims=[
            Claim(text_span="1.062",
                  value=1.062,
                  source=CellLookup({"category": "DAIRY", "derived_zone": "Z05"}, "own_value")),
        ],
        result=merged_dairy,
    )
    # The 42% had no claim — its clause is stripped from so_what.
    assert report.so_what is None or "42%" not in report.so_what


def test_structured_strip_residue_fragment_dropped(merged_dairy) -> None:
    """When stripping an ungrounded numeric clause leaves a grammatically
    dependent residue (em-dash / sep case: "Peers fell only 8% — a smaller
    relative decline." → "a smaller relative decline."), the residue is a
    lowercase fragment and must be dropped, not surfaced as a bullet."""
    from src.agents.claims import validate_structured_response
    report = validate_structured_response(
        headline="Your University City store is doing worse than peers.",
        evidence=[
            "Peers fell only 8% — a smaller relative decline.",
            "The peer benchmark sits at 1.0.",  # grounded — must survive
        ],
        so_what=None,
        claims=[
            Claim(text_span="1.0",
                  value=1.00,
                  source=CellLookup({"category": "DAIRY", "derived_zone": "Z05"}, "peer_benchmark")),
        ],
        result=merged_dairy,
    )
    # The dangling "a smaller relative decline." fragment is gone…
    assert not any("smaller relative decline" in e for e in report.evidence)
    assert all(not (e and e[0].isalpha() and e[0].islower()) for e in report.evidence)
    # …while the grounded bullet survives intact.
    assert any("1.0" in e for e in report.evidence)


def test_is_fragment_classification() -> None:
    """_is_fragment flags lowercase-initial dependent clauses, but not
    sentences that begin with a capital, a digit, or a $/number sigil."""
    from src.agents.claims import _is_fragment
    assert _is_fragment("a smaller relative decline.")
    assert _is_fragment("than peers.")
    assert _is_fragment("")
    assert not _is_fragment("Your dairy ASP is higher than peers.")
    assert not _is_fragment("$3.50 per unit beats the peer average.")
    assert not _is_fragment("35% of baskets included produce.")


# ---------------------------------------------------------------------
# Semantic label checks (Wave 4 — §15 residual hardening)
# ---------------------------------------------------------------------

@pytest.fixture
def semantic_frame() -> pd.DataFrame:
    """One row: a revenue level (for magnitude), a recent/baseline pair
    (pct_change = +0.10, an INCREASE), and own/peer prices (difference
    own-peer = -0.30, i.e. own is LOWER)."""
    return pd.DataFrame([
        {"merchant": "KRG", "revenue": 6_400_000.0,
         "recent": 110.0, "baseline": 100.0,
         "own_price": 3.20, "peer_price": 3.50},
    ])


def _pct_change_claim(text_span: str, value: float) -> Claim:
    return Claim(
        text_span=text_span, value=value,
        source=Derivation(op="pct_change", operands=[
            CellLookup({"merchant": "KRG"}, "recent"),
            CellLookup({"merchant": "KRG"}, "baseline"),
        ]),
    )


def test_magnitude_mislabel_strips(semantic_frame) -> None:
    """Number traces (6.4M) but the scale word says billions → strip."""
    prose = "Revenue reached $6.4B this quarter."
    claim = Claim(text_span="$6.4B", value=6_400_000.0,
                  source=CellLookup({"merchant": "KRG"}, "revenue"))
    rep = validate_claims(prose, [claim], semantic_frame)
    assert rep.claim_dispositions[0].status == "stripped_semantic"
    assert "6.4B" not in rep.prose
    assert rep.has_any_strip


def test_correct_magnitude_passes_untouched(semantic_frame) -> None:
    prose = "Revenue reached $6.4M this quarter."
    claim = Claim(text_span="$6.4M", value=6_400_000.0,
                  source=CellLookup({"merchant": "KRG"}, "revenue"))
    rep = validate_claims(prose, [claim], semantic_frame)
    assert rep.claim_dispositions[0].status == "passed"
    assert rep.prose == prose
    assert not rep.has_any_strip


def test_direction_down_word_contradicts_increase_strips(semantic_frame) -> None:
    """pct_change is +0.10 (up) but the prose says 'fell' → strip."""
    prose = "Sales fell 10% versus the prior week."
    rep = validate_claims(prose, [_pct_change_claim("10%", 0.10)], semantic_frame)
    assert rep.claim_dispositions[0].status == "stripped_semantic"
    assert rep.has_any_strip


def test_correct_direction_passes_untouched(semantic_frame) -> None:
    prose = "Sales rose 10% versus the prior week."
    rep = validate_claims(prose, [_pct_change_claim("10%", 0.10)], semantic_frame)
    assert rep.claim_dispositions[0].status == "passed"
    assert not rep.has_any_strip


def test_direction_mixed_signals_no_strip(semantic_frame) -> None:
    """Both an up- and a down-word in the window → ambiguous → silent
    (false-positive guard: never strip when intent is unclear)."""
    prose = "Revenue rose even as visits fell, up 10% overall."
    rep = validate_claims(prose, [_pct_change_claim("10%", 0.10)], semantic_frame)
    assert rep.claim_dispositions[0].status == "passed"
    assert not rep.has_any_strip


def test_direction_only_checks_signed_derivations(semantic_frame) -> None:
    """A ratio has no meaningful sign — 'lower' nearby must NOT strip it."""
    prose = "The recent-to-baseline ratio is 1.10, lower than rivals."
    claim = Claim(text_span="1.10", value=1.10,
                  source=Derivation(op="ratio", operands=[
                      CellLookup({"merchant": "KRG"}, "recent"),
                      CellLookup({"merchant": "KRG"}, "baseline"),
                  ]))
    rep = validate_claims(prose, [claim], semantic_frame)
    assert rep.claim_dispositions[0].status == "passed"
    assert not rep.has_any_strip


def test_peer_comparison_adjective_not_stripped(semantic_frame) -> None:
    """'above'/'below' are intentionally NOT checked (peer-subject
    ambiguity) — confirm we don't over-reach and strip such prose."""
    prose = "Our price sits above peers by $0.30."
    claim = Claim(text_span="$0.30", value=-0.30,
                  source=Derivation(op="difference", operands=[
                      CellLookup({"merchant": "KRG"}, "own_price"),
                      CellLookup({"merchant": "KRG"}, "peer_price"),
                  ]))
    rep = validate_claims(prose, [claim], semantic_frame)
    assert rep.claim_dispositions[0].status == "passed"
    assert not rep.has_any_strip


def test_semantic_checks_can_be_disabled(semantic_frame, monkeypatch) -> None:
    """Toggling SEMANTIC_CHECKS_ENABLED off reverts to value-only behavior:
    the magnitude label is no longer caught (the number still traces)."""
    import src.agents.claims as _claims
    monkeypatch.setattr(_claims, "SEMANTIC_CHECKS_ENABLED", False)
    prose = "Revenue reached $6.4B this quarter."
    claim = Claim(text_span="$6.4B", value=6_400_000.0,
                  source=CellLookup({"merchant": "KRG"}, "revenue"))
    rep = validate_claims(prose, [claim], semantic_frame)
    assert rep.claim_dispositions[0].status == "passed"
    assert "6.4B" in rep.prose
