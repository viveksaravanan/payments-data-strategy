"""Per-qid takeaway synthesis — the single source of truth for the
mechanically-computed caption displayed beneath each chart.

Phase 5.1.9 motivation: the chat panel's ``_render_*`` functions and
the specialist agents were independently producing analytical
summaries of the same underlying data, using different windows
(weekly trajectory vs first-half-mean vs second-half-mean), and
disagreeing in prose. This module centralizes the chart's
takeaway computation so that:

  * ``chat.py``'s ``_render_*`` functions call into this module
    and display the result as the chart caption.
  * ``agents.py``'s ``_run_specialist`` calls into this module
    BEFORE dispatching, and injects the takeaway into the
    specialist's input as authoritative ground truth.

Both paths read from the same string, so the prose the agent
produces and the caption the chart displays are guaranteed to
agree by construction.

For qids not covered here, ``compute_takeaway`` returns ``None``
and the dispatch path proceeds without injection (the specialist
operates as before).
"""
from __future__ import annotations

from typing import Callable

from src.dashboard import chart_patterns as CP
from src.dashboard import data as D


# ---------------------------------------------------------------------------
# Per-qid takeaway functions
#
# Each function mirrors the synthesis in ``chat.py::_render_<qid>``
# byte-for-byte (same data helper, same conditional branches, same
# wording). The duplication is intentional — the takeaway string is
# the contract, and a single source of truth eliminates drift between
# the chart caption and the agent's injected context.
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
            f"You're priced {above[0]:.1f}% above {CP.peer_display(above[2])} "
            f"in {above[1]}; {abs(below[0]):.1f}% below "
            f"{CP.peer_display(below[2])} in {below[1]}."
        )
    if above and above[0] > PARITY:
        return (
            f"You're priced above peers across categories; "
            f"widest gap: +{above[0]:.1f}% in {above[1]} "
            f"(vs {CP.peer_display(above[2])})."
        )
    if below and below[0] < -PARITY:
        return (
            f"You're priced below peers across categories; "
            f"widest gap: {below[0]:.1f}% in {below[1]} "
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
    return CP.format_takeaway(
        "You're over-indexed on {top_category} (+{top_pp:.1f}pp vs "
        "peer-average); under-indexed on {bottom_category} "
        "({bottom_pp:.1f}pp).",
        chart_data,
    )


def _takeaway_p2(merchant_id: str, filters: dict | None = None) -> str | None:
    chart_data = D.staple_vs_nonfood_pricing(merchant_id, filters=filters)
    if (not chart_data["panel_a_data"]["categories"]
            and not chart_data["panel_b_data"]["categories"]):
        return None
    return CP.format_takeaway(
        "Your staple tier averages {staple_pct:+.1f}% vs Peer A; "
        "non-food tier averages {nonfood_pct:+.1f}%. "
        "Your pricing strategy is {tier_signal} across tiers.",
        chart_data,
    )


def _takeaway_t_p2(merchant_id: str, filters: dict | None = None) -> str | None:
    chart_data = D.category_unit_price_trends(merchant_id, top_n=6, filters=filters)
    if not chart_data["series"]:
        return None
    return (
        f"{chart_data['top_category']} prices are {chart_data['top_direction']} "
        f"{abs(chart_data['top_pct']):.1f}% over 90 days; next-largest shift "
        f"{chart_data['next_category']} at {chart_data['next_direction']} "
        f"{abs(chart_data['next_pct']):.1f}%."
    )


def _takeaway_t_a2(merchant_id: str, filters: dict | None = None) -> str | None:
    chart_data = D.sku_anomalies(merchant_id, top_n=25, filters=filters)
    if not chart_data["rows"]:
        return None
    n_flag = chart_data["n_flagged"]
    spike  = chart_data["top_spike"]
    drop   = chart_data["top_drop"]
    if n_flag == 0:
        return "No menu items deviate from baseline by >15% in the recent week."
    parts: list[str] = []
    if spike is not None:
        parts.append(
            f"largest spike: {spike['sku_name']} "
            f"({spike['deviation_pct']:+.1f}%)"
        )
    if drop is not None:
        parts.append(
            f"largest drop: {drop['sku_name']} "
            f"({drop['deviation_pct']:+.1f}%)"
        )
    return (
        f"{n_flag} menu item{'s' if n_flag != 1 else ''} deviate from "
        f"baseline by >15%; " + "; ".join(parts) + "."
    )


def _takeaway_share_trajectory(
    merchant_id: str, filters: dict | None = None,
) -> str | None:
    """Shared body for T-D2 and R-D2 (identical synthesis)."""
    chart_data = D.category_share_trajectory(merchant_id, top_n=6, filters=filters)
    if not chart_data["series"]:
        return None
    grow = chart_data["growing_category"]
    grow_pp = chart_data["growing_pp"]
    dec = chart_data["declining_category"]
    dec_pp = chart_data["declining_pp"]
    if grow != "—" and dec != "—":
        return (
            f"{grow} share is up {grow_pp:+.1f}pp over 90 days; "
            f"{dec} is down {dec_pp:+.1f}pp."
        )
    if grow != "—":
        return (
            f"{grow} share is up {grow_pp:+.1f}pp over 90 days; "
            "no category is materially declining."
        )
    if dec != "—":
        return (
            f"{dec} share is down {dec_pp:+.1f}pp over 90 days; "
            "no category is materially growing."
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
            f"{widest['category']} has the widest price spread "
            f"({widest['spread_ratio']:.1f}× from min to max); "
            f"{tightest['category']} is narrowest at "
            f"{tightest['spread_ratio']:.1f}×."
        )
    return "Insufficient category-price data."


def _takeaway_a2(merchant_id: str, filters: dict | None = None) -> str | None:
    chart_data = D.store_anomalies(merchant_id, filters=filters)
    if not chart_data["rows"]:
        return None
    n_flag = chart_data["n_flagged"]
    n_under = chart_data["n_under"]
    n_over  = chart_data["n_over"]
    top     = chart_data["top"]
    peer    = chart_data["peer_signal_for_top"]
    if n_flag == 0:
        return "All your stores are within 15% of your panel baseline."
    if n_under == 0:
        return (
            f"{n_flag} of your stores are running >15% above baseline; "
            f"{top['store_id']} ({top['neighborhood']}) shows the largest "
            f"swing ({top['deviation_pct']:+.1f}%); {peer}."
        )
    if n_over == 0:
        return (
            f"{n_flag} of your stores are running >15% below baseline; "
            f"{top['store_id']} ({top['neighborhood']}) shows the largest "
            f"swing ({top['deviation_pct']:+.1f}%); {peer}."
        )
    return (
        f"{n_flag} stores deviate from baseline by >15% "
        f"({n_under} under, {n_over} over); "
        f"{top['store_id']} ({top['neighborhood']}) shows the largest "
        f"swing ({top['deviation_pct']:+.1f}%); {peer}."
    )


def _takeaway_a3(merchant_id: str, filters: dict | None = None) -> str | None:
    chart_data = D.category_anomalies(merchant_id, filters=filters)
    if not chart_data["rows"]:
        return None
    n_flag = chart_data["n_flagged"]
    top    = chart_data["top"]
    direction = chart_data["top_direction"]
    peer   = chart_data["peer_signal_for_top"]
    if n_flag == 0:
        return (
            "No category-level anomalies in the recent week — every "
            "category is within 15% of your baseline."
        )
    word = "spikes" if direction == "spike" else (
        "drops" if direction == "drop" else "swings"
    )
    return (
        f"{n_flag} categor{'y' if n_flag == 1 else 'ies'} show recent "
        f"volume off baseline by >15%; {top['category']} {word} the "
        f"most ({top['deviation_pct']:+.1f}%); {peer}."
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
            f"{weakest['name']} under-performs by "
            f"{abs(weakest['own_delta_pct']):.1f}%; {signal_phrase}."
        )
    if (strongest and strongest["own_delta_pct"] is not None
            and strongest["own_delta_pct"] > noise):
        return (
            f"All neighborhoods at or above your panel baseline; "
            f"{strongest['name']} leads at +{strongest['own_delta_pct']:.1f}%."
        )
    return (
        "Every neighborhood is within "
        f"{noise:.0f}% of your panel baseline of "
        f"{chart_data['own_baseline']:.0f} txns/store."
    )


def _takeaway_t2(merchant_id: str, filters: dict | None = None) -> str | None:
    chart_data = D.customer_home_density(merchant_id, filters=filters)
    if not chart_data["neighborhoods"]:
        return None
    pct = chart_data["pct_underserved"]
    densest = chart_data["densest_underserved"]
    if densest is not None:
        return (
            f"{pct:.1f}% of your customers live in neighborhoods without a "
            f"same-merchant store; densest under-served area is "
            f"{densest['name']} ({densest['n_customers']} customers)."
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
            f"Top expansion opportunity: {top['name']} "
            f"(score {top['score']:.1f}); {top['peer_n_stores']} peer "
            f"store(s) suggests {signal}."
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
    return CP.format_takeaway(
        "Your UC transactions dropped {own_pct_drop}% from baseline "
        "by week of {trough_week}; peers also declined "
        "({peer_a_pct_drop}% and {peer_b_pct_drop}%). "
        "The pattern is {market_signal}.",
        chart_data,
    )


def _takeaway_d4(merchant_id: str, filters: dict | None = None) -> str | None:
    chart_data = D.category_share_vs_peer_share(merchant_id, filters=filters)
    if not chart_data["points"]:
        return None
    return (
        f"{chart_data['over_category']} overperforms peers by "
        f"{chart_data['over_pp']:+.1f}pp share; "
        f"{chart_data['under_category']} underperforms by "
        f"{chart_data['under_pp']:+.1f}pp."
    )


def _takeaway_t_p1(merchant_id: str, filters: dict | None = None) -> str | None:
    chart_data = D.tbl_daypart_ticket_trends(merchant_id, filters=filters)
    if not chart_data["series"]:
        return None
    top_dp  = chart_data["top_daypart"]
    top_pct = chart_data["top_pct"]
    top_dir = chart_data["top_direction"]
    bot_dp  = chart_data["bottom_daypart"]
    bot_pct = chart_data["bottom_pct"]
    bot_dir = chart_data["bottom_direction"]
    return (
        f"Your {top_dp.lower()} ticket is {top_dir} {abs(top_pct):.1f}% "
        f"over 90 days; {bot_dp.lower()} is {bot_dir} {abs(bot_pct):.1f}%."
    )


def _takeaway_t_p3(merchant_id: str, filters: dict | None = None) -> str | None:
    chart_data = D.per_store_mean_ticket(merchant_id, filters=filters)
    if not chart_data["labels"]:
        return None
    return (
        f"Your highest-ticket store is {chart_data['top_store']} at "
        f"${chart_data['top_value']:.2f}; lowest is {chart_data['bottom_store']} "
        f"at ${chart_data['bottom_value']:.2f}; range is "
        f"${chart_data['range_value']:.2f}."
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
        return "All your stores are within 15% of your panel baseline."
    if n_under == 0:
        return (
            f"{n_flag} of your stores are running >15% above baseline; "
            f"{top['store_id']} ({top['neighborhood']}) shows the largest "
            f"swing ({top['deviation_pct']:+.1f}%)."
        )
    if n_over == 0:
        return (
            f"{n_flag} of your stores are running >15% below baseline; "
            f"{top['store_id']} ({top['neighborhood']}) shows the largest "
            f"swing ({top['deviation_pct']:+.1f}%)."
        )
    return (
        f"{n_flag} stores deviate from baseline by >15% "
        f"({n_under} under, {n_over} over); "
        f"{top['store_id']} ({top['neighborhood']}) shows the largest "
        f"swing ({top['deviation_pct']:+.1f}%)."
    )


def _takeaway_t_a3(merchant_id: str, filters: dict | None = None) -> str | None:
    chart_data = D.day_daypart_heatmap(merchant_id, filters=filters)
    weakest = chart_data["weakest"]
    strongest = chart_data["strongest"]
    if weakest is None or strongest is None:
        return "Insufficient daypart-level data to score this week."
    w_val, w_dow, w_dp = weakest
    s_val, s_dow, s_dp = strongest
    return (
        f"Weakest day-daypart this week: {w_dow} {w_dp.lower()} at "
        f"{w_val * 100:.0f}% of baseline; strongest: {s_dow} "
        f"{s_dp.lower()} at {s_val * 100:.0f}%."
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
        f"Top 3 categories ({names}) account for "
        f"{chart_data['top3_pct']:.1f}% of revenue."
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
    dom_pp = chart_data["dominant_pp"]
    tied = chart_data.get("tied_with") or []
    if not tied:
        return (
            f"Revenue {direction} {abs(change):.1f}% vs your first-4-week "
            f"baseline; {dom_name} contributes {dom_pp:+.1f}pp."
        )
    both_names = [dom_name] + [n.lower() for n, _ in tied]
    pps        = [dom_pp]   + [pp for _, pp in tied]
    names      = " + ".join(both_names)
    magnitudes = " / ".join(f"{pp:+.1f}pp" for pp in pps)
    return (
        f"Revenue {direction} {abs(change):.1f}% vs baseline; "
        f"{names} together drive the change ({magnitudes})."
    )


def _takeaway_r_p1(merchant_id: str, filters: dict | None = None) -> str | None:
    """R-P1: same data helper as T-P2, but framed as 'ticket' not 'price'."""
    chart_data = D.category_unit_price_trends(merchant_id, top_n=6, filters=filters)
    if not chart_data["series"]:
        return None
    return (
        f"{chart_data['top_category']} ticket is "
        f"{chart_data['top_direction']} {abs(chart_data['top_pct']):.1f}% "
        f"over 90 days; {chart_data['next_category']} is "
        f"{chart_data['next_direction']} {abs(chart_data['next_pct']):.1f}%."
    )


def _takeaway_r_p3(merchant_id: str, filters: dict | None = None) -> str | None:
    chart_data = D.ticket_band_distribution(merchant_id, filters=filters)
    if not chart_data["labels"]:
        return None
    return (
        f"Your top ticket band ({chart_data['top_band']}) accounts for "
        f"{chart_data['top_txn_pct']:.1f}% of transactions and "
        f"{chart_data['top_rev_pct']:.1f}% of revenue."
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
            "No categories deviate from baseline by >15% in the recent week."
        )
    word = "spikes" if direction == "spike" else (
        "drops" if direction == "drop" else "swings"
    )
    return (
        f"{n_flag} categor{'y' if n_flag == 1 else 'ies'} show recent "
        f"volume off baseline by >15%; {top['category']} {word} the "
        f"most ({top['deviation_pct']:+.1f}%)."
    )


def _takeaway_r_a3(merchant_id: str, filters: dict | None = None) -> str | None:
    chart_data = D.day_week_heatmap(merchant_id, filters=filters)
    weakest = chart_data["weakest"]
    if weakest is None:
        return "Insufficient day-level data to score this period."
    ratio, dow, week_label = weakest
    return (
        f"Weakest day-week this period: {dow} week of {week_label} "
        f"at {ratio * 100:.0f}% of baseline."
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
