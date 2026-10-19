"""AP: historical invoices, lines, distributions and holds (the GL-coding history)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from .context import SEED_USER_ID, SeedContext, money
from .gl import COMPANY_BY_ORG, ensure_usable_ccid, period_name
from .po import company_for

US_TAX_CODES = ["US SALES 6%", "US SALES 7.25%", "US SALES 8%", "US SALES 8.25%", "US SALES 8.875%"]
MISCODE_ACCOUNTS = ["6410", "6420", "6430", "6440", "6450", "6210", "6530", "7910"]


def next_invoice_num(ctx: SeedContext, supplier: dict[str, Any], d: date) -> str:
    supplier["invoice_counter"] += ctx.rnd.randint(1, 37)
    n = supplier["invoice_counter"]
    return supplier["invoice_pattern"].format(
        p=supplier["prefix"], n=n, y=d.year, y2=f"{d.year % 100:02d}", y3=f"{(d.year + 1) % 100:02d}", m=d.month
    )[:50]


def tax_code_for(ctx: SeedContext, supplier: dict[str, Any]) -> str | None:
    if "tax_code" not in supplier:
        if supplier["country"] == "US":
            supplier["tax_code"] = ctx.rnd.choice(US_TAX_CODES) if ctx.rnd.random() < 0.8 else None
        elif supplier["country"] == "IN":
            supplier["tax_code"] = ctx.rnd.choice(["GST 18%", "GST 18%", "GST 12%", "IGST 18%", "GST 5%"])
        else:
            supplier["tax_code"] = None  # reverse charge / export: no tax line
    return supplier["tax_code"]


def create_invoice(
    ctx: SeedContext,
    *,
    supplier: dict[str, Any],
    site: dict[str, Any],
    invoice_date: date,
    lines: list[dict[str, Any]],      # description, amount, qty?, price?, ccid, po?/po_line?/rcv_id?
    invoice_num: str | None = None,
    tax_code: str | None | bool = True,
    po: dict[str, Any] | None = None,
    source: str = "MANUAL INVOICE ENTRY",
    paid: bool | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    rnd = ctx.rnd
    org_id = site["org_id"]
    ledger = ctx.ref["ledger_by_org"][org_id]
    invoice_id = ctx.next_id("ap.ap_invoices_s")
    invoice_num = invoice_num or next_invoice_num(ctx, supplier, invoice_date)
    if tax_code is True:
        tax_code = tax_code_for(ctx, supplier)
    functional = ctx.ref["functional_currency"][org_id]
    currency = supplier["currency"]
    rate = None
    if currency != functional:
        rate = _rate(ctx, currency, functional, invoice_date)

    items_total = sum((money(ln["amount"]) for ln in lines), Decimal(0))
    tax_amount = Decimal(0)
    if tax_code:
        tax_amount = money(items_total * Decimal(str(ctx.ref["tax_rates"][tax_code])) / 100)
    total = items_total + tax_amount
    gl_date = invoice_date
    period = period_name(gl_date)
    terms_days = 30
    if paid is None:
        paid = (ctx.anchor - invoice_date).days > terms_days + 15

    base = (lambda amt: money(amt * rate)) if rate else (lambda amt: None)
    ctx.add(
        "ap.ap_invoices_all",
        invoice_id=invoice_id, invoice_num=invoice_num, vendor_id=supplier["vendor_id"],
        vendor_site_id=site["vendor_site_id"], org_id=org_id, set_of_books_id=ledger,
        invoice_type_lookup_code="STANDARD", invoice_date=ctx.ts(invoice_date),
        invoice_received_date=ctx.ts(min(invoice_date + timedelta(days=rnd.randint(0, 4)), ctx.anchor)),
        gl_date=ctx.ts(gl_date), invoice_amount=total, base_amount=base(total),
        amount_paid=total if paid else Decimal(0), total_tax_amount=tax_amount,
        invoice_currency_code=currency, payment_currency_code=currency,
        exchange_rate=rate, exchange_rate_type="Corporate" if rate else None,
        exchange_date=ctx.ts(invoice_date) if rate else None,
        terms_id=site["terms_id"], terms_date=ctx.ts(invoice_date),
        description=description or (f"PO {po['segment1']}" if po else lines[0]["description"])[:240],
        source=source, po_header_id=po["po_header_id"] if po else None,
        payment_status_flag="Y" if paid else "N", wfapproval_status="NOT REQUIRED", approval_status="APPROVED",
        pay_group_lookup_code=site.get("pay_group_lookup_code") or "STANDARD",
        payment_method_code=site.get("payment_method_lookup_code") or "EFT",
        accts_pay_code_combination_id=site["accts_pay_code_combination_id"],
        party_id=supplier["party_id"], doc_sequence_value=invoice_id - 800000,
        created_by=rnd.choice(ctx.ref["ap_clerk_user_ids"]), creation_date=ctx.ts(invoice_date),
        last_updated_by=SEED_USER_ID, last_update_date=ctx.ts(invoice_date),
    )

    line_no = 0
    first_ccid = None
    for ln in lines:
        line_no += 1
        amount = money(ln["amount"])
        po_line = ln.get("po_line")
        ccid = ln["ccid"]
        first_ccid = first_ccid or ccid
        ctx.add(
            "ap.ap_invoice_lines_all",
            invoice_id=invoice_id, line_number=line_no, line_type_lookup_code="ITEM",
            line_source="IMPORTED" if source == "AAP" else "MANUAL LINE ENTRY", org_id=org_id,
            description=ln["description"][:240], amount=amount, base_amount=base(amount),
            quantity_invoiced=ln.get("qty"), unit_price=ln.get("price"),
            unit_meas_lookup_code=ln.get("uom"),
            # Non-PO lines may carry an item too (R12); it gives the history its category.
            inventory_item_id=(po_line["item"] if po_line else ln.get("item") or {}).get("inventory_item_id"),
            item_description=po_line["description"] if po_line else (ln.get("item") or {}).get("description"),
            po_header_id=po["po_header_id"] if po_line else None,
            po_line_id=po_line["po_line_id"] if po_line else None,
            po_line_location_id=po_line["ll_id"] if po_line else None,
            po_distribution_id=po_line["dist_id"] if po_line else None,
            rcv_transaction_id=ln.get("rcv_id"),
            match_type=("ITEM_TO_RECEIPT" if ln.get("rcv_id") else "ITEM_TO_PO") if po_line else "NOT_MATCHED",
            accounting_date=ctx.ts(gl_date), period_name=period, default_dist_ccid=ccid,
            tax_classification_code=tax_code or None, tax_rate_code=tax_code or None,
            tax_rate=ctx.ref["tax_rates"][tax_code] if tax_code else None,
            generate_dists="D", discarded_flag="N", cancelled_flag="N", set_of_books_id=ledger,
            creation_date=ctx.ts(invoice_date),
        )
        ctx.add(
            "ap.ap_invoice_distributions_all",
            invoice_distribution_id=ctx.next_id("ap.ap_invoice_distributions_s"), invoice_id=invoice_id,
            invoice_line_number=line_no, distribution_line_number=1, line_type_lookup_code="ITEM",
            amount=amount, base_amount=base(amount), quantity_invoiced=ln.get("qty"),
            unit_price=ln.get("price"), description=ln["description"][:240],
            accounting_date=ctx.ts(gl_date), period_name=period, dist_code_combination_id=ccid,
            po_distribution_id=po_line["dist_id"] if po_line else None, rcv_transaction_id=ln.get("rcv_id"),
            match_status_flag="A", posted_flag="Y" if paid else "N",
            accrual_posted_flag="Y" if paid else "N", org_id=org_id, set_of_books_id=ledger,
            creation_date=ctx.ts(invoice_date),
        )
        if po_line is not None and ln.get("qty") is not None:
            qty = Decimal(str(ln["qty"]))
            po_line["billed"] += qty
            po_line["ll_row"]["quantity_billed"] = po_line["billed"]
            po_line["ll_row"]["amount_billed"] = (po_line["ll_row"].get("amount_billed") or Decimal(0)) + amount
            po_line["dist_row"]["quantity_billed"] = po_line["billed"]
            po_line["dist_row"]["amount_billed"] = (po_line["dist_row"].get("amount_billed") or Decimal(0)) + amount

    if tax_amount:
        line_no += 1
        company = COMPANY_BY_ORG[org_id][0]
        tax_ccid = ensure_usable_ccid(ctx, company, "000", "1450") if supplier["country"] == "IN" else first_ccid
        ctx.add("ap.ap_invoice_lines_all", invoice_id=invoice_id, line_number=line_no, line_type_lookup_code="TAX",
                line_source="MANUAL LINE ENTRY", org_id=org_id, description=f"Tax {tax_code}",
                amount=tax_amount, base_amount=base(tax_amount), accounting_date=ctx.ts(gl_date),
                period_name=period, tax_classification_code=tax_code, tax_rate_code=tax_code,
                tax_rate=ctx.ref["tax_rates"][tax_code], set_of_books_id=ledger,
                creation_date=ctx.ts(invoice_date))
        ctx.add("ap.ap_invoice_distributions_all",
                invoice_distribution_id=ctx.next_id("ap.ap_invoice_distributions_s"), invoice_id=invoice_id,
                invoice_line_number=line_no, distribution_line_number=1, line_type_lookup_code="TAX",
                amount=tax_amount, base_amount=base(tax_amount), description=f"Tax {tax_code}",
                accounting_date=ctx.ts(gl_date), period_name=period, dist_code_combination_id=tax_ccid,
                match_status_flag="A", posted_flag="Y" if paid else "N", accrual_posted_flag="N",
                org_id=org_id, set_of_books_id=ledger, creation_date=ctx.ts(invoice_date))

    inv = dict(invoice_id=invoice_id, invoice_num=invoice_num, supplier=supplier, site=site,
               invoice_date=invoice_date, total=total, po=po, org_id=org_id, lines=lines)
    ctx.ref.setdefault("invoices", []).append(inv)
    ctx.ref.setdefault("invoice_keys", set()).add((supplier["vendor_id"], invoice_num))
    return inv


def _rate(ctx: SeedContext, frm: str, to: str, d: date) -> Decimal:
    ts = ctx.ts(d)
    idx = ctx.ref.get("_rate_idx")
    if idx is None:
        idx = {(r["from_currency"], r["to_currency"], r["conversion_date"]): r["conversion_rate"]
               for r in ctx.rows["gl.gl_daily_rates"]}
        ctx.ref["_rate_idx"] = idx
    for back in range(0, 10):
        v = idx.get((frm, to, ts - timedelta(days=back)))
        if v is not None:
            return v
    return Decimal("1")


def po_invoice_lines(po: dict[str, Any], bill: dict[int, Decimal]) -> list[dict[str, Any]]:
    out = []
    for line_num, qty in bill.items():
        ln = po["lines"][line_num - 1]
        rcv_id = ln["receipts"][-1]["rcv_transaction_id"] if ln["receipts"] else None
        out.append(dict(description=ln["item"]["vendor_text"], qty=qty, price=ln["price"],
                        amount=money(qty * ln["price"]), uom=ln["item"]["uom"], ccid=ln["ccid"],
                        po_line=ln, rcv_id=rcv_id if (ln["ll_row"].get("match_option") == "R") else None))
    return out


def generate(ctx: SeedContext) -> None:
    rnd = ctx.rnd
    target = ctx.scale.invoices
    count = 0

    # ---- PO-matched invoices (billed <= received for 3-way lines).
    for po in [p for p in ctx.ref.get("pos", []) if not p["supplier"].get("scenario")]:
        if po["type"] != "STANDARD" or po["cancel"] or po["closed_code"] == "CLOSED FOR INVOICE" and rnd.random() < 0.5:
            continue
        billable = {}
        latest = po["po_date"]
        for ln in po["lines"]:
            if ln["goods"]:
                qty = ln["received"] - ln["billed"]
                if ln["receipts"]:
                    latest = max(latest, max(r["date"] for r in ln["receipts"]))
            else:
                qty = ln["qty"] - ln["billed"]
            if qty > 0:
                billable[ln["line_num"]] = qty
        if not billable:
            continue
        # Leave recent receipts unbilled more often (open work for the invoice generator).
        age = (ctx.anchor - latest).days
        if rnd.random() < (0.6 if age < 90 else 0.12):
            continue
        inv_date = min(latest + timedelta(days=rnd.randint(0, 12)), ctx.anchor - timedelta(days=1))
        keys = list(billable)
        if len(keys) > 1 and rnd.random() < 0.15:
            split = rnd.randint(1, len(keys) - 1)
            parts = [keys[:split], keys[split:]]
        else:
            parts = [keys]
        for part in parts:
            create_invoice(ctx, supplier=po["supplier"], site=po["site"], invoice_date=inv_date,
                           lines=po_invoice_lines(po, {k: billable[k] for k in part}), po=po)
            count += 1
            inv_date = min(inv_date + timedelta(days=rnd.randint(1, 10)), ctx.anchor - timedelta(days=1))

    # ---- Non-PO recurring invoices for subscription vendors (per-vendor coding habits).
    subs = [s for s in ctx.ref["suppliers"] if not s["po_required"] and not s.get("scenario")]
    remaining = max(target - count, 0)
    if subs and remaining:
        per_vendor = max(1, remaining // len(subs))
        for supplier in subs:
            site = next((s for s in supplier["sites"] if s["inactive_date"] is None), None)
            if site is None:
                continue
            items = [i for fam in supplier["families"] for code, cat_items in ctx.ref["items_by_category"].items()
                     if code.startswith(fam + "-") for i in cat_items]
            if not items:
                continue
            habit = rnd.sample(items, min(len(items), rnd.randint(1, 4)))
            fx = Decimal("83") if supplier["currency"] == "INR" else Decimal("1")
            company = company_for(ctx, site["org_id"])
            cc = supplier["default_cc"] or "510"
            # Spread over 24 months; big vendors bill several times a month (per account/usage).
            for m in range(per_vendor):
                d = ctx.anchor - timedelta(days=5 + int(725 * m / per_vendor)) + timedelta(days=rnd.randint(-2, 2))
                lines = []
                for item in habit:
                    amount = money(item["price"] * fx * Decimal(str(rnd.uniform(0.7, 1.3))))
                    acct = item["account"]
                    if acct[0] in "123":
                        acct = "6440"
                    if rnd.random() < 0.04:   # deliberate past miscoding noise
                        acct = rnd.choice([a for a in MISCODE_ACCOUNTS if a != acct])
                    lines.append(dict(description=item["vendor_text"], amount=amount, item=item,
                                      ccid=ensure_usable_ccid(ctx, company, cc, acct)))
                create_invoice(ctx, supplier=supplier, site=site, invoice_date=d, lines=lines)
                count += 1

    _holds(ctx)


def _holds(ctx: SeedContext) -> None:
    rnd = ctx.rnd
    candidates = [i for i in ctx.ref.get("invoices", []) if i["po"] is not None]
    n = min(len(candidates), max(10, ctx.scale.invoices // 40))
    for inv in rnd.sample(candidates, n):
        code = rnd.choice(["QTY REC", "QTY REC", "PRICE", "QTY ORD"])
        released = rnd.random() < 0.7
        ln = inv["lines"][0]
        ctx.add("ap.ap_holds_all", hold_id=ctx.next_id("ap.ap_holds_s"), invoice_id=inv["invoice_id"],
                line_location_id=ln["po_line"]["ll_id"] if ln.get("po_line") else None,
                hold_lookup_code=code, hold_reason={"QTY REC": "Quantity billed exceeds quantity received",
                                                    "QTY ORD": "Quantity billed exceeds quantity ordered",
                                                    "PRICE": "Invoice price exceeds purchase order price"}[code],
                hold_date=ctx.ts(inv["invoice_date"]), held_by=5, status_flag="R" if released else "S",
                release_lookup_code="VARIANCE CORRECTED" if released else None,
                release_reason="Variance corrected" if released else None, org_id=inv["org_id"],
                creation_date=ctx.ts(inv["invoice_date"]))
