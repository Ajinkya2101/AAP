-- GL

CREATE SEQUENCE gl.gl_ledgers_s START 2021;
CREATE SEQUENCE gl.gl_code_combinations_s START 12000;

CREATE TABLE gl.gl_ledgers (
    ledger_id               bigint       PRIMARY KEY,
    name                    varchar(30)  NOT NULL UNIQUE,
    short_name              varchar(20)  NOT NULL,
    description             varchar(240),
    ledger_category_code    varchar(30)  NOT NULL DEFAULT 'PRIMARY',
    object_type_code        varchar(1)   NOT NULL DEFAULT 'L',
    chart_of_accounts_id    bigint       NOT NULL,
    currency_code           varchar(15)  NOT NULL,
    period_set_name         varchar(15)  NOT NULL,
    accounted_period_type   varchar(15)  NOT NULL,
    @WHO@
);

CREATE TABLE gl.gl_code_combinations (
    code_combination_id     bigint       PRIMARY KEY,
    chart_of_accounts_id    bigint       NOT NULL,
    account_type            varchar(1)   NOT NULL,   -- A L O R E
    enabled_flag            varchar(1)   NOT NULL,
    summary_flag            varchar(1)   NOT NULL DEFAULT 'N',
    detail_posting_allowed_flag   varchar(1) NOT NULL DEFAULT 'Y',
    detail_budgeting_allowed_flag varchar(1) NOT NULL DEFAULT 'Y',
    start_date_active       timestamp(0),
    end_date_active         timestamp(0),
    template_id             bigint,
    segment1  varchar(25), segment2  varchar(25), segment3  varchar(25), segment4  varchar(25),
    segment5  varchar(25), segment6  varchar(25), segment7  varchar(25), segment8  varchar(25),
    segment9  varchar(25), segment10 varchar(25), segment11 varchar(25), segment12 varchar(25),
    segment13 varchar(25), segment14 varchar(25), segment15 varchar(25), segment16 varchar(25),
    segment17 varchar(25), segment18 varchar(25), segment19 varchar(25), segment20 varchar(25),
    segment21 varchar(25), segment22 varchar(25), segment23 varchar(25), segment24 varchar(25),
    segment25 varchar(25), segment26 varchar(25), segment27 varchar(25), segment28 varchar(25),
    segment29 varchar(25), segment30 varchar(25),
    @WHO@
);
CREATE UNIQUE INDEX gl_code_combinations_u2
    ON gl.gl_code_combinations (chart_of_accounts_id, segment1, segment2, segment3, segment4);

CREATE TABLE gl.gl_period_statuses (
    application_id          bigint       NOT NULL,
    ledger_id               bigint       NOT NULL,
    set_of_books_id         bigint       NOT NULL,
    period_name             varchar(15)  NOT NULL,
    closing_status          varchar(1)   NOT NULL,   -- O open, C closed, F future-entry, N never opened
    start_date              timestamp(0) NOT NULL,
    end_date                timestamp(0) NOT NULL,
    period_type             varchar(15)  NOT NULL DEFAULT 'Month',
    period_year             integer      NOT NULL,
    period_num              integer      NOT NULL,
    quarter_num             integer      NOT NULL,
    adjustment_period_flag  varchar(1)   NOT NULL DEFAULT 'N',
    effective_period_num    bigint       NOT NULL,
    @WHO@,
    PRIMARY KEY (application_id, ledger_id, period_name)
);

CREATE TABLE gl.gl_daily_rates (
    from_currency           varchar(15)  NOT NULL,
    to_currency             varchar(15)  NOT NULL,
    conversion_date         timestamp(0) NOT NULL,
    conversion_type         varchar(30)  NOT NULL,
    conversion_rate         numeric      NOT NULL,
    status_code             varchar(1)   NOT NULL DEFAULT 'C',
    @WHO@,
    PRIMARY KEY (from_currency, to_currency, conversion_date, conversion_type)
);
