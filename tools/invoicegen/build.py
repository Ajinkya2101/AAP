"""Builds InvoiceDocs from the mock EBS (read through the apps views) and the scenarios."""

from __future__ import annotations

import random
from collections import defaultdict
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import psycopg
from psycopg.rows import dict_row

from aap.ebs_mock import scenarios as sc

from .layouts import (
    ACCENTS,
    TERMS_TEXT,
    fmt_date,
    labels_for,
    number_style,
    pick_layout,
)
from .model import InvoiceDoc, Line, Vendor

CENT = Decimal("0.01")
BILL_TO = {
    204: ["ACSE Corp", "Accounts Payable", "2500 Ross Ave", "Dallas, TX 75201"],
    458: ["ACSE India Pvt Ltd", "Accounts Payable", "Survey No 12, Hinjewadi Phase 1", "Pune, Maharashtra 411057"],
}
COUNTRY_NAMES = {"DE": "Germany", "GB": "United Kingdom", "NL": "Netherlands", "IE": "Ireland", "IN": "India"}
UOM_PRINT = {"Each": ["ea", "EA", "pcs", "Each", "Nos", "unit"], "Box": ["box", "BX"], "Hour": ["hrs", "HR", "Hours"],
             "Month": ["month", "Mo"], "Pack": ["pk", "Pack"], "Set": ["set", "SET"], "License": ["License", "lic"]}
US_TAX = ["6%", "7.25%", "8%", "8.25%", "8.875%"]


def q(v: Decimal) -> Decimal:
    return v.quantize(CENT, rounding=ROUND_HALF_UP)


def fmt_qty(v: Decimal) -> str:
    return str(int(v)) if v == v.to_integral_value() else f"{v.normalize():f}"


class Ebs:
    """Read-only access to the mock EBS through the APPS views (apps_ro)."""

    def __init__(self, dsn: str):
        self.conn = psycopg.connect(dsn, row_factory=dict_row, autocommit=True, options="-c search_path=apps")

    def all(self, sql: str, params: Any = None) -> list[dict[str, Any]]:
        return self.conn.execute(sql, params).fetchall()  # type: ignore[arg-type,return-value]

    def close(self) -> None:
        self.conn.close()

    def vendor_site(self, vendor_id: int, site_code: str | None = None) -> dict[str, Any]:
        rows = self.all("""
            SELECT s.vendor_id, s.vendor_name, coalesce(s.num_1099, s.vat_registration_num) AS tax_id,
                   s.attribute1 AS po_required, s.invoice_currency_code AS currency,
                   ss.vendor_site_id, ss.vendor_site_code, ss.org_id, ss.address_line1, ss.city, ss.state,
                   ss.province, ss.zip, ss.country, ss.email_address, ss.area_code, ss.phone, ss.terms_id,
                   t.name AS terms_name, ss.inactive_date
            FROM ap_suppliers s JOIN ap_supplier_sites_all ss ON ss.vendor_id = s.vendor_id
            LEFT JOIN ap_terms t ON t.term_id = ss.terms_id
            WHERE s.vendor_id = %s AND (%s::varchar IS NULL OR ss.vendor_site_code = %s)
            ORDER BY ss.primary_pay_site_flag DESC, ss.vendor_site_id""", (vendor_id, site_code, site_code))
        if not rows:
            raise LookupError(f"vendor {vendor_id} site {site_code} not found")
        return rows[0]

    def vendor_by_name(self, name: str) -> int:
        rows = self.all("SELECT vendor_id FROM ap_suppliers WHERE vendor_name = %s", (name,))
        if not rows:
            raise LookupError(f"vendor {name!r} not found: seed the mock EBS first")
        return rows[0]["vendor_id"]

    def bank(self, vendor_id: int) -> dict[str, Any] | None:
        rows = self.all("""SELECT a.bank_account_num, a.iban FROM iby_ext_bank_accounts a
                           JOIN iby_account_owners o ON o.ext_bank_account_id = a.ext_bank_account_id
                           JOIN ap_suppliers s ON s.party_id = o.account_owner_party_id
                           WHERE s.vendor_id = %s ORDER BY a.ext_bank_account_id LIMIT 1""", (vendor_id,))
        return rows[0] if rows else None

    def invoice_exists(self, vendor_id: int, num: str) -> bool:
        return bool(self.all("SELECT 1 FROM ap_invoices_all WHERE vendor_id = %s AND upper(invoice_num) = upper(%s)",
                             (vendor_id, num)))


def vendor_block(site: dict[str, Any], *, show_address: bool = True, show_tax: bool = True,
                 email: str | None = None) -> Vendor:
    country = site["country"]
    lines: list[str] = []
    if show_address:
        lines.append(site["address_line1"])
        if country == "US":
            lines.append(f"{site['city']}, {site['state']} {site['zip']}")
        elif country == "IN":
            lines.append(f"{site['city']} - {site['zip']}" + (f", {site['province']}" if site["province"] else ""))
            lines.append("India")
        elif country in ("DE", "NL"):
            lines.append(f"{site['zip']} {site['city']}")
            lines.append(COUNTRY_NAMES[country])
        else:
            lines.append(f"{site['city']} {site['zip']}")
            lines.append(COUNTRY_NAMES.get(country, country))
    phone = None
    if site.get("phone") and show_address:
        phone = f"({site['area_code']}) {site['phone']}" if site.get("area_code") else site["phone"]
    return Vendor(name=site["vendor_name"], address_lines=lines, email=email or (site["email_address"] or "").split(",")[0] or None,
                  phone=phone, tax_id=site["tax_id"] if show_tax else None)


def _terms_text(name: str | None, rnd: random.Random) -> str | None:
    if not name:
        return None
    return rnd.choice(TERMS_TEXT.get(name, [name]))


# ------------------------------------------------------------ scenarios
def scenario_docs(ebs: Ebs, rnd: random.Random) -> list[InvoiceDoc]:
    docs = []
    for inv in sc.all_invoices():
        v = sc.VENDORS[inv.vendor]
        vendor_id = v.vendor_id or ebs.vendor_by_name(v.name)
        site = ebs.vendor_site(vendor_id, inv.site)
        eu = inv.number_style == "eu"
        currency = v.currency or ("USD" if v.country == "US" else "INR")
        style = number_style(currency, v.country, rnd, forced="eu" if eu else "us")
        if inv.currency_text in ("$", "€", "£", "₹"):
            style = type(style)(style.thousands, style.decimal, inv.currency_text, style.suffix)
        elif not eu:
            style = type(style)(",", ".", None)

        money = style.fmt
        layout = inv.layout or ("rechnung" if v.country == "DE" else rnd.choice(["classic", "modern", "boxed", "service",
                                                                              "letterhead", "sidebar", "compact"]))
        labels = labels_for(layout, v.country, rnd)
        lines = [Line(description=ln.text, amount=money(ln.amount),
                      quantity=fmt_qty(ln.qty) if ln.qty is not None else None,
                      unit_price=money(ln.price) if ln.price is not None else None) for ln in inv.lines]
        doc = InvoiceDoc(
            key=f"{inv.scenario}_{inv.invoice_num}", layout=layout, labels=labels,
            lang="de" if layout == "rechnung" else "en",
            vendor=vendor_block(site, show_address=inv.show_address, show_tax=inv.show_tax_id),
            bill_to=BILL_TO[site["org_id"]], invoice_number=inv.invoice_num, invoice_date=inv.invoice_date_text,
            lines=lines, total=money(inv.total), po_number=inv.po_number, payment_terms=inv.terms_text,
            currency_label=inv.currency_text if inv.currency_text not in ("$", "€", "£", "₹") else None,
            currency_symbol=style.symbol.strip() if style.symbol else None,
            subtotal=money(inv.subtotal) if (inv.tax_amount or len(inv.lines) > 1) else None,
            tax_label=inv.tax_label, tax_amount=money(inv.tax_amount) if inv.tax_amount else None,
            accent=rnd.choice(ACCENTS), scanned=inv.scanned, page_size="A4" if v.country == "DE" else "Letter",
            deliver="eml" if (inv.email_group or inv.sender) else "pdf", sender=inv.sender,
            email_group=inv.email_group, account_ref="1234-5678-9012" if inv.vendor == "AWS" else None,
            notes=None,
        )
        if inv.bank_text:
            doc.bank_lines = [inv.bank_text]
            nums = [t.strip(",.") for t in inv.bank_text.split() if t.strip(",.").isdigit()]
            doc.bank_account = nums[0] if nums else None
            doc.bank_routing = nums[1] if len(nums) > 1 else None
        doc.expected = dict(
            scenario=inv.scenario, vendor_id=vendor_id, vendor_site_id=site["vendor_site_id"], org_id=site["org_id"],
            po_number=inv.po_number, currency_code=currency,
            invoice_date=inv.invoice_date.isoformat() if inv.invoice_date else None,
            invoice_amount=str(inv.total), subtotal=str(inv.subtotal), tax_amount=str(inv.tax_amount),
            expect_fail=list(inv.expect_fail), expect_warn=list(inv.expect_warn),
            expect_flags=list(inv.expect_flags), expect_status=inv.expect_status,
            expect_reasons=list(inv.expect_reasons), expect_holds=list(inv.expect_holds),
            notes=inv.notes)
        docs.append(doc)
    return docs


# ---------------------------------------------------------- random: PO
SQL_OPEN_PO_LINES = """
SELECT h.po_header_id, h.segment1 AS po_number, h.vendor_id, h.vendor_site_id, h.currency_code, h.org_id,
       l.line_num, l.item_description, l.unit_meas_lookup_code AS uom, ll.price_override AS price,
       ll.quantity, ll.quantity_received, ll.quantity_billed, ll.receipt_required_flag,
       msi.attribute1 AS vendor_text
FROM   po_headers_all h
JOIN   po_lines_all l ON l.po_header_id = h.po_header_id
JOIN   po_line_locations_all ll ON ll.po_line_id = l.po_line_id
LEFT JOIN mtl_system_items_b msi ON msi.inventory_item_id = l.item_id AND msi.organization_id = 207
WHERE  h.type_lookup_code = 'STANDARD' AND h.authorization_status = 'APPROVED'
  AND  coalesce(h.cancel_flag, 'N') = 'N' AND coalesce(h.closed_code, 'OPEN') = 'OPEN'
  AND  coalesce(ll.cancel_flag, 'N') = 'N'
  AND  NOT (h.segment1 = ANY(%s))
ORDER BY h.po_header_id, l.line_num"""


def _invoice_number(ebs: Ebs, rnd: random.Random, vendor_id: int, name: str, used: set[tuple[int, str]]) -> str:
    prefix = "".join(w[0] for w in name.split()[:3] if w[:1].isalpha()).upper() or "INV"
    patterns = ["{p}-INV-{n}", "INV-2026-{n}", "{p}{n}", "{n}", "{p}/26-27/{n}"]
    pattern = patterns[vendor_id % len(patterns)]
    while True:
        num = pattern.format(p=prefix, n=rnd.randint(10000, 99999))
        if (vendor_id, num) not in used and not ebs.invoice_exists(vendor_id, num):
            used.add((vendor_id, num))
            return num


def _tax(country: str, org_id: int, rnd: random.Random) -> tuple[str | None, Decimal]:
    if org_id == 458 or country == "IN":
        pct = rnd.choice(["18%", "18%", "12%", "5%"])
        return f"{rnd.choice(['GST', 'IGST'])} {pct}", Decimal(pct.rstrip("%"))
    if country == "US":
        if rnd.random() < 0.15:
            return None, Decimal(0)
        pct = rnd.choice(US_TAX)
        return f"{rnd.choice(['Sales tax', 'Sales Tax', 'Tax'])} {pct}", Decimal(pct.rstrip("%"))
    return None, Decimal(0)


def _finish(doc: InvoiceDoc, rnd: random.Random, style, items_total: Decimal, tax_label: str | None,  # noqa: ANN001
            tax_pct: Decimal, freight: Decimal) -> tuple[Decimal, Decimal]:
    tax = q(items_total * tax_pct / 100) if tax_label else Decimal(0)
    total = items_total + tax + freight
    doc.subtotal = style.fmt(items_total) if (tax or freight or len(doc.lines) > 1) else None
    doc.tax_label = tax_label
    doc.tax_amount = style.fmt(tax) if tax else None
    doc.freight_amount = style.fmt(freight) if freight else None
    doc.total = style.fmt(total)
    return tax, total


def random_po_docs(ebs: Ebs, rnd: random.Random, n: int, used: set[tuple[int, str]], today: date) -> list[InvoiceDoc]:
    rows = ebs.all(SQL_OPEN_PO_LINES, (list(sc.POS.keys()),))
    by_po: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        billable = (r["quantity_received"] if r["receipt_required_flag"] == "Y" else r["quantity"]) - r["quantity_billed"]
        if billable > 0:
            by_po[r["po_header_id"]].append(dict(r, billable=billable))
    po_ids = sorted(by_po)
    rnd.shuffle(po_ids)
    docs = []
    for po_id in po_ids[:n]:
        lines = by_po[po_id]
        first = lines[0]
        site = _site_by_id(ebs, first["vendor_id"], first["vendor_site_id"])
        country = site["country"]
        layout = pick_layout(country, "goods", rnd)
        labels = labels_for(layout, country, rnd)
        style = number_style(first["currency_code"], country, rnd)
        show_tax = rnd.random() < 0.9
        vendor = vendor_block(site, show_tax=show_tax)
        doc_lines, items_total = [], Decimal(0)
        for ln in lines:
            qty, price = ln["billable"], Decimal(ln["price"])
            amount = q(qty * price)
            items_total += amount
            uom_choices = UOM_PRINT.get(ln["uom"] or "Each", [ln["uom"]])
            doc_lines.append(Line(description=(ln["vendor_text"] or ln["item_description"])[:120], amount=style.fmt(amount),
                                  quantity=fmt_qty(qty),
                                  unit_price=style.fmt(price, symbol=rnd.random() < 0.5),
                                  uom=rnd.choice(uom_choices) if rnd.random() < 0.4 else None))
        inv_date = today - timedelta(days=rnd.randint(1, 20))
        doc = InvoiceDoc(
            key="", layout=layout, labels=labels, lang="de" if layout == "rechnung" else "en", vendor=vendor,
            bill_to=BILL_TO[first["org_id"]],
            invoice_number=_invoice_number(ebs, rnd, first["vendor_id"], site["vendor_name"], used),
            invoice_date=fmt_date(inv_date, country, rnd, address_shown=True), lines=doc_lines, total="",
            po_number=first["po_number"], payment_terms=_terms_text(site["terms_name"], rnd) if rnd.random() < 0.85 else None,
            currency_label=first["currency_code"] if style.symbol is None or rnd.random() < 0.2 else None,
            currency_symbol=style.symbol.strip() if style.symbol else None, accent=rnd.choice(ACCENTS),
            page_size="A4" if country != "US" else "Letter",
        )
        if doc.currency_label is None and doc.currency_symbol is None:
            doc.currency_label = first["currency_code"]
        tax_label, pct = _tax(country, first["org_id"], rnd)
        freight = q(Decimal(rnd.randint(25, 250))) if rnd.random() < 0.08 else Decimal(0)
        tax, total = _finish(doc, rnd, style, items_total, tax_label, pct, freight)
        _maybe_bank(ebs, doc, first["vendor_id"], rnd)
        if rnd.random() < 0.3:
            doc.due_date = fmt_date(inv_date + timedelta(days=30), country, rnd, address_shown=True)
        doc.expected = dict(scenario=None, vendor_id=first["vendor_id"], vendor_site_id=first["vendor_site_id"],
                            org_id=first["org_id"], po_number=first["po_number"], currency_code=first["currency_code"],
                            invoice_date=inv_date.isoformat(), invoice_amount=str(total), subtotal=str(items_total),
                            tax_amount=str(tax), expect_fail=[], expect_warn=[], expect_flags=[])
        docs.append(doc)
    return docs


def _site_by_id(ebs: Ebs, vendor_id: int, site_id: int) -> dict[str, Any]:
    rows = ebs.all("SELECT vendor_site_code FROM ap_supplier_sites_all WHERE vendor_site_id = %s", (site_id,))
    return ebs.vendor_site(vendor_id, rows[0]["vendor_site_code"])


def _maybe_bank(ebs: Ebs, doc: InvoiceDoc, vendor_id: int, rnd: random.Random) -> None:
    if rnd.random() >= 0.3:
        return
    bank = ebs.bank(vendor_id)
    if not bank:
        return
    if bank["iban"]:
        doc.iban = bank["iban"]
        doc.bank_lines = [f"IBAN: {bank['iban']}"]
    else:
        doc.bank_account = bank["bank_account_num"]
        doc.bank_lines = [f"Account No: {bank['bank_account_num']}"]


# ------------------------------------------------------- random: non-PO
def random_nonpo_docs(ebs: Ebs, rnd: random.Random, n: int, used: set[tuple[int, str]], today: date) -> list[InvoiceDoc]:
    scenario_names = [v.name for v in sc.VENDORS.values()]
    vendors = ebs.all("""SELECT vendor_id FROM ap_suppliers WHERE attribute1 = 'N' AND NOT (vendor_name = ANY(%s))
                         ORDER BY vendor_id""", (scenario_names,))
    if not vendors:
        return []
    docs = []
    for _ in range(n):
        vendor_id = rnd.choice(vendors)["vendor_id"]
        history = ebs.all("""
            SELECT d.description, avg(d.amount) AS amount FROM ap_invoice_distributions_all d
            JOIN ap_invoices_all i ON i.invoice_id = d.invoice_id
            WHERE i.vendor_id = %s AND d.line_type_lookup_code = 'ITEM' AND d.description IS NOT NULL
            GROUP BY d.description ORDER BY d.description""", (vendor_id,))
        if not history:
            continue
        rows = ebs.all("""SELECT ss.vendor_site_code FROM ap_supplier_sites_all ss WHERE ss.vendor_id = %s
                          AND ss.inactive_date IS NULL ORDER BY ss.primary_pay_site_flag DESC LIMIT 1""", (vendor_id,))
        if not rows:
            continue
        site = ebs.vendor_site(vendor_id, rows[0]["vendor_site_code"])
        country = site["country"]
        layout = pick_layout(country, "subscription", rnd)
        labels = labels_for(layout, country, rnd)
        style = number_style(site["currency"], country, rnd)
        long_invoice = rnd.random() < 0.05
        picks = [rnd.choice(history) for _ in range(rnd.randint(28, 45))] if long_invoice else \
            rnd.sample(history, min(len(history), rnd.randint(1, 4)))
        doc_lines, items_total = [], Decimal(0)
        for h in picks:
            amount = q(Decimal(h["amount"]) * Decimal(str(rnd.uniform(0.75, 1.25))))
            items_total += amount
            doc_lines.append(Line(description=h["description"][:120], amount=style.fmt(amount)))
        inv_date = today - timedelta(days=rnd.randint(1, 20))
        doc = InvoiceDoc(
            key="", layout=layout, labels=labels, lang="de" if layout == "rechnung" else "en",
            vendor=vendor_block(site), bill_to=BILL_TO[site["org_id"]],
            invoice_number=_invoice_number(ebs, rnd, vendor_id, site["vendor_name"], used),
            invoice_date=fmt_date(inv_date, country, rnd, address_shown=True), lines=doc_lines, total="",
            payment_terms=_terms_text(site["terms_name"], rnd),
            currency_label=site["currency"] if style.symbol is None else None,
            currency_symbol=style.symbol.strip() if style.symbol else None, accent=rnd.choice(ACCENTS),
            account_ref=f"{rnd.randint(1000, 9999)}-{rnd.randint(1000, 9999)}" if layout == "cloud" else None,
        )
        tax_label, pct = _tax(country, site["org_id"], rnd)
        tax, total = _finish(doc, rnd, style, items_total, tax_label, pct, Decimal(0))
        _maybe_bank(ebs, doc, vendor_id, rnd)
        doc.expected = dict(scenario=None, vendor_id=vendor_id, vendor_site_id=site["vendor_site_id"],
                            org_id=site["org_id"], po_number=None, currency_code=site["currency"],
                            invoice_date=inv_date.isoformat(), invoice_amount=str(total), subtotal=str(items_total),
                            tax_amount=str(tax), expect_fail=[], expect_warn=[], expect_flags=[],
                            long_invoice=long_invoice)
        docs.append(doc)
    return docs
