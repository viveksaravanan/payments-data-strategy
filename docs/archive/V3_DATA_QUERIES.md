# V3 Data Queries

Read-only inspection of the Phase 1.5 regenerated DB. No interpretation in this doc — that conversation happens in chat based on these results.

DB info: `/Users/viveksaravanan/Documents/payments-data-strategy/data/payments.db`, 2,849 MB on disk, generated 2026-05-17T22:30:36. 236,512 transactions across 10,000 customers. 10 materialized lake tables + 30 per-viewer tenant views (Phase 1.5).


## Section 1. Anomaly verification


### 1.1 University City decline — daily traffic by grocer (stage-aggregated)

```sql
WITH labeled AS (
  SELECT
    t.merchant_id,
    DATE(t.txn_ts) AS day,
    CASE
      WHEN DATE(t.txn_ts) < '2026-04-12' THEN '0. Baseline (Mar 1 – Apr 11)'
      WHEN DATE(t.txn_ts) BETWEEN '2026-04-12' AND '2026-04-18' THEN '1. Stage 1 (Apr 12–18)'
      WHEN DATE(t.txn_ts) BETWEEN '2026-04-19' AND '2026-04-25' THEN '2. Stage 2 (Apr 19–25)'
      WHEN DATE(t.txn_ts) BETWEEN '2026-04-26' AND '2026-05-02' THEN '3. Stage 3 (Apr 26 – May 2)'
      ELSE '4. Stage 4 (May 3–29)'
    END AS stage
  FROM tenant_transactions t
  JOIN tenant_stores s ON s.store_id = t.store_id
  WHERE s.neighborhood = 'University City'
    AND t.merchant_id IN ('KRG','ACM','WDX')
),
daily AS (
  SELECT merchant_id, stage, day, COUNT(*) AS daily_txns
  FROM labeled
  GROUP BY merchant_id, stage, day
),
stage_stats AS (
  SELECT merchant_id, stage,
         ROUND(AVG(daily_txns), 1) AS mean_daily_txns,
         COUNT(*) AS n_days
  FROM daily
  GROUP BY merchant_id, stage
),
baseline AS (
  SELECT merchant_id, mean_daily_txns AS baseline_mean
  FROM stage_stats
  WHERE stage = '0. Baseline (Mar 1 – Apr 11)'
)
SELECT ss.merchant_id, ss.stage, ss.mean_daily_txns, ss.n_days,
       ROUND(ss.mean_daily_txns / b.baseline_mean, 3) AS ratio_to_baseline
FROM stage_stats ss
JOIN baseline b ON b.merchant_id = ss.merchant_id
ORDER BY ss.merchant_id, ss.stage
```

| merchant_id | stage | mean_daily_txns | n_days | ratio_to_baseline |
| --- | --- | --- | --- | --- |
| ACM | 0. Baseline (Mar 1 – Apr 11) | 77.3 | 42 | 1 |
| ACM | 1. Stage 1 (Apr 12–18) | 82.1 | 7 | 1.062 |
| ACM | 2. Stage 2 (Apr 19–25) | 73.6 | 7 | 0.952 |
| ACM | 3. Stage 3 (Apr 26 – May 2) | 54.9 | 7 | 0.71 |
| ACM | 4. Stage 4 (May 3–29) | 65.9 | 27 | 0.853 |
| KRG | 0. Baseline (Mar 1 – Apr 11) | 46.1 | 42 | 1 |
| KRG | 1. Stage 1 (Apr 12–18) | 49.9 | 7 | 1.082 |
| KRG | 2. Stage 2 (Apr 19–25) | 37.9 | 7 | 0.822 |
| KRG | 3. Stage 3 (Apr 26 – May 2) | 29.1 | 7 | 0.631 |
| KRG | 4. Stage 4 (May 3–29) | 33.8 | 27 | 0.733 |
| WDX | 0. Baseline (Mar 1 – Apr 11) | 82.3 | 42 | 1 |
| WDX | 1. Stage 1 (Apr 12–18) | 89.3 | 7 | 1.085 |
| WDX | 2. Stage 2 (Apr 19–25) | 73.9 | 7 | 0.898 |
| WDX | 3. Stage 3 (Apr 26 – May 2) | 59.4 | 7 | 0.722 |
| WDX | 4. Stage 4 (May 3–29) | 70.6 | 27 | 0.858 |

Returned 15 rows (all shown above). Columns: merchant_id, stage, mean_daily_txns, n_days, ratio_to_baseline. Numeric ranges — `mean_daily_txns`: 29.1 to 89.3; `n_days`: 7 to 42; `ratio_to_baseline`: 0.631 to 1.085.


### 1.2 Plaza Midwood avocado spike — daily avocado units per grocer (Apr 18–26 window)

```sql
SELECT
    DATE(t.txn_ts) AS day,
    t.merchant_id,
    SUM(i.qty) AS avocado_units
FROM tenant_transaction_items i
JOIN tenant_transactions t ON t.txn_id = i.txn_id
JOIN tenant_stores s ON s.store_id = t.store_id
JOIN tenant_products p ON p.sku = i.sku
WHERE p.name LIKE '%avocado%'
  AND s.neighborhood = 'Plaza Midwood'
  AND t.merchant_id IN ('KRG','ACM','WDX')
  AND DATE(t.txn_ts) BETWEEN '2026-04-18' AND '2026-04-26'
GROUP BY day, t.merchant_id
ORDER BY day, t.merchant_id
```

| day | merchant_id | avocado_units |
| --- | --- | --- |
| 2026-04-18 | ACM | 10 |
| 2026-04-18 | KRG | 4 |
| 2026-04-19 | ACM | 13 |
| 2026-04-19 | KRG | 15 |
| 2026-04-20 | ACM | 3 |
| 2026-04-20 | KRG | 6 |
| 2026-04-20 | WDX | 2 |
| 2026-04-21 | ACM | 11 |
| 2026-04-21 | KRG | 9 |
| 2026-04-21 | WDX | 3 |
| 2026-04-22 | ACM | 4 |
| 2026-04-22 | KRG | 21 |
| 2026-04-22 | WDX | 5 |
| 2026-04-23 | ACM | 7 |
| 2026-04-23 | KRG | 26 |
| 2026-04-24 | ACM | 5 |
| 2026-04-24 | KRG | 16 |
| 2026-04-24 | WDX | 1 |
| 2026-04-25 | ACM | 17 |
| 2026-04-25 | KRG | 30 |
| 2026-04-25 | WDX | 11 |
| 2026-04-26 | ACM | 11 |
| 2026-04-26 | KRG | 16 |
| 2026-04-26 | WDX | 2 |

Returned 24 rows (all shown above). Columns: day, merchant_id, avocado_units. Numeric ranges — `avocado_units`: 1 to 30.


### 1.3 Pasta promo divergence — pasta units in promo windows vs prior baselines

```sql
WITH promo_buckets AS (
  SELECT 'KRG' AS merchant_id, '0. KRG pre-promo (Apr 8–14)'  AS bucket, '2026-04-08' AS s, '2026-04-14' AS e UNION ALL
  SELECT 'KRG',                  '1. KRG promo (Apr 15–21)',           '2026-04-15',          '2026-04-21'    UNION ALL
  SELECT 'ACM',                  '0. ACM pre-promo (Apr 12–18)',       '2026-04-12',          '2026-04-18'    UNION ALL
  SELECT 'ACM',                  '1. ACM promo (Apr 19–25)',           '2026-04-19',          '2026-04-25'    UNION ALL
  SELECT 'WDX',                  '0. WDX pre-promo (Apr 15–21)',       '2026-04-15',          '2026-04-21'    UNION ALL
  SELECT 'WDX',                  '1. WDX promo (Apr 22–28)',           '2026-04-22',          '2026-04-28'
)
SELECT
  pb.merchant_id,
  pb.bucket,
  COALESCE(SUM(i.qty), 0) AS pasta_units,
  COALESCE(COUNT(DISTINCT t.txn_id), 0) AS pasta_txns
FROM promo_buckets pb
LEFT JOIN tenant_transactions t
  ON t.merchant_id = pb.merchant_id
  AND DATE(t.txn_ts) BETWEEN pb.s AND pb.e
LEFT JOIN tenant_transaction_items i ON i.txn_id = t.txn_id
LEFT JOIN tenant_products p ON p.sku = i.sku AND p.subcategory = 'pasta'
GROUP BY pb.merchant_id, pb.bucket
ORDER BY pb.merchant_id, pb.bucket
```

| merchant_id | bucket | pasta_units | pasta_txns |
| --- | --- | --- | --- |
| ACM | 0. ACM pre-promo (Apr 12–18) | 80,437 | 4,690 |
| ACM | 1. ACM promo (Apr 19–25) | 80,582 | 4,750 |
| KRG | 0. KRG pre-promo (Apr 8–14) | 84,186 | 5,022 |
| KRG | 1. KRG promo (Apr 15–21) | 87,708 | 5,136 |
| WDX | 0. WDX pre-promo (Apr 15–21) | 64,042 | 3,886 |
| WDX | 1. WDX promo (Apr 22–28) | 59,657 | 3,552 |

Returned 6 rows (all shown above). Columns: merchant_id, bucket, pasta_units, pasta_txns. Numeric ranges — `pasta_units`: 59,657 to 87,708; `pasta_txns`: 3,552 to 5,136.


## Section 2. Dairy chart reality


### 2.1 KRG dairy staples vs grocery peers — unit price and gap (top 10 by peer volume)

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
         ROUND(AVG(i.unit_price), 2) AS krg_price,
         SUM(i.qty) AS krg_qty
  FROM tenant_transaction_items i
  JOIN tenant_products p ON p.sku = i.sku
  WHERE p.merchant_id = 'KRG' AND p.category = 'DAIRY'
  GROUP BY p.name
),
peer_dairy AS (
  SELECT canonical_name,
         ROUND(AVG(unit_price), 2) AS peer_avg_price
  FROM lake_transactions_KRG
  WHERE category = 'DAIRY' AND peer_segment = 'grocery'
  GROUP BY canonical_name
)
SELECT
  td.canonical_name,
  kd.krg_price,
  pd.peer_avg_price,
  ROUND(kd.krg_price - pd.peer_avg_price, 2) AS gap_usd,
  ROUND(100.0 * (kd.krg_price - pd.peer_avg_price) / pd.peer_avg_price, 1) AS krg_vs_peer_pct,
  td.peer_qty
FROM top_dairy_canonical td
LEFT JOIN krg_dairy kd ON kd.canonical_name = td.canonical_name
LEFT JOIN peer_dairy pd ON pd.canonical_name = td.canonical_name
ORDER BY td.peer_qty DESC
```

| canonical_name | krg_price | peer_avg_price | gap_usd | krg_vs_peer_pct | peer_qty |
| --- | --- | --- | --- | --- | --- |
| Half and half (quart) | 3.93 | 3.99 | -0.06 | -1.5 | 4,070 |
| Sharp cheddar shredded (8 oz) | 3.96 | 4.03 | -0.07 | -1.7 | 4,019 |
| Cream cheese whipped (8 oz) | 3 | 3.03 | -0.03 | -1 | 3,513 |
| Lactose-free 2% milk (half gallon) | 5.07 | 4.95 | 0.12 | 2.4 | 3,503 |
| Pepper jack shredded (8 oz) | 4.01 | 4.02 | -0.01 | -0.2 | 3,438 |
| Crescent roll dough (8 oz) | 3.53 | 3.48 | 0.05 | 1.4 | 3,431 |
| Eggs jumbo (dozen) | 5.01 | 5.02 | -0.01 | -0.2 | 3,430 |
| Organic Greek yogurt (32 oz) | 7.08 | 7.04 | 0.04 | 0.6 | 3,415 |
| Skyr Icelandic yogurt (5.3 oz) | 1.81 | 1.79 | 0.02 | 1.1 | 3,397 |
| Coffee creamer vanilla (32 oz) | 3.94 | 3.99 | -0.05 | -1.3 | 3,397 |

Returned 10 rows (all shown above). Columns: canonical_name, krg_price, peer_avg_price, gap_usd, krg_vs_peer_pct, peer_qty. Numeric ranges — `krg_price`: 1.81 to 7.08; `peer_avg_price`: 1.79 to 7.04; `gap_usd`: -0.07 to 0.12; `krg_vs_peer_pct`: -1.7 to 2.4; `peer_qty`: 3,397 to 4,070.


### 2.2 KRG dairy attach: line-level dairy share vs grocery peers (lake metric)

```sql
-- KRG via tenant (line-level dairy share)
SELECT
  'KRG (tenant)' AS source,
  COUNT(*) AS total_lines,
  SUM(CASE WHEN p.category = 'DAIRY' THEN 1 ELSE 0 END) AS dairy_lines,
  ROUND(100.0 * SUM(CASE WHEN p.category = 'DAIRY' THEN 1 ELSE 0 END) / COUNT(*), 1) AS dairy_line_share_pct
FROM tenant_transaction_items i
JOIN tenant_products p ON p.sku = i.sku
WHERE p.merchant_id = 'KRG'
UNION ALL
-- peer_a + peer_b (grocery peers) via lake_KRG
SELECT
  'peer_' || SUBSTR(peer_id, -1, 1) || ' (lake_KRG)' AS source,
  COUNT(*) AS total_lines,
  SUM(CASE WHEN category = 'DAIRY' THEN 1 ELSE 0 END) AS dairy_lines,
  ROUND(100.0 * SUM(CASE WHEN category = 'DAIRY' THEN 1 ELSE 0 END) / COUNT(*), 1) AS dairy_line_share_pct
FROM lake_transactions_KRG
WHERE peer_segment = 'grocery'
GROUP BY peer_id
ORDER BY source
```

| source | total_lines | dairy_lines | dairy_line_share_pct |
| --- | --- | --- | --- |
| KRG (tenant) | 743,609 | 102,984 | 13.8 |
| peer_a (lake_KRG) | 686,175 | 94,640 | 13.8 |
| peer_b (lake_KRG) | 548,103 | 75,285 | 13.7 |

Returned 3 rows (all shown above). Columns: source, total_lines, dairy_lines, dairy_line_share_pct. Numeric ranges — `total_lines`: 548,103 to 743,609; `dairy_lines`: 75,285 to 102,984; `dairy_line_share_pct`: 13.7 to 13.8.


## Section 3. Customer overlap


### 3.1 Customer count per merchant (via tenant_view_<M>_customers)

```sql
SELECT 'KRG' AS merchant, COUNT(*) AS n_customers FROM tenant_view_KRG_customers
UNION ALL SELECT 'ACM', COUNT(*) FROM tenant_view_ACM_customers
UNION ALL SELECT 'WDX', COUNT(*) FROM tenant_view_WDX_customers
UNION ALL SELECT 'TBL', COUNT(*) FROM tenant_view_TBL_customers
UNION ALL SELECT 'TJX', COUNT(*) FROM tenant_view_TJX_customers
ORDER BY merchant
```

| merchant | n_customers |
| --- | --- |
| ACM | 8,019 |
| KRG | 7,471 |
| TBL | 8,453 |
| TJX | 4,718 |
| WDX | 5,951 |

Returned 5 rows (all shown above). Columns: merchant, n_customers. Numeric ranges — `n_customers`: 4,718 to 8,453.


### 3.2 Customer-merchant intersection — pairwise shared customers

```sql
WITH cm AS (
  SELECT DISTINCT t.merchant_id, t.customer_id
  FROM tenant_transactions t
)
SELECT
  a.merchant_id AS merchant_a,
  b.merchant_id AS merchant_b,
  COUNT(*) AS shared_customers
FROM cm a
JOIN cm b
  ON a.customer_id = b.customer_id
  AND a.merchant_id < b.merchant_id
GROUP BY a.merchant_id, b.merchant_id
ORDER BY a.merchant_id, b.merchant_id
```

| merchant_a | merchant_b | shared_customers |
| --- | --- | --- |
| ACM | KRG | 5,939 |
| ACM | TBL | 6,763 |
| ACM | TJX | 3,812 |
| ACM | WDX | 4,573 |
| KRG | TBL | 6,325 |
| KRG | TJX | 3,473 |
| KRG | WDX | 4,019 |
| TBL | TJX | 3,979 |
| TBL | WDX | 5,047 |
| TJX | WDX | 2,795 |

Returned 10 rows (all shown above). Columns: merchant_a, merchant_b, shared_customers. Numeric ranges — `shared_customers`: 2,795 to 6,763.


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
| 1 | 165 | 1.7 |
| 2 | 1,170 | 11.7 |
| 3 | 3,657 | 36.6 |
| 4 | 3,844 | 38.5 |
| 5 | 1,152 | 11.5 |

Returned 5 rows (all shown above). Columns: n_merchants, n_customers, pct_of_panel. Numeric ranges — `n_merchants`: 1 to 5; `n_customers`: 165 to 3,844; `pct_of_panel`: 1.7 to 38.5.


## Section 4. Basket and transaction shape


### 4.1 Basket size (items per transaction) per merchant

```sql
WITH bs AS (SELECT t.merchant_id, t.txn_id, COUNT(*) AS items
            FROM tenant_transactions t
            JOIN tenant_transaction_items i ON i.txn_id = t.txn_id
            GROUP BY t.merchant_id, t.txn_id)
SELECT merchant_id, COUNT(*), AVG(items), MEDIAN(items),
       MIN(items), MAX(items) FROM bs GROUP BY merchant_id;
-- (median computed in Python; SQLite has no built-in MEDIAN)
```

| merchant_id | n_txns | mean_items | median_items | min_items | max_items |
| --- | --- | --- | --- | --- | --- |
| ACM | 60,948 | 11.26 | 10 | 3 | 40 |
| KRG | 65,809 | 11.3 | 10 | 3 | 40 |
| TBL | 49,471 | 3.88 | 4 | 2 | 7 |
| TJX | 11,556 | 6.14 | 6 | 1 | 13 |
| WDX | 48,728 | 11.25 | 10 | 3 | 39 |

Returned 5 rows (all shown above). Columns: merchant_id, n_txns, mean_items, median_items, min_items, max_items. Numeric ranges — `n_txns`: 11,556 to 65,809; `mean_items`: 3.88 to 11.3; `median_items`: 4 to 10; `min_items`: 1 to 3; `max_items`: 7 to 40.


### 4.2 Transaction ticket value per merchant

```sql
SELECT merchant_id, COUNT(*), AVG(txn_total), MEDIAN(txn_total),
       MIN(txn_total), MAX(txn_total)
FROM tenant_transactions GROUP BY merchant_id;
-- (median computed in Python)
```

| merchant_id | n_txns | mean_ticket | median_ticket | min_ticket | max_ticket |
| --- | --- | --- | --- | --- | --- |
| ACM | 60,948 | 95.34 | 79.48 | 8.18 | 594.12 |
| KRG | 65,809 | 92.89 | 77.83 | 5.95 | 598.82 |
| TBL | 49,471 | 22.14 | 20.9 | 3.74 | 73.91 |
| TJX | 11,556 | 371.17 | 350.44 | 12.57 | 1,386.97 |
| WDX | 48,728 | 88.92 | 74.59 | 3.85 | 510.42 |

Returned 5 rows (all shown above). Columns: merchant_id, n_txns, mean_ticket, median_ticket, min_ticket, max_ticket. Numeric ranges — `n_txns`: 11,556 to 65,809; `mean_ticket`: 22.14 to 371.17; `median_ticket`: 20.9 to 350.44; `min_ticket`: 3.74 to 12.57; `max_ticket`: 73.91 to 1,386.97.


### 4.3 Daily transaction count per merchant — distribution

```sql
WITH d AS (
  SELECT merchant_id, DATE(txn_ts) AS day, COUNT(*) AS daily_txns
  FROM tenant_transactions GROUP BY merchant_id, day
)
SELECT merchant_id, COUNT(*) AS n_days,
       ROUND(AVG(daily_txns), 1) AS mean_daily,
       MIN(daily_txns) AS min_daily,
       MAX(daily_txns) AS max_daily
FROM d GROUP BY merchant_id ORDER BY merchant_id
```

| merchant_id | n_days | mean_daily | min_daily | max_daily |
| --- | --- | --- | --- | --- |
| ACM | 90 | 677.2 | 512 | 1,193 |
| KRG | 90 | 731.2 | 526 | 1,310 |
| TBL | 90 | 549.7 | 407 | 1,040 |
| TJX | 90 | 128.4 | 74 | 209 |
| WDX | 90 | 541.4 | 382 | 848 |

Returned 5 rows (all shown above). Columns: merchant_id, n_days, mean_daily, min_daily, max_daily. Numeric ranges — `n_days`: 90 to 90; `mean_daily`: 128.4 to 731.2; `min_daily`: 74 to 526; `max_daily`: 209 to 1,310.


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
| ACM | 8,019 | 6,429 | 80.2 |
| KRG | 7,471 | 6,214 | 83.2 |
| TBL | 8,453 | 6,221 | 73.6 |
| TJX | 4,718 | 2,945 | 62.4 |
| WDX | 5,951 | 4,681 | 78.7 |

Returned 5 rows (all shown above). Columns: merchant_id, total_customers, repeat_customers, repeat_pct. Numeric ranges — `total_customers`: 4,718 to 8,453; `repeat_customers`: 2,945 to 6,429; `repeat_pct`: 62.4 to 83.2.


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
| ACM | 1 | MEAT | 954,525.00 | 16.8 |
| ACM | 2 | PANTRY | 804,368.00 | 14.2 |
| ACM | 3 | DAIRY | 555,101.00 | 9.8 |
| ACM | 4 | HOUSEHOLD | 549,380.00 | 9.7 |
| ACM | 5 | PRODUCE | 526,216.00 | 9.3 |
| KRG | 1 | MEAT | 1,030,487.00 | 17.2 |
| KRG | 2 | PANTRY | 847,699.00 | 14.2 |
| KRG | 3 | DAIRY | 594,331.00 | 9.9 |
| KRG | 4 | HOUSEHOLD | 569,989.00 | 9.5 |
| KRG | 5 | PRODUCE | 558,985.00 | 9.3 |
| TBL | 1 | COMBO | 237,181.00 | 23.2 |
| TBL | 2 | DRINK | 176,025.00 | 17.2 |
| TBL | 3 | BURR | 144,405.00 | 14.1 |
| TBL | 4 | SIDE | 139,869.00 | 13.7 |
| TBL | 5 | SPEC | 139,720.00 | 13.6 |
| TJX | 1 | ACC | 1,363,700.00 | 34 |
| TJX | 2 | SHO | 524,068.00 | 13.1 |
| TJX | 3 | WOM | 516,222.00 | 12.9 |
| TJX | 4 | MEN | 429,102.00 | 10.7 |
| TJX | 5 | JEW | 422,904.00 | 10.5 |
| WDX | 1 | MEAT | 745,826.00 | 17.6 |
| WDX | 2 | PANTRY | 607,487.00 | 14.3 |
| WDX | 3 | DAIRY | 433,662.00 | 10.2 |
| WDX | 4 | HOUSEHOLD | 397,325.00 | 9.4 |
| WDX | 5 | PRODUCE | 387,862.00 | 9.1 |

Returned 25 rows (all shown above). Columns: merchant_id, category_rank, category, revenue_usd, pct_of_merchant_revenue. Numeric ranges — `category_rank`: 1 to 5; `revenue_usd`: 139,720 to 1.3637e+06; `pct_of_merchant_revenue`: 9.1 to 34.


### 5.2 Top 6 SKUs by line-volume per merchant

```sql
WITH sku_volume AS (
  SELECT t.merchant_id, p.name, p.category, COUNT(*) AS lines, SUM(i.qty) AS units
  FROM tenant_transaction_items i
  JOIN tenant_transactions t ON t.txn_id = i.txn_id
  JOIN tenant_products p ON p.sku = i.sku
  GROUP BY t.merchant_id, p.name, p.category
),
ranked AS (
  SELECT merchant_id, name, category, lines, units,
         ROW_NUMBER() OVER (PARTITION BY merchant_id ORDER BY lines DESC) AS rk
  FROM sku_volume
)
SELECT merchant_id, rk AS sku_rank, name, category, lines, units
FROM ranked
WHERE rk <= 6
ORDER BY merchant_id, rk
```

| merchant_id | sku_rank | name | category | lines | units |
| --- | --- | --- | --- | --- | --- |
| ACM | 1 | Sharp cheddar shredded (8 oz) | DAIRY | 1,696 | 2,494 |
| ACM | 2 | 80/20 ground beef (lb) | MEAT | 1,473 | 2,058 |
| ACM | 3 | Half and half (quart) | DAIRY | 1,455 | 2,136 |
| ACM | 4 | Babybel mini cheese wheels (12-count) | DAIRY | 1,240 | 1,793 |
| ACM | 5 | Lactose-free 2% milk (half gallon) | DAIRY | 1,237 | 1,813 |
| ACM | 6 | Crescent roll dough (8 oz) | DAIRY | 1,236 | 1,857 |
| KRG | 1 | Sharp cheddar shredded (8 oz) | DAIRY | 1,755 | 2,598 |
| KRG | 2 | Half and half (quart) | DAIRY | 1,403 | 2,074 |
| KRG | 3 | 80/20 ground beef (lb) | MEAT | 1,357 | 1,940 |
| KRG | 4 | Sharp cheddar block (8 oz) | DAIRY | 1,212 | 1,774 |
| KRG | 5 | Coffee creamer hazelnut (32 oz) | DAIRY | 1,207 | 1,788 |
| KRG | 6 | Almond milk unsweetened (half gallon) | DAIRY | 1,205 | 1,748 |
| TBL | 1 | Cinnamon Twists | SIDE | 10,419 | 13,559 |
| TBL | 2 | Chalupa Combo | COMBO | 4,017 | 4,460 |
| TBL | 3 | Black Beans & Rice | SIDE | 3,980 | 5,177 |
| TBL | 4 | Crunchwrap Combo | COMBO | 3,967 | 4,457 |
| TBL | 5 | Build Your Own Cravings Box | COMBO | 3,918 | 4,349 |
| TBL | 6 | Chips & Cheese | SIDE | 3,911 | 5,103 |
| TJX | 1 | Earrings | JEW | 3,218 | 3,664 |
| TJX | 2 | Hand cream | BTY | 2,619 | 3,054 |
| TJX | 3 | Body lotion | BTY | 2,513 | 2,946 |
| TJX | 4 | Crossbody bag | ACC | 1,998 | 2,275 |
| TJX | 5 | Designer handbag | ACC | 1,954 | 2,227 |
| TJX | 6 | Sweater | MEN | 1,814 | 2,004 |
| WDX | 1 | Half and half (quart) | DAIRY | 1,272 | 1,934 |
| WDX | 2 | Eggs jumbo (dozen) | DAIRY | 1,137 | 1,630 |
| WDX | 3 | Coffee creamer vanilla (32 oz) | DAIRY | 1,132 | 1,674 |
| WDX | 4 | String cheese mozzarella (12-count) | DAIRY | 1,131 | 1,667 |
| WDX | 5 | Pepper jack shredded (8 oz) | DAIRY | 1,129 | 1,659 |
| WDX | 6 | Butter spread tub (15 oz) | DAIRY | 1,125 | 1,666 |

Returned 30 rows (all shown above). Columns: merchant_id, sku_rank, name, category, lines, units. Numeric ranges — `sku_rank`: 1 to 6; `lines`: 1,125 to 10,419; `units`: 1,630 to 13,559.


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
| ACM | 25 | 2,438.00 | 2,107 | 2,651 | 22.3 |
| KRG | 30 | 2,194.00 | 1,831 | 2,464 | 28.9 |
| TBL | 40 | 1,237.00 | 1,035 | 1,404 | 29.8 |
| TJX | 8 | 1,445.00 | 1,413 | 1,489 | 5.3 |
| WDX | 20 | 2,436.00 | 2,156 | 2,768 | 25.1 |

Returned 5 rows (all shown above). Columns: merchant_id, n_stores, mean_txns_per_store, min_txns_per_store, max_txns_per_store, range_pct_of_mean. Numeric ranges — `n_stores`: 8 to 40; `mean_txns_per_store`: 1,237 to 2,438; `min_txns_per_store`: 1,035 to 2,156; `max_txns_per_store`: 1,404 to 2,768; `range_pct_of_mean`: 5.3 to 29.8.


### 5.4 Promo vs non-promo trip ticket comparison per merchant

```sql
WITH txn_promo AS (
  SELECT t.txn_id, t.merchant_id, t.txn_total,
         MAX(CASE WHEN i.promo_id IS NOT NULL THEN 1 ELSE 0 END) AS has_promo
  FROM tenant_transactions t
  JOIN tenant_transaction_items i ON i.txn_id = t.txn_id
  GROUP BY t.txn_id, t.merchant_id, t.txn_total
)
SELECT
  merchant_id,
  SUM(CASE WHEN has_promo = 0 THEN 1 ELSE 0 END) AS non_promo_txns,
  ROUND(AVG(CASE WHEN has_promo = 0 THEN txn_total END), 2) AS non_promo_mean_ticket,
  SUM(CASE WHEN has_promo = 1 THEN 1 ELSE 0 END) AS promo_txns,
  ROUND(AVG(CASE WHEN has_promo = 1 THEN txn_total END), 2) AS promo_mean_ticket,
  ROUND(100.0 *
    (AVG(CASE WHEN has_promo = 1 THEN txn_total END) -
     AVG(CASE WHEN has_promo = 0 THEN txn_total END)) /
     AVG(CASE WHEN has_promo = 0 THEN txn_total END), 1) AS promo_vs_non_pct
FROM txn_promo
GROUP BY merchant_id
ORDER BY merchant_id
```

| merchant_id | non_promo_txns | non_promo_mean_ticket | promo_txns | promo_mean_ticket | promo_vs_non_pct |
| --- | --- | --- | --- | --- | --- |
| ACM | 21,378 | 80.45 | 39,570 | 103.39 | 28.5 |
| KRG | 27,675 | 82.82 | 38,134 | 100.2 | 21 |
| TBL | 42,941 | 22.23 | 6,530 | 21.56 | -3 |
| TJX | 10,130 | 367.17 | 1,426 | 399.53 | 8.8 |
| WDX | 22,263 | 79.85 | 26,465 | 96.56 | 20.9 |

Returned 5 rows (all shown above). Columns: merchant_id, non_promo_txns, non_promo_mean_ticket, promo_txns, promo_mean_ticket, promo_vs_non_pct. Numeric ranges — `non_promo_txns`: 10,130 to 42,941; `non_promo_mean_ticket`: 22.23 to 367.17; `promo_txns`: 1,426 to 39,570; `promo_mean_ticket`: 21.56 to 399.53; `promo_vs_non_pct`: -3 to 28.5.


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
| 2026-02-23 | 52 |
| 2026-03-02 | 324 |
| 2026-03-09 | 322 |
| 2026-03-16 | 333 |
| 2026-03-23 | 300 |
| 2026-03-30 | 324 |
| 2026-04-06 | 336 |
| 2026-04-13 | 350 |
| 2026-04-20 | 247 |
| 2026-04-27 | 210 |
| 2026-05-04 | 202 |
| 2026-05-11 | 232 |
| 2026-05-18 | 243 |
| 2026-05-25 | 191 |

Returned 14 rows (all shown above). Columns: week_starting_sunday, uc_txns_this_week. Numeric ranges — `uc_txns_this_week`: 52 to 350.


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
| ACM | 2026-02-23 | 114 |
| ACM | 2026-03-02 | 507 |
| ACM | 2026-03-09 | 510 |
| ACM | 2026-03-16 | 544 |
| ACM | 2026-03-23 | 564 |
| ACM | 2026-03-30 | 578 |
| ACM | 2026-04-06 | 542 |
| ACM | 2026-04-13 | 571 |
| ACM | 2026-04-20 | 476 |
| ACM | 2026-04-27 | 408 |
| ACM | 2026-05-04 | 400 |
| ACM | 2026-05-11 | 433 |
| ACM | 2026-05-18 | 471 |
| ACM | 2026-05-25 | 380 |
| WDX | 2026-02-23 | 95 |
| WDX | 2026-03-02 | 573 |
| WDX | 2026-03-09 | 621 |
| WDX | 2026-03-16 | 561 |
| WDX | 2026-03-23 | 574 |
| WDX | 2026-03-30 | 576 |
| WDX | 2026-04-06 | 578 |
| WDX | 2026-04-13 | 630 |
| WDX | 2026-04-20 | 455 |
| WDX | 2026-04-27 | 457 |
| WDX | 2026-05-04 | 448 |
| WDX | 2026-05-11 | 464 |
| WDX | 2026-05-18 | 495 |
| WDX | 2026-05-25 | 393 |

Returned 28 rows (all shown above). Columns: merchant_id, week_starting_sunday, uc_txns_this_week. Numeric ranges — `uc_txns_this_week`: 95 to 630.


### 6.3 KRG comparison neighborhood — weekly transactions, comparable store count

```sql
-- First subquery: store counts to support choosing a comparable neighborhood.
-- Output is store-count-by-neighborhood; the next query (6.3b) uses the
-- neighborhood we pick.
SELECT s.neighborhood, COUNT(*) AS n_stores
FROM tenant_stores s
WHERE s.merchant_id = 'KRG'
GROUP BY s.neighborhood
ORDER BY n_stores
```

| neighborhood | n_stores |
| --- | --- |
| Ballantyne | 2 |
| Matthews | 2 |
| NoDa | 2 |
| Pineville | 2 |
| SouthPark | 2 |
| University City | 2 |
| Uptown / Center City | 2 |
| Mooresville | 3 |
| Plaza Midwood | 5 |
| Dilworth | 8 |

Returned 10 rows (all shown above). Columns: neighborhood, n_stores. Numeric ranges — `n_stores`: 2 to 8.


### 6.3b KRG Ballantyne (comparable 2 stores) — weekly transactions, same window

```sql
SELECT
          DATE(t.txn_ts, 'weekday 0', '-6 days') AS week_starting_sunday,
          COUNT(*) AS txns_this_week
        FROM tenant_transactions t
        JOIN tenant_stores s ON s.store_id = t.store_id
        WHERE t.merchant_id = 'KRG'
          AND s.neighborhood = 'Ballantyne'
        GROUP BY week_starting_sunday
        ORDER BY week_starting_sunday
```

| week_starting_sunday | txns_this_week |
| --- | --- |
| 2026-02-23 | 53 |
| 2026-03-02 | 324 |
| 2026-03-09 | 303 |
| 2026-03-16 | 352 |
| 2026-03-23 | 316 |
| 2026-03-30 | 315 |
| 2026-04-06 | 288 |
| 2026-04-13 | 300 |
| 2026-04-20 | 313 |
| 2026-04-27 | 291 |
| 2026-05-04 | 308 |
| 2026-05-11 | 368 |
| 2026-05-18 | 350 |
| 2026-05-25 | 318 |

Returned 14 rows (all shown above). Columns: week_starting_sunday, txns_this_week. Numeric ranges — `txns_this_week`: 53 to 368.


## Section 7. Schema and row count sanity


### 7.1 Row counts across all tenant and lake tables

```sql
SELECT 'merchants' AS table_name, COUNT(*) AS n_rows FROM merchants
UNION ALL SELECT 'tenant_customers',           COUNT(*) FROM tenant_customers
UNION ALL SELECT 'tenant_stores',              COUNT(*) FROM tenant_stores
UNION ALL SELECT 'tenant_products',            COUNT(*) FROM tenant_products
UNION ALL SELECT 'tenant_transactions',        COUNT(*) FROM tenant_transactions
UNION ALL SELECT 'tenant_transaction_items',   COUNT(*) FROM tenant_transaction_items
UNION ALL SELECT 'tenant_promotions',          COUNT(*) FROM tenant_promotions
UNION ALL SELECT 'lake_transactions_KRG',      COUNT(*) FROM lake_transactions_KRG
UNION ALL SELECT 'lake_transactions_ACM',      COUNT(*) FROM lake_transactions_ACM
UNION ALL SELECT 'lake_transactions_WDX',      COUNT(*) FROM lake_transactions_WDX
UNION ALL SELECT 'lake_transactions_TBL',      COUNT(*) FROM lake_transactions_TBL
UNION ALL SELECT 'lake_transactions_TJX',      COUNT(*) FROM lake_transactions_TJX
UNION ALL SELECT 'lake_stores_KRG',            COUNT(*) FROM lake_stores_KRG
UNION ALL SELECT 'lake_stores_ACM',            COUNT(*) FROM lake_stores_ACM
UNION ALL SELECT 'lake_stores_WDX',            COUNT(*) FROM lake_stores_WDX
UNION ALL SELECT 'lake_stores_TBL',            COUNT(*) FROM lake_stores_TBL
UNION ALL SELECT 'lake_stores_TJX',            COUNT(*) FROM lake_stores_TJX
ORDER BY table_name
```

| table_name | n_rows |
| --- | --- |
| lake_stores_ACM | 98 |
| lake_stores_KRG | 93 |
| lake_stores_TBL | 83 |
| lake_stores_TJX | 115 |
| lake_stores_WDX | 103 |
| lake_transactions_ACM | 1,554,377 |
| lake_transactions_KRG | 1,496,943 |
| lake_transactions_TBL | 2,048,825 |
| lake_transactions_TJX | 2,169,614 |
| lake_transactions_WDX | 1,692,449 |
| merchants | 5 |
| tenant_customers | 10,000 |
| tenant_products | 3,249 |
| tenant_promotions | 5,036 |
| tenant_stores | 123 |
| tenant_transaction_items | 2,240,552 |
| tenant_transactions | 236,512 |

Returned 17 rows (all shown above). Columns: table_name, n_rows. Numeric ranges — `n_rows`: 5 to 2,240,552.


## Section 8: Surprises and anomalies

Free-form notes on what I encountered while running the battery. Literal observations only — no conclusions.

1. **Section 1.3 pasta-promo volume deltas are small relative to the audit's reference multipliers.** V3_AUDIT.md §3.1 cited the anomaly module's prediction as KRG +2.2× lift, ACM 0.8× (failure), WDX +1.4× lift. Observed pasta-unit deltas, pre-promo vs promo:
   - KRG 84,186 → 87,708 (ratio 1.04)
   - ACM 80,437 → 80,582 (ratio 1.002)
   - WDX 64,042 → 59,657 (ratio 0.93)
   The anomaly module's multiplier acts on SKU selection probability *inside* the PANTRY category at trip-sampling time, not on aggregate pasta volume. Reporting the raw deltas without claiming the multiplier did or didn't fire.

2. **Section 2.1 KRG-vs-peer dairy price gaps are all within ±2.4%.** Across the top 10 dairy staples by peer volume, the largest gap is "Lactose-free 2% milk (half gallon)" at KRG +2.4% vs peer average. Seven of 10 SKUs sit within ±1.5%. The V3_VISION.md worked example imagined "+7.4% on whole milk, +8.6% on eggs" as the gold-standard chart story; observed `Eggs jumbo (dozen)` gap is −0.2% ($5.01 vs $5.02 peer avg). The full canonical name `whole milk (gallon)` does not appear in the top 10; only `Lactose-free 2% milk (half gallon)` carries the milk-staple line in this top-10 set.

3. **Section 2.2 line-level dairy share is identical (within 0.1 pt) across KRG and grocery peers.** KRG 13.8%, peer_a 13.8%, peer_b 13.7%. V3_VISION.md cited "your 41%, peer average 49%" as the example agent response; the line-level metric used here is a different definition than the trip-level one V3_VISION named (see the literal description on 2.2). Both numbers are reportable; only one is computable per peer from the lake without `customer_id`.

4. **Section 1.1 UC stage-3 ratios sit slightly above the anomaly module's predicted floors.** Module predicts (after applying per-grocer magnitude × 0.55 base): KRG 0.55, ACM 0.64, WDX 0.685. Observed Stage 3 ratios: KRG 0.631, ACM 0.710, WDX 0.722. All three grocers are above their predicted multipliers by 5–14 percentage points. The 7-day stage window covers Apr 26 – May 2; the observed values are means of 7 daily counts each.

5. **Section 1.2 KRG Plaza Midwood avocado-unit peak is on Apr 25 (30 units), not Apr 22.** The anomaly module's `DAILY_MULTIPLIER` peaks at 5.0× on Apr 22; the window also has 3.0× on Apr 23 and 1.5× on each of Apr 21 / Apr 24. Observed daily KRG PM avocado units across Apr 22–25: 21, 26, 16, 30. ACM PM avocado units in the same window range 4–17; WDX PM is sparse (1–11 units, with several missing days).

6. **Section 5.4 TBL is the only merchant where promo-trip mean ticket is *below* non-promo.** TBL: 21.56 (promo) vs 22.23 (non-promo) = −3.0%. ACM +28.5%, KRG +21.0%, TJX +8.8%, WDX +20.9%. Reporting the direction reversal as a numeric outlier.

7. **Section 3.1 customer counts per merchant view:** KRG 7,471 / ACM 8,019 / WDX 5,951 / TBL 8,453 / TJX 4,718. Sum is 34,612 — i.e., the average customer appears in ~3.5 merchant views, consistent with Section 3.3 where the 3-merchant and 4-merchant buckets together account for 75.1% of the panel.

8. **Section 3.3 panel-shopping distribution is centered at 3–4 merchants.** Buckets: 1 merchant 1.7%, 2 merchants 11.7%, 3 merchants 36.6%, 4 merchants 38.5%, 5 merchants 11.5%. Only 165 of 10,000 customers shopped at a single merchant in the window.

9. **Section 5.2 KRG and ACM top-6 SKUs are dominated by DAIRY.** ACM: 5 of 6 in DAIRY (one MEAT). KRG: 5 of 6 in DAIRY (one MEAT). WDX: 6 of 6 in DAIRY. TBL is dominated by SIDE/COMBO; TJX by JEW/BTY/ACC/MEN.

10. **Section 7.1 row counts:** tenant_transactions 236,512 / tenant_transaction_items 2,240,552 (≈ 9.5 items/txn averaged across all merchants, including TBL's small baskets). Per-viewer lake_transactions rows: KRG 1,496,943; ACM 1,554,377; WDX 1,692,449; TBL 2,048,825; TJX 2,169,614. TJX's lake has the largest row count because excluding TJX itself (which has the fewest line items) keeps the most rows; KRG's lake has the smallest because KRG itself is the largest line-item producer.

11. **DB on disk is 2,849 MB.** Roughly 2.9 GB. Pre-Phase-1.5 the same DB content would have been about 1 GB (tenant + indexes); the materialized lake adds ~9M extra rows across 5 viewers.

12. **Section 6.3 KRG store-count distribution by neighborhood is flat at 2 stores per neighborhood for 7 of 10 neighborhoods,** with the remaining three at 3 (Mooresville), 5 (Plaza Midwood), and 8 (Dilworth) stores. The script picked Ballantyne (2 stores) as the comparable non-UC neighborhood; the KRG UC vs Ballantyne weekly trajectories differ markedly during the Apr 20 – May 4 window.

13. **The `data/payments.db` file's mtime is 2026-05-17T22:30:36** — i.e., the file was rebuilt during Phase 1.5 Step 5 the previous day. This battery ran against that build; no regeneration was done for Phase 2.
