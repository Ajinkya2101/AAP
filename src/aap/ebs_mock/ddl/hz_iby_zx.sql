-- Parties (TCA), external bank accounts, tax rates

CREATE SEQUENCE hz.hz_parties_s START 1020000;
CREATE SEQUENCE iby.iby_ext_bank_accounts_s START 50000;
CREATE SEQUENCE iby.iby_account_owners_s START 60000;
CREATE SEQUENCE zx.zx_rates_b_s START 10000;

CREATE TABLE hz.hz_parties (
    party_id                bigint       PRIMARY KEY,
    party_number            varchar(30)  NOT NULL UNIQUE,
    party_name              varchar(360) NOT NULL,
    party_type              varchar(30)  NOT NULL,   -- ORGANIZATION, PERSON
    jgzz_fiscal_code        varchar(20),
    status                  varchar(1)   NOT NULL DEFAULT 'A',
    address1                varchar(240),
    city                    varchar(60),
    postal_code             varchar(60),
    state                   varchar(60),
    country                 varchar(60),
    email_address           varchar(2000),
    category_code           varchar(30),             -- SUPPLIER, BANK, BANK_BRANCH
    @WHO@,
    @DFF@
);

CREATE TABLE iby.iby_ext_bank_accounts (
    ext_bank_account_id     bigint       PRIMARY KEY,
    bank_id                 bigint,
    branch_id               bigint,
    bank_account_num        varchar(100) NOT NULL,
    bank_account_name       varchar(80),
    bank_account_type       varchar(25),
    iban                    varchar(50),
    check_digits            varchar(30),
    currency_code           varchar(15),
    country_code            varchar(2)   NOT NULL,
    start_date              timestamp(0),
    end_date                timestamp(0),
    @WHO@,
    @DFF@
);

CREATE TABLE iby.iby_account_owners (
    account_owner_id        bigint       PRIMARY KEY,
    ext_bank_account_id     bigint       NOT NULL REFERENCES iby.iby_ext_bank_accounts,
    account_owner_party_id  bigint       NOT NULL,
    primary_flag            varchar(1)   NOT NULL DEFAULT 'Y',
    end_date                timestamp(0),
    @WHO@
);

CREATE TABLE zx.zx_rates_b (
    tax_rate_id             bigint       PRIMARY KEY,
    tax_rate_code           varchar(150) NOT NULL,
    tax_regime_code         varchar(30)  NOT NULL,
    tax                     varchar(30)  NOT NULL,
    tax_status_code         varchar(30)  NOT NULL,
    content_owner_id        bigint       NOT NULL DEFAULT -99,
    rate_type_code          varchar(30)  NOT NULL DEFAULT 'PERCENTAGE',
    percentage_rate         numeric,
    active_flag             varchar(1)   NOT NULL DEFAULT 'Y',
    default_rate_flag       varchar(1)   NOT NULL DEFAULT 'N',
    effective_from          timestamp(0) NOT NULL,
    effective_to            timestamp(0),
    @WHO@,
    @DFF@
);
