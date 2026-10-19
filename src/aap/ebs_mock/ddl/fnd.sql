-- FND / foundation (owner APPLSYS -> schema fnd)

CREATE SEQUENCE fnd.fnd_user_s START 1100;
CREATE SEQUENCE fnd.fnd_concurrent_requests_s START 7340000;
CREATE SEQUENCE fnd.fnd_flex_value_sets_s START 1002000;
CREATE SEQUENCE fnd.fnd_flex_values_s START 110000;

CREATE TABLE fnd.fnd_currencies (
    currency_code           varchar(15)  PRIMARY KEY,
    enabled_flag            varchar(1)   NOT NULL,
    currency_flag           varchar(1)   NOT NULL DEFAULT 'Y',
    issuing_territory_code  varchar(2),
    precision               integer,
    extended_precision      integer,
    minimum_accountable_unit numeric,
    symbol                  varchar(12),
    start_date_active       timestamp(0),
    end_date_active         timestamp(0),
    @WHO@,
    @DFF@
);

CREATE TABLE fnd.fnd_lookup_values (
    lookup_type             varchar(30)  NOT NULL,
    language                varchar(30)  NOT NULL DEFAULT 'US',
    lookup_code             varchar(30)  NOT NULL,
    meaning                 varchar(80)  NOT NULL,
    description             varchar(240),
    enabled_flag            varchar(1)   NOT NULL DEFAULT 'Y',
    start_date_active       timestamp(0),
    end_date_active         timestamp(0),
    source_lang             varchar(4)   NOT NULL DEFAULT 'US',
    security_group_id       bigint       NOT NULL DEFAULT 0,
    view_application_id     bigint       NOT NULL DEFAULT 200,
    territory_code          varchar(2),
    tag                     varchar(150),
    @WHO@,
    @DFF@,
    PRIMARY KEY (lookup_type, language, lookup_code, security_group_id, view_application_id)
);

CREATE TABLE fnd.fnd_user (
    user_id                 bigint       PRIMARY KEY,
    user_name               varchar(100) NOT NULL UNIQUE,
    description             varchar(240),
    employee_id             bigint,
    email_address           varchar(240),
    start_date              timestamp(0) NOT NULL,
    end_date                timestamp(0),
    @WHO@
);

CREATE TABLE fnd.fnd_concurrent_programs (
    application_id          bigint       NOT NULL,
    concurrent_program_id   bigint       NOT NULL,
    concurrent_program_name varchar(30)  NOT NULL,
    user_concurrent_program_name varchar(240),
    executable_id           bigint,
    enabled_flag            varchar(1)   NOT NULL DEFAULT 'Y',
    @WHO@,
    PRIMARY KEY (application_id, concurrent_program_id),
    UNIQUE (application_id, concurrent_program_name)
);

CREATE TABLE fnd.fnd_concurrent_requests (
    request_id              bigint       PRIMARY KEY,
    program_application_id  bigint       NOT NULL,
    concurrent_program_id   bigint       NOT NULL,
    phase_code              varchar(1)   NOT NULL,   -- P pending, R running, C completed
    status_code             varchar(1)   NOT NULL,   -- I normal(pending), R running, C normal, E error, G warning
    description             varchar(240),
    argument_text           varchar(240),
    argument1               varchar(240),
    argument2               varchar(240),
    argument3               varchar(240),
    argument4               varchar(240),
    argument5               varchar(240),
    argument6               varchar(240),
    argument7               varchar(240),
    argument8               varchar(240),
    argument9               varchar(240),
    argument10              varchar(240),
    requested_by            bigint       NOT NULL,
    request_date            timestamp(0) NOT NULL,
    requested_start_date    timestamp(0) NOT NULL,
    actual_start_date       timestamp(0),
    actual_completion_date  timestamp(0),
    completion_text         varchar(240),
    org_id                  bigint,
    @WHO@
);
CREATE INDEX fnd_concurrent_requests_n1 ON fnd.fnd_concurrent_requests (phase_code, requested_start_date);

CREATE TABLE fnd.fnd_flex_value_sets (
    flex_value_set_id       bigint       PRIMARY KEY,
    flex_value_set_name     varchar(60)  NOT NULL UNIQUE,
    validation_type         varchar(1)   NOT NULL DEFAULT 'I',
    format_type             varchar(1)   NOT NULL DEFAULT 'C',
    maximum_size            integer      NOT NULL,
    description             varchar(240),
    @WHO@
);

CREATE TABLE fnd.fnd_flex_values (
    flex_value_id           bigint       PRIMARY KEY,
    flex_value_set_id       bigint       NOT NULL REFERENCES fnd.fnd_flex_value_sets,
    flex_value              varchar(150) NOT NULL,
    enabled_flag            varchar(1)   NOT NULL DEFAULT 'Y',
    summary_flag            varchar(1)   NOT NULL DEFAULT 'N',
    start_date_active       timestamp(0),
    end_date_active         timestamp(0),
    parent_flex_value_low   varchar(60),
    compiled_value_attributes varchar(2000),
    @WHO@,
    @DFF@,
    UNIQUE (flex_value_set_id, flex_value)
);

CREATE TABLE fnd.fnd_flex_values_tl (
    flex_value_id           bigint       NOT NULL REFERENCES fnd.fnd_flex_values,
    language                varchar(4)   NOT NULL DEFAULT 'US',
    source_lang             varchar(4)   NOT NULL DEFAULT 'US',
    description             varchar(240),
    flex_value_meaning      varchar(150),
    @WHO@,
    PRIMARY KEY (flex_value_id, language)
);
