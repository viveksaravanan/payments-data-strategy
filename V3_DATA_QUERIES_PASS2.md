# V3 Data Queries — Pass 2

Phase 1.6 Pass 2 verification queries. Parameter calibration (commit `Phase 1.6 Pass 2: differentiate grocers along trade-area, category mix, basket size`) applied on top of Pass 1; no other changes. SQL is verbatim from `V3_DATA_QUERIES.md` where the section name matches; Pass-2-specific sections are new.

DB info: `/Users/viveksaravanan/Documents/payments-data-strategy/data/payments.db`, 2,765 MB on disk, regenerated 2026-05-19T00:13:50. 223,480 transactions across 10,000 customers.


## Section 3. Customer overlap


### 3.3 Distribution of customers by number of merchants shopped (1–5 buckets)

```sql
WITH cm AS (
  SELECT customer_id, COUNT(DISTINCT merchant_id) AS n_merchants
  FROM tenant_transactions
  GROUP BY customer_id
)
SELECT n_merchants, COUNT(*) AS n_customers,
       ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM cm), 1) AS pct_of_panel
FROM cm
GROUP BY n_merchants
ORDER BY n_merchants
```

| n_merchants | n_customers | pct_of_panel |
| --- | --- | --- |
| 1 | 326 | 3.3 |
| 2 | 1,833 | 18.4 |
| 3 | 3,759 | 37.7 |
| 4 | 3,173 | 31.8 |
| 5 | 893 | 8.9 |

Returned 5 rows (all shown above). Columns: n_merchants, n_customers, pct_of_panel. Numeric ranges — `n_merchants`: 1 to 5; `n_customers`: 326 to 3,759; `pct_of_panel`: 3.3 to 37.7.


## Section 4. Basket and transaction shape

4.1 and 4.2 add p95 columns (alongside median) so the basket-mult hi-cap on WDX is verifiable — the change was specifically meant to keep the high end realistic.


### 4.1 Basket size (items per transaction) per merchant — incl. p95 and max

```sql
WITH bs AS (SELECT t.merchant_id, t.txn_id, COUNT(*) AS items
            FROM tenant_transactions t
            JOIN tenant_transaction_items i ON i.txn_id = t.txn_id
            GROUP BY t.merchant_id, t.txn_id)
SELECT merchant_id, COUNT(*), AVG(items), MEDIAN(items),
       p95(items), MIN(items), MAX(items) FROM bs GROUP BY merchant_id;
-- (median + p95 computed in Python; SQLite has no built-in MEDIAN)
```

| merchant_id | n_txns | mean_items | median_items | p95_items | min_items | max_items |
| --- | --- | --- | --- | --- | --- | --- |
| ACM | 60,449 | 10.1 | 9 | 23 | 3 | 37 |
| KRG | 65,772 | 11.27 | 10 | 26 | 3 | 40 |
| TBL | 36,129 | 3.86 | 4 | 5 | 2 | 7 |
| TJX | 12,130 | 6.14 | 6 | 10 | 1 | 13 |
| WDX | 49,000 | 12.43 | 11 | 30 | 4 | 40 |

Returned 5 rows (all shown above). Columns: merchant_id, n_txns, mean_items, median_items, p95_items, min_items, max_items. Numeric ranges — `n_txns`: 12,130 to 65,772; `mean_items`: 3.86 to 12.43; `median_items`: 4 to 11; `p95_items`: 5 to 30; `min_items`: 1 to 4; `max_items`: 7 to 40.


### 4.2 Transaction ticket value per merchant — incl. p95 and max

```sql
SELECT merchant_id, COUNT(*), AVG(txn_total), MEDIAN(txn_total),
       p95(txn_total), MIN(txn_total), MAX(txn_total)
FROM tenant_transactions GROUP BY merchant_id;
-- (median + p95 computed in Python)
```

| merchant_id | n_txns | mean_ticket | median_ticket | p95_ticket | min_ticket | max_ticket |
| --- | --- | --- | --- | --- | --- | --- |
| ACM | 60,449 | 87.24 | 72.57 | 211.9 | 4.91 | 532.06 |
| KRG | 65,772 | 92.3 | 77.75 | 222.6 | 7.02 | 554.77 |
| TBL | 36,129 | 21.98 | 20.75 | 37.95 | 3.59 | 77.93 |
| TJX | 12,130 | 375.82 | 354.14 | 714.56 | 8.56 | 1,531.07 |
| WDX | 49,000 | 95.52 | 80.86 | 230.24 | 10.02 | 476.75 |

Returned 5 rows (all shown above). Columns: merchant_id, n_txns, mean_ticket, median_ticket, p95_ticket, min_ticket, max_ticket. Numeric ranges — `n_txns`: 12,130 to 65,772; `mean_ticket`: 21.98 to 375.82; `median_ticket`: 20.75 to 354.14; `p95_ticket`: 37.95 to 714.56; `min_ticket`: 3.59 to 10.02; `max_ticket`: 77.93 to 1,531.07.


### 4.4 Repeat-customer ratio per merchant (shopped on ≥2 distinct days)

```sql
WITH cd AS (
  SELECT merchant_id, customer_id,
         COUNT(DISTINCT DATE(txn_ts)) AS n_days
  FROM tenant_transactions GROUP BY merchant_id, customer_id
)
SELECT merchant_id, COUNT(*) AS total_customers,
       SUM(CASE WHEN n_days >= 2 THEN 1 ELSE 0 END) AS repeat_customers,
       ROUND(100.0 *
         SUM(CASE WHEN n_days >= 2 THEN 1 ELSE 0 END) / COUNT(*),
         1) AS repeat_pct
FROM cd GROUP BY merchant_id ORDER BY merchant_id
```

| merchant_id | total_customers | repeat_customers | repeat_pct |
| --- | --- | --- | --- |
| ACM | 7,382 | 5,777 | 78.3 |
| KRG | 7,020 | 5,835 | 83.1 |
| TBL | 7,697 | 6,953 | 90.3 |
| TJX | 4,893 | 3,101 | 63.4 |
| WDX | 5,434 | 4,556 | 83.8 |

Returned 5 rows (all shown above). Columns: merchant_id, total_customers, repeat_customers, repeat_pct. Numeric ranges — `total_customers`: 4,893 to 7,697; `repeat_customers`: 3,101 to 6,953; `repeat_pct`: 63.4 to 90.3.


## Section 4.5 (new) — Trade area verification


### 4.5 Per-merchant per-neighborhood store counts (Pass 2 trade-area bias)

```sql
SELECT merchant_id, neighborhood, COUNT(*) AS n_stores
FROM tenant_stores
WHERE merchant_id IN ('KRG','ACM','WDX')
GROUP BY merchant_id, neighborhood
ORDER BY merchant_id, neighborhood
```

| merchant_id | neighborhood | n_stores |
| --- | --- | --- |
| ACM | Ballantyne | 5 |
| ACM | Concord | 2 |
| ACM | Dilworth | 5 |
| ACM | Matthews | 1 |
| ACM | Plaza Midwood | 3 |
| ACM | SouthPark | 6 |
| ACM | University City | 3 |
| KRG | Ballantyne | 2 |
| KRG | Dilworth | 8 |
| KRG | Matthews | 2 |
| KRG | Mooresville | 2 |
| KRG | NoDa | 1 |
| KRG | Pineville | 2 |
| KRG | Plaza Midwood | 5 |
| KRG | SouthPark | 3 |
| KRG | University City | 3 |
| KRG | Uptown / Center City | 2 |
| WDX | Ballantyne | 2 |
| WDX | Concord | 1 |
| WDX | Dilworth | 2 |
| WDX | Huntersville | 2 |
| WDX | Mooresville | 2 |
| WDX | NoDa | 1 |
| WDX | Plaza Midwood | 3 |
| WDX | SouthPark | 2 |
| WDX | University City | 2 |
| WDX | Uptown / Center City | 3 |

Returned 27 rows (all shown above). Columns: merchant_id, neighborhood, n_stores. Numeric ranges — `n_stores`: 1 to 8.


## Section 5. Peer-comparison plausibility


### 5.1 Top 5 categories by revenue per merchant

```sql
WITH revenue AS (
  SELECT t.merchant_id, p.category, SUM(i.line_total) AS rev
  FROM tenant_transaction_items i
  JOIN tenant_transactions t ON t.txn_id = i.txn_id
  JOIN tenant_products p ON p.sku = i.sku
  GROUP BY t.merchant_id, p.category
),
total_rev AS (
  SELECT merchant_id, SUM(rev) AS total
  FROM revenue
  GROUP BY merchant_id
),
ranked AS (
  SELECT r.merchant_id, r.category, r.rev,
         ROW_NUMBER() OVER (PARTITION BY r.merchant_id ORDER BY r.rev DESC) AS rk,
         tr.total
  FROM revenue r
  JOIN total_rev tr ON tr.merchant_id = r.merchant_id
)
SELECT merchant_id, rk AS category_rank, category,
       ROUND(rev, 0) AS revenue_usd,
       ROUND(100.0 * rev / total, 1) AS pct_of_merchant_revenue
FROM ranked
WHERE rk <= 5
ORDER BY merchant_id, rk
```

| merchant_id | category_rank | category | revenue_usd | pct_of_merchant_revenue |
| --- | --- | --- | --- | --- |
| ACM | 1 | MEAT | 851,905.00 | 16.5 |
| ACM | 2 | PANTRY | 628,877.00 | 12.2 |
| ACM | 3 | DAIRY | 607,710.00 | 11.8 |
| ACM | 4 | PRODUCE | 477,789.00 | 9.3 |
| ACM | 5 | HOUSEHOLD | 463,902.00 | 9 |
| KRG | 1 | MEAT | 1,095,571.00 | 18.4 |
| KRG | 2 | PANTRY | 813,655.00 | 13.7 |
| KRG | 3 | PRODUCE | 643,433.00 | 10.8 |
| KRG | 4 | DAIRY | 562,420.00 | 9.5 |
| KRG | 5 | HOUSEHOLD | 545,181.00 | 9.2 |
| TBL | 1 | COMBO | 171,478.00 | 23.1 |
| TBL | 2 | DRINK | 128,578.00 | 17.3 |
| TBL | 3 | BURR | 103,959.00 | 14 |
| TBL | 4 | SPEC | 100,850.00 | 13.6 |
| TBL | 5 | SIDE | 100,844.00 | 13.6 |
| TJX | 1 | ACC | 1,492,598.00 | 35 |
| TJX | 2 | SHO | 549,760.00 | 12.9 |
| TJX | 3 | WOM | 540,500.00 | 12.7 |
| TJX | 4 | MEN | 457,432.00 | 10.7 |
| TJX | 5 | JEW | 446,695.00 | 10.5 |
| WDX | 1 | MEAT | 794,071.00 | 17.3 |
| WDX | 2 | PANTRY | 766,283.00 | 16.7 |
| WDX | 3 | DAIRY | 461,302.00 | 10.1 |
| WDX | 4 | HOUSEHOLD | 416,977.00 | 9.1 |
| WDX | 5 | BEVERAGES | 363,102.00 | 7.9 |

Returned 25 rows (all shown above). Columns: merchant_id, category_rank, category, revenue_usd, pct_of_merchant_revenue. Numeric ranges — `category_rank`: 1 to 5; `revenue_usd`: 100,844 to 1.4926e+06; `pct_of_merchant_revenue`: 7.9 to 35.


### 5.3 Store traffic distribution per merchant (mean / min / max / spread)

```sql
WITH store_traffic AS (
  SELECT t.merchant_id, t.store_id, COUNT(*) AS txns
  FROM tenant_transactions t
  GROUP BY t.merchant_id, t.store_id
)
SELECT
  merchant_id,
  COUNT(*) AS n_stores,
  ROUND(AVG(txns), 0) AS mean_txns_per_store,
  MIN(txns) AS min_txns_per_store,
  MAX(txns) AS max_txns_per_store,
  ROUND(100.0 * (MAX(txns) - MIN(txns)) / AVG(txns), 1) AS range_pct_of_mean
FROM store_traffic
GROUP BY merchant_id
ORDER BY merchant_id
```

| merchant_id | n_stores | mean_txns_per_store | min_txns_per_store | max_txns_per_store | range_pct_of_mean |
| --- | --- | --- | --- | --- | --- |
| ACM | 25 | 2,418.00 | 2,002 | 2,776 | 32 |
| KRG | 30 | 2,192.00 | 1,719 | 2,454 | 33.5 |
| TBL | 40 | 903 | 780 | 1,052 | 30.1 |
| TJX | 8 | 1,516.00 | 1,442 | 1,650 | 13.7 |
| WDX | 20 | 2,450.00 | 2,014 | 2,869 | 34.9 |

Returned 5 rows (all shown above). Columns: merchant_id, n_stores, mean_txns_per_store, min_txns_per_store, max_txns_per_store, range_pct_of_mean. Numeric ranges — `n_stores`: 8 to 40; `mean_txns_per_store`: 903 to 2,450; `min_txns_per_store`: 780 to 2,014; `max_txns_per_store`: 1,052 to 2,869; `range_pct_of_mean`: 13.7 to 34.9.


## Section 5.6 (new) — Pass 1 pricing re-verification (per-peer dairy)

Pass 2 should not have moved Pass 1's pricing positioning. Per-peer means should still cluster around KRG ≈ −5% vs ACM (peer_a) and ≈ +5% vs WDX (peer_b) on the tight (dairy) tier.


### 5.6 KRG dairy staples — per-peer price comparison

```sql
WITH top_dairy_canonical AS (
  SELECT canonical_name, SUM(qty) AS peer_qty
  FROM lake_transactions_KRG
  WHERE category = 'DAIRY' AND peer_segment = 'grocery'
  GROUP BY canonical_name
  ORDER BY peer_qty DESC
  LIMIT 10
),
krg_dairy AS (
  SELECT p.name AS canonical_name,
         ROUND(AVG(i.unit_price), 2) AS krg_price
  FROM tenant_transaction_items i
  JOIN tenant_products p ON p.sku = i.sku
  WHERE p.merchant_id = 'KRG' AND p.category = 'DAIRY'
  GROUP BY p.name
),
peer_dairy AS (
  SELECT canonical_name, peer_id,
         ROUND(AVG(unit_price), 2) AS peer_price
  FROM lake_transactions_KRG
  WHERE category = 'DAIRY' AND peer_segment = 'grocery'
  GROUP BY canonical_name, peer_id
)
SELECT
  td.canonical_name,
  kd.krg_price,
  MAX(CASE WHEN pd.peer_id = 'peer_a' THEN pd.peer_price END) AS peer_a_price,
  MAX(CASE WHEN pd.peer_id = 'peer_b' THEN pd.peer_price END) AS peer_b_price,
  ROUND(100.0 *
        (kd.krg_price - MAX(CASE WHEN pd.peer_id = 'peer_a' THEN pd.peer_price END)) /
         MAX(CASE WHEN pd.peer_id = 'peer_a' THEN pd.peer_price END), 1) AS krg_vs_peer_a_pct,
  ROUND(100.0 *
        (kd.krg_price - MAX(CASE WHEN pd.peer_id = 'peer_b' THEN pd.peer_price END)) /
         MAX(CASE WHEN pd.peer_id = 'peer_b' THEN pd.peer_price END), 1) AS krg_vs_peer_b_pct
FROM top_dairy_canonical td
LEFT JOIN krg_dairy kd ON kd.canonical_name = td.canonical_name
LEFT JOIN peer_dairy pd ON pd.canonical_name = td.canonical_name
GROUP BY td.canonical_name, kd.krg_price
ORDER BY td.peer_qty DESC
```

| canonical_name | krg_price | peer_a_price | peer_b_price | krg_vs_peer_a_pct | krg_vs_peer_b_pct |
| --- | --- | --- | --- | --- | --- |
| Sharp cheddar shredded (8 oz) | 3.96 | 4.24 | 3.75 | -6.6 | 5.6 |
| Half and half (quart) | 3.93 | 4.21 | 3.74 | -6.7 | 5.1 |
| Coffee creamer vanilla (32 oz) | 3.94 | 4.13 | 3.84 | -4.6 | 2.6 |
| Mozzarella whole milk (8 oz) | 4.03 | 4.24 | 3.86 | -5 | 4.4 |
| Mozzarella shredded (8 oz) | 3.52 | 3.64 | 3.32 | -3.3 | 6 |
| Greek yogurt plain (32 oz) | 5.89 | 6.29 | 5.76 | -6.4 | 2.3 |
| Crescent roll dough (8 oz) | 3.53 | 3.62 | 3.32 | -2.5 | 6.3 |
| Pepper jack shredded (8 oz) | 4.01 | 4.19 | 3.85 | -4.3 | 4.2 |
| Organic Greek yogurt (32 oz) | 7.08 | 7.43 | 6.63 | -4.7 | 6.8 |
| Organic whole milk (half gallon) | 5.41 | 5.73 | 5.28 | -5.6 | 2.5 |

Returned 10 rows (all shown above). Columns: canonical_name, krg_price, peer_a_price, peer_b_price, krg_vs_peer_a_pct, krg_vs_peer_b_pct. Numeric ranges — `krg_price`: 3.52 to 7.08; `peer_a_price`: 3.62 to 7.43; `peer_b_price`: 3.32 to 6.63; `krg_vs_peer_a_pct`: -6.7 to -2.5; `krg_vs_peer_b_pct`: 2.3 to 6.8.


Per-peer pct summary — krg_vs_peer_a_pct: range -6.7 to -2.5, mean -4.97; krg_vs_peer_b_pct: range 2.3 to 6.8, mean 4.58.


## Section 6. University City as alternative anchor


### 6.1 KRG UC stores — weekly transactions across the 90-day window

```sql
SELECT
  DATE(t.txn_ts, 'weekday 0', '-6 days') AS week_starting_sunday,
  COUNT(*) AS uc_txns_this_week
FROM tenant_transactions t
JOIN tenant_stores s ON s.store_id = t.store_id
WHERE t.merchant_id = 'KRG'
  AND s.neighborhood = 'University City'
GROUP BY week_starting_sunday
ORDER BY week_starting_sunday
```

| week_starting_sunday | uc_txns_this_week |
| --- | --- |
| 2026-02-23 | 93 |
| 2026-03-02 | 492 |
| 2026-03-09 | 522 |
| 2026-03-16 | 478 |
| 2026-03-23 | 492 |
| 2026-03-30 | 520 |
| 2026-04-06 | 514 |
| 2026-04-13 | 483 |
| 2026-04-20 | 382 |
| 2026-04-27 | 267 |
| 2026-05-04 | 328 |
| 2026-05-11 | 359 |
| 2026-05-18 | 399 |
| 2026-05-25 | 332 |

Returned 14 rows (all shown above). Columns: week_starting_sunday, uc_txns_this_week. Numeric ranges — `uc_txns_this_week`: 93 to 522.


### 6.2 ACM and WDX UC stores — weekly transactions, same window

```sql
SELECT
  t.merchant_id,
  DATE(t.txn_ts, 'weekday 0', '-6 days') AS week_starting_sunday,
  COUNT(*) AS uc_txns_this_week
FROM tenant_transactions t
JOIN tenant_stores s ON s.store_id = t.store_id
WHERE t.merchant_id IN ('ACM','WDX')
  AND s.neighborhood = 'University City'
GROUP BY t.merchant_id, week_starting_sunday
ORDER BY t.merchant_id, week_starting_sunday
```

| merchant_id | week_starting_sunday | uc_txns_this_week |
| --- | --- | --- |
| ACM | 2026-02-23 | 124 |
| ACM | 2026-03-02 | 503 |
| ACM | 2026-03-09 | 534 |
| ACM | 2026-03-16 | 507 |
| ACM | 2026-03-23 | 533 |
| ACM | 2026-03-30 | 551 |
| ACM | 2026-04-06 | 514 |
| ACM | 2026-04-13 | 551 |
| ACM | 2026-04-20 | 446 |
| ACM | 2026-04-27 | 400 |
| ACM | 2026-05-04 | 403 |
| ACM | 2026-05-11 | 436 |
| ACM | 2026-05-18 | 429 |
| ACM | 2026-05-25 | 357 |
| WDX | 2026-02-23 | 73 |
| WDX | 2026-03-02 | 315 |
| WDX | 2026-03-09 | 324 |
| WDX | 2026-03-16 | 361 |
| WDX | 2026-03-23 | 338 |
| WDX | 2026-03-30 | 359 |
| WDX | 2026-04-06 | 334 |
| WDX | 2026-04-13 | 344 |
| WDX | 2026-04-20 | 320 |
| WDX | 2026-04-27 | 279 |
| WDX | 2026-05-04 | 279 |
| WDX | 2026-05-11 | 325 |
| WDX | 2026-05-18 | 314 |
| WDX | 2026-05-25 | 255 |

Returned 28 rows (all shown above). Columns: merchant_id, week_starting_sunday, uc_txns_this_week. Numeric ranges — `uc_txns_this_week`: 73 to 551.


## Pass 2 deltas (vs `V3_DATA_QUERIES.md` and `V3_DATA_QUERIES_PASS1.md`)

Literal numeric changes only — no interpretation.


### 3.3 — customers-by-N-merchants distribution

| n_merchants | orig n | P1 n | P2 n | orig pct | P1 pct | P2 pct | Δ P2 vs P1 (pp) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 165 | 366 | 326 | 1.7 | 3.7 | 3.3 | -0.4 |
| 2 | 1,170 | 1,696 | 1,833 | 11.7 | 17.0 | 18.4 | +1.4 |
| 3 | 3,657 | 3,918 | 3,759 | 36.6 | 39.3 | 37.7 | -1.6 |
| 4 | 3,844 | 3,138 | 3,173 | 38.5 | 31.4 | 31.8 | +0.4 |
| 5 | 1,152 | 860 | 893 | 11.5 | 8.6 | 8.9 | +0.3 |

### 4.4 — repeat-customer ratio per merchant

| merchant | orig repeat_pct | P1 repeat_pct | P2 repeat_pct | Δ P2 vs P1 (pp) |
| --- | --- | --- | --- | --- |
| ACM | 80.2 | 78.2 | 78.3 | +0.1 |
| KRG | 83.2 | 83.6 | 83.1 | -0.5 |
| TBL | 73.6 | 90.1 | 90.3 | +0.2 |
| TJX | 62.4 | 60.4 | 63.4 | +3.0 |
| WDX | 78.7 | 84.8 | 83.8 | -1.0 |

### 4.1 — basket-size shape (median / p95 / max)

| merchant | orig median | P1 median | P2 median | P2 p95 | P2 max | Δ P2 median vs P1 |
| --- | --- | --- | --- | --- | --- | --- |
| ACM | 10 | 10 | 9 | 23 | 37 | -1 |
| KRG | 10 | 10 | 10.0 | 26 | 40 | +0 |
| TBL | 4 | 4 | 4 | 5 | 7 | +0 |
| TJX | 6 | 6 | 6.0 | 10 | 13 | +0 |
| WDX | 10 | 10 | 11.0 | 30 | 40 | +1 |

### 4.2 — median ticket per merchant

| merchant | orig median | P1 median | P2 median | Δ P2 vs P1 |
| --- | --- | --- | --- | --- |
| ACM | 79.48 | 80.79 | 72.57 | -8.22 |
| KRG | 77.83 | 77.16 | 77.75 | +0.59 |
| TBL | 20.9 | 20.87 | 20.75 | -0.12 |
| TJX | 350.44 | 351.51 | 354.14 | +2.63 |
| WDX | 74.59 | 72.59 | 80.86 | +8.27 |

### 5.1 — category emphasis deltas (key categories)

| merchant | category | orig pct | P1 pct | P2 pct | Δ P2 vs P1 (pp) |
| --- | --- | --- | --- | --- | --- |
| KRG | PRODUCE | 9.3 | 9.4 | 10.8 | +1.4 |
| KRG | MEAT | 17.2 | 17.3 | 18.4 | +1.1 |
| KRG | DAIRY | 9.9 | 9.9 | 9.5 | -0.4 |
| KRG | PANTRY | 14.2 | 14.1 | 13.7 | -0.4 |
| KRG | HOUSEHOLD | 9.5 | 9.4 | 9.2 | -0.2 |
| ACM | PRODUCE | 9.3 | 9.3 | 9.3 | +0.0 |
| ACM | MEAT | 16.8 | 16.7 | 16.5 | -0.2 |
| ACM | DAIRY | 9.8 | 9.7 | 11.8 | +2.1 |
| ACM | PANTRY | 14.2 | 14.1 | 12.2 | -1.9 |
| ACM | HOUSEHOLD | 9.7 | 9.6 | 9.0 | -0.6 |
| WDX | PRODUCE | 9.1 | 9.2 | 7.7 | -1.5 |
| WDX | FROZEN | — | — | 7.7 | — |
| WDX | MEAT | 17.6 | 17.5 | 17.3 | -0.2 |
| WDX | DAIRY | 10.2 | 10.3 | 10.1 | -0.2 |
| WDX | PANTRY | 14.3 | 14.3 | 16.7 | +2.4 |
| WDX | HOUSEHOLD | 9.4 | 9.4 | 9.1 | -0.3 |

### 5.3 — mean txns per store per merchant

| merchant | orig mean | P2 mean | Δ |
| --- | --- | --- | --- |
| ACM | 2,438 | 2,418 | -20 |
| KRG | 2,194 | 2,192 | -2 |
| TBL | 1,237 | 903 | -334 |
| TJX | 1,445 | 1,516 | +71 |
| WDX | 2,436 | 2,450 | +14 |
