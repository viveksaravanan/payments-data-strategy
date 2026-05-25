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
# Registry — qid → takeaway function
# ---------------------------------------------------------------------------

# Phase 5.1.9 coverage: the 12 baseline cassettes + T-P2 (the failing
# case that motivated the architectural fix). Other qids fall back
# to ``None`` and the specialist proceeds without takeaway injection.
_REGISTRY: dict[str, Callable[..., str | None]] = {
    "P1":   _takeaway_p1,
    "P3":   _takeaway_p3,
    "D3":   _takeaway_d3,
    "T-P2": _takeaway_t_p2,
    "T-A2": _takeaway_t_a2,
    "T-D2": _takeaway_share_trajectory,
    "R-P2": _takeaway_r_p2,
    "R-D2": _takeaway_share_trajectory,
    "A2":   _takeaway_a2,
    "A3":   _takeaway_a3,
    "T1":   _takeaway_t1,
    "T2":   _takeaway_t2,
    "T4":   _takeaway_t4,
}


def compute_takeaway(
    qid: str | None,
    merchant_id: str,
    *,
    filters: dict | None = None,
) -> str | None:
    """Return the mechanically-computed takeaway string for ``qid``,
    or ``None`` if the qid is unknown / has no chart helper / the
    chart helper returned empty data.

    Used by both ``chat.py`` (display as the chart caption) and
    ``agents.py`` (inject as authoritative ground truth into the
    specialist's input)."""
    if not qid:
        return None
    fn = _REGISTRY.get(qid)
    if fn is None:
        return None
    try:
        return fn(merchant_id, filters=filters)
    except Exception:  # noqa: BLE001 — takeaway failures must not break dispatch
        return None
