-- v2.5 SQLite schema. Only tenant_* tables are physical; the lake is a
-- virtual layer computed at query time from the tenant tables (see
-- src/lake/views.py for the view-builders). Foreign-key enforcement is
-- per-connection — see src/db/seed.py for the PRAGMA.

PRAGMA foreign_keys = ON;

-- ============== Shared dimensions ==============

CREATE TABLE merchants (
    merchant_id   TEXT PRIMARY KEY,             -- 'KRG','ACM','WDX','TBL','TJX'
    name          TEXT NOT NULL,
    segment       TEXT NOT NULL,                -- 'grocery','qsr','off_price_retail'
    mcc           TEXT NOT NULL
);

-- ============== Tenant layer (per-merchant, full granularity) ==============

CREATE TABLE tenant_customers (
    customer_id           TEXT PRIMARY KEY,
    home_zip5             TEXT NOT NULL,
    behavioral_segment    TEXT NOT NULL,        -- 'filler','stocker'
    grocer_affinity_type  TEXT NOT NULL,        -- 'loyalist','splitter','three_chain','lapsed_light'
    primary_grocer        TEXT NOT NULL,        -- 'KRG','ACM','WDX'
    secondary_grocer      TEXT,                 -- nullable; only set for splitters/three_chain
    primary_card_type     TEXT NOT NULL,        -- 'credit','debit','mixed' (no EBT in v2.5)
    has_mobile_wallet     INTEGER NOT NULL,
    signup_date           DATE NOT NULL
);

CREATE TABLE tenant_stores (
    store_id      TEXT PRIMARY KEY,
    merchant_id   TEXT NOT NULL REFERENCES merchants(merchant_id),
    store_zip5    TEXT NOT NULL,                -- full ZIP at this layer
    neighborhood  TEXT NOT NULL,                -- e.g. 'Plaza Midwood', 'University City'
    metro_region  TEXT NOT NULL,                -- 'urban_core','inner_suburbs','outer_suburbs'
    latitude      REAL NOT NULL,
    longitude     REAL NOT NULL,
    open_date     DATE NOT NULL
);

CREATE TABLE tenant_products (
    sku            TEXT PRIMARY KEY,
    merchant_id    TEXT NOT NULL REFERENCES merchants(merchant_id),
    name           TEXT NOT NULL,
    category       TEXT NOT NULL,
    subcategory    TEXT NOT NULL,
    base_price     REAL NOT NULL
);

CREATE TABLE tenant_transactions (
    txn_id            TEXT PRIMARY KEY,
    merchant_id       TEXT NOT NULL REFERENCES merchants(merchant_id),
    customer_id       TEXT NOT NULL REFERENCES tenant_customers(customer_id),
    store_id          TEXT NOT NULL REFERENCES tenant_stores(store_id),
    terminal_id       TEXT NOT NULL,             -- '<store_id>-T<NN>'; no FK
    txn_ts            DATETIME NOT NULL,         -- full timestamp at this layer
    payment_type      TEXT NOT NULL,
    card_network      TEXT,
    entry_mode        TEXT NOT NULL,
    wallet_type       TEXT,
    connectivity_type TEXT NOT NULL,             -- 'wifi'/'cellular_4g'/'cellular_5g'/'ethernet'
    subtotal          REAL NOT NULL,             -- sum of line_total across items
    tax_total         REAL NOT NULL,             -- sum of tax across items
    txn_total         REAL NOT NULL              -- subtotal + tax_total
);

CREATE TABLE tenant_transaction_items (
    txn_id         TEXT NOT NULL REFERENCES tenant_transactions(txn_id),
    line_id        INTEGER NOT NULL,
    sku            TEXT NOT NULL REFERENCES tenant_products(sku),
    qty            INTEGER NOT NULL CHECK (qty > 0),
    unit_price     REAL NOT NULL CHECK (unit_price >= 0),
    discount       REAL NOT NULL DEFAULT 0,
    tax            REAL NOT NULL DEFAULT 0,      -- line_total × tax_rate(category)
    line_total     REAL NOT NULL,                -- (unit_price × qty) - discount
    promo_id       TEXT,                         -- nullable FK to tenant_promotions(promo_id)
    PRIMARY KEY (txn_id, line_id)
);

CREATE TABLE tenant_promotions (
    promo_id       TEXT NOT NULL,                -- '<merchant>-PROMO-<NNNN>' (campaign id; one row per affected SKU)
    merchant_id    TEXT NOT NULL REFERENCES merchants(merchant_id),
    sku            TEXT NOT NULL REFERENCES tenant_products(sku),
    start_date     DATE NOT NULL,
    end_date       DATE NOT NULL,
    discount_pct   REAL NOT NULL,                -- 0.15 == 15% off
    promo_name     TEXT NOT NULL,                -- human-readable, used as campaign grouping key
    promo_type     TEXT NOT NULL,                -- 'weekly_ad'/'holiday'/'lto'/'clearance'
    PRIMARY KEY (promo_id, sku)
);

-- ============== Indexes ==============

CREATE INDEX ix_t_txn_customer  ON tenant_transactions(customer_id);
CREATE INDEX ix_t_txn_merchant  ON tenant_transactions(merchant_id);
CREATE INDEX ix_t_txn_store     ON tenant_transactions(store_id);
CREATE INDEX ix_t_txn_ts        ON tenant_transactions(txn_ts);
CREATE INDEX ix_t_items_sku     ON tenant_transaction_items(sku);
CREATE INDEX ix_t_items_txn     ON tenant_transaction_items(txn_id);
CREATE INDEX ix_t_promo_sku     ON tenant_promotions(merchant_id, sku);
CREATE INDEX ix_t_promo_dates   ON tenant_promotions(start_date, end_date);

-- ============== Per-viewer tenant views (Phase 1.5, Decision §1.5) ==============
--
-- Each merchant gets one view per tenant_* table, scoped to its own
-- merchant_id. The agent's query_tenant tool (src/agents/tools.py)
-- CTE-wraps the agent's SQL so references to `tenant_<table>` resolve
-- to the appropriate `tenant_view_<merchant>_<table>` for the current
-- viewing merchant — making it impossible to satisfy the runner's
-- regex predicate check and still read another merchant's rows.
--
-- tenant_view_<M>_customers shows customers who have transacted
-- with merchant <M>, established by JOIN against tenant_transactions.
-- A customer who shopped at multiple merchants appears in multiple
-- views (correctly — each merchant has independently observed
-- them). Customers in the panel who have never transacted with
-- <M> do not appear in <M>'s view.
--
-- This view is the tenant-isolation boundary for the customers
-- table. Cross-merchant analysis happens in the lake, where
-- customer_id is dropped per the "no consumer linkage" rule.

CREATE VIEW tenant_view_KRG_customers AS
  SELECT DISTINCT c.* FROM tenant_customers c
  JOIN tenant_transactions t ON t.customer_id = c.customer_id
  WHERE t.merchant_id = 'KRG';
CREATE VIEW tenant_view_KRG_stores       AS SELECT * FROM tenant_stores       WHERE merchant_id = 'KRG';
CREATE VIEW tenant_view_KRG_products     AS SELECT * FROM tenant_products     WHERE merchant_id = 'KRG';
CREATE VIEW tenant_view_KRG_transactions AS SELECT * FROM tenant_transactions WHERE merchant_id = 'KRG';
CREATE VIEW tenant_view_KRG_transaction_items AS
  SELECT i.* FROM tenant_transaction_items i
  JOIN tenant_transactions t ON t.txn_id = i.txn_id
  WHERE t.merchant_id = 'KRG';
CREATE VIEW tenant_view_KRG_promotions   AS SELECT * FROM tenant_promotions   WHERE merchant_id = 'KRG';

CREATE VIEW tenant_view_ACM_customers AS
  SELECT DISTINCT c.* FROM tenant_customers c
  JOIN tenant_transactions t ON t.customer_id = c.customer_id
  WHERE t.merchant_id = 'ACM';
CREATE VIEW tenant_view_ACM_stores       AS SELECT * FROM tenant_stores       WHERE merchant_id = 'ACM';
CREATE VIEW tenant_view_ACM_products     AS SELECT * FROM tenant_products     WHERE merchant_id = 'ACM';
CREATE VIEW tenant_view_ACM_transactions AS SELECT * FROM tenant_transactions WHERE merchant_id = 'ACM';
CREATE VIEW tenant_view_ACM_transaction_items AS
  SELECT i.* FROM tenant_transaction_items i
  JOIN tenant_transactions t ON t.txn_id = i.txn_id
  WHERE t.merchant_id = 'ACM';
CREATE VIEW tenant_view_ACM_promotions   AS SELECT * FROM tenant_promotions   WHERE merchant_id = 'ACM';

CREATE VIEW tenant_view_WDX_customers AS
  SELECT DISTINCT c.* FROM tenant_customers c
  JOIN tenant_transactions t ON t.customer_id = c.customer_id
  WHERE t.merchant_id = 'WDX';
CREATE VIEW tenant_view_WDX_stores       AS SELECT * FROM tenant_stores       WHERE merchant_id = 'WDX';
CREATE VIEW tenant_view_WDX_products     AS SELECT * FROM tenant_products     WHERE merchant_id = 'WDX';
CREATE VIEW tenant_view_WDX_transactions AS SELECT * FROM tenant_transactions WHERE merchant_id = 'WDX';
CREATE VIEW tenant_view_WDX_transaction_items AS
  SELECT i.* FROM tenant_transaction_items i
  JOIN tenant_transactions t ON t.txn_id = i.txn_id
  WHERE t.merchant_id = 'WDX';
CREATE VIEW tenant_view_WDX_promotions   AS SELECT * FROM tenant_promotions   WHERE merchant_id = 'WDX';

CREATE VIEW tenant_view_TBL_customers AS
  SELECT DISTINCT c.* FROM tenant_customers c
  JOIN tenant_transactions t ON t.customer_id = c.customer_id
  WHERE t.merchant_id = 'TBL';
CREATE VIEW tenant_view_TBL_stores       AS SELECT * FROM tenant_stores       WHERE merchant_id = 'TBL';
CREATE VIEW tenant_view_TBL_products     AS SELECT * FROM tenant_products     WHERE merchant_id = 'TBL';
CREATE VIEW tenant_view_TBL_transactions AS SELECT * FROM tenant_transactions WHERE merchant_id = 'TBL';
CREATE VIEW tenant_view_TBL_transaction_items AS
  SELECT i.* FROM tenant_transaction_items i
  JOIN tenant_transactions t ON t.txn_id = i.txn_id
  WHERE t.merchant_id = 'TBL';
CREATE VIEW tenant_view_TBL_promotions   AS SELECT * FROM tenant_promotions   WHERE merchant_id = 'TBL';

CREATE VIEW tenant_view_TJX_customers AS
  SELECT DISTINCT c.* FROM tenant_customers c
  JOIN tenant_transactions t ON t.customer_id = c.customer_id
  WHERE t.merchant_id = 'TJX';
CREATE VIEW tenant_view_TJX_stores       AS SELECT * FROM tenant_stores       WHERE merchant_id = 'TJX';
CREATE VIEW tenant_view_TJX_products     AS SELECT * FROM tenant_products     WHERE merchant_id = 'TJX';
CREATE VIEW tenant_view_TJX_transactions AS SELECT * FROM tenant_transactions WHERE merchant_id = 'TJX';
CREATE VIEW tenant_view_TJX_transaction_items AS
  SELECT i.* FROM tenant_transaction_items i
  JOIN tenant_transactions t ON t.txn_id = i.txn_id
  WHERE t.merchant_id = 'TJX';
CREATE VIEW tenant_view_TJX_promotions   AS SELECT * FROM tenant_promotions   WHERE merchant_id = 'TJX';
