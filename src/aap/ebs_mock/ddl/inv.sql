-- INV (items, categories, units of measure)

CREATE SEQUENCE inv.mtl_system_items_b_s START 150000;
CREATE SEQUENCE inv.mtl_categories_s START 5000;

CREATE TABLE inv.mtl_units_of_measure_tl (
    unit_of_measure         varchar(25)  NOT NULL,
    uom_code                varchar(3)   NOT NULL,
    uom_class               varchar(10)  NOT NULL,
    base_uom_flag           varchar(1)   NOT NULL DEFAULT 'N',
    unit_of_measure_tl      varchar(25)  NOT NULL,
    description             varchar(50),
    language                varchar(4)   NOT NULL DEFAULT 'US',
    source_lang             varchar(4)   NOT NULL DEFAULT 'US',
    disable_date            timestamp(0),
    @WHO@,
    @DFF@,
    PRIMARY KEY (unit_of_measure, language),
    UNIQUE (uom_code, language)
);

CREATE TABLE inv.mtl_categories_b (
    category_id             bigint       PRIMARY KEY,
    structure_id            bigint       NOT NULL,
    segment1 varchar(40), segment2 varchar(40), segment3 varchar(40), segment4 varchar(40),
    enabled_flag            varchar(1)   NOT NULL DEFAULT 'Y',
    summary_flag            varchar(1)   NOT NULL DEFAULT 'N',
    start_date_active       timestamp(0),
    end_date_active         timestamp(0),
    disable_date            timestamp(0),
    @WHO@,
    @DFF@
);

CREATE TABLE inv.mtl_categories_tl (
    category_id             bigint       NOT NULL REFERENCES inv.mtl_categories_b,
    language                varchar(4)   NOT NULL DEFAULT 'US',
    source_lang             varchar(4)   NOT NULL DEFAULT 'US',
    description             varchar(240),
    @WHO@,
    PRIMARY KEY (category_id, language)
);

CREATE TABLE inv.mtl_system_items_b (
    inventory_item_id       bigint       NOT NULL,
    organization_id         bigint       NOT NULL,
    segment1                varchar(40)  NOT NULL,
    description             varchar(240),
    primary_uom_code        varchar(3),
    primary_unit_of_measure varchar(25),
    item_type               varchar(30),
    purchasing_item_flag    varchar(1)   NOT NULL DEFAULT 'Y',
    inventory_item_flag     varchar(1)   NOT NULL DEFAULT 'N',
    expense_account         bigint,
    list_price_per_unit     numeric,
    enabled_flag            varchar(1)   NOT NULL DEFAULT 'Y',
    @WHO@,
    @DFF@,
    PRIMARY KEY (inventory_item_id, organization_id)
);

CREATE TABLE inv.mtl_item_categories (
    inventory_item_id       bigint       NOT NULL,
    organization_id         bigint       NOT NULL,
    category_set_id         bigint       NOT NULL,
    category_id             bigint       NOT NULL REFERENCES inv.mtl_categories_b,
    @WHO@,
    PRIMARY KEY (inventory_item_id, organization_id, category_set_id)
);
