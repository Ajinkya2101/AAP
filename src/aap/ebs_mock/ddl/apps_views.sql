-- APPS "synonyms": one simple (auto-updatable) view per base table, so adapter SQL
-- written as `SELECT ... FROM ap_invoices_all` runs unchanged with search_path=apps.
DO $$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE table_schema IN ('fnd', 'hr', 'gl', 'hz', 'iby', 'zx', 'ap', 'po', 'inv')
          AND table_type = 'BASE TABLE'
        ORDER BY table_schema, table_name
    LOOP
        EXECUTE format('CREATE OR REPLACE VIEW apps.%I AS SELECT * FROM %I.%I',
                       r.table_name, r.table_schema, r.table_name);
    END LOOP;
END $$;

-- ------------------------------------------------------ standard EBS views

-- AP_TERMS: AP_TERMS_TL in the session language (US).
CREATE OR REPLACE VIEW apps.ap_terms AS
SELECT term_id, name, description, enabled_flag, type, rank, due_cutoff_day,
       start_date_active, end_date_active,
       attribute_category, attribute1, attribute2, attribute3, attribute4, attribute5,
       created_by, creation_date, last_updated_by, last_update_date, last_update_login
FROM   ap.ap_terms_tl
WHERE  language = 'US';

-- HR_OPERATING_UNITS
CREATE OR REPLACE VIEW apps.hr_operating_units AS
SELECT o.business_group_id,
       o.organization_id,
       o.name,
       o.date_from,
       o.date_to,
       i.org_information5                 AS short_code,
       i.org_information3::bigint         AS set_of_books_id,
       i.org_information2::bigint         AS default_legal_context_id,
       'Y'::varchar(1)                    AS usable_flag
FROM   hr.hr_all_organization_units o
JOIN   hr.hr_organization_information i
       ON  i.organization_id = o.organization_id
       AND i.org_information_context = 'Operating Unit Information';

-- GL_CODE_COMBINATIONS_KFV (4 populated segments, delimiter '-')
CREATE OR REPLACE VIEW apps.gl_code_combinations_kfv AS
SELECT c.*,
       concat_ws('-', c.segment1, c.segment2, c.segment3, c.segment4) AS concatenated_segments,
       concat_ws('-', c.segment1, c.segment2, c.segment3, c.segment4) AS padded_concatenated_segments
FROM   gl.gl_code_combinations c;

-- GL_SETS_OF_BOOKS (11i compatible view over GL_LEDGERS)
CREATE OR REPLACE VIEW apps.gl_sets_of_books AS
SELECT ledger_id AS set_of_books_id, name, short_name, description, chart_of_accounts_id,
       currency_code, period_set_name, accounted_period_type
FROM   gl.gl_ledgers;

-- PO_VENDORS / PO_VENDOR_SITES_ALL (11i compatible views)
CREATE OR REPLACE VIEW apps.po_vendors AS
SELECT vendor_id, vendor_name, vendor_name_alt, segment1, party_id, vendor_type_lookup_code,
       enabled_flag, summary_flag, one_time_flag, num_1099, vat_registration_num, terms_id,
       pay_group_lookup_code, invoice_currency_code, payment_currency_code,
       payment_method_lookup_code, match_option, receipt_required_flag, inspection_required_flag,
       hold_all_payments_flag, start_date_active, end_date_active,
       attribute_category, attribute1, attribute2, attribute3, attribute4, attribute5,
       last_update_date, last_updated_by, creation_date, created_by
FROM   ap.ap_suppliers;

CREATE OR REPLACE VIEW apps.po_vendor_sites_all AS
SELECT * FROM ap.ap_supplier_sites_all;

-- FND_FLEX_VALUES_VL
CREATE OR REPLACE VIEW apps.fnd_flex_values_vl AS
SELECT b.flex_value_id, b.flex_value_set_id, b.flex_value, b.enabled_flag, b.summary_flag,
       b.start_date_active, b.end_date_active, b.parent_flex_value_low,
       b.compiled_value_attributes, t.description, t.flex_value_meaning
FROM   fnd.fnd_flex_values b
JOIN   fnd.fnd_flex_values_tl t ON t.flex_value_id = b.flex_value_id AND t.language = 'US';

-- MTL_CATEGORIES_VL / MTL_CATEGORIES_KFV
CREATE OR REPLACE VIEW apps.mtl_categories_vl AS
SELECT b.category_id, b.structure_id, b.segment1, b.segment2, b.segment3, b.segment4,
       b.enabled_flag, b.summary_flag, b.start_date_active, b.end_date_active, b.disable_date,
       t.description
FROM   inv.mtl_categories_b b
JOIN   inv.mtl_categories_tl t ON t.category_id = b.category_id AND t.language = 'US';

CREATE OR REPLACE VIEW apps.mtl_categories_kfv AS
SELECT b.*, concat_ws('-', b.segment1, b.segment2, b.segment3, b.segment4) AS concatenated_segments
FROM   inv.mtl_categories_b b;

-- MTL_UNITS_OF_MEASURE (US)
CREATE OR REPLACE VIEW apps.mtl_units_of_measure AS
SELECT unit_of_measure, uom_code, uom_class, base_uom_flag, unit_of_measure_tl, description, disable_date
FROM   inv.mtl_units_of_measure_tl
WHERE  language = 'US';

-- ------------------------------------------------ FND_REQUEST.SUBMIT_REQUEST
-- Thin wrapper: records the request (phase P) and notifies the mock concurrent
-- manager (aap.ebs_mock.concurrent_manager), which runs APXIIMPT.
-- APXIIMPT arguments used by the mock:
--   argument1 org_id, argument2 source, argument3 group_id, argument4 batch name,
--   argument5 hold name, argument6 hold reason, argument7 gl date, argument8 purge,
--   argument9 trace switch, argument10 debug switch
CREATE OR REPLACE FUNCTION apps.fnd_request_submit_request(
    p_application varchar,
    p_program     varchar,
    p_description varchar DEFAULT NULL,
    p_start_time  varchar DEFAULT NULL,
    p_sub_request boolean DEFAULT false,
    p_argument1   varchar DEFAULT NULL,
    p_argument2   varchar DEFAULT NULL,
    p_argument3   varchar DEFAULT NULL,
    p_argument4   varchar DEFAULT NULL,
    p_argument5   varchar DEFAULT NULL,
    p_argument6   varchar DEFAULT NULL,
    p_argument7   varchar DEFAULT NULL,
    p_argument8   varchar DEFAULT NULL,
    p_argument9   varchar DEFAULT NULL,
    p_argument10  varchar DEFAULT NULL,
    p_requested_by bigint DEFAULT 1318
) RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = fnd, pg_temp
AS $$
DECLARE
    v_program   fnd.fnd_concurrent_programs%ROWTYPE;
    v_request_id bigint;
    v_app_id    bigint;
BEGIN
    v_app_id := CASE upper(p_application) WHEN 'SQLAP' THEN 200 WHEN 'PO' THEN 201 WHEN 'SQLGL' THEN 101 ELSE NULL END;
    SELECT * INTO v_program
    FROM fnd.fnd_concurrent_programs
    WHERE application_id = v_app_id
      AND concurrent_program_name = upper(p_program)
      AND enabled_flag = 'Y';
    IF NOT FOUND THEN
        RETURN 0;   -- FND_REQUEST.SUBMIT_REQUEST returns 0 on failure
    END IF;

    v_request_id := nextval('fnd.fnd_concurrent_requests_s');
    INSERT INTO fnd.fnd_concurrent_requests (
        request_id, program_application_id, concurrent_program_id, phase_code, status_code,
        description, argument_text,
        argument1, argument2, argument3, argument4, argument5,
        argument6, argument7, argument8, argument9, argument10,
        requested_by, request_date, requested_start_date, org_id,
        created_by, creation_date, last_updated_by, last_update_date)
    VALUES (
        v_request_id, v_app_id, v_program.concurrent_program_id, 'P', 'I',
        p_description,
        concat_ws(', ', p_argument1, p_argument2, p_argument3, p_argument4, p_argument5,
                        p_argument6, p_argument7, p_argument8, p_argument9, p_argument10),
        p_argument1, p_argument2, p_argument3, p_argument4, p_argument5,
        p_argument6, p_argument7, p_argument8, p_argument9, p_argument10,
        p_requested_by, localtimestamp(0),
        coalesce(to_timestamp(p_start_time, 'DD-MON-YYYY HH24:MI:SS')::timestamp(0), localtimestamp(0)),
        CASE WHEN p_argument1 ~ '^[0-9]+$' THEN p_argument1::bigint END,
        p_requested_by, localtimestamp(0), p_requested_by, localtimestamp(0));

    PERFORM pg_notify('fnd_concurrent_manager', v_request_id::text);
    RETURN v_request_id;
END $$;
