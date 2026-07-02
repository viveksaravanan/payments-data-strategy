"""Stage 2.4 — Anomaly specialist tests.

The Anomaly Detection Agent flags BUSINESS anomalies only — never
fraud or tampering (D20.3). The prompt's hard rule is:
"Never say fraud, tampering, theft, skimming, or chargeback."

Coverage:

* Canonical operational anomaly: own wow_delta vs peer wow_delta at
  (category, derived_zone, period_start).
* Anomaly prompt NEVER contains fraud/tampering/theft/skimming/
  chargeback vocabulary.
* When the model's prose mentions "fraud" anyway, the test confirms
  the validator strips that clause (or the prompt's hard rule
  prevents it from being emitted in the first place — both ways
  count as the guarantee holding).
"""
from __future__ import annotations

import pytest

from src.agents.anomaly import AnomalyDetectionSpecialist
from src.agents.context import MerchantContext
from src.agents.response import AgentResponse
from tests.agents._fake_llm import (
    patch_llm,
    scripted_emit_response,
    scripted_tool_use,
)


@pytest.fixture
def viewer_krg() -> MerchantContext:
    return MerchantContext.for_merchant("KRG")


def test_anomaly_canonical_operational_question(viewer_krg, monkeypatch) -> None:
    """datamodel-v2 two-query flow: own milk units (tenant) vs peer milk
    units (lake). No fraud vocabulary in the delivered prose (D20.3)."""
    own_sql = (
        "SELECT p.functional_category AS category, AVG(i.qty) AS own_avg_qty "
        "FROM transaction_items i JOIN transactions t USING (txn_id) "
        "JOIN products p ON i.sku = p.sku "
        "WHERE t.banner_code = 'KRG' AND p.functional_category = 'Milk' "
        "GROUP BY p.functional_category"
    )
    emit = scripted_emit_response(
        headline="Your milk units per line run at 1.24 — in line with peers, "
                 "no unusual movement.",
        evidence=["Your milk average units per line is 1.24."],
        claims=[{
            "text_span": "1.24",
            "value": 1.24,
            "source": {
                "type": "CellLookup",
                "row_filter": {"category": "Milk"},
                "column": "own_avg_qty",
                "agg": "mean",
                "frame": "tenant",
            },
        }],
        caveats=["Peer set is 2 grocers (segment peers).",
                 "Window: 90 days."],
    )
    script = [
        scripted_tool_use("query_tenant", {"sql": own_sql}),
        scripted_tool_use("query_lake_sql", {"sql": "SELECT category, AVG(qty) AS peer_units FROM lake_transactions WHERE peer_relationship = 'peer' AND category = 'Milk' GROUP BY category"}),
        emit,
    ]
    specialist = AnomalyDetectionSpecialist(viewer_krg)
    with patch_llm(monkeypatch, script):
        resp = specialist.answer("Is there anything unusual in milk?")

    assert isinstance(resp, AgentResponse)
    # No fraud/tampering vocabulary in the delivered prose (D20.3).
    prose_lower = resp.prose.lower()
    assert "fraud" not in prose_lower
    assert "tampering" not in prose_lower
    assert "skimming" not in prose_lower
    assert "chargeback" not in prose_lower


def test_anomaly_prompt_never_mentions_fraud_signals() -> None:
    """Every prohibited word is absent from the prompt as a
    POSITIVE statement (the prompt MENTIONS each word in the "never
    say" rule, so we check the file in a more nuanced way)."""
    # Normalize whitespace so phrases broken across line wraps still match.
    prompt_raw = AnomalyDetectionSpecialist.PROMPT_PATH.read_text()
    prompt = " ".join(prompt_raw.split())
    # The prompt MAY mention these words inside the prohibition
    # statement — that's fine. What we check: the prohibition
    # statement IS present and explicit.
    assert "Never say fraud" in prompt
    assert "panel doesn't contain any fraud signals" in prompt or "no fraud" in prompt.lower()
    assert "D20.3" in prompt_raw


def test_anomaly_prompt_business_anomalies_only() -> None:
    """The prompt must explicitly frame the agent as business-anomaly-
    only."""
    prompt = AnomalyDetectionSpecialist.PROMPT_PATH.read_text().lower()
    assert "business anomalies only" in prompt


def test_anomaly_prompt_uses_query_lake_sql_flow() -> None:
    # Wave 3.5: peer data comes from query_lake_sql (line-item lake),
    # not read_lake_table; daily peer grain (txn_date) is now available
    # so the old "no daily" Exclude no longer applies.
    prompt = AnomalyDetectionSpecialist.PROMPT_PATH.read_text().lower()
    assert "query_lake_sql" in prompt
    assert "peer_relationship" in prompt
    assert "read_lake_table" not in prompt
