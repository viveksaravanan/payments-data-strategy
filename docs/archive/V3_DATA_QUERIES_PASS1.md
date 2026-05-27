# V3 Data Queries — Pass 1

Phase 1.6 Pass 1 re-run of the 7 named queries from `V3_DATA_QUERIES.md`. Parameter calibration applied (commit `Phase 1.6 Pass 1: calibrate affinity + pricing parameters`); no other changes. SQL is verbatim from the original file.

DB info: `/Users/viveksaravanan/Documents/payments-data-strategy/data/payments.db`, 2,776 MB on disk, regenerated 2026-05-18T23:45:02. 222,912 transactions across 10,000 customers.


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
| Sharp cheddar shredded (8 oz) | 3.96 | 4.04 | -0.08 | -2 | 4,156 |
| Half and half (quart) | 3.93 | 3.99 | -0.06 | -1.5 | 4,135 |
| Babybel mini cheese wheels (12-count) | 6.86 | 7.08 | -0.22 | -3.1 | 3,469 |
| Colby jack block (8 oz) | 3.92 | 4.01 | -0.09 | -2.2 | 3,430 |
| Skyr Icelandic yogurt (5.3 oz) | 1.81 | 1.78 | 0.03 | 1.7 | 3,429 |
| Greek yogurt vanilla (32 oz) | 5.91 | 5.93 | -0.02 | -0.3 | 3,421 |
| Whole milk (quart) | 1.97 | 2 | -0.03 | -1.5 | 3,403 |
| Parmesan shredded (5 oz) | 5.02 | 5.01 | 0.01 | 0.2 | 3,390 |
| Strawberry milk (half gallon) | 3.49 | 3.55 | -0.06 | -1.7 | 3,377 |
| Kids yogurt tubes (8-count) | 3.98 | 4.04 | -0.06 | -1.5 | 3,367 |

Returned 10 rows (all shown above). Columns: canonical_name, krg_price, peer_avg_price, gap_usd, krg_vs_peer_pct, peer_qty. Numeric ranges — `krg_price`: 1.81 to 6.86; `peer_avg_price`: 1.78 to 7.08; `gap_usd`: -0.22 to 0.03; `krg_vs_peer_pct`: -3.1 to 1.7; `peer_qty`: 3,367 to 4,156.


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
| KRG (tenant) | 744,416 | 102,964 | 13.8 |
| peer_a (lake_KRG) | 675,019 | 93,019 | 13.8 |
| peer_b (lake_KRG) | 547,918 | 75,375 | 13.8 |

Returned 3 rows (all shown above). Columns: source, total_lines, dairy_lines, dairy_line_share_pct. Numeric ranges — `total_lines`: 547,918 to 744,416; `dairy_lines`: 75,375 to 102,964; `dairy_line_share_pct`: 13.8 to 13.8.


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
| ACM | 7,431 |
| KRG | 7,034 |
| TBL | 7,708 |
| TJX | 4,797 |
| WDX | 5,394 |

Returned 5 rows (all shown above). Columns: merchant, n_customers. Numeric ranges — `n_customers`: 4,797 to 7,708.


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
| ACM | KRG | 5,193 |
| ACM | TBL | 5,718 |
| ACM | TJX | 3,582 |
| ACM | WDX | 3,805 |
| KRG | TBL | 5,463 |
| KRG | TJX | 3,364 |
| KRG | WDX | 3,286 |
| TBL | TJX | 3,698 |
| TBL | WDX | 4,168 |
| TJX | WDX | 2,601 |

Returned 10 rows (all shown above). Columns: merchant_a, merchant_b, shared_customers. Numeric ranges — `shared_customers`: 2,601 to 5,718.


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
| 1 | 366 | 3.7 |
| 2 | 1,696 | 17 |
| 3 | 3,918 | 39.3 |
| 4 | 3,138 | 31.4 |
| 5 | 860 | 8.6 |

Returned 5 rows (all shown above). Columns: n_merchants, n_customers, pct_of_panel. Numeric ranges — `n_merchants`: 1 to 5; `n_customers`: 366 to 3,918; `pct_of_panel`: 3.7 to 39.3.


## Section 4. Basket and transaction shape


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
| ACM | 7,431 | 5,814 | 78.2 |
| KRG | 7,034 | 5,881 | 83.6 |
| TBL | 7,708 | 6,946 | 90.1 |
| TJX | 4,797 | 2,895 | 60.4 |
| WDX | 5,394 | 4,576 | 84.8 |

Returned 5 rows (all shown above). Columns: merchant_id, total_customers, repeat_customers, repeat_pct. Numeric ranges — `total_customers`: 4,797 to 7,708; `repeat_customers`: 2,895 to 6,946; `repeat_pct`: 60.4 to 90.1.


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
| ACM | 1 | MEAT | 955,199.00 | 16.7 |
| ACM | 2 | PANTRY | 806,185.00 | 14.1 |
| ACM | 3 | DAIRY | 555,926.00 | 9.7 |
| ACM | 4 | HOUSEHOLD | 550,253.00 | 9.6 |
| ACM | 5 | PRODUCE | 529,321.00 | 9.3 |
| KRG | 1 | MEAT | 1,033,164.00 | 17.3 |
| KRG | 2 | PANTRY | 844,154.00 | 14.1 |
| KRG | 3 | DAIRY | 592,352.00 | 9.9 |
| KRG | 4 | HOUSEHOLD | 563,390.00 | 9.4 |
| KRG | 5 | PRODUCE | 562,629.00 | 9.4 |
| TBL | 1 | COMBO | 174,180.00 | 23.4 |
| TBL | 2 | DRINK | 128,199.00 | 17.2 |
| TBL | 3 | BURR | 103,871.00 | 13.9 |
| TBL | 4 | SIDE | 103,067.00 | 13.8 |
| TBL | 5 | SPEC | 100,809.00 | 13.5 |
| TJX | 1 | ACC | 1,389,339.00 | 34.4 |
| TJX | 2 | SHO | 546,368.00 | 13.5 |
| TJX | 3 | WOM | 508,095.00 | 12.6 |
| TJX | 4 | JEW | 427,948.00 | 10.6 |
| TJX | 5 | MEN | 427,315.00 | 10.6 |
| WDX | 1 | MEAT | 725,862.00 | 17.5 |
| WDX | 2 | PANTRY | 594,419.00 | 14.3 |
| WDX | 3 | DAIRY | 425,087.00 | 10.3 |
| WDX | 4 | HOUSEHOLD | 391,407.00 | 9.4 |
| WDX | 5 | PRODUCE | 382,680.00 | 9.2 |

Returned 25 rows (all shown above). Columns: merchant_id, category_rank, category, revenue_usd, pct_of_merchant_revenue. Numeric ranges — `category_rank`: 1 to 5; `revenue_usd`: 100,809 to 1.38934e+06; `pct_of_merchant_revenue`: 9.2 to 34.4.


## Pass 1 deltas (vs `V3_DATA_QUERIES.md`)

Literal numeric changes from the seven re-run queries. No interpretation.


### Section 2.1 — dairy staple gap shift (top 10 by peer volume)

The "top 10 by peer volume" set is recomputed from the regenerated `lake_transactions_KRG`. 7 of the 10 SKUs that were in the original top 10 dropped out of the Pass 1 top 10 (peer volume ranking shifted). Those rows are marked _(missing from Pass 1)_ below.

Pass 1 top 10 absolute range: `krg_vs_peer_pct` from -3.1 to +1.7. Original top 10 absolute range: -1.7 to +2.4.

| canonical_name | original krg_vs_peer_pct | Pass 1 krg_vs_peer_pct | abs Δ |
| --- | --- | --- | --- |
| Half and half (quart) | -1.5 | -1.5 | 0.0 |
| Sharp cheddar shredded (8 oz) | -1.7 | -2.0 | 0.3 |
| Cream cheese whipped (8 oz) | -1.0 | _(missing from Pass 1)_ | — |
| Lactose-free 2% milk (half gallon) | +2.4 | _(missing from Pass 1)_ | — |
| Pepper jack shredded (8 oz) | -0.2 | _(missing from Pass 1)_ | — |
| Crescent roll dough (8 oz) | +1.4 | _(missing from Pass 1)_ | — |
| Eggs jumbo (dozen) | -0.2 | _(missing from Pass 1)_ | — |
| Organic Greek yogurt (32 oz) | +0.6 | _(missing from Pass 1)_ | — |
| Skyr Icelandic yogurt (5.3 oz) | +1.1 | +1.7 | 0.6 |
| Coffee creamer vanilla (32 oz) | -1.3 | _(missing from Pass 1)_ | — |

### Section 3.3 — customers-by-N-merchants distribution shift

| n_merchants | original n_customers | Pass 1 n_customers | original pct | Pass 1 pct | Δ pct |
| --- | --- | --- | --- | --- | --- |
| 1 | 165 | 366 | 1.7 | 3.7 | +2.0 |
| 2 | 1,170 | 1,696 | 11.7 | 17.0 | +5.3 |
| 3 | 3,657 | 3,918 | 36.6 | 39.3 | +2.7 |
| 4 | 3,844 | 3,138 | 38.5 | 31.4 | -7.1 |
| 5 | 1,152 | 860 | 11.5 | 8.6 | -2.9 |

### Section 3.1 — customer count per merchant

| merchant | original | Pass 1 | Δ |
| --- | --- | --- | --- |
| ACM | 8,019 | 7,431 | -588 |
| KRG | 7,471 | 7,034 | -437 |
| TBL | 8,453 | 7,708 | -745 |
| TJX | 4,718 | 4,797 | +79 |
| WDX | 5,951 | 5,394 | -557 |

### Section 4.4 — repeat-customer ratio per merchant

| merchant | original repeat_pct | Pass 1 repeat_pct | Δ pp |
| --- | --- | --- | --- |
| ACM | 80.2 | 78.2 | -2.0 |
| KRG | 83.2 | 83.6 | +0.4 |
| TBL | 73.6 | 90.1 | +16.5 |
| TJX | 62.4 | 60.4 | -2.0 |
| WDX | 78.7 | 84.8 | +6.1 |

### Section 5.1 — DAIRY share of merchant revenue (category-mix stability check)

| merchant | original DAIRY pct | Pass 1 DAIRY pct | Δ pp |
| --- | --- | --- | --- |
| ACM | 9.8 | 9.7 | -0.1 |
| KRG | 9.9 | 9.9 | +0.0 |
| WDX | 10.2 | 10.3 | +0.1 |


### 2.3 KRG dairy staples — per-peer price comparison (Pass 1 verification)

Section 2.1 compared KRG to the **average** of peer_a and peer_b. With symmetric multipliers (ACM 1.05 / WDX 0.95), that average lands at 1.00 × base by construction — identical to KRG plus per-SKU noise. The pricing signal is in the **per-peer** comparison below.

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
| Babybel mini cheese wheels (12-count) | 6.86 | 7.48 | 6.63 | -8.3 | 3.5 |
| Colby jack block (8 oz) | 3.92 | 4.21 | 3.81 | -6.9 | 2.9 |
| Skyr Icelandic yogurt (5.3 oz) | 1.81 | 1.89 | 1.67 | -4.2 | 8.4 |
| Greek yogurt vanilla (32 oz) | 5.91 | 6.19 | 5.66 | -4.5 | 4.4 |
| Whole milk (quart) | 1.97 | 2.13 | 1.87 | -7.5 | 5.3 |
| Parmesan shredded (5 oz) | 5.02 | 5.31 | 4.71 | -5.5 | 6.6 |
| Strawberry milk (half gallon) | 3.49 | 3.71 | 3.36 | -5.9 | 3.9 |
| Kids yogurt tubes (8-count) | 3.98 | 4.22 | 3.85 | -5.7 | 3.4 |

Returned 10 rows. Per-peer pct summary — krg_vs_peer_a_pct: range -8.3 to -4.2, mean -6.18; krg_vs_peer_b_pct: range 2.9 to 8.4, mean 4.91.


### 2.4 KRG household staples — per-peer price comparison (loose-tier verification)

HOUSEHOLD is a **loose-tier** category (overlay multipliers ACM 1.10 / WDX 0.90). Expected per-peer gap magnitude ≈ ±10% (Pass 1 widened from the original ±7%). Same top-10-by-peer-volume construction as the dairy query.

```sql
WITH top_canonical AS (
  SELECT canonical_name, SUM(qty) AS peer_qty
  FROM lake_transactions_KRG
  WHERE category = 'HOUSEHOLD' AND peer_segment = 'grocery'
  GROUP BY canonical_name
  ORDER BY peer_qty DESC
  LIMIT 10
),
krg_cat AS (
  SELECT p.name AS canonical_name,
         ROUND(AVG(i.unit_price), 2) AS krg_price
  FROM tenant_transaction_items i
  JOIN tenant_products p ON p.sku = i.sku
  WHERE p.merchant_id = 'KRG' AND p.category = 'HOUSEHOLD'
  GROUP BY p.name
),
peer_cat AS (
  SELECT canonical_name, peer_id,
         ROUND(AVG(unit_price), 2) AS peer_price
  FROM lake_transactions_KRG
  WHERE category = 'HOUSEHOLD' AND peer_segment = 'grocery'
  GROUP BY canonical_name, peer_id
)
SELECT
  tc.canonical_name,
  kc.krg_price,
  MAX(CASE WHEN pc.peer_id = 'peer_a' THEN pc.peer_price END) AS peer_a_price,
  MAX(CASE WHEN pc.peer_id = 'peer_b' THEN pc.peer_price END) AS peer_b_price,
  ROUND(100.0 *
        (kc.krg_price - MAX(CASE WHEN pc.peer_id = 'peer_a' THEN pc.peer_price END)) /
         MAX(CASE WHEN pc.peer_id = 'peer_a' THEN pc.peer_price END), 1) AS krg_vs_peer_a_pct,
  ROUND(100.0 *
        (kc.krg_price - MAX(CASE WHEN pc.peer_id = 'peer_b' THEN pc.peer_price END)) /
         MAX(CASE WHEN pc.peer_id = 'peer_b' THEN pc.peer_price END), 1) AS krg_vs_peer_b_pct
FROM top_canonical tc
LEFT JOIN krg_cat kc ON kc.canonical_name = tc.canonical_name
LEFT JOIN peer_cat pc ON pc.canonical_name = tc.canonical_name
GROUP BY tc.canonical_name, kc.krg_price
ORDER BY tc.peer_qty DESC
```

| canonical_name | krg_price | peer_a_price | peer_b_price | krg_vs_peer_a_pct | krg_vs_peer_b_pct |
| --- | --- | --- | --- | --- | --- |
| Trash bags 8 gallon small (60-count) | 7.97 | 8.84 | 7.22 | -9.8 | 10.4 |
| Light bulbs LED daylight (4-count) | 8.89 | 9.77 | 8.16 | -9 | 8.9 |
| Lysol disinfecting wipes (75-count) | 5.94 | 6.63 | 5.4 | -10.4 | 10 |
| Toilet paper (12 mega rolls) | 14.71 | 16.4 | 13.32 | -10.3 | 10.4 |
| Cottonelle toilet paper (12 mega rolls) | 16.83 | 19.04 | 15.13 | -11.6 | 11.2 |
| Charmin toilet paper ultra soft (12 mega rolls) | 17.8 | 19.85 | 16.44 | -10.3 | 8.3 |
| Paper towels select-a-size (6 mega rolls) | 15.03 | 16.36 | 13.58 | -8.1 | 10.7 |
| Bounce dryer sheets (240-count) | 10.13 | 11.17 | 8.86 | -9.3 | 14.3 |
| Downy fabric softener (111 oz) | 12.22 | 13.27 | 10.67 | -7.9 | 14.5 |
| Scrub brushes (2-count) | 3.98 | 4.35 | 3.57 | -8.5 | 11.5 |

Returned 10 rows. Per-peer pct summary — krg_vs_peer_a_pct: range -11.6 to -7.9, mean -9.52; krg_vs_peer_b_pct: range 8.3 to 14.5, mean 11.02.

