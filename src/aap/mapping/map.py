"""Stage 7 Map: normalized values -> Oracle values (ARCHITECTURE 6.5, F-04/F-05).

Per value: step 1 exact (value map / reference copy), step 2 RapidFuzz top 5,
step 3 LLM chooses one candidate or "none" (enum schema). Supplier first, then
the site filtered by that supplier.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from rapidfuzz import fuzz, process
from sqlalchemy.orm import Session

from aap.common.text import email_domain, name_key, text_key
from aap.db.models import Document, RefSupplier, RefSupplierSite
from aap.llm import LlmClient
from aap.schemas.canonical import CanonicalInvoice, CanonicalLine
from aap.schemas.fields import Field, Flag, Method

from .choose import choose
from .reference import Reference, history_category

SUPPLIER_AUTO = 92
SUPPLIER_MARGIN = 8
SUPPLIER_MIN = 50
SITE_AUTO = 80
SITE_MARGIN = 10


@dataclass
class MapContext:
    session: Session
    llm: LlmClient
    ref: Reference
    document: Document
    sender: str | None = None

    @property
    def doc_id(self) -> uuid.UUID:
        return self.document.id


def _like(src: Field, value, method: Method, *flags: str, note: str | None = None,  # noqa: ANN001
          confidence: float | None = None) -> Field:
    return Field(value=value, source_value=src.source_value if src.source_value is not None else (
        str(src.value) if src.value is not None else None), page=src.page, bbox=src.bbox, method=method,
                 flags=[f for f in src.flags if f not in (Flag.currency_ambiguous,)] + list(flags), note=note,
                 confidence=confidence)


# ================================================================ supplier
def _set_supplier(inv: CanonicalInvoice, s: RefSupplier, method: Method, *flags: str, note: str | None = None,
                  confidence: float | None = None) -> None:
    src = inv.vendor_name
    inv.vendor_id = _like(src, s.vendor_id, method, *flags, note=note, confidence=confidence)
    inv.vendor_name = _like(src, s.vendor_name, method, *flags, note=note, confidence=confidence)
    inv.vendor_number = Field(value=s.vendor_number, method=Method.derived)


def map_supplier(ctx: MapContext, inv: CanonicalInvoice) -> RefSupplier | None:
    ref = ctx.ref
    printed = inv.vendor_name.value
    tax = inv.vendor_tax_id.value

    vm = ref.value_map("supplier", printed)
    if vm and vm.oracle_id in ref.suppliers:
        s = ref.suppliers[vm.oracle_id]
        _set_supplier(inv, s, Method.value_map)
        return s

    if tax:
        hits = [s for s in ref.suppliers.values() if s.tax_id and s.tax_id == tax]
        if len(hits) == 1:
            s = hits[0]
            flags = []
            if printed and fuzz.WRatio(name_key(printed), s.name_key) < 60:
                flags.append("tax_id_name_conflict")
            _set_supplier(inv, s, Method.exact, *flags, note="matched on tax ID")
            return s

    key = name_key(printed)
    if key:
        hits = [s for s in ref.suppliers.values() if s.name_key == key or
                (s.vendor_name_alt and name_key(s.vendor_name_alt) == key)]
        if len(hits) == 1:
            _set_supplier(inv, hits[0], Method.exact, note="matched on name")
            return hits[0]

    for addr in (inv.vendor_email.value, ctx.sender):
        domain = email_domain(addr)
        if not domain:
            continue
        vendors = {site.vendor_id for site in ref.sites.values() if site.email_domains and domain in site.email_domains}
        if len(vendors) == 1:
            s = ref.suppliers[vendors.pop()]
            _set_supplier(inv, s, Method.exact, note=f"matched on email domain {domain}")
            return s

    if not key:
        inv.vendor_id = _like(inv.vendor_name, None, Method.extracted, Flag.no_map, Flag.missing)
        return None
    choices = {vid: s.name_key for vid, s in ref.suppliers.items()}
    top = process.extract(key, choices, scorer=fuzz.WRatio, limit=5)
    if not top or top[0][1] < SUPPLIER_MIN:
        inv.vendor_id = _like(inv.vendor_name, None, Method.fuzzy, Flag.no_map)
        return None
    best_score, second = top[0][1], (top[1][1] if len(top) > 1 else 0)
    if best_score >= SUPPLIER_AUTO and best_score - second >= SUPPLIER_MARGIN:
        s = ref.suppliers[top[0][2]]
        _set_supplier(inv, s, Method.fuzzy, confidence=best_score / 100,
                      note=f"fuzzy {best_score:.0f} vs next {second:.0f}")
        return s
    candidates = [{"id": str(vid), "label": f"{ref.suppliers[vid].vendor_name} (supplier {ref.suppliers[vid].vendor_number}"
                                             f", tax id {ref.suppliers[vid].tax_id or '-'})", "score": round(sc)}
                  for _, sc, vid in top]
    pick = choose(ctx.session, ctx.llm, document_id=ctx.doc_id, node="map.supplier",
                  question="Which supplier sent this invoice?", value=printed or "",
                  candidates=candidates, context=f"tax id: {tax or '-'}; address: {inv.vendor_address.value or '-'}")
    if pick.id is None:
        inv.vendor_id = _like(inv.vendor_name, None, Method.fuzzy_llm, Flag.no_map, note=pick.reason)
        return None
    s = ref.suppliers[int(pick.id)]
    _set_supplier(inv, s, Method.fuzzy_llm, Flag.check_map, note=pick.reason)
    return s


# ==================================================================== site
def _addr_key(text: str | None) -> str:
    return text_key(text or "").replace(",", " ")


def map_site(ctx: MapContext, inv: CanonicalInvoice, supplier: RefSupplier) -> RefSupplierSite | None:
    sites = [s for s in ctx.ref.sites_by_vendor.get(supplier.vendor_id, []) if s.pay_site]
    if not sites:
        inv.vendor_site_id = Field(value=None, method=Method.default, flags=[Flag.no_map], note="no pay site")
        return None
    cur = inv.currency_code.value
    orgs = {s.org_id for s in sites}
    if len(orgs) > 1 and cur:
        preferred = [s for s in sites if ctx.ref.operating_units.get(s.org_id) and
                     ctx.ref.operating_units[s.org_id].currency_code == cur]
        sites = preferred or sites
    src = inv.vendor_address
    if len(sites) == 1:
        return _set_site(inv, sites[0], Method.exact, note="only pay site")
    addr = _addr_key(inv.vendor_address.value)
    email = (inv.vendor_email.value or "").lower()
    scored = []
    for s in sites:
        score = fuzz.token_set_ratio(addr, _addr_key(s.address)) if addr else 0.0
        if email and s.email_address and email in s.email_address.lower():
            score += 10
        if addr and s.vendor_site_code.lower().replace("-", " ") in addr:
            score += 10
        scored.append((min(score, 100.0), s))
    scored.sort(key=lambda x: x[0], reverse=True)
    best, second = scored[0][0], scored[1][0]
    if not addr:
        primary = next((s for s in sites if s.primary_pay_site and s.inactive_date is None), sites[0])
        return _set_site(inv, primary, Method.default, Flag.check_map, note="no address printed: primary pay site")
    if best >= SITE_AUTO and best - second >= SITE_MARGIN:
        return _set_site(inv, scored[0][1], Method.fuzzy, note=f"address {best:.0f} vs next {second:.0f}",
                         confidence=best / 100)
    candidates = [{"id": str(s.vendor_site_id), "label": f"{s.vendor_site_code}: {s.address}", "score": round(sc)}
                  for sc, s in scored[:5]]
    pick = choose(ctx.session, ctx.llm, document_id=ctx.doc_id, node="map.site",
                  question=f"Which site of supplier {supplier.vendor_name} matches the address on the invoice?",
                  value=src.value or "", candidates=candidates)
    if pick.id is None:
        inv.vendor_site_id = Field(value=None, method=Method.fuzzy_llm, flags=[Flag.no_map], note=pick.reason)
        return None
    return _set_site(inv, ctx.ref.sites[int(pick.id)], Method.fuzzy_llm, Flag.check_map, note=pick.reason)


def _set_site(inv: CanonicalInvoice, site: RefSupplierSite, method: Method, *flags: str, note: str | None = None,
              confidence: float | None = None) -> RefSupplierSite:
    inv.vendor_site_id = _like(inv.vendor_address, site.vendor_site_id, method, *flags, note=note,
                               confidence=confidence)
    inv.vendor_site_code = Field(value=site.vendor_site_code, method=Method.derived)
    return site


# ===================================================== org and currency
def map_org_currency(ctx: MapContext, inv: CanonicalInvoice, supplier: RefSupplier | None,
                     site: RefSupplierSite | None) -> None:
    if site is not None:
        ou = ctx.ref.operating_units.get(site.org_id)
        inv.org_id = Field(value=site.org_id, method=Method.derived, note="from supplier site")
        if ou:
            inv.operating_unit = Field(value=ou.name, method=Method.derived)
            inv.ledger_currency = Field(value=ou.currency_code, method=Method.derived)
    default = (site.invoice_currency_code if site else None) or (supplier.invoice_currency_code if supplier else None)
    cur = inv.currency_code
    if cur.value is None:
        if default:
            inv.currency_code = _like(cur, default, Method.default, Flag.currency_from_vendor_default)
        else:
            inv.currency_code = _like(cur, None, Method.default, Flag.no_map, Flag.missing)
    elif Flag.currency_ambiguous in cur.flags:
        candidates = (cur.note or "").removeprefix("candidates:").split(",")
        if default and default in candidates:
            inv.currency_code = _like(cur, default, Method.default, Flag.currency_from_vendor_default,
                                      note=f"symbol {cur.source_value!r} -> supplier default currency")
    if inv.currency_code.value and inv.currency_code.value not in ctx.ref.currencies:
        inv.currency_code.with_flag(Flag.no_map)
    ledger_cur = inv.ledger_currency.value
    if ledger_cur and inv.currency_code.value and inv.currency_code.value != ledger_cur:
        inv.exchange_rate_type = Field(value="Corporate", method=Method.default)


# =================================================================== terms
TERMS_IMMEDIATE = re.compile(r"(?i)(receipt|immediate|sofort|on delivery|cod\b)")
TERMS_DISCOUNT = re.compile(r"(?i)(\d+(?:\.\d+)?)\s*%?\s*/\s*(\d+)\s*,?\s*(?:days?\s*,?\s*)?net\s*(\d+)")
TERMS_NET = re.compile(r"(?i)(?:net|netto|within|in|due|zahlbar|payable)\D{0,20}?(\d{1,3})|(\d{1,3})\s*(?:days?|tage|dias|jours)\b|"
                       r"\bn\s?(\d{1,3})\b")


def map_terms(ctx: MapContext, inv: CanonicalInvoice, supplier: RefSupplier | None,
              site: RefSupplierSite | None) -> None:
    ref = ctx.ref
    src = inv.terms_name
    printed = src.value
    terms_by_name = {t.name.upper(): t for t in ref.terms.values() if t.enabled}

    def set_terms(term, method: Method, *flags: str, note: str | None = None) -> None:  # noqa: ANN001
        inv.terms_id = _like(src, term.term_id, method, *flags, note=note)
        inv.terms_name = _like(src, term.name, method, *flags, note=note)

    if not printed:
        default_id = (site.terms_id if site else None) or (supplier.terms_id if supplier else None)
        if default_id and default_id in ref.terms:
            set_terms(ref.terms[default_id], Method.default, note="no terms printed: supplier site default")
        return
    vm = ref.value_map("terms", printed, supplier.vendor_id if supplier else None)
    if vm and vm.oracle_value.upper() in terms_by_name:
        set_terms(terms_by_name[vm.oracle_value.upper()], Method.value_map)
        return
    if printed.upper() in terms_by_name:
        set_terms(terms_by_name[printed.upper()], Method.exact)
        return
    if TERMS_IMMEDIATE.search(printed) and "IMMEDIATE" in terms_by_name:
        set_terms(terms_by_name["IMMEDIATE"], Method.normalized, note="parsed: due immediately")
        return
    m = TERMS_DISCOUNT.search(printed)
    if m:
        pct, days, net = Decimal(m.group(1)), int(m.group(2)), int(m.group(3))
        for t in ref.terms.values():
            if t.enabled and t.discount_percent == pct and t.discount_days == days and t.due_days == net:
                set_terms(t, Method.normalized, note=f"parsed: {pct}% {days} days, net {net}")
                return
    if re.search(r"(?i)\b(eom|end of (the )?month)\b", printed):
        eom = [t for t in ref.terms.values() if t.enabled and t.due_day_of_month]
        if len(eom) == 1:
            set_terms(eom[0], Method.normalized, note="parsed: end of month")
            return
    m = TERMS_NET.search(printed)
    if m:
        days = int(next(g for g in m.groups() if g))
        plain = [t for t in ref.terms.values() if t.enabled and t.due_days == days and t.installments == 1
                 and not t.discount_percent and not t.due_day_of_month]
        if plain:
            plain.sort(key=lambda t: (t.name != f"{days} NET", t.name))
            set_terms(plain[0], Method.normalized, note=f"parsed: net {days} days")
            return
    labels = {t.term_id: f"{t.name} {t.description or ''}" for t in ref.terms.values() if t.enabled}
    top = process.extract(text_key(printed), labels, scorer=fuzz.token_set_ratio, limit=5)
    candidates = [{"id": str(tid), "label": f"{ref.terms[tid].name} ({ref.terms[tid].description})", "score": round(sc)}
                  for _, sc, tid in top]
    pick = choose(ctx.session, ctx.llm, document_id=ctx.doc_id, node="map.terms",
                  question="Which payment terms mean the same as the terms printed on the invoice?", value=printed,
                  candidates=candidates)
    if pick.id is None:
        inv.terms_id = _like(src, None, Method.fuzzy_llm, Flag.no_map, note=pick.reason)
        return
    set_terms(ref.terms[int(pick.id)], Method.fuzzy_llm, Flag.check_map, note=pick.reason)


# ==================================================================== lines
def _map_uom(ctx: MapContext, line: CanonicalLine, vendor_id: int | None) -> None:
    printed = line.uom.value
    if not printed:
        return
    uoms = ctx.ref.uoms
    vm = ctx.ref.value_map("uom", printed, vendor_id)
    if vm and vm.oracle_value in uoms:
        line.uom = _like(line.uom, vm.oracle_value, Method.value_map)
        return
    low = printed.lower().strip(". ")
    for u in uoms.values():
        if low in (u.unit_of_measure.lower(), u.uom_code.lower()):
            line.uom = _like(line.uom, u.unit_of_measure, Method.exact)
            return
    line.uom = _like(line.uom, None, Method.fuzzy, Flag.no_map, note=f"unknown unit {printed!r}")


def _map_category(ctx: MapContext, line: CanonicalLine, supplier: RefSupplier) -> None:
    desc = line.description.value
    if not desc:
        return
    code, n = history_category(ctx.session, supplier.vendor_id, desc)
    by_code = {c.code: c for c in ctx.ref.categories.values() if c.enabled}
    if code and code in by_code:
        cat = by_code[code]
        line.category_code = Field(value=cat.code, source_value=desc, method=Method.exact,
                                   note=f"{n} past line(s) of this supplier with the same text")
        line.category_id = Field(value=cat.category_id, method=Method.derived)
        return
    labels = {c.category_id: f"{c.code} {c.description}" for c in by_code.values()}
    top = process.extract(text_key(desc), labels, scorer=fuzz.token_set_ratio, limit=5)
    candidates = [{"id": str(cid), "label": f"{ctx.ref.categories[cid].code}: {ctx.ref.categories[cid].description}",
                   "score": round(sc)} for _, sc, cid in top]
    pick = choose(ctx.session, ctx.llm, document_id=ctx.doc_id, node="map.category",
                  question="Which purchasing category fits this invoice line?", value=desc, candidates=candidates,
                  context=f"supplier: {supplier.vendor_name}", line=line.line_number)
    if pick.id is None:
        line.category_code = Field(value=None, source_value=desc, method=Method.fuzzy_llm, flags=[Flag.no_map],
                                   note=pick.reason)
        return
    cat = ctx.ref.categories[int(pick.id)]
    line.category_code = Field(value=cat.code, source_value=desc, method=Method.fuzzy_llm, flags=[Flag.check_map],
                               note=pick.reason)
    line.category_id = Field(value=cat.category_id, method=Method.derived)


def _tax_code(ctx: MapContext, inv: CanonicalInvoice, supplier: RefSupplier | None) -> tuple[str | None, Decimal | None]:
    rate = inv.tax_rate.value
    sub, tax = inv.subtotal.value, inv.tax_amount.value
    if rate is None and sub and tax:
        rate = (tax / sub * 100).quantize(Decimal("0.001"))
    if rate is None:
        return None, None
    org = inv.org_id.value
    regimes = {"IN-GST"} if org == 458 else {"US-SALES-TAX"}
    if supplier is not None and inv.currency_code.value not in (None, "USD", "INR"):
        regimes = {"EU-VAT", "GB-VAT"}
    best = None
    for r in ctx.ref.tax_rates:
        if not r.active or r.tax_regime_code not in regimes or r.percentage_rate is None:
            continue
        diff = abs(Decimal(r.percentage_rate) - rate)
        if diff <= Decimal("0.05") and (best is None or diff < best[0]):
            best = (diff, r)
    return (best[1].tax_rate_code, rate) if best else (None, rate)


def map_lines(ctx: MapContext, inv: CanonicalInvoice, supplier: RefSupplier | None) -> None:
    vendor_id = supplier.vendor_id if supplier else None
    items = [ln for ln in inv.lines if ln.line_type == "ITEM"]
    for ln in items:
        _map_uom(ctx, ln, vendor_id)
        if inv.po_number.value:
            ln.po_number = Field(value=inv.po_number.value, method=Method.derived)
        elif supplier is not None:
            _map_category(ctx, ln, supplier)
    next_no = max((ln.line_number for ln in items), default=0) + 1
    extra: list[CanonicalLine] = []
    if inv.tax_amount.value:
        code, rate = _tax_code(ctx, inv, supplier)
        tax_line = CanonicalLine(line_number=next_no, line_type="TAX")
        tax_line.amount = Field(value=inv.tax_amount.value, source_value=inv.tax_amount.source_value,
                                page=inv.tax_amount.page, bbox=inv.tax_amount.bbox, method=Method.derived)
        tax_line.tax_rate = Field(value=rate, method=Method.derived)
        tax_line.tax_rate_code = Field(value=code, method=Method.exact if code else Method.derived,
                                       flags=[] if code else [Flag.no_map])
        tax_line.description = Field(value=f"Tax {code or ''}".strip(), method=Method.derived)
        extra.append(tax_line)
        next_no += 1
    if inv.freight_amount.value:
        fr = CanonicalLine(line_number=next_no, line_type="FREIGHT")
        fr.amount = Field(value=inv.freight_amount.value, source_value=inv.freight_amount.source_value,
                          method=Method.derived)
        fr.description = Field(value="Freight", method=Method.derived)
        extra.append(fr)
    inv.lines = items + extra


def _gl_date(ctx: MapContext, inv: CanonicalInvoice) -> None:
    """GL date = invoice date when its AP period is open; otherwise today (AP practice)."""
    from datetime import date as _date

    ou = ctx.ref.operating_units.get(inv.org_id.value) if inv.org_id.value else None
    d = inv.invoice_date.value
    if ou is None or d is None:
        return

    def status(day: _date) -> str | None:
        p = next((p for p in ctx.ref.periods if p.ledger_id == ou.ledger_id and p.start_date <= day <= p.end_date), None)
        return p.closing_status if p else None

    if status(d) in ("O", "F"):
        inv.gl_date = Field(value=d, method=Method.derived, note="invoice date")
        return
    today = _date.today()
    if status(today) in ("O", "F"):
        inv.gl_date = Field(value=today, method=Method.derived,
                            note=f"period of invoice date {d} is not open: GL date set to today")


# ==================================================================== entry
def map_invoice(ctx: MapContext, normalized: CanonicalInvoice) -> CanonicalInvoice:
    inv = normalized.model_copy(deep=True)
    if ctx.ref.empty:
        inv.add_flag("reference_data_missing")
        return inv
    supplier = map_supplier(ctx, inv)
    site = map_site(ctx, inv, supplier) if supplier else None
    map_org_currency(ctx, inv, supplier, site)
    map_terms(ctx, inv, supplier, site)
    map_lines(ctx, inv, supplier)
    if inv.due_date.value is None and inv.invoice_date.value and inv.terms_id.value in ctx.ref.terms:
        t = ctx.ref.terms[inv.terms_id.value]
        if t.due_days is not None:
            inv.due_date = Field(value=inv.invoice_date.value + timedelta(days=t.due_days), method=Method.derived,
                                 note=f"invoice date + {t.due_days} days ({t.name})")
    _gl_date(ctx, inv)
    if inv.invoice_num.value and inv.vendor_name.value:
        inv.description = Field(value=f"{inv.vendor_name.value[:180]} invoice {inv.invoice_num.value}"[:240],
                                method=Method.derived)
    return inv
