"""Layouts, their labels, and number/date formatting styles."""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

EN = dict(title="INVOICE", number="Invoice #", date="Date", due="Due Date", po="Your PO#", terms="Terms",
          currency="Currency", bill_to="Bill to", ship_to="Ship to", no="#", desc="Description", qty="Qty",
          uom="Unit", price="Unit Price", amount="Amount", subtotal="Subtotal", tax="Tax", freight="Freight",
          total="TOTAL DUE", payment="Payment details", tax_id="Tax ID (EIN)", phone="Tel", page="Page",
          thanks="Thank you for your business.")

LABEL_VARIANTS = [
    dict(number="Invoice No.", po="PO Number", total="Total", subtotal="Sub-total"),
    dict(number="Invoice Number", po="Purchase Order", total="Amount Due", date="Invoice Date"),
    dict(number="Invoice #", po="Your PO#", total="TOTAL DUE"),
    dict(number="Bill No.", po="Order Ref", total="Grand Total", title="TAX INVOICE"),
    dict(number="Invoice", po="Customer PO", total="Balance Due", date="Issued"),
]

DE = dict(EN, title="RECHNUNG", number="Rechnungsnummer", date="Rechnungsdatum", due="Fällig am",
          po="Ihre Bestellnummer", terms="Zahlungsbedingungen", currency="Währung", bill_to="Rechnungsempfänger",
          ship_to="Lieferadresse", no="Pos.", desc="Bezeichnung", qty="Menge", uom="Einheit", price="Einzelpreis",
          amount="Betrag", subtotal="Zwischensumme", tax="MwSt.", total="Gesamtbetrag", payment="Bankverbindung",
          tax_id="USt-IdNr.", phone="Tel.", page="Seite", thanks="Vielen Dank für Ihren Auftrag.")

IN = dict(EN, title="TAX INVOICE", number="Invoice No.", po="PO No.", tax_id="GSTIN", total="Total Amount",
          payment="Bank details", thanks="This is a computer generated invoice.")

LAYOUTS = ["classic", "modern", "compact", "letterhead", "cloud", "gst", "rechnung", "minimal", "sidebar",
           "boxed", "service"]
GENERAL_LAYOUTS = ["classic", "modern", "compact", "letterhead", "minimal", "sidebar", "boxed", "service"]
ACCENTS = ["#1f4e79", "#2e7d32", "#8e24aa", "#c62828", "#00695c", "#37474f", "#ef6c00"]


def labels_for(layout: str, country: str, rnd: random.Random) -> dict[str, str]:
    if layout == "rechnung":
        return dict(DE)
    if layout == "gst" or country == "IN":
        return dict(IN)
    labels = dict(EN)
    labels.update(rnd.choice(LABEL_VARIANTS))
    if country not in ("US",):
        labels["tax_id"] = "VAT No." if country in ("GB", "DE", "NL", "IE") else "Tax ID"
    return labels


def pick_layout(country: str, kind: str, rnd: random.Random) -> str:
    if country == "IN" and rnd.random() < 0.6:
        return "gst"
    if country == "DE" and rnd.random() < 0.6:
        return "rechnung"
    if kind == "subscription" and rnd.random() < 0.4:
        return "cloud"
    return rnd.choice(GENERAL_LAYOUTS)


# ------------------------------------------------------------ formatting
@dataclass(frozen=True)
class NumberStyle:
    thousands: str
    decimal: str
    symbol: str | None          # printed before (prefix) or after (suffix)
    suffix: bool = False

    def fmt(self, value: Decimal, *, symbol: bool = True, places: int = 2) -> str:
        q = value.quantize(Decimal(1) / (Decimal(10) ** places), rounding=ROUND_HALF_UP)
        sign = "-" if q < 0 else ""
        whole, _, frac = f"{abs(q):f}".partition(".")
        groups = []
        while len(whole) > 3:
            groups.insert(0, whole[-3:])
            whole = whole[:-3]
        groups.insert(0, whole)
        s = self.thousands.join(groups) + (self.decimal + frac if places else "")
        if symbol and self.symbol:
            s = f"{s} {self.symbol}" if self.suffix else f"{self.symbol}{s}"
        return sign + s


def number_style(currency: str, country: str, rnd: random.Random, forced: str | None = None) -> NumberStyle:
    symbol = {"USD": "$", "EUR": "€", "GBP": "£", "INR": "₹", "JPY": "¥"}.get(currency)
    if forced == "eu" or (forced is None and country in ("DE", "NL") and currency == "EUR"):
        return NumberStyle(".", ",", "€" if currency == "EUR" else symbol, suffix=True)
    if currency == "INR":
        return NumberStyle(",", ".", rnd.choice(["₹", "Rs. ", None]))
    return NumberStyle(",", ".", symbol if rnd.random() < 0.8 else None)


DATE_FORMATS_US = ["%m/%d/%Y", "%b %d, %Y", "%B %d, %Y", "%d-%b-%Y", "%Y-%m-%d"]
DATE_FORMATS_IN = ["%d/%m/%Y", "%d-%b-%Y", "%d %b %Y", "%d.%m.%Y"]
DATE_FORMATS_EU = ["%d.%m.%Y", "%d %B %Y", "%Y-%m-%d"]
DATE_FORMATS_GB = ["%d/%m/%Y", "%d %b %Y", "%d %B %Y"]


def fmt_date(d: date, country: str, rnd: random.Random, *, address_shown: bool) -> str:
    if country == "US":
        pool = DATE_FORMATS_US if address_shown else ["%b %d, %Y", "%d-%b-%Y", "%Y-%m-%d"]
    elif country == "IN":
        pool = DATE_FORMATS_IN if address_shown else ["%d-%b-%Y", "%d %b %Y"]
    elif country == "GB":
        pool = DATE_FORMATS_GB if address_shown else ["%d %b %Y"]
    else:
        pool = DATE_FORMATS_EU
    fmt = rnd.choice(pool)
    # Unambiguous numeric dates only when a country hint is printed (address or tax id).
    return d.strftime(fmt)


TERMS_TEXT = {
    "IMMEDIATE": ["Due upon receipt", "Due on receipt", "Payable immediately"],
    "7 NET": ["Net 7", "7 days net", "Payment within 7 days"],
    "10 NET": ["Net 10", "Net 10 days"],
    "15 NET": ["Net 15", "15 days"],
    "30 NET": ["Net 30", "Net 30 days from date of invoice", "30 days net", "Payment due within 30 days"],
    "45 NET": ["Net 45", "45 days"],
    "60 NET": ["Net 60", "60 days net"],
    "90 NET": ["Net 90"],
    "2/10 NET 30": ["2/10 Net 30", "2% / 10 days, net 30"],
    "1/15 NET 45": ["1/15 Net 45"],
    "NET 30 EOM": ["Net 30 EOM", "30 days end of month"],
    "50/50 30-60": ["50/50 30-60"],
}
