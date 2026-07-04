# Agent validation — live battery vs v2 data + lake

Model: `claude-sonnet-4-6` · 14 questions. Each answer's own SQL was independently re-run and every surviving claim recomputed.


**Scorecard:** claims passed=88 · normalized=7 · stripped=1 · auto-flags=1


---

## Pricing (grocery) — `KRG`  ·  routed → pricing (pill)

**Q:** How do my prices compare to peer grocers across categories?

**Headline:** Kroger prices below same-segment peers in most high-volume categories, with the biggest gaps in Coffee & Tea, Seafood, and Beef — but runs above peers in Cleaning & Paper and Salty Snacks.

**Evidence:**
- In Coffee & Tea, you charge about $7.91/unit versus about $9.24 at peer grocers — roughly $1.33 cheaper per item.
- Seafood shows a similar gap: your price is about $9.03/unit versus peers at about $12.87 — nearly $3.84 below the market.
- Beef is also meaningfully cheaper on your shelves: about $8.16/unit versus peers at about $10.42.
- You price above peers in Cleaning & Paper (about $7.93 vs. $7.85) and Salty Snacks (about $4.06 vs. $4.32 — you're actually cheaper there too), and you're close to even on most staples like Bread, Eggs, and Butter.

**So what:** Coffee & Tea and Seafood have the most room to test a modest price lift — you're running well below market and could close some of that gap without standing out on price.


| status | span | stated | validator_true | my_recompute | match |
|---|---|---|---|---|---|
| passed | $7.91/unit | 7.9086 | 7.9086 | 7.9086 | ✅ |
| passed | about $9.24 at peer grocers | 9.2422 | 9.2422 | 9.2422 | ✅ |
| passed | roughly $1.33 cheaper per item | -1.3337 | -1.3337 | -1.3337 | ✅ |
| passed | your price is about $9.03/unit | 9.0345 | 9.0345 | 9.0345 | ✅ |
| passed | peers at about $12.87 | 12.8739 | 12.8739 | 12.8739 | ✅ |
| passed | nearly $3.84 below the market | -3.8394 | -3.8394 | -3.8394 | ✅ |
| passed | about $8.16/unit | 8.1563 | 8.1563 | 8.1563 | ✅ |
| passed | peers at about $10.42 | 10.4183 | 10.4183 | 10.4183 | ✅ |
| passed | about $7.93 | 7.9255 | 7.9255 | 7.9255 | ✅ |
| passed | $7.85 | 7.8460 | 7.8460 | 7.8460 | ✅ |
| passed | about $4.06 | 4.0581 | 4.0581 | 4.0581 | ✅ |
| passed | $4.32 | 4.3224 | 4.3224 | 4.3224 | ✅ |

**Grain used:** tenant: functional_category, category · lake_sql: category

<details><summary>SQL [tenant] rows=33</summary>

```sql
SELECT 
    p.functional_category AS category,
    AVG(i.unit_price) AS own_asp,
    SUM(i.line_total) AS own_revenue,
    COUNT(i.line_id) AS own_lines
FROM transaction_items i
JOIN transactions t ON i.txn_id = t.txn_id
JOIN products p ON i.sku = p.sku
WHERE t.banner_code = 'KRG'
GROUP BY p.functional_category
ORDER BY own_revenue DESC
```
</details>

<details><summary>SQL [lake_sql] rows=33</summary>

```sql
SELECT 
    category,
    AVG(unit_price) AS peer_asp,
    SUM(line_total) AS peer_revenue,
    COUNT(lake_line_id) AS peer_lines
FROM lake_transactions
WHERE peer_relationship = 'peer'
GROUP BY category
ORDER BY peer_revenue DESC
```
</details>

**Semantic flags:** none auto-detected.

*telemetry: 3 turns · 1829 out tok · $0.1406*

---

## Demand (grocery) — `KRG`  ·  routed → demand (pill)

**Q:** Which categories over- or under-perform vs peers given my mix?

**Headline:** Kroger punches above its mix weight in Cleaning & Paper, Fresh Vegetables, and Juice & Tea — but trails peers in Beef, Seafood, Coffee & Tea, and Bakery Sweets.

**Evidence:**
- Cleaning & Paper is your biggest revenue category at about $5.3M vs peers' $4.5M.
- Fresh Vegetables and Juice & Tea follow a similar pattern: you pull in about $3.5M and $2.5M respectively, both ahead of the peer pool ($2.9M and $2.1M).
- Beef is the sharpest underperformance.
- Coffee & Tea is another lag: you bring in about $1.8M vs peers' $2.0M, and Bakery Sweets trails at about $1.1M vs peers' $0.94M on units but peers earn more per unit on average.

**So what:** Prioritize meat counter investment (Beef + Seafood) and a coffee fixture upgrade to close the biggest dollar gaps — and protect your Cleaning & Paper and Produce leads with in-stock discipline.


| status | span | stated | validator_true | my_recompute | match |
|---|---|---|---|---|---|
| passed | $5.3M | 5327383.2700 | 5327383.2700 | 5327383.2700 | ✅ |
| passed | peers' $4.5M | 4507352.3400 | 4507352.3400 | 4507352.3400 | ✅ |
| passed | about $3.5M | 3458563.4800 | 3458563.4800 | 3458563.4800 | ✅ |
| passed | peer pool ($2.9M | 2929987.7600 | 2929987.7600 | 2929987.7600 | ✅ |
| passed | $2.5M | 2507272.0100 | 2507272.0100 | 2507272.0100 | ✅ |
| passed | $2.1M) | 2147979.1100 | 2147979.1100 | 2147979.1100 | ✅ |
| passed | about $2.5M vs peers' $3.3M | 2493171.4500 | 2493171.4500 | 2493171.4500 | ✅ |
| passed | peers' $3.3M | 3274350.3400 | 3274350.3400 | 3274350.3400 | ✅ |
| passed | $1.5M vs $1.9M peers | 1487436.2900 | 1487436.2900 | 1487436.2900 | ✅ |
| passed | $1.9M peers | 1850631.0500 | 1850631.0500 | 1850631.0500 | ✅ |
| passed | about $1.8M vs peers' $2.0M | 1819266.3900 | 1819266.3900 | 1819266.3900 | ✅ |
| passed | peers' $2.0M | 1960069.7500 | 1960069.7500 | 1960069.7500 | ✅ |
| passed | about $1.1M | 1118374.1000 | 1118374.1000 | 1118374.1000 | ✅ |
| passed | peers' $0.94M | 939848.9900 | 939848.9900 | 939848.9900 | ✅ |

**Grain used:** tenant: functional_category, category · lake_sql: category

<details><summary>SQL [tenant] rows=33</summary>

```sql
SELECT
  p.functional_category AS category,
  SUM(i.qty) AS own_units,
  SUM(i.line_total) AS own_revenue
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
  SUM(qty) AS peer_units,
  SUM(line_total) AS peer_revenue,
  COUNT(DISTINCT lake_txn_id) AS peer_txns
FROM lake_transactions
WHERE peer_relationship = 'peer'
  AND txn_date < '2026-05-25'
GROUP BY category
ORDER BY peer_revenue DESC
```
</details>

**Semantic flags:** none auto-detected.

*telemetry: 3 turns · 1956 out tok · $0.1402*

---

## Trade (grocery) — `KRG`  ·  routed → trade (pill)

**Q:** Which of my neighborhoods are over- or under-performing?

**Headline:** Matthews is your biggest missed opportunity.

**Evidence:**
- In Matthews, your revenue is about $11.1M versus peers' $13.6M.
- Eastway and Cabarrus Edge show peer demand ($6.5M and $4.2M respectively) where you have no stores at all — two neighborhoods you're completely absent from.

**So what:** Prioritize Matthews with targeted promotions or a store review to close the gap, and evaluate whether opening in Eastway or Cabarrus Edge makes sense given the peer demand already there.


| status | span | stated | validator_true | my_recompute | match |
|---|---|---|---|---|---|
| passed | your revenue is about $11.1M | 11138952.0700 | 11138952.0700 | 11138952.0700 | ✅ |
| passed | peers' $13.6M | 13555368.9200 | 13555368.9200 | 13555368.9200 | ✅ |
| passed | you bring in about $11.8M | 11778548.4800 | 11778548.4800 | 11778548.4800 | ✅ |
| passed | peers' $8.7M | 8740941.8600 | 8740941.8600 | 8740941.8600 | ✅ |
| passed | your $11.6M | 11599249.5300 | 11599249.5300 | 11599249.5300 | ✅ |
| passed | peers' $8.4M | 8440319.5100 | 8440319.5100 | 8440319.5100 | ✅ |
| passed | Eastway and Cabarrus Edge show peer demand ( | 6490211.8700 | 6490211.8700 | 6490211.8700 | ✅ |
| passed | and $4.2M respectively) | 4196828.9900 | 4196828.9900 | 4196828.9900 | ✅ |

**Grain used:** tenant: neighborhood · lake_sql: neighborhood

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

**Semantic flags:** none auto-detected.

*telemetry: 3 turns · 2227 out tok · $0.1381*

---

## Anomaly (grocery) — `KRG`  ·  routed → anomaly (pill)

**Q:** Which SKUs or categories are spiking or dropping unusually versus peers?

**Headline:** Based on data through the week of May 18, results are truncated — pulling the two most recent full weeks per category now to find the biggest divergences.


| status | span | stated | validator_true | my_recompute | match |
|---|---|---|---|---|---|

**Grain used:** tenant: functional_category, category · lake_sql: category

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

**Semantic flags:** none auto-detected.

*telemetry: 3 turns · 464 out tok · $0.1337*

---

## Advisor payment mix (grocery) — `KRG`  ·  routed → advisor

**Q:** What's my contactless payment mix versus peers?

**Headline:** You ran 650,502 contactless transactions out of 1,257,180 total — a contactless share of 51.7%.

**Evidence:**
- Your same-segment grocery peers show 580,767 contactless transactions out of 1,121,925 total — a peer contactless share of 51.8%.
- Chip is the next biggest method for both sides — you're at 38.2% chip, peers at 38.2% as well, so neither of you has a big gap there either.

**So what:** Both you and peers are leaning into contactless at roughly the same rate — focus on making sure your tap experience is fast and reliable at checkout to keep that share from slipping.


| status | span | stated | validator_true | my_recompute | match |
|---|---|---|---|---|---|
| passed | 650,502 contactless transactions | 650502.0000 | 650502.0000 | 650502.0000 | ✅ |
| passed | 1,257,180 total | 1257180.0000 | 1257180.0000 | 1257180.0000 | ✅ |
| normalized | contactless share of about 51% | 0.5174 | 0.5174 | 0.5174 | ✅ |
| passed | 580,767 contactless transactions | 580767.0000 | 580767.0000 | 580767.0000 | ✅ |
| passed | 1,121,925 total | 1121925.0000 | 1121925.0000 | 1121925.0000 | ✅ |
| normalized | peer contactless share of about 52% | 0.5177 | 0.5177 | 0.5177 | ✅ |
| normalized | about 38% chip | 0.3822 | 0.3822 | 0.3822 | ✅ |
| normalized | peers at about 38% | 0.3824 | 0.3824 | 0.3824 | ✅ |

**Grain used:** tenant: entry_mode · lake_sql: entry_mode

<details><summary>SQL [tenant] rows=4</summary>

```sql
SELECT
  entry_mode,
  COUNT(txn_id) AS txns
FROM transactions
WHERE banner_code = 'KRG'
GROUP BY entry_mode
```
</details>

<details><summary>SQL [lake_sql] rows=4</summary>

```sql
SELECT
  entry_mode,
  COUNT(DISTINCT lake_txn_id) AS txns
FROM lake_transactions
WHERE peer_relationship = 'peer'
GROUP BY entry_mode
```
</details>

**Label-review corrections:**
- `share`: fraction 0.5174 printed with % → 51.7%
- `share`: fraction 0.5177 printed with % → 51.8%
- `share`: fraction 0.3824 printed with % → 38.2%
- `share`: fraction 0.3822 printed with % → 38.2%

**Semantic flags:** none auto-detected.

*telemetry: 3 turns · 1430 out tok · $0.1227*

---

## Own-only top categories (KRG) — `KRG`  ·  routed → demand

**Q:** What are my top categories by sales?

**Headline:** Cleaning is your top revenue category at $5.3M, followed by Chips & Snacks at $4.6M and Fruit at $4.3M.

**Evidence:**
- Cleaning leads the shelf at $5.3M in revenue, though it moves fewer units (672K) than Chips & Snacks or Fruit — suggesting a higher price per item.
- Chips & Snacks is your second-biggest category at $4.6M and your highest unit mover at over 1.1M units.
- Fruit and Vegetables together bring in $7.8M, making fresh produce your single biggest combined area.
- Beef rounds out the top 10 at $2.5M on just 306K units.

**So what:** Fresh produce (Fruit + Vegetables) is your biggest combined revenue driver — keeping in-stock rates and quality high there will protect more sales than any single other category.


| status | span | stated | validator_true | my_recompute | match |
|---|---|---|---|---|---|
| passed | $5.3M | 5327383.2700 | 5327383.2700 | 5327383.2700 | ✅ |
| passed | $4.6M | 4631840.9600 | 4631840.9600 | 4631840.9600 | ✅ |
| passed | $4.3M | 4311644.3100 | 4311644.3100 | 4311644.3100 | ✅ |
| passed | 672K | 672161.0000 | 672161.0000 | 672161.0000 | ✅ |
| passed | 1.1M units | 1141472.0000 | 1141472.0000 | 1141472.0000 | ✅ |
| passed | $7.8M | 7770207.7900 | 7770207.7900 | 7770207.7900 | ✅ |
| passed | $2.5M | 2493171.4500 | 2493171.4500 | 2493171.4500 | ✅ |
| passed | 306K units | 305767.0000 | 305767.0000 | 305767.0000 | ✅ |

**Grain used:** tenant: merchant_category, category

<details><summary>SQL [tenant] rows=10</summary>

```sql
SELECT 
  p.merchant_category AS category,
  SUM(i.line_total) AS revenue,
  SUM(i.qty) AS units
FROM transaction_items i
JOIN transactions t ON i.txn_id = t.txn_id
JOIN products p ON i.sku = p.sku
WHERE t.banner_code = 'KRG'
  AND t.txn_ts < '2026-05-25'
GROUP BY p.merchant_category
ORDER BY revenue DESC
LIMIT 10
```
</details>

**Semantic flags:** none auto-detected.

*telemetry: 3 turns · 1257 out tok · $0.1246*

---

## Own-only top categories (ACM) — `ACM`  ·  routed → advisor

**Q:** What are my top categories by sales?

**Headline:** Salty Snacks is your top-selling category at about $3.1M, followed by Personal Care and Household Cleaning.

**Evidence:**
- Salty Snacks leads with about $3.1M in sales, well ahead of #2 Personal Care at about $2.6M.
- Beef and Fresh Fruit round out the top 5, each just under $2.5M — showing strong fresh and meat performance.
- Soft Drinks ($2.1M), Coffee & Tea ($1.9M), and Fresh Vegetables ($1.9M) make up the next tier, with Fresh Seafood and Pasta & Italian just above $1.6M each.

**So what:** Salty Snacks and Personal Care are doing the heavy lifting — make sure those shelves are fully stocked and promotionally active to protect that lead.


| status | span | stated | validator_true | my_recompute | match |
|---|---|---|---|---|---|
| passed | $3.1M | 3107337.8100 | 3107337.8100 | 3107337.8100 | ✅ |
| passed | about $2.6M | 2589330.6700 | 2589330.6700 | 2589330.6700 | ✅ |
| passed | Household Cleaning | 2530326.8300 | 2530326.8300 | 2530326.8300 | ✅ |
| passed | Beef and Fresh Fruit round out the top 5, ea | 2507905.0700 | 2507905.0700 | 2507905.0700 | ✅ |
| passed | Fresh Fruit | 2482175.6500 | 2482175.6500 | 2482175.6500 | ✅ |
| passed | Soft Drinks ($2.1M) | 2117199.8900 | 2117199.8900 | 2117199.8900 | ✅ |
| passed | Coffee & Tea ($1.9M) | 1926377.3700 | 1926377.3700 | 1926377.3700 | ✅ |
| passed | Fresh Vegetables ($1.9M) | 1903728.3900 | 1903728.3900 | 1903728.3900 | ✅ |
| passed | Fresh Seafood | 1645234.8300 | 1645234.8300 | 1645234.8300 | ✅ |
| passed | Pasta & Italian just above $1.6M | 1642253.6300 | 1642253.6300 | 1642253.6300 | ✅ |

**Grain used:** tenant: merchant_category, category

<details><summary>SQL [tenant] rows=10</summary>

```sql
SELECT
    p.merchant_category AS category,
    SUM(i.line_total) AS revenue
FROM transaction_items i
JOIN transactions t ON i.txn_id = t.txn_id
JOIN products p ON i.sku = p.sku
WHERE t.banner_code = 'ACM'
GROUP BY p.merchant_category
ORDER BY revenue DESC
LIMIT 10
```
</details>

**Semantic flags:** none auto-detected.

*telemetry: 3 turns · 1342 out tok · $0.1212*

---

## Department-grain (grocery) — `KRG`  ·  routed → pricing

**Q:** How does my Dairy & Eggs department compare to peers?

**Headline:** Your Dairy & Eggs prices run below peers in Cheese and Milk — the two biggest categories — while Eggs and Butter are nearly level.

**Evidence:**
- In Cheese, your average price per item is about $4.55 versus competitors' $4.76 — you're roughly 20 cents cheaper, even though Cheese is your top revenue category at about $2.3M.
- In Milk, you charge about $3.45 per unit versus the peer average of about $3.66 — about 20 cents cheaper again, with your milk revenue at about $1.4M.
- Eggs and Butter & Margarine are almost exactly in line with peers — your Eggs ASP is about $3.76 versus the peer $3.77, and Butter runs $3.52 versus $3.54.
- Yogurt is also slightly below peers — about $1.03 per unit versus $1.05 — but the gap is small.

**So what:** You have clear room to nudge Cheese and Milk prices up by 10–20 cents — test a small increase and watch if unit volume holds.


| status | span | stated | validator_true | my_recompute | match |
|---|---|---|---|---|---|
| passed | $4.55 | 4.5544 | 4.5544 | 4.5544 | ✅ |
| passed | $4.76 | 4.7551 | 4.7551 | 4.7551 | ✅ |
| passed | $2.3M | 2295813.0700 | 2295813.0700 | 2295813.0700 | ✅ |
| passed | $3.45 | 3.4496 | 3.4496 | 3.4496 | ✅ |
| passed | $3.66 | 3.6551 | 3.6551 | 3.6551 | ✅ |
| passed | $1.4M | 1420484.5600 | 1420484.5600 | 1420484.5600 | ✅ |
| passed | your Eggs ASP is about $3.76 | 3.7641 | 3.7641 | 3.7641 | ✅ |
| passed | peer $3.77 | 3.7665 | 3.7665 | 3.7665 | ✅ |
| passed | Butter runs $3.52 | 3.5186 | 3.5186 | 3.5186 | ✅ |
| passed | $3.54 | 3.5409 | 3.5409 | 3.5409 | ✅ |
| passed | about $1.03 per unit | 1.0340 | 1.0340 | 1.0340 | ✅ |
| passed | $1.05 | 1.0500 | 1.0500 | 1.0500 | ✅ |

**Grain used:** tenant: functional_category, category · lake_sql: category, department

<details><summary>SQL [tenant] rows=5</summary>

```sql
SELECT
    p.functional_category AS category,
    AVG(i.unit_price) AS own_asp,
    SUM(i.line_total) AS own_revenue,
    SUM(i.qty) AS own_units,
    COUNT(DISTINCT t.txn_id) AS own_txns
FROM transaction_items i
JOIN transactions t ON i.txn_id = t.txn_id
JOIN products p ON i.sku = p.sku
WHERE t.banner_code = 'KRG'
  AND p.functional_department = 'Dairy & Eggs'
GROUP BY p.functional_category
ORDER BY own_revenue DESC
```
</details>

<details><summary>SQL [lake_sql] rows=5</summary>

```sql
SELECT
    category,
    AVG(unit_price) AS peer_asp,
    SUM(line_total) AS peer_revenue,
    SUM(qty) AS peer_units,
    COUNT(DISTINCT lake_txn_id) AS peer_txns
FROM lake_transactions
WHERE peer_relationship = 'peer'
  AND department = 'Dairy & Eggs'
GROUP BY category
ORDER BY peer_revenue DESC
```
</details>

**Semantic flags:** none auto-detected.

*telemetry: 3 turns · 1724 out tok · $0.1349*

---

## Cannot-answer cohort (grocery) — `KRG`  ·  routed → advisor

**Q:** Which of my shoppers also buy at a competitor?

**Headline:** Cross-merchant shopper overlap isn't available — the peer lake has no consumer identity linked across merchants by design.

**Evidence:**
- Your own data shows about 114,257 unique shoppers, but there is no way to match any of them to transactions at a competitor — the peer lake strips all consumer identifiers before sharing.
- The peer lake provides anonymized purchase lines only: no loyalty IDs, no card tokens, no shopper linkage of any kind across merchants.

**So what:** To get at cross-shopping behavior, your best option is a first-party survey or a third-party panel (e.g. Nielsen Homescan) that tracks households across retailers — that data isn't in this system.


| status | span | stated | validator_true | my_recompute | match |
|---|---|---|---|---|---|
| passed | 114,257 unique shoppers | 114257.0000 | 114257.0000 | 114257.0000 | ✅ |

**Grain used:** tenant: —

<details><summary>SQL [tenant] rows=1</summary>

```sql
SELECT COUNT(DISTINCT customer_token) AS total_shoppers FROM transactions WHERE banner_code = 'KRG'
```
</details>

**⚠ Semantic flags:**
- CANNOT-ANSWER: response carried claims for an unanswerable question

*telemetry: 4 turns · 578 out tok · $0.1390*

---

## Pricing (QSR) — `TBL`  ·  routed → pricing (pill)

**Q:** How does my pricing compare to peers across my menu?

**Headline:** Taco Bell prices below same-segment competitors in every category except Beverages and Sides, with the biggest gap in Entrées.

**Evidence:**
- On Entrées, you charge about $3.51 per item versus peers' $6.87 — you're roughly $3.37 cheaper, the largest gap on your menu.
- Combos also run below the competition: your average is about $7.57 versus peers' $9.15, a gap of around $1.58 per combo.
- Chicken is similarly priced below peers.
- Beverages and Sides are the two spots where you actually price above peers: your Beverages average $2.59 versus peers' $2.48, and your Sides run $2.99 versus peers' $2.32.

**So what:** Entrées, Combos, and Chicken all have meaningful headroom to raise prices — even a modest increase in those categories would capture significant margin without pushing above competitor levels.


| status | span | stated | validator_true | my_recompute | match |
|---|---|---|---|---|---|
| passed | you charge about $3.51 per item | 3.5070 | 3.5070 | 3.5070 | ✅ |
| passed | peers' $6.87 | 6.8745 | 6.8745 | 6.8745 | ✅ |
| passed | roughly $3.37 cheaper | -3.3676 | -3.3676 | -3.3676 | ✅ |
| passed | your average is about $7.57 | 7.5663 | 7.5663 | 7.5663 | ✅ |
| passed | peers' $9.15 | 9.1453 | 9.1453 | 9.1453 | ✅ |
| passed | gap of around $1.58 per combo | -1.5790 | -1.5790 | -1.5790 | ✅ |
| passed | $5.49 on your side | 5.4878 | 5.4878 | 5.4878 | ✅ |
| passed | $6.12 for competitors | 6.1158 | 6.1158 | 6.1158 | ✅ |
| passed | your Beverages average $2.59 | 2.5910 | 2.5910 | 2.5910 | ✅ |
| passed | peers' $2.48 | 2.4842 | 2.4842 | 2.4842 | ✅ |
| passed | your Sides run $2.99 | 2.9863 | 2.9863 | 2.9863 | ✅ |
| passed | peers' $2.32 | 2.3200 | 2.3200 | 2.3200 | ✅ |

**Grain used:** tenant: functional_category, category · lake_sql: category

<details><summary>SQL [tenant] rows=7</summary>

```sql
SELECT 
    p.functional_category AS category,
    AVG(i.unit_price) AS own_asp,
    SUM(i.line_total) AS own_revenue,
    COUNT(DISTINCT t.txn_id) AS own_txn_count
FROM transaction_items i
JOIN transactions t ON i.txn_id = t.txn_id
JOIN products p ON i.sku = p.sku
WHERE t.banner_code = 'TBL'
  AND t.txn_ts < '2026-05-25'
GROUP BY p.functional_category
ORDER BY own_revenue DESC
```
</details>

<details><summary>SQL [lake_sql] rows=7</summary>

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
```
</details>

**Semantic flags:** none auto-detected.

*telemetry: 3 turns · 1937 out tok · $0.1381*

---

## Demand (QSR) — `TBL`  ·  routed → demand (pill)

**Q:** Which menu categories are growing or slowing versus peers?

**Headline:** A grounded peer comparison wasn't available for this view; your own figures and the peer benchmark are shown below.


| status | span | stated | validator_true | my_recompute | match |
|---|---|---|---|---|---|

**Grain used:** tenant: functional_category, category · lake_sql: category

<details><summary>SQL [tenant] rows=91</summary>

```sql
SELECT
  p.functional_category AS category,
  date_trunc('week', t.txn_ts) AS week_start,
  SUM(i.qty) AS units,
  SUM(i.line_total) AS revenue
FROM transaction_items i
JOIN transactions t ON i.txn_id = t.txn_id
JOIN products p ON i.sku = p.sku
WHERE t.banner_code = 'TBL'
  AND date_trunc('week', t.txn_ts) < DATE '2026-05-25'
GROUP BY p.functional_category, date_trunc('week', t.txn_ts)
ORDER BY p.functional_category, week_start
```
</details>

<details><summary>SQL [lake_sql] rows=91</summary>

```sql
SELECT
  category,
  date_trunc('week', txn_date) AS week_start,
  SUM(qty) AS peer_units,
  SUM(line_total) AS peer_revenue,
  COUNT(DISTINCT lake_txn_id) AS peer_txns
FROM lake_transactions
WHERE peer_relationship = 'peer'
  AND date_trunc('week', txn_date) < DATE '2026-05-25'
GROUP BY category, date_trunc('week', txn_date)
ORDER BY category, week_start
```
</details>

**Semantic flags:** none auto-detected.

*telemetry: 3 turns · 521 out tok · $0.1252*

---

## Anomaly late-night (QSR) — `TBL`  ·  routed → anomaly (pill)

**Q:** Is my late-night business unusual compared with peers?

**Headline:** Your late-night business (midnight–2 AM) is a major strength that your same-segment QSR peers don't have — you run roughly 37,000 late-night transactions every night while peers show almost no traffic in those hours.

**Evidence:**
- You log about 12,500–12,800 transactions per hour across midnight, 1 AM, and 2 AM — together that's about 38,095 late-night transactions every hour-band, with $326,000 in revenue across those three hours.
- Peers have virtually no presence between 10 PM and 5 AM — the peer lake only surfaces traffic starting at 2 AM, and even then it looks like a different daypart distribution (peers concentrate heavily at 4–7 AM, suggesting an early-morning focus, not a late-night one).
- Your own busiest dayparts are the lunch rush (10 AM–1 PM, ~42,000 transactions/hour) and dinner (5–8 PM, ~40,000 transactions/hour) — late night at 12698.3333/hour is a solid third peak that peers don't replicate.

**So what:** Late night is your competitive edge — make sure those stores are fully staffed and stocked, and consider late-night-specific promotions to grow that already-uncontested window even further.


| status | span | stated | validator_true | my_recompute | match |
|---|---|---|---|---|---|
| passed | 12,500–12,800 transactions per hour across m | 12561.0000 | 12561.0000 | 12561.0000 | ✅ |
| passed | about 38,095 late-night transactions every h | 38095.0000 | 38095.0000 | 38095.0000 | ✅ |
| passed | $326,000 in revenue across those three hours | 325704.1500 | 325704.1500 | 325704.1500 | ✅ |
| passed | ~42,000 transactions/hour | 41854.0000 | 41854.0000 | 41854.0000 | ✅ |
| passed | ~40,000 transactions/hour | 40763.0000 | 40763.0000 | 40763.0000 | ✅ |
| normalized | late night at ~12,700/hour | 12698.3300 | 12698.3333 | 12698.3333 | ✅ |
| stripped | about 68,250 transactions | 68251.0000 | 81010.0000 | 81010.0000 | ✅ |

**Grain used:** tenant: — · lake_sql: —

<details><summary>SQL [tenant] rows=21</summary>

```sql
SELECT 
  hour_of_day,
  COUNT(DISTINCT txn_id) AS own_txns,
  SUM(subtotal) AS own_revenue
FROM (
  SELECT 
    txn_id, subtotal,
    EXTRACT(HOUR FROM txn_ts) AS hour_of_day
  FROM transactions
  WHERE banner_code = 'TBL'
    AND txn_ts < DATE '2026-05-24'
) sub
GROUP BY hour_of_day
ORDER BY hour_of_day
```
</details>

<details><summary>SQL [lake_sql] rows=8</summary>

```sql
SELECT 
  hour_bucket,
  COUNT(DISTINCT lake_txn_id) AS peer_txns,
  SUM(line_total) AS peer_revenue
FROM lake_transactions
WHERE peer_relationship = 'peer'
  AND txn_date < DATE '2026-05-24'
GROUP BY hour_bucket
ORDER BY hour_bucket
```
</details>

**Semantic flags:** none auto-detected.

*telemetry: 3 turns · 2070 out tok · $0.1490*

---

## Advisor payment mix (QSR) — `TBL`  ·  routed → advisor

**Q:** What's my payment mix versus peers?

**Headline:** Your customers split almost exactly 50/50 between credit and debit.

**Evidence:**
- You ran 292,492 credit transactions vs 291,512 debit — a nearly perfect split of 50.1% each.

**So what:** Your higher debit share may reflect a slightly more value-driven customer base — worth keeping in mind if you're evaluating card processing fees or designing rewards-linked promotions.


| status | span | stated | validator_true | my_recompute | match |
|---|---|---|---|---|---|
| normalized | about 50% each | 0.5008 | 0.5008 | 0.5008 | ✅ |
| normalized | about 56% of transactions on credit | 0.5632 | 0.5635 | 0.5635 | ✅ |

**Grain used:** tenant: tender · lake_sql: payment_type

<details><summary>SQL [tenant] rows=2</summary>

```sql
SELECT 
  tender,
  COUNT(txn_id) AS txns
FROM transactions
WHERE banner_code = 'TBL'
GROUP BY tender
ORDER BY txns DESC
```
</details>

<details><summary>SQL [lake_sql] rows=2</summary>

```sql
SELECT 
  payment_type,
  COUNT(DISTINCT lake_txn_id) AS txns
FROM lake_transactions
WHERE peer_relationship = 'peer'
GROUP BY payment_type
```
</details>

**Label-review corrections:**
- `share`: fraction 0.5008 printed with % → 50.1%

**Semantic flags:** none auto-detected.

*telemetry: 3 turns · 1097 out tok · $0.1176*

---

## Anomaly Sunday zero (CFA) — `CFA`  ·  routed → anomaly (pill)

**Q:** How does my Sunday traffic compare to peers?

**Headline:** Chick-fil-A is closed on Sundays, so you have zero Sunday transactions — your same-segment peers logged about 122,788 Sunday transactions over the same window, but that gap is expected and intentional, not a problem.

**Evidence:**
- Your Sunday transaction count is 0 — you don't operate on Sundays, so there's nothing to compare.
- Your same-segment QSR peers ran roughly 122,788 Sunday transactions over the full data window, showing healthy Sunday demand in the market that you're not chasing by design.

**So what:** There's no action here — Sunday closure is a core part of Chick-fil-A's brand. If you're curious about the revenue opportunity, the peer Sunday volume gives you a rough sense of the market size you're sitting out.


| status | span | stated | validator_true | my_recompute | match |
|---|---|---|---|---|---|
| passed | zero Sunday transactions | 0.0000 | 0.0000 | 0.0000 | ✅ |
| passed | 122,788 Sunday transactions | 122788.0000 | 122788.0000 | 122788.0000 | ✅ |

**Grain used:** tenant: — · lake_sql: —

<details><summary>SQL [tenant] rows=1</summary>

```sql
SELECT COUNT(DISTINCT txn_id) AS own_sunday_txns
FROM transactions
WHERE banner_code = 'CFA'
  AND dayofweek(txn_ts) = 0
  AND txn_ts < DATE '2026-05-24'
```
</details>

<details><summary>SQL [lake_sql] rows=1</summary>

```sql
SELECT peer_relationship,
       COUNT(DISTINCT lake_txn_id) AS peer_sunday_txns
FROM lake_transactions
WHERE peer_relationship = 'peer'
  AND dayofweek(txn_date) = 0
  AND txn_date < DATE '2026-05-24'
GROUP BY peer_relationship
```
</details>

**Semantic flags:** none auto-detected.

*telemetry: 3 turns · 698 out tok · $0.1262*