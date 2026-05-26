"""Per-qid takeaway synthesis — directional framing for chart captions
and specialist injection.

Phase 5.1.9 introduced takeaway injection so the agent and the chart
caption share a narrative. Phase 5.5 regression revealed a second-order
problem: when the takeaway pinned a SPECIFIC percentage or dollar
figure, the agent tried to reproduce that number with its own SQL,
got a slightly different result (different aggregation window), and
spun reconciling — sometimes past MAX_TURNS, sometimes leaking the
reconciliation work into prose, sometimes fabricating evidence
endpoints that back-derive to the takeaway's magnitude.

Phase 5.5.1 resolves this by rewriting takeaways to be DIRECTIONAL:
entity + direction + qualitative magnitude. The takeaway names which
entity matters and which way it's moving, but not the precise
magnitude. The agent's tool calls produce its own numbers; the
agent's prose aligns with the takeaway's narrative (entity, direction)
while citing its own arithmetic in the Evidence bullets. The chart
itself still displays exact numbers visually on bars/lines, so the
user sees specifics — they just don't have to match the prose's
specifics byte-for-byte.

What this is and isn't:
  * It IS narrative coordination (chart and prose tell the same story).
  * It is NOT math unification — chart helpers and the agent's tool
    layer still compute independently. Their numbers may differ by a
    few percentage points. Both are correct under slightly different
    aggregations. v4 documented in V3_AUDIT.md will unify the compute
    layer; v3 ships with the narrative-coordination approach.

For qids not covered here, ``compute_takeaway`` returns ``None`` (or
the pattern-type fallback for D7) and the dispatch path proceeds
accordingly.
"""
from __future__ import annotations

from typing import Callable

from src.dashboard import chart_patterns as CP
from src.dashboard import data as D


# ---------------------------------------------------------------------------
# Per-qid takeaway functions
#
# Phase 5.5.1: each function returns a DIRECTIONAL takeaway — entity
# names + direction words + qualitative magnitude. No specific
# percentages, dollar amounts, or counts that the agent would need to
# reproduce arithmetically. The agent uses its own SQL to produce
# its own numbers in Evidence bullets while aligning to the takeaway's
# named entities and directions.
#
# What stays in the takeaway:
#   * Entity names (BURR, BFAST, University City, KRG-NC-0019)
#   * Direction (up/down, largest/smallest, widest/narrowest,
#     growing/declining, over/under-performs)
#   * Qualitative magnitude (modest / significant / sharp / broad)
#   * Relative ranking ("the only ... that", "the largest ... among")
#
# What does NOT belong in the takeaway:
#   * Specific %s (+4.4%, -13.9%)
#   * Specific $ amounts ($21.78)
#   * Specific counts (3,508 transactions)
#   * Anything the agent would have to reproduce arithmetically
# ---------------------------------------------------------------------------


def _takeaway_p1(merchant_id: str, filters: dict | None = None) -> str | None:
    chart_data = D.category_peer_pricing_gaps(merchant_id, filters=filters)
    if not chart_data["rows"]:
        return None
    PARITY = 0.5
    above = chart_data["max_above"]
    below = chart_data["max_below"]
    if above and below and above[0] > PARITY and below[0] < -PARITY:
        return (
            f"{above[1]} is your widest peer premium "
            f"(vs {CP.peer_display(above[2])}); "
            f"{below[1]} is your largest peer discount "
            f"(vs {CP.peer_display(below[2])})."
        )
    if above and above[0] > PARITY:
        return (
            f"You're priced above peers across categories; "
            f"{above[1]} is the widest peer premium "
            f"(vs {CP.peer_display(above[2])})."
        )
    if below and below[0] < -PARITY:
        return (
            f"You're priced below peers across categories; "
            f"{below[1]} is the largest peer discount "
            f"(vs {CP.peer_display(below[2])})."
        )
    return "Your prices are at or near peer levels across categories."


def _takeaway_p3(merchant_id: str, filters: dict | None = None) -> str | None:
    chart_data = D.category_pricing_leverage(merchant_id, filters=filters)
    if not chart_data["points"]:
        return None
    above = chart_data["above_peer_names"]
    if above:
        names = ", ".join(above)
        return (
            f"Your largest priced-above-peers categories are {names}; "
            f"{chart_data['top_volume_category']} is the highest-volume "
            "opportunity."
        )
    return (
        "You're at or below peer pricing on every category; "
        f"{chart_data['top_volume_category']} is your largest "
        "category by volume."
    )


def _takeaway_d3(merchant_id: str, filters: dict | None = None) -> str | None:
    chart_data = D.basket_mix_vs_peers(merchant_id, filters=filters)
    if not chart_data["categories"]:
        return None
    return (
        f"You're over-indexed on {chart_data['top_category']} vs the "
        f"peer-average basket mix; under-indexed on "
        f"{chart_data['bottom_category']}."
    )


def _takeaway_p2(merchant_id: str, filters: dict | None = None) -> str | None:
    chart_data = D.staple_vs_nonfood_pricing(merchant_id, filters=filters)
    if (not chart_data["panel_a_data"]["categories"]
            and not chart_data["panel_b_data"]["categories"]):
        return None
    # Derive directional signal from the signed pcts without pinning a
    # magnitude. ``tier_signal`` is already qualitative; the per-tier
    # directions add narrative texture.
    def _dir(pct: float) -> str:
        if pct > 0.5:
            return "above"
        if pct < -0.5:
            return "below"
        return "at parity with"
    staple_dir  = _dir(chart_data["staple_pct"])
    nonfood_dir = _dir(chart_data["nonfood_pct"])
    return (
        f"Your staple-tier pricing sits {staple_dir} Peer A; non-food-tier "
        f"sits {nonfood_dir} Peer A. Strategy reads as "
        f"{chart_data['tier_signal']} across tiers."
    )


def _takeaway_t_p2(merchant_id: str, filters: dict | None = None) -> str | None:
    chart_data = D.category_unit_price_trends(merchant_id, top_n=6, filters=filters)
    if not chart_data["series"]:
        return None
    return (
        f"{chart_data['top_category']} prices show the largest "
        f"{chart_data['top_direction']}ward shift over 90 days; "
        f"next-largest shift is {chart_data['next_category']} "
        f"{chart_data['next_direction']}ward."
    )


def _takeaway_t_a2(merchant_id: str, filters: dict | None = None) -> str | None:
    chart_data = D.sku_anomalies(merchant_id, top_n=25, filters=filters)
    if not chart_data["rows"]:
        return None
    n_flag = chart_data["n_flagged"]
    spike  = chart_data["top_spike"]
    drop   = chart_data["top_drop"]
    if n_flag == 0:
        return "No menu items deviate from baseline materially in the recent week."
    parts: list[str] = []
    if spike is not None:
        parts.append(f"largest spike: {spike['sku_name']}")
    if drop is not None:
        parts.append(f"largest drop: {drop['sku_name']}")
    return (
        "Multiple menu items deviate from baseline; "
        + "; ".join(parts) + "."
    )


def _takeaway_share_trajectory(
    merchant_id: str, filters: dict | None = None,
) -> str | None:
    """Shared body for T-D2 and R-D2 (identical synthesis)."""
    chart_data = D.category_share_trajectory(merchant_id, top_n=6, filters=filters)
    if not chart_data["series"]:
        return None
    grow = chart_data["growing_category"]
    dec = chart_data["declining_category"]
    if grow != "—" and dec != "—":
        return (
            f"{grow} share is gaining over 90 days; {dec} is the "
            f"largest declining category."
        )
    if grow != "—":
        return (
            f"{grow} share is gaining over 90 days; no category is "
            "materially declining."
        )
    if dec != "—":
        return (
            f"{dec} share is declining over 90 days; no category is "
            "materially growing."
        )
    return "Category shares are flat across the 90-day window."


def _takeaway_r_p2(merchant_id: str, filters: dict | None = None) -> str | None:
    chart_data = D.category_price_spread(merchant_id, filters=filters)
    if not chart_data["rows"]:
        return None
    widest   = chart_data["widest"]
    tightest = chart_data["tightest"]
    if widest and tightest:
        return (
            f"{widest['category']} has the widest min-to-max price spread; "
            f"{tightest['category']} is the narrowest."
        )
    return "Insufficient category-price data."


def _takeaway_a2(merchant_id: str, filters: dict | None = None) -> str | None:
    chart_data = D.store_anomalies(merchant_id, filters=filters)
    if not chart_data["rows"]:
        return None
    n_flag  = chart_data["n_flagged"]
    n_under = chart_data["n_under"]
    n_over  = chart_data["n_over"]
    top     = chart_data["top"]
    peer    = chart_data["peer_signal_for_top"]
    if n_flag == 0:
        return "All your stores are running close to your panel baseline."
    if n_under == 0:
        return (
            "Multiple stores are running materially above baseline; "
            f"{top['store_id']} ({top['neighborhood']}) shows the "
            f"largest swing; {peer}."
        )
    if n_over == 0:
        return (
            "Multiple stores are running materially below baseline; "
            f"{top['store_id']} ({top['neighborhood']}) shows the "
            f"largest swing; {peer}."
        )
    return (
        "Multiple stores deviate from baseline in both directions; "
        f"{top['store_id']} ({top['neighborhood']}) shows the largest "
        f"swing; {peer}."
    )


def _takeaway_a3(merchant_id: str, filters: dict | None = None) -> str | None:
    chart_data = D.category_anomalies(merchant_id, filters=filters)
    if not chart_data["rows"]:
        return None
    n_flag    = chart_data["n_flagged"]
    top       = chart_data["top"]
    direction = chart_data["top_direction"]
    peer      = chart_data["peer_signal_for_top"]
    if n_flag == 0:
        return (
            "No category-level anomalies in the recent week — every "
            "category is close to your baseline."
        )
    word = "spikes" if direction == "spike" else (
        "drops" if direction == "drop" else "swings"
    )
    return (
        f"Categories show recent volume off baseline; "
        f"{top['category']} {word} the most; {peer}."
    )


def _takeaway_t1(merchant_id: str, filters: dict | None = None) -> str | None:
    chart_data = D.neighborhood_performance(merchant_id, filters=filters)
    if not chart_data["neighborhoods"]:
        return None
    weakest   = chart_data["weakest"]
    strongest = chart_data["strongest"]
    noise = 5.0
    if (weakest and weakest["own_delta_pct"] is not None
            and weakest["own_delta_pct"] < -noise):
        signal = weakest["peer_signal"]
        signal_phrase = {
            "market-wide":   "peers co-decline; suggests market-wide",
            "operational":   "peers stable; suggests operational",
            "market-wide (positive)": "peers also above; market-wide",
            "operational (positive)": "peers stable; operational lift",
            "on baseline":   "peer signal flat",
            "limited peer footprint": "limited peer footprint for signal",
            "limited own footprint":  "limited own footprint",
        }.get(signal, signal)
        return (
            f"{weakest['name']} shows the largest deviation below your "
            f"chain baseline; {signal_phrase}."
        )
    if (strongest and strongest["own_delta_pct"] is not None
            and strongest["own_delta_pct"] > noise):
        return (
            "All neighborhoods at or above your panel baseline; "
            f"{strongest['name']} leads."
        )
    return "Every neighborhood is close to your panel baseline."


def _takeaway_t2(merchant_id: str, filters: dict | None = None) -> str | None:
    chart_data = D.customer_home_density(merchant_id, filters=filters)
    if not chart_data["neighborhoods"]:
        return None
    densest = chart_data["densest_underserved"]
    if densest is not None:
        return (
            "Some of your customers live in neighborhoods without a "
            f"same-merchant store; the densest under-served area is "
            f"{densest['name']}."
        )
    return (
        "Every neighborhood with your customers also has at least one "
        "of your stores — no under-served neighborhoods in the panel."
    )


def _takeaway_t4(merchant_id: str, filters: dict | None = None) -> str | None:
    chart_data = D.expansion_opportunity(merchant_id, filters=filters)
    if not chart_data["neighborhoods"]:
        return None
    top    = chart_data["top"]
    signal = chart_data["top_peer_signal"]
    if top:
        return (
            f"Top expansion opportunity: {top['name']}; peer presence "
            f"there suggests {signal}."
        )
    return "No scored neighborhoods in the panel."


# ---------------------------------------------------------------------------
# Phase 5.2 — coverage extension for the remaining qids in
# QUESTION_RENDERERS. Each function mirrors the synthesis in
# chat.py::_render_<qid> byte-for-byte so the chart caption and
# the agent's injected ground truth remain a single source of truth.
# ---------------------------------------------------------------------------


def _takeaway_a1(merchant_id: str, filters: dict | None = None) -> str | None:
    chart_data = D.uc_decline_trajectory(merchant_id, filters=filters)
    if not chart_data.get("weeks"):
        return None
    if not chart_data.get("trough_week"):
        return (
            "You have no University City stores in the panel — "
            "showing peer trajectories only."
        )
    return (
        f"Your University City transactions declined through the panel "
        f"window with a trough around week of {chart_data['trough_week']}; "
        f"peers also declined. The pattern reads as "
        f"{chart_data['market_signal']}."
    )


def _takeaway_d4(merchant_id: str, filters: dict | None = None) -> str | None:
    chart_data = D.category_share_vs_peer_share(merchant_id, filters=filters)
    if not chart_data["points"]:
        return None
    return (
        f"{chart_data['over_category']} overperforms peers in share; "
        f"{chart_data['under_category']} underperforms."
    )


def _takeaway_t_p1(merchant_id: str, filters: dict | None = None) -> str | None:
    """T-P1 daypart ticket trend takeaway — DIRECTIONAL only.

    Phase 5.2.1 fix: the chart helper computes drift as a
    mean-of-means at hour granularity (each hour weighted equally
    regardless of transaction count) and reports first-non-None vs
    last-non-None percentages. The agent's natural SQL is
    transaction-weighted ``AVG(txn_total) GROUP BY daypart``, which
    produces numerically different drift values. When the takeaway
    asserted a specific magnitude (e.g., "+4.4%"), the agent either
    spun trying to reconcile (hit MAX_TURNS) or fabricated evidence
    numbers to back-derive the takeaway's percentages. Both
    failures observed in Phase 5.2 smoke.

    Resolution: name the entities and directions (which the chart
    visualization shows clearly) without pinning a specific
    magnitude the agent must reproduce. Agent Evidence can cite
    per-daypart ticket figures from its own SQL without
    reconciliation. The chart itself still shows the precise
    trajectory via series lines; the takeaway becomes a
    direction-only summary."""
    chart_data = D.tbl_daypart_ticket_trends(merchant_id, filters=filters)
    if not chart_data["series"]:
        return None
    top_dp  = chart_data["top_daypart"]
    top_dir = chart_data["top_direction"]
    bot_dp  = chart_data["bottom_daypart"]
    bot_dir = chart_data["bottom_direction"]
    if top_dp == "—" or bot_dp == "—":
        return None
    return (
        f"Among QSR dayparts over 90 days, {top_dp.lower()} ticket "
        f"shows the largest {top_dir}ward drift; "
        f"{bot_dp.lower()} shows the largest {bot_dir}ward drift."
    )


def _takeaway_t_p3(merchant_id: str, filters: dict | None = None) -> str | None:
    chart_data = D.per_store_mean_ticket(merchant_id, filters=filters)
    if not chart_data["labels"]:
        return None
    return (
        f"Your highest-ticket store is {chart_data['top_store']}; "
        f"lowest is {chart_data['bottom_store']}."
    )


def _takeaway_store_anomalies_own_only(
    merchant_id: str, filters: dict | None = None,
) -> str | None:
    """Shared body for T-A1 and R-A1 (identical helper, identical phrasing)."""
    chart_data = D.store_anomalies_own_only(merchant_id, filters=filters)
    if not chart_data["rows"]:
        return None
    n_flag = chart_data["n_flagged"]
    n_under = chart_data["n_under"]
    n_over  = chart_data["n_over"]
    top     = chart_data["top"]
    if n_flag == 0:
        return "All your stores are running close to your panel baseline."
    if n_under == 0:
        return (
            "Multiple stores are running materially above baseline; "
            f"{top['store_id']} ({top['neighborhood']}) shows the "
            "largest swing."
        )
    if n_over == 0:
        return (
            "Multiple stores are running materially below baseline; "
            f"{top['store_id']} ({top['neighborhood']}) shows the "
            "largest swing."
        )
    return (
        "Multiple stores deviate from baseline in both directions; "
        f"{top['store_id']} ({top['neighborhood']}) shows the largest "
        "swing."
    )


def _takeaway_t_a3(merchant_id: str, filters: dict | None = None) -> str | None:
    chart_data = D.day_daypart_heatmap(merchant_id, filters=filters)
    weakest = chart_data["weakest"]
    strongest = chart_data["strongest"]
    if weakest is None or strongest is None:
        return "Insufficient daypart-level data to score this week."
    _, w_dow, w_dp = weakest
    _, s_dow, s_dp = strongest
    return (
        f"Weakest day-daypart this week: {w_dow} {w_dp.lower()}; "
        f"strongest: {s_dow} {s_dp.lower()}."
    )


def _takeaway_category_share_own(
    merchant_id: str, filters: dict | None = None,
) -> str | None:
    """Shared body for T-D1 and R-D1 (both call category_share_own)."""
    chart_data = D.category_share_own(merchant_id, top_n=8, filters=filters)
    if not chart_data["labels"]:
        return None
    names = ", ".join(chart_data["top3_names"])
    return (
        f"Top 3 categories ({names}) dominate your revenue mix."
    )


def _takeaway_revenue_change_own(
    merchant_id: str, filters: dict | None = None,
) -> str | None:
    """Shared body for T-D3 and R-D3 (both call revenue_change_decomposition_own)."""
    chart_data = D.revenue_change_decomposition_own(merchant_id, filters=filters)
    if not chart_data.get("has_data"):
        return None
    change = chart_data["total_change_pct"]
    direction = "up" if change > 0 else ("down" if change < 0 else "flat")
    dom_name = chart_data["dominant_driver"].lower()
    tied = chart_data.get("tied_with") or []
    if not tied:
        return (
            f"Revenue is {direction} vs your first-4-week baseline; "
            f"{dom_name} is the dominant driver."
        )
    both_names = [dom_name] + [n.lower() for n, _ in tied]
    names = " + ".join(both_names)
    return (
        f"Revenue is {direction} vs baseline; {names} together drive "
        "the change."
    )


def _takeaway_r_p1(merchant_id: str, filters: dict | None = None) -> str | None:
    """R-P1: same data helper as T-P2, but framed as 'ticket' not 'price'."""
    chart_data = D.category_unit_price_trends(merchant_id, top_n=6, filters=filters)
    if not chart_data["series"]:
        return None
    return (
        f"{chart_data['top_category']} ticket shows the largest "
        f"{chart_data['top_direction']}ward shift over 90 days; "
        f"next-largest shift is {chart_data['next_category']} "
        f"{chart_data['next_direction']}ward."
    )


def _takeaway_r_p3(merchant_id: str, filters: dict | None = None) -> str | None:
    chart_data = D.ticket_band_distribution(merchant_id, filters=filters)
    if not chart_data["labels"]:
        return None
    return (
        f"Your top ticket band is {chart_data['top_band']}, accounting "
        "for the bulk of transactions and revenue."
    )


def _takeaway_r_a2(merchant_id: str, filters: dict | None = None) -> str | None:
    """R-A2: same helper as A3 but without the peer signal phrase."""
    chart_data = D.category_anomalies(merchant_id, filters=filters)
    if not chart_data["rows"]:
        return None
    n_flag = chart_data["n_flagged"]
    top    = chart_data["top"]
    direction = chart_data["top_direction"]
    if n_flag == 0:
        return (
            "No categories deviate materially from baseline in the recent week."
        )
    word = "spikes" if direction == "spike" else (
        "drops" if direction == "drop" else "swings"
    )
    return (
        f"Categories show recent volume off baseline; "
        f"{top['category']} {word} the most."
    )


def _takeaway_r_a3(merchant_id: str, filters: dict | None = None) -> str | None:
    chart_data = D.day_week_heatmap(merchant_id, filters=filters)
    weakest = chart_data["weakest"]
    if weakest is None:
        return "Insufficient day-level data to score this period."
    _, dow, week_label = weakest
    return (
        f"Weakest day-week this period: {dow} week of {week_label}."
    )


# ---------------------------------------------------------------------------
# Registry — qid → takeaway function
# ---------------------------------------------------------------------------

# Coverage: every qid in ``chat.py::QUESTION_RENDERERS``. Phase 5.1.9
# wired the original 13; Phase 5.2 extended to the remaining qids
# plus added a pattern-type fallback for D7 (which renders two
# waterfalls and can't be summarized in a single sentence).
_REGISTRY: dict[str, Callable[..., str | None]] = {
    "A1":   _takeaway_a1,
    "A2":   _takeaway_a2,
    "A3":   _takeaway_a3,
    "D3":   _takeaway_d3,
    "D4":   _takeaway_d4,
    "P1":   _takeaway_p1,
    "P2":   _takeaway_p2,
    "P3":   _takeaway_p3,
    "R-A1": _takeaway_store_anomalies_own_only,
    "R-A2": _takeaway_r_a2,
    "R-A3": _takeaway_r_a3,
    "R-D1": _takeaway_category_share_own,
    "R-D2": _takeaway_share_trajectory,
    "R-D3": _takeaway_revenue_change_own,
    "R-P1": _takeaway_r_p1,
    "R-P2": _takeaway_r_p2,
    "R-P3": _takeaway_r_p3,
    "T-A1": _takeaway_store_anomalies_own_only,
    "T-A2": _takeaway_t_a2,
    "T-A3": _takeaway_t_a3,
    "T-D1": _takeaway_category_share_own,
    "T-D2": _takeaway_share_trajectory,
    "T-D3": _takeaway_revenue_change_own,
    "T-P1": _takeaway_t_p1,
    "T-P2": _takeaway_t_p2,
    "T-P3": _takeaway_t_p3,
    "T1":   _takeaway_t1,
    "T2":   _takeaway_t2,
    "T4":   _takeaway_t4,
}


# Pattern-type fallback registry: qid → (pattern_type, brief_description).
# Used when a full takeaway isn't computable but pattern context still
# helps the specialist frame its response. Currently a single entry
# for D7 which renders two waterfalls (one per peer) — no single
# sentence captures both.
_PATTERN_FALLBACKS: dict[str, tuple[str, str]] = {
    "D7": (
        "pair of waterfalls",
        "the revenue-gap decomposition vs each same-segment peer "
        "(peer_a + peer_b), with bars for Stores, Traffic/store, "
        "Basket, Ticket, Mix, and Residual drivers",
    ),
}


# Sentinel substring used by callers to detect "this is a pattern
# fallback, not a numeric takeaway." The injection prompt in
# ``agents.py`` softens its language when this string is present.
PATTERN_FALLBACK_MARKER = "chart that will render below your response is"


def _pattern_fallback(qid: str) -> str | None:
    """Brief pattern descriptor for qids without full takeaways.

    Returns a single sentence describing the chart shape. The
    specialist gets pattern context even when no single-sentence
    takeaway exists. Returns ``None`` for qids not registered."""
    entry = _PATTERN_FALLBACKS.get(qid)
    if entry is None:
        return None
    pattern_type, description = entry
    return (
        f"The {PATTERN_FALLBACK_MARKER} a {pattern_type} showing "
        f"{description}. Frame your response around what this chart "
        f"shape highlights."
    )


def compute_takeaway(
    qid: str | None,
    merchant_id: str,
    *,
    filters: dict | None = None,
) -> str | None:
    """Return the mechanically-computed takeaway string for ``qid``,
    or ``None`` if the qid is unknown / has no chart helper / the
    chart helper returned empty data.

    Tries the full-takeaway registry first; falls back to a brief
    pattern-type descriptor if a full takeaway isn't computable.

    Used by both ``chat.py`` (display as the chart caption) and
    ``agents.py`` (inject as ground truth / context into the
    specialist's input)."""
    if not qid:
        return None
    fn = _REGISTRY.get(qid)
    if fn is not None:
        try:
            result = fn(merchant_id, filters=filters)
        except Exception:  # noqa: BLE001 — takeaway failures must not break dispatch
            result = None
        if result is not None:
            return result
    # Full takeaway unavailable — try pattern-type fallback.
    return _pattern_fallback(qid)


def is_pattern_fallback(takeaway: str | None) -> bool:
    """True when the given takeaway is a pattern-type fallback rather
    than a numeric authoritative takeaway. Used by ``agents.py`` to
    pick the softer injection prompt language."""
    return takeaway is not None and PATTERN_FALLBACK_MARKER in takeaway
