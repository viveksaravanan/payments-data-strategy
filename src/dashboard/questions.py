"""Question registry for the v3 dashboard's chat panel.

Maps (merchant_segment, specialist) → list of 3 suggested questions.
Each question has an ID matching docs/archive/V3_QUESTIONS.md, the question text
shown to the merchant, and the chart pattern the agent's response
should render against (see chart_patterns.md).
"""
from __future__ import annotations

QUESTIONS: dict[str, dict[str, list[dict]]] = {
    "GROCER": {
        "pricing": [
            # Three complementary shapes, all off the one sortable own-vs-peer
            # gap query (own from `self` rows, peer from `'peer'` rows, per-store
            # normalized). P1 = a two-directional POSITIONING scan; P2 = the
            # DECISION layer (the full gate stack); P3 = a named-subcategory
            # LOOKUP. All three validated live ground-truth-first (temp 0).
            {
                # Two-directional positioning scan: the agent reads the gap
                # ranking from BOTH ends (most below-peer AND most above-peer)
                # in one answer, and calls out "well-aligned, nothing stands
                # out" when the widest gap is small (the WDX ~+2.7% case) rather
                # than dressing a trivial gap up as a finding.
                "id":      "P1",
                "text":    "Where is my pricing out of line with peer grocers — too high or too low?",
                "pattern": "pattern_2_comparison",
            },
            {
                # The decision layer: forces the full gate stack — rank the
                # below-peer gaps, check per-store volume, screen out known-value
                # items, confirm price-not-mix, size the prize. When no gap is
                # both below-peer AND under-selling per store (the KRG case —
                # every low price is winning traffic), the honest answer names
                # the best *tested* raise candidate, it does not fabricate a
                # free-margin category or return a bare null.
                "id":      "P2",
                "text":    "Where can I raise price without losing traffic?",
                "pattern": "pattern_2_comparison",
            },
            {
                # Named-subcategory lookup. The named subcategory (2% Reduced-Fat
                # Milk) is a Known-Value Item, so this pill uniquely exercises the
                # KVI gate: a below-peer price on a price-checked staple reads as a
                # deliberate traffic driver ("hold on purpose"), not a margin
                # opportunity. Then it drills to the viewer's own milk SKUs. The
                # agent filters the gap query to the named subcategory (it does not
                # rank-and-drift) — validated live.
                "id":      "P3",
                "text":    "How does 2% Reduced-Fat Milk pricing compare to peers, and which of my products should I look at?",
                "pattern": "pattern_2_comparison",
            },
        ],
        "anomaly": [
            {
                "id":      "A1",
                "text":    "Why is University City declining? Are peers seeing the same drop?",
                "pattern": "pattern_1_time_series",
            },
            {
                "id":      "A2",
                "text":    "Which of my stores show abnormal traffic recently?",
                "pattern": "pattern_9_table",
            },
            {
                "id":      "A3",
                "text":    "Which SKUs or categories are spiking or dropping unusually?",
                "pattern": "pattern_9_table",
            },
        ],
        "demand": [
            # Own-data demand-planning trio (timing / velocity / affinity),
            # grounded in signals the data actually carries. The old peer-mix /
            # revenue-gap pills echoed pricing and (T-D2-style) leaned on a flat
            # within-window trend. See docs/AGENT_QUALITY_STANDARD.md.
            {
                "id":      "GD1",
                "text":    "When is demand highest — which days and dayparts should I stock and staff around?",
                "pattern": "pattern_1_time_series",
            },
            {
                "id":      "GD2",
                "text":    "Which categories and items are my fastest and slowest movers — what should I stock up on versus mark down?",
                "pattern": "pattern_2_comparison",
            },
            {
                "id":      "GD3",
                "text":    "What reliably sells together in my baskets — where can I cross-merchandise or bundle?",
                "pattern": "pattern_2_comparison",
            },
        ],
        "trade": [
            {
                "id":      "T1",
                "text":    "Which of my neighborhoods are over- or under-performing?",
                "pattern": "pattern_6_map",
            },
            {
                "id":      "T2",
                "text":    "Which neighborhoods do my customers shop from most?",
                "pattern": "pattern_6_map",
            },
            {
                "id":      "T4",
                "text":    "Which neighborhoods show the biggest expansion opportunity?",
                "pattern": "pattern_6_map",
            },
        ],
    },

    "QSR": {  # TBL / BKG / CFA — shared, chain-generic QSR pill set.
              # datamodel-v2: each QSR banner now has 2 same-segment
              # peers, so pricing / demand / anomaly lead with a peer
              # comparison (parity with the grocer set); the old
              # own-only framing dated to when TBL was the lone QSR.
        "pricing": [
            # Mirrors the grocer three-shape set (P1/P2/P3), QSR-worded: the same
            # pricing agent + sortable own-vs-peer gap query + product drill.
            # QP1 = two-directional positioning scan; QP2 = the raise-decision
            # layer; QP3 = a named-subcategory lookup. QSR has no grocery
            # known-value-item concept, so QP3's named lookup naturally exercises
            # the price-vs-per-store-volume quadrant read instead of the KVI gate.
            # QP3 names **Fries** — a verified-comparable subcategory (a fried
            # potato side is genuinely the same item at all three banners, so the
            # gap is real, not a taxonomy artifact). It gives a strong contrast:
            # BKG cheaper-and-under-selling (margin on the table) vs CFA a premium
            # that's still winning (hold). NOTE: some functional QSR subcategories
            # (Wrap/Handheld, Chicken Nuggets, Combo Meal, Potatoes) lump
            # non-comparable items across banners and can produce inflated gaps in
            # the QP1/QP2 scan — flagged for a taxonomy-comparability audit.
            {"id": "QP1", "text": "Where is my menu pricing out of line with peer chains — too high or too low?",                             "pattern": "pattern_2_comparison"},
            {"id": "QP2", "text": "Where can I raise price without losing traffic?",                                                          "pattern": "pattern_2_comparison"},
            {"id": "QP3", "text": "How do my Fries prices compare to peers, and which items should I look at?",                               "pattern": "pattern_2_comparison"},
        ],
        "anomaly": [
            {"id": "T-A4", "text": "Which of my stores or dayparts are dropping — are peers seeing the same decline?", "pattern": "pattern_1_time_series"},
            {"id": "T-A1", "text": "Which of my stores has unusual traffic this week?",                                "pattern": "pattern_9_table"},
            {"id": "T-A2", "text": "Are any menu items spiking or dropping unusually?",                                "pattern": "pattern_9_table"},
        ],
        "demand": [
            # Own-data demand-planning trio, QSR-worded (timing leads on dayparts;
            # CFA Sunday-closed surfaces in D1). Replaces the peer-mix pill, the
            # dead over-time-trend pill, and the "this week" revenue decomposition.
            {"id": "QD1", "text": "When is demand highest across the day and week — which dayparts should I plan around?", "pattern": "pattern_1_time_series"},
            {"id": "QD2", "text": "Which menu items are my fastest and slowest movers right now?",                        "pattern": "pattern_2_comparison"},
            {"id": "QD3", "text": "Which items sell together, so I can build combos or upsell them?",                     "pattern": "pattern_2_comparison"},
        ],
        "trade": [
            # Shared with the grocer set — map-based, segment-agnostic.
            {"id": "T1", "text": "Which of my neighborhoods are over- or under-performing?",                           "pattern": "pattern_6_map"},
            {"id": "T2", "text": "Which neighborhoods do my customers shop from most?",                                 "pattern": "pattern_6_map"},
            {"id": "T4", "text": "Which neighborhoods show the biggest expansion opportunity?",                        "pattern": "pattern_6_map"},
        ],
    },

    # (RETAIL/TJX block removed in datamodel-v2 — off-price dropped.)
}


_GROCERS = {"KRG", "ACM", "WDX"}
# datamodel-v2: all three QSR banners share the QSR pill set (chain-generic).
_QSR     = {"TBL", "BKG", "CFA"}


def segment_for_merchant(merchant_id: str) -> str:
    """Return 'GROCER' / 'QSR' for a panel merchant_id.

    KRG, ACM, WDX → GROCER. TBL, BKG, CFA → QSR.
    Raises ValueError on unknown merchant_id.
    """
    if merchant_id in _GROCERS:
        return "GROCER"
    if merchant_id in _QSR:
        return "QSR"
    raise ValueError(f"Unknown merchant_id: {merchant_id!r}")


def questions_for(merchant_id: str, specialist: str) -> list[dict]:
    """Return the 3 suggested questions for (merchant, specialist).

    `specialist` is one of: 'pricing', 'anomaly', 'demand', 'trade'.
    Each returned dict has keys: 'id', 'text', 'pattern'.
    """
    segment = segment_for_merchant(merchant_id)
    return QUESTIONS[segment][specialist]
