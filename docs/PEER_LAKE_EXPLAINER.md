# From raw transactions to a safe peer lake, in plain English

This explains how we turn one merchant's raw sales data into a shared "peer lake"
that lets every merchant compare itself to its competitors — **without any merchant
ever seeing another's raw data.** It is written to match the code as it actually
runs today, not an ideal.

---

## 1. Why the lake exists

A grocer can see everything about its own stores. What it *can't* see is how its
prices, its baskets, or its foot traffic stack up against the store down the street —
because that data belongs to a competitor. A company sitting at the payment terminal
across many merchants is the one party that could answer that, if it could do so
safely.

The **peer lake** is how we do it safely. It is a curated, anonymized copy of
*everyone else's* purchase lines, prepared separately for each merchant, with enough
stripped out that no merchant can pull a competitor's real numbers, identity, or
customers back out of it — but enough left in that honest, like-for-like comparison
still works.

Two ideas do the heavy lifting:

- **Each merchant gets its own copy of the lake** with *its own rows removed* — so
  "the peers" a merchant sees never include itself.
- **A competitor is only ever "a peer"** — never a name, never even a stable
  nickname. You can see what same-segment competitors do *in aggregate*; you can
  never single one out.

---

## 2. The inputs (the raw data)

The lake is built from four raw tables (Parquet files under `data/raw/`):

| Table | What it holds |
|---|---|
| `transactions` | one row per visit: which store, which banner, timestamp, the payment details (card type, network, contactless, wallet), the total. |
| `transaction_items` | one row per item on a receipt — but only the bare essentials: a product code (`sku`), quantity, unit price, line total. **No product name and no category live here.** |
| `products` | the catalogue: for each `sku`, its name, price, and *two* sets of category labels (explained below). |
| `stores` | one row per store: its banner, its real neighborhood. |

Because the line item carries only a `sku`, everything descriptive about a product —
its name, its category — is looked up by joining to the `products` catalogue on that
`sku`.

**The two category systems.** Every product carries two hierarchies:

- **Functional** (`functional_department` / `category` / `subcategory`) — normalized,
  identical wording across all merchants. Both Acme's "White Milk" and Kroger's "Milk"
  map to the functional category **Milk**. This is the only sensible key for comparing
  merchants.
- **Merchant** (`merchant_department` / `category` / `subcategory`) — each banner's own
  shelf labels, the way *it* merchandises. These differ banner to banner.

**Only the functional labels ever go into the lake.** A merchant's own labels reveal
how it organizes its business and don't line up across merchants, so they are left
out by design.

---

## 3. How the lake is built, step by step

All of this lives in `src/lake/build_line_items.py`.

1. **Read only what's allowed.** Every raw read goes through a gatekeeper
   (`observable_guard.load_table`) that permits only an explicit allow-list of
   columns. Whole tables that describe *people* (`customers`) or planted profile data
   (`zones`) are forbidden outright, and the merchant-own category columns are simply
   never requested.

2. **Attach the shared categories.** The build joins each purchase line to the
   `products` catalogue on `sku` and pulls the **functional** department / category /
   subcategory. These are published under the plain names `department`, `category`,
   `subcategory` — the shared comparison key.

3. **Generalize the identifiers.** Real transaction and store IDs are replaced with
   one-way hashed tokens (SHA-256, shortened to 16 characters). You cannot run the
   token backwards to recover the original ID. (The hash uses a fixed seed, so the
   build is reproducible.)

4. **Blur the timing.** The exact timestamp is reduced to the **date** plus a coarse
   **time-of-day bucket** (the 24 hours collapse into 10 buckets). You can see "late
   morning," not "10:47am."

5. **Keep the real neighborhood.** Store geography is published as the real
   neighborhood name (e.g. "University City") — no street address, no latitude /
   longitude, no ZIP.

6. **Make it per-viewer.** Finally, for each of the six merchants, the build:
   - **drops that merchant's own rows entirely** (structural exclusion — they are
     never written to its copy);
   - labels every remaining row **`peer`** (a competitor in the *same* segment) or
     **`merchant`** (a different segment);
   - **strips the real `banner_code`** so no competitor can be identified.

   The result is two files per merchant under `data/lake/items/<MERCHANT>/`:
   `lake_transactions` (the peer purchase lines) and `lake_stores` (peer store
   reference — tokenized id, segment, neighborhood).

What a peer line ends up looking like: a hashed txn/line/store token, a date and
time-of-day bucket, the `peer`/`merchant` label, the functional
`department`/`category`/`subcategory`, the real `unit_price` / `qty` / `line_total`,
and the payment details — and nothing that ties it to a company, a store, or a shopper.

---

## 4. The privacy guardrails (and where each lives)

| Guardrail | What it does | Where |
|---|---|---|
| **Observable allow-list** | Only pre-approved columns are ever read from raw; `customers`/`zones` are forbidden; merchant-own category labels are never read. | `src/lake/observable_guard.py` (enforced at build time) |
| **You are absent from your own lake** | Each merchant's copy has its rows removed structurally — not filtered at query time. | `scope_lines_for_viewer` in `build_line_items.py` |
| **No competitor identity** | A peer is only `peer` or `merchant`; real banner codes are stripped; there are no per-competitor pseudonyms. | `build_line_items.py` |
| **No consumer linkage** | No customer id of any kind reaches the lake, so nothing threads a shopper across visits or merchants. | (the customer field is never selected) |
| **One-way IDs + blurred time** | Hashed tokens for ids; date + 10-bucket hour only. | `build_line_items.py` |
| **Functional-only taxonomy** | A merchant's own merchandising labels never leave its walls. | `build_line_items.py` |
| **Aggregates only, at query time** | A peer query must return grouped sums/averages/counts — raw individual lines are rejected. This is checked on the parsed SQL itself, not by trust. | `src/lake/lake_sql.py` |
| **k = 50 suppression floor** | Any result group backed by fewer than 50 underlying lines is dropped before the agent sees it, and the number dropped is reported back as `suppressed`. So no answer can rest on a tiny, potentially identifying handful of purchases. | `src/lake/lake_sql.py` |

The first six are baked in when the lake is built (the data simply isn't there to
leak). The last two are enforced every time a question is asked.

---

## 5. How an AI agent uses it

An agent answering a question runs **two separate queries and compares them** — there
is no step that merges raw peer data with own data:

- **`query_tenant`** — the merchant's *own* data, in full detail. Every such query is
  locked to that merchant (`WHERE banner_code = '<viewer>'`) and any mention of a
  different merchant is rejected.
- **`query_lake_sql`** — the *peer* lake, aggregating-only, with the k = 50 floor
  applied and the suppressed count surfaced.

The agent then reasons over both and states the comparison in prose, and every number
it states is checked back against a real query result before you see it.

**Which category system the agent uses depends on the question:**

- A question *only about the merchant's own data* ("what are my top categories?") uses
  the merchant's **own** labels — its real shelf taxonomy.
- Any *comparison to peers* uses the **functional** labels on both sides, because the
  lake publishes functional taxonomy and only functional labels line up across
  merchants. Comparing a merchant's "White Milk" bucket to the lake's "Milk" bucket
  would be comparing mismatched things — so the agent groups its own side on
  `functional_category` (or `functional_department`) to match the lake's
  `category` / `department`.

Own data can see *both* label systems; the lake only ever has functional.

---

## 6. What the lake deliberately cannot answer

Because identity and linkage are removed on purpose, some questions are out of scope
by design — and the agent will say so plainly rather than guess:

- **"Which shoppers buy at me *and* a competitor?"** — there is no customer thread in
  the lake, so cross-merchant shopper overlap can't be computed.
- **"What is *Acme* specifically charging for milk?"** — peers are reduced to the
  aggregate `peer` label, so no single competitor can be isolated. The agent can give
  the same-segment peer *average*, not a named competitor's figure.

These are not gaps to be fixed later; they are the point. The lake is built so that
the useful comparison survives and the unsafe lookup is impossible.
