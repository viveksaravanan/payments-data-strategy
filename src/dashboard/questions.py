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
            # The two flagship both-directions questions. Each resolves
            # deterministically off the sortable own-vs-peer gap query (own
            # from `self` rows, peer from `'peer'` rows, ORDER BY gap with a
            # total tiebreak) → the furthest-gap subcategory is a row the
            # agent reads off, then drills to named own products. Same answer
            # every run (temp 0 + total order). See docs/AGENT_QUALITY_STANDARD.md.
            {
                "id":      "P1",
                "text":    "Which subcategory am I priced furthest below peer grocers on — and which of my products drive it?",
                "pattern": "pattern_2_comparison",
            },
            {
                "id":      "P2",
                "text":    "Which subcategory am I priced furthest above peer grocers on — and which of my products drive it?",
                "pattern": "pattern_2_comparison",
            },
            {
                # P3 is the decision layer that P1/P2 (descriptive, both
                # directions) set up: it forces the full gate stack — rank the
                # below-peer gaps, screen out known-value items, confirm it's
                # price not mix, check per-store volume, size the prize — and
                # hand back the shortlist worth acting on. This is the agent's
                # differentiator (earning the recommendation), not another
                # furthest-gap readout.
                "id":      "P3",
                "text":    "Where's my best opportunity to raise price without losing traffic — a real gap that isn't a known-value staple?",
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
            {
                "id":      "D3",
                "text":    "What does my basket-mix look like compared to peers? Where am I over or under indexed?",
                "pattern": "pattern_2_comparison",
            },
            {
                "id":      "D4",
                "text":    "Which categories over- or under-perform vs peers given my mix?",
                "pattern": "pattern_4_scatter",
            },
            {
                "id":      "D7",
                "text":    "What's driving my revenue gap vs peers this period?",
                "pattern": "pattern_5_waterfall",
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
            {"id": "T-P4", "text": "How do my menu prices compare to peer QSR chains across categories?",             "pattern": "pattern_3_heatmap"},
            {"id": "T-P1", "text": "How is my average ticket trending across dayparts?",                                "pattern": "pattern_1_time_series"},
            {"id": "T-P2", "text": "Which menu categories have shifted in price over the last 90 days?",               "pattern": "pattern_1_time_series"},
        ],
        "anomaly": [
            {"id": "T-A4", "text": "Which of my stores or dayparts are dropping — are peers seeing the same decline?", "pattern": "pattern_1_time_series"},
            {"id": "T-A1", "text": "Which of my stores has unusual traffic this week?",                                "pattern": "pattern_9_table"},
            {"id": "T-A2", "text": "Are any menu items spiking or dropping unusually?",                                "pattern": "pattern_9_table"},
        ],
        "demand": [
            {"id": "T-D4", "text": "How does my menu mix compare to peer chains — where am I over- or under-indexed?", "pattern": "pattern_2_comparison"},
            {"id": "T-D2", "text": "Which categories are gaining or losing share over time?",                          "pattern": "pattern_1_time_series"},
            {"id": "T-D3", "text": "What's driving my revenue change this week — traffic, ticket, or mix?",            "pattern": "pattern_5_waterfall"},
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
