"""Inserts the golden scenario fixtures (aap.ebs_mock.scenarios) on top of the random data."""

from __future__ import annotations

from decimal import Decimal

from aap.ebs_mock import scenarios as sc

from .context import SeedContext, money
from .gl import ensure_usable_ccid
from .invoices import create_invoice
from .items import add_item
from .po import create_po, receive
from .suppliers import create_supplier

SCENARIO_VENDOR_NAMES = {v.name for v in sc.VENDORS.values()}


def reserve_ids(ctx: SeedContext) -> None:
    for v in sc.VENDORS.values():
        if v.vendor_id:
            ctx.reserve("ap.ap_suppliers_s", v.vendor_id)


def generate(ctx: SeedContext) -> None:
    items = {}
    for key, it in sc.ITEMS.items():
        cat = ctx.ref["categories"][it.category]
        cat = dict(cat, uom=it.uom)
        items[key] = add_item(ctx, cat, it.internal, it.vendor_text, it.price, item_number=f"SC-{key}")

    suppliers = {}
    for key, v in sc.VENDORS.items():
        sites = [dict(code=s.code, line1=s.line1, city=s.city, state=s.state, zip=s.zip, email=s.email,
                      inactive_date=ctx.ts(s.inactive_date) if s.inactive_date else None, match_option="P")
                 for s in v.sites]
        sup = create_supplier(
            ctx, name=v.name, country=v.country, kind=v.kind, families=list(v.families), tax_id=v.tax_id,
            vendor_id=v.vendor_id, currency=v.currency, terms=v.terms, tolerance=v.tolerance,
            po_required=v.po_required, default_cc=v.default_cc, domain=v.domain, sites=sites,
            bank_accounts=list(v.bank_accounts) or None,
        )
        sup["scenario"] = key
        sup["tax_code"] = v.tax_code
        # Random inactivation must not touch scenario sites.
        for site, spec in zip(sup["sites"], sites, strict=True):
            site["inactive_date"] = spec["inactive_date"]
            for r in ctx.rows["ap.ap_supplier_sites_all"]:
                if r["vendor_site_id"] == site["vendor_site_id"]:
                    r["inactive_date"] = spec["inactive_date"]
        suppliers[key] = sup

    for number, p in sc.POS.items():
        sup = suppliers[p.vendor]
        site = next(s for s in sup["sites"] if s["vendor_site_code"] == p.site)
        lines = [dict(item=items[ln.item], qty=ln.qty, price=ln.price,
                      ccid=ensure_usable_ccid(ctx, *ln.ccid)) for ln in p.lines]
        po = create_po(ctx, supplier=sup, site=site, lines=lines, po_date=p.po_date, po_number=number,
                       closed_code=p.closed_code)
        received = {i: ln.received for i, ln in enumerate(p.lines, start=1) if ln.received > 0}
        if received and p.receipt_date:
            # Receipts are recorded even for service lines here (fixtures are explicit).
            receive(ctx, po, received, p.receipt_date)

    for h in sc.HISTORY:
        sup = suppliers[h.vendor]
        site = next(s for s in sup["sites"] if s["inactive_date"] is None)
        if h.po:
            po = ctx.ref["pos_by_number"][h.po]
            lines = []
            for ln in po["lines"]:
                qty = ln["received"] - ln["billed"]
                lines.append(dict(description=ln["item"]["vendor_text"], qty=qty, price=ln["price"],
                                  amount=money(qty * ln["price"]), uom=ln["item"]["uom"], ccid=ln["ccid"],
                                  po_line=ln))
            create_invoice(ctx, supplier=sup, site=po["site"], invoice_date=h.invoice_date, lines=lines,
                           invoice_num=h.invoice_num, po=po)
        else:
            company = "11" if sup["country"] == "IN" else "01"
            cc = sup["default_cc"] or "510"
            lines = [dict(description=ln.description, amount=ln.amount,
                          ccid=ensure_usable_ccid(ctx, company, cc, ln.account)) for ln in h.lines]
            create_invoice(ctx, supplier=sup, site=site, invoice_date=h.invoice_date, lines=lines,
                           invoice_num=h.invoice_num, tax_code=None)

    ctx.ref["scenario_suppliers"] = suppliers
    ctx.ref["scenario_items"] = items
    _check(ctx)


def _check(ctx: SeedContext) -> None:
    po = ctx.ref["pos_by_number"]["4500123"]
    assert po["lines"][0]["received"] == Decimal(8), "S1 fixture: line 1 must be received 8 of 10"
    assert po["supplier"]["vendor_id"] == 30418
