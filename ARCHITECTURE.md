# Architecture & Strategy Doc Mapping

How the demo maps to the parent Core Data Strategy & Solutions document, what's been simplified, and what's deferred.

---

## The panel

A **single fictional metro modeled on Charlotte, NC** with **10,000 customers** and **five merchants**: Kroger, Acme, Winn-Dixie, Taco Bell, TJ Maxx. 90-day window: March 1 – May 29, 2026.

All v3 agents are merchant-scoped — every query inherits a viewing-merchant context. Strategy doc §10.2 specifies seven merchant-scoped specialist personas; the demo ships an orchestrator that routes free-form questions to four specialists — pricing, anomaly, demand, and trade-area — over a shared tool loop and the Headline → Evidence → Therefore → Caveats response contract. The remaining three personas (demand forecasting, segmentation, payment optimization) stay on the v4 roadmap. There is no network-level analyst agent in v3.

---

## The data architecture (lake-as-views)

`V2_5_DATA_DESIGN.md` collapses the v2 dual-path layout (parallel `tenant_*` and `lake_*` physical tables) into a single physical layer plus a virtual lake.

```
    ┌─────────┐ ┌─────────┐ ┌─────────────┐ ┌──────────┐ ┌──────────┐
    │ Kroger  │ │  Acme   │ │ Winn-Dixie  │ │ Taco Bell│ │ TJ Maxx  │
    │  gen    │ │  gen    │ │     gen     │ │   gen    │ │   gen    │
    └────┬────┘ └────┬────┘ └─────┬───────┘ └────┬─────┘ └────┬─────┘
         │          │             │              │            │
         └──────────┴─────────────┴──────────────┴────────────┘
                                  │
                                  ▼
              ┌───────────────────────────────────┐
              │  CSVs in data/raw/                │   capture
              │  customer_id is generated         │   (no PII at any
              │  directly; no PAN, no PII         │   stage)
              └─────────────┬─────────────────────┘
                            ▼
              ┌───────────────────────────────────┐
              │  SQLite — tenant_* tables only    │   store
              │  (per-merchant, full granularity) │
              └─────────────┬─────────────────────┘
                            ▼
              ┌───────────────────────────────────┐
              │  src/lake/views.py                │   privacy engine
              │  view-builders compute the lake   │   (computed
              │  from tenant rows at query time:  │   per-query;
              │  - exclude viewing merchant       │   no physical
              │  - peer_id pseudonymization       │   lake tables)
              │  - opaque IDs, ZIP3, hour buckets │
              │  - txn_total binning, no          │
              │    customer_id                    │
              └─────────────┬─────────────────────┘
                            ▼
              ┌───────────────────────────────────┐
              │  Orchestrator + 4 specialists     │   insight (§10)
              │  (pricing, anomaly, demand,       │
              │  trade) + Streamlit dashboard     │
              │  (5-merchant role selector)       │
              └───────────────────────────────────┘
```

**Tenant tables (`tenant_*`)** hold each merchant's data at full granularity: full timestamps, full ZIP5, SKU-level basket detail, real (synthetic) prices, terminal IDs, connectivity. The generator emits `customer_id` directly — there's no PAN on disk and no anonymization pre-stage.

**Lake (virtual)** is two logical tables — `lake_transactions` (21 columns; one row per peer line item) and `lake_stores` (6 columns; peer store reference) — exposed as parameterized query builders in `src/lake/views.py`. Each view-builder takes a `viewing_merchant_id` and returns a SELECT that:

- excludes the viewing merchant's own rows;
- maps the other four merchants to `peer_a`..`peer_d` per the locked Phase 2 mapping (same-segment peers come first, then cross-segment alphabetically);
- replaces internal IDs with salted SHA-256 opaque IDs;
- truncates ZIP5 → ZIP3, generalizes timestamps to a 10-bucket time-of-day, bins `txn_total` into 10 bins;
- drops `customer_id` entirely ("no consumer linkage" — peer line items can't be tied back to a specific customer).

The agent's `query_lake` tool wraps the agent's SELECT in two CTEs (`WITH lake_transactions AS (...), lake_stores AS (...)`) so the agent writes ordinary SQL referencing those names; the runner rewrites it transparently and bakes in the viewing merchant.

**Tenant isolation** is enforced at the agent tool layer. Every specialist's `query_tenant` tool requires every query to include `WHERE merchant_id = '<current_merchant>'`; queries lacking this predicate are rejected before any DB connection opens. In production, tenant isolation would be enforced by actual authn/authz and per-tenant storage; the demo's enforcement simulates the principle.

**k=5 cell suppression** lives at the aggregate level (`apply_k_anonymity` in `src/lake/views.py`). Customer-dimension breakdowns drop cells with fewer than 5 rows. k is 5 instead of 50 because the panel is 10,000 customers; the strategy doc's k≥50 assumes millions per region.

---

## Mapping to the strategy doc

| Strategy doc section | Demo equivalent | Concession / how it differs |
|---|---|---|
| §2.1 Converged Data Position | Same physical customer's transactions at all five merchants share one `customer_id`. Only a payments company sits at this intersection. | Demonstrated; not a concession. |
| §3 Payment Device Hardware | (not built) — data appears already-captured | The hardware/OS/edge story from §3–§6 is the part of the strategy doc least represented by this demo. |
| §4 Terminal OS & Software Stack | (not built) | — |
| §5 On-Device Data Capture | Five per-merchant generators feed the unified record from §5.2 | Synthetic, batch. Real terminal capture, P2PE, basket-bridge integration not built. |
| §6 Edge-to-Cloud Transmission | (not built) | No Kafka, no mTLS, no schema registry, no compression, no store-and-forward. |
| §7 Cloud Data Platform | SQLite + Python loader | No streaming (no Flink), no hot/warm/cold tiers, no API gateway. |
| §8.1 Privacy by Design | Lake-as-views is the architecture | Demonstrated. |
| §8.2 Anonymization Techniques | Implemented: deterministic `customer_id` (SHA-256), opaque IDs (SHA-256 + salt) for `lake_txn_id` / `lake_store_id`, generalization (ZIP3, hour buckets, txn-total bins), k=5 cell suppression, no consumer linkage in the lake | k=5 instead of k≥50. L-diversity not implemented. Differential privacy is a documented stub. |
| **§8.3 Tenant/Aggregate Isolation** | **Tenant tables hold full-granularity per-merchant data; the lake is computed per-query from tenant rows with the viewing merchant excluded and peers pseudonymized.** | Tenant isolation enforced via SQL filtering at the agent tool layer, not via authn/authz or separate databases. |
| §9 Real-Time Pipeline | (not built) — batch only | No latency targets enforced; data freshness is "whenever you ran make seed." |
| §10.1 Agent Architecture | One four-layer agent loop pattern. Tools, prompts, model inference, delivery. | Single LLM (Anthropic Claude); no per-agent model selection; no feedback/learning loop (§10.3). |
| §10.2 Conversational Business Advisor | **Shipped as the orchestrator**, which routes free-form questions to the four specialists below. | One of seven; the orchestrator is the entry point users actually type into. |
| §10.2 Pricing / Anomaly / Demand / Trade Area | **Built — four specialists** sharing a tool loop and the Headline → Evidence → Therefore → Caveats response contract. | Each runs on Haiku 4.5 with MAX_TURNS=10 and the same SELECT-only tenant + lake tools. |
| §10.2 Demand Forecasting / Segmentation / Payment Optimization | (not built) | Roadmap. Same architectural pattern as the four shipped — incremental, not architectural. |
| §10.3 Feedback & Learning Loop | (not built) | Roadmap. |
| §11 Merchant Use Cases | All three target segments (grocery, QSR, off-price retail) represented; demo questions span single-merchant analytics, peer benchmarking, and cross-merchant patterns | Five merchants in one metro, not an enterprise panel. |
| §12 Data Products & Monetization | (not represented) | Out of scope for a technical demo. |
| §14 Governance & Compliance | Demonstrated through the privacy engine: no PII at any stage, peer pseudonymization, no consumer linkage in the lake | Real compliance is more than tech architecture — out of scope. |

---

## Per-merchant peer mapping

The lake re-labels the four non-viewing merchants as `peer_a..peer_d`. Same-segment peers come first (so a grocery viewer sees two grocery peers as `peer_a`/`peer_b`); cross-segment peers follow alphabetically by underlying `merchant_id`.

| Viewing as | peer_a | peer_b | peer_c | peer_d |
|---|---|---|---|---|
| Kroger     | Acme   | Winn-Dixie | Taco Bell  | TJ Maxx |
| Acme       | Kroger | Winn-Dixie | Taco Bell  | TJ Maxx |
| Winn-Dixie | Acme   | Kroger     | Taco Bell  | TJ Maxx |
| Taco Bell  | Acme   | Kroger     | Winn-Dixie | TJ Maxx |
| TJ Maxx    | Acme   | Kroger     | Winn-Dixie | Taco Bell |

The mapping is the source-of-truth in `src/generate/parameters.PEER_MAPPING`; both the generator and the lake view-builders read from it. The agent never sees the underlying merchant_ids — that's intentional, peer privacy.

---

## Deferred items (post-demo roadmap)

If the demo lands and there's appetite to extend it, the priority order:

1. **Add the remaining three agents** from strategy doc §10.2 (demand forecasting, segmentation, payment optimization). The pattern is shared with the four shipped (system prompt + tools + loop + response contract); each is incremental work.
2. **Add merchants** beyond the initial five. The generation framework is parameterized; adding Shell (gas) or CVS (pharmacy) is a config + catalog file.
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
- *"The k-anonymity threshold is set to 5 instead of 50 because the demo dataset has 10,000 customers; with 50 the suppression would eliminate most of the data. In production, with millions of customers per region, k ≥ 50 works as specified."*
- *"Four of the seven agents from §10.2 are built — the orchestrator (Conversational Business Advisor) routes free-form questions to pricing, anomaly, demand, and trade-area specialists. The remaining three follow the same architectural pattern; the build cost is incremental rather than architectural."*

The demo's strategic claim is intact: a payments company can deliver merchants a private view of their own data alongside cross-merchant insights that no card network and no POS vendor can produce. That's what the strategy doc is selling, and that's what the demo demonstrates.
