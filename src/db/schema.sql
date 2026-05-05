-- Dual-path SQLite schema. Tenant tables hold per-merchant full-granularity
-- data; lake tables hold the cross-merchant anonymized aggregate. Foreign-key
-- enforcement is per-connection — see src/db/seed.py for the PRAGMA.

PRAGMA foreign_keys = ON;

-- ============== Shared dimensions ==============

CREATE TABLE merchants (
    merchant_id   TEXT PRIMARY KEY,             -- 'KRG','TBL','TJX'
    name          TEXT NOT NULL,
    segment       TEXT NOT NULL,                -- 'grocery','qsr','retail_offprice'
    mcc           TEXT NOT NULL
);

-- ============== Tenant layer (per-merchant, full granularity) ==============

CREATE TABLE tenant_customers (
    customer_id        TEXT PRIMARY KEY,
    age_band           TEXT NOT NULL,
    income_band        TEXT NOT NULL,
    home_zip5          TEXT NOT NULL,           -- full ZIP at this layer
    signup_date        DATE NOT NULL,
    primary_card_type  TEXT NOT NULL,
    has_mobile_wallet  INTEGER NOT NULL
);

CREATE TABLE tenant_stores (
    store_id      TEXT PRIMARY KEY,
    merchant_id   TEXT NOT NULL REFERENCES merchants(merchant_id),
    store_zip5    TEXT NOT NULL,                -- full ZIP at this layer
    region        TEXT NOT NULL,
    open_date     DATE NOT NULL
);

CREATE TABLE tenant_products (
    sku            TEXT PRIMARY KEY,
    merchant_id    TEXT NOT NULL REFERENCES merchants(merchant_id),
    name           TEXT NOT NULL,
    category       TEXT NOT NULL,
    subcategory    TEXT NOT NULL,
    is_organic     INTEGER NOT NULL,
    base_price     REAL NOT NULL
);

CREATE TABLE tenant_transactions (
    txn_id          TEXT PRIMARY KEY,
    merchant_id     TEXT NOT NULL REFERENCES merchants(merchant_id),
    customer_id     TEXT NOT NULL REFERENCES tenant_customers(customer_id),
    store_id        TEXT NOT NULL REFERENCES tenant_stores(store_id),
    txn_ts          DATETIME NOT NULL,          -- full timestamp at this layer
    payment_type    TEXT NOT NULL,
    card_network    TEXT,
    entry_mode      TEXT NOT NULL,
    wallet_type     TEXT,
    txn_total       REAL NOT NULL
);

CREATE TABLE tenant_transaction_items (
    txn_id         TEXT NOT NULL REFERENCES tenant_transactions(txn_id),
    line_id        INTEGER NOT NULL,
    sku            TEXT NOT NULL REFERENCES tenant_products(sku),
    qty            INTEGER NOT NULL CHECK (qty > 0),
    unit_price     REAL NOT NULL CHECK (unit_price >= 0),
    discount       REAL NOT NULL DEFAULT 0,
    line_total     REAL NOT NULL,
    PRIMARY KEY (txn_id, line_id)
);

-- ============== Lake layer (cross-merchant, additionally anonymized) ==============

CREATE TABLE lake_customers (
    customer_id        TEXT PRIMARY KEY,
    age_band           TEXT NOT NULL,
    income_band        TEXT NOT NULL,
    home_zip3          TEXT,                    -- ZIP3 only; NULL when k-anonymity suppresses
    signup_date        DATE NOT NULL,
    primary_card_type  TEXT NOT NULL,
    has_mobile_wallet  INTEGER NOT NULL
);

CREATE TABLE lake_transactions (
    txn_id          TEXT PRIMARY KEY,
    merchant_id     TEXT NOT NULL REFERENCES merchants(merchant_id),
    customer_id     TEXT NOT NULL REFERENCES lake_customers(customer_id),
    store_zip3      TEXT NOT NULL,              -- denormalized; no separate lake_stores table
    region          TEXT NOT NULL,
    txn_ts          DATETIME NOT NULL,
    txn_hour_bucket DATETIME NOT NULL,
    payment_type    TEXT NOT NULL,
    card_network    TEXT,
    entry_mode      TEXT NOT NULL,
    wallet_type     TEXT,
    txn_total       REAL NOT NULL
);

CREATE TABLE lake_transaction_items (
    txn_id         TEXT NOT NULL REFERENCES lake_transactions(txn_id),
    line_id        INTEGER NOT NULL,
    sku_category   TEXT NOT NULL,               -- subcategory; SKU-level not retained in lake
    qty            INTEGER NOT NULL,
    unit_price     REAL NOT NULL,
    line_total     REAL NOT NULL,
    PRIMARY KEY (txn_id, line_id)
);

-- ============== Indexes ==============

CREATE INDEX ix_t_txn_customer  ON tenant_transactions(customer_id);
CREATE INDEX ix_t_txn_merchant  ON tenant_transactions(merchant_id);
CREATE INDEX ix_t_txn_store     ON tenant_transactions(store_id);
CREATE INDEX ix_t_txn_ts        ON tenant_transactions(txn_ts);
CREATE INDEX ix_t_items_sku     ON tenant_transaction_items(sku);
CREATE INDEX ix_t_items_txn     ON tenant_transaction_items(txn_id);

CREATE INDEX ix_l_txn_customer  ON lake_transactions(customer_id);
CREATE INDEX ix_l_txn_merchant  ON lake_transactions(merchant_id);
CREATE INDEX ix_l_txn_ts        ON lake_transactions(txn_ts);
CREATE INDEX ix_l_items_txn     ON lake_transaction_items(txn_id);
