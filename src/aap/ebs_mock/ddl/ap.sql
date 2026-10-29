-- AP (Payables)

CREATE SEQUENCE ap.ap_suppliers_s START 30000;
CREATE SEQUENCE ap.ap_supplier_sites_s START 7000;
CREATE SEQUENCE ap.ap_terms_s START 10000;
CREATE SEQUENCE ap.ap_tolerance_templates_s START 1;
CREATE SEQUENCE ap.ap_invoices_s START 880000;
CREATE SEQUENCE ap.ap_invoice_distributions_s START 3200000;
CREATE SEQUENCE ap.ap_holds_s START 90000;
CREATE SEQUENCE ap.ap_invoices_interface_s START 500000;
CREATE SEQUENCE ap.ap_invoice_lines_interface_s START 800000;
CREATE SEQUENCE ap.ap_interface_groups_s START 1000;

CREATE TABLE ap.ap_terms_tl (
    term_id                 bigint       NOT NULL,
    language                varchar(4)   NOT NULL DEFAULT 'US',
    source_lang             varchar(4)   NOT NULL DEFAULT 'US',
    name                    varchar(50)  NOT NULL,
    description             varchar(240),
    enabled_flag            varchar(1)   NOT NULL DEFAULT 'Y',
    type                    varchar(15)  NOT NULL DEFAULT 'STANDARD',
    rank                    integer,
    due_cutoff_day          integer,
    start_date_active       timestamp(0),
    end_date_active         timestamp(0),
    @WHO@,
    @DFF@,
    PRIMARY KEY (term_id, language)
);

CREATE TABLE ap.ap_terms_lines (
    term_id                 bigint       NOT NULL,
    sequence_num            integer      NOT NULL,
    due_percent             numeric,
    due_amount              numeric,
    due_days                integer,
    due_day_of_month        integer,
    due_months_forward      integer,
    discount_percent        numeric,
    discount_days           integer,
    discount_percent_2      numeric,
    discount_days_2         integer,
    @WHO@,
    PRIMARY KEY (term_id, sequence_num)
);

CREATE TABLE ap.ap_tolerance_templates (
    tolerance_id            bigint       PRIMARY KEY,
    tolerance_name          varchar(255) NOT NULL UNIQUE,
    tolerance_type          varchar(30)  NOT NULL DEFAULT 'GOODS',
    description             varchar(240),
    price_tolerance         numeric,     -- percent
    quantity_tolerance      numeric,     -- percent
    qty_received_tolerance  numeric,     -- percent
    max_qty_ord_tolerance   numeric,     -- absolute
    max_qty_rec_tolerance   numeric,     -- absolute
    ship_amt_tolerance      numeric,
    total_amt_tolerance     numeric,
    @WHO@
);

CREATE TABLE ap.ap_suppliers (
    vendor_id               bigint       PRIMARY KEY,
    vendor_name             varchar(240) NOT NULL,
    vendor_name_alt         varchar(320),
    segment1                varchar(30)  NOT NULL UNIQUE,
    party_id                bigint       NOT NULL,
    vendor_type_lookup_code varchar(30),
    enabled_flag            varchar(1)   NOT NULL DEFAULT 'Y',
    summary_flag            varchar(1)   NOT NULL DEFAULT 'N',
    one_time_flag           varchar(1)   DEFAULT 'N',
    employee_id             bigint,
    num_1099                varchar(30),
    vat_registration_num    varchar(20),
    terms_id                bigint,
    terms_date_basis        varchar(25),
    pay_group_lookup_code   varchar(25),
    invoice_currency_code   varchar(15),
    payment_currency_code   varchar(15),
    payment_method_lookup_code varchar(25),
    match_option            varchar(25),
    receipt_required_flag   varchar(1),
    inspection_required_flag varchar(1),
    hold_all_payments_flag  varchar(1)   DEFAULT 'N',
    hold_future_payments_flag varchar(1) DEFAULT 'N',
    start_date_active       timestamp(0),
    end_date_active         timestamp(0),
    @WHO@,
    @DFF@
);
CREATE UNIQUE INDEX ap_suppliers_u2 ON ap.ap_suppliers (vendor_name);

CREATE TABLE ap.ap_supplier_sites_all (
    vendor_site_id          bigint       PRIMARY KEY,
    vendor_id               bigint       NOT NULL REFERENCES ap.ap_suppliers,
    vendor_site_code        varchar(15)  NOT NULL,
    vendor_site_code_alt    varchar(320),
    org_id                  bigint       NOT NULL,
    purchasing_site_flag    varchar(1),
    pay_site_flag           varchar(1),
    primary_pay_site_flag   varchar(1),
    rfq_only_site_flag      varchar(1),
    address_line1           varchar(240),
    address_line2           varchar(240),
    address_line3           varchar(240),
    address_line4           varchar(240),
    city                    varchar(60),
    state                   varchar(150),
    zip                     varchar(20),
    province                varchar(150),
    country                 varchar(25),
    area_code               varchar(10),
    phone                   varchar(15),
    email_address           varchar(2000),
    remittance_email        varchar(2000),
    terms_id                bigint,
    invoice_currency_code   varchar(15),
    payment_currency_code   varchar(15),
    pay_group_lookup_code   varchar(25),
    payment_method_lookup_code varchar(25),
    vat_registration_num    varchar(20),
    match_option            varchar(25),
    tolerance_id            bigint,
    accts_pay_code_combination_id bigint,
    prepay_code_combination_id    bigint,
    location_id             bigint,
    party_site_id           bigint,
    hold_all_payments_flag  varchar(1)   DEFAULT 'N',
    inactive_date           timestamp(0),
    @WHO@,
    @DFF@,
    UNIQUE (vendor_id, org_id, vendor_site_code)
);

CREATE TABLE ap.ap_invoices_all (
    invoice_id              bigint       PRIMARY KEY,
    invoice_num             varchar(50)  NOT NULL,
    vendor_id               bigint       NOT NULL,
    vendor_site_id          bigint       NOT NULL,
    org_id                  bigint       NOT NULL,
    set_of_books_id         bigint       NOT NULL,
    invoice_type_lookup_code varchar(25) NOT NULL,
    invoice_date            timestamp(0) NOT NULL,
    invoice_received_date   timestamp(0),
    gl_date                 timestamp(0) NOT NULL,
    invoice_amount          numeric      NOT NULL,
    base_amount             numeric,
    amount_paid             numeric      DEFAULT 0,
    total_tax_amount        numeric,
    invoice_currency_code   varchar(15)  NOT NULL,
    payment_currency_code   varchar(15)  NOT NULL,
    exchange_rate           numeric,
    exchange_rate_type      varchar(30),
    exchange_date           timestamp(0),
    terms_id                bigint,
    terms_date              timestamp(0),
    description             varchar(240),
    source                  varchar(25),
    po_header_id            bigint,
    payment_status_flag     varchar(1)   DEFAULT 'N',
    wfapproval_status       varchar(50),
    approval_status         varchar(25),
    pay_group_lookup_code   varchar(25),
    payment_method_code     varchar(30),
    accts_pay_code_combination_id bigint,
    party_id                bigint,
    party_site_id           bigint,
    requester_id            bigint,
    cancelled_date          timestamp(0),
    cancelled_amount        numeric,
    doc_sequence_value      bigint,
    request_id              bigint,
    @WHO@,
    @DFF@
);
CREATE UNIQUE INDEX ap_invoices_u2 ON ap.ap_invoices_all (vendor_id, invoice_num, org_id);
CREATE INDEX ap_invoices_n1 ON ap.ap_invoices_all (vendor_id, invoice_date);
CREATE INDEX ap_invoices_n2 ON ap.ap_invoices_all (po_header_id);

CREATE TABLE ap.ap_invoice_lines_all (
    invoice_id              bigint       NOT NULL REFERENCES ap.ap_invoices_all,
    line_number             integer      NOT NULL,
    line_type_lookup_code   varchar(25)  NOT NULL,   -- ITEM TAX FREIGHT MISCELLANEOUS
    line_source             varchar(30),
    org_id                  bigint       NOT NULL,
    description             varchar(240),
    amount                  numeric      NOT NULL,
    base_amount             numeric,
    quantity_invoiced       numeric,
    unit_price              numeric,
    unit_meas_lookup_code   varchar(25),
    inventory_item_id       bigint,
    item_description        varchar(240),
    po_header_id            bigint,
    po_line_id              bigint,
    po_release_id           bigint,
    po_line_location_id     bigint,
    po_distribution_id      bigint,
    rcv_transaction_id      bigint,
    match_type              varchar(25),
    accounting_date         timestamp(0) NOT NULL,
    period_name             varchar(15),
    default_dist_ccid       bigint,
    tax_classification_code varchar(30),
    tax_rate_code           varchar(150),
    tax_rate                numeric,
    generate_dists          varchar(1)   DEFAULT 'D',
    discarded_flag          varchar(1)   DEFAULT 'N',
    cancelled_flag          varchar(1)   DEFAULT 'N',
    set_of_books_id         bigint,
    @WHO@,
    @DFF@,
    PRIMARY KEY (invoice_id, line_number)
);
CREATE INDEX ap_invoice_lines_n1 ON ap.ap_invoice_lines_all (po_line_location_id);

CREATE TABLE ap.ap_invoice_distributions_all (
    invoice_distribution_id bigint       PRIMARY KEY,
    invoice_id              bigint       NOT NULL,
    invoice_line_number     integer      NOT NULL,
    distribution_line_number integer     NOT NULL,
    line_type_lookup_code   varchar(25)  NOT NULL,
    amount                  numeric      NOT NULL,
    base_amount             numeric,
    quantity_invoiced       numeric,
    unit_price              numeric,
    description             varchar(240),
    accounting_date         timestamp(0) NOT NULL,
    period_name             varchar(15),
    dist_code_combination_id bigint      NOT NULL,
    po_distribution_id      bigint,
    rcv_transaction_id      bigint,
    match_status_flag       varchar(1),
    posted_flag             varchar(1)   DEFAULT 'N',
    accrual_posted_flag     varchar(1)   DEFAULT 'N',
    org_id                  bigint       NOT NULL,
    set_of_books_id         bigint       NOT NULL,
    @WHO@,
    @DFF@,
    FOREIGN KEY (invoice_id, invoice_line_number) REFERENCES ap.ap_invoice_lines_all (invoice_id, line_number)
);
CREATE INDEX ap_invoice_distributions_n1 ON ap.ap_invoice_distributions_all (invoice_id);

CREATE TABLE ap.ap_holds_all (
    hold_id                 bigint       PRIMARY KEY,
    invoice_id              bigint       NOT NULL REFERENCES ap.ap_invoices_all,
    line_location_id        bigint,
    hold_lookup_code        varchar(25)  NOT NULL,
    hold_reason             varchar(240),
    hold_date               timestamp(0) NOT NULL,
    held_by                 bigint       NOT NULL,
    release_lookup_code     varchar(25),
    release_reason          varchar(240),
    status_flag             varchar(1),              -- S still held, R released
    org_id                  bigint       NOT NULL,
    @WHO@,
    @DFF@
);
CREATE INDEX ap_holds_n1 ON ap.ap_holds_all (invoice_id);

-- ---------------------------------------------------- Payables Open Interface
CREATE TABLE ap.ap_invoices_interface (
    invoice_id              bigint       PRIMARY KEY,
    invoice_num             varchar(50),
    invoice_type_lookup_code varchar(25),
    invoice_date            timestamp(0),
    invoice_received_date   timestamp(0),
    gl_date                 timestamp(0),
    po_number               varchar(20),
    vendor_id               bigint,
    vendor_num              varchar(30),
    vendor_name             varchar(240),
    vendor_site_id          bigint,
    vendor_site_code        varchar(15),
    invoice_amount          numeric,
    invoice_currency_code   varchar(15),
    exchange_rate           numeric,
    exchange_rate_type      varchar(30),
    exchange_date           timestamp(0),
    terms_id                bigint,
    terms_name              varchar(50),
    terms_date              timestamp(0),
    description             varchar(240),
    source                  varchar(80),
    group_id                varchar(80),
    request_id              bigint,
    status                  varchar(25),
    org_id                  bigint,
    operating_unit          varchar(240),
    payment_method_code     varchar(30),
    pay_group_lookup_code   varchar(25),
    calc_tax_during_import_flag varchar(1),
    add_tax_to_inv_amt_flag varchar(1),
    @WHO@,
    @DFF@
);
CREATE INDEX ap_invoices_interface_n1 ON ap.ap_invoices_interface (group_id, status);
CREATE INDEX ap_invoices_interface_n2 ON ap.ap_invoices_interface (attribute15);

CREATE TABLE ap.ap_invoice_lines_interface (
    invoice_id              bigint       NOT NULL,
    invoice_line_id         bigint       PRIMARY KEY,
    line_number             integer,
    line_type_lookup_code   varchar(25),
    amount                  numeric,
    description             varchar(240),
    quantity_invoiced       numeric,
    unit_price              numeric,
    unit_of_meas_lookup_code varchar(25),
    inventory_item_id       bigint,
    item_description        varchar(240),
    po_header_id            bigint,
    po_number               varchar(20),
    po_line_id              bigint,
    po_line_number          integer,
    po_line_location_id     bigint,
    po_shipment_num         integer,
    po_release_id           bigint,
    release_num             integer,
    po_distribution_id      bigint,
    po_distribution_num     integer,
    receipt_number          varchar(30),
    rcv_transaction_id      bigint,
    dist_code_combination_id bigint,
    dist_code_concatenated  varchar(250),
    accounting_date         timestamp(0),
    tax_classification_code varchar(30),
    tax_rate_code           varchar(150),
    org_id                  bigint,
    @WHO@,
    @DFF@
);
CREATE INDEX ap_invoice_lines_interface_n1 ON ap.ap_invoice_lines_interface (invoice_id);

CREATE TABLE ap.ap_interface_rejections (
    parent_table            varchar(30)  NOT NULL,   -- AP_INVOICES_INTERFACE / AP_INVOICE_LINES_INTERFACE
    parent_id               bigint       NOT NULL,
    reject_lookup_code      varchar(30)  NOT NULL,
    request_id              bigint,
    @WHO@
);
CREATE INDEX ap_interface_rejections_n1 ON ap.ap_interface_rejections (parent_table, parent_id);
