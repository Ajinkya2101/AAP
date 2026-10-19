-- Public EBS APIs the adapter calls as apps_intf (idempotent: re-applied by every migrate).

-- AP_HOLDS_PKG.INSERT_SINGLE_HOLD: place a manual hold on an imported invoice.
-- Returns the hold_id; an unreleased hold with the same code is reused, not duplicated.
CREATE OR REPLACE FUNCTION apps.ap_holds_pkg_insert_single_hold(
    p_invoice_id       bigint,
    p_hold_lookup_code varchar,
    p_hold_type        varchar DEFAULT 'INVOICE HOLD REASON',
    p_hold_reason      varchar DEFAULT NULL,
    p_held_by          bigint DEFAULT 1318
) RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ap, pg_temp
AS $$
DECLARE
    v_org_id  bigint;
    v_hold_id bigint;
BEGIN
    SELECT org_id INTO v_org_id FROM ap.ap_invoices_all WHERE invoice_id = p_invoice_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'AP_HOLDS_PKG: invoice % does not exist', p_invoice_id USING ERRCODE = 'no_data_found';
    END IF;
    SELECT hold_id INTO v_hold_id FROM ap.ap_holds_all
    WHERE invoice_id = p_invoice_id AND hold_lookup_code = p_hold_lookup_code AND release_lookup_code IS NULL
    LIMIT 1;
    IF FOUND THEN
        RETURN v_hold_id;
    END IF;
    v_hold_id := nextval('ap.ap_holds_s');
    INSERT INTO ap.ap_holds_all (hold_id, invoice_id, hold_lookup_code, hold_reason, hold_date, held_by,
                                 status_flag, org_id, created_by, last_updated_by)
    VALUES (v_hold_id, p_invoice_id, p_hold_lookup_code, left(p_hold_reason, 240), localtimestamp(0), p_held_by,
            'S', v_org_id, p_held_by, p_held_by);
    RETURN v_hold_id;
END $$;

REVOKE EXECUTE ON FUNCTION apps.ap_holds_pkg_insert_single_hold(bigint, varchar, varchar, varchar, bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION apps.ap_holds_pkg_insert_single_hold(bigint, varchar, varchar, varchar, bigint) TO apps_intf;
