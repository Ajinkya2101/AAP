"""Payables lookups the Oracle-ready JSON's codes come from (FND_LOOKUP_VALUES, view application 200).

The codes are Oracle's seeded ones; an instance can disable some or add its own, so the instance's
list (refsync -> ref_lookup) decides what is valid, and validation rule V-LOV checks every invoice
against it. The meanings below are what the Invoice Workbench shows; a list delivered as meanings
("Credit Memo", "Freight") is turned back into codes with them (clientdata/lov.py).
"""

from __future__ import annotations

from aap.common.text import text_key

INVOICE_TYPE = "INVOICE TYPE"
LINE_TYPE = "INVOICE LINE TYPE"
SOURCE = "SOURCE"
# The lookup types the reference sync copies and V-LOV checks.
CHECKED_TYPES = (INVOICE_TYPE, LINE_TYPE, SOURCE)

# Oracle EBS R12 seeded values: code, Workbench meaning, description.
ORACLE_SEEDED: dict[str, list[tuple[str, str, str | None]]] = {
    INVOICE_TYPE: [
        ("STANDARD", "Standard", "Standard invoice"),
        ("CREDIT", "Credit Memo", "Credit memo"),
        ("DEBIT", "Debit Memo", "Debit memo"),
        ("PREPAYMENT", "Prepayment", "Prepayment"),
        ("MIXED", "Mixed", "Mixed invoice"),
        ("EXPENSE REPORT", "Expense Report", "Employee expense report"),
        ("RETAINAGE RELEASE", "Retainage Release", "Retainage release"),
        ("AWT", "Withholding Tax", "Withholding tax invoice"),
        ("PAYMENT REQUEST", "Payment Request", "Payment request"),
    ],
    LINE_TYPE: [
        ("ITEM", "Item", "Goods or services"),
        ("FREIGHT", "Freight", "Freight charge"),
        ("MISCELLANEOUS", "Miscellaneous", "Miscellaneous charge"),
        ("TAX", "Tax", "Tax"),
        ("AWT", "Withholding Tax", "Withholding tax"),
        ("PREPAY", "Prepayment", "Prepayment application"),
    ],
}

# Other spellings seen in delivered lists, by lookup type (text_key form).
ALIASES: dict[str, dict[str, str]] = {
    INVOICE_TYPE: {"credit": "CREDIT", "creditmemo": "CREDIT", "debitmemo": "DEBIT", "withholdingtax": "AWT",
                   "withholdingtaxinvoice": "AWT", "employeeexpensereport": "EXPENSE REPORT",
                   "mixedtypeinvoice": "MIXED", "retainagereleaseinvoices": "RETAINAGE RELEASE",
                   "prepaymentsadvancesandfinances": "PREPAYMENT", "standardinvoice": "STANDARD"},
    LINE_TYPE: {"withholdingtax": "AWT", "prepayment": "PREPAY", "misc": "MISCELLANEOUS"},
}


def resolve_code(lookup_type: str, value: str) -> tuple[str, bool]:
    """(code, known): the seeded code for a code, meaning or known alias; otherwise the value in upper
    case (at most 30 characters, the column size) and known=False, for someone to confirm."""
    key = text_key(value).replace(" ", "")
    for code, meaning, description in ORACLE_SEEDED.get(lookup_type, []):
        if key in {text_key(code).replace(" ", ""), text_key(meaning).replace(" ", ""),
                   text_key(description or "").replace(" ", "")}:
            return code, True
    alias = ALIASES.get(lookup_type, {}).get(key)
    if alias:
        return alias, True
    return " ".join(str(value).split()).upper()[:30], False
