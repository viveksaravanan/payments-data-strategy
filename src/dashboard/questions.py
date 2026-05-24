"""Question registry for the v3 dashboard's chat panel.

Maps (merchant_segment, specialist) → list of 3 suggested questions.
Each question has an ID matching V3_QUESTIONS.md, the question text
shown to the merchant, and the chart pattern the agent's response
should render against (see chart_patterns.md).
"""
from __future__ import annotations

QUESTIONS: dict[str, dict[str, list[dict]]] = {
    "GROCER": {
        "pricing": [
            {
                "id":      "P1",
                "text":    "How do my prices compare to peer grocers across categories?",
                "pattern": "pattern_3_heatmap",
            },
            {
                "id":      "P2",
                "text":    "How does my pricing position compare across my staple vs non-food categories?",
                "pattern": "pattern_2_comparison",
            },
            {
                "id":      "P3",
                "text":    "Which categories show the biggest pricing-leverage opportunity?",
                "pattern": "pattern_4_scatter",
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
                "text":    "Where do my customers live relative to my stores?",
                "pattern": "pattern_6_map",
            },
            {
                "id":      "T4",
                "text":    "Which neighborhoods show the biggest expansion opportunity?",
                "pattern": "pattern_6_map",
            },
        ],
    },

    "QSR": {  # TBL
        "pricing": [
            {"id": "T-P1", "text": "How is my average ticket trending across dayparts?",                                "pattern": "pattern_1_time_series"},
            {"id": "T-P2", "text": "Which menu categories have shifted in price over the last 90 days?",               "pattern": "pattern_1_time_series"},
            {"id": "T-P3", "text": "What's my price distribution across stores? Are any outliers?",                    "pattern": "pattern_2_comparison"},
        ],
        "anomaly": [
            {"id": "T-A1", "text": "Which of my stores has unusual traffic this week?",                                "pattern": "pattern_9_table"},
            {"id": "T-A2", "text": "Are any menu items spiking or dropping unusually?",                                "pattern": "pattern_9_table"},
            {"id": "T-A3", "text": "Which dayparts are running below my own baseline?",                                "pattern": "pattern_3_heatmap"},
        ],
        "demand": [
            {"id": "T-D1", "text": "What does my menu mix look like? Where am I most concentrated?",                   "pattern": "pattern_2_comparison"},
            {"id": "T-D2", "text": "Which categories are gaining or losing share over time?",                          "pattern": "pattern_1_time_series"},
            {"id": "T-D3", "text": "What's driving my revenue change this week — traffic, ticket, or mix?",            "pattern": "pattern_5_waterfall"},
        ],
        "trade": [
            # TBL reuses T1/T2/T4 from the grocer set verbatim.
            {"id": "T1", "text": "Which of my neighborhoods are over- or under-performing?",                           "pattern": "pattern_6_map"},
            {"id": "T2", "text": "Where do my customers live relative to my stores?",                                  "pattern": "pattern_6_map"},
            {"id": "T4", "text": "Which neighborhoods show the biggest expansion opportunity?",                        "pattern": "pattern_6_map"},
        ],
    },

    "RETAIL": {  # TJX
        "pricing": [
            {"id": "R-P1", "text": "How is my average ticket trending across categories?",                             "pattern": "pattern_1_time_series"},
            {"id": "R-P2", "text": "Which categories have the widest price spread within them?",                       "pattern": "pattern_9_table"},
            {"id": "R-P3", "text": "What's my high-ticket vs low-ticket transaction split?",                           "pattern": "pattern_2_comparison"},
        ],
        "anomaly": [
            {"id": "R-A1", "text": "Which of my stores has unusual traffic this week?",                                "pattern": "pattern_9_table"},
            {"id": "R-A2", "text": "Are any categories spiking or dropping unusually?",                                "pattern": "pattern_9_table"},
            {"id": "R-A3", "text": "Which days of the week are running below my baseline?",                            "pattern": "pattern_3_heatmap"},
        ],
        "demand": [
            {"id": "R-D1", "text": "What does my category mix look like?",                                             "pattern": "pattern_2_comparison"},
            {"id": "R-D2", "text": "Which categories are gaining or losing share over time?",                          "pattern": "pattern_1_time_series"},
            {"id": "R-D3", "text": "What's driving my revenue change this week?",                                      "pattern": "pattern_5_waterfall"},
        ],
        "trade": [
            # TJX reuses T1/T2/T4 from the grocer set verbatim.
            {"id": "T1", "text": "Which of my neighborhoods are over- or under-performing?",                           "pattern": "pattern_6_map"},
            {"id": "T2", "text": "Where do my customers live relative to my stores?",                                  "pattern": "pattern_6_map"},
            {"id": "T4", "text": "Which neighborhoods show the biggest expansion opportunity?",                        "pattern": "pattern_6_map"},
        ],
    },
}


_GROCERS = {"KRG", "ACM", "WDX"}
_QSR     = {"TBL"}
_RETAIL  = {"TJX"}


def segment_for_merchant(merchant_id: str) -> str:
    """Return 'GROCER' / 'QSR' / 'RETAIL' for a panel merchant_id.

    KRG, ACM, WDX → GROCER. TBL → QSR. TJX → RETAIL.
    Raises ValueError on unknown merchant_id.
    """
    if merchant_id in _GROCERS:
        return "GROCER"
    if merchant_id in _QSR:
        return "QSR"
    if merchant_id in _RETAIL:
        return "RETAIL"
    raise ValueError(f"Unknown merchant_id: {merchant_id!r}")


def questions_for(merchant_id: str, specialist: str) -> list[dict]:
    """Return the 3 suggested questions for (merchant, specialist).

    `specialist` is one of: 'pricing', 'anomaly', 'demand', 'trade'.
    Each returned dict has keys: 'id', 'text', 'pattern'.
    """
    segment = segment_for_merchant(merchant_id)
    return QUESTIONS[segment][specialist]
