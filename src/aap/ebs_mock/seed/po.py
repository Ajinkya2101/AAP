"""PO: purchase orders, shipments, distributions, receipts (RCV_*)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from .context import SeedContext, money
from .gl import COMPANY_BY_ORG, ensure_usable_ccid

LINE_TYPE_GOODS = 1
LINE_TYPE_SERVICES = 1020

COST_CENTRES_BY_FAMILY = {
    "IT": ["500", "510", "520", "530", "540"], "OFF": ["110", "115", "120", "320"],
    "FAC": ["320"], "LAB": ["400", "410", "330"], "PRO": ["100", "110", "120", "130"],
    "MKT": ["220", "200"], "LOG": ["310", "300"], "MRO": ["300", "310", "320"],
}


def generate_line_types(ctx: SeedContext) -> None:
    for lt_id, name, basis in ((LINE_TYPE_GOODS, "Goods", "GOODS"), (LINE_TYPE_SERVICES, "Services", "SERVICES")):
        ctx.reserve("po.po_line_types_s", lt_id)
        ctx.add("po.po_line_types_b", line_type_id=lt_id, line_type=name, order_type_lookup_code="QUANTITY",
                purchase_basis=basis, matching_basis="QUANTITY", receiving_flag="Y" if basis == "GOODS" else "N")


def company_for(ctx: SeedContext, org_id: int) -> str:
    companies = COMPANY_BY_ORG[org_id]
    return companies[0] if len(companies) == 1 or ctx.rnd.random() < 0.85 else companies[1]


def charge_account(ctx: SeedContext, org_id: int, item: dict[str, Any], cc: str | None = None,
                   company: str | None = None) -> int:
    company = company or company_for(ctx, org_id)
    acct = item["account"]
    if acct[0] in "123":
        cc = "000"
    elif cc is None:
        cc = ctx.rnd.choice(COST_CENTRES_BY_FAMILY.get(item["category"].split("-")[0], ["300"]))
    return ensure_usable_ccid(ctx, company, cc, acct)


def create_po(
    ctx: SeedContext,
    *,
    supplier: dict[str, Any],
    site: dict[str, Any],
    lines: list[dict[str, Any]],     # item, qty, price, optional: cc, ccid, description
    po_date: date,
    po_number: str | None = None,
    po_type: str = "STANDARD",
    closed_code: str = "OPEN",
    cancel: bool = False,
    comments: str | None = None,
) -> dict[str, Any]:
    rnd = ctx.rnd
    org_id = site["org_id"]
    header_id = ctx.next_id("po.po_headers_s")
    if po_number is None:
        po_number = str(ctx.next_id("po.po_segment1_s"))
    agent = rnd.choice(ctx.ref["buyers"][org_id])
    ship_to = rnd.choice(ctx.ref["locations_by_org"][org_id])
    bill_to = ctx.ref["location_ids"]["ACSE-DALLAS-HQ" if org_id == 204 else "ACSE-PUNE-HQ"]
    functional = ctx.ref["functional_currency"][org_id]
    currency = supplier["currency"]
    ledger = ctx.ref["ledger_by_org"][org_id]
    inv_org = ctx.ref["inv_org_by_ou"][org_id]
    company = company_for(ctx, org_id)
    total = sum(money(Decimal(str(line["qty"])) * Decimal(str(line["price"]))) for line in lines)

    header = ctx.add(
        "po.po_headers_all",
        po_header_id=header_id, segment1=po_number, type_lookup_code=po_type,
        vendor_id=supplier["vendor_id"], vendor_site_id=site["vendor_site_id"], agent_id=agent,
        ship_to_location_id=ship_to, bill_to_location_id=bill_to, terms_id=ctx.ref["terms"][supplier["terms"]],
        currency_code=currency, rate_type="Corporate" if currency != functional else None,
        rate_date=ctx.ts(po_date) if currency != functional else None,
        authorization_status="APPROVED", approved_flag="Y", approved_date=ctx.ts(po_date + timedelta(days=1)),
        closed_code=closed_code, closed_date=ctx.ts(ctx.anchor - timedelta(days=5)) if closed_code != "OPEN" else None,
        cancel_flag="Y" if cancel else "N", comments=comments or f"{supplier['name'][:40]} order",
        start_date=ctx.ts(po_date) if po_type == "BLANKET" else None,
        end_date=ctx.ts(po_date + timedelta(days=365)) if po_type == "BLANKET" else None,
        blanket_total_amount=total * 4 if po_type == "BLANKET" else None,
        org_id=org_id, creation_date=ctx.ts(po_date), last_update_date=ctx.ts(po_date),
    )
    po: dict[str, Any] = dict(po_header_id=header_id, segment1=po_number, supplier=supplier, site=site,
                              org_id=org_id, currency=currency, po_date=po_date, type=po_type,
                              closed_code=closed_code, cancel=cancel, lines=[], header=header, ledger=ledger)

    for num, spec in enumerate(lines, start=1):
        item = spec["item"]
        qty = Decimal(str(spec["qty"]))
        price = Decimal(str(spec["price"]))
        goods = item["kind"] == "G"
        line_id = ctx.next_id("po.po_lines_s")
        ctx.add("po.po_lines_all", po_line_id=line_id, po_header_id=header_id, line_num=num,
                line_type_id=LINE_TYPE_GOODS if goods else LINE_TYPE_SERVICES,
                item_id=item["inventory_item_id"], item_description=spec.get("description", item["description"]),
                category_id=item["category_id"], unit_meas_lookup_code=item["uom"], unit_price=price,
                quantity=qty if po_type == "STANDARD" else None, vendor_product_num=item["segment1"][-10:],
                matching_basis="QUANTITY", order_type_lookup_code="QUANTITY",
                closed_code=closed_code, cancel_flag="Y" if cancel else "N", org_id=org_id,
                creation_date=ctx.ts(po_date))
        line: dict[str, Any] = dict(po_line_id=line_id, line_num=num, item=item, qty=qty, price=price,
                                    description=spec.get("description", item["description"]),
                                    goods=goods, received=Decimal(0), billed=Decimal(0), ll_id=None,
                                    dist_id=None, ccid=None, receipts=[])
        po["lines"].append(line)
        if po_type != "STANDARD":
            continue  # a blanket agreement ships through its releases (generate_releases)

        ll_id = ctx.next_id("po.po_line_locations_s")
        need_by = po_date + timedelta(days=rnd.randint(10, 45))
        ll = ctx.add("po.po_line_locations_all", line_location_id=ll_id, po_header_id=header_id, po_line_id=line_id,
                     shipment_num=1, shipment_type="STANDARD", quantity=qty, quantity_received=Decimal(0),
                     quantity_accepted=Decimal(0), quantity_rejected=Decimal(0), quantity_billed=Decimal(0),
                     quantity_cancelled=qty if cancel else Decimal(0), price_override=price,
                     need_by_date=ctx.ts(need_by), promised_date=ctx.ts(need_by),
                     ship_to_location_id=ship_to, ship_to_organization_id=inv_org,
                     receipt_required_flag="Y" if goods else "N", inspection_required_flag="N",
                     match_option=site.get("match_option") or "P", qty_rcv_tolerance=0,
                     invoice_close_tolerance=0, receive_close_tolerance=0, taxable_flag="Y",
                     closed_code=closed_code, approved_flag="Y", cancel_flag="Y" if cancel else "N",
                     org_id=org_id, creation_date=ctx.ts(po_date))
        ccid = spec.get("ccid") or charge_account(ctx, org_id, item, spec.get("cc"), company)
        dist_id = ctx.next_id("po.po_distributions_s")
        dist = ctx.add("po.po_distributions_all", po_distribution_id=dist_id, po_header_id=header_id,
                       po_line_id=line_id, line_location_id=ll_id, distribution_num=1, set_of_books_id=ledger,
                       code_combination_id=ccid,
                       accrual_account_id=ensure_usable_ccid(ctx, company, "000", "2230"),
                       variance_account_id=ensure_usable_ccid(ctx, company, "000", "5040"),
                       budget_account_id=ccid, quantity_ordered=qty, quantity_delivered=Decimal(0),
                       quantity_billed=Decimal(0), quantity_cancelled=qty if cancel else Decimal(0),
                       amount_billed=Decimal(0), destination_type_code="EXPENSE",
                       deliver_to_location_id=ship_to,
                       deliver_to_person_id=rnd.choice(ctx.ref["requesters"][org_id]),
                       accrue_on_receipt_flag="Y" if goods else "N", org_id=org_id,
                       creation_date=ctx.ts(po_date))
        line.update(ll_id=ll_id, dist_id=dist_id, ccid=ccid, ll_row=ll, dist_row=dist, need_by=need_by)

    ctx.ref.setdefault("pos", []).append(po)
    ctx.ref.setdefault("pos_by_number", {})[po_number] = po
    return po


def receive(ctx: SeedContext, po: dict[str, Any], quantities: dict[int, Decimal], when: date,
            returns: dict[int, Decimal] | None = None) -> None:
    """One receipt (RCV_SHIPMENT_HEADERS) for several lines of a PO.
    quantities maps line_num -> quantity received. Creates RECEIVE + DELIVER
    transactions, optional RETURN TO VENDOR, and updates the PO quantities."""
    when = min(when, ctx.anchor)
    supplier, site = po["supplier"], po["site"]
    header_id = ctx.next_id("po.rcv_shipment_headers_s")
    inv_org = ctx.ref["inv_org_by_ou"][po["org_id"]]
    ctx.add("po.rcv_shipment_headers", shipment_header_id=header_id,
            receipt_num=str(ctx.next_id("po.rcv_receipt_num_s")), receipt_source_code="VENDOR",
            vendor_id=supplier["vendor_id"], vendor_site_id=site["vendor_site_id"],
            shipment_num=f"ASN{header_id}", packing_slip=f"PS-{ctx.rnd.randint(10000, 99999)}",
            shipped_date=ctx.ts(when - timedelta(days=ctx.rnd.randint(1, 5))), ship_to_org_id=inv_org,
            creation_date=ctx.ts(when))
    for n, (line_num, qty) in enumerate(sorted(quantities.items()), start=1):
        if qty <= 0:
            continue
        line = po["lines"][line_num - 1]
        sl_id = ctx.next_id("po.rcv_shipment_lines_s")
        ctx.add("po.rcv_shipment_lines", shipment_line_id=sl_id, shipment_header_id=header_id, line_num=n,
                item_id=line["item"]["inventory_item_id"], item_description=line["description"],
                quantity_shipped=qty, quantity_received=qty, unit_of_measure=line["item"]["uom"],
                po_header_id=po["po_header_id"], po_line_id=line["po_line_id"],
                po_line_location_id=line["ll_id"], po_distribution_id=line["dist_id"],
                shipment_line_status_code="FULLY RECEIVED", to_organization_id=inv_org,
                creation_date=ctx.ts(when))
        common = dict(unit_of_measure=line["item"]["uom"], shipment_header_id=header_id, shipment_line_id=sl_id,
                      po_header_id=po["po_header_id"], po_line_id=line["po_line_id"],
                      po_line_location_id=line["ll_id"], po_distribution_id=line["dist_id"],
                      vendor_id=supplier["vendor_id"], vendor_site_id=site["vendor_site_id"],
                      po_unit_price=line["price"], currency_code=po["currency"],
                      destination_type_code="EXPENSE", organization_id=inv_org)
        rcv_id = ctx.next_id("po.rcv_transactions_s")
        ctx.add("po.rcv_transactions", transaction_id=rcv_id, parent_transaction_id=-1,
                transaction_type="RECEIVE", transaction_date=ctx.ts(when), quantity=qty,
                creation_date=ctx.ts(when), **common)
        ctx.add("po.rcv_transactions", transaction_id=ctx.next_id("po.rcv_transactions_s"),
                parent_transaction_id=rcv_id, transaction_type="DELIVER", transaction_date=ctx.ts(when),
                quantity=qty, creation_date=ctx.ts(when), **common)
        net = qty
        ret = (returns or {}).get(line_num, Decimal(0))
        if ret > 0:
            ctx.add("po.rcv_transactions", transaction_id=ctx.next_id("po.rcv_transactions_s"),
                    parent_transaction_id=rcv_id, transaction_type="RETURN TO VENDOR",
                    transaction_date=ctx.ts(min(when + timedelta(days=3), ctx.anchor)), quantity=ret,
                    creation_date=ctx.ts(when), **common)
            net -= ret
        line["received"] += net
        line["receipts"].append(dict(rcv_transaction_id=rcv_id, date=when, qty=net))
        line["ll_row"]["quantity_received"] = line["received"]
        line["ll_row"]["quantity_accepted"] = line["received"]
        line["dist_row"]["quantity_delivered"] = line["received"]


def generate(ctx: SeedContext) -> None:
    rnd = ctx.rnd
    suppliers = [s for s in ctx.ref["suppliers"] if s["po_required"] and not s.get("scenario")]
    for _ in range(ctx.scale.pos):
        supplier = rnd.choice(suppliers)
        sites = [s for s in supplier["sites"] if s["purchasing_site_flag"] == "Y" and s["inactive_date"] is None]
        if not sites:
            continue
        site = rnd.choice(sites)
        items = [i for fam in supplier["families"] for code, cat_items in ctx.ref["items_by_category"].items()
                 if code.startswith(fam + "-") for i in cat_items]
        if not items:
            continue
        n_lines = rnd.randint(2, 6)
        chosen = rnd.sample(items, min(n_lines, len(items)))
        fx = Decimal("1") if supplier["currency"] in ("USD", "EUR", "GBP") else Decimal("83")
        specs = []
        for item in chosen:
            price = money(item["price"] * fx * Decimal(str(rnd.uniform(0.92, 1.08))))
            if item["kind"] == "G":
                qty = rnd.choice([1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 24, 25, 30, 40, 50, 100])
                if price > 2000:
                    qty = rnd.choice([1, 2, 3, 4])
            else:
                qty = rnd.choice([1, 1, 2, 3, 4, 6, 12]) if item["uom"] != "Hour" else rnd.choice([8, 16, 24, 40, 80])
            specs.append(dict(item=item, qty=qty, price=price))
        po_date = ctx.random_date(730, 20)
        roll = rnd.random()
        po_type = "BLANKET" if roll < 0.10 else "STANDARD"
        closed, cancel = "OPEN", False
        if po_type == "STANDARD" and rnd.random() < 0.03:
            closed, cancel = rnd.choice([("CLOSED", False), ("FINALLY CLOSED", False), ("CLOSED FOR INVOICE", False), ("OPEN", True)])
        po = create_po(ctx, supplier=supplier, site=site, lines=specs, po_date=po_date, po_type=po_type,
                       closed_code=closed, cancel=cancel)
        if po_type != "STANDARD" or cancel:
            continue
        # 80% fully received, 12% partially, 5% not received (+3% closed/cancelled above).
        r = rnd.random()
        status = "full" if r < 0.82 else ("partial" if r < 0.95 else "none")
        if closed != "OPEN":
            status = "full"
        po["receipt_status"] = status
        goods_lines = [ln for ln in po["lines"] if ln["goods"]]
        if status == "none" or not goods_lines:
            continue
        first = min(ln["need_by"] for ln in goods_lines)
        if first > ctx.anchor:
            po["receipt_status"] = "none"
            continue
        if status == "full":
            if rnd.random() < 0.3 and len(goods_lines) > 1:
                split = rnd.randint(1, len(goods_lines) - 1)
                receive(ctx, po, {ln["line_num"]: ln["qty"] for ln in goods_lines[:split]}, first)
                receive(ctx, po, {ln["line_num"]: ln["qty"] for ln in goods_lines[split:]},
                        first + timedelta(days=rnd.randint(3, 20)))
            else:
                receive(ctx, po, {ln["line_num"]: ln["qty"] for ln in goods_lines}, first)
        else:
            qtys = {}
            for ln in goods_lines:
                part = (ln["qty"] * Decimal(str(rnd.uniform(0.3, 0.9)))).to_integral_value()
                qtys[ln["line_num"]] = part if part > 0 else Decimal(0)
            returns = {}
            if rnd.random() < 0.15:
                ln = rnd.choice(goods_lines)
                if qtys.get(ln["line_num"], 0) > 1:
                    returns[ln["line_num"]] = Decimal(1)
            receive(ctx, po, qtys, first, returns)


def create_release(ctx: SeedContext, po: dict[str, Any], quantities: dict[int, Decimal], when: date,
                   release_num: int) -> dict[str, Any]:
    """One release of a blanket agreement: PO_RELEASES_ALL plus a shipment and a distribution per
    released line (quantities maps line_num -> quantity). Returns a PO-shaped dict whose lines are
    the release's shipments, so receive() works on it."""
    org_id = po["org_id"]
    release_id = ctx.next_id("po.po_releases_s")
    ctx.add("po.po_releases_all", po_release_id=release_id, po_header_id=po["po_header_id"], release_num=release_num,
            agent_id=po["header"]["agent_id"], release_date=ctx.ts(when), release_type="BLANKET",
            authorization_status="APPROVED", approved_flag="Y", approved_date=ctx.ts(when), closed_code="OPEN",
            cancel_flag="N", org_id=org_id, creation_date=ctx.ts(when), last_update_date=ctx.ts(when))
    company = company_for(ctx, org_id)
    lines: list[dict[str, Any] | None] = [None] * len(po["lines"])
    for line_num, qty in sorted(quantities.items()):
        base = po["lines"][line_num - 1]
        ll_id = ctx.next_id("po.po_line_locations_s")
        need_by = when + timedelta(days=ctx.rnd.randint(7, 30))
        ll = ctx.add("po.po_line_locations_all", line_location_id=ll_id, po_header_id=po["po_header_id"],
                     po_line_id=base["po_line_id"], po_release_id=release_id, shipment_num=1, shipment_type="BLANKET",
                     quantity=qty, quantity_received=Decimal(0), quantity_accepted=Decimal(0),
                     quantity_rejected=Decimal(0), quantity_billed=Decimal(0), quantity_cancelled=Decimal(0),
                     price_override=base["price"], need_by_date=ctx.ts(need_by), promised_date=ctx.ts(need_by),
                     ship_to_location_id=po["header"]["ship_to_location_id"],
                     ship_to_organization_id=ctx.ref["inv_org_by_ou"][org_id],
                     receipt_required_flag="Y" if base["goods"] else "N", inspection_required_flag="N",
                     match_option=po["site"].get("match_option") or "P", qty_rcv_tolerance=0,
                     invoice_close_tolerance=0, receive_close_tolerance=0, taxable_flag="Y", closed_code="OPEN",
                     approved_flag="Y", cancel_flag="N", org_id=org_id, creation_date=ctx.ts(when))
        ccid = charge_account(ctx, org_id, base["item"], None, company)
        dist_id = ctx.next_id("po.po_distributions_s")
        dist = ctx.add("po.po_distributions_all", po_distribution_id=dist_id, po_header_id=po["po_header_id"],
                       po_line_id=base["po_line_id"], line_location_id=ll_id, po_release_id=release_id,
                       distribution_num=1, set_of_books_id=po["ledger"], code_combination_id=ccid,
                       accrual_account_id=ensure_usable_ccid(ctx, company, "000", "2230"),
                       variance_account_id=ensure_usable_ccid(ctx, company, "000", "5040"),
                       budget_account_id=ccid, quantity_ordered=qty, quantity_delivered=Decimal(0),
                       quantity_billed=Decimal(0), quantity_cancelled=Decimal(0), amount_billed=Decimal(0),
                       destination_type_code="EXPENSE", deliver_to_location_id=po["header"]["ship_to_location_id"],
                       deliver_to_person_id=ctx.rnd.choice(ctx.ref["requesters"][org_id]),
                       accrue_on_receipt_flag="Y" if base["goods"] else "N", org_id=org_id,
                       creation_date=ctx.ts(when))
        lines[line_num - 1] = dict(base, qty=qty, received=Decimal(0), ll_id=ll_id, dist_id=dist_id, ccid=ccid,
                                   ll_row=ll, dist_row=dist, need_by=need_by, receipts=[])
    release = dict(po, po_release_id=release_id, release_num=release_num, lines=lines)
    po.setdefault("releases", []).append(release)
    return release


def generate_releases(ctx: SeedContext) -> None:
    """1-3 releases per open blanket agreement, most of them received. Runs after every other
    generator, so the ids and random draws of the rest of the seed do not move."""
    rnd = ctx.rnd
    for po in [p for p in ctx.ref.get("pos", []) if p["type"] == "BLANKET" and not p["cancel"]]:
        when = po["po_date"]
        for release_num in range(1, rnd.randint(1, 3) + 1):
            when = min(when + timedelta(days=rnd.randint(15, 90)), ctx.anchor - timedelta(days=5))
            chosen = rnd.sample(po["lines"], rnd.randint(1, len(po["lines"])))
            qtys = {ln["line_num"]: max(Decimal(1), (ln["qty"] * Decimal(str(rnd.uniform(0.3, 1.0))))
                                        .to_integral_value()) for ln in chosen}
            release = create_release(ctx, po, qtys, when, release_num)
            goods = {n: q for n, q in qtys.items() if po["lines"][n - 1]["goods"]}
            if goods and rnd.random() < 0.8:
                receive(ctx, release, goods, when + timedelta(days=rnd.randint(5, 20)))
