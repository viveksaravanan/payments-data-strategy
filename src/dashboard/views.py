"""Dashboard view renderers — six own-data cards stacked top → bottom:

    1. KPI strip (3 tiles: sales volume, transactions, avg basket)
    2. SKU performance (promotion candidates / top performers)
    3. Category mix (with subcategory drill)
    4. Payment mix (entry mode, card network, mobile wallet)
    5. Store performance distribution
    6. Hour × day-of-week traffic

Every renderer takes ``(merchant_id, filters)`` and writes directly into
the current Streamlit container. Filter state propagates through every
query via ``_filters_key()``. All six cards are OWN-DATA, tenant-scoped
to the selected merchant — there are no peer/lake queries on the main
dashboard.
"""
from __future__ import annotations

import streamlit as st

from . import data as D

ACCENT = "#0F4C81"


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _fmt_money(n: float) -> str:
    if n >= 1_000_000:
        return f"${n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"${n / 1_000:.0f}K"
    return f"${n:.2f}"


def _fmt_int(n: int) -> str:
    return f"{int(n):,}"


def _direction(pct: float | None) -> str:
    if pct is None:
        return "flat"
    return "up" if pct > 0 else ("down" if pct < 0 else "flat")


def _window_too_narrow(filters: dict) -> bool:
    """True when the selected header range is too short to form a
    recent-vs-baseline comparison (< 14 days ≈ fewer than 2 complete
    Mon–Sun weeks). Matches the KPI strip's delta-suppression threshold
    so the comparison cards guard consistently."""
    start = filters.get("date_start") or D.PANEL_START
    end = filters.get("date_end") or D.PANEL_END
    return (end - start).days < 14


# ---------------------------------------------------------------------------
# Card 1 — KPI strip (3 tiles)
# ---------------------------------------------------------------------------

def render_kpi_strip(merchant_id: str, filters: dict) -> None:
    """Three Pattern-8 KPI callouts — sales volume, transactions, avg
    basket — each for the most recent COMPLETE week with a "vs last
    month avg" delta (the average of the prior 4 complete weeks) and a
    weekly trend sparkline.
    """
    from . import chart_patterns as CP

    with st.spinner("Loading sales KPIs…"):
        k = D.kpi_strip(merchant_id, filters=filters)

    def _delta_text(pct: float | None) -> str:
        if pct is None:
            return "— (window too narrow)"
        return f"{pct:+.1f}% vs last month avg"

    cols = st.columns(3, gap="small")

    with cols[0]:
        CP.render_kpi_callout(
            label="Sales volume this week",
            value=_fmt_money(k["sales"]["value"]),
            delta_text=_delta_text(k["sales"]["delta_pct"]),
            delta_direction=_direction(k["sales"]["delta_pct"]),
            sparkline=k["sales"]["sparkline"],
            ask_about_this={
                "key":        f"ask_about_sales_{merchant_id}",
                "specialist": "anomaly",
                "prefill":    "What's driving the change in my sales this week?",
            },
        )

    with cols[1]:
        CP.render_kpi_callout(
            label="Transactions this week",
            value=_fmt_int(k["transactions"]["value"]),
            delta_text=_delta_text(k["transactions"]["delta_pct"]),
            delta_direction=_direction(k["transactions"]["delta_pct"]),
            sparkline=k["transactions"]["sparkline"],
            ask_about_this={
                "key":        f"ask_about_txns_{merchant_id}",
                "specialist": "anomaly",
                "prefill":    "What's driving the change in my transaction count this week?",
            },
        )

    with cols[2]:
        CP.render_kpi_callout(
            label="Avg basket",
            value=_fmt_money(k["avg_basket"]["value"]),
            delta_text=_delta_text(k["avg_basket"]["delta_pct"]),
            delta_direction=_direction(k["avg_basket"]["delta_pct"]),
            sparkline=k["avg_basket"]["sparkline"],
            ask_about_this={
                "key":        f"ask_about_basket_{merchant_id}",
                "specialist": "demand",
                "prefill":    "What's changing about my average ticket?",
            },
        )


# ---------------------------------------------------------------------------
# Card 2 — SKU performance (promotion candidates / top performers)
# ---------------------------------------------------------------------------

def render_sku_performance(merchant_id: str, filters: dict) -> None:
    """Per-SKU recent-week performance vs the first-4-week baseline, in
    two toggled views:

      - **Promotion candidates** (default) — SKUs selling *below* their
        baseline (the "what to move / mark down" list).
      - **Top performers** — SKUs at or above baseline.

    The split is STRICT on the sign of the baseline deviation, so a
    below-baseline SKU can never appear in the top list and vice-versa.
    Product names + the merchant's own category labels come from the
    products catalog (never SKU codes).
    """
    from . import chart_patterns as CP

    state = st.session_state
    state.setdefault("sku_view_by_merchant", {})

    with st.container(border=True):
        current = state.sku_view_by_merchant.get(merchant_id, "promo")
        view = st.radio(
            "Show",
            options=["Promotion candidates", "Top performers"],
            index=0 if current == "promo" else 1,
            horizontal=True,
            key=f"sku_view_{merchant_id}",
            label_visibility="collapsed",
        )
        view_key = "promo" if view == "Promotion candidates" else "top"
        state.sku_view_by_merchant[merchant_id] = view_key

        with st.spinner("Loading SKU performance…"):
            data = D.sku_performance(merchant_id, filters=filters)
        all_rows = data["rows"]

        # Recent-vs-baseline needs ≥ ~2 complete weeks; on a too-narrow
        # header range the data layer returns no rows. Say WHY explicitly
        # rather than showing the generic per-view "nothing here" state.
        if not all_rows:
            CP._render_card_header(
                "SKU performance", "",
                ask_about_this={
                    "key":        f"ask_about_sku_{merchant_id}",
                    "specialist": "demand",
                    "prefill":    "How are my SKUs performing?",
                },
            )
            if _window_too_narrow(filters):
                st.caption(
                    "Select a wider date range (≥ 2 weeks) to compare recent "
                    "weeks against a baseline."
                )
            else:
                CP.render_empty_state("No SKU activity in the selected window.")
            return

        house_growth = data.get("house_growth_pct", 0.0)

        # STRICT split on the TREND-ADJUSTED deviation — each SKU's growth
        # relative to the merchant's own (house) growth, i.e. whether it
        # gained or lost SHARE of the basket. Losing share (negative) →
        # promotion/markdown candidate; gaining/holding share (≥ 0) or a
        # brand-new SKU with no baseline (None) → top performer. This stays
        # meaningful when the whole store is up: absolute deviation would
        # flag nothing, but the share view still finds the laggards. The
        # absolute "Δ vs baseline" is shown alongside for context.
        promo_rows = [
            r for r in all_rows
            if r["trend_dev_pct"] is not None and r["trend_dev_pct"] < 0
        ]
        top_rows = [
            r for r in all_rows
            if r["trend_dev_pct"] is None or r["trend_dev_pct"] >= 0
        ]

        columns = [
            {"key": "sku_name",       "label": "Product",             "kind": "text"},
            {"key": "category",       "label": "Category",            "kind": "text"},
            {"key": "baseline_lines", "label": "Units/wk (baseline)", "kind": "float"},
            {"key": "recent_lines",   "label": "Recent",              "kind": "int"},
            {"key": "deviation_pct",  "label": "Δ vs baseline",       "kind": "pct"},
            {"key": "trend_dev_pct",  "label": "Δ vs store trend",
             "kind": "pct", "diverging": True},
            {"key": "recent_revenue", "label": "Sales ($)",           "kind": "int"},
        ]

        if view_key == "promo":
            ask = {
                "key":        f"ask_about_sku_{merchant_id}",
                "specialist": "anomaly",
                "prefill": (
                    "Which of my items are losing share of the basket and "
                    "worth a promotion?"
                ),
            }
            # A ranked shortlist, NOT a classification of the whole
            # catalog: show the biggest share-losers. (Reporting "how many
            # SKUs are below trend" is meaningless — in any distribution
            # roughly half fall below the average, and the segments differ
            # too much for a single cutoff: QSR menus cluster within a few
            # points of trend, grocery spreads much wider.)
            rows = sorted(promo_rows, key=lambda r: r["trend_dev_pct"])[:15]
            if not rows:
                CP._render_card_header(
                    "SKU performance — promotion candidates",
                    "No SKUs are losing share against your store trend.",
                    ask_about_this=ask,
                )
                CP.render_empty_state(
                    "Every SKU is holding or gaining share of the basket — "
                    "nothing lagging the store's overall trend to promote."
                )
                return
            worst = rows[0]
            takeaway = (
                f"Your {len(rows)} biggest share-losers — items growing "
                f"slower than your {house_growth:+.0f}% store trend, so worth "
                f"a promotion. Biggest laggard: {worst['sku_name']} at "
                f"{worst['trend_dev_pct']:+.1f}% vs trend "
                f"({worst['deviation_pct']:+.1f}% absolute)."
            )
            title = "SKU performance — promotion candidates"
        else:
            ask = {
                "key":        f"ask_about_sku_{merchant_id}",
                "specialist": "demand",
                "prefill": (
                    "Tell me more about my top-performing SKUs and what's "
                    "driving them."
                ),
            }
            rows = sorted(
                top_rows, key=lambda r: r["recent_revenue"], reverse=True
            )[:20]
            if not rows:
                CP._render_card_header(
                    "SKU performance — top performers",
                    "No SKUs are holding or gaining share this week.",
                    ask_about_this=ask,
                )
                CP.render_empty_state("No SKU activity to rank this week.")
                return
            total_rev = sum(r["recent_revenue"] for r in all_rows) or 1.0
            top10_rev = sum(r["recent_revenue"] for r in rows[:10])
            top_sku = rows[0]
            takeaway = (
                f"Top SKU by sales: {top_sku['sku_name']} "
                f"({_fmt_money(top_sku['recent_revenue'])} this week). "
                f"Top 10 account for {top10_rev / total_rev * 100:.0f}% of "
                "sales; all shown are holding or gaining share."
            )
            title = "SKU performance — top performers"

        CP.render_table_with_drilldown(
            {"rows": rows, "columns": columns},
            title=title,
            takeaway=takeaway,
            height=430,
            ask_about_this=ask,
        )


# ---------------------------------------------------------------------------
# Card 3 — Sales by department (with category drill)
# ---------------------------------------------------------------------------

def render_department_mix(merchant_id: str, filters: dict) -> None:
    """Share of sales through the merchant's own shelf taxonomy, drilled
    down — **segment-aware**. Grocery drills department → category →
    subcategory; QSR (whose ``merchant_department`` is a single "Menu"
    bucket) starts at category → subcategory. Uses the merchant's own shelf
    labels (readable names — never SKU codes or customer segments)."""
    from . import chart_patterns as CP

    with st.container(border=True):
        with st.spinner("Loading sales mix…"):
            data = D.department_mix_own(merchant_id, filters=filters)
        grain = data["top_grain"]                      # "department" | "category"
        title = "Sales by department" if grain == "department" else "Sales by category"

        ask = {
            "key":        f"ask_about_dept_mix_{merchant_id}",
            "specialist": "demand",
            "prefill": (
                f"What does my sales mix look like by {grain}? Where am "
                "I most concentrated?"
            ),
        }

        if not data["labels"]:
            CP._render_card_header(title, "", ask_about_this=ask)
            st.caption(f"_No {grain}-sales data available._")
            return

        names = data["top3_names"]
        lead = (
            f"{names[0]}, {names[1]}, and {names[2]}"
            if len(names) >= 3 else ", ".join(names)
        )
        takeaway = (
            f"{lead} are the largest {grain}s — together "
            f"{data['top3_pct']:.0f}% of sales."
        )
        CP.render_horizontal_bars_own(
            {
                "labels":       data["labels"],
                "values":       data["values"],
                "x_label":      "Share of sales (%)",
                "value_format": ".1f",
            },
            title=title,
            takeaway=takeaway,
            height=360,
            ask_about_this=ask,
        )

        # Drill — one expander per top-level item. Grocery goes one level
        # deeper (category → subcategory) via a selectbox inside the expander,
        # because Streamlit forbids nesting expanders.
        st.caption(f"Expand a {grain} to see its breakdown.")
        for label in data["labels"]:
            node = data["drill"].get(label)
            if not node or not node["labels"]:
                continue  # no children → bar shown above, just not expandable
            child_grain = "category" if grain == "department" else "subcategory"
            with st.expander(label):
                CP.render_horizontal_bars_own(
                    {
                        "labels":       node["labels"],
                        "values":       node["values"],
                        "x_label":      f"Share of {grain} sales (%)",
                        "value_format": ".1f",
                    },
                    title=f"{child_grain.capitalize()} mix",
                    takeaway="",
                    height=max(180, 26 * len(node["labels"]) + 60),
                )
                # Grocery only: 3rd level (subcategory) behind a selectbox.
                sub = node.get("sub") or {}
                drillable = [c for c in node["labels"] if sub.get(c, {}).get("labels")]
                if drillable:
                    chosen = st.selectbox(
                        "Drill into a category",
                        drillable,
                        key=f"subcat_sel_{merchant_id}_{label}",
                    )
                    leaf = sub.get(chosen) or {"labels": [], "values": []}
                    if leaf["labels"]:
                        CP.render_horizontal_bars_own(
                            {
                                "labels":       leaf["labels"],
                                "values":       leaf["values"],
                                "x_label":      "Share of category sales (%)",
                                "value_format": ".1f",
                            },
                            title=f"{chosen} — subcategory mix",
                            takeaway="",
                            height=max(160, 26 * len(leaf["labels"]) + 60),
                        )


# ---------------------------------------------------------------------------
# Card 4 — Payment mix (NEW)
# ---------------------------------------------------------------------------

def render_payment_mix(merchant_id: str, filters: dict) -> None:
    """Own-transaction payment breakdowns: entry mode, card network, and
    mobile-wallet adoption by provider. Simple labeled bars under a
    plain-language summary."""
    from . import chart_patterns as CP

    with st.container(border=True):
        ask = {
            "key":        f"ask_about_payment_{merchant_id}",
            "specialist": "advisor",
            "prefill": (
                "What does my payment mix tell me — entry modes, card "
                "networks, and wallet adoption?"
            ),
        }
        with st.spinner("Loading payment mix…"):
            pm = D.payment_mix(merchant_id, filters=filters)

        if pm["total_txns"] == 0:
            CP._render_card_header("Payment mix", "", ask_about_this=ask)
            st.caption("_No transactions in the selected window._")
            return

        # One-line plain-language summary, led by the contactless story
        # when it dominates, otherwise the top entry mode.
        if pm["contactless_pct"] >= 50:
            summary = (
                f"Over half your checkouts are contactless taps "
                f"({pm['contactless_pct']:.0f}%)."
            )
        else:
            summary = (
                f"{pm['top_entry_label']} is your most common checkout "
                f"({pm['top_entry_pct']:.0f}% of transactions)."
            )
        summary += (
            f" Mobile wallets are used on {pm['wallet']['adoption_pct']:.0f}% "
            "of transactions."
        )
        CP._render_card_header("Payment mix", summary, ask_about_this=ask)

        sub = st.columns(3, gap="small")
        with sub[0]:
            CP.render_horizontal_bars_own(
                {
                    "labels":       pm["entry_mode"]["labels"],
                    "values":       pm["entry_mode"]["values"],
                    "x_label":      "Share of transactions (%)",
                    "value_format": ".1f",
                },
                title="Entry mode",
                takeaway="",
                height=200,
            )
        with sub[1]:
            CP.render_horizontal_bars_own(
                {
                    "labels":       pm["network"]["labels"],
                    "values":       pm["network"]["values"],
                    "x_label":      "Share of transactions (%)",
                    "value_format": ".1f",
                },
                title="Card network",
                takeaway="",
                height=200,
            )
        with sub[2]:
            CP.render_horizontal_bars_own(
                {
                    "labels":       pm["wallet"]["labels"],
                    "values":       pm["wallet"]["values"],
                    "x_label":      "Share of transactions (%)",
                    "value_format": ".1f",
                },
                title="Mobile wallet",
                takeaway="",
                height=200,
            )


# ---------------------------------------------------------------------------
# Card 5 — Store performance distribution
# ---------------------------------------------------------------------------

def render_store_performance(merchant_id: str, filters: dict) -> None:
    """Per-store recent-week sales vs the first-4-week baseline, worst
    performers first, colour-coded on the deviation. Shows ALL of the
    merchant's own stores (own-data only — no peer comparison)."""
    from . import chart_patterns as CP

    with st.container(border=True):
        ask = {
            "key":        f"ask_about_store_perf_{merchant_id}",
            "specialist": "anomaly",
            "prefill": "Which of my stores had the biggest sales change this week?",
        }
        with st.spinner("Loading store performance…"):
            data = D.store_anomalies_own_only(merchant_id, filters=filters)
        rows = data["rows"]

        if not rows:
            CP._render_card_header(
                "Store performance distribution", "", ask_about_this=ask,
            )
            if _window_too_narrow(filters):
                st.caption(
                    "Select a wider date range (≥ 2 weeks) to compare recent "
                    "weeks against a baseline."
                )
            else:
                st.caption("_No stores with enough baseline data._")
            return

        # Worst-first: most-negative deviation at the top (what to act on).
        rows = sorted(rows, key=lambda r: r["deviation_pct"])
        n_under = sum(1 for r in rows if r["deviation_pct"] <= -15.0)
        top_perf = max(rows, key=lambda r: r["deviation_pct"])
        takeaway = (
            f"{n_under} of {len(rows)} stores running below baseline by "
            f">15%; strongest store {top_perf['store_id']} at "
            f"{top_perf['deviation_pct']:+.1f}% vs baseline."
        )
        columns = [
            {"key": "store_id",      "label": "Store",             "kind": "text"},
            {"key": "neighborhood",  "label": "Neighborhood",      "kind": "text"},
            {"key": "baseline",      "label": "Baseline/wk (sales $)", "kind": "int"},
            {"key": "recent",        "label": "Recent (sales $)",  "kind": "int"},
            {"key": "deviation_pct", "label": "Δ vs baseline",
             "kind": "pct", "diverging": True},
        ]
        CP.render_table_with_drilldown(
            {"rows": rows, "columns": columns},
            title="Store performance distribution",
            takeaway=takeaway,
            height=360,
            ask_about_this=ask,
        )


# ---------------------------------------------------------------------------
# Card 6 — Hour × day-of-week traffic
# ---------------------------------------------------------------------------

def render_hour_dow_traffic(merchant_id: str, filters: dict) -> None:
    """Transactions by hour × day-of-week. Day rows are ordered Mon→Sun
    (DuckDB ``strftime('%w')`` returns Sunday = 0; the data layer maps it
    to the Mon→Sun display order)."""
    from . import chart_patterns as CP

    with st.container(border=True):
        ask = {
            "key":        f"ask_about_hour_dow_{merchant_id}",
            "specialist": "trade",
            "prefill": "What does my hour-by-day traffic pattern tell me?",
        }
        with st.spinner("Loading traffic heatmap…"):
            h = D.hour_dow_heatmap_card(merchant_id, filters=filters)

        wd_we_phrase = (
            f"{h['wd_we_higher']} traffic is {h['wd_we_ratio']}% higher "
            "than the inverse"
            if h["wd_we_ratio"] > 0
            else "weekday and weekend traffic are roughly even"
        )
        takeaway = (
            f"Peak: {h['peak_dow']} {h['peak_hr']:02d}:00 "
            f"({h['peak_val']:,} txns). Slowest: {h['slow_dow']} "
            f"{h['slow_hr']:02d}:00 ({h['slow_val']:,}). {wd_we_phrase}."
        )
        CP.render_heatmap(
            h,
            title="Hour × day-of-week traffic",
            takeaway=takeaway,
            mode="own_only_sequential",
            height=300,
            ask_about_this=ask,
        )
