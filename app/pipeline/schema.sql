-- Table definitions for build/cloudnova.duckdb.
-- Source of truth: specs/01_data_contract.md, section 3.

CREATE TABLE invoices (
    invoice_id                VARCHAR PRIMARY KEY,
    account_id                VARCHAR NOT NULL,
    account_name              VARCHAR NOT NULL,
    region                    VARCHAR NOT NULL CHECK (region IN ('NA', 'EMEA', 'APAC', 'LATAM')),
    industry                  VARCHAR,
    plan                      VARCHAR NOT NULL CHECK (plan IN ('Starter', 'Pro', 'Enterprise')),
    billing_cycle             VARCHAR NOT NULL CHECK (billing_cycle IN ('monthly', 'annual')),
    seats                     INTEGER NOT NULL CHECK (seats > 0),
    currency                  VARCHAR NOT NULL CHECK (currency IN ('USD', 'EUR', 'GBP')),
    amount_local              DECIMAL(14,2) NOT NULL,
    discount_pct              DECIMAL(5,2) NOT NULL CHECK (discount_pct BETWEEN 0 AND 100),
    status                    VARCHAR NOT NULL CHECK (status IN ('paid', 'pending', 'failed', 'refunded', 'void')),
    payment_method            VARCHAR CHECK (payment_method IN ('credit_card', 'ach', 'wire', 'invoice')),
    signup_date               DATE NOT NULL,
    invoice_date              DATE NOT NULL,
    invoice_year              INTEGER NOT NULL,
    invoice_month             VARCHAR NOT NULL,
    churned                   BOOLEAN NOT NULL,
    csat_score                INTEGER CHECK (csat_score BETWEEN 1 AND 5),
    support_tickets           INTEGER NOT NULL CHECK (support_tickets >= 0),
    fx_rate_to_usd            DECIMAL(8,4) NOT NULL,
    amount_usd                DECIMAL(14,2) NOT NULL,
    recognized_revenue_usd    DECIMAL(14,2) NOT NULL,
    list_price_usd            INTEGER NOT NULL,
    billed_months             INTEGER NOT NULL,
    mrr_usd                   DECIMAL(14,2) NOT NULL,
    dq_duplicate_rows         INTEGER NOT NULL,
    dq_status_conflict        BOOLEAN NOT NULL,
    dq_missing_cents          BOOLEAN NOT NULL,
    dq_amount_off_formula     BOOLEAN NOT NULL,
    dq_invoice_date_ambiguous BOOLEAN NOT NULL,
    dq_signup_date_ambiguous  BOOLEAN NOT NULL,
    dq_future_dated           BOOLEAN NOT NULL,
    source_rows               VARCHAR NOT NULL
);

-- Email addresses live apart from invoices so the question tool never reads them (G3).
CREATE TABLE invoice_contacts (
    invoice_id        VARCHAR PRIMARY KEY,
    contact_email     VARCHAR,
    dq_email_repaired BOOLEAN NOT NULL
);

CREATE TABLE accounts (
    account_id             VARCHAR PRIMARY KEY,
    account_name           VARCHAR NOT NULL,
    region                 VARCHAR NOT NULL,
    industry               VARCHAR,
    plan                   VARCHAR NOT NULL,
    billing_cycle          VARCHAR NOT NULL,
    seats                  INTEGER NOT NULL,
    discount_pct           DECIMAL(5,2) NOT NULL,
    currency               VARCHAR NOT NULL,
    signup_date            DATE NOT NULL,
    churned                BOOLEAN NOT NULL,
    support_tickets        INTEGER NOT NULL,
    is_active              BOOLEAN NOT NULL,
    mrr_usd                DECIMAL(14,2) NOT NULL,
    active_mrr_usd         DECIMAL(14,2) NOT NULL,
    latest_csat            INTEGER,
    invoice_count          INTEGER NOT NULL,
    first_invoice_date     DATE NOT NULL,
    last_invoice_date      DATE NOT NULL,
    paid_revenue_usd       DECIMAL(14,2) NOT NULL,
    refunded_usd           DECIMAL(14,2) NOT NULL,
    net_revenue_usd        DECIMAL(14,2) NOT NULL,
    distinct_account_names INTEGER NOT NULL,
    dq_identity_conflict   BOOLEAN NOT NULL
);

CREATE TABLE dq_duplicates (
    invoice_id    VARCHAR PRIMARY KEY,
    raw_row_count INTEGER NOT NULL,
    kind          VARCHAR NOT NULL CHECK (kind IN ('exact_copy', 'same_meaning', 'status_conflict')),
    raw_statuses  VARCHAR NOT NULL,
    chosen_status VARCHAR NOT NULL,
    source_rows   VARCHAR NOT NULL
);

CREATE TABLE rejects (
    source_row INTEGER NOT NULL,
    invoice_id VARCHAR,
    reason     VARCHAR NOT NULL,
    detail     VARCHAR,
    raw_record VARCHAR NOT NULL
);
