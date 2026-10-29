"""Rules added for the real sample invoices (docs/PLAN-REAL-INVOICES.md, R1.3-R1.8)."""

from datetime import date
from decimal import Decimal

from aap.db.models import RefTerms, ValueMap
from aap.mapping.map import MapContext, map_lines, map_location, map_terms
from aap.mapping.reference import Reference
from aap.normalize.currency import resolve_currency
from aap.normalize.hints import country_hint
from aap.normalize.normalize import normalize
from aap.schemas.canonical import CanonicalInvoice, CanonicalLine, CanonicalTax
from aap.schemas.fields import Field, Flag, Method
from aap.schemas.source import ExtractedInvoice, SourceInvoice
from aap.validation.engine import RuleContext
from aap.validation.rules.r02_maths import lines_subtotal, tax_maths
from aap.validation.rules.r07_bank import bank_details
from aap.validation.rules.r13_invoice_type import invoice_type_sign

D = Decimal


# ------------------------------------------------------------------ helpers
def _source(lines=(), taxes=(), **header) -> SourceInvoice:
    base = {k: None for k in ExtractedInvoice.model_fields if k not in ("lines", "taxes")}
    base.update(header)
    line = dict(description=None, item_code=None, quantity=None, uom=None, unit_price=None, amount=None,
                tax_rate=None, po_number=None, po_line_ref=None)
    return SourceInvoice.from_extracted(ExtractedInvoice.model_validate(
        {**base, "lines": [{**line, **ln} for ln in lines],
         "taxes": [{"label": None, "rate": None, "amount": None, **t} for t in taxes]}))


def _term(term_id: int, name: str, days: int | None, **kw) -> RefTerms:
    return RefTerms(term_id=term_id, name=name, description=name, enabled=True, due_days=days,
                    due_day_of_month=kw.get("eom"), discount_percent=kw.get("pct"), discount_days=kw.get("disc_days"),
                    installments=1)


def _ref(value_maps=None) -> Reference:
    terms = [_term(1, "IMMEDIATE", 0), _term(2, "30 NET", 30), _term(3, "60 NET", 60), _term(4, "45 NET", 45),
             _term(5, "2/10 NET 30", 30, pct=D(2), disc_days=10)]
    ref = Reference(operating_units={}, suppliers={}, sites={}, sites_by_vendor={}, banks_by_vendor={},
                    terms={t.term_id: t for t in terms}, currencies={}, uoms={}, categories={}, tax_rates=[],
                    tolerances={}, periods=[])
    for vm in value_maps or []:
        ref.value_maps[(vm.map_type, vm.source_key, vm.vendor_id)] = vm
    return ref


def _ctx(ref: Reference) -> MapContext:
    return MapContext(session=None, llm=None, ref=ref, document=None)  # type: ignore[arg-type]


class _Supplier:
    vendor_id, terms_id = 7, 4      # site default: 45 NET


def _terms_inv(*, printed=None, printed_flags=(), invoice=date(2026, 2, 27), due=None) -> CanonicalInvoice:
    inv = CanonicalInvoice()
    inv.terms_name = Field(value=printed, source_value=printed, flags=list(printed_flags))
    inv.invoice_date = Field(value=invoice)
    inv.due_date = Field(value=due, source_value=str(due) if due else None)
    return inv


def _rule_ctx(inv: CanonicalInvoice) -> RuleContext:
    return RuleContext(inv=inv, session=None, ref=_ref(), adapter=None, document=None,  # type: ignore[arg-type]
                       today=date(2026, 10, 26))


# ------------------------------------------------------------- R1.3 terms
def test_terms_from_due_date_when_none_printed():                 # Level Access: Feb 27 -> Apr 28
    inv = _terms_inv(due=date(2026, 4, 28))
    map_terms(_ctx(_ref()), inv, None, None)
    assert inv.terms_name.value == "60 NET" and inv.terms_id.method == Method.derived


def test_unverified_printed_terms_give_way_to_due_date():         # model wrote "Net 30", not on the page
    inv = _terms_inv(printed="Net 30", printed_flags=[Flag.not_found_in_source], due=date(2026, 4, 28))
    map_terms(_ctx(_ref()), inv, None, None)
    assert inv.terms_name.value == "60 NET" and "not found on the page" in inv.terms_id.note


def test_label_read_as_terms_uses_due_date():                     # Mspark-style label instead of terms
    inv = _terms_inv(printed="Please Pay This Amount", invoice=date(2021, 7, 6), due=date(2021, 8, 5))
    map_terms(_ctx(_ref()), inv, None, None)
    assert inv.terms_name.value == "30 NET"


def test_due_date_without_matching_term_uses_supplier_default():  # Mspark: 17 days
    inv = _terms_inv(invoice=date(2021, 7, 6), due=date(2021, 7, 23))
    map_terms(_ctx(_ref()), inv, _Supplier(), None)  # type: ignore[arg-type]
    assert inv.terms_name.value == "45 NET" and Flag.check_map in inv.terms_id.flags
    inv = _terms_inv(invoice=date(2021, 7, 6), due=date(2021, 7, 23))
    map_terms(_ctx(_ref()), inv, None, None)
    assert inv.terms_id.value is None and Flag.no_map in inv.terms_id.flags
    # the model's "Net 30" is not on the page and no term fits: it does not stay as the terms name
    inv = _terms_inv(printed="Net 30", printed_flags=[Flag.not_found_in_source], invoice=date(2021, 7, 6),
                     due=date(2021, 7, 23))
    map_terms(_ctx(_ref()), inv, None, None)
    assert inv.terms_name.value is None and inv.terms_name.source_value == "Net 30"


def test_printed_terms_win_but_conflict_is_flagged():
    inv = _terms_inv(printed="Net 30", due=date(2026, 4, 28))
    map_terms(_ctx(_ref()), inv, None, None)
    assert inv.terms_name.value == "30 NET" and Flag.terms_due_date_conflict in inv.terms_id.flags
    inv = _terms_inv(printed="2/10 Net 30", due=date(2026, 3, 29))
    map_terms(_ctx(_ref()), inv, None, None)
    assert inv.terms_name.value == "2/10 NET 30" and not inv.terms_id.flags


# ------------------------------------------------------- R1.6 normalize
def test_price_per_thousand_and_cents():
    src = _source(lines=[
        {"description": "TNB01818: NC-HVL/28792", "quantity": "16,408", "unit_price": "$56.21^ /M", "amount": "$922.29"},
        {"description": "Gaz naturel fourni", "quantity": "589", "unit_price": "15,99491 ¢/m³", "amount": "94,21 $"},
        {"description": "Distribution", "quantity": "29", "unit_price": "138,44800", "amount": "40,15 $"},  # inferred
        {"description": "Cable", "quantity": "10", "uom": "/m", "unit_price": "2.50", "amount": "25.00"},
    ], total="$1,082.10")
    inv = normalize(src)
    a, b, c, d = inv.lines
    assert a.unit_price.value == D("0.05621") and Flag.price_basis in a.unit_price.flags
    assert b.unit_price.value == D("0.1599491") and "cents" in b.unit_price.note
    assert c.unit_price.value == D("1.384480") and "inferred" in c.unit_price.note
    assert d.unit_price.value == D("2.50") and Flag.price_basis not in d.unit_price.flags


def test_subtotal_that_is_a_line_amount_gives_way_to_the_lines():  # Mspark, prompt v5: no subtotal row
    lines = [{"description": "TNB01818: NC-HVL/28792", "amount": "$922.29"},
             {"description": "TNB01818: NC-HVL/28791", "amount": "$380.32"}]
    inv = normalize(_source(lines=lines, subtotal="$922.29", tax_amount="$12.91", total="$1,315.52"))
    assert inv.subtotal.value == D("1302.61") and Flag.implausible_value in inv.subtotal.flags
    assert inv.subtotal.source_value == "$922.29"
    # a printed subtotal that fits stays, also when the lines do not add up to it (C3 reports that)
    inv = normalize(_source(lines=lines, subtotal="$1,302.61", tax_amount="$12.91", total="$1,315.52"))
    assert inv.subtotal.method == Method.normalized
    inv = normalize(_source(lines=lines, subtotal="$1,300.00", tax_amount="$15.52", total="$1,315.52"))
    assert inv.subtotal.value == D("1300.00")
    # a carrier's invoice is all freight rows: they are in the subtotal (eval dev-55, R0017_30136)
    freight = [{"description": "Parcel Freight Tier 2", "amount": "1,307.73"},
               {"description": "Express Freight Standard", "amount": "1,281.68"}]
    inv = normalize(_source(lines=freight, subtotal="2,589.41", total="2,589.41"))
    assert inv.subtotal.value == D("2589.41") and Flag.implausible_value not in inv.subtotal.flags


def test_several_taxes_add_up():                                   # Énergir: GST + QST
    src = _source(lines=[{"description": "Volume", "amount": "431,47 $"}], subtotal="431,47 $", total="496,08 $",
                  tax_amount="21,57 $", taxes=[{"label": "TPS (n° 121411813)", "amount": "21,57 $"},
                                               {"label": "TVQ (n° 1010997280)", "amount": "43,04 $"}],
                  vendor_address="C.P. 6115, Succ. Centre-Ville, Montréal Qc H3C 4N7", currency="$")
    inv = normalize(src)
    assert inv.tax_amount.value == D("64.61") and inv.tax_amount.method == Method.derived
    assert inv.country_hint == "CA" and inv.currency_code.value == "CAD"


def test_same_tax_listed_twice_is_not_doubled():                   # Mspark: summary table + totals row
    lines = [{"description": "Insert A", "amount": "$922.29"}, {"description": "Insert B", "amount": "$380.32"}]
    src = _source(lines=lines, total="$1,315.52", tax_amount="$12.91",
                  taxes=[{"label": "Sales/Use Tax", "amount": "$12.91"}, {"label": "Sales Tax", "amount": "$12.91"},
                         {"label": "Sales Tax", "amount": "$12.91"}])
    inv = normalize(src)
    assert len(inv.taxes) == 2 and inv.tax_amount.value == D("12.91")


def test_canada_hints():
    assert country_hint(vendor_address="1560 BOUL LEBOURGNEUF QUEBEC QC G2K 2M4") == "CA"
    assert country_hint(tax_labels=["TVQ"]) == "CA"
    assert country_hint(tax_labels=["GST"]) is None                 # also Indian
    assert country_hint(vendor_address="Stafford VA 22554") == "US"
    assert resolve_currency("$", country="CA")[0] == "CAD"


def test_credit_memo_printed_positive_becomes_negative():
    src = _source(document_type="CREDIT MEMO", total="$100.00", subtotal="$100.00",
                  lines=[{"description": "Return", "quantity": "2", "unit_price": "50.00", "amount": "100.00"}])
    inv = normalize(src)
    assert inv.invoice_type.value == "CREDIT"
    assert inv.invoice_amount.value == D("-100.00") and Flag.sign_from_document_type in inv.invoice_amount.flags
    assert inv.lines[0].amount.value == D("-100.00") and inv.lines[0].quantity.value == D("-2")
    assert normalize(_source(document_type="Debit Note", total="10.00")).invoice_type.value == "STANDARD"
    assert normalize(_source(total="-25.00")).invoice_type.value == "CREDIT"
    assert normalize(_source(document_type="Invoice", total="25.00")).invoice_type.value == "STANDARD"


def test_implausible_bank_numbers_dropped():                       # Wasserstrom: "000"
    inv = normalize(_source(bank_account="000", bank_routing="122105980", total="1.00"))
    assert inv.bank_account.value is None and Flag.implausible_value in inv.bank_account.flags
    assert inv.bank_routing.value == "122105980"


def test_freight_row_and_unprinted_rate():
    src = _source(lines=[{"description": "DISPLAY CASE", "amount": "2,574.18"},
                         {"description": "OUTBOUND FREIGHT AND HANDLING", "amount": "202.50"}],
                  total="2,776.68", tax_label="Sales Tax", tax_rate="8%", tax_amount="12.91")
    src.tax_rate.with_flag(Flag.not_found_in_source)                # Mspark: rate not printed
    inv = normalize(src)
    assert [ln.line_type for ln in inv.lines] == ["ITEM", "FREIGHT"]
    assert inv.tax_rate.value is None and "not printed" in inv.tax_rate.note


# ---------------------------------------------------------- R1.7 mapping
def test_one_tax_line_per_tax_row_and_printed_freight_kept():
    inv = CanonicalInvoice()
    inv.lines = [CanonicalLine(line_number=1, amount=Field(value=D("431.47"))),
                 CanonicalLine(line_number=2, line_type="FREIGHT", amount=Field(value=D("10.00")))]
    inv.subtotal, inv.tax_amount = Field(value=D("441.47")), Field(value=D("64.61"))
    inv.freight_amount = Field(value=D("10.00"))                    # same freight in the totals: not twice
    inv.taxes = [CanonicalTax(tax_no=1, label=Field(value="TPS"), amount=Field(value=D("21.57"))),
                 CanonicalTax(tax_no=2, label=Field(value="TVQ"), amount=Field(value=D("43.04")))]
    map_lines(_ctx(_ref()), inv, None)
    assert [(ln.line_type, ln.printed, ln.amount.value) for ln in inv.lines] == [
        ("ITEM", True, D("431.47")), ("FREIGHT", True, D("10.00")),
        ("TAX", False, D("21.57")), ("TAX", False, D("43.04"))]
    assert lines_subtotal(_rule_ctx(inv))[0].result == "pass"


def test_location_to_cost_center():
    vm = ValueMap(map_type="location", source_value="316", source_key="316", vendor_id=None, oracle_value="00316",
                  status="active", created_by="test")
    inv = CanonicalInvoice()
    inv.location_ref = Field(value="STORE 316")
    map_location(_ctx(_ref([vm])), inv, None)
    assert inv.cost_center.value == "00316" and inv.cost_center.method == Method.value_map
    inv.location_ref = Field(value="KRS 51")
    inv.cost_center = Field()
    map_location(_ctx(_ref([vm])), inv, None)
    assert inv.cost_center.value is None


# -------------------------------------------------------- R1.8 validation
def test_bank_change_notice_fails_c11():
    inv = CanonicalInvoice()
    inv.bank_change_notice = Field(value="PLEASE USE NEW ROUTING NUMBER EFFECTIVE IMMEDIATELY: 122105980")
    r = bank_details(_rule_ctx(inv))[0]
    assert r.result == "fail" and r.details == {"fraud": True}


def test_invoice_type_sign_c13():
    inv = CanonicalInvoice()
    inv.invoice_type, inv.invoice_amount = Field(value="CREDIT"), Field(value=D("10"))
    assert invoice_type_sign(_rule_ctx(inv))[0].result == "fail"
    inv.invoice_amount = Field(value=D("-10"))
    assert invoice_type_sign(_rule_ctx(inv))[0].result == "pass"


def test_tax_maths_per_tax_row():
    inv = CanonicalInvoice()
    inv.subtotal = Field(value=D("431.47"))
    inv.taxes = [CanonicalTax(tax_no=1, rate=Field(value=D("5")), amount=Field(value=D("21.57"))),
                 CanonicalTax(tax_no=2, rate=Field(value=D("9.975")), amount=Field(value=D("43.04")))]
    assert tax_maths(_rule_ctx(inv))[0].result == "pass"
    inv.taxes[1].amount = Field(value=D("40.00"))
    assert tax_maths(_rule_ctx(inv))[0].result == "fail"


def test_rate_without_percent_sign_is_not_a_rate():                # Énergir: "12" for TPS
    inv = normalize(_source(total="496.08", tax_amount="21.57", taxes=[{"label": "TPS", "rate": "12", "amount": "21.57"}]))
    assert inv.taxes[0].rate.value is None and inv.tax_rate.value is None
    inv = normalize(_source(total="108.00", tax_label="Sales tax 8%", tax_amount="8.00"))
    assert inv.tax_rate.value == D("8")


def test_amount_repeated_as_unit_price_is_dropped():
    inv = normalize(_source(total="$412.07", lines=[{"description": "AWS", "unit_price": "$412.07", "amount": "$412.07"}]))
    assert inv.lines[0].unit_price.value is None and inv.lines[0].amount.value == D("412.07")


def test_us_routing_number_needs_aba_checksum():
    us = "Dallas, TX 75284-8469"
    inv = normalize(_source(vendor_address=us, bank_routing="(205) 620-6200", total="1.00"))  # Mspark: phone
    assert inv.bank_routing.value is None and Flag.implausible_value in inv.bank_routing.flags
    for routing in ("121143260", "122105980"):                                               # Level Access
        assert normalize(_source(vendor_address=us, bank_routing=routing, total="1.00")).bank_routing.value == routing
    assert normalize(_source(bank_routing="HDFC0001234", total="1.00")).bank_routing.value == "0001234"  # not US


def test_iban_must_look_like_one():
    inv = normalize(_source(iban="0", total="1.00"))
    assert inv.iban.value is None and Flag.implausible_value in inv.iban.flags
    assert normalize(_source(iban="DE89 3704 0044 0532 0130 00", total="1.00")).iban.value == "DE89370400440532013000"
