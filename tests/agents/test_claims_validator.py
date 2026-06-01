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
