# Architecture & Strategy Doc Mapping

How the demo maps to the parent Core Data Strategy & Solutions document, what's been simplified, and what's deferred.

---

## The four-stage flow

The demo collapses the seven-stage pipeline from the strategy doc into four observable steps. Naming is kept consistent with the strategy doc so the mapping is direct.

```
   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
   │  Kroger      │  │  Taco Bell   │  │  TJ Maxx     │   capture
   │  generator   │  │  generator   │  │  generator   │   (strategy §3-§5)
   └──────┬───────┘  └──────┬───────┘  └──────┬───────┘
          │                 │                 │
          │   raw CSVs (PII present, shared customer_pan across merchants)
          └────────┬────────┘────────┬────────┘
                   ▼                 ▼
          ┌────────────────────────────────┐
          │  anonymize/tenant.py           │   anonymize stage 1
          │  - drop name, email            │   (strategy §8.2)
          │  - hash PAN → customer_id      │
          └─────────────┬──────────────────┘
                        ▼
          ┌────────────────────────────────┐
          │  anonymize/lake.py             │   anonymize stage 2
          │  - ZIP5 → ZIP3                 │   (strategy §8.2)
          │  - txn_ts → hour bucket        │
          │  - k=5 anonymity check         │
          │  - downgrade to category-level │
          └─────────────┬──────────────────┘
                        ▼
          ┌────────────────────────────────┐
          │  SQLite                        │   store
          │  tenant_* tables (per-merchant)│   (strategy §7 + §8.3)
          │  lake_*   tables (aggregate)   │
          └─────────────┬──────────────────┘
                        ▼
          ┌────────────────────────────────┐
          │  Merchant Advisor agent        │   insight
          │  Network Analyst agent         │   (strategy §10)
          │           ↑                    │
          │   Streamlit dashboard          │
          │   (role selector)              │
          └────────────────────────────────┘
```

---

## Mapping to the strategy doc

| Strategy doc section | Demo equivalent | Concession / how it differs |
|---|---|---|
| §2.1 Converged Data Position | The cross-merchant join via shared `customer_id` hash. The same physical customer's transactions at Kroger, Taco Bell, and TJ Maxx all share one `customer_id` — only a payments company sits at this intersection. | Demonstrated; not a concession. |
| §3 Payment Device Hardware | (not built) — data appears already-captured | The hardware/OS/edge story from §3–§6 is the part of the strategy doc least represented by this demo. |
| §4 Terminal OS & Software Stack | (not built) | — |
| §5 On-Device Data Capture | Three `generate/*.py` modules produce the unified record from §5.2 | Synthetic, batch. Real terminal capture, P2PE, basket-bridge integration not built. |
| §6 Edge-to-Cloud Transmission | (not built) | No Kafka, no mTLS, no schema registry, no compression, no store-and-forward. |
| §7 Cloud Data Platform | SQLite + Python loader | No streaming (no Flink), no hot/warm/cold tiers, no API gateway. |
| §8.1 Privacy by Design | The dual-path pipeline is the architecture | Demonstrated. |
| §8.2 Anonymization Techniques | Implemented: PAN tokenization (SHA-256), generalization (ZIP3, hour buckets), k-anonymity (k=5) | k=5 instead of k≥50. L-diversity not implemented. Differential privacy is a documented stub. |
| **§8.3 Dual-Path Data Isolation** | **Implemented as `tenant_*` and `lake_*` tables in SQLite. Tenant queries auto-filtered by merchant; lake queries are anonymized aggregate.** | Tenant isolation enforced via SQL filtering at the agent tool layer, not via actual authn/authz or separate databases. |
| §9 Real-Time Pipeline | (not built) — batch only | No latency targets enforced; data freshness is "whenever you ran make seed." |
| §10.1 Agent Architecture | One four-layer agent loop pattern shared by both demo agents. Tools, prompts, model inference, delivery. | Single LLM (Anthropic Claude); no per-agent model selection; no feedback/learning loop (§10.3). |
| §10.2 Conversational Business Advisor | **Built as Merchant Advisor and Network Analyst.** | Two specializations of the conversational pattern; the other six agents are roadmap. |
| §10.2 Demand Forecasting | (stretch — built only if time allows) | 7-day rolling mean with day-of-week adjustment, no Prophet/ARIMA. |
| §10.2 Dynamic Pricing & Benchmarking | (not built) | Roadmap. |
| §10.2 Consumer Segmentation | (not built as standalone — embedded in advisor's lake queries) | Roadmap. |
| §10.2 Location & Trade Area Intelligence | (not built) | Roadmap. |
| §10.2 Payment Optimization Advisor | (not built) | Roadmap. |
| §10.2 Anomaly Detection & Fraud Intelligence | (not built as real-time — anomalies are findable on-demand by the advisor when asked) | Roadmap for the streaming version. |
| §10.3 Feedback & Learning Loop | (not built) | Roadmap. |
| §11 Merchant Use Cases | All three target segments (grocery, QSR, retail) represented; demo questions span single-merchant analytics, peer benchmarking, and cross-merchant insights | Three merchants, not an enterprise panel. |
| §12 Data Products & Monetization | (not represented) | Out of scope for a technical demo. |
| §14 Governance & Compliance | Demonstrated through anonymization pipeline structure (PII never reaches DB; anonymization is a separate stage). | Real compliance is more than tech architecture — out of scope. |

---

## What "dual-path isolation" looks like in practice

The strategy doc's §8.3 states:

> A strict separation exists between merchant-proprietary data and the anonymized aggregate data lake. Each merchant's own transaction data remains accessible in full granularity within a tenant-isolated environment. The anonymized data lake contains only data that has passed through the complete anonymization pipeline. No raw or merchant-identifiable data ever enters the aggregate analytics layer.

In the demo:

- **Tenant tables (`tenant_*`)** hold each merchant's data with full granularity: full timestamps, full ZIP5, SKU-level basket detail, real (synthetic) prices. The only anonymization is dropping the customer's name/email and hashing the PAN to a `customer_id`. This is what each merchant uses to run their own business.
- **Lake tables (`lake_*`)** hold the cross-merchant aggregate, additionally anonymized: ZIP3 only, hour-bucketed timestamps, category-level (not SKU-level) line items, k=5 anonymity nulling rare quasi-identifier combinations. This is what enables cross-merchant analytics without exposing individuals.
- **Tenant isolation is enforced at the agent tool layer.** The Merchant Advisor's `query_tenant` tool requires every query to include `WHERE merchant_id = '<current_merchant>'`; queries lacking this predicate are rejected before execution. When acting as Kroger, the dashboard cannot pull Taco Bell's specific tenant data.
- **Lake queries are unrestricted on merchant** (the lake is meant for cross-merchant analytics), but its anonymization is what protects merchants from being individually identifiable through aggregate queries.

In production, tenant isolation would be enforced by actual authn/authz and separate storage tenants, not by SQL-predicate enforcement. The demo's enforcement is a simulation that demonstrates the principle.

---

## Deferred items (post-demo roadmap)

If the demo lands and there's appetite to extend it, the priority order:

1. **Add the remaining five agents** from strategy doc §10.2. The pattern is shared (system prompt + tools + loop); each agent is incremental work.
2. **Add merchants** beyond the initial three. The generation framework is parameterized; adding Shell (gas) or CVS (pharmacy) is a config + catalog file.
3. **Real-time path.** Replace the batch loader with Kafka producer + Flink stream consumer. Strategy doc §9. The agents stay the same; data freshness changes from "post-batch" to "sub-minute."
4. **Production-grade privacy.** k = 50, l-diversity, ε-bounded differential privacy on aggregate releases. Strategy doc §8.2.
5. **Real tenant isolation.** Move from SQL-predicate enforcement to actual authn/authz with per-tenant database isolation. Strategy doc §8.3.
6. **Annual seasonality in the synthetic data.** Set `DAYS = 730` and add seasonal multipliers per category (pumpkin in October, BBQ in summer, etc.).
7. **Hardware/edge layer.** Build a terminal simulator that produces P2PE-ceremony events, mTLS-authenticated transmission to a fake cloud endpoint, schema registry validation. Strategy doc §3–§6.
8. **Feedback/learning loop.** Track which agent recommendations were accepted, feed back into prompt or model tuning. Strategy doc §10.3.

---

## Honest framing for stakeholder demos

Be ready to say, when asked:

- *"This demo focuses on the analytics and AI-agent layers from sections 7, 8, and 10 of the strategy document. The real-time capture and streaming pipeline from sections 3 through 6 and 9 is architected in the strategy doc but not built in this prototype — the data here represents what would arrive at the cloud platform after the pipeline runs."*
- *"The k-anonymity threshold is set to 5 instead of 50 because the demo dataset has 5,000 customers; with 50 the suppression would eliminate most of the data. In production, with millions of customers per region, k ≥ 50 works as specified."*
- *"Two of the seven agents are built — both specializations of the Conversational Business Advisor pattern from section 10.2. The other five agents follow the same architectural pattern; the build cost is incremental rather than architectural."*

The demo's strategic claim is intact: a payments company can deliver merchants a private view of their own data alongside cross-merchant insights that no card network and no POS vendor can produce. That's what the strategy doc is selling, and that's what the demo demonstrates.
