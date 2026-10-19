"""Stage 6 Normalize: SourceInvoice -> CanonicalInvoice with normalized values
(ISO dates, Decimal amounts, ISO currency). No Oracle values yet (stage 7)."""

from __future__ import annotations

import re
from decimal import Decimal

from aap.config import get_settings
from aap.schemas.canonical import CanonicalInvoice, CanonicalLine
from aap.schemas.fields import Field, Flag, Method
from aap.schemas.source import SourceInvoice

from .amounts import decimal_sep_for, parse_amount, parse_percent, parse_quantity
from .currency import resolve_currency
from .dates import parse_date
from .hints import country_hint


def _carry(src: Field[str], value, method: Method = Method.normalized, flags: list[str] | None = None):  # noqa: ANN001
    return Field(value=value, source_value=src.source_value, page=src.page, bbox=src.bbox, method=method,
                 flags=[*src.flags, *(flags or [])])


def _text(src: Field[str]) -> Field[str]:
    return _carry(src, " ".join(src.value.split()) if src.value else None)


def _amount(src: Field[str], sep: str | None) -> Field[Decimal]:
    if not src.present:
        return _carry(src, None)
    value = parse_amount(src.value, sep)
    return _carry(src, value, flags=[] if value is not None else [Flag.amount_parse_error])


def _terms_text(src: Field[str]) -> Field[str]:
    return _text(src)


def normalize(src: SourceInvoice) -> CanonicalInvoice:
    settings = get_settings()
    country = country_hint(vendor_address=src.vendor_address.value, vendor_tax_id=src.vendor_tax_id.value,
                           vendor_phone=src.vendor_phone.value, currency_text=src.currency.value,
                           remit_to=src.remit_to.value)
    sep = decimal_sep_for(country)
    inv = CanonicalInvoice(country_hint=country)
    inv.flags = list(src.flags)

    inv.invoice_type = Field(value="STANDARD", method=Method.default)
    inv.source = Field(value=settings.invoice_source, method=Method.default)
    inv.vendor_name = _text(src.vendor_name)
    inv.vendor_tax_id = _carry(src.vendor_tax_id, (src.vendor_tax_id.value or "").upper().replace(" ", "") or None)
    inv.vendor_email = _text(src.vendor_email)
    inv.vendor_address = _text(src.vendor_address)
    inv.invoice_num = _carry(src.invoice_number, (src.invoice_number.value or "").strip() or None)
    inv.po_number = _carry(src.po_number, _po(src.po_number.value))
    inv.terms_name = _terms_text(src.payment_terms)   # free text until stage 7 maps it
    inv.description = Field(value=None, method=Method.default)

    for name, target in (("invoice_date", "invoice_date"), ("due_date", "due_date")):
        f = getattr(src, name)
        if f.present:
            value, ambiguous = parse_date(f.value, country)
            flags = []
            if ambiguous:
                flags.append(Flag.two_date_formats)
            if value is None:
                flags.append(Flag.date_parse_error)
            setattr(inv, target, _carry(f, value, flags=flags))
        else:
            setattr(inv, target, _carry(f, None))
    inv.gl_date = Field(value=inv.invoice_date.value, method=Method.derived)

    code, candidates = resolve_currency(src.currency.value, country=country)
    if code is None:
        for f in (src.total, src.subtotal):
            code, candidates = resolve_currency(f.value, country=country)
            if code:
                break
    cur_flags = [Flag.currency_ambiguous] if len(candidates) > 1 else []
    inv.currency_code = _carry(src.currency, code, flags=cur_flags)
    if candidates:
        inv.currency_code.note = "candidates:" + ",".join(candidates)

    inv.subtotal = _amount(src.subtotal, sep)
    inv.tax_amount = _amount(src.tax_amount, sep)
    inv.freight_amount = _amount(src.freight_amount, sep)
    inv.invoice_amount = _amount(src.total, sep)
    rate_src = src.tax_rate if src.tax_rate.present else src.tax_label
    inv.tax_rate = _carry(rate_src, parse_percent(rate_src.value))

    inv.bank_account = _carry(src.bank_account, _digits(src.bank_account.value))
    inv.bank_routing = _carry(src.bank_routing, _digits(src.bank_routing.value))
    inv.iban = _carry(src.iban, (src.iban.value or "").replace(" ", "").upper() or None)

    lines: list[CanonicalLine] = []
    for ln in src.lines:
        cl = CanonicalLine(line_number=ln.line_no, line_type="ITEM")
        cl.description = _text(ln.description)
        cl.item_code = _text(ln.item_code)
        cl.quantity = _carry(ln.quantity, parse_quantity(ln.quantity.value)) if ln.quantity.present else _carry(ln.quantity, None)
        cl.unit_price = _amount(ln.unit_price, sep)
        cl.amount = _amount(ln.amount, sep)
        cl.uom = _text(ln.uom)
        cl.tax_rate = _carry(ln.tax_rate, parse_percent(ln.tax_rate.value))
        cl.po_line_number = _carry(ln.po_line_ref, _int(ln.po_line_ref.value))
        if cl.amount.value is None and cl.quantity.value is not None and cl.unit_price.value is not None:
            cl.amount = Field(value=(cl.quantity.value * cl.unit_price.value).quantize(Decimal("0.01")),
                              method=Method.derived)
        lines.append(cl)
    inv.lines = lines

    if inv.subtotal.value is None and lines and all(ln.amount.value is not None for ln in lines):
        inv.subtotal = Field(value=sum((ln.amount.value for ln in lines), Decimal(0)), method=Method.derived)
    if inv.tax_amount.value is None and not src.tax_amount.present:
        inv.tax_amount = Field(value=Decimal(0), method=Method.default, note="no tax printed")
    return inv


def _po(value: str | None) -> str | None:
    if not value:
        return None
    m = re.search(r"\d{4,}", value)
    return m.group(0) if m else value.strip()


def _digits(value: str | None) -> str | None:
    if not value:
        return None
    d = re.sub(r"\D", "", value)
    return d or None


def _int(value: str | None) -> int | None:
    if not value:
        return None
    m = re.search(r"\d+", value)
    return int(m.group(0)) if m else None
