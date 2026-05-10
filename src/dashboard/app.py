"""Role-based Streamlit dashboard for the Payments Data Strategy demo.

Top: title + role selectbox + MOCK MODE toggle. Below: a context paragraph
explaining what the current role can see, a canned-question dropdown, a
free-text input, and a Run button. The agent's response is rendered with the
headline, the SQL it ran (in an expander, labeled by tool), the last result as
a dataframe, and an optional chart.
"""
from __future__ import annotations

from pathlib import Path

# Load `.env` BEFORE importing the agents — they read ANTHROPIC_API_KEY at
# construction time. (They also call `load_dotenv()` themselves; doing it here
# too is harmless and makes the dashboard's dependency on `.env` explicit.)
from dotenv import load_dotenv

load_dotenv()

import pandas as pd
import streamlit as st

from src.agents.advisor import MAX_TURNS, MerchantAdvisor

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "payments.db"

ROLE_TO_MERCHANT_ID = {
    "Kroger":     "KRG",
    "Acme":       "ACM",
    "Winn-Dixie": "WDX",
    "Taco Bell":  "TBL",
    "TJ Maxx":    "TJX",
}
ROLES = list(ROLE_TO_MERCHANT_ID.keys())

CANNED_QUESTIONS: dict[str, list[str]] = {
    "Kroger": [
        "What are my top categories by revenue last week, and which subcategories drove each?",
        "Which products are most often bought together with whole milk in my stores?",
        "Have any of my stores seen a drop in transaction count week-over-week?",
        "How does my average basket size compare to peer grocers in the panel?",
        "How does my dairy unit pricing compare to peer grocers?",
    ],
    "Acme": [
        "What are my top categories by revenue last week, and which subcategories drove each?",
        "Which products are most often bought together with whole milk in my stores?",
        "Have any of my stores seen a drop in transaction count week-over-week?",
        "How does my average basket size compare to peer grocers in the panel?",
        "How does my produce pricing compare to peer grocers?",
    ],
    "Winn-Dixie": [
        "What are my top categories by revenue last week, and which subcategories drove each?",
        "Which products are most often bought together with bread in my stores?",
        "Have any of my stores seen a drop in transaction count week-over-week?",
        "How does my average basket size compare to peer grocers in the panel?",
        "How does my bakery pricing compare to peer grocers?",
    ],
    "Taco Bell": [
        "What are my top menu items by revenue last week, and which categories drove the volume?",
        "Which items are most often ordered together with a Crunchwrap Supreme?",
        "Have any of my stores seen a drop in transaction count week-over-week?",
        "How does my average ticket size compare to QSR peers in the panel?",
        "What payment-mix or entry-mode patterns differ between my stores and QSR peers?",
    ],
    "TJ Maxx": [
        "What are my top categories by revenue last week, and which subcategories drove each?",
        "Which categories are most often purchased together (e.g., apparel + accessories)?",
        "Have any of my stores seen a drop in transaction count week-over-week?",
        "How does my average ticket size compare to off-price retail peers in the panel?",
        "What payment-mix or entry-mode patterns differ between my stores and retail peers?",
    ],
}

CONTEXT_BLURBS: dict[str, str] = {
    "Kroger":
        "You are acting as **Kroger's** ops team (one of three grocers in the "
        "5-merchant Charlotte panel). Your tenant queries see Kroger's full "
        "granularity. Lake queries return the other four merchants pseudonymized "
        "as `peer_a`..`peer_d`; Kroger's own rows are excluded automatically.",
    "Acme":
        "You are acting as **Acme's** ops team (one of three grocers in the "
        "5-merchant Charlotte panel). Your tenant queries see Acme's full "
        "granularity. Lake queries return the other four merchants pseudonymized "
        "as `peer_a`..`peer_d`; Acme's own rows are excluded automatically.",
    "Winn-Dixie":
        "You are acting as **Winn-Dixie's** ops team (one of three grocers in "
        "the 5-merchant Charlotte panel). Your tenant queries see Winn-Dixie's "
        "full granularity. Lake queries return the other four merchants "
        "pseudonymized as `peer_a`..`peer_d`; Winn-Dixie's own rows are excluded.",
    "Taco Bell":
        "You are acting as **Taco Bell's** ops team (the QSR in the 5-merchant "
        "Charlotte panel). Your tenant queries see Taco Bell's full granularity. "
        "Lake queries return the other four merchants pseudonymized as "
        "`peer_a`..`peer_d`; Taco Bell's own rows are excluded automatically.",
    "TJ Maxx":
        "You are acting as **TJ Maxx's** ops team (the off-price retailer in "
        "the 5-merchant Charlotte panel). Your tenant queries see TJ Maxx's "
        "full granularity. Lake queries return the other four merchants "
        "pseudonymized as `peer_a`..`peer_d`; TJ Maxx's own rows are excluded.",
}


# ---------------------------------------------------------------------------
# Page setup + DB sanity check
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Payments Data Strategy Demo", layout="wide")

if not DB_PATH.exists() or DB_PATH.stat().st_size == 0:
    st.error("Database not found or empty. Run `make seed` first.")
    st.stop()


# ---------------------------------------------------------------------------
# Header: title + MOCK MODE toggle
# ---------------------------------------------------------------------------

title_col, mock_col = st.columns([4, 1])
with title_col:
    st.title("Payments Data Strategy Demo")
    st.caption(
        "Cross-merchant analytics on a synthetic 10,000-customer Charlotte "
        "panel · Kroger · Acme · Winn-Dixie · Taco Bell · TJ Maxx"
    )
with mock_col:
    st.write("")  # vertical alignment with the title
    mock = st.checkbox(
        "MOCK MODE",
        value=False,
        help="Skip the LLM API and return canned responses (offline demo safety net).",
    )


# ---------------------------------------------------------------------------
# Role selector + context blurb
# ---------------------------------------------------------------------------

role = st.selectbox("Acting as", ROLES, key="role")
st.info(CONTEXT_BLURBS[role])


# ---------------------------------------------------------------------------
# Question form
# ---------------------------------------------------------------------------

with st.form("ask_form", clear_on_submit=False):
    canned = st.selectbox(
        f"Canned questions for {role}",
        CANNED_QUESTIONS[role],
        key=f"canned__{role}",
    )
    free_text = st.text_input(
        "Or type your own (overrides the dropdown when non-empty):",
        key=f"free__{role}",
    )
    submit = st.form_submit_button("Run", type="primary")


# ---------------------------------------------------------------------------
# Agent factory — cached so toggling mock toggles the cached instance
# ---------------------------------------------------------------------------

@st.cache_resource
def _get_agent(role: str, mock: bool) -> MerchantAdvisor:
    return MerchantAdvisor(current_merchant_id=ROLE_TO_MERCHANT_ID[role], mock=mock)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def _render_response(resp) -> None:
    st.divider()

    if not getattr(resp, "converged", True) or (not resp.answer and resp.turns >= MAX_TURNS):
        st.warning(
            "The agent reached its turn limit before producing a final answer. "
            "Showing best-effort partial result."
        )

    st.markdown(resp.answer or "_(no answer text)_")

    if resp.sql:
        n = len(resp.sql)
        with st.expander(f"Show the SQL the agent ran ({n} {'query' if n == 1 else 'queries'})"):
            for i, s in enumerate(resp.sql, start=1):
                label = "Tenant query" if s["tool"] == "tenant" else "Lake query"
                st.markdown(f"**{i}. {label}**")
                st.code(s["query"], language="sql")

    last = resp.last_table
    if last and last.get("rows"):
        st.subheader("Last result")
        df = pd.DataFrame(last["rows"], columns=last["columns"])
        if last.get("truncated"):
            st.caption(f"Truncated to {len(df)} rows.")
        st.dataframe(df, use_container_width=True)

        spec = resp.chart
        if spec and spec.get("x") in df.columns and spec.get("y") in df.columns:
            st.subheader(spec.get("title") or "Chart")
            try:
                chart_df = df[[spec["x"], spec["y"]]].set_index(spec["x"])
                if spec["type"] == "bar":
                    st.bar_chart(chart_df)
                elif spec["type"] == "line":
                    st.line_chart(chart_df)
            except Exception as e:  # noqa: BLE001 - cosmetic fallback
                st.caption(f"(could not render chart: {e})")


if submit:
    question = free_text.strip() if free_text and free_text.strip() else canned

    try:
        agent = _get_agent(role, mock)
    except RuntimeError as e:
        st.error(str(e))
        st.stop()

    with st.spinner(f"Asking the {role} agent..."):
        try:
            resp = agent.ask(question)
        except Exception as e:  # noqa: BLE001 - surface any runtime error to the user
            st.error(f"Agent run failed: {e}")
            st.stop()

    _render_response(resp)
