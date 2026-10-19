"""GL: chart of accounts (4 segments, IP-02), code combinations, periods, daily rates."""

from __future__ import annotations

import calendar
import math
from datetime import date, datetime, timedelta
from decimal import Decimal

from .context import SeedContext
from .org import COA_ID

COMPANIES = [
    ("01", "ACSE Corp (US)"),
    ("02", "ACSE Services LLC (US)"),
    ("11", "ACSE India Pvt Ltd"),
]
COMPANY_BY_ORG = {204: ["01", "02"], 458: ["11"]}

COST_CENTRES = [
    ("000", "No cost centre"), ("100", "Executive office"), ("110", "Finance"),
    ("115", "Accounts payable"), ("120", "Human resources"), ("130", "Legal"),
    ("140", "Procurement"), ("200", "Sales - Americas"), ("210", "Sales - APAC"),
    ("220", "Marketing"), ("230", "Customer success"), ("300", "Operations"),
    ("310", "Warehouse and logistics"), ("320", "Facilities"), ("330", "Quality assurance"),
    ("400", "Research and development"), ("410", "Product engineering"), ("420", "Data science"),
    ("500", "IT - Management"), ("510", "IT - Cloud and infrastructure"), ("520", "IT - End user services"),
    ("530", "IT - Security"), ("540", "IT - Applications"), ("600", "Training"), ("700", "Corporate projects"),
]

# natural account, description (1xxx A, 2xxx L, 3xxx O, 4xxx R, 5xxx-7xxx E)
ACCOUNTS = [
    ("1010", "Cash - operating"), ("1020", "Cash - payroll"), ("1030", "Petty cash"),
    ("1110", "Short-term investments"), ("1210", "Trade receivables"), ("1220", "Other receivables"),
    ("1310", "Inventory - raw materials"), ("1320", "Inventory - finished goods"),
    ("1410", "Prepaid expenses"), ("1420", "Prepaid software subscriptions"),
    ("1450", "Input tax recoverable (GST)"), ("1460", "Input tax recoverable (VAT)"),
    ("1510", "Land"), ("1520", "Buildings"), ("1530", "Leasehold improvements"),
    ("1610", "Furniture and fixtures"), ("1620", "Office equipment"), ("1650", "Computer equipment"),
    ("1660", "Network equipment"), ("1670", "Lab equipment"), ("1680", "Vehicles"),
    ("1710", "Accumulated depreciation - buildings"), ("1750", "Accumulated depreciation - computers"),
    ("1810", "Capitalised software"), ("1820", "Goodwill"), ("1910", "Security deposits"),
    ("2110", "Trade payables - control"), ("2210", "Accounts payable liability"),
    ("2220", "Accrued liabilities"), ("2230", "Receipt accrual (AP accrual)"),
    ("2310", "Sales tax payable"), ("2320", "GST payable"), ("2330", "TDS payable"),
    ("2410", "Payroll liabilities"), ("2510", "Deferred revenue"), ("2610", "Short-term borrowings"),
    ("2710", "Long-term debt"), ("2810", "Lease liabilities"),
    ("3100", "Share capital"), ("3200", "Retained earnings"), ("3300", "Other reserves"),
    ("4010", "Product revenue"), ("4020", "Services revenue"), ("4030", "Subscription revenue"),
    ("4110", "Interest income"), ("4210", "Foreign exchange gain"),
    ("5010", "Cost of goods sold"), ("5020", "Freight in"), ("5030", "Purchase price variance"),
    ("5040", "Invoice price variance"), ("5050", "Exchange rate variance"),
    ("5110", "Direct labour"), ("5210", "Subcontractor costs"),
    ("6010", "Salaries and wages"), ("6020", "Bonuses"), ("6030", "Employee benefits"),
    ("6040", "Payroll taxes"), ("6050", "Contractors and temporary staff"),
    ("6110", "Rent"), ("6120", "Utilities - electricity"), ("6130", "Utilities - water"),
    ("6140", "Building maintenance"), ("6150", "Cleaning and janitorial"), ("6160", "Security services"),
    ("6170", "Waste disposal"), ("6210", "Office supplies"), ("6220", "Printing and stationery"),
    ("6230", "Postage and courier"), ("6240", "Kitchen and pantry supplies"),
    ("6310", "Travel - airfare"), ("6320", "Travel - hotels"), ("6330", "Travel - meals"),
    ("6340", "Travel - ground transport"), ("6350", "Mileage reimbursement"),
    ("6410", "Computer accessories"), ("6420", "Cloud compute and networking"),
    ("6430", "Data storage and backup"), ("6440", "Software subscriptions (SaaS)"),
    ("6450", "IT services"), ("6460", "Telecommunications"), ("6470", "Internet and connectivity"),
    ("6480", "Software maintenance and support"), ("6490", "Hardware maintenance"),
    ("6510", "Legal fees"), ("6520", "Audit fees"), ("6530", "Consulting fees"),
    ("6540", "Recruitment fees"), ("6550", "Bank charges"),
    ("6610", "Advertising"), ("6620", "Trade shows and events"), ("6630", "Marketing agencies"),
    ("6640", "Sponsorships"), ("6710", "Training and education"), ("6720", "Memberships and subscriptions"),
    ("6730", "Books and publications"), ("6810", "Insurance - property"), ("6820", "Insurance - liability"),
    ("6910", "Repairs and maintenance - equipment"), ("6920", "Lab consumables"),
    ("6930", "Safety equipment"), ("6940", "Uniforms and workwear"), ("6950", "Tools and small equipment"),
    ("7010", "Depreciation expense"), ("7020", "Amortisation expense"), ("7110", "Interest expense"),
    ("7210", "Foreign exchange loss"), ("7310", "Bad debt expense"), ("7410", "Donations"),
    ("7510", "Licences and permits"), ("7520", "Property taxes"), ("7610", "Freight out"),
    ("7620", "Customs and duties"), ("7710", "Vehicle fuel"), ("7720", "Vehicle maintenance"),
    ("7810", "Entertainment"), ("7820", "Gifts"), ("7910", "Miscellaneous expense"),
    ("7920", "Rounding differences"), ("7930", "Suspense"),
]

SUBACCOUNTS = [("0000", "No sub-account"), ("0001", "Project A"), ("0002", "Project B"), ("0003", "Capex")]

# Combinations that golden scenarios and core postings rely on: never disabled.
PROTECTED_ACCOUNTS = {"2210", "2230", "1450", "1460", "5040", "5050", "6420", "6430", "6410", "6450", "1650"}


def account_type(acct: str) -> str:
    return {"1": "A", "2": "L", "3": "O", "4": "R"}.get(acct[0], "E")


def _value_set(ctx: SeedContext, name: str, size: int, values: list[tuple[str, str]], attrs=None) -> None:
    vs_id = ctx.next_id("fnd.fnd_flex_value_sets_s")
    ctx.add("fnd.fnd_flex_value_sets", flex_value_set_id=vs_id, flex_value_set_name=name,
            validation_type="I", format_type="C", maximum_size=size, description=name)
    for value, desc in values:
        fv_id = ctx.next_id("fnd.fnd_flex_values_s")
        ctx.add("fnd.fnd_flex_values", flex_value_id=fv_id, flex_value_set_id=vs_id,
                flex_value=value, enabled_flag="Y", summary_flag="N",
                compiled_value_attributes=attrs(value) if attrs else "Y\nY")
        ctx.add("fnd.fnd_flex_values_tl", flex_value_id=fv_id, language="US", source_lang="US",
                description=desc, flex_value_meaning=value)


def generate(ctx: SeedContext) -> None:
    _value_set(ctx, "ACSE_COMPANY", 2, COMPANIES)
    _value_set(ctx, "ACSE_COST_CENTRE", 3, COST_CENTRES)
    _value_set(ctx, "ACSE_ACCOUNT", 4, ACCOUNTS, attrs=lambda v: f"Y\nY\n{account_type(v)}")
    _value_set(ctx, "ACSE_SUBACCOUNT", 4, SUBACCOUNTS)

    ctx.ref["ccids"] = {}          # (co, cc, acct, sub) -> ccid
    ctx.ref["ccid_rows"] = {}      # ccid -> row
    ctx.ref["account_desc"] = dict(ACCOUNTS)
    ctx.ref["cost_centre_desc"] = dict(COST_CENTRES)

    balance = [a for a, _ in ACCOUNTS if a[0] in "123"]
    expense = [a for a, _ in ACCOUNTS if a[0] in "4567"]
    for co, _ in COMPANIES:
        for acct in balance:
            ensure_ccid(ctx, co, "000", acct)
        for cc, _ in COST_CENTRES[1:]:
            for acct in ctx.rnd.sample(expense, 6):
                ensure_ccid(ctx, co, cc, acct)
        for acct in ("5030", "5040", "5050", "7210", "7920"):
            ensure_ccid(ctx, co, "000", acct)

    # ~3% disabled or end-dated (never the protected ones).
    for row in ctx.ref["ccid_rows"].values():
        if row["segment3"] in PROTECTED_ACCOUNTS or row["segment2"] == "000" and row["segment3"][0] in "12":
            continue
        if ctx.chance(0.03):
            if ctx.chance(0.5):
                row["enabled_flag"] = "N"
            else:
                row["end_date_active"] = ctx.ts(ctx.days_ago(ctx.rnd.randint(30, 400)))

    _periods(ctx)
    _rates(ctx)


def ensure_ccid(ctx: SeedContext, co: str, cc: str, acct: str, sub: str = "0000") -> int:
    key = (co, cc, acct, sub)
    ccid = ctx.ref["ccids"].get(key)
    if ccid:
        return ccid
    ccid = ctx.next_id("gl.gl_code_combinations_s")
    row = ctx.add("gl.gl_code_combinations", code_combination_id=ccid, chart_of_accounts_id=COA_ID,
                  account_type=account_type(acct), enabled_flag="Y", summary_flag="N",
                  detail_posting_allowed_flag="Y", detail_budgeting_allowed_flag="Y",
                  start_date_active=datetime(2015, 1, 1),
                  segment1=co, segment2=cc, segment3=acct, segment4=sub)
    ctx.ref["ccids"][key] = ccid
    ctx.ref["ccid_rows"][ccid] = row
    return ccid


def ccid_usable(ctx: SeedContext, ccid: int) -> bool:
    row = ctx.ref["ccid_rows"][ccid]
    return row["enabled_flag"] == "Y" and row.get("end_date_active") is None


def ensure_usable_ccid(ctx: SeedContext, co: str, cc: str, acct: str, sub: str = "0000") -> int:
    """Like ensure_ccid, but re-enables a combination that was randomly disabled."""
    ccid = ensure_ccid(ctx, co, cc, acct, sub)
    row = ctx.ref["ccid_rows"][ccid]
    row["enabled_flag"] = "Y"
    row.pop("end_date_active", None)
    return ccid


def period_name(d: date) -> str:
    return f"{calendar.month_abbr[d.month].upper()}-{d.year % 100:02d}"


def _periods(ctx: SeedContext) -> None:
    anchor_month = date(ctx.anchor.year, ctx.anchor.month, 1)
    prev_month = (anchor_month - timedelta(days=1)).replace(day=1)
    statuses: dict[str, str] = {}
    for ledger_id in (2021, 2022):
        d = date(2024, 10, 1)
        while d <= date(2026, 12, 1):
            last = calendar.monthrange(d.year, d.month)[1]
            if d in (anchor_month, prev_month):
                status = "O"
            elif d < prev_month:
                status = "C"
            elif (d.year, d.month) == ((anchor_month + timedelta(days=32)).year, (anchor_month + timedelta(days=32)).month):
                status = "F"
            else:
                status = "N"
            name = period_name(d)
            statuses[name] = status
            for app_id in (200, 101):
                ctx.add("gl.gl_period_statuses", application_id=app_id, ledger_id=ledger_id,
                        set_of_books_id=ledger_id, period_name=name, closing_status=status,
                        start_date=ctx.ts(d), end_date=ctx.ts(date(d.year, d.month, last)),
                        period_type="Month", period_year=d.year, period_num=d.month,
                        quarter_num=(d.month - 1) // 3 + 1, adjustment_period_flag="N",
                        effective_period_num=d.year * 10000 + d.month)
            d = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    ctx.ref["period_status"] = statuses


def _rates(ctx: SeedContext) -> None:
    """Daily Corporate rates: random walk around real levels, both directions,
    against the two functional currencies (USD, INR)."""
    currencies = ctx.ref["currencies"]
    days = ctx.scale.rate_days
    start = ctx.anchor - timedelta(days=days)
    end = ctx.anchor + timedelta(days=7)
    levels = {code: c[4] for code, c in currencies.items()}  # USD per unit
    walk = dict(levels)
    d = start
    rows = ctx.rows["gl.gl_daily_rates"]
    while d <= end:
        for code in walk:
            if code in ("USD", "AED"):
                continue
            walk[code] *= math.exp(ctx.rnd.gauss(0, 0.004))
            walk[code] = min(max(walk[code], levels[code] * 0.85), levels[code] * 1.15)
        ts = ctx.ts(d)
        for functional in ("USD", "INR"):
            for code in walk:
                if code == functional:
                    continue
                rate = walk[code] / walk[functional]  # functional units per 1 foreign unit
                for frm, to, r in ((code, functional, rate), (functional, code, 1 / rate)):
                    rows.append(dict(from_currency=frm, to_currency=to, conversion_date=ts,
                                     conversion_type="Corporate",
                                     conversion_rate=Decimal(f"{r:.8g}"), status_code="C"))
        d += timedelta(days=1)
    # USD<->INR appear twice (once per functional loop); keep the first of each key.
    seen: set[tuple] = set()
    unique = []
    for r in rows:
        key = (r["from_currency"], r["to_currency"], r["conversion_date"], r["conversion_type"])
        if key not in seen:
            seen.add(key)
            unique.append(r)
    ctx.rows["gl.gl_daily_rates"] = unique
