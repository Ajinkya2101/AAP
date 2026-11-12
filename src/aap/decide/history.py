"""The facts a context pack is built from (PLAN-MODEL-JSON §4): supplier and site defaults, the keyed
distributions with their dates, account descriptions, distribution sets, the instance's lists and the
store table.

Two loaders fill the same `HistoryStore`, so a context pack built for training and one built in
production come from the same code:

- `from_delivery`: a client delivery (aap.clientdata.readers) for datasets and offline evaluation;
- `from_db`: the application database (reference copy and `gl_history_line`) at request time.

Amounts are kept signed, as keyed; shares are computed on absolute amounts.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from aap.common.coa import CoaLayout

TAX_TYPES = frozenset({"TAX", "NONREC_TAX", "REC_TAX", "TRV", "TERV", "TIPV", "AWT"})
ZERO = Decimal(0)


@dataclass(frozen=True)
class HistLine:
    """One keyed distribution (or one line of an invoice approved in this application)."""
    vendor_id: int
    vendor_site_id: int | None
    invoice_id: int | None
    invoice_num: str | None
    invoice_date: date | None
    gl_date: date | None
    invoice_type: str | None
    line_number: int | None
    line_type: str
    description: str
    amount: Decimal
    account: str | None
    distribution_set_id: int | None = None

    @property
    def date(self) -> date | None:
        return self.gl_date or self.invoice_date

    @property
    def is_tax(self) -> bool:
        return self.line_type.upper() in TAX_TYPES


@dataclass
class SupplierFacts:
    vendor_id: int
    vendor_number: str | None
    vendor_name: str | None
    terms: str | None = None
    pay_group: str | None = None
    payment_method: str | None = None
    currency: str | None = None


@dataclass
class SiteFacts:
    vendor_site_id: int
    vendor_id: int
    vendor_site_code: str | None
    org_id: int | None
    operating_unit: str | None = None
    currency: str | None = None
    terms: str | None = None
    pay_group: str | None = None
    payment_method: str | None = None
    liability_account: str | None = None
    distribution_set_id: int | None = None
    terms_date_basis: str | None = None


@dataclass
class DistributionSet:
    distribution_set_id: int
    name: str | None
    description: str | None = None
    lines: list[dict[str, Any]] = field(default_factory=list)      # {"account", "percent", "description"}


@dataclass
class HistoryStore:
    suppliers: dict[int, SupplierFacts] = field(default_factory=dict)
    sites: dict[int, SiteFacts] = field(default_factory=dict)
    lines: list[HistLine] = field(default_factory=list)
    account_descriptions: dict[str, str] = field(default_factory=dict)
    distribution_sets: dict[int, DistributionSet] = field(default_factory=dict)
    # invoice_types, line_types, sources, terms, payment_methods, pay_groups: the instance's enabled values
    lists: dict[str, list[str]] = field(default_factory=dict)
    stores: dict[str, str] = field(default_factory=dict)              # store as keyed -> cost center
    # from_db holds one supplier's lines only, so it computes the cross-supplier fallback in SQL
    precomputed_fallback: list[tuple[str, Decimal, int]] | None = None
    _by_vendor: dict[int, list[HistLine]] | None = field(default=None, repr=False)

    def lines_of(self, vendor_id: int) -> list[HistLine]:
        if self._by_vendor is None:
            by: dict[int, list[HistLine]] = defaultdict(list)
            for ln in self.lines:
                by[ln.vendor_id].append(ln)
            self._by_vendor = dict(by)
        return self._by_vendor.get(vendor_id, [])

    def fallback_accounts(self, as_of: date, exclude: set[int], limit: int) -> list[tuple[str, Decimal, int]]:
        """The most used accounts across all suppliers before `as_of`: (account, amount, invoices). For a
        supplier without history (PLAN-MODEL-JSON §4); such a line always goes to review."""
        if self.precomputed_fallback is not None:
            return self.precomputed_fallback[:limit]
        amount: Counter = Counter()
        invoices: dict[str, set] = defaultdict(set)
        for ln in self.lines:
            if ln.account and not ln.is_tax and ln.date and ln.date < as_of and ln.invoice_id not in exclude:
                amount[ln.account] += abs(ln.amount)
                invoices[ln.account].add(ln.invoice_id)
        return [(a, v, len(invoices[a])) for a, v in amount.most_common(limit)]


def store_key(text: str | None) -> str | None:
    """A store reference as compared: its number without leading zeros ("STORE 0316" -> "316"), else
    the text in upper case."""
    if not text:
        return None
    m = re.search(r"\d{2,}", text)
    return (m.group(0).lstrip("0") or "0") if m else text.strip().upper()


# =================================================================== delivery
def delivered_accounts(d: Any, coa: CoaLayout) -> dict[int, str]:
    """CCID -> account string for the combinations a delivery has (GL_CODE_COMBINATIONS)."""
    from aap.clientdata.readers import num

    combos: dict[int, str] = {}
    for c in d.rows("GL_CODE_COMBINATIONS"):
        ccid = num(c.get("CODE_COMBINATION_ID"))
        parts = [c.get(f"SEGMENT{i}") for i in range(1, coa.segment_count + 1)]
        if ccid is not None and all(p is not None for p in parts):
            combos[ccid] = coa.join([str(p) for p in parts])
    return combos


def account_resolver(d: Any, coa: CoaLayout) -> Any:
    """ccid -> account string, as the replica loads it: the delivered combination, else the placeholder
    account (aap.ebs_mock.client_load.placeholder_account) until the chart of accounts arrives."""
    from aap.clientdata.readers import num
    from aap.ebs_mock.client_load import placeholder_account

    combos = delivered_accounts(d, coa)

    def account(ccid: Any) -> str | None:
        c = num(ccid)
        if c is None:
            return None
        return combos.get(c) or coa.join(placeholder_account(c, coa))

    return account


def from_delivery(d: Any, coa: CoaLayout) -> HistoryStore:
    """All suppliers, sites and keyed history of a delivery (aap.clientdata.readers.Delivery)."""
    from aap.clientdata.readers import day, dec, num

    combos = delivered_accounts(d, coa)
    account = account_resolver(d, coa)
    store = HistoryStore()
    store.account_descriptions = _flex_descriptions(d, coa, combos)
    terms = {num(t.get("TERM_ID")): str(t.get("NAME")) for t in d.rows("AP_TERMS")}
    ous = {num(o.get("ORGANIZATION_ID")): o.get("NAME") for o in d.rows("HR_OPERATING_UNITS")}
    for s in d.rows("AP_SUPPLIERS"):
        v = num(s.get("VENDOR_ID"))
        if v is None:
            continue
        store.suppliers[v] = SupplierFacts(
            vendor_id=v, vendor_number=str(s.get("SEGMENT1")) if s.get("SEGMENT1") is not None else None,
            vendor_name=s.get("VENDOR_NAME"), terms=terms.get(num(s.get("TERMS_ID"))),
            pay_group=s.get("PAY_GROUP_LOOKUP_CODE"), payment_method=s.get("PAYMENT_METHOD_LOOKUP_CODE"),
            currency=s.get("INVOICE_CURRENCY_CODE"))
    for s in d.rows("AP_SUPPLIER_SITES_ALL"):
        site, v = num(s.get("VENDOR_SITE_ID")), num(s.get("VENDOR_ID"))
        if site is None or v is None:
            continue
        org = num(s.get("ORG_ID"))
        store.sites[site] = SiteFacts(
            vendor_site_id=site, vendor_id=v, vendor_site_code=s.get("VENDOR_SITE_CODE"), org_id=org,
            operating_unit=ous.get(org) or (f"OU {org}" if org is not None else None),
            currency=s.get("INVOICE_CURRENCY_CODE"), terms=terms.get(num(s.get("TERMS_ID"))),
            pay_group=s.get("PAY_GROUP_LOOKUP_CODE"), payment_method=s.get("PAYMENT_METHOD_LOOKUP_CODE"),
            liability_account=account(s.get("ACCTS_PAY_CODE_COMBINATION_ID")),
            distribution_set_id=num(s.get("DISTRIBUTION_SET_ID")), terms_date_basis=s.get("TERMS_DATE_BASIS"))

    invoices = {num(x.get("INVOICE_ID")): x for x in d.rows("AP_INVOICES_ALL") if not x.get("CANCELLED_DATE")}
    lines = {(num(x.get("INVOICE_ID")), num(x.get("LINE_NUMBER"))): x for x in d.rows("AP_INVOICE_LINES_ALL")
             if (x.get("DISCARDED_FLAG") or "N") != "Y" and (x.get("CANCELLED_FLAG") or "N") != "Y"}
    for x in d.rows("AP_INVOICE_DISTRIBUTIONS_ALL"):
        if (x.get("REVERSAL_FLAG") or "N") == "Y":          # a reversal pair nets to zero
            continue
        inv_id = num(x.get("INVOICE_ID"))
        inv = invoices.get(inv_id)
        ln = lines.get((inv_id, num(x.get("INVOICE_LINE_NUMBER"))))
        if inv is None or ln is None or num(inv.get("VENDOR_ID")) is None:
            continue
        store.lines.append(HistLine(
            vendor_id=num(inv["VENDOR_ID"]), vendor_site_id=num(inv.get("VENDOR_SITE_ID")),  # type: ignore[arg-type]
            invoice_id=inv_id, invoice_num=str(inv.get("INVOICE_NUM")) if inv.get("INVOICE_NUM") is not None else None,
            invoice_date=day(inv.get("INVOICE_DATE")), gl_date=day(inv.get("GL_DATE")),
            invoice_type=inv.get("INVOICE_TYPE_LOOKUP_CODE"), line_number=num(ln.get("LINE_NUMBER")),
            line_type=str(x.get("LINE_TYPE_LOOKUP_CODE") or ln.get("LINE_TYPE_LOOKUP_CODE") or "ITEM").upper(),
            description=str(x.get("DESCRIPTION") or ln.get("DESCRIPTION") or ""),
            amount=dec(x.get("AMOUNT")) or ZERO, account=account(x.get("DIST_CODE_COMBINATION_ID")),
            distribution_set_id=num(ln.get("DISTRIBUTION_SET_ID"))))

    set_lines: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for x in d.rows("AP_DISTRIBUTION_SET_LINES_ALL"):
        sid = num(x.get("DISTRIBUTION_SET_ID"))
        if sid is not None:
            set_lines[sid].append({"account": account(x.get("DIST_CODE_COMBINATION_ID")),
                                   "percent": str(dec(x.get("PERCENT_DISTRIBUTION")) or ZERO),
                                   "description": x.get("DESCRIPTION")})
    for x in d.rows("AP_DISTRIBUTION_SETS_ALL"):
        sid = num(x.get("DISTRIBUTION_SET_ID"))
        if sid is not None and not x.get("INACTIVE_DATE"):
            store.distribution_sets[sid] = DistributionSet(sid, x.get("DISTRIBUTION_SET_NAME"), x.get("DESCRIPTION"),
                                                           set_lines.get(sid, []))

    lookups: dict[str, list[str]] = defaultdict(list)
    for x in d.rows("FND_LOOKUP_VALUES"):
        if (x.get("ENABLED_FLAG") or "Y") == "Y" and not x.get("END_DATE_ACTIVE"):
            lookups[str(x.get("LOOKUP_TYPE")).upper()].append(str(x.get("LOOKUP_CODE")))
    used = list(invoices.values())
    store.lists = {
        "invoice_types": _first_or(lookups.get("INVOICE TYPE"), (x.get("INVOICE_TYPE_LOOKUP_CODE") for x in used)),
        "line_types": _first_or(lookups.get("INVOICE LINE TYPE"), (x.get("LINE_TYPE_LOOKUP_CODE") for x in lines.values())),
        "sources": _first_or(lookups.get("SOURCE"), (x.get("SOURCE") for x in used)),
        "terms": sorted({str(t.get("NAME")) for t in d.rows("AP_TERMS")
                         if (t.get("ENABLED_FLAG") or "Y") == "Y" and t.get("NAME")}),
        "payment_methods": sorted({str(p.get("PAYMENT_METHOD_CODE")) for p in d.rows("IBY_PAYMENT_METHODS")
                                   if p.get("PAYMENT_METHOD_CODE")}),
        "pay_groups": sorted({str(v) for t in ("AP_SUPPLIERS", "AP_SUPPLIER_SITES_ALL", "AP_INVOICES_ALL")
                              for x in d.rows(t) if (v := x.get("PAY_GROUP_LOOKUP_CODE"))}),
    }
    store.stores = {str(x.get("STORE")): str(x.get("COST_CENTER")) for x in d.rows("STORES")
                    if x.get("STORE") and x.get("COST_CENTER")}
    return store


def _first_or(listed: list[str] | None, used: Any) -> list[str]:
    """The instance's list when delivered, else the values the history uses."""
    if listed:
        return sorted(set(listed))
    return sorted({str(v) for v in used if v})


def _flex_descriptions(d: Any, coa: CoaLayout, combos: dict[int, str]) -> dict[str, str]:
    """account -> "cost-center description / natural-account description" from the segment value sets."""
    from aap.clientdata.readers import num

    value_set = {num(str(s.get("APPLICATION_COLUMN_NAME") or "").upper().replace("SEGMENT", "")):
                 s.get("FLEX_VALUE_SET_NAME") for s in d.rows("FND_ID_FLEX_SEGMENTS")}
    values = {(x.get("FLEX_VALUE_SET_NAME"), str(x.get("FLEX_VALUE"))): x.get("DESCRIPTION")
              for x in d.rows("FND_FLEX_VALUES")}
    if not values:
        return {}
    out: dict[str, str] = {}
    for acct in combos.values():
        parts = coa.split(acct) or []
        descs = [values.get((value_set.get(n), parts[n - 1])) for n in (coa.cost_center_segment,
                                                                         coa.natural_account_segment)
                 if n <= len(parts)]
        text = " / ".join(x for x in descs if x)
        if text:
            out[acct] = text
    return out


# ========================================================================= db
def from_db(session: Any, vendor_id: int, *, as_of: date | None = None, fallback_limit: int = 15) -> HistoryStore:
    """The reference copy and GL history of one supplier, as the application holds them."""
    from sqlalchemy import func, select

    from aap.common.lookups import INVOICE_TYPE, LINE_TYPE, SOURCE
    from aap.db.models import (
        GlHistoryLine,
        RefCcid,
        RefDistributionSet,
        RefLookup,
        RefOperatingUnit,
        RefPaymentMethod,
        RefSupplier,
        RefSupplierSite,
        RefTerms,
        ValueMap,
    )

    store = HistoryStore()
    terms = dict(session.execute(select(RefTerms.term_id, RefTerms.name)).all())
    s = session.get(RefSupplier, vendor_id)
    if s is not None:
        store.suppliers[vendor_id] = SupplierFacts(vendor_id=vendor_id, vendor_number=s.vendor_number,
                                                   vendor_name=s.vendor_name, terms=terms.get(s.terms_id),
                                                   pay_group=s.pay_group, payment_method=s.payment_method,
                                                   currency=s.invoice_currency_code)
    ous = {o.org_id: o.name for o in session.scalars(select(RefOperatingUnit))}
    sites = list(session.scalars(select(RefSupplierSite).where(RefSupplierSite.vendor_id == vendor_id)))
    liab = {c.ccid: c.concatenated for c in session.scalars(
        select(RefCcid).where(RefCcid.ccid.in_([x.liability_ccid for x in sites if x.liability_ccid])))}
    for x in sites:
        store.sites[x.vendor_site_id] = SiteFacts(
            vendor_site_id=x.vendor_site_id, vendor_id=vendor_id, vendor_site_code=x.vendor_site_code,
            org_id=x.org_id, operating_unit=ous.get(x.org_id), currency=x.invoice_currency_code,
            terms=terms.get(x.terms_id), pay_group=x.pay_group, payment_method=x.payment_method,
            liability_account=liab.get(x.liability_ccid), distribution_set_id=x.distribution_set_id,
            terms_date_basis=x.terms_date_basis)
    for h in session.scalars(select(GlHistoryLine).where(GlHistoryLine.vendor_id == vendor_id)):
        store.lines.append(HistLine(
            vendor_id=h.vendor_id, vendor_site_id=h.vendor_site_id, invoice_id=h.invoice_id,
            invoice_num=h.invoice_num, invoice_date=h.invoice_date, gl_date=h.gl_date, invoice_type=h.invoice_type,
            line_number=h.line_number, line_type=(h.line_type or "ITEM").upper(), description=h.description or "",
            amount=h.amount or ZERO, account=h.concatenated, distribution_set_id=h.distribution_set_id))
    used_accounts = {ln.account for ln in store.lines if ln.account}
    for c in session.scalars(select(RefCcid).where(RefCcid.concatenated.in_(used_accounts))):
        text = " / ".join(x for x in (c.cost_centre_desc, c.account_desc) if x)
        if text:
            store.account_descriptions[c.concatenated] = text
    set_ids = {ln.distribution_set_id for ln in store.lines if ln.distribution_set_id}
    set_ids |= {x.distribution_set_id for x in store.sites.values() if x.distribution_set_id}
    for ds in session.scalars(select(RefDistributionSet).where(RefDistributionSet.distribution_set_id.in_(set_ids))):
        store.distribution_sets[ds.distribution_set_id] = DistributionSet(
            ds.distribution_set_id, ds.name, ds.description, list(ds.lines or []))
    lookups: dict[str, list[str]] = defaultdict(list)
    for r in session.scalars(select(RefLookup).where(RefLookup.enabled.is_(True))):
        lookups[r.lookup_type].append(r.lookup_code)
    store.lists = {
        "invoice_types": sorted(lookups.get(INVOICE_TYPE, [])),
        "line_types": sorted(lookups.get(LINE_TYPE, [])),
        "sources": sorted(lookups.get(SOURCE, [])),
        "terms": sorted(n for (n,) in session.execute(select(RefTerms.name).where(RefTerms.enabled.is_(True))).all()),
        "payment_methods": sorted(c for (c,) in session.execute(
            select(RefPaymentMethod.payment_method_code).where(RefPaymentMethod.enabled.is_(True))).all()),
        "pay_groups": sorted({v for (v,) in session.execute(select(RefSupplierSite.pay_group).distinct()).all() if v}),
    }
    store.stores = {v.source_value: v.oracle_value for v in session.scalars(
        select(ValueMap).where(ValueMap.map_type == "location"))}
    if as_of is not None and not store.lines:
        rows = session.execute(
            select(GlHistoryLine.concatenated, func.sum(func.abs(GlHistoryLine.amount)),
                   func.count(func.distinct(GlHistoryLine.invoice_id)))
            .where(func.coalesce(GlHistoryLine.gl_date, GlHistoryLine.invoice_date) < as_of,
                   GlHistoryLine.concatenated.is_not(None))
            .group_by(GlHistoryLine.concatenated)
            .order_by(func.sum(func.abs(GlHistoryLine.amount)).desc()).limit(fallback_limit)).all()
        store.precomputed_fallback = [(a, Decimal(str(v or 0)), n) for a, v, n in rows]
    return store
