"""Stage 6 Normalize: SourceInvoice -> CanonicalInvoice with normalized values
(ISO dates, Decimal amounts, ISO currency). No Oracle values yet (stage 7)."""

from __future__ import annotations

import re
from decimal import Decimal

from aap.config import get_settings
from aap.schemas.canonical import CanonicalInvoice, CanonicalLine, CanonicalTax
from aap.schemas.fields import Field, Flag, Method
from aap.schemas.source import SourceInvoice

from .amounts import decimal_sep_for, parse_amount, parse_percent, parse_quantity
from .currency import resolve_currency
from .dates import parse_date
from .hints import country_hint


def _carry(src: Field[str], value, method: Method = Method.normalized, flags: list[str] | None = None,  # noqa: ANN001
           note_text: str | None = None):
    return Field(value=value, source_value=src.source_value, page=src.page, bbox=src.bbox, method=method,
                 flags=[*src.flags, *(flags or [])], note=note_text)


def _text(src: Field[str]) -> Field[str]:
    return _carry(src, " ".join(src.value.split()) if src.value else None)


def _amount(src: Field[str], sep: str | None) -> Field[Decimal]:
    if not src.present:
        return _carry(src, None)
    value = parse_amount(src.value, sep)
    return _carry(src, value, flags=[] if value is not None else [Flag.amount_parse_error])


def _terms_text(src: Field[str]) -> Field[str]:
    return _text(src)


# Supplier documents that lower what we owe. A supplier "debit note" is an extra charge,
# so it is a STANDARD invoice in Payables (an EBS Debit Memo is raised by the buyer).
CREDIT_TITLE = re.compile(r"(?i)\b(credit\s*(memo|note)|gutschrift|avoir|note\s+de\s+cr[ée]dit)\b")
DEBIT_TITLE = re.compile(r"(?i)\b(debit\s*(memo|note)|note\s+de\s+d[ée]bit|belastungsanzeige)\b")
# Unit price printed per 1000 units ("$56.21 /M") or in cents ("15,99491 ¢/m³").
# "/M" is case-sensitive: "/m" is per metre.
PER_THOUSAND = re.compile(r"/\s*M\b|(?i:\bper\s*(1,?000|thousand|mille)\b)|/\s*1,?000\b|\bCPM\b")
CENTS = re.compile(r"(?i)(¢|\bcents?\b)")
# "handling" alone is not enough ("material handling equipment"); "freight and handling" is.
FREIGHT_LINE = re.compile(r"(?i)\b(freight|shipping|delivery\s+charges?|carriage|fracht|versand(kosten)?)\b")


def normalize(src: SourceInvoice) -> CanonicalInvoice:
    settings = get_settings()
    country = country_hint(vendor_address=src.vendor_address.value, vendor_tax_id=src.vendor_tax_id.value,
                           vendor_phone=src.vendor_phone.value, currency_text=src.currency.value,
                           remit_to=src.remit_to.value,
                           tax_labels=[src.tax_label.value, *(t.label.value for t in src.taxes)])
    sep = decimal_sep_for(country)
    inv = CanonicalInvoice(country_hint=country)
    inv.flags = list(src.flags)

    inv.invoice_type = _invoice_type(src.document_type)
    inv.source = Field(value=settings.invoice_source, method=Method.default)
    inv.location_ref = _text(src.location_ref)
    inv.bank_change_notice = _text(src.bank_change_notice)
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
    inv.tax_rate = _rate(src.tax_rate, src.tax_label)
    inv.taxes = _tax_rows(src, sep)

    inv.bank_account = _bank(src.bank_account)
    inv.bank_routing = _bank(src.bank_routing, us_routing=country == "US")
    inv.iban = _iban(src.iban)

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
        if cl.quantity.value is None and cl.unit_price.value is not None and cl.unit_price.value == cl.amount.value:
            # amount-only line: the model repeated the amount as the unit price (eval 2026-10-26)
            cl.unit_price = _carry(ln.unit_price, None, note_text="same as the amount, no quantity printed: not a price")
        _price_basis(cl, ln.unit_price.value, ln.uom.value)
        if cl.description.value and FREIGHT_LINE.search(cl.description.value):
            cl.line_type = "FREIGHT"
        if cl.amount.value is None and cl.quantity.value is not None and cl.unit_price.value is not None:
            cl.amount = Field(value=(cl.quantity.value * cl.unit_price.value).quantize(Decimal("0.01")),
                              method=Method.derived)
        lines.append(cl)
    inv.lines = lines

    if inv.subtotal.value is None and lines and all(ln.amount.value is not None for ln in lines):
        inv.subtotal = Field(value=sum((ln.amount.value for ln in lines), Decimal(0)), method=Method.derived)
    _total_tax(inv)
    if inv.tax_amount.value is None and not src.tax_amount.present and not inv.taxes:
        inv.tax_amount = Field(value=Decimal(0), method=Method.default, note="no tax printed")
    _apply_sign(inv)
    return inv


def _invoice_type(title: Field[str]) -> Field[str]:
    text = title.value or ""
    if CREDIT_TITLE.search(text):
        return _carry(title, "CREDIT", note_text=f"document title {text!r}")
    if DEBIT_TITLE.search(text):
        return _carry(title, "STANDARD", note_text="supplier debit note: an extra charge, entered as a standard invoice")
    if text:
        return _carry(title, "STANDARD")
    return Field(value="STANDARD", method=Method.default)


def _apply_sign(inv: CanonicalInvoice) -> None:
    """A credit memo is negative in Payables. Suppliers usually print it with positive amounts."""
    total = inv.invoice_amount.value
    if inv.invoice_type.value != "CREDIT":
        if total is not None and total < 0:
            inv.invoice_type = Field(value="CREDIT", source_value=inv.invoice_type.source_value, method=Method.derived,
                                     note="negative invoice total")
        return
    if total is None or total <= 0:
        return
    fields = [inv.subtotal, inv.tax_amount, inv.freight_amount, inv.invoice_amount,
              *(t.amount for t in inv.taxes), *(ln.amount for ln in inv.lines)]
    for f in fields:
        if f.value:
            f.value = -abs(f.value)
            f.with_flag(Flag.sign_from_document_type)
    for ln in inv.lines:
        if ln.quantity.value:
            ln.quantity.value = -abs(ln.quantity.value)
    inv.invoice_amount.note = "credit memo printed with positive amounts: amounts made negative"


def _rate(rate: Field[str], label: Field[str]) -> Field[Decimal]:
    """Tax rate printed in the rate field or in the label. A printed rate carries "%"; a rate without
    one, or one the source check did not find on the page, was computed by the model and is not used
    (the tax-maths check needs a printed rate). Énergir sample: "12" for GST + QST."""
    if rate.present and "%" in rate.value and Flag.not_found_in_source not in rate.flags:
        return _carry(rate, parse_percent(rate.value))
    out = _carry(label, parse_percent(label.value) if label.value and "%" in label.value else None)
    if rate.present:
        out.note = f"rate {rate.value!r} is not printed on the invoice: not used"
    return out


def _tax_rows(src: SourceInvoice, sep: str | None) -> list[CanonicalTax]:
    """Printed tax rows, each once: a tax shown both in a tax summary table and in the totals
    (same label and amount) is one tax."""
    out: list[CanonicalTax] = []
    seen: set[tuple[str, Decimal | None]] = set()
    for t in src.taxes:
        row = CanonicalTax(tax_no=len(out) + 1, label=_text(t.label), rate=_rate(t.rate, t.label),
                           amount=_amount(t.amount, sep))
        key = (re.sub(r"\W+", "", (row.label.value or "").lower()), row.amount.value)
        if row.amount.value is None or key in seen or (not key[0] and any(a == key[1] for _, a in seen)):
            continue
        seen.add(key)
        out.append(row)
    return out


def _total_tax(inv: CanonicalInvoice) -> None:
    """Several printed tax rows (e.g. GST + QST): tax_amount is their sum. A printed tax amount is
    replaced only when it is one of the rows and the sum, not it, makes subtotal + tax + freight = total."""
    amounts = [t.amount.value for t in inv.taxes]
    if not amounts:
        return
    total = sum(amounts, Decimal(0))
    current = inv.tax_amount.value
    note = f"sum of {len(amounts)} tax rows" if len(amounts) > 1 else "tax row"
    if current is None:
        inv.tax_amount = Field(value=total, method=Method.derived, note=note)
    elif len(amounts) > 1 and current != total and current in amounts:
        sub, grand = inv.subtotal.value, inv.invoice_amount.value
        freight = inv.freight_amount.value or Decimal(0)
        tol = get_settings().amount_tolerance
        if sub is not None and grand is not None and abs(sub + total + freight - grand) <= tol:
            inv.tax_amount = Field(value=total, source_value=inv.tax_amount.source_value, method=Method.derived,
                                   note=f"{note} (the printed {current} is one of them)")
    if inv.tax_rate.value is None and len(inv.taxes) == 1 and inv.taxes[0].rate.value is not None:
        inv.tax_rate = inv.taxes[0].rate.model_copy()


def _price_basis(cl: CanonicalLine, price_text: str | None, uom_text: str | None) -> None:
    """Unit price per 1000 units or in cents -> per unit, so quantity x price = amount.
    From a printed marker ("/M", "¢") or, failing that, from quantity x price being 100x / 1000x the amount."""
    qty, price, amount = cl.quantity.value, cl.unit_price.value, cl.amount.value
    if price is None:
        return
    tol = get_settings().amount_tolerance
    if qty is not None and amount is not None and abs((qty * price).quantize(Decimal("0.01")) - amount) <= tol:
        return                                  # already per unit
    marker = " ".join(t for t in (price_text, uom_text) if t)
    basis = 1000 if PER_THOUSAND.search(marker) else 100 if CENTS.search(marker) else None
    how = "printed"
    if basis is None and qty is not None and amount is not None:
        basis = next((b for b in (100, 1000)
                      if abs((qty * price / b).quantize(Decimal("0.01")) - amount) <= tol), None)
        how = "inferred from quantity x price vs amount"
    if basis is None:
        return
    unit = "1000 units" if basis == 1000 else "cents"
    cl.unit_price = Field(value=price / basis, source_value=cl.unit_price.source_value, page=cl.unit_price.page,
                          bbox=cl.unit_price.bbox, method=Method.derived, flags=[*cl.unit_price.flags, Flag.price_basis],
                          note=f"price per {unit} ({how}): {price} / {basis}")
    if uom_text and PER_THOUSAND.fullmatch(uom_text.strip()):
        cl.uom = Field(value=None, source_value=uom_text, method=Method.derived, note="price basis, not a unit")


def _bank(src: Field[str], *, us_routing: bool = False) -> Field[str]:
    """Digits of a bank number. All zeros or fewer than 5 digits is not a bank number (the model read
    a reference or a blank form field): dropped and flagged. us_routing: an all-digit routing number on
    a US invoice must be an ABA number (9 digits, checksum); Mspark sample: the phone number was read."""
    d = _digits(src.value)
    if d and (set(d) == {"0"} or len(d) < 5):
        return _carry(src, None, flags=[Flag.implausible_value], note_text=f"{src.value!r} is not a bank number")
    if d and us_routing and re.fullmatch(r"[\d\s().-]+", src.value or "") and not _aba_ok(d):
        return _carry(src, None, flags=[Flag.implausible_value], note_text=f"{src.value!r} is not a US routing number")
    return _carry(src, d)


def _iban(src: Field[str]) -> Field[str]:
    """An IBAN is 2 letters, 2 check digits and 8-30 letters or digits. Anything else (Wasserstrom
    sample: "0") is not one: dropped and flagged. The checksum is left to C11, so a misread IBAN still
    reaches the bank check."""
    v = (src.value or "").replace(" ", "").upper() or None
    if v and not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{8,30}", v):
        return _carry(src, None, flags=[Flag.implausible_value], note_text=f"{src.value!r} is not an IBAN")
    return _carry(src, v)


def _aba_ok(d: str) -> bool:
    if len(d) != 9:
        return False
    n = [int(c) for c in d]
    return (3 * (n[0] + n[3] + n[6]) + 7 * (n[1] + n[4] + n[7]) + n[2] + n[5] + n[8]) % 10 == 0


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
