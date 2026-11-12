-- Objects added after the first release (2026-11-04: chart of accounts layout, payment methods,
-- payment defaults). Idempotent: applied when the schema is created and again on every refresh
-- (ddl_loader.REFRESH_FILES), so a database seeded before these existed gets them without a rebuild.

-- ------------------------------------------------ chart of accounts structure (key flexfield GL#)
-- FND_ID_FLEX_STRUCTURES: one row per chart of accounts (ID_FLEX_NUM = CHART_OF_ACCOUNTS_ID),
-- with the delimiter of concatenated accounts. FND_ID_FLEX_SEGMENTS: which column holds which
-- segment, its order and value set. The KFV view and the import read the delimiter from here.
CREATE TABLE IF NOT EXISTS fnd.fnd_id_flex_structures (
    application_id          bigint       NOT NULL DEFAULT 101,
    id_flex_code            varchar(4)   NOT NULL DEFAULT 'GL#',
    id_flex_num             bigint       NOT NULL,
    id_flex_structure_code  varchar(30)  NOT NULL,
    id_flex_structure_name  varchar(30),
    concatenated_segment_delimiter varchar(1) NOT NULL,
    enabled_flag            varchar(1)   NOT NULL DEFAULT 'Y',
    @WHO@,
    PRIMARY KEY (application_id, id_flex_code, id_flex_num)
);

CREATE TABLE IF NOT EXISTS fnd.fnd_id_flex_segments (
    application_id          bigint       NOT NULL DEFAULT 101,
    id_flex_code            varchar(4)   NOT NULL DEFAULT 'GL#',
    id_flex_num             bigint       NOT NULL,
    application_column_name varchar(30)  NOT NULL,     -- SEGMENT1 .. SEGMENT30
    segment_name            varchar(30)  NOT NULL,
    segment_num             integer      NOT NULL,     -- display order
    flex_value_set_id       bigint,
    enabled_flag            varchar(1)   NOT NULL DEFAULT 'Y',
    display_flag            varchar(1)   NOT NULL DEFAULT 'Y',
    @WHO@,
    PRIMARY KEY (application_id, id_flex_code, id_flex_num, application_column_name)
);

-- -------------------------------------------------------------------- payment methods (IBY)
CREATE TABLE IF NOT EXISTS iby.iby_payment_methods_b (
    payment_method_code     varchar(30)  PRIMARY KEY,
    support_bills_payable_flag varchar(1) NOT NULL DEFAULT 'N',
    inactive_date           timestamp(0),
    seeded_flag             varchar(1)   NOT NULL DEFAULT 'N',
    @WHO@
);

CREATE TABLE IF NOT EXISTS iby.iby_payment_methods_tl (
    payment_method_code     varchar(30)  NOT NULL REFERENCES iby.iby_payment_methods_b,
    language                varchar(4)   NOT NULL DEFAULT 'US',
    source_lang             varchar(4)   NOT NULL DEFAULT 'US',
    payment_method_name     varchar(100) NOT NULL,
    description             varchar(255),
    @WHO@,
    PRIMARY KEY (payment_method_code, language)
);

-- ------------------------------------------------------------------- columns added later
ALTER TABLE ap.ap_supplier_sites_all ADD COLUMN IF NOT EXISTS terms_date_basis varchar(25);
ALTER TABLE ap.ap_invoices_interface ADD COLUMN IF NOT EXISTS payment_currency_code varchar(15);
-- Reversed distributions (a miskeyed account reversed and keyed again): both rows of the pair carry
-- REVERSAL_FLAG = 'Y' and are left out of the GL history.
ALTER TABLE ap.ap_invoice_distributions_all ADD COLUMN IF NOT EXISTS reversal_flag varchar(1);
ALTER TABLE ap.ap_invoice_distributions_all ADD COLUMN IF NOT EXISTS parent_reversal_id bigint;

-- Distribution sets (2026-11-12): a non-PO line keyed through a set, and a site's default set. The
-- context pack names them (PLAN-MODEL-JSON §4); the replica loads them from the delivery.
CREATE TABLE IF NOT EXISTS ap.ap_distribution_sets_all (
    distribution_set_id     bigint       PRIMARY KEY,
    distribution_set_name   varchar(50)  NOT NULL,
    description             varchar(240),
    total_percent_distribution numeric   DEFAULT 100,
    inactive_date           timestamp(0),
    org_id                  bigint,
    @WHO@
);
CREATE TABLE IF NOT EXISTS ap.ap_distribution_set_lines_all (
    distribution_set_id     bigint       NOT NULL REFERENCES ap.ap_distribution_sets_all,
    distribution_set_line_number integer NOT NULL,
    dist_code_combination_id bigint      NOT NULL,
    percent_distribution    numeric      NOT NULL,
    description             varchar(240),
    org_id                  bigint,
    @WHO@,
    PRIMARY KEY (distribution_set_id, distribution_set_line_number)
);
ALTER TABLE ap.ap_invoice_lines_all ADD COLUMN IF NOT EXISTS distribution_set_id bigint;
ALTER TABLE ap.ap_supplier_sites_all ADD COLUMN IF NOT EXISTS distribution_set_id bigint;

-- Unique combinations over 8 segments. The first index covered segment1-4 only, which an 8-segment
-- chart breaks (the same company / cost center / account with different segments 5-8).
DROP INDEX IF EXISTS gl.gl_code_combinations_u2;
CREATE UNIQUE INDEX IF NOT EXISTS gl_code_combinations_u3
    ON gl.gl_code_combinations (chart_of_accounts_id, segment1, segment2, segment3, segment4,
                                segment5, segment6, segment7, segment8) NULLS NOT DISTINCT;

-- ------------------------------------------------------------- APPS synonyms and views
-- The synonyms are `SELECT *` views: re-created so they show the columns added above.
CREATE OR REPLACE VIEW apps.fnd_id_flex_structures AS SELECT * FROM fnd.fnd_id_flex_structures;
CREATE OR REPLACE VIEW apps.fnd_id_flex_segments AS SELECT * FROM fnd.fnd_id_flex_segments;
CREATE OR REPLACE VIEW apps.iby_payment_methods_b AS SELECT * FROM iby.iby_payment_methods_b;
CREATE OR REPLACE VIEW apps.iby_payment_methods_tl AS SELECT * FROM iby.iby_payment_methods_tl;
CREATE OR REPLACE VIEW apps.ap_supplier_sites_all AS SELECT * FROM ap.ap_supplier_sites_all;
CREATE OR REPLACE VIEW apps.po_vendor_sites_all AS SELECT * FROM ap.ap_supplier_sites_all;
CREATE OR REPLACE VIEW apps.ap_invoices_interface AS SELECT * FROM ap.ap_invoices_interface;
CREATE OR REPLACE VIEW apps.ap_invoice_distributions_all AS SELECT * FROM ap.ap_invoice_distributions_all;
CREATE OR REPLACE VIEW apps.ap_invoice_lines_all AS SELECT * FROM ap.ap_invoice_lines_all;
CREATE OR REPLACE VIEW apps.ap_distribution_sets_all AS SELECT * FROM ap.ap_distribution_sets_all;
CREATE OR REPLACE VIEW apps.ap_distribution_set_lines_all AS SELECT * FROM ap.ap_distribution_set_lines_all;

-- IBY_PAYMENT_METHODS_VL
CREATE OR REPLACE VIEW apps.iby_payment_methods_vl AS
SELECT b.payment_method_code, t.payment_method_name, t.description, b.support_bills_payable_flag,
       b.inactive_date, b.seeded_flag
FROM   iby.iby_payment_methods_b b
JOIN   iby.iby_payment_methods_tl t ON t.payment_method_code = b.payment_method_code AND t.language = 'US';

-- GL_CODE_COMBINATIONS_KFV: the populated segments joined by the structure's delimiter
-- (concat_ws skips the NULL segments past the last one; '-' when a chart has no structure row).
CREATE OR REPLACE VIEW apps.gl_code_combinations_kfv AS
SELECT c.*,
       concat_ws(coalesce(s.concatenated_segment_delimiter, '-'),
                 c.segment1, c.segment2, c.segment3, c.segment4, c.segment5, c.segment6, c.segment7, c.segment8,
                 c.segment9, c.segment10, c.segment11, c.segment12, c.segment13, c.segment14, c.segment15,
                 c.segment16, c.segment17, c.segment18, c.segment19, c.segment20, c.segment21, c.segment22,
                 c.segment23, c.segment24, c.segment25, c.segment26, c.segment27, c.segment28, c.segment29,
                 c.segment30) AS concatenated_segments,
       concat_ws(coalesce(s.concatenated_segment_delimiter, '-'),
                 c.segment1, c.segment2, c.segment3, c.segment4, c.segment5, c.segment6, c.segment7, c.segment8,
                 c.segment9, c.segment10, c.segment11, c.segment12, c.segment13, c.segment14, c.segment15,
                 c.segment16, c.segment17, c.segment18, c.segment19, c.segment20, c.segment21, c.segment22,
                 c.segment23, c.segment24, c.segment25, c.segment26, c.segment27, c.segment28, c.segment29,
                 c.segment30) AS padded_concatenated_segments
FROM   gl.gl_code_combinations c
LEFT JOIN fnd.fnd_id_flex_structures s
       ON s.application_id = 101 AND s.id_flex_code = 'GL#' AND s.id_flex_num = c.chart_of_accounts_id;

-- --------------------------------------------- backfill for databases seeded before 2026-11-04
-- The synthetic chart (4 segments, '-', value sets ACSE_*) gets its structure rows; a chart loaded
-- from client extracts brings its own and is left alone (ON CONFLICT / NOT EXISTS).
INSERT INTO fnd.fnd_id_flex_structures (id_flex_num, id_flex_structure_code, id_flex_structure_name,
                                        concatenated_segment_delimiter)
SELECT DISTINCT l.chart_of_accounts_id, 'ACSE_ACCOUNTING_FLEX', 'ACSE Accounting Flex', '-'
FROM   gl.gl_ledgers l
WHERE  EXISTS (SELECT 1 FROM fnd.fnd_flex_value_sets WHERE flex_value_set_name = 'ACSE_ACCOUNT')
ON CONFLICT DO NOTHING;

INSERT INTO fnd.fnd_id_flex_segments (id_flex_num, application_column_name, segment_name, segment_num,
                                      flex_value_set_id)
SELECT s.id_flex_num, x.col, x.name, x.num, v.flex_value_set_id
FROM   fnd.fnd_id_flex_structures s
CROSS JOIN (VALUES ('SEGMENT1', 'Company', 1, 'ACSE_COMPANY'),
                   ('SEGMENT2', 'Cost Centre', 2, 'ACSE_COST_CENTRE'),
                   ('SEGMENT3', 'Account', 3, 'ACSE_ACCOUNT'),
                   ('SEGMENT4', 'Sub-account', 4, 'ACSE_SUBACCOUNT')) AS x(col, name, num, value_set)
JOIN   fnd.fnd_flex_value_sets v ON v.flex_value_set_name = x.value_set
WHERE  s.id_flex_structure_code = 'ACSE_ACCOUNTING_FLEX'
  AND  NOT EXISTS (SELECT 1 FROM fnd.fnd_id_flex_segments g WHERE g.id_flex_num = s.id_flex_num)
ON CONFLICT DO NOTHING;

-- Payment methods: before IBY_PAYMENT_METHODS existed the mock kept them as PAYMENT METHOD lookups.
INSERT INTO iby.iby_payment_methods_b (payment_method_code, seeded_flag)
SELECT lookup_code, 'Y' FROM fnd.fnd_lookup_values WHERE lookup_type = 'PAYMENT METHOD' AND language = 'US'
ON CONFLICT DO NOTHING;
INSERT INTO iby.iby_payment_methods_tl (payment_method_code, payment_method_name, description)
SELECT lookup_code, meaning, description FROM fnd.fnd_lookup_values
WHERE  lookup_type = 'PAYMENT METHOD' AND language = 'US'
ON CONFLICT DO NOTHING;

-- ------------------------------------------------------------ Payables lookups (2026-11-04)
-- FND_LOOKUP_VALUES_VL: the lookups in the session language, as the Workbench and the reference sync
-- read them (invoice types, invoice line types, invoice sources; common/lookups.py).
CREATE OR REPLACE VIEW apps.fnd_lookup_values_vl AS
SELECT lookup_type, lookup_code, meaning, description, enabled_flag, start_date_active, end_date_active,
       security_group_id, view_application_id, tag
FROM   fnd.fnd_lookup_values
WHERE  language = 'US';

-- Databases seeded before INVOICE LINE TYPE existed in the mock get Oracle's seeded line types. Not on
-- a new schema (no lookups yet: the seed or the replica loader brings them), and not on a replica
-- whose delivery brought its own list (NOT EXISTS on the lookup type).
INSERT INTO fnd.fnd_lookup_values (lookup_type, language, lookup_code, meaning, description, enabled_flag,
                                   source_lang, security_group_id, view_application_id)
SELECT 'INVOICE LINE TYPE', 'US', x.code, x.meaning, x.description, 'Y', 'US', 0, 200
FROM   (VALUES ('ITEM', 'Item', 'Goods or services'), ('FREIGHT', 'Freight', 'Freight charge'),
               ('MISCELLANEOUS', 'Miscellaneous', 'Miscellaneous charge'), ('TAX', 'Tax', 'Tax'),
               ('AWT', 'Withholding Tax', 'Withholding tax'),
               ('PREPAY', 'Prepayment', 'Prepayment application')) AS x(code, meaning, description)
WHERE  NOT EXISTS (SELECT 1 FROM fnd.fnd_lookup_values WHERE lookup_type = 'INVOICE LINE TYPE')
  AND  EXISTS (SELECT 1 FROM fnd.fnd_lookup_values WHERE lookup_type = 'INVOICE TYPE')
ON CONFLICT DO NOTHING;
DELETE FROM fnd.fnd_lookup_values WHERE lookup_type = 'LINE TYPE';   -- the mock's earlier name for it

-- -------------------------------------------------------------------------------- grants
GRANT SELECT ON apps.fnd_id_flex_structures, apps.fnd_id_flex_segments, apps.iby_payment_methods_b,
                apps.iby_payment_methods_tl, apps.iby_payment_methods_vl, apps.gl_code_combinations_kfv,
                apps.ap_supplier_sites_all, apps.po_vendor_sites_all, apps.ap_invoices_interface,
                apps.ap_invoice_distributions_all, apps.fnd_lookup_values_vl, apps.ap_invoice_lines_all,
                apps.ap_distribution_sets_all, apps.ap_distribution_set_lines_all
      TO apps_ro, apps_intf;
GRANT INSERT ON apps.ap_invoices_interface TO apps_intf;
