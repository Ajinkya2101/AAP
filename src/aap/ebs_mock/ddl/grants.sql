-- Adapter accounts (TECH-STACK 9): apps_ro reads apps.*; apps_intf also writes the
-- Payables Open Interface and submits APXIIMPT. Neither can touch base tables.

GRANT USAGE ON SCHEMA apps TO apps_ro, apps_intf;
GRANT SELECT ON ALL TABLES IN SCHEMA apps TO apps_ro, apps_intf;

GRANT INSERT ON apps.ap_invoices_interface, apps.ap_invoice_lines_interface TO apps_intf;

-- Interface ID sequences (AP_INVOICES_INTERFACE_S.NEXTVAL in real EBS).
GRANT USAGE ON SCHEMA ap TO apps_intf;
GRANT USAGE, SELECT ON SEQUENCE ap.ap_invoices_interface_s,
                                ap.ap_invoice_lines_interface_s,
                                ap.ap_interface_groups_s TO apps_intf;

REVOKE EXECUTE ON FUNCTION apps.fnd_request_submit_request(
    varchar, varchar, varchar, varchar, boolean,
    varchar, varchar, varchar, varchar, varchar,
    varchar, varchar, varchar, varchar, varchar, bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION apps.fnd_request_submit_request(
    varchar, varchar, varchar, varchar, boolean,
    varchar, varchar, varchar, varchar, varchar,
    varchar, varchar, varchar, varchar, varchar, bigint) TO apps_intf;
