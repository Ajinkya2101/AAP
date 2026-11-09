"""The extracts a delivery may contain: EBS table names, the columns the checks and loaders need, and
the other file names they are known by.

Column names are EBS's (upper case). A file is matched to a table by its name without extension
(case-insensitive): the table name or one of its aliases (pay_terms.xlsx -> AP_TERMS). A workbook
with several sheets is read sheet by sheet, each sheet named like a file.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TableSpec:
    name: str
    key: tuple[str, ...]                     # unique per row
    required: tuple[str, ...]                # the checks / loaders need these
    aliases: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()           # used when present
    description: str = ""
    columns_alt: dict[str, str] = field(default_factory=dict)   # other spelling -> EBS column
    # generic names ("Header", "Line"): they match only a sheet that has the required columns, so a
    # list-of-values workbook with the same sheet names is not read as an extract
    weak_aliases: tuple[str, ...] = ()


TABLES: dict[str, TableSpec] = {t.name: t for t in [
    TableSpec("SELECTED_SUPPLIERS", key=("SEGMENT1",), required=("SEGMENT1",),
              aliases=("suppliers", "supplier_list", "chosen_suppliers"),
              columns_alt={"VENDOR_NUM": "SEGMENT1", "SUPPLIER_NUM": "SEGMENT1", "SUPPLIER_NUMBER": "SEGMENT1",
                           "VENDOR_NUMBER": "SEGMENT1"},
              description="The suppliers chosen for the test (supplier numbers)"),
    TableSpec("HR_OPERATING_UNITS", key=("ORGANIZATION_ID",), required=("ORGANIZATION_ID", "NAME"),
              optional=("SET_OF_BOOKS_ID", "SHORT_CODE", "DATE_TO"), aliases=("operating_units", "org"),
              columns_alt={"ORG_ID": "ORGANIZATION_ID"}),
    TableSpec("AP_SUPPLIERS", key=("VENDOR_ID",), required=("VENDOR_ID", "SEGMENT1", "VENDOR_NAME"),
              optional=("VENDOR_NAME_ALT", "ENABLED_FLAG", "END_DATE_ACTIVE", "TERMS_ID", "INVOICE_CURRENCY_CODE",
                        "PAYMENT_METHOD_LOOKUP_CODE", "PAY_GROUP_LOOKUP_CODE", "VAT_REGISTRATION_NUM", "NUM_1099",
                        "VENDOR_TYPE_LOOKUP_CODE"),
              aliases=("supplier_master", "vendors"), columns_alt={"VENDOR_NUM": "SEGMENT1"}),
    TableSpec("AP_SUPPLIER_SITES_ALL", key=("VENDOR_SITE_ID",),
              required=("VENDOR_SITE_ID", "VENDOR_ID", "VENDOR_SITE_CODE", "ORG_ID"),
              optional=("ADDRESS_LINE1", "CITY", "STATE", "ZIP", "COUNTRY", "PAY_SITE_FLAG", "PRIMARY_PAY_SITE_FLAG",
                        "TERMS_ID", "INVOICE_CURRENCY_CODE", "PAYMENT_CURRENCY_CODE", "PAYMENT_METHOD_LOOKUP_CODE",
                        "PAY_GROUP_LOOKUP_CODE", "TERMS_DATE_BASIS", "ACCTS_PAY_CODE_COMBINATION_ID",
                        "DISTRIBUTION_SET_ID", "INACTIVE_DATE", "EMAIL_ADDRESS"),
              aliases=("supplier_sites", "sites")),
    TableSpec("SUPPLIER_SITE_VIEW", key=("VENDOR_SITE_ID",),
              required=("SEGMENT1", "VENDOR_NAME", "VENDOR_SITE_ID", "VENDOR_SITE_CODE", "ORG_ID"),
              optional=("VENDOR_ID", "VENDOR_NAME_ALT", "TERMS_ID", "SUPPLIER_TERMS_ID", "PAY_GROUP_LOOKUP_CODE",
                        "DEFAULT_PAYMENT_METHOD_CODE", "PRIMARY_PAYMENT_METHOD", "PRIMARY_PAYMENT_FLAG",
                        "ACCTS_PAY_CODE_COMBINATION_ID", "DISTRIBUTION_SET_ID"),
              aliases=("ap_sup_list", "sup_list", "supplier_site_list", "supplier_sites_view"),
              columns_alt={"VENDOR_NUMBER": "SEGMENT1", "VENDOR_NUM": "SEGMENT1", "DBA_VENDOR_NAME": "VENDOR_NAME_ALT",
                           "PURCHASING_SITE": "PURCHASING_SITE_FLAG", "PAYMENT_SITE": "PAY_SITE_FLAG",
                           "PURCHASE_PAY_EMAIL_ADDRESS": "EMAIL_ADDRESS", "PO_EMAIL_ADDRESS": "CONTACT_EMAIL_ADDRESS",
                           "ADDRESS_LINE_1": "ADDRESS_LINE1", "ADDRESS_LINE_2": "ADDRESS_LINE2",
                           "TAX_ID": "NUM_1099", "TAX_TYPE": "TYPE_1099", "VENDOR_END_DATE": "END_DATE_ACTIVE",
                           "SITE_NUMBER": "PARTY_SITE_NUMBER", "SITE_CODE": "VENDOR_SITE_CODE",
                           "SITE_ID": "VENDOR_SITE_ID", "SITE_INACTIVE_FLAG": "INACTIVE_DATE",
                           "SITE_INACTIVE_DATE": "INACTIVE_DATE", "VENDOR_TYPE": "VENDOR_TYPE_LOOKUP_CODE",
                           "SUPPLIER_TERMS": "SUPPLIER_TERMS_ID", "SITE_TERMS": "TERMS_ID",
                           "HOLD_ALL_PAYMENTS": "HOLD_ALL_PAYMENTS_FLAG",
                           "SITE_LIABILITY_ACCOUNT": "ACCTS_PAY_CODE_COMBINATION_ID"},
              description="One export joining suppliers, sites, contacts and payment methods (several rows per "
                          "site); split into AP_SUPPLIERS and AP_SUPPLIER_SITES_ALL by aap.clientdata.views"),
    TableSpec("GL_CODE_COMBINATIONS", key=("CODE_COMBINATION_ID",), required=("CODE_COMBINATION_ID", "SEGMENT1"),
              optional=tuple(f"SEGMENT{i}" for i in range(2, 31)) + (
                  "CHART_OF_ACCOUNTS_ID", "ENABLED_FLAG", "DETAIL_POSTING_ALLOWED_FLAG", "ACCOUNT_TYPE",
                  "START_DATE_ACTIVE", "END_DATE_ACTIVE", "CONCATENATED_SEGMENTS"),
              aliases=("code_combinations", "ccids", "accounts")),
    TableSpec("FND_ID_FLEX_SEGMENTS", key=("APPLICATION_COLUMN_NAME",),
              required=("APPLICATION_COLUMN_NAME", "SEGMENT_NAME"),
              optional=("SEGMENT_NUM", "FLEX_VALUE_SET_NAME", "FLEX_VALUE_SET_ID", "ID_FLEX_NUM"),
              aliases=("segments", "coa_segments", "segment_meanings"),
              description="What each account segment is (company, store, natural account, ...)"),
    TableSpec("FND_FLEX_VALUES", key=("FLEX_VALUE_SET_NAME", "FLEX_VALUE"),
              required=("FLEX_VALUE_SET_NAME", "FLEX_VALUE"),
              optional=("DESCRIPTION", "ENABLED_FLAG", "SUMMARY_FLAG", "END_DATE_ACTIVE"),
              aliases=("segment_values", "flex_values", "coa_values")),
    TableSpec("AP_TERMS", key=("TERM_ID",), required=("TERM_ID", "NAME"),
              optional=("DESCRIPTION", "ENABLED_FLAG", "END_DATE_ACTIVE"), aliases=("pay_terms", "terms", "ap_terms_tl")),
    TableSpec("AP_TERMS_LINES", key=("TERM_ID", "SEQUENCE_NUM"), required=("TERM_ID", "SEQUENCE_NUM"),
              optional=("DUE_PERCENT", "DUE_AMOUNT", "DUE_DAYS", "DUE_DAY_OF_MONTH", "DUE_MONTHS_FORWARD",
                        "DISCOUNT_PERCENT", "DISCOUNT_DAYS"), aliases=("terms_lines", "pay_terms_lines")),
    TableSpec("IBY_PAYMENT_METHODS", key=("PAYMENT_METHOD_CODE",), required=("PAYMENT_METHOD_CODE",),
              optional=("PAYMENT_METHOD_NAME", "DESCRIPTION", "INACTIVE_DATE", "LANGUAGE"),
              aliases=("pay_method", "payment_methods", "iby_payment_methods_tl", "iby_payment_methods_b")),
    TableSpec("FND_LOOKUP_VALUES", key=("LOOKUP_TYPE", "LOOKUP_CODE"), required=("LOOKUP_TYPE", "LOOKUP_CODE"),
              optional=("MEANING", "DESCRIPTION", "ENABLED_FLAG", "START_DATE_ACTIVE", "END_DATE_ACTIVE"),
              aliases=("lookups", "fnd_lookup_values_vl", "ap_lookup_codes", "lookup_values"),
              columns_alt={"DISPLAYED_FIELD": "MEANING"},
              description="Payables lookups: invoice types, invoice line types, sources (an extract, or "
                          "written from a list-of-values workbook by python -m aap.clientdata.lov)"),
    TableSpec("STORES", key=("STORE",), required=("STORE", "COST_CENTER"),
              aliases=("store_cost_center", "locations", "store_table"),
              columns_alt={"STORE_NUMBER": "STORE", "LOCATION": "STORE", "LOCATION_CODE": "STORE",
                           "COST_CENTRE": "COST_CENTER", "CC": "COST_CENTER"},
              description="Store / location <-> cost-center segment value"),
    TableSpec("AP_INVOICES_ALL", key=("INVOICE_ID",),
              required=("INVOICE_ID", "VENDOR_ID", "INVOICE_NUM", "INVOICE_DATE", "INVOICE_AMOUNT"),
              optional=("VENDOR_SITE_ID", "ORG_ID", "INVOICE_CURRENCY_CODE", "INVOICE_TYPE_LOOKUP_CODE", "TERMS_ID",
                        "TERMS_DATE", "GL_DATE", "PAYMENT_METHOD_CODE", "PAY_GROUP_LOOKUP_CODE", "SOURCE",
                        "DESCRIPTION", "CANCELLED_DATE", "PO_HEADER_ID", "CREATION_DATE", "TOTAL_TAX_AMOUNT",
                        "ACCTS_PAY_CODE_COMBINATION_ID", "PAYMENT_CURRENCY_CODE", "PARTY_SITE_ID"),
              aliases=("invoices", "invoice_headers"), weak_aliases=("header", "headers", "invoice_header")),
    TableSpec("AP_INVOICE_LINES_ALL", key=("INVOICE_ID", "LINE_NUMBER"),
              required=("INVOICE_ID", "LINE_NUMBER", "LINE_TYPE_LOOKUP_CODE", "AMOUNT"),
              optional=("DESCRIPTION", "QUANTITY_INVOICED", "UNIT_PRICE", "PO_HEADER_ID", "DISCARDED_FLAG",
                        "CANCELLED_FLAG", "ACCOUNTING_DATE", "ORG_ID", "DISTRIBUTION_SET_ID", "DEFAULT_DIST_CCID",
                        "LINE_SOURCE", "MATCH_TYPE", "TAX_REGIME_CODE", "TAX_RATE_CODE"),
              aliases=("invoice_lines", "lines"), weak_aliases=("line", "invoice_line")),
    TableSpec("AP_INVOICE_DISTRIBUTIONS_ALL", key=("INVOICE_DISTRIBUTION_ID",),
              required=("INVOICE_DISTRIBUTION_ID", "INVOICE_ID", "INVOICE_LINE_NUMBER", "AMOUNT",
                        "DIST_CODE_COMBINATION_ID"),
              optional=("DISTRIBUTION_LINE_NUMBER", "LINE_TYPE_LOOKUP_CODE", "DESCRIPTION", "ACCOUNTING_DATE",
                        "REVERSAL_FLAG", "PARENT_REVERSAL_ID", "PO_DISTRIBUTION_ID", "CANCELLATION_FLAG",
                        "ACCTS_PAY_CODE_COMBINATION_ID", "ORG_ID"),
              aliases=("invoice_distributions", "distributions"),
              weak_aliases=("distribution", "invoice_distribution")),
    TableSpec("AP_DISTRIBUTION_SETS_ALL", key=("DISTRIBUTION_SET_ID",),
              required=("DISTRIBUTION_SET_ID", "DISTRIBUTION_SET_NAME"),
              optional=("ORG_ID", "DESCRIPTION", "TOTAL_PERCENT_DISTRIBUTION", "INACTIVE_DATE"),
              aliases=("distribution_sets", "dist_sets")),
    TableSpec("AP_DISTRIBUTION_SET_LINES_ALL", key=("DISTRIBUTION_SET_ID", "DISTRIBUTION_SET_LINE_NUMBER"),
              required=("DISTRIBUTION_SET_ID", "DISTRIBUTION_SET_LINE_NUMBER", "DIST_CODE_COMBINATION_ID",
                        "PERCENT_DISTRIBUTION"),
              optional=("DESCRIPTION", "ORG_ID", "LINE_TYPE_LOOKUP_CODE"),
              aliases=("distribution_set_lines", "dist_set_lines")),
]}

ATTACHMENTS_DIR = "attachments"


def table_for(file_stem: str, columns: list[str] | None = None) -> TableSpec | None:
    """The table a file or sheet holds, by its name; a generic name (weak alias) only when `columns`
    (normalised, after columns_alt) has every required column of the table."""
    key = file_stem.strip().lower().replace(" ", "_").replace("-", "_")
    for t in TABLES.values():
        if key == t.name.lower() or key in t.aliases:
            return t
    for t in TABLES.values():
        if key in t.weak_aliases and columns is not None and set(t.required) <= set(columns):
            return t
    return None
