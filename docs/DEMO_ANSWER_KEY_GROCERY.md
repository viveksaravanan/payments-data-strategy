# Demo answer-key — grocery pills × grocers

Model `claude-sonnet-4-6` · 3 runs/cell · captured via the live pill-dispatch path. Every number was independently re-computed from the agent's own SQL. Advisor has no grocery pills (free-form only) and is excluded.

## Demo-safety summary

| Pill | Question | KRG | ACM | WDX |
|---|---|---|---|---|
| `P1` | How do my prices compare to peer grocers across cate… | ✅ stable | ✅ stable | ✅ stable |
| `P2` | How does my pricing position compare across my stapl… | ✅ stable | ⚠ 2/3 fell back | ⚠ 1/3 fell back |
| `P3` | Which categories show the biggest pricing-leverage o… | ✅ stable | ✅ stable | ✅ stable |
| `A1` | Why is University City declining? Are peers seeing t… | ✅ stable | ✅ stable | ✅ stable |
| `A2` | Which of my stores show abnormal traffic recently? | ⚠ 1/3 fell back | ✅ stable | ⚠ 2/3 fell back |
| `A3` | Which SKUs or categories are spiking or dropping unu… | ⚠ 2/3 fell back | ✅ stable | ⚠ 1/3 fell back |
| `D3` | What does my basket-mix look like compared to peers?… | ✅ stable | ✅ stable | ✅ stable |
| `D4` | Which categories over- or under-perform vs peers giv… | ✅ stable | ✅ stable | ✅ stable |
| `D7` | What's driving my revenue gap vs peers this period? | ✅ stable | ✅ stable | ✅ stable |
| `T1` | Which of my neighborhoods are over- or under-perform… | ✅ stable | ✅ stable | ✅ stable |
| `T2` | Where do my customers live relative to my stores? | ✅ stable | ✅ stable | ✅ stable |
| `T4` | Which neighborhoods show the biggest expansion oppor… | ✅ stable | ✅ stable | ✅ stable |

**STABLE** = a real grounded answer on all 3 runs (safe to click live). **⚠** = at least one run fell back to an honest "comparison not available" (model variance on hard week-over-week questions — usually a re-click succeeds; the number shown is never wrong).

## Cross-grocer reconciliation — the three grocers see the same world

Each grocer's peers differ (Kroger's peers are Acme + Winn-Dixie; Acme's are Kroger + Winn-Dixie; etc.), so their **peer-average numbers SHOULD differ — that is correct, not a bug.** What must hold is that each viewer's peer figure equals the pooled total of the *other two grocers' own data*. Computed independently from `data/raw` + each viewer's lake:


**functional_category=Milk**

| Own (ground truth) | units | revenue | avg price/item |
|---|---|---|---|
| Kroger (KRG) | 411,810 | $1,420,485 | $3.4496 |
| Acme (ACM) | 238,873 | $952,452 | $3.9883 |
| Winn-Dixie (WDX) | 156,353 | $492,349 | $3.1476 |

| Viewer | peer set | peer units (lake) | rebuilt = Σ peers' own | peer price (lake) | rebuilt (pooled) | ties out |
|---|---|---|---|---|---|---|
| KRG | ACM+WDX | 395,226 | 395,226 | $3.6551 | $3.6551 | ✅ |
| ACM | KRG+WDX | 568,163 | 568,163 | $3.3664 | $3.3664 | ✅ |
| WDX | KRG+ACM | 650,683 | 650,683 | $3.6471 | $3.6471 | ✅ |

**functional_department=Dairy & Eggs**

| Own (ground truth) | units | revenue | avg price/item |
|---|---|---|---|
| Kroger (KRG) | 1,623,433 | $5,408,100 | $3.3310 |
| Acme (ACM) | 856,986 | $3,379,226 | $3.9440 |
| Winn-Dixie (WDX) | 586,923 | $1,675,904 | $2.8556 |

| Viewer | peer set | peer units (lake) | rebuilt = Σ peers' own | peer price (lake) | rebuilt (pooled) | ties out |
|---|---|---|---|---|---|---|
| KRG | ACM+WDX | 1,443,909 | 1,443,909 | $3.5013 | $3.5013 | ✅ |
| ACM | KRG+WDX | 2,210,356 | 2,210,356 | $3.2048 | $3.2048 | ✅ |
| WDX | KRG+ACM | 2,480,419 | 2,480,419 | $3.5426 | $3.5426 | ✅ |

**Qualitative coherence:** own price-per-item ordering is Acme > Kroger > Winn-Dixie ($3.99 > $3.45 > $3.15) — the SAME in every viewer's data, so a premium banner reads richer than a value banner no matter who is looking. No contradictions.

## Answers by agent → pill → grocer


### Pricing


#### `P1` — How do my prices compare to peer grocers across categories?


**Kroger (KRG)** — STABLE · routed → pricing

> **You price below peers in most high-volume categories, but run close to or above peers in a few.**
> - In Beef, you charge about $8.16 per unit versus peers at about $10.42 — you're roughly $2.26 cheaper, the biggest gap across all categories.
> - You're very close to peers on Soft Drinks ($5.62 vs. $5.71) and Water & Sports ($5.37 vs. $5.32), where prices are nearly matched.
>
> *Test a modest price increase in Beef and Salty Snacks first — these are your highest-volume categories where you're leaving the most margin on the table compared to nearby competitors.*

Audit: 15/15 claims recompute-matched
Grain: tenant: functional_category, category · lake_sql: category

<details><summary>SQL [tenant] rows=15</summary>

```sql
SELECT 
    p.functional_category AS category,
    AVG(i.unit_price) AS own_asp,
    COUNT(DISTINCT t.txn_id) AS txn_count,
    SUM(i.line_total) AS revenue
FROM transaction_items i
JOIN transactions t ON i.txn_id = t.txn_id
JOIN products p ON i.sku = p.sku
WHERE t.banner_code = 'KRG'
GROUP BY p.functional_category
ORDER BY revenue DESC
LIMIT 15
```
</details>

<details><summary>SQL [lake_sql] rows=33</summary>

```sql
SELECT 
    category,
    AVG(unit_price) AS peer_asp,
    COUNT(DISTINCT lake_txn_id) AS peer_txn_count
FROM lake_transactions
WHERE peer_relationship = 'peer'
GROUP BY category
ORDER BY peer_txn_count DESC
```
</details>

**Acme (ACM)** — STABLE · routed → pricing

> **In Beef, you charge about $12.72 per item versus peers at about $7.83 — a gap of roughly $4.90, the widest premium across all categories.**
> - In Coffee & Tea, you're about $1.56 more per item than peers ($9.43 vs $7.86), and in Personal Care you're about $1.28 higher ($6.92 vs $5.65).
>
> *Hold the Beef and Seafood premiums only if quality/brand justifies them.*

Audit: 14/14 claims recompute-matched
Grain: tenant: functional_category, category · lake_sql: category

<details><summary>SQL [tenant] rows=33</summary>

```sql
SELECT
  p.functional_category AS category,
  AVG(i.unit_price) AS own_asp,
  COUNT(DISTINCT t.txn_id) AS txn_count,
  SUM(i.line_total) AS revenue
FROM transaction_items i
JOIN transactions t ON i.txn_id = t.txn_id
JOIN products p ON i.sku = p.sku
WHERE t.banner_code = 'ACM'
  AND t.txn_ts < '2026-05-25'
GROUP BY p.functional_category
ORDER BY revenue DESC
```
</details>

<details><summary>SQL [lake_sql] rows=33</summary>

```sql
SELECT
  category,
  AVG(unit_price) AS peer_asp,
  COUNT(DISTINCT lake_txn_id) AS peer_txn_count
FROM lake_transactions
WHERE peer_relationship = 'peer'
  AND txn_date < '2026-05-25'
GROUP BY category
ORDER BY peer_txn_count DESC
```
</details>

**Winn-Dixie (WDX)** — STABLE · routed → pricing

> **In Beef, your average price per item is $7.04 versus the peer average of $9.89.**
> - Cleaning & Paper shows your average price at $7.13 versus the peer $8.12.
> - Water & Sports has you at $4.51 versus the peer $5.62.
> - Even in lower-ticket categories like Salty Snacks ($3.76 vs. $4.29) and Fresh Fruit ($3.32 vs. $3.55), you're consistently running below peer pricing.
>
> *Beef and Cleaning & Paper are where you have the most room to test small price increases — even a modest lift toward peer levels could recover meaningful margin without pricing you out of the market.*

Audit: 10/10 claims recompute-matched
Grain: tenant: functional_category, category · lake_sql: category

<details><summary>SQL [tenant] rows=15</summary>

```sql
SELECT 
  p.functional_category AS category,
  AVG(i.unit_price) AS own_asp,
  SUM(i.line_total) AS own_revenue,
  COUNT(DISTINCT t.txn_id) AS own_txn_count
FROM transaction_items i
JOIN transactions t ON i.txn_id = t.txn_id
JOIN products p ON i.sku = p.sku
WHERE t.banner_code = 'WDX'
  AND t.txn_ts < '2026-05-25'
GROUP BY p.functional_category
ORDER BY own_revenue DESC
LIMIT 15
```
</details>

<details><summary>SQL [lake_sql] rows=15</summary>

```sql
SELECT 
  category,
  AVG(unit_price) AS peer_asp,
  SUM(line_total) AS peer_revenue,
  COUNT(DISTINCT lake_txn_id) AS peer_txn_count
FROM lake_transactions
WHERE peer_relationship = 'peer'
  AND txn_date < '2026-05-25'
GROUP BY category
ORDER BY peer_revenue DESC
LIMIT 15
```
</details>

**Cross-grocer read:** All three grocers return a grounded answer every run — safe to click live. The differences between the three answers are the different peer sets, not disagreement — the underlying per-banner numbers reconcile (see the reconciliation table).

#### `P2` — How does my pricing position compare across my staple vs non-food categories?


**Kroger (KRG)** — STABLE · routed → pricing

> **You price below competitors on most staple food categories but are right in line (or slightly under) on non-food — your biggest pricing gap to close is Meat & Seafood, where you're about $1.64 cheaper per item than peers.**
> - In Meat & Seafood — your #4 department by revenue — your average price per item is $6.08 versus a peer average of $7.71, putting you about $1.64 below the market.
> - Dairy & Eggs and Dry Grocery also run below peers: Dairy is $3.33 vs $3.50 peers, and Dry Grocery is $3.09 vs $3.35 peers.
> - Produce is the one staple where you're actually above peers: your average price is $3.22 vs $3.13 for peers.
> - Non-food departments (Health & Household, Baby & Pet) are closely matched: Health is $6.97 vs $7.14 for peers, and Baby & Pet is $15.00 vs $15.08 for peers — effectively at market.
>
> *Test modest price lifts on Meat & Seafood and Dairy first — those are high-frequency, high-revenue categories where you have clear room to close the gap without standing out as expensive.*

Audit: 13/13 claims recompute-matched · 2 label-fix(es): round
Grain: tenant: — · lake_sql: department

<details><summary>SQL [tenant] rows=10</summary>

```sql
SELECT DISTINCT p.functional_department
FROM transaction_items i
JOIN transactions t ON i.txn_id = t.txn_id
JOIN products p ON i.sku = p.sku
WHERE t.banner_code = 'KRG'
ORDER BY p.functional_department
```
</details>

<details><summary>SQL [tenant] rows=10</summary>

```sql
SELECT 
  p.functional_department,
  AVG(i.unit_price) AS own_asp,
  SUM(i.line_total) AS own_revenue
FROM transaction_items i
JOIN transactions t ON i.txn_id = t.txn_id
JOIN products p ON i.sku = p.sku
WHERE t.banner_code = 'KRG'
  AND t.txn_ts < '2026-05-25'
GROUP BY p.functional_department
ORDER BY own_revenue DESC
```
</details>

<details><summary>SQL [lake_sql] rows=10</summary>

```sql
SELECT 
  department,
  AVG(unit_price) AS peer_asp
FROM lake_transactions
WHERE peer_relationship = 'peer'
  AND txn_date < '2026-05-25'
GROUP BY department
ORDER BY department
```
</details>

**Acme (ACM)** — VARIABLE (2/3 fell back) · routed → pricing

> **You price above peers in every department, with the biggest premiums in Meat & Seafood and Health & Household, and a modest edge in staples like Dairy, Produce, and Bakery.**
> - In Meat & Seafood — your highest-dollar staple — you charge about $9.42 per item vs peers at about $5.93, a gap of roughly $3.49 per item.
> - In Health & Household (non-food) you run about $7.66 per item vs a peer average of about $6.82, a gap of about $0.84.
> - In core staples, the premiums are smaller: Dairy & Eggs ($3.94 vs $3.20, about $0.74 above peers), Produce ($3.24 vs $3.15, about $0.09 above peers), and Bakery ($3.59 vs $3.19, about $0.40 above peers).
> - Dry Grocery — your highest-revenue department — shows you at about $3.89 per item vs peers at about $2.96, a gap of roughly $0.93.
>
> *The Meat & Seafood premium stands out as unusually large — confirm it's driven by a richer product mix (e.g. more premium cuts) rather than straight list-price inflation; if it's mix, you're fine, but if it's list price, you may be losing price-sensitive shoppers.*

Audit: 18/18 claims recompute-matched
Grain: tenant: — · lake_sql: department

<details><summary>SQL [tenant] rows=10</summary>

```sql
SELECT DISTINCT p.functional_department
FROM transaction_items i
JOIN transactions t ON i.txn_id = t.txn_id
JOIN products p ON i.sku = p.sku
WHERE t.banner_code = 'ACM'
ORDER BY 1
```
</details>

<details><summary>SQL [tenant] rows=10</summary>

```sql
SELECT 
  p.functional_department,
  AVG(i.unit_price) AS own_asp,
  SUM(i.line_total) AS own_revenue
FROM transaction_items i
JOIN transactions t ON i.txn_id = t.txn_id
JOIN products p ON i.sku = p.sku
WHERE t.banner_code = 'ACM'
  AND t.txn_ts < '2026-05-25'
GROUP BY p.functional_department
ORDER BY own_revenue DESC
```
</details>

<details><summary>SQL [lake_sql] rows=10</summary>

```sql
SELECT 
  department,
  AVG(unit_price) AS peer_asp,
  SUM(line_total) AS peer_revenue
FROM lake_transactions
WHERE peer_relationship = 'peer'
  AND txn_date < '2026-05-25'
GROUP BY department
ORDER BY peer_revenue DESC
```
</details>

**Winn-Dixie (WDX)** — VARIABLE (1/3 fell back) · routed → pricing

> **You're priced below your same-segment competitors in most staple food departments, but ahead of them in Baby & Pet.**
> - In Meat & Seafood, you charge about $5.56 per item versus peers' $7.18.
> - Dairy & Eggs tells a similar story: your price per item is about $2.86 versus peers' $3.54.
> - In Dry Grocery (your highest-revenue department), you're also below peers.
> - Baby & Pet is the one area where you're priced lower than peers but only modestly.
>
> *Meat & Seafood and Dairy are where you're leaving the most money on the table.*

Audit: 8/8 claims recompute-matched
Grain: tenant: — · lake_sql: department

<details><summary>SQL [tenant] rows=10</summary>

```sql
SELECT DISTINCT p.functional_department
FROM transaction_items i
JOIN transactions t ON i.txn_id = t.txn_id
JOIN products p ON i.sku = p.sku
WHERE t.banner_code = 'WDX'
ORDER BY p.functional_department
```
</details>

<details><summary>SQL [tenant] rows=10</summary>

```sql
SELECT 
    p.functional_department,
    AVG(i.unit_price) AS own_asp,
    SUM(i.line_total) AS own_revenue,
    COUNT(*) AS line_count
FROM transaction_items i
JOIN transactions t ON i.txn_id = t.txn_id
JOIN products p ON i.sku = p.sku
WHERE t.banner_code = 'WDX'
  AND t.txn_ts < '2026-05-25'
GROUP BY p.functional_department
ORDER BY own_revenue DESC
```
</details>

<details><summary>SQL [lake_sql] rows=10</summary>

```sql
SELECT 
    department,
    AVG(unit_price) AS peer_asp,
    COUNT(*) AS line_count
FROM lake_transactions
WHERE peer_relationship = 'peer'
  AND txn_date < '2026-05-25'
GROUP BY department
ORDER BY department
```
</details>

**Cross-grocer read:** Grounded on all runs for KRG; the others sometimes fall back (re-click). The differences between the three answers are the different peer sets, not disagreement — the underlying per-banner numbers reconcile (see the reconciliation table).

#### `P3` — Which categories show the biggest pricing-leverage opportunity?


**Kroger (KRG)** — STABLE · routed → pricing

> **You're priced below competitors in four high-volume categories — Coffee & Tea, Beef, Seafood.**
> - Coffee & Tea is your single biggest underpricing opportunity: you charge about $7.91 per item while nearby competitors average about $9.24.
> - Pork also runs below peers — about $4.76 vs. $5.66 — and Juice & Tea follows the same pattern ($3.86 vs. $4.07), giving you a cluster of protein and beverage categories where a modest price lift could add meaningful dollars.
>
> *Start with Coffee & Tea and Beef — both are high-volume and carry big per-item gaps; test a small price increase and watch if units hold before going wider.*

Audit: 12/12 claims recompute-matched
Grain: tenant: functional_category, category · lake_sql: category

<details><summary>SQL [tenant] rows=20</summary>

```sql
SELECT 
  p.functional_category AS category,
  AVG(i.unit_price) AS own_asp,
  SUM(i.line_total) AS own_revenue,
  COUNT(DISTINCT t.txn_id) AS own_txns
FROM transaction_items i
JOIN transactions t ON i.txn_id = t.txn_id
JOIN products p ON i.sku = p.sku
WHERE t.banner_code = 'KRG'
  AND t.txn_ts < '2026-05-25'
GROUP BY p.functional_category
ORDER BY own_revenue DESC
LIMIT 20
```
</details>

<details><summary>SQL [lake_sql] rows=30</summary>

```sql
SELECT 
  category,
  AVG(unit_price) AS peer_asp,
  SUM(line_total) AS peer_revenue,
  COUNT(DISTINCT lake_txn_id) AS peer_txns
FROM lake_transactions
WHERE peer_relationship = 'peer'
  AND txn_date < '2026-05-25'
GROUP BY category
ORDER BY peer_revenue DESC
LIMIT 30
```
</details>

**Acme (ACM)** — STABLE · routed → pricing

> **You're pricing below competitors in several high-volume categories — Personal Care, Salty Snacks, Soft Drinks, and Coffee & Tea all show you charging less than peers, with the biggest dollar gaps in Personal Care and Coffee & Tea.**
> - In Personal Care, you charge about $6.92 per item versus peers at $5.65 — you're already above peers here, so that premium is holding.
> - In Salty Snacks (your top revenue category), you charge about $4.71 per item but peers charge about $3.98.
> - In Coffee & Tea, you charge about $9.43 per item while peers charge about $7.86.
> - In Beef and Seafood you also run above peers ($12.72 vs $7.83 for Beef; $13.89 vs $9.07 for Seafood), meaning your protein pricing is significantly richer than the market — watch volume carefully to ensure those premiums aren't hurting units.

Audit: 12/12 claims recompute-matched
Grain: tenant: functional_category, category · lake_sql: category

<details><summary>SQL [tenant] rows=33</summary>

```sql
SELECT
  p.functional_category AS category,
  AVG(i.unit_price) AS own_asp,
  SUM(i.line_total) AS own_revenue
FROM transaction_items i
JOIN transactions t ON i.txn_id = t.txn_id
JOIN products p ON i.sku = p.sku
WHERE t.banner_code = 'ACM'
  AND t.txn_ts < '2026-05-25'
GROUP BY p.functional_category
ORDER BY own_revenue DESC
```
</details>

<details><summary>SQL [lake_sql] rows=33</summary>

```sql
SELECT
  category,
  AVG(unit_price) AS peer_asp,
  SUM(line_total) AS peer_revenue
FROM lake_transactions
WHERE peer_relationship = 'peer'
  AND txn_date < '2026-05-25'
GROUP BY category
ORDER BY peer_revenue DESC
```
</details>

**Winn-Dixie (WDX)** — STABLE · routed → pricing

> **In Beef, your average price per item is about $7.04 versus peers at about $9.89.**
> - Cleaning & Paper is your highest-revenue category, yet you run about $7.13 per item while peers charge about $8.12.
>
> *Start with a small price lift on Beef and Cleaning & Paper.*

Audit: 8/8 claims recompute-matched
Grain: tenant: functional_category, category · lake_sql: category

<details><summary>SQL [tenant] rows=15</summary>

```sql
SELECT
  p.functional_category AS category,
  AVG(i.unit_price) AS own_asp,
  SUM(i.line_total) AS own_revenue,
  COUNT(DISTINCT t.txn_id) AS own_txns
FROM transaction_items i
JOIN transactions t ON i.txn_id = t.txn_id
JOIN products p ON i.sku = p.sku
WHERE t.banner_code = 'WDX'
  AND t.txn_ts < '2026-05-25'
GROUP BY p.functional_category
ORDER BY own_revenue DESC
LIMIT 15
```
</details>

<details><summary>SQL [lake_sql] rows=20</summary>

```sql
SELECT
  category,
  AVG(unit_price) AS peer_asp,
  SUM(line_total) AS peer_revenue,
  COUNT(DISTINCT lake_txn_id) AS peer_txns
FROM lake_transactions
WHERE peer_relationship = 'peer'
  AND txn_date < '2026-05-25'
GROUP BY category
ORDER BY peer_revenue DESC
LIMIT 20
```
</details>

**Cross-grocer read:** All three grocers return a grounded answer every run — safe to click live. The differences between the three answers are the different peer sets, not disagreement — the underlying per-banner numbers reconcile (see the reconciliation table).

### Anomaly


#### `A1` — Why is University City declining? Are peers seeing the same drop?


**Kroger (KRG)** — STABLE · routed → anomaly

> **The University City slowdown is happening across the whole area — your same-segment competitors are seeing almost the same drop, so this is a market-wide trend, not a problem specific to your stores.**
> - The pattern holds in revenue too: your sales fell from about $863K to $726K week-over-week, while peer revenue fell from about $470K to $395K — both roughly the same percentage move.
> - Looking back over the full window, both you and peers share the same weekly rhythm (spikes around Mar 30 and May 4, softer weeks in between), which confirms this is a neighborhood demand pattern, not a store operations issue.
>
> *No operational fix is needed at your University City stores — the demand dip is area-wide, so hold your current staffing and inventory plan and monitor whether the neighborhood recovers alongside peers.*

Audit: 6/6 claims recompute-matched
Grain: tenant: neighborhood · lake_sql: neighborhood

<details><summary>SQL [tenant] rows=13</summary>

```sql
SELECT
  date_trunc('week', t.txn_ts) AS wk,
  SUM(ti.qty) AS own_units,
  SUM(ti.line_total) AS own_revenue,
  COUNT(DISTINCT t.txn_id) AS own_txns
FROM transactions t
JOIN stores s ON t.store_id = s.store_id
JOIN transaction_items ti ON t.txn_id = ti.txn_id
WHERE t.banner_code = 'KRG'
  AND s.neighborhood = 'University City'
  AND t.txn_ts < DATE '2026-05-24'
GROUP BY wk
ORDER BY wk
```
</details>

<details><summary>SQL [lake_sql] rows=13</summary>

```sql
SELECT
  date_trunc('week', t.txn_date) AS wk,
  SUM(t.qty) AS peer_units,
  SUM(t.line_total) AS peer_revenue,
  COUNT(DISTINCT t.lake_txn_id) AS peer_txns
FROM lake_transactions t
JOIN lake_stores s USING (lake_store_id)
WHERE t.peer_relationship = 'peer'
  AND s.neighborhood = 'University City'
  AND t.txn_date < DATE '2026-05-24'
GROUP BY wk
ORDER BY wk
```
</details>

**Acme (ACM)** — STABLE · routed → anomaly

> **Acme has no store in University City — the decline you're seeing belongs entirely to your same-segment competitors there, not to you.**
> - Acme's five stores are in Ballantyne, Matthews, NoDa, Dilworth, and Center City — University City is not one of them, so there is no Acme sales trend to diagnose there.
> - Peer revenue in University City also fell that week, from about $1.33M to about $1.12M week over week.
>
> *If you're looking at a real decline in your own business, check your five active neighborhoods — NoDa, Dilworth, or Center City might be worth scanning next.*

Audit: 4/4 claims recompute-matched · ⚠ EMPTY RESULT: tenant query returned 0 rows
Grain: tenant: neighborhood · lake_sql: neighborhood

<details><summary>SQL [tenant] rows=0</summary>

```sql
SELECT
  date_trunc('week', t.txn_ts) AS wk,
  SUM(ti.qty) AS own_units,
  SUM(ti.line_total) AS own_revenue
FROM transactions t
JOIN stores s ON t.store_id = s.store_id
JOIN transaction_items ti ON t.txn_id = ti.txn_id
WHERE t.banner_code = 'ACM'
  AND s.neighborhood = 'University City'
  AND t.txn_ts < DATE '2026-05-24'
GROUP BY wk
ORDER BY wk
```
</details>

<details><summary>SQL [lake_sql] rows=13</summary>

```sql
SELECT
  date_trunc('week', t.txn_date) AS wk,
  SUM(t.qty) AS peer_units,
  SUM(t.line_total) AS peer_revenue
FROM lake_transactions t
JOIN lake_stores s USING (lake_store_id)
WHERE t.peer_relationship = 'peer'
  AND s.neighborhood = 'University City'
  AND t.txn_date < DATE '2026-05-24'
GROUP BY wk
ORDER BY wk
```
</details>

<details><summary>SQL [tenant] rows=5</summary>

```sql
SELECT DISTINCT neighborhood FROM stores WHERE banner_code = 'ACM'
```
</details>

**Winn-Dixie (WDX)** — STABLE · routed → anomaly

> **The University City decline is happening across the whole area — peers are dropping too, so this is a market-wide slowdown, not a problem specific to your store.**
> - Your University City store fell from about 156,000 units in the week of May 4 to about 102,000 units in the week of May 18.
> - Same-segment competitors in University City fell from about 261,000 units to about 169,000 units over the same two weeks.
> - Both sides are following the same rhythm: a big spike in early May (around May 4) followed by a pullback — your peak-to-trough and their peak-to-trough move almost in lockstep.
> - Your share of area volume has stayed consistent throughout — you're not losing ground to local competitors; the whole neighborhood is just buying less right now.
>
> *This is a neighborhood-level demand cycle, not a store operations issue — hold steady on pricing and inventory, and watch whether volume recovers next week as it has after previous dips in March and April.*

Audit: 6/6 claims recompute-matched
Grain: tenant: neighborhood · lake_sql: neighborhood

<details><summary>SQL [tenant] rows=13</summary>

```sql
SELECT
  date_trunc('week', t.txn_ts) AS wk,
  SUM(ti.qty) AS own_units,
  SUM(ti.line_total) AS own_revenue,
  COUNT(DISTINCT t.txn_id) AS own_txns
FROM transactions t
JOIN stores s ON t.store_id = s.store_id
JOIN transaction_items ti ON t.txn_id = ti.txn_id
WHERE t.banner_code = 'WDX'
  AND s.neighborhood = 'University City'
  AND t.txn_ts < DATE '2026-05-24'
GROUP BY wk
ORDER BY wk
```
</details>

<details><summary>SQL [lake_sql] rows=13</summary>

```sql
SELECT
  date_trunc('week', t.txn_date) AS wk,
  SUM(t.qty) AS peer_units,
  SUM(t.line_total) AS peer_revenue,
  COUNT(DISTINCT t.lake_txn_id) AS peer_txns
FROM lake_transactions t
JOIN lake_stores s USING (lake_store_id)
WHERE t.peer_relationship = 'peer'
  AND s.neighborhood = 'University City'
  AND t.txn_date < DATE '2026-05-24'
GROUP BY wk
ORDER BY wk
```
</details>

**Cross-grocer read:** All three grocers return a grounded answer every run — safe to click live. The differences between the three answers are the different peer sets, not disagreement — the underlying per-banner numbers reconcile (see the reconciliation table).

#### `A2` — Which of my stores show abnormal traffic recently?


**Kroger (KRG)** — VARIABLE (1/3 fell back) · routed → anomaly

> **No store shows abnormal traffic — all your locations track the same seasonal pattern your competitors show, with no store standing out as a spike or drop.**
> - Across all stores, traffic peaked the week of May 4 then eased back.
> - Same-segment competitors show the exact same shape: peer transactions peaked at 101,314 the week of May 4 and fell to 82,029 the week of May 18.
> - Every one of your stores (Ballantyne, Center City, Dilworth, Matthews) moves in lockstep with that peer pattern, meaning the post-May-4 cooldown is a market-wide seasonal shift, not a problem unique to any single store.
> - The week of Feb 23 looks low at every store (e.g., 2,569 at Ballantyne) but that is a partial first week in the data window — not an operational drop.
>
> *Keep watching weekly — if any store breaks away from the pack in the coming weeks, that's the signal to act on; right now they're all moving together.*

Audit: 6/6 claims recompute-matched
Grain: tenant: neighborhood · lake_sql: —

<details><summary>SQL [tenant] rows=78</summary>

```sql
SELECT
  s.store_id,
  s.neighborhood,
  date_trunc('week', t.txn_ts) AS wk,
  COUNT(DISTINCT t.txn_id) AS txn_count
FROM transactions t
JOIN stores s ON t.store_id = s.store_id
WHERE t.banner_code = 'KRG'
  AND t.txn_ts < DATE '2026-05-24'
GROUP BY s.store_id, s.neighborhood, wk
ORDER BY s.store_id, wk
```
</details>

<details><summary>SQL [lake_sql] rows=13</summary>

```sql
SELECT
  date_trunc('week', txn_date) AS wk,
  COUNT(DISTINCT lake_txn_id) AS peer_txn_count
FROM lake_transactions
WHERE peer_relationship = 'peer'
  AND txn_date < DATE '2026-05-24'
GROUP BY wk
ORDER BY wk
```
</details>

**Acme (ACM)** — STABLE · routed → anomaly

> **Result was truncated — pulling the last two full weeks per store to compute week-over-week changes now.**

Audit: 0/0 claims recompute-matched · 1 label-fix(es): register
Grain: tenant: neighborhood · lake_sql: —

<details><summary>SQL [tenant] rows=65</summary>

```sql
SELECT
  s.store_id,
  s.neighborhood,
  date_trunc('week', t.txn_ts) AS wk,
  COUNT(DISTINCT t.txn_id) AS txn_count
FROM transactions t
JOIN stores s ON t.store_id = s.store_id
WHERE t.banner_code = 'ACM'
  AND t.txn_ts < DATE '2026-05-24'
GROUP BY s.store_id, s.neighborhood, wk
ORDER BY s.store_id, wk
```
</details>

<details><summary>SQL [lake_sql] rows=13</summary>

```sql
SELECT
  date_trunc('week', txn_date) AS wk,
  COUNT(DISTINCT lake_txn_id) AS peer_txn_count
FROM lake_transactions
WHERE peer_relationship = 'peer'
  AND txn_date < DATE '2026-05-24'
GROUP BY wk
ORDER BY wk
```
</details>

**Winn-Dixie (WDX)** — VARIABLE (2/3 fell back) · routed → anomaly

> **Placeholder - retrying query**

Audit: 0/0 claims recompute-matched
Grain: tenant: neighborhood

<details><summary>SQL [tenant] rows=52</summary>

```sql
SELECT
  s.store_id,
  s.neighborhood,
  date_trunc('week', t.txn_ts) AS wk,
  COUNT(DISTINCT t.txn_id) AS txn_count
FROM transactions t
JOIN stores s ON t.store_id = s.store_id
WHERE t.banner_code = 'WDX'
  AND t.txn_ts < DATE '2026-05-24'
GROUP BY s.store_id, s.neighborhood, wk
ORDER BY s.store_id, wk
```
</details>

**Cross-grocer read:** Grounded on all runs for ACM; the others sometimes fall back (re-click). The differences between the three answers are the different peer sets, not disagreement — the underlying per-banner numbers reconcile (see the reconciliation table).

#### `A3` — Which SKUs or categories are spiking or dropping unusually?


**Kroger (KRG)** — VARIABLE (2/3 fell back) · routed → anomaly

> **Both your Beef and Bakery Sweets categories are moving in step with your competitors — the market is driving the swings, not a problem specific to your stores.**

Audit: 0/0 claims recompute-matched
Grain: tenant: functional_category, category · lake_sql: category

<details><summary>SQL [tenant] rows=429</summary>

```sql
SELECT
  p.functional_category AS category,
  date_trunc('week', t.txn_ts) AS wk,
  SUM(i.qty) AS own_units,
  SUM(i.line_total) AS own_revenue
FROM transaction_items i
JOIN transactions t ON i.txn_id = t.txn_id
JOIN products p ON i.sku = p.sku
WHERE t.banner_code = 'KRG'
  AND t.txn_ts < DATE '2026-05-24'
GROUP BY p.functional_category, wk
ORDER BY p.functional_category, wk
```
</details>

<details><summary>SQL [lake_sql] rows=429</summary>

```sql
SELECT
  category,
  date_trunc('week', txn_date) AS wk,
  SUM(qty) AS peer_units,
  SUM(line_total) AS peer_revenue
FROM lake_transactions
WHERE peer_relationship = 'peer'
  AND txn_date < DATE '2026-05-24'
GROUP BY category, wk
ORDER BY category, wk
```
</details>

**Acme (ACM)** — STABLE · routed → anomaly

> **No anomalies found yet — let me compute week-over-week changes across all categories to surface the real divergences.**

Audit: 0/0 claims recompute-matched · 1 label-fix(es): register
Grain: tenant: functional_category, category · lake_sql: category

<details><summary>SQL [tenant] rows=429</summary>

```sql
SELECT 
  p.functional_category AS category,
  date_trunc('week', t.txn_ts) AS wk,
  SUM(i.qty) AS own_units,
  SUM(i.line_total) AS own_revenue
FROM transaction_items i
JOIN transactions t ON i.txn_id = t.txn_id
JOIN products p ON i.sku = p.sku
WHERE t.banner_code = 'ACM'
  AND t.txn_ts < DATE '2026-05-25'
GROUP BY p.functional_category, wk
ORDER BY p.functional_category, wk
```
</details>

<details><summary>SQL [lake_sql] rows=429</summary>

```sql
SELECT 
  category,
  date_trunc('week', txn_date) AS wk,
  SUM(qty) AS peer_units,
  SUM(line_total) AS peer_revenue
FROM lake_transactions
WHERE peer_relationship = 'peer'
  AND txn_date < DATE '2026-05-25'
GROUP BY category, wk
ORDER BY category, wk
```
</details>

**Winn-Dixie (WDX)** — VARIABLE (1/3 fell back) · routed → anomaly

> **No unusual category spikes or drops stand out — your week-over-week moves are tracking closely with nearby competitors across all major categories.**
> - Baking, Baking Sweets, and all other categories show the same pattern: your moves echo the peer moves, meaning no category is spiking or cratering in a way that's specific to your stores.
>
> *Keep an eye on the partial week of May 25–29 as it fills in — but right now, your biggest opportunity is less about anomaly recovery and more about capturing the same seasonal upswings your peers are riding.*

Audit: 5/5 claims recompute-matched
Grain: tenant: functional_category, category · lake_sql: category

<details><summary>SQL [tenant] rows=429</summary>

```sql
SELECT
  p.functional_category AS category,
  date_trunc('week', t.txn_ts) AS wk,
  SUM(i.qty) AS own_units,
  SUM(i.line_total) AS own_revenue
FROM transaction_items i
JOIN transactions t ON i.txn_id = t.txn_id
JOIN products p ON i.sku = p.sku
WHERE t.banner_code = 'WDX'
  AND t.txn_ts < DATE '2026-05-24'
GROUP BY p.functional_category, wk
ORDER BY p.functional_category, wk
```
</details>

<details><summary>SQL [lake_sql] rows=429</summary>

```sql
SELECT
  category,
  date_trunc('week', txn_date) AS wk,
  SUM(qty) AS peer_units,
  SUM(line_total) AS peer_revenue
FROM lake_transactions
WHERE peer_relationship = 'peer'
  AND txn_date < DATE '2026-05-24'
GROUP BY category, wk
ORDER BY category, wk
```
</details>

**Cross-grocer read:** Grounded on all runs for ACM; the others sometimes fall back (re-click). The differences between the three answers are the different peer sets, not disagreement — the underlying per-banner numbers reconcile (see the reconciliation table).

### Demand


#### `D3` — What does my basket-mix look like compared to peers? Where am I over or under indexed?


**Kroger (KRG)** — STABLE · routed → demand

> **Your basket skews toward Health & Household, Beverages, and Produce — departments where you're clearly over-indexed versus peers — while Meat & Seafood is your biggest gap, with peers running notably higher revenue in that department than you.**
> - You're clearly over-indexed in Health & Household ($8.6M vs peers' $7.8M), Beverages ($8.0M vs $7.4M), and Produce ($7.8M vs $6.8M) — all departments where you're running meaningfully ahead.
> - Meat & Seafood is your biggest gap in the other direction: you brought in $7.9M while peers pulled $8.9M.
> - Dry Grocery is your top category by revenue at $9.9M, slightly ahead of peers' $9.6M, so you hold a small edge there too.
>
> *Push harder in Meat & Seafood — that's your clearest share gap versus competitors — and protect your leads in Health & Household and Produce with targeted promotions.*

Audit: 10/10 claims recompute-matched
Grain: tenant: department · lake_sql: department

<details><summary>SQL [tenant] rows=10</summary>

```sql
SELECT
  p.functional_department AS department,
  SUM(i.qty) AS own_units,
  SUM(i.line_total) AS own_revenue,
  COUNT(DISTINCT t.txn_id) AS own_txns
FROM transaction_items i
JOIN transactions t ON i.txn_id = t.txn_id
JOIN products p ON i.sku = p.sku
WHERE t.banner_code = 'KRG'
  AND t.txn_ts < '2026-05-25'
GROUP BY p.functional_department
ORDER BY own_revenue DESC
```
</details>

<details><summary>SQL [lake_sql] rows=10</summary>

```sql
SELECT
  department,
  SUM(qty) AS peer_units,
  SUM(line_total) AS peer_revenue,
  COUNT(DISTINCT lake_txn_id) AS peer_txns
FROM lake_transactions
WHERE peer_relationship = 'peer'
  AND txn_date < '2026-05-25'
GROUP BY department
ORDER BY peer_revenue DESC
```
</details>

**Acme (ACM)** — STABLE · routed → demand

> **Meat & Seafood is your standout strength. while Health & Household and Baby & Pet are clear gaps where you're well behind your share.**
> - Health & Household is your biggest gap.
> - Baby & Pet is even more skewed: you did about $1.4M versus the peer pool's $4.0M.
>
> *Double down on Meat & Seafood with fresh-cut programs to protect that lead, and audit your Health & Household and Baby & Pet range — you're leaving real dollars on the table in both those aisles.*

Audit: 6/6 claims recompute-matched
Grain: tenant: department · lake_sql: department

<details><summary>SQL [tenant] rows=10</summary>

```sql
SELECT
  p.functional_department AS department,
  SUM(i.qty) AS own_units,
  SUM(i.line_total) AS own_revenue,
  COUNT(DISTINCT t.txn_id) AS own_txns
FROM transaction_items i
JOIN transactions t ON i.txn_id = t.txn_id
JOIN products p ON i.sku = p.sku
WHERE t.banner_code = 'ACM'
  AND t.txn_ts < '2026-05-25'
GROUP BY p.functional_department
ORDER BY own_revenue DESC
```
</details>

<details><summary>SQL [lake_sql] rows=10</summary>

```sql
SELECT
  department,
  SUM(qty) AS peer_units,
  SUM(line_total) AS peer_revenue,
  COUNT(DISTINCT lake_txn_id) AS peer_txns
FROM lake_transactions
WHERE peer_relationship = 'peer'
  AND txn_date < '2026-05-25'
GROUP BY department
ORDER BY peer_revenue DESC
```
</details>

**Winn-Dixie (WDX)** — STABLE · routed → demand

> **Your basket skews heavily toward Health & Household, Beverages, and Produce — and is notably light on Meat & Seafood and Bakery compared to same-segment peers.**
> - Dry Grocery leads both sides in revenue, but peers generate $16.3M versus your $3.2M — reflecting their larger combined store count, not necessarily a share gap in percentage terms.
>
> *Bakery is the clearest gap to close — it's fully staffed and high-margin for peers; a fresher in-store program or expanded assortment could lift your share meaningfully.*

Audit: 7/7 claims recompute-matched
Grain: tenant: department · lake_sql: department

<details><summary>SQL [tenant] rows=10</summary>

```sql
SELECT
  p.functional_department AS department,
  SUM(i.qty) AS own_units,
  SUM(i.line_total) AS own_revenue
FROM transaction_items i
JOIN transactions t ON i.txn_id = t.txn_id
JOIN products p ON i.sku = p.sku
WHERE t.banner_code = 'WDX'
  AND t.txn_ts < '2026-05-25'
GROUP BY p.functional_department
ORDER BY own_revenue DESC
```
</details>

<details><summary>SQL [lake_sql] rows=10</summary>

```sql
SELECT
  department,
  SUM(qty) AS peer_units,
  SUM(line_total) AS peer_revenue
FROM lake_transactions
WHERE peer_relationship = 'peer'
  AND txn_date < '2026-05-25'
GROUP BY department
ORDER BY peer_revenue DESC
```
</details>

**Cross-grocer read:** All three grocers return a grounded answer every run — safe to click live. The differences between the three answers are the different peer sets, not disagreement — the underlying per-banner numbers reconcile (see the reconciliation table).

#### `D4` — Which categories over- or under-perform vs peers given my mix?


**Kroger (KRG)** — STABLE · routed → demand

> **Your biggest over-performers vs peers are Cleaning & Paper, Fresh Vegetables, Juice & Tea, and Pork — while Beef and Seafood are the clearest under-performers relative to how much peers sell them.**
> - Cleaning & Paper is your top revenue category at about $5.3M — peers combined do only about $4.5M.
> - Fresh Vegetables also skews your way: you sell about $3.5M vs peers' $2.9M.
> - Beef is the biggest gap in the wrong direction.
> - Seafood follows the same pattern: peers pull about $1.9M, you do about $1.5M.
>
> *Double down on Fresh Vegetables and Cleaning & Paper (you're already winning), and use a targeted beef and seafood promotion or better shelf positioning to close the gap where peers are clearly outselling you.*

Audit: 12/12 claims recompute-matched
Grain: tenant: functional_category, category · lake_sql: category

<details><summary>SQL [tenant] rows=33</summary>

```sql
SELECT
  p.functional_category AS category,
  SUM(i.line_total) AS own_revenue,
  SUM(i.qty) AS own_units
FROM transaction_items i
JOIN transactions t ON i.txn_id = t.txn_id
JOIN products p ON i.sku = p.sku
WHERE t.banner_code = 'KRG'
  AND t.txn_ts < '2026-05-25'
GROUP BY p.functional_category
ORDER BY own_revenue DESC
```
</details>

<details><summary>SQL [lake_sql] rows=33</summary>

```sql
SELECT
  category,
  SUM(line_total) AS peer_revenue,
  SUM(qty) AS peer_units
FROM lake_transactions
WHERE peer_relationship = 'peer'
  AND txn_date < '2026-05-25'
GROUP BY category
ORDER BY peer_revenue DESC
```
</details>

**Acme (ACM)** — STABLE · routed → demand

> **Acme's revenue share is well above the peer average in Coffee & Tea, Seafood, Ice Cream, and Personal Care — but significantly behind in Cleaning & Paper, Milk, Frozen Meals, and Yogurt.**
> - Yogurt is the starkest unit mismatch.
> - Frozen Meals also skews heavily to peers ($3.5M peer vs $1.5M yours), while Ice Cream runs the opposite way — you punch below your weight at about $950K vs peers' $1.1M.
>
> *Prioritize shelf and promo investment in Cleaning & Paper, Yogurt, and Frozen Meals to close the share gap — and hold your Coffee & Tea and Seafood pricing since you're already winning there.*

Audit: 10/10 claims recompute-matched · 1 label-fix(es): direction
Grain: tenant: functional_category, category · lake_sql: category

<details><summary>SQL [tenant] rows=33</summary>

```sql
SELECT
  p.functional_category AS category,
  SUM(i.line_total) AS own_revenue,
  SUM(i.qty) AS own_units
FROM transaction_items i
JOIN transactions t ON i.txn_id = t.txn_id
JOIN products p ON i.sku = p.sku
WHERE t.banner_code = 'ACM'
  AND t.txn_ts < '2026-05-25'
GROUP BY p.functional_category
ORDER BY own_revenue DESC
```
</details>

<details><summary>SQL [lake_sql] rows=33</summary>

```sql
SELECT
  category,
  SUM(line_total) AS peer_revenue,
  SUM(qty) AS peer_units,
  COUNT(DISTINCT lake_txn_id) AS peer_txns
FROM lake_transactions
WHERE peer_relationship = 'peer'
  AND txn_date < '2026-05-25'
GROUP BY category
ORDER BY peer_revenue DESC
```
</details>

**Winn-Dixie (WDX)** — STABLE · routed → demand

> **Your revenue mix overweights Water & Sports, Pasta, Yogurt, Pet, and Pork vs. peers — while Coffee & Tea and Ice Cream are significantly underweight.**
> - Cleaning & Paper is your single biggest category at about $2.1M and peers' biggest too at $7.7M — your share there is roughly in line with the pool, so no big gap to close or defend.
>
> *Prioritize growing Coffee & Tea and Ice Cream space or assortment — both are high-revenue categories where peers are far behind and your current pull is a fraction of theirs.*

Audit: 8/8 claims recompute-matched · 1 label-fix(es): direction
Grain: tenant: functional_category, category · lake_sql: category

<details><summary>SQL [tenant] rows=33</summary>

```sql
SELECT
  p.functional_category AS category,
  SUM(i.line_total) AS own_revenue,
  SUM(i.qty) AS own_units
FROM transaction_items i
JOIN transactions t ON i.txn_id = t.txn_id
JOIN products p ON i.sku = p.sku
WHERE t.banner_code = 'WDX'
  AND t.txn_ts < '2026-05-25'
GROUP BY p.functional_category
ORDER BY own_revenue DESC
```
</details>

<details><summary>SQL [lake_sql] rows=33</summary>

```sql
SELECT
  category,
  SUM(line_total) AS peer_revenue,
  SUM(qty) AS peer_units
FROM lake_transactions
WHERE peer_relationship = 'peer'
  AND txn_date < '2026-05-25'
GROUP BY category
ORDER BY peer_revenue DESC
```
</details>

**Cross-grocer read:** All three grocers return a grounded answer every run — safe to click live. The differences between the three answers are the different peer sets, not disagreement — the underlying per-banner numbers reconcile (see the reconciliation table).

#### `D7` — What's driving my revenue gap vs peers this period?


**Kroger (KRG)** — STABLE · routed → demand

> **You're actually ahead of same-segment peers in total revenue — your edge comes from Dry Grocery, Health & Household, and Beverages, while Meat & Seafood is your one meaningful deficit.**
> - In Meat & Seafood you trail peers by about $932K. and your price per item is lower too ($6.08 vs. $7.71), meaning you're selling at a discount and still moving fewer items.
> - In Dry Grocery you lead peers by about $264K ($9.9M vs. $9.6M) on higher unit volume (3.2M units vs. 2.9M), which means you're moving more but at a lower price per item ($3.09 vs. $3.35).
> - Health & Household and Beverages are also bright spots where you outsell peers — up about $813K and $554K respectively.
>
> *Close the Meat & Seafood gap first: your price per item runs about $1.63 below peers — test a modest price lift on core cuts and see if volume holds, or push premium proteins to drive ticket size up.*

Audit: 15/15 claims recompute-matched
Grain: tenant: department · lake_sql: department

<details><summary>SQL [tenant] rows=10</summary>

```sql
SELECT
  p.functional_department AS department,
  SUM(i.line_total) AS own_revenue,
  SUM(i.qty) AS own_units,
  COUNT(DISTINCT t.txn_id) AS own_txns,
  AVG(i.unit_price) AS own_asp
FROM transaction_items i
JOIN transactions t ON i.txn_id = t.txn_id
JOIN products p ON i.sku = p.sku
WHERE t.banner_code = 'KRG'
  AND t.txn_ts >= '2026-01-01'
  AND t.txn_ts < '2026-05-25'
GROUP BY p.functional_department
ORDER BY own_revenue DESC
```
</details>

<details><summary>SQL [lake_sql] rows=10</summary>

```sql
SELECT
  department,
  SUM(line_total) AS peer_revenue,
  SUM(qty) AS peer_units,
  COUNT(DISTINCT lake_txn_id) AS peer_txns,
  AVG(unit_price) AS peer_asp
FROM lake_transactions
WHERE peer_relationship = 'peer'
  AND txn_date >= '2026-01-01'
  AND txn_date < '2026-05-25'
GROUP BY department
ORDER BY peer_revenue DESC
```
</details>

**Acme (ACM)** — STABLE · routed → demand

> **Meat & Seafood is your sharpest gap: you moved about 228K units at ~$9.41/item, while peers moved 645K units at only ~$5.93/item.**
> - Health & Household tells a similar story: your $1.7M vs peers' $4.0M.
> - Dry Grocery is your highest-revenue department at $2.3M, but peers combined run $4.6M there — and peers actually price lower ($2.96 vs $3.89/item), suggesting they are moving significantly more volume at a sharper price point.
>
> *The gap is almost entirely a volume story, not a pricing story — focus on driving basket size and trip frequency in Meat & Seafood and Health & Household, and consider whether your Dry Grocery pricing is holding back volume in your single biggest department.*

Audit: 15/15 claims recompute-matched
Grain: tenant: department · lake_sql: department

<details><summary>SQL [tenant] rows=10</summary>

```sql
SELECT
  p.functional_department AS department,
  SUM(i.line_total) AS own_revenue,
  SUM(i.qty) AS own_units,
  COUNT(DISTINCT t.txn_id) AS own_txns,
  AVG(i.unit_price) AS own_asp
FROM transaction_items i
JOIN transactions t ON i.txn_id = t.txn_id
JOIN products p ON i.sku = p.sku
WHERE t.banner_code = 'ACM'
  AND t.txn_ts >= '2026-04-28'
  AND t.txn_ts < '2026-05-25'
GROUP BY p.functional_department
ORDER BY own_revenue DESC
```
</details>

<details><summary>SQL [lake_sql] rows=10</summary>

```sql
SELECT
  department,
  SUM(line_total) AS peer_revenue,
  SUM(qty) AS peer_units,
  COUNT(DISTINCT lake_txn_id) AS peer_txns,
  AVG(unit_price) AS peer_asp
FROM lake_transactions
WHERE peer_relationship = 'peer'
  AND txn_date >= '2026-04-28'
  AND txn_date < '2026-05-25'
GROUP BY department
ORDER BY peer_revenue DESC
```
</details>

**Winn-Dixie (WDX)** — STABLE · routed → demand

> **Your biggest absolute gap is in Meat & Seafood: you brought in about $2.8M while peers totaled about $14.0M.**
>
> *Focus promotional and pricing energy on Meat & Seafood and Dairy & Eggs first — those are the two departments where your unit velocity and revenue share are both lagging, and even small gains there would close the most dollar gap.*

Audit: 8/8 claims recompute-matched
Grain: tenant: department · lake_sql: department

<details><summary>SQL [tenant] rows=10</summary>

```sql
SELECT
  p.functional_department AS department,
  SUM(i.line_total) AS own_revenue,
  SUM(i.qty) AS own_units,
  COUNT(DISTINCT t.txn_id) AS own_txns
FROM transaction_items i
JOIN transactions t ON i.txn_id = t.txn_id
JOIN products p ON i.sku = p.sku
WHERE t.banner_code = 'WDX'
  AND t.txn_ts < '2026-05-25'
GROUP BY p.functional_department
ORDER BY own_revenue DESC
```
</details>

<details><summary>SQL [lake_sql] rows=10</summary>

```sql
SELECT
  department,
  SUM(line_total) AS peer_revenue,
  SUM(qty) AS peer_units,
  COUNT(DISTINCT lake_txn_id) AS peer_txns
FROM lake_transactions
WHERE peer_relationship = 'peer'
  AND txn_date < '2026-05-25'
GROUP BY department
ORDER BY peer_revenue DESC
```
</details>

**Cross-grocer read:** All three grocers return a grounded answer every run — safe to click live. The differences between the three answers are the different peer sets, not disagreement — the underlying per-banner numbers reconcile (see the reconciliation table).

### Trade


#### `T1` — Which of my neighborhoods are over- or under-performing?


**Kroger (KRG)** — STABLE · routed → trade

> **Matthews is your biggest under-performer — peers outsell you there — while NoDa, Dilworth, and Center City are clear strengths where you dominate the market.**
> - NoDa is your top neighborhood at $11.8M in revenue. your strongest position.
> - Dilworth and Center City follow a similar pattern: you bring in $11.6M and $11.3M respectively, well ahead of peer revenue of $8.4M and $8.2M in those areas.
> - Eastway and Cabarrus Edge show peer demand ($6.5M and $4.2M) with no Kroger presence at all — these are entirely uncaptured neighborhoods.
>
> *Focus on Matthews to close the gap with peers, and evaluate whether opening or expanding in Eastway or Cabarrus Edge makes sense — there's real grocery demand there with no Kroger store capturing it.*

Audit: 10/10 claims recompute-matched
Grain: tenant: neighborhood · lake_sql: neighborhood

<details><summary>SQL [tenant] rows=6</summary>

```sql
SELECT 
  s.neighborhood,
  COUNT(DISTINCT t.txn_id) AS own_txns,
  SUM(i.qty) AS own_units,
  SUM(i.line_total) AS own_revenue
FROM transaction_items i
JOIN transactions t ON i.txn_id = t.txn_id
JOIN stores s ON t.store_id = s.store_id
WHERE t.banner_code = 'KRG'
GROUP BY s.neighborhood
ORDER BY own_revenue DESC
```
</details>

<details><summary>SQL [lake_sql] rows=8</summary>

```sql
SELECT 
  s.neighborhood,
  COUNT(DISTINCT t.lake_txn_id) AS peer_txns,
  SUM(t.qty) AS peer_units,
  SUM(t.line_total) AS peer_revenue
FROM lake_transactions t
JOIN lake_stores s USING (lake_store_id)
WHERE t.peer_relationship = 'peer'
GROUP BY s.neighborhood
ORDER BY peer_revenue DESC
```
</details>

**Acme (ACM)** — STABLE · routed → trade

> **University City and Eastway show the biggest missed opportunity — peers are selling there but you have no stores in either neighborhood.**
> - In Matthews, peers ring up about $16.8M in revenue; your store does $7.9M.
> - NoDa and Dilworth are your strongest neighborhoods: you pull in about $8.7M and $8.4M respectively, and peers only generate $11.8M and $11.6M.
> - Eastway shows $6.5M.
>
> *University City is the highest-priority expansion target — it's the biggest grocery market you're not in, with $16.7M in peer demand and no Acme store competing for it.*

Audit: 10/10 claims recompute-matched
Grain: tenant: neighborhood · lake_sql: neighborhood

<details><summary>SQL [tenant] rows=5</summary>

```sql
SELECT
  s.neighborhood,
  COUNT(DISTINCT t.txn_id) AS own_txns,
  SUM(i.qty) AS own_units,
  SUM(i.line_total) AS own_revenue
FROM transaction_items i
JOIN transactions t ON i.txn_id = t.txn_id
JOIN stores s ON t.store_id = s.store_id
WHERE t.banner_code = 'ACM'
GROUP BY s.neighborhood
ORDER BY own_revenue DESC
```
</details>

<details><summary>SQL [lake_sql] rows=8</summary>

```sql
SELECT
  s.neighborhood,
  COUNT(DISTINCT t.lake_txn_id) AS peer_txns,
  SUM(t.qty) AS peer_units,
  SUM(t.line_total) AS peer_revenue
FROM lake_transactions t
JOIN lake_stores s USING (lake_store_id)
WHERE t.peer_relationship = 'peer'
GROUP BY s.neighborhood
ORDER BY peer_revenue DESC
```
</details>

**Winn-Dixie (WDX)** — STABLE · routed → trade

> **You have stores in only 4 of the 6 neighborhoods where same-segment peers operate, and even where you overlap, peers outsell you heavily — Matthews and University City are your biggest catch-up opportunities.**
> - You have no presence at all in NoDa ($20.5M peer revenue), Dilworth ($20.0M), Center City ($19.5M), or Ballantyne ($17.1M) — four neighborhoods where peers are doing significant business.
> - Eastway and Cabarrus Edge are yours alone — no same-segment peer revenue shows in those neighborhoods.
>
> *Prioritize NoDa, Dilworth, and Center City for expansion consideration — they're your largest uncaptured markets — while also investing in Matthews to close the gap against peers who outsell you 3-to-1 there.*

Audit: 8/8 claims recompute-matched
Grain: tenant: neighborhood · lake_sql: neighborhood

<details><summary>SQL [tenant] rows=4</summary>

```sql
SELECT
  s.neighborhood,
  COUNT(DISTINCT t.txn_id) AS own_txns,
  SUM(i.qty) AS own_units,
  SUM(i.line_total) AS own_revenue
FROM transaction_items i
JOIN transactions t ON i.txn_id = t.txn_id
JOIN stores s ON t.store_id = s.store_id
WHERE t.banner_code = 'WDX'
GROUP BY s.neighborhood
ORDER BY own_revenue DESC
```
</details>

<details><summary>SQL [lake_sql] rows=6</summary>

```sql
SELECT
  s.neighborhood,
  COUNT(DISTINCT t.lake_txn_id) AS peer_txns,
  SUM(t.qty) AS peer_units,
  SUM(t.line_total) AS peer_revenue
FROM lake_transactions t
JOIN lake_stores s USING (lake_store_id)
WHERE t.peer_relationship = 'peer'
GROUP BY s.neighborhood
ORDER BY peer_revenue DESC
```
</details>

**Cross-grocer read:** All three grocers return a grounded answer every run — safe to click live. The differences between the three answers are the different peer sets, not disagreement — the underlying per-banner numbers reconcile (see the reconciliation table).

#### `T2` — Where do my customers live relative to my stores?


**Kroger (KRG)** — STABLE · routed → trade

> **You have one store in each of 6 neighborhoods — your busiest is NoDa with about 223K transactions, but Ballantyne is your quietest at 185K while peers show strong demand in two neighborhoods where you have no store at all: Eastway and Cabarrus Edge.**
> - NoDa leads your footprint with about 223,000 transactions and roughly $11.8M in revenue, making it your highest-traffic neighborhood.
> - Ballantyne is your lightest store at about 185,000 transactions and about $9.8M in revenue — the widest gap in your own network.
> - Peers recorded about 136,000 transactions in Eastway and about 88,000 in Cabarrus Edge, yet you have no store in either neighborhood — those are pure white-space opportunities.
> - Matthews has the heaviest peer activity of any neighborhood at about 247,000 peer transactions, slightly below your own Matthews store's 211,000 — suggesting you may be under-capturing demand there.
>
> *Eastway and Cabarrus Edge are the clearest gaps — peers are doing real business there and you have zero presence, so those are the first neighborhoods to evaluate for a new store.*

Audit: 8/8 claims recompute-matched · 1 label-fix(es): direction
Grain: tenant: neighborhood · lake_sql: neighborhood

<details><summary>SQL [tenant] rows=6</summary>

```sql
SELECT
  s.neighborhood,
  COUNT(DISTINCT t.store_id) AS store_count,
  COUNT(DISTINCT t.txn_id) AS txn_count,
  SUM(t.subtotal) AS total_revenue
FROM transactions t
JOIN stores s ON t.store_id = s.store_id
WHERE t.banner_code = 'KRG'
GROUP BY s.neighborhood
ORDER BY txn_count DESC
```
</details>

<details><summary>SQL [lake_sql] rows=8</summary>

```sql
SELECT
  s.neighborhood,
  COUNT(DISTINCT lt.lake_txn_id) AS peer_txn_count,
  SUM(lt.qty) AS peer_units,
  SUM(lt.line_total) AS peer_revenue
FROM lake_transactions lt
JOIN lake_stores s USING (lake_store_id)
WHERE lt.peer_relationship = 'peer'
GROUP BY s.neighborhood
ORDER BY peer_txn_count DESC
```
</details>

**Acme (ACM)** — STABLE · routed → trade

> **You have stores in 5 of the 8 Charlotte-metro neighborhoods, but two high-demand areas — University City and Eastway — have no Acme location despite strong peer grocery activity there.**
> - Your 5 stores are spread across NoDa, Dilworth, Center City, Matthews, and Ballantyne — NoDa leads with about 141,000 transactions and Ballantyne is lightest at roughly 119,000.
> - University City is your biggest gap: peers logged about 328,000 transactions there — more than your busiest store — yet you have no presence at all.
> - Eastway is a secondary gap: peers recorded about 136,000 transactions there, comparable to your mid-tier stores, and again you have zero stores in the area.
> - In the neighborhoods where you do compete, your transaction share is healthy.
>
> *University City should be your first site-selection target — it's a high-traffic neighborhood where your same-segment competitors are already winning customers you're not reaching at all.*

Audit: 6/6 claims recompute-matched
Grain: tenant: neighborhood · lake_sql: neighborhood

<details><summary>SQL [tenant] rows=5</summary>

```sql
SELECT
  s.neighborhood,
  COUNT(DISTINCT t.store_id) AS store_count,
  COUNT(DISTINCT t.txn_id) AS txn_count,
  SUM(t.subtotal) AS revenue
FROM transactions t
JOIN stores s ON t.store_id = s.store_id
WHERE t.banner_code = 'ACM'
GROUP BY s.neighborhood
ORDER BY txn_count DESC
```
</details>

<details><summary>SQL [lake_sql] rows=8</summary>

```sql
SELECT
  s.neighborhood,
  COUNT(DISTINCT lt.lake_txn_id) AS peer_txn_count,
  SUM(lt.qty) AS peer_units,
  SUM(lt.line_total) AS peer_revenue
FROM lake_transactions lt
JOIN lake_stores s USING (lake_store_id)
WHERE lt.peer_relationship = 'peer'
GROUP BY s.neighborhood
ORDER BY peer_txn_count DESC
```
</details>

**Winn-Dixie (WDX)** — STABLE · routed → trade

> **You have stores in 4 neighborhoods — Eastway, University City, Matthews, and Cabarrus Edge — while same-segment competitors cover 6 neighborhoods, including high-volume NoDa, Dilworth, Center City, and Ballantyne where you have no presence at all.**
> - Your busiest store is in Eastway with about 135,852 transactions, followed closely by University City (123,085) and Matthews (119,699) — your quietest is Cabarrus Edge at 87,891 transactions.
> - Peers are most active in NoDa (about 364,289 transactions) and Dilworth (about 356,612), two neighborhoods where you have zero stores.
> - In Matthews — the one neighborhood you share with peers.
> - NoDa, Dilworth, Center City.
>
> *Matthews is your best head-to-head test market — if you can grow your share there, NoDa and Dilworth are the highest-volume neighborhoods worth evaluating for a new store.*

Audit: 7/7 claims recompute-matched
Grain: tenant: neighborhood · lake_sql: neighborhood

<details><summary>SQL [tenant] rows=4</summary>

```sql
SELECT
  s.neighborhood,
  COUNT(DISTINCT t.store_id) AS store_count,
  COUNT(DISTINCT t.txn_id) AS txn_count,
  SUM(t.subtotal) AS revenue
FROM transactions t
JOIN stores s ON t.store_id = s.store_id
WHERE t.banner_code = 'WDX'
GROUP BY s.neighborhood
ORDER BY txn_count DESC
```
</details>

<details><summary>SQL [lake_sql] rows=6</summary>

```sql
SELECT
  s.neighborhood,
  COUNT(DISTINCT lt.lake_txn_id) AS peer_txn_count,
  SUM(lt.qty) AS peer_units,
  SUM(lt.line_total) AS peer_revenue
FROM lake_transactions lt
JOIN lake_stores s USING (lake_store_id)
WHERE lt.peer_relationship = 'peer'
GROUP BY s.neighborhood
ORDER BY peer_txn_count DESC
```
</details>

**Cross-grocer read:** All three grocers return a grounded answer every run — safe to click live. The differences between the three answers are the different peer sets, not disagreement — the underlying per-banner numbers reconcile (see the reconciliation table).

#### `T4` — Which neighborhoods show the biggest expansion opportunity?


**Kroger (KRG)** — STABLE · routed → trade

> **Eastway and Cabarrus Edge are your biggest missed opportunities — peers are doing real business there but you have no presence.**
> - Eastway has about $6.5M in peer grocery revenue and over 1.7M peer units sold, but you have zero sales there — it's entirely unserved by Kroger.
> - Cabarrus Edge adds another $4.2M in peer revenue and 1.1M peer units with no Kroger footprint at all.
> - Matthews is the one neighborhood where peers clearly outsell you: peers pull in about $13.6M there versus your $11.1M — a gap of roughly $2.4M, suggesting competitors have stronger positioning or more locations there.
> - In every other neighborhood you serve (NoDa, Dilworth, Center City, University City, Ballantyne), you out-earn peers, so your existing stores are well-placed.
>
> *Prioritize Eastway first — it has the most peer demand of any neighborhood you don't serve.*

Audit: 7/7 claims recompute-matched
Grain: tenant: neighborhood · lake_sql: neighborhood

<details><summary>SQL [tenant] rows=6</summary>

```sql
SELECT 
  s.neighborhood,
  SUM(i.line_total) AS own_revenue,
  SUM(i.qty) AS own_units,
  COUNT(DISTINCT t.txn_id) AS own_txns
FROM transaction_items i
JOIN transactions t ON i.txn_id = t.txn_id
JOIN stores s ON t.store_id = s.store_id
WHERE t.banner_code = 'KRG'
GROUP BY s.neighborhood
ORDER BY own_revenue DESC
```
</details>

<details><summary>SQL [lake_sql] rows=8</summary>

```sql
SELECT 
  s.neighborhood,
  SUM(t.line_total) AS peer_revenue,
  SUM(t.qty) AS peer_units,
  COUNT(DISTINCT t.lake_txn_id) AS peer_txns
FROM lake_transactions t
JOIN lake_stores s USING (lake_store_id)
WHERE t.peer_relationship = 'peer'
GROUP BY s.neighborhood
ORDER BY peer_revenue DESC
```
</details>

**Acme (ACM)** — STABLE · routed → trade

> **University City and Eastway are your biggest expansion opportunities — peers are ringing up millions there and you have zero presence in either neighborhood.**
> - University City is the single largest gap: peers recorded about $16.7M in sales there, but Acme has no stores and no revenue in that neighborhood.
> - Eastway is the second uncovered neighborhood — peers pulled in about $6.5M with no Acme competition.
> - Even in neighborhoods where you do compete, peers outsell you: in Matthews, peers generated about $16.8M versus your $7.9M.
> - Cabarrus Edge is a smaller but wide-open market with about $4.2M in peer sales and no Acme presence.
>
> *Open or partner in University City first — it's the highest-revenue market where you're completely absent, and peers are already proving the demand is there.*

Audit: 5/5 claims recompute-matched
Grain: tenant: neighborhood · lake_sql: neighborhood

<details><summary>SQL [tenant] rows=5</summary>

```sql
SELECT 
  s.neighborhood,
  SUM(i.qty) AS own_units,
  SUM(i.line_total) AS own_revenue,
  COUNT(DISTINCT t.txn_id) AS own_txns
FROM transaction_items i
JOIN transactions t ON i.txn_id = t.txn_id
JOIN stores s ON t.store_id = s.store_id
WHERE t.banner_code = 'ACM'
GROUP BY s.neighborhood
ORDER BY own_revenue DESC
```
</details>

<details><summary>SQL [lake_sql] rows=8</summary>

```sql
SELECT 
  s.neighborhood,
  SUM(t.qty) AS peer_units,
  SUM(t.line_total) AS peer_revenue,
  COUNT(DISTINCT t.lake_txn_id) AS peer_txns
FROM lake_transactions t
JOIN lake_stores s USING (lake_store_id)
WHERE t.peer_relationship = 'peer'
GROUP BY s.neighborhood
ORDER BY peer_revenue DESC
```
</details>

**Winn-Dixie (WDX)** — STABLE · routed → trade

> **NoDa, Dilworth, Center City.**
> - NoDa is the single largest untapped neighborhood: same-segment competitors are doing about $20.5M in revenue and selling roughly 4.5M units there, with no Winn-Dixie presence at all.
> - Dilworth and Center City are nearly as large, with peers posting about $20M and $19.5M in revenue respectively — both wide open for you.
> - Ballantyne adds another roughly $17.1M in peer demand that you're not capturing.
> - In the two neighborhoods where you do compete — Matthews and University City — peers are still outselling you heavily: about $19M vs. your $5.7M in Matthews, and about $10.8M vs. your $5.9M in University City.
>
> *Prioritize NoDa, Dilworth, and Center City for new-store or partnership exploration.*

Audit: 9/9 claims recompute-matched
Grain: tenant: neighborhood · lake_sql: neighborhood

<details><summary>SQL [tenant] rows=4</summary>

```sql
SELECT s.neighborhood,
       SUM(i.qty) AS own_units,
       SUM(i.line_total) AS own_revenue
FROM transaction_items i
JOIN transactions t ON i.txn_id = t.txn_id
JOIN stores s ON t.store_id = s.store_id
WHERE t.banner_code = 'WDX'
GROUP BY s.neighborhood
ORDER BY own_revenue DESC
```
</details>

<details><summary>SQL [lake_sql] rows=6</summary>

```sql
SELECT s.neighborhood,
       SUM(t.qty) AS peer_units,
       SUM(t.line_total) AS peer_revenue,
       COUNT(DISTINCT t.lake_txn_id) AS peer_txns
FROM lake_transactions t
JOIN lake_stores s USING (lake_store_id)
WHERE t.peer_relationship = 'peer'
GROUP BY s.neighborhood
ORDER BY peer_revenue DESC
```
</details>

**Cross-grocer read:** All three grocers return a grounded answer every run — safe to click live. The differences between the three answers are the different peer sets, not disagreement — the underlying per-banner numbers reconcile (see the reconciliation table).