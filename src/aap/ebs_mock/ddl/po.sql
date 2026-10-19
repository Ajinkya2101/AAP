-- PO (Purchasing), including buyers

CREATE SEQUENCE po.po_headers_s START 110000;
CREATE SEQUENCE po.po_lines_s START 220000;
CREATE SEQUENCE po.po_line_locations_s START 330000;
CREATE SEQUENCE po.po_distributions_s START 440000;
CREATE SEQUENCE po.po_segment1_s START 4500200;   -- PO numbers (PO_UNIQUE_IDENTIFIER_CONTROL in real EBS)
CREATE SEQUENCE po.po_line_types_s START 1000;

CREATE TABLE po.po_agents (
    agent_id                bigint       PRIMARY KEY,   -- = PER_ALL_PEOPLE_F.PERSON_ID
    location_id             bigint,
    category_id             bigint,
    authorization_limit     numeric,
    start_date_active       timestamp(0),
    end_date_active         timestamp(0),
    @WHO@,
    @DFF@
);

CREATE TABLE po.po_line_types_b (
    line_type_id            bigint       PRIMARY KEY,
    line_type               varchar(25)  NOT NULL,
    order_type_lookup_code  varchar(25)  NOT NULL,   -- QUANTITY / AMOUNT
    purchase_basis          varchar(30),              -- GOODS / SERVICES
    matching_basis          varchar(30),              -- QUANTITY / AMOUNT
    category_id             bigint,
    unit_of_measure         varchar(25),
    receiving_flag          varchar(1),
    inactive_date           timestamp(0),
    @WHO@,
    @DFF@
);

CREATE TABLE po.po_headers_all (
    po_header_id            bigint       PRIMARY KEY,
    segment1                varchar(20)  NOT NULL,
    type_lookup_code        varchar(25)  NOT NULL,   -- STANDARD / BLANKET
    vendor_id               bigint       NOT NULL,
    vendor_site_id          bigint       NOT NULL,
    vendor_contact_id       bigint,
    agent_id                bigint       NOT NULL,
    ship_to_location_id     bigint,
    bill_to_location_id     bigint,
    terms_id                bigint,
    currency_code           varchar(15)  NOT NULL,
    rate_type               varchar(30),
    rate_date               timestamp(0),
    rate                    numeric,
    authorization_status    varchar(25),
    approved_flag           varchar(1),
    approved_date           timestamp(0),
    closed_code             varchar(25),
    closed_date             timestamp(0),
    cancel_flag             varchar(1)   DEFAULT 'N',
    comments                varchar(240),
    start_date              timestamp(0),
    end_date                timestamp(0),
    blanket_total_amount    numeric,
    amount_limit            numeric,
    org_id                  bigint       NOT NULL,
    @WHO@,
    @DFF@,
    UNIQUE (segment1, org_id, type_lookup_code)
);
CREATE INDEX po_headers_n1 ON po.po_headers_all (vendor_id);

CREATE TABLE po.po_lines_all (
    po_line_id              bigint       PRIMARY KEY,
    po_header_id            bigint       NOT NULL REFERENCES po.po_headers_all,
    line_num                integer      NOT NULL,
    line_type_id            bigint       NOT NULL,
    item_id                 bigint,
    item_description        varchar(240),
    category_id             bigint,
    unit_meas_lookup_code   varchar(25),
    unit_price              numeric,
    quantity                numeric,
    amount                  numeric,
    vendor_product_num      varchar(25),
    matching_basis          varchar(30),
    order_type_lookup_code  varchar(25),
    closed_code             varchar(25),
    cancel_flag             varchar(1)   DEFAULT 'N',
    org_id                  bigint       NOT NULL,
    @WHO@,
    @DFF@,
    UNIQUE (po_header_id, line_num)
);

CREATE TABLE po.po_line_locations_all (
    line_location_id        bigint       PRIMARY KEY,
    po_header_id            bigint       NOT NULL,
    po_line_id              bigint       NOT NULL REFERENCES po.po_lines_all,
    shipment_num            integer      NOT NULL,
    shipment_type           varchar(25)  NOT NULL,   -- STANDARD / BLANKET
    quantity                numeric,
    quantity_received       numeric      DEFAULT 0,
    quantity_accepted       numeric      DEFAULT 0,
    quantity_rejected       numeric      DEFAULT 0,
    quantity_billed         numeric      DEFAULT 0,
    quantity_cancelled      numeric      DEFAULT 0,
    amount                  numeric,
    amount_received         numeric,
    amount_billed           numeric,
    price_override          numeric,
    need_by_date            timestamp(0),
    promised_date           timestamp(0),
    ship_to_location_id     bigint,
    ship_to_organization_id bigint,
    receipt_required_flag   varchar(1),
    inspection_required_flag varchar(1),
    match_option            varchar(25),              -- P (PO) / R (receipt)
    qty_rcv_tolerance       numeric,
    invoice_close_tolerance numeric,
    receive_close_tolerance numeric,
    taxable_flag            varchar(1),
    closed_code             varchar(25),
    approved_flag           varchar(1),
    cancel_flag             varchar(1)   DEFAULT 'N',
    org_id                  bigint       NOT NULL,
    @WHO@,
    @DFF@
);
CREATE INDEX po_line_locations_n1 ON po.po_line_locations_all (po_line_id);
CREATE INDEX po_line_locations_n2 ON po.po_line_locations_all (po_header_id);

CREATE TABLE po.po_distributions_all (
    po_distribution_id      bigint       PRIMARY KEY,
    po_header_id            bigint       NOT NULL,
    po_line_id              bigint       NOT NULL,
    line_location_id        bigint       NOT NULL REFERENCES po.po_line_locations_all,
    distribution_num        integer      NOT NULL,
    set_of_books_id         bigint       NOT NULL,
    code_combination_id     bigint       NOT NULL,
    accrual_account_id      bigint,
    variance_account_id     bigint,
    budget_account_id       bigint,
    quantity_ordered        numeric,
    quantity_delivered      numeric      DEFAULT 0,
    quantity_billed         numeric      DEFAULT 0,
    quantity_cancelled      numeric      DEFAULT 0,
    amount_billed           numeric      DEFAULT 0,
    destination_type_code   varchar(25),
    deliver_to_location_id  bigint,
    deliver_to_person_id    bigint,
    accrue_on_receipt_flag  varchar(1),
    project_id              bigint,
    task_id                 bigint,
    org_id                  bigint       NOT NULL,
    @WHO@,
    @DFF@
);
CREATE INDEX po_distributions_n1 ON po.po_distributions_all (line_location_id);

-- ---------------------------------------------------------------- receiving
CREATE SEQUENCE po.rcv_shipment_headers_s START 900000;
CREATE SEQUENCE po.rcv_shipment_lines_s START 1900000;
CREATE SEQUENCE po.rcv_transactions_s START 2900000;
CREATE SEQUENCE po.rcv_receipt_num_s START 17000;

CREATE TABLE po.rcv_shipment_headers (
    shipment_header_id      bigint       PRIMARY KEY,
    receipt_num             varchar(30),
    receipt_source_code     varchar(25)  NOT NULL,
    vendor_id               bigint,
    vendor_site_id          bigint,
    shipment_num            varchar(30),
    packing_slip            varchar(25),
    shipped_date            timestamp(0),
    ship_to_org_id          bigint,
    @WHO@,
    @DFF@
);

CREATE TABLE po.rcv_shipment_lines (
    shipment_line_id        bigint       PRIMARY KEY,
    shipment_header_id      bigint       NOT NULL REFERENCES po.rcv_shipment_headers,
    line_num                integer      NOT NULL,
    item_id                 bigint,
    item_description        varchar(240),
    quantity_shipped        numeric,
    quantity_received       numeric,
    unit_of_measure         varchar(25),
    po_header_id            bigint,
    po_line_id              bigint,
    po_line_location_id     bigint,
    po_distribution_id      bigint,
    shipment_line_status_code varchar(25),
    to_organization_id      bigint,
    @WHO@,
    @DFF@
);
CREATE INDEX rcv_shipment_lines_n1 ON po.rcv_shipment_lines (po_line_location_id);

CREATE TABLE po.rcv_transactions (
    transaction_id          bigint       PRIMARY KEY,
    parent_transaction_id   bigint,
    transaction_type        varchar(25)  NOT NULL,
    transaction_date        timestamp(0) NOT NULL,
    quantity                numeric      NOT NULL,
    unit_of_measure         varchar(25),
    shipment_header_id      bigint,
    shipment_line_id        bigint,
    po_header_id            bigint,
    po_line_id              bigint,
    po_line_location_id     bigint,
    po_distribution_id      bigint,
    vendor_id               bigint,
    vendor_site_id          bigint,
    po_unit_price           numeric,
    currency_code           varchar(15),
    destination_type_code   varchar(25),
    organization_id         bigint,
    @WHO@,
    @DFF@
);
CREATE INDEX rcv_transactions_n1 ON po.rcv_transactions (po_line_location_id);
