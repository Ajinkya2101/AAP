-- HR / organization

CREATE SEQUENCE hr.hr_locations_s START 14000;
CREATE SEQUENCE hr.hr_organization_units_s START 1000;
CREATE SEQUENCE hr.hr_organization_information_s START 30000;
CREATE SEQUENCE hr.per_people_s START 25000;

CREATE TABLE hr.hr_locations_all (
    location_id             bigint       PRIMARY KEY,
    location_code           varchar(60)  NOT NULL,
    description             varchar(240),
    address_line_1          varchar(240),
    address_line_2          varchar(240),
    address_line_3          varchar(240),
    town_or_city            varchar(30),
    region_1                varchar(120),
    region_2                varchar(120),
    postal_code             varchar(30),
    country                 varchar(60),
    telephone_number_1      varchar(60),
    ship_to_site_flag       varchar(30),
    bill_to_site_flag       varchar(30),
    receiving_site_flag     varchar(30),
    office_site_flag        varchar(30),
    inventory_organization_id bigint,
    business_group_id       bigint,
    inactive_date           timestamp(0),
    @WHO@,
    @DFF@
);

CREATE TABLE hr.hr_all_organization_units (
    organization_id         bigint       PRIMARY KEY,
    business_group_id       bigint       NOT NULL,
    name                    varchar(240) NOT NULL,
    type                    varchar(30),
    date_from               timestamp(0) NOT NULL,
    date_to                 timestamp(0),
    location_id             bigint,
    internal_external_flag  varchar(30)  DEFAULT 'INT',
    @WHO@,
    @DFF@
);

CREATE TABLE hr.hr_organization_information (
    org_information_id      bigint       PRIMARY KEY,
    organization_id         bigint       NOT NULL REFERENCES hr.hr_all_organization_units,
    org_information_context varchar(40)  NOT NULL,
    org_information1        varchar(150),
    org_information2        varchar(150),
    org_information3        varchar(150),
    org_information4        varchar(150),
    org_information5        varchar(150),
    org_information6        varchar(150),
    @WHO@
);

CREATE TABLE hr.per_all_people_f (
    person_id               bigint       NOT NULL,
    effective_start_date    timestamp(0) NOT NULL,
    effective_end_date      timestamp(0) NOT NULL,
    business_group_id       bigint       NOT NULL,
    person_type_id          bigint,
    employee_number         varchar(30),
    full_name               varchar(240),
    first_name              varchar(150),
    last_name               varchar(150) NOT NULL,
    email_address           varchar(240),
    @WHO@,
    @DFF@,
    PRIMARY KEY (person_id, effective_start_date, effective_end_date)
);
