"""Suppliers (AP_SUPPLIERS + HZ_PARTIES), sites and bank accounts."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from aap.common.taxid import make_ein, make_gstin, make_iban, make_vat

from .context import SeedContext
from .gl import COMPANY_BY_ORG, ensure_usable_ccid

START = datetime(2016, 1, 1)

# Families a supplier can specialise in, by kind. Subscription families bill monthly without a PO.
GOODS_FAMILIES = ["IT-HW", "IT-NET", "IT-SRV", "OFF-SUP", "OFF-FUR", "OFF-PAN", "FAC-CLN", "LAB-EQP",
                  "LAB-CON", "MRO-TLS", "MRO-SAF", "MKT-PRN", "LOG-PKG"]
PO_SERVICE_FAMILIES = ["IT-SVC", "IT-SW", "FAC-MNT", "PRO-CON", "PRO-LEG", "PRO-AUD", "PRO-REC",
                       "PRO-TRN", "MKT-EVT", "LOG-FRT"]
SUBSCRIPTION_FAMILIES = ["IT-CLD", "IT-TEL", "IT-SW", "FAC-UTL", "FAC-SEC", "FAC-WST", "FAC-CLN", "MKT-AGY", "IT-SVC"]
DEFAULT_CC_BY_FAMILY = {"IT-CLD": "510", "IT-TEL": "520", "IT-SW": "540", "FAC-UTL": "320",
                        "FAC-SEC": "320", "FAC-WST": "320", "FAC-CLN": "320", "MKT-AGY": "220", "IT-SVC": "520"}

BANKS = [
    ("First Meridian Bank", "US"), ("Lone Star Commerce Bank", "US"), ("Pacific Crest Bank", "US"),
    ("Bharat Mercantile Bank", "IN"), ("Deccan Co-operative Bank", "IN"),
    ("Rheinland Handelsbank", "DE"), ("Thames Commercial Bank", "GB"), ("Amstel Zakenbank", "NL"),
]

INVOICE_NUM_PATTERNS = ["{p}-INV-{n:05d}", "INV-{y}-{n:05d}", "{n:07d}", "{p}{n:06d}", "{y2}{m:02d}-{n:04d}",
                        "{p}/{y2}-{y3}/{n:04d}"]

US_STATES = ["TX", "CA", "NY", "IL", "WA", "GA", "NC", "MA", "CO", "AZ", "FL", "OH", "MN", "OR"]


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    for suffix in ("-inc", "-llc", "-ltd", "-pvt-ltd", "-gmbh", "-co", "-plc", "-group", "-bv"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
    return s[:40].strip("-")


def initials(name: str) -> str:
    words = [w for w in re.split(r"[^A-Za-z]+", name) if w and w.lower() not in ("and", "the", "of")]
    return ("".join(w[0] for w in words[:3]) or "SUP").upper()


def _bank_parties(ctx: SeedContext) -> None:
    ctx.ref["banks"] = {}
    for name, country in BANKS:
        party_id = ctx.next_id("hz.hz_parties_s")
        ctx.add("hz.hz_parties", party_id=party_id, party_number=str(party_id), party_name=name,
                party_type="ORGANIZATION", status="A", country=country, category_code="BANK")
        ctx.ref["banks"].setdefault(country, []).append(party_id)


def create_supplier(
    ctx: SeedContext,
    *,
    name: str,
    country: str,
    kind: str,                   # goods | services | subscription
    families: list[str],
    tax_id: str | None = None,
    vendor_id: int | None = None,
    currency: str | None = None,
    terms: str | None = None,
    tolerance: str | None = None,
    po_required: bool | None = None,
    default_cc: str | None = None,
    domain: str | None = None,
    sites: list[dict[str, Any]] | None = None,
    bank_accounts: list[str] | None = None,
    invoice_pattern: str | None = None,
    alt_name: str | None = None,
) -> dict[str, Any]:
    rnd = ctx.rnd
    if vendor_id is None:
        vendor_id = ctx.next_id("ap.ap_suppliers_s", step=rnd.randint(1, 3))
    else:
        ctx.reserve("ap.ap_suppliers_s", vendor_id)
    ctx.ref.setdefault("supplier_num", 1000)
    ctx.ref["supplier_num"] += 1
    domain = domain or f"{slug(name)}.example"
    org_ids = [458] if country == "IN" else [204]
    currency = currency or {"US": "USD", "IN": "INR", "DE": "EUR", "NL": "EUR", "IE": "EUR", "GB": "GBP"}[country]
    if tax_id is None:
        tax_id = {"US": lambda: make_ein(rnd), "IN": lambda: make_gstin(rnd, name)}.get(
            country, lambda: make_vat(rnd, country))()
    if po_required is None:
        po_required = kind != "subscription"
    terms = terms or ("IMMEDIATE" if kind == "subscription" and rnd.random() < 0.5 else
                      rnd.choice(["30 NET", "30 NET", "45 NET", "60 NET", "15 NET", "2/10 NET 30", "NET 30 EOM", "7 NET"]))
    tolerance = tolerance or ("Services" if kind != "goods" else ("Strict" if rnd.random() < 0.1 else "Standard"))
    if default_cc is None and kind == "subscription":
        default_cc = DEFAULT_CC_BY_FAMILY.get(families[0], "510")

    party_id = ctx.next_id("hz.hz_parties_s")
    ctx.add("hz.hz_parties", party_id=party_id, party_number=str(party_id), party_name=name,
            party_type="ORGANIZATION", jgzz_fiscal_code=tax_id, status="A", country=country,
            email_address=f"billing@{domain}", category_code="SUPPLIER")
    vendor_type = {"subscription": "SUBSCRIPTION", "services": "CONTRACTOR"}.get(kind, "VENDOR")
    ctx.add(
        "ap.ap_suppliers",
        vendor_id=vendor_id, vendor_name=name, vendor_name_alt=alt_name,
        segment1=str(ctx.ref["supplier_num"]), party_id=party_id,
        vendor_type_lookup_code=vendor_type, enabled_flag="Y", summary_flag="N", one_time_flag="N",
        num_1099=tax_id if country == "US" else None,
        vat_registration_num=tax_id if country != "US" else None,
        terms_id=ctx.ref["terms"][terms], terms_date_basis="Invoice",
        pay_group_lookup_code="SUBSCRIPTION" if kind == "subscription" else (
            "INTERNATIONAL" if country not in ("US", "IN") else "STANDARD"),
        invoice_currency_code=currency, payment_currency_code=currency,
        payment_method_lookup_code="EFT" if country != "US" or rnd.random() < 0.7 else "CHECK",
        match_option="P", receipt_required_flag="Y" if kind == "goods" else "N",
        inspection_required_flag="N", hold_all_payments_flag="N", hold_future_payments_flag="N",
        start_date_active=START,
        attribute_category="ACSE", attribute1="Y" if po_required else "N", attribute2=default_cc,
    )

    supplier: dict[str, Any] = dict(
        vendor_id=vendor_id, name=name, country=country, kind=kind, families=families,
        tax_id=tax_id, currency=currency, terms=terms, tolerance=tolerance, po_required=po_required,
        default_cc=default_cc, domain=domain, party_id=party_id, org_ids=org_ids, sites=[],
        banks=[], prefix=initials(name), invoice_pattern=invoice_pattern or rnd.choice(INVOICE_NUM_PATTERNS),
        invoice_counter=rnd.randint(1000, 20000),
    )

    if sites is None:
        n_sites = rnd.choices([1, 2, 3, 4], weights=[45, 30, 17, 8])[0]
        sites = [_random_site(ctx, country, i) for i in range(n_sites)]
    used_codes: set[str] = set()
    for i, s in enumerate(sites):
        org_id = s.get("org_id", org_ids[0])
        code = s["code"]
        while code in used_codes:
            code = f"{code[:12]}-{len(used_codes)}"
        used_codes.add(code)
        site_id = s.get("vendor_site_id") or ctx.next_id("ap.ap_supplier_sites_s")
        if s.get("vendor_site_id"):
            ctx.reserve("ap.ap_supplier_sites_s", site_id)
        company = COMPANY_BY_ORG[org_id][0]
        liability = ensure_usable_ccid(ctx, company, "000", "2210")
        prepay = ensure_usable_ccid(ctx, company, "000", "1410")
        email = s.get("email", f"{rnd.choice(['billing', 'ar', 'invoices', 'accounts'])}@{domain}")
        row = ctx.add(
            "ap.ap_supplier_sites_all",
            vendor_site_id=site_id, vendor_id=vendor_id, vendor_site_code=code, org_id=org_id,
            purchasing_site_flag=s.get("purchasing", "Y"), pay_site_flag="Y",
            primary_pay_site_flag="Y" if i == 0 else "N", rfq_only_site_flag="N",
            address_line1=s["line1"], address_line2=s.get("line2"), city=s["city"],
            state=s.get("state"), zip=s["zip"], province=s.get("province"), country=country,
            area_code=s.get("area_code"), phone=s.get("phone"), email_address=email,
            remittance_email=f"remit@{domain}", terms_id=ctx.ref["terms"][s.get("terms", terms)],
            invoice_currency_code=currency, payment_currency_code=currency,
            pay_group_lookup_code="STANDARD", payment_method_lookup_code="EFT",
            vat_registration_num=tax_id if country != "US" else None,
            match_option=s.get("match_option", "R" if rnd.random() < 0.2 else "P"),
            tolerance_id=ctx.ref["tolerances"][tolerance],
            accts_pay_code_combination_id=liability, prepay_code_combination_id=prepay,
            hold_all_payments_flag="N", inactive_date=s.get("inactive_date"),
        )
        supplier["sites"].append(dict(row))

    # ~5% of non-primary sites are inactive.
    for site in supplier["sites"][1:]:
        if site["inactive_date"] is None and rnd.random() < 0.10:
            site["inactive_date"] = ctx.ts(ctx.days_ago(rnd.randint(30, 700)))
            for r in ctx.rows["ap.ap_supplier_sites_all"]:
                if r["vendor_site_id"] == site["vendor_site_id"]:
                    r["inactive_date"] = site["inactive_date"]

    accounts = bank_accounts or [None] * rnd.choice([1, 1, 2])
    for acct_num in accounts:
        supplier["banks"].append(_bank_account(ctx, supplier, acct_num))

    ctx.ref.setdefault("suppliers", []).append(supplier)
    ctx.ref.setdefault("suppliers_by_id", {})[vendor_id] = supplier
    return supplier


def _random_site(ctx: SeedContext, country: str, i: int) -> dict[str, Any]:
    rnd = ctx.rnd
    if country == "US":
        f = ctx.fake
        city = f.city()
        state = rnd.choice(US_STATES)
        return dict(code=(city.upper().replace(" ", "-")[:10] + ("-HQ" if i == 0 else "")), line1=f.street_address(),
                    city=city, state=state, zip=f.zipcode(), area_code=f"{rnd.randint(201, 989)}",
                    phone=f"{rnd.randint(200, 999)}-{rnd.randint(1000, 9999)}")
    if country == "IN":
        f = ctx.fake_in
        city = rnd.choice(["Pune", "Mumbai", "Bengaluru", "Chennai", "Hyderabad", "New Delhi", "Ahmedabad", "Kolkata", "Noida"])
        return dict(code=city.upper().replace(" ", "-")[:12] + ("" if i == 0 else f"-{i}"),
                    line1=f.street_address()[:240], city=city, state=None, province=f.state(),
                    zip=f"{rnd.randint(110001, 700099)}")
    f = ctx.fake_de if country in ("DE", "NL") else ctx.fake_gb
    city = f.city()
    return dict(code=city.upper().replace(" ", "-")[:12] + ("" if i == 0 else f"-{i}"),
                line1=f.street_address()[:240], city=city, zip=f.postcode())


def _bank_account(ctx: SeedContext, supplier: dict[str, Any], acct_num: str | None) -> dict[str, Any]:
    rnd = ctx.rnd
    country = supplier["country"]
    bank_country = country if country in ctx.ref["banks"] else "DE"
    bank_id = rnd.choice(ctx.ref["banks"][bank_country])
    iban = None
    if acct_num is None:
        if country in ("DE", "NL", "IE", "GB"):
            iban = make_iban(rnd, country)
            acct_num = iban[-10:]
        elif country == "IN":
            acct_num = str(rnd.randint(10**11, 10**14 - 1))
        else:
            acct_num = str(rnd.randint(10**9, 10**12 - 1))
    ext_id = ctx.next_id("iby.iby_ext_bank_accounts_s")
    ctx.add("iby.iby_ext_bank_accounts", ext_bank_account_id=ext_id, bank_id=bank_id, branch_id=bank_id,
            bank_account_num=acct_num, bank_account_name=supplier["name"][:80], bank_account_type="CHECKING",
            iban=iban, currency_code=supplier["currency"], country_code=country,
            start_date=ctx.ts(START))
    ctx.add("iby.iby_account_owners", account_owner_id=ctx.next_id("iby.iby_account_owners_s"),
            ext_bank_account_id=ext_id, account_owner_party_id=supplier["party_id"], primary_flag="Y")
    return dict(ext_bank_account_id=ext_id, bank_account_num=acct_num, iban=iban)


def generate(ctx: SeedContext, reserved_names: set[str]) -> None:
    _bank_parties(ctx)
    rnd = ctx.rnd
    used = {n.lower() for n in reserved_names}
    n = ctx.scale.suppliers
    for _ in range(n):
        country = rnd.choices(["US", "IN", "DE", "GB", "NL", "IE"], weights=[55, 30, 5, 5, 3, 2])[0]
        kind = rnd.choices(["goods", "services", "subscription"], weights=[50, 25, 25])[0]
        for _attempt in range(20):
            if country == "US":
                name = ctx.fake.company().replace(",", "") + rnd.choice([" Inc", " LLC", " Corp", ""])
            elif country == "IN":
                name = ctx.fake_in.company().replace(",", "") + rnd.choice([" Pvt Ltd", " Ltd", " LLP"])
            elif country in ("DE", "NL"):
                name = ctx.fake_de.company()
            else:
                name = ctx.fake_gb.company().replace(",", "") + rnd.choice([" Ltd", " plc", ""])
            name = name.strip()[:240]
            if name.lower() not in used:
                break
        used.add(name.lower())
        if kind == "goods":
            families = rnd.sample(GOODS_FAMILIES, rnd.choice([1, 2, 2, 3]))
        elif kind == "services":
            families = rnd.sample(PO_SERVICE_FAMILIES, rnd.choice([1, 2]))
        else:
            families = [rnd.choice(SUBSCRIPTION_FAMILIES)]
        create_supplier(ctx, name=name, country=country, kind=kind, families=families)
