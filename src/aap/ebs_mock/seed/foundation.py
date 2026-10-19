"""FND: currencies, lookups, users, concurrent programs."""

from __future__ import annotations

from datetime import datetime

from .context import SEED_USER_ID, SeedContext

START = datetime(2000, 1, 1)

# code, symbol, precision, territory, USD per unit (for the rate walk)
CURRENCIES = [
    ("USD", "$", 2, "US", 1.0),
    ("INR", "₹", 2, "IN", 0.01195),
    ("EUR", "€", 2, None, 1.09),
    ("GBP", "£", 2, "GB", 1.28),
    ("JPY", "¥", 0, "JP", 0.0068),
    ("CAD", "$", 2, "CA", 0.73),
    ("AUD", "$", 2, "AU", 0.66),
    ("SGD", "$", 2, "SG", 0.75),
    ("CHF", "CHF", 2, "CH", 1.13),
    ("AED", "AED", 2, "AE", 0.2723),
]

LOOKUPS: dict[str, list[tuple[str, str, str | None]]] = {
    "INVOICE TYPE": [
        ("STANDARD", "Standard", "Standard invoice"),
        ("CREDIT", "Credit Memo", "Credit memo"),
        ("DEBIT", "Debit Memo", "Debit memo"),
        ("PREPAYMENT", "Prepayment", "Prepayment"),
        ("MIXED", "Mixed", "Mixed invoice"),
    ],
    "SOURCE": [
        ("MANUAL INVOICE ENTRY", "Manual Invoice Entry", None),
        ("AAP", "ACSE Autonomous AP", "Invoices created by the AAP module"),
        ("ERS", "ERS", "Evaluated receipt settlement"),
        ("EDI GATEWAY", "EDI Gateway", None),
        ("XML GATEWAY", "XML Gateway", None),
    ],
    "HOLD CODE": [
        ("QTY REC", "Qty Rec", "Quantity billed exceeds quantity received"),
        ("QTY ORD", "Qty Ord", "Quantity billed exceeds quantity ordered"),
        ("PRICE", "Price", "Invoice price exceeds purchase order price"),
        ("AMOUNT", "Amount", "Invoice amount exceeds the hold limit"),
        ("MAX SHIP AMOUNT", "Max Ship Amount", "Shipment amount over tolerance"),
        ("DIST VARIANCE", "Dist Variance", "Distribution total does not equal line amount"),
        ("AWAITING RECEIPT", "Awaiting Receipt", "Held until goods are received"),
    ],
    "REJECT CODE": [
        ("DUPLICATE INVOICE NUMBER", "Duplicate Invoice Number", None),
        ("INVALID SUPPLIER", "Invalid Supplier", None),
        ("INVALID SUPPLIER SITE", "Invalid Supplier Site", None),
        ("INVALID PO NUM", "Invalid PO Number", None),
        ("INVALID PO LINE NUM", "Invalid PO Line Number", None),
        ("INVALID TERMS", "Invalid Terms", None),
        ("INCONSISTENT CURR", "Inconsistent Currency", None),
        ("INVALID CURRENCY CODE", "Invalid Currency Code", None),
        ("NO EXCHANGE RATE", "No Exchange Rate", None),
        ("INVALID DISTRIBUTION ACCT", "Invalid Distribution Account", None),
        # REJECT_LOOKUP_CODE is VARCHAR2(30); the long wording lives in the meaning.
        ("LINES AMOUNT MISMATCH", "Lines amount does not match invoice amount", None),
        ("ACCT DATE NOT IN OPEN PD", "Accounting Date Not In Open Period", None),
        ("INVALID INVOICE AMOUNT", "Invalid Invoice Amount", None),
        ("NO SUPPLIER", "No Supplier", None),
        ("INVALID OPERATING UNIT", "Invalid Operating Unit", None),
    ],
    "PAY GROUP": [
        ("STANDARD", "Standard", None),
        ("INTERNATIONAL", "International", None),
        ("SUBSCRIPTION", "Subscription", "Recurring subscription vendors"),
        ("EMPLOYEE", "Employee", None),
    ],
    "VENDOR TYPE": [
        ("VENDOR", "Supplier", None),
        ("CONTRACTOR", "Contractor", None),
        ("SUBSCRIPTION", "Subscription", None),
        ("EMPLOYEE", "Employee", None),
    ],
    "PAYMENT METHOD": [
        ("CHECK", "Check", None),
        ("EFT", "Electronic", None),
        ("WIRE", "Wire", None),
    ],
    "LINE TYPE": [
        ("ITEM", "Item", None),
        ("TAX", "Tax", None),
        ("FREIGHT", "Freight", None),
        ("MISCELLANEOUS", "Miscellaneous", None),
    ],
    "MATCH OPTION": [
        ("P", "Purchase Order", None),
        ("R", "Receipt", None),
    ],
}

USERS = [
    (0, "SYSADMIN", "System Administrator"),
    (SEED_USER_ID, "AAP_INTEGRATION", "ACSE Autonomous AP integration user"),
]

CONCURRENT_PROGRAMS = [
    (200, 10653, "APXIIMPT", "Payables Open Interface Import"),
    (200, 10708, "APPRVL", "Invoice Validation"),
    (201, 32263, "RVCTP", "Receiving Transaction Processor"),
]


def generate(ctx: SeedContext) -> None:
    for code, symbol, precision, territory, _ in CURRENCIES:
        ctx.add(
            "fnd.fnd_currencies",
            currency_code=code, enabled_flag="Y", currency_flag="Y",
            issuing_territory_code=territory, precision=precision,
            extended_precision=precision + 3, symbol=symbol, start_date_active=START,
        )

    for lookup_type, values in LOOKUPS.items():
        for code, meaning, description in values:
            ctx.add(
                "fnd.fnd_lookup_values",
                lookup_type=lookup_type, language="US", lookup_code=code, meaning=meaning,
                description=description, enabled_flag="Y", start_date_active=START,
                source_lang="US", security_group_id=0, view_application_id=200,
            )

    for user_id, name, desc in USERS:
        ctx.reserve("fnd.fnd_user_s", user_id)
        ctx.add("fnd.fnd_user", user_id=user_id, user_name=name, description=desc,
                start_date=START, email_address=None)

    clerks = []
    for _ in range(6):
        first, last = ctx.fake.first_name(), ctx.fake.last_name()
        user_id = ctx.next_id("fnd.fnd_user_s")
        name = f"{first[0]}{last}".upper()[:20]
        if any(c["user_name"] == name for c in clerks):
            name = f"{name}{user_id}"
        row = ctx.add("fnd.fnd_user", user_id=user_id, user_name=name,
                      description=f"AP Clerk {first} {last}", start_date=START,
                      email_address=f"{first}.{last}@acse.local".lower())
        clerks.append(row)
    ctx.ref["ap_clerk_user_ids"] = [c["user_id"] for c in clerks]

    for app_id, prog_id, name, user_name in CONCURRENT_PROGRAMS:
        ctx.add("fnd.fnd_concurrent_programs", application_id=app_id, concurrent_program_id=prog_id,
                concurrent_program_name=name, user_concurrent_program_name=user_name,
                enabled_flag="Y")

    ctx.ref["currencies"] = {c[0]: c for c in CURRENCIES}
