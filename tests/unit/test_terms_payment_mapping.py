"""Terms matching against a client-style term list (plan B8 / P3.2) and the payment defaults of the
Oracle-ready JSON (map_payment). Term names follow Oracle's usual wording; nothing here is client data."""

from datetime import date, datetime
from decimal import Decimal

from aap.db.models import Document, RefPaymentMethod, RefSupplier, RefSupplierSite, RefTerms
from aap.mapping.map import MapContext, _parse_terms, map_payment, map_terms, terms_family
from aap.mapping.reference import Reference
from aap.schemas.canonical import CanonicalInvoice
from aap.schemas.fields import Field, Flag, Method

D = Decimal


def _term(term_id, name, days=None, eom=None, pct=None, disc_days=None):
    return RefTerms(term_id=term_id, name=name, description=name, enabled=True, due_days=days, due_day_of_month=eom,
                    discount_percent=pct, discount_days=disc_days, installments=1)


TERMS = [_term(1, "Immediate", 0), _term(2, "CONTADO", 0), _term(3, "30 NET", 30), _term(4, "30 DIAS", 30),
         _term(5, "60 NET", 60), _term(6, "60 DIAS", 60), _term(7, "15 EOM", eom=15), _term(8, "30 EOM", eom=30),
         _term(9, "1 EOM", eom=1), _term(10, "30 NET 2%10", 30, pct=D(2), disc_days=10),
         _term(11, "30 NET 1.5%10", 30, pct=D("1.5"), disc_days=10)]


def _ref(methods=("CHECK", "EFT")) -> Reference:
    ref = Reference(operating_units={}, suppliers={}, sites={}, sites_by_vendor={}, banks_by_vendor={},
                    terms={t.term_id: t for t in TERMS}, currencies={}, uoms={}, categories={}, tax_rates=[],
                    tolerances={}, periods=[])
    ref.payment_methods = {m: RefPaymentMethod(payment_method_code=m, name=m, enabled=True) for m in methods}
    return ref


def _ctx(ref=None, document=None) -> MapContext:
    return MapContext(session=None, llm=None, ref=ref or _ref(), document=document)  # type: ignore[arg-type]


def _inv(printed=None, due=None, invoice=date(2026, 10, 1)) -> CanonicalInvoice:
    inv = CanonicalInvoice()
    inv.terms_name = Field(value=printed, source_value=printed)
    inv.invoice_date = Field(value=invoice)
    inv.due_date = Field(value=due, source_value=str(due) if due else None)
    inv.currency_code = Field(value="USD")
    return inv


class _Supplier:
    def __init__(self, terms_id):
        self.vendor_id, self.terms_id = 7, terms_id


def _terms(printed=None, due=None, default=3) -> CanonicalInvoice:
    inv = _inv(printed, due)
    map_terms(_ctx(), inv, _Supplier(default), None)  # type: ignore[arg-type]
    return inv


# ------------------------------------------------------------------- terms
def test_family_of_a_term_name():
    assert terms_family("30 NET") == "NET" and terms_family("30 DIAS") == "DIAS"
    assert terms_family("30 NET 2%10") == "NET" and terms_family("15 EOM") == "EOM"


def test_same_days_take_the_supplier_default_family():
    assert _terms("Net 30", default=3).terms_name.value == "30 NET"
    assert _terms("30 dias", default=4).terms_name.value == "30 DIAS"
    assert _terms("Payable within 60 days", default=6).terms_name.value == "60 DIAS"
    # only a due date printed: 60 days, supplier on DIAS terms
    assert _terms(due=date(2026, 11, 30), default=4).terms_name.value == "60 DIAS"
    assert _terms(due=date(2026, 11, 30), default=3).terms_name.value == "60 NET"


def test_contado_and_immediate():
    assert _terms("Contado", default=4).terms_name.value == "CONTADO"
    assert _terms("Pago de contado", default=4).terms_name.value == "CONTADO"
    assert _terms("Pago inmediato", default=2).terms_name.value == "CONTADO"     # supplier on CONTADO
    assert _terms("Due upon receipt", default=3).terms_name.value == "Immediate"


def test_end_of_month_terms_by_day():
    for printed, expected in [("15 EOM", "15 EOM"), ("Net 15 EOM", "15 EOM"), ("EOM 15", "15 EOM"),
                              ("Due 15th after month end", "15 EOM"), ("30th of the following month", "30 EOM"),
                              ("1st of next month", "1 EOM")]:
        inv = _terms(printed)
        assert inv.terms_name.value == expected, printed
    # no term on the 20th: not parsed (the model chooses among the candidates), never another day
    assert _parse_terms(_ref(), "Net 20 EOM", None) is None


def test_discount_terms_in_several_wordings():
    for printed in ("2/10 Net 30", "2% 10 Net 30", "Net 30, 2% 10 days", "30 NET 2%10",
                    "Net due in 30 days 2% discount in 10 days"):
        assert _terms(printed).terms_name.value == "30 NET 2%10", printed
    assert _terms("1.5% 10 net 30").terms_name.value == "30 NET 1.5%10"


# ----------------------------------------------------------------- payment
def _supplier(**kw) -> RefSupplier:
    values = dict(vendor_id=7, vendor_name="Example Co", name_key="example co", vendor_number="22001", enabled=True,
                  payment_method="CHECK", pay_group="STANDARD")
    values.update(kw)
    return RefSupplier(**values)


def _site(**kw) -> RefSupplierSite:
    values = dict(vendor_site_id=70, vendor_id=7, vendor_site_code="MAIN", org_id=204, pay_site=True,
                  purchasing_site=True, primary_pay_site=True)
    values.update(kw)
    return RefSupplierSite(**values)


def test_payment_defaults_come_from_the_site_then_the_supplier():
    inv = _inv()
    map_payment(_ctx(), inv, _supplier(), _site(payment_method="EFT"))
    assert inv.payment_method.value == "EFT" and inv.payment_method.method == Method.default
    assert inv.pay_group.value == "STANDARD" and "supplier default" in inv.pay_group.note
    assert inv.payment_currency_code.value == "USD" and inv.terms_date.value == date(2026, 10, 1)
    inv = _inv()
    map_payment(_ctx(), inv, _supplier(payment_method=None), _site())
    assert inv.payment_method.value is None and Flag.missing in inv.payment_method.flags


def test_unknown_payment_method_is_flagged():
    inv = _inv()
    map_payment(_ctx(_ref(methods=("EFT",))), inv, _supplier(payment_method="WIRE"), _site())
    assert Flag.no_map in inv.payment_method.flags


def test_site_payment_currency_and_terms_date_basis():
    doc = Document(created_at=datetime(2026, 10, 5, 9, 30))
    inv = _inv()
    map_payment(_ctx(document=doc), inv, _supplier(),
                _site(payment_currency_code="CAD", terms_date_basis="Invoice Received"))
    assert inv.payment_currency_code.value == "CAD" and inv.terms_date.value == date(2026, 10, 5)
    inv = _inv()
    map_payment(_ctx(), inv, _supplier(), _site(terms_date_basis="Goods Received"))
    assert inv.terms_date.value == date(2026, 10, 1)        # no receipt on a non-PO invoice


# ------------------------------------------- line types (Vikram's examples, AI AP Excel.xlsx 2026-11-04)
def test_freight_lines_from_their_text():
    from aap.normalize.normalize import FREIGHT_LINE

    for text in ("Freight Charges", "Shipping Charges", "Delivery Charges", "Courier Charges", "DHL Courier Charges",
                 "FedEx Freight Cost", "Flete", "Cargo por flete", "Delivery fee"):
        assert FREIGHT_LINE.search(text), text
    for text in ("Consulting Services", "Cloud Services", "Software Subscription", "Office Supplies",
                 "AWS Cloud Services", "Equipment Purchase", "Courier bags 50 pack", "Material handling equipment",
                 "Environmental Fee", "Admin Fee"):
        assert not FREIGHT_LINE.search(text), text


def test_vikram_terms_wordings():
    for printed, expected in [("Net 30", "30 NET"), ("NET30", "30 NET"), ("30 Days", "30 NET"),
                              ("Due in 30 Days", "30 NET"), ("Immediate", "Immediate"),
                              ("Cash On Delivery", "Immediate"), ("Due in 60 Days", "60 NET")]:
        assert _terms(printed).terms_name.value == expected, printed


# -------------------------------------------------- TERMS_SOURCE=site_default
def _site_default(monkeypatch):
    from aap.config import get_settings

    monkeypatch.setenv("TERMS_SOURCE", "site_default")
    get_settings.cache_clear()


def test_site_default_terms_win_over_printed_ones(monkeypatch):
    _site_default(monkeypatch)
    try:
        inv = _terms("Net 60", default=3)
        assert inv.terms_id.value == 3 and inv.terms_id.method == Method.default
        assert "the invoice suggests 60 NET" in (inv.terms_id.note or "")
        same = _terms("Net 30", default=3)
        assert same.terms_id.value == 3 and "suggests" not in (same.terms_id.note or "")
        by_due = _terms(None, due=date(2026, 11, 30), default=3)          # 60 days after the invoice date
        assert by_due.terms_id.value == 3 and "60 NET" in (by_due.terms_id.note or "")
        unmatched = _terms("pay whenever", default=3)                     # no model call in this mode
        assert unmatched.terms_id.value == 3 and "not matched" in (unmatched.terms_id.note or "")
    finally:
        from aap.config import get_settings

        get_settings.cache_clear()


def test_site_default_mode_without_a_default_falls_back_to_the_printed_terms(monkeypatch):
    _site_default(monkeypatch)
    try:
        inv = _terms("Net 60", default=None)
        assert inv.terms_id.value == 5
    finally:
        from aap.config import get_settings

        get_settings.cache_clear()
