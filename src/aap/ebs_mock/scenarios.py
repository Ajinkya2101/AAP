"""Golden scenario fixtures S1-S15 (plan 3.5).

One source of truth shared by the seeder (master data, POs, receipts, history)
and the invoice generator (the invoices / emails that exercise each scenario).
All names, numbers and accounts are made up.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

D = Decimal


@dataclass(frozen=True)
class ScItem:
    key: str
    category: str          # MTL category code
    internal: str          # PO / item master wording
    vendor_text: str       # what the vendor prints
    price: Decimal
    uom: str = "Each"


@dataclass(frozen=True)
class ScSite:
    code: str
    line1: str
    city: str
    state: str | None
    zip: str
    email: str
    inactive_date: date | None = None


@dataclass(frozen=True)
class ScVendor:
    key: str
    name: str
    country: str
    kind: str                       # goods | services | subscription
    families: tuple[str, ...]
    tax_id: str
    domain: str
    sites: tuple[ScSite, ...]
    terms: str = "30 NET"
    tolerance: str = "Standard"
    vendor_id: int | None = None
    currency: str | None = None
    po_required: bool | None = None
    default_cc: str | None = None
    bank_accounts: tuple[str, ...] = ()
    tax_code: str | None = None


@dataclass(frozen=True)
class ScPoLine:
    item: str               # ScItem.key
    qty: Decimal
    price: Decimal
    ccid: tuple[str, str, str]      # company, cost centre, natural account
    received: Decimal = D(0)


@dataclass(frozen=True)
class ScPo:
    number: str
    vendor: str
    site: str
    po_date: date
    lines: tuple[ScPoLine, ...]
    receipt_date: date | None = None
    closed_code: str = "OPEN"


@dataclass(frozen=True)
class ScHistoryLine:
    description: str
    amount: Decimal
    account: str            # natural account; company/cost centre from the vendor


@dataclass(frozen=True)
class ScHistoryInvoice:
    vendor: str
    invoice_num: str
    invoice_date: date
    lines: tuple[ScHistoryLine, ...]
    po: str | None = None           # bill the whole PO (all received qty)


@dataclass(frozen=True)
class ScInvLine:
    text: str
    qty: Decimal | None
    price: Decimal | None
    amount: Decimal
    po_line: int | None = None


@dataclass(frozen=True)
class ScInvoice:
    """The invoice a scenario sends. Values are printed verbatim by the generator."""
    scenario: str
    vendor: str
    site: str
    invoice_num: str
    invoice_date_text: str
    invoice_date: date | None           # None when ambiguous on purpose (S9)
    lines: tuple[ScInvLine, ...]
    tax_label: str | None = None
    tax_amount: Decimal = D(0)
    po_number: str | None = None
    terms_text: str | None = "Net 30 days from date of invoice"
    currency_text: str = "$"
    number_style: str = "us"            # us | eu
    show_address: bool = True
    show_tax_id: bool = True
    bank_text: str | None = None
    sender: str | None = None           # email From; None = site email
    layout: str | None = None
    scanned: bool = False
    email_group: str | None = None      # invoices sharing a group go in one email (S12)
    expect_fail: tuple[str, ...] = ()
    expect_warn: tuple[str, ...] = ()
    expect_flags: tuple[str, ...] = ()
    # Agent layer (plan 8): final status, reason codes on the open review task, proposed holds.
    expect_status: str = "IN_REVIEW"
    expect_reasons: tuple[str, ...] = ()
    expect_holds: tuple[str, ...] = ()
    notes: str = ""

    @property
    def subtotal(self) -> Decimal:
        return sum((ln.amount for ln in self.lines), D(0))

    @property
    def total(self) -> Decimal:
        return self.subtotal + self.tax_amount


# --------------------------------------------------------------------- items
ITEMS: dict[str, ScItem] = {i.key: i for i in [
    ScItem("L14", "IT-HW-LAP-001", "LAPTOP, 14 IN, STANDARD ISSUE", 'Dell Latitude 5440 14" i7/16GB/512GB', D("1150.00")),
    ScItem("L16", "IT-HW-LAP-001", "LAPTOP, 16 IN, ENGINEERING", 'Dell Precision 5680 16" i9/32GB/1TB', D("1150.00")),
    ScItem("DOCK", "IT-HW-DCK-004", "DOCKING STATION, USB-C", "Dell Dock WD19S USB-C 130W", D("180.00")),
    ScItem("IMG", "IT-SVC-IMG-001", "DEVICE IMAGING SERVICE", "On-site imaging & setup", D("500.00")),
    ScItem("MON", "IT-HW-MON-003", "MONITOR, 24 IN", "Dell P2425H 24in Monitor", D("250.00")),
    ScItem("KBM", "IT-HW-PER-005", "KEYBOARD AND MOUSE SET, WIRELESS", "Logitech MK540 Keyboard/Mouse", D("110.00")),
    ScItem("PAPER", "OFF-SUP-PAP-001", "PAPER, COPY, LETTER, 10 REAMS", "Hammermill Copy Plus 8.5x11 (case)", D("46.50"), "Box"),
    ScItem("PENS", "OFF-SUP-WRT-002", "PENS, BALLPOINT, BLUE, BOX OF 12", "BIC Round Stic Blue (12pk)", D("4.20"), "Box"),
    ScItem("DRILL", "MRO-TLS-PWR-002", "POWER DRILL, CORDLESS 20V", "DeWalt DCD791D2 20V Drill Kit", D("150.00")),
    ScItem("VEST", "MRO-SAF-WRK-003", "SAFETY VEST, HI-VIS CLASS 2", "Ergodyne GloWear 8210 Vest", D("12.00")),
    ScItem("SW24", "IT-NET-SWT-001", "NETWORK SWITCH, 24 PORT POE", "Cisco Catalyst C9200L-24P-4G", D("2400.00")),
    ScItem("SENSOR", "LAB-EQP-INS-001", "SENSOR MODULE, PRECISION", "TSP-4410 Praezisionssensor", D("85.00")),
    ScItem("BEAKER", "LAB-CON-GLS-002", "BEAKER SET, BOROSILICATE", "Pyrex 1000 Beaker Set 6pc", D("85.00"), "Set"),
    ScItem("CHAIR", "OFF-FUR-CHR-001", "CHAIR, ERGONOMIC TASK", "Steelcase Series 1 Task Chair", D("500.00")),
    ScItem("GLOVES", "LAB-CON-PPE-004", "GLOVES, NITRILE, MEDIUM", "Kimtech Purple Nitrile Gloves M (100/bx)", D("18.00"), "Box"),
    ScItem("TIPS", "LAB-CON-GLS-002", "PIPETTE TIPS, 200UL", "Rainin LTS 200uL Tips (960/pk)", D("64.00"), "Pack"),
    ScItem("BROCH", "MKT-PRN-COL-002", "BROCHURE, PRINTED, TRI-FOLD", "Tri-fold brochure 8.5x11 100lb gloss", D("0.42")),
]}

# ------------------------------------------------------------------- vendors
NORTHWIND = "NORTHWIND"
VENDORS: dict[str, ScVendor] = {v.key: v for v in [
    ScVendor(NORTHWIND, "Northwind IT Supplies Inc", "US", "goods", ("IT-HW",), "84-1234567", "northwind.example",
             (ScSite("DALLAS-HQ", "1200 Commerce St", "Dallas", "TX", "75201", "billing@northwind.example"),
              ScSite("AUSTIN", "901 S Mopac Expy", "Austin", "TX", "78746", "billing@northwind.example"),
              ScSite("HOUSTON", "1400 Smith St", "Houston", "TX", "77002", "ar@northwind.example")),
             vendor_id=30418, tax_code="US SALES 8%"),
    ScVendor("AWS", "Amazon Web Services, Inc.", "US", "subscription", ("IT-CLD",), "91-2340187", "aws-billing.example",
             (ScSite("SEATTLE", "410 Terry Ave N", "Seattle", "WA", "98109", "aws-receivables@aws-billing.example"),),
             terms="IMMEDIATE", tolerance="Services", po_required=False, default_cc="510"),
    ScVendor("CONTOSO", "Contoso Office Products LLC", "US", "goods", ("OFF-SUP",), "36-4417205", "contoso-office.example",
             (ScSite("CHICAGO", "233 S Wacker Dr", "Chicago", "IL", "60606", "invoices@contoso-office.example"),),
             tax_code="US SALES 6%"),
    ScVendor("FABRIKAM", "Fabrikam Industrial Supply Co", "US", "goods", ("MRO-TLS", "MRO-SAF"), "75-3301947",
             "fabrikam-supply.example",
             (ScSite("FORT-WORTH", "3900 Airport Fwy", "Fort Worth", "TX", "76111", "billing@fabrikam-supply.example"),),
             tax_code="US SALES 8.25%"),
    ScVendor("LITWARE", "Litware Components Inc", "US", "goods", ("IT-NET",), "94-2201384", "litware.example",
             (ScSite("SAN-JOSE", "2100 N First St", "San Jose", "CA", "95131", "ar@litware.example"),),
             bank_accounts=("000123456789",), tax_code="US SALES 7.25%"),
    ScVendor("ADATUM", "Adatum Facilities Services", "US", "subscription", ("FAC-CLN",), "86-1055732", "adatum-fs.example",
             (ScSite("TEMPE", "60 E Rio Salado Pkwy", "Tempe", "AZ", "85281", "billing@adatum-fs.example"),
              ScSite("PHOENIX", "2 N Central Ave", "Phoenix", "AZ", "85004", "billing@adatum-fs.example",
                     inactive_date=date(2026, 3, 31))),
             terms="15 NET", tolerance="Services", po_required=False, default_cc="320"),
    ScVendor("TAILSPIN", "Tailspin Precision GmbH", "DE", "goods", ("LAB-EQP",), "DE287445193", "tailspin-precision.example",
             (ScSite("STUTTGART", "Heilbronner Str. 150", "Stuttgart", None, "70191", "rechnung@tailspin-precision.example"),),
             currency="EUR", terms="45 NET"),
    ScVendor("WIDEWORLD", "Wide World Consulting Group", "US", "subscription", ("PRO-CON",), "27-6620381", "wideworld-consulting.example",
             (ScSite("ATLANTA", "1180 Peachtree St NE", "Atlanta", "GA", "30309", "accounts@wideworld-consulting.example"),),
             tolerance="Services", po_required=False, default_cc="110"),
    ScVendor("PROSEWARE", "Proseware Lab Equipment Inc", "US", "goods", ("LAB-CON",), "04-3319274", "proseware-lab.example",
             (ScSite("BOSTON", "100 Cambridge St", "Boston", "MA", "02114", "ar@proseware-lab.example"),),
             tax_code="US SALES 6%"),
    ScVendor("COHO", "Coho Office Furniture Inc", "US", "goods", ("OFF-FUR",), "41-2287716", "coho-furniture.example",
             (ScSite("MINNEAPOLIS", "80 S 8th St", "Minneapolis", "MN", "55402", "billing@coho-furniture.example"),),
             tax_code="US SALES 8%"),
    ScVendor("TREY", "Trey Research Lab Supplies", "US", "goods", ("LAB-CON",), "33-1904452", "trey-research.example",
             (ScSite("SAN-DIEGO", "4225 Executive Sq", "San Diego", "CA", "92037", "invoices@trey-research.example"),),
             tax_code="US SALES 7.25%"),
    ScVendor("LUCERNE", "Lucerne Printing Services", "US", "goods", ("MKT-PRN",), "13-5561028", "lucerne-print.example",
             (ScSite("NEW-YORK", "575 8th Ave", "New York", "NY", "10018", "billing@lucerne-print.example"),),
             tax_code="US SALES 8.875%"),
]}

# ----------------------------------------------------------------------- POs
POS: dict[str, ScPo] = {p.number: p for p in [
    # S1: qty mismatch (WORKED-EXAMPLE 1). Line 1 received 8 of 10; line 4 same price as line 1.
    ScPo("4500123", NORTHWIND, "DALLAS-HQ", date(2026, 8, 20), (
        ScPoLine("L14", D(10), D("1150.00"), ("01", "000", "1650"), received=D(8)),
        ScPoLine("DOCK", D(10), D("180.00"), ("01", "510", "6410"), received=D(10)),
        ScPoLine("IMG", D(1), D("500.00"), ("01", "510", "6450"), received=D(1)),
        ScPoLine("L16", D(5), D("1150.00"), ("01", "000", "1650"), received=D(0)),
    ), receipt_date=date(2026, 9, 5)),
    # S3: the PO that fits the unnumbered invoice ...
    ScPo("4500187", NORTHWIND, "DALLAS-HQ", date(2026, 8, 25), (
        ScPoLine("MON", D(10), D("250.00"), ("01", "520", "6410"), received=D(10)),
        ScPoLine("KBM", D(10), D("110.00"), ("01", "520", "6410"), received=D(10)),
    ), receipt_date=date(2026, 9, 10)),
    # ... and a second open Northwind PO that also fits (ambiguous -> review).
    ScPo("4500188", NORTHWIND, "AUSTIN", date(2026, 9, 1), (
        ScPoLine("MON", D(20), D("250.00"), ("01", "520", "6410"), received=D(20)),
        ScPoLine("DOCK", D(5), D("180.00"), ("01", "520", "6410"), received=D(5)),
    ), receipt_date=date(2026, 9, 15)),
    # S4: fully billed by the existing invoice CT-88412.
    ScPo("4500130", "CONTOSO", "CHICAGO", date(2026, 7, 28), (
        ScPoLine("PAPER", D(20), D("46.50"), ("01", "110", "6210"), received=D(20)),
        ScPoLine("PENS", D(10), D("4.20"), ("01", "110", "6210"), received=D(10)),
    ), receipt_date=date(2026, 8, 4)),
    # S5: invoice price +4% vs 2% tolerance (Standard template).
    ScPo("4500145", "FABRIKAM", "FORT-WORTH", date(2026, 8, 12), (
        ScPoLine("DRILL", D(12), D("150.00"), ("01", "300", "6950"), received=D(12)),
        ScPoLine("VEST", D(50), D("12.00"), ("01", "300", "6940"), received=D(50)),
    ), receipt_date=date(2026, 8, 30)),
    # S6: all matches; only the bank account on the invoice is new.
    ScPo("4500156", "LITWARE", "SAN-JOSE", date(2026, 8, 15), (
        ScPoLine("SW24", D(2), D("2400.00"), ("01", "000", "1660"), received=D(2)),
    ), receipt_date=date(2026, 9, 2)),
    # S8: EUR PO in the US OU.
    ScPo("4500162", "TAILSPIN", "STUTTGART", date(2026, 8, 3), (
        ScPoLine("SENSOR", D(40), D("85.00"), ("01", "000", "1670"), received=D(40)),
    ), receipt_date=date(2026, 9, 8)),
    # S10: closed for invoicing.
    ScPo("4500171", "PROSEWARE", "BOSTON", date(2026, 7, 20), (
        ScPoLine("BEAKER", D(30), D("85.00"), ("01", "400", "6920"), received=D(30)),
    ), receipt_date=date(2026, 8, 6), closed_code="CLOSED FOR INVOICE"),
    # S11: tax maths error on the invoice.
    ScPo("4500176", "COHO", "MINNEAPOLIS", date(2026, 8, 18), (
        ScPoLine("CHAIR", D(10), D("500.00"), ("01", "000", "1610"), received=D(10)),
    ), receipt_date=date(2026, 9, 3)),
    # S12: two invoices (one per line) in one email.
    ScPo("4500181", "TREY", "SAN-DIEGO", date(2026, 8, 22), (
        ScPoLine("GLOVES", D(40), D("18.00"), ("01", "400", "6930"), received=D(40)),
        ScPoLine("TIPS", D(20), D("64.00"), ("01", "400", "6920"), received=D(20)),
    ), receipt_date=date(2026, 9, 4)),
    # S13: scanned invoice.
    ScPo("4500184", "LUCERNE", "NEW-YORK", date(2026, 8, 26), (
        ScPoLine("BROCH", D(5000), D("0.42"), ("01", "220", "6220"), received=D(5000)),
    ), receipt_date=date(2026, 9, 9)),
]}

# ----------------------------------------------------------- invoice history
_EC2 = "Amazon Elastic Compute Cloud - US East (N. Virginia) - Linux/UNIX On-Demand"
_AWS_S3_HISTORY = {
    2: ScHistoryLine("Amazon Simple Storage Service - US East", D("398.40"), "6430"),
    3: ScHistoryLine("AWS Data Transfer - US East", D("121.77"), "6420"),
    5: ScHistoryLine("Amazon S3 - Standard storage", D("377.15"), "6430"),
    8: ScHistoryLine("Amazon Simple Storage Service - EU (Ireland)", D("88.90"), "6420"),
    11: ScHistoryLine("Amazon S3 Glacier", D("41.23"), "6430"),
}


def _aws_history() -> list[ScHistoryInvoice]:
    out = []
    for m in range(1, 15):   # 14 past EC2 lines, all coded 6420
        y, mo = 2026, 9 - m
        while mo <= 0:
            mo += 12
            y -= 1
        lines = [ScHistoryLine(_EC2, D("2100.00") + D(m * 17) + D("0.33"), "6420")]
        if m in _AWS_S3_HISTORY:
            lines.append(_AWS_S3_HISTORY[m])
        out.append(ScHistoryInvoice("AWS", f"INV-US-{y}-88{m:02d}{1000 + m * 7}", date(y, mo, 1), tuple(lines)))
    return out


HISTORY: list[ScHistoryInvoice] = _aws_history() + [
    ScHistoryInvoice("CONTOSO", "CT-88412", date(2026, 8, 6), (), po="4500130"),
    ScHistoryInvoice("ADATUM", "ADT-2026-0801", date(2026, 8, 1),
                     (ScHistoryLine("Nightly janitorial service - August 2026", D("3200.00"), "6150"),)),
    ScHistoryInvoice("WIDEWORLD", "WW-0377", date(2026, 2, 27),
                     (ScHistoryLine("Consulting services - February", D("4800.00"), "6530"),)),
]

# ------------------------------------------------------------------ invoices
_NET30 = "Net 30 days from date of invoice"

INVOICES: dict[str, list[ScInvoice]] = {
    "S1": [ScInvoice("S1", NORTHWIND, "DALLAS-HQ", "NW-INV-20457", "12-Sep-2026", date(2026, 9, 12), (
        ScInvLine('Dell Latitude 5440 14" i7/16GB/512GB', D(10), D("1150.00"), D("11500.00"), 1),
        ScInvLine("Dell Dock WD19S USB-C 130W", D(10), D("180.00"), D("1800.00"), 2),
        ScInvLine("On-site imaging & setup", D(1), D("500.00"), D("500.00"), 3),
    ), tax_label="Sales tax 8%", tax_amount=D("1104.00"), po_number="4500123", terms_text=_NET30,
        expect_status="ON_HOLD", expect_reasons=("QTY_VARIANCE",), expect_holds=("QTY REC",),
        notes="Qty mismatch on line 1 (billed 10, received 8): validation passes, the 3-way match holds it.")],
    "S2": [ScInvoice("S2", "AWS", "SEATTLE", "INV-US-2026-8841327", "01 Sep 2026", date(2026, 9, 1), (
        ScInvLine("Amazon Elastic Compute Cloud - US East (N. Virginia) - Linux/UNIX On-Demand", None, None, D("2340.18")),
        ScInvLine("Amazon Simple Storage Service - US East (N. Virginia)", None, None, D("412.07")),
    ), terms_text="Due upon receipt", layout="cloud",
        expect_reasons=("GL_REVIEW",),
        notes="Non-PO vendor: EC2 line coded 6420 from history, S3 line chosen by the model -> review.")],
    "S3": [ScInvoice("S3", NORTHWIND, "DALLAS-HQ", "NW-INV-20511", "20-Sep-2026", date(2026, 9, 20), (
        ScInvLine("Dell P2425H 24in Monitor", D(10), D("250.00"), D("2500.00")),
        ScInvLine("Logitech MK540 Keyboard/Mouse", D(10), D("110.00"), D("1100.00")),
    ), tax_label=None, terms_text=_NET30, expect_warn=("V-PO",),
        expect_reasons=("PO_MISSING",),
        notes="No PO number from a PO_REQUIRED vendor; two open POs fit -> review (ask the vendor).")],
    "S4": [ScInvoice("S4", "CONTOSO", "CHICAGO", "CT-88412", "08/06/2026", date(2026, 8, 6), (
        ScInvLine("Hammermill Copy Plus 8.5x11 (case)", D(20), D("46.50"), D("930.00"), 1),
        ScInvLine("BIC Round Stic Blue (12pk)", D(10), D("4.20"), D("42.00"), 2),
    ), tax_label="Sales Tax 6%", tax_amount=D("58.32"), po_number="4500130", terms_text="Net 30",
        expect_fail=("C10",), expect_reasons=("DUPLICATE",),
        notes="Resend of an invoice already in AP_INVOICES_ALL.")],
    "S5": [ScInvoice("S5", "FABRIKAM", "FORT-WORTH", "FAB-77120", "September 14, 2026", date(2026, 9, 14), (
        ScInvLine("DeWalt DCD791D2 20V Drill Kit", D(12), D("156.00"), D("1872.00"), 1),
        ScInvLine("Ergodyne GloWear 8210 Vest", D(50), D("12.00"), D("600.00"), 2),
    ), tax_label="Sales Tax 8.25%", tax_amount=D("203.94"), po_number="4500145", terms_text="Net 30",
        expect_status="ON_HOLD", expect_reasons=("PRICE_VARIANCE",), expect_holds=("PRICE",),
        notes="Unit price 156.00 vs PO 150.00 (+4%, tolerance 2%): PRICE hold.")],
    "S6": [ScInvoice("S6", "LITWARE", "SAN-JOSE", "LW-3391", "2026-09-11", date(2026, 9, 11), (
        ScInvLine("Cisco Catalyst C9200L-24P-4G", D(2), D("2400.00"), D("4800.00"), 1),
    ), tax_label="Sales tax 7.25%", tax_amount=D("348.00"), po_number="4500156", terms_text=_NET30,
        bank_text="Please remit to our NEW bank account: Account No. 987654321000, Routing 111000025",
        expect_fail=("C11",), expect_reasons=("BANK_CHANGE",),
        notes="Bank account differs from IBY_EXT_BANK_ACCOUNTS: fraud flag.")],
    "S7": [ScInvoice("S7", "ADATUM", "PHOENIX", "ADT-2026-0901", "09/01/2026", date(2026, 9, 1), (
        ScInvLine("Nightly janitorial service - September 2026", None, None, D("3200.00")),
    ), terms_text="Net 15", expect_fail=("C8",), expect_reasons=("SUPPLIER_INACTIVE",),
        notes="Printed site (PHOENIX) is inactive since 2026-03-31.")],
    "S8": [ScInvoice("S8", "TAILSPIN", "STUTTGART", "TSP-2026-0917", "17.09.2026", date(2026, 9, 17), (
        ScInvLine("TSP-4410 Praezisionssensor", D(40), D("85.00"), D("3400.00"), 1),
    ), po_number="4500162", terms_text="45 Tage netto", currency_text="EUR", number_style="eu",
        tax_label=None, notes="EUR invoice to the US OU; reverse charge (no tax line).")],
    "S9": [ScInvoice("S9", "WIDEWORLD", "ATLANTA", "WW-0412", "03/04/2026", None, (
        ScInvLine("Consulting services - March", None, None, D("4800.00")),
    ), terms_text="Net 30", currency_text="USD", show_address=False, show_tax_id=False,
        expect_warn=("C9",), expect_flags=("two_date_formats",), expect_reasons=("DATE_AMBIGUOUS",),
        notes="Day/month order cannot be decided; no country hint on the invoice.")],
    "S10": [ScInvoice("S10", "PROSEWARE", "BOSTON", "PLE-10233", "Sep 10, 2026", date(2026, 9, 10), (
        ScInvLine("Pyrex 1000 Beaker Set 6pc", D(30), D("85.00"), D("2550.00"), 1),
    ), tax_label="Sales tax 6%", tax_amount=D("153.00"), po_number="4500171", terms_text=_NET30,
        expect_fail=("V-PO",), expect_reasons=("PO_NOT_OPEN",), notes="PO is CLOSED FOR INVOICE.")],
    "S11": [ScInvoice("S11", "COHO", "MINNEAPOLIS", "COHO-5520", "09/15/2026", date(2026, 9, 15), (
        ScInvLine("Steelcase Series 1 Task Chair", D(10), D("500.00"), D("5000.00"), 1),
    ), tax_label="Sales tax 8%", tax_amount=D("450.00"), po_number="4500176", terms_text=_NET30,
        expect_fail=("C4",), expect_reasons=("INVOICE_MATH",),
        notes="Tax 450.00 but 5,000.00 x 8% = 400.00 -> ask the vendor for a corrected invoice.")],
    "S12": [
        ScInvoice("S12", "TREY", "SAN-DIEGO", "TR-5501", "09/16/2026", date(2026, 9, 16), (
            ScInvLine("Kimtech Purple Nitrile Gloves M (100/bx)", D(40), D("18.00"), D("720.00"), 1),
        ), tax_label="Sales Tax 7.25%", tax_amount=D("52.20"), po_number="4500181", terms_text="Net 30",
            email_group="S12", expect_flags=("multiple_invoices",)),
        ScInvoice("S12", "TREY", "SAN-DIEGO", "TR-5502", "09/16/2026", date(2026, 9, 16), (
            ScInvLine("Rainin LTS 200uL Tips (960/pk)", D(20), D("64.00"), D("1280.00"), 2),
        ), tax_label="Sales Tax 7.25%", tax_amount=D("92.80"), po_number="4500181", terms_text="Net 30",
            email_group="S12", expect_flags=("multiple_invoices",)),
    ],
    "S13": [ScInvoice("S13", "LUCERNE", "NEW-YORK", "LPS-88317", "Sept 12, 2026", date(2026, 9, 12), (
        ScInvLine("Tri-fold brochure 8.5x11 100lb gloss", D(5000), D("0.42"), D("2100.00"), 1),
    ), tax_label="Sales Tax 8.875%", tax_amount=D("186.38"), po_number="4500184", terms_text="Net 30",
        scanned=True, notes="Image-only PDF (route image_only).")],
    "S15": [ScInvoice("S15", NORTHWIND, "DALLAS-HQ", "NW-INV-20533", "22-Sep-2026", date(2026, 9, 22), (
        ScInvLine("Dell Dock WD19S USB-C 130W", D(5), D("180.00"), D("900.00")),
    ), tax_label="Sales tax 8%", tax_amount=D("72.00"), po_number="4500188", terms_text=_NET30,
        sender="accounts@northwind-billing.example",
        bank_text="Bank transfer to: Account 55012093311, Routing 026009593",
        expect_fail=("C11",), expect_flags=("unknown_sender",), expect_reasons=("BANK_CHANGE", "UNKNOWN_SENDER"),
        notes="Look-alike sender domain and a different bank account.")],
}

# S14 is an out-of-office auto-reply saved as an .eml: not an invoice.
S14_AUTO_REPLY = dict(
    sender="billing@northwind.example",
    subject="Automatic reply: Invoice NW-INV-20511 - PO number needed [AP-REF:{ref}]",
    body="I am out of the office until 5 October 2026 with limited access to email.\n"
         "For urgent billing questions please call +1 214 555 0100.",
)


def all_invoices() -> list[ScInvoice]:
    return [inv for invs in INVOICES.values() for inv in invs]
