"""Reference data sync (ARCHITECTURE 8.3, plan section 4).

Reads apps.* through the adapter (apps_ro) and replaces the ref_* tables in one
transaction. Also copies new AP distribution history into gl_history_line
(embeddings are added by the GL agent phase; rows start with embedding NULL).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from aap.adapters.base import ErpReadPort
from aap.audit import audit
from aap.common.text import email_domain, name_key
from aap.db.models import (
    GlHistoryLine,
    RefCategory,
    RefCcid,
    RefCurrency,
    RefGlPeriod,
    RefOperatingUnit,
    RefSupplier,
    RefSupplierBank,
    RefSupplierSite,
    RefSyncRun,
    RefTaxRate,
    RefTerms,
    RefTolerance,
    RefUom,
)
from aap.logging import get_logger

log = get_logger(__name__)

REF_TABLES = [RefOperatingUnit, RefSupplier, RefSupplierSite, RefSupplierBank, RefTerms, RefCurrency, RefCcid,
              RefCategory, RefUom, RefTaxRate, RefTolerance, RefGlPeriod]


def _yn(v: Any) -> bool:
    return (v or "N") == "Y"


def _rows(data) -> dict[type, list[dict[str, Any]]]:  # noqa: ANN001
    out: dict[type, list[dict[str, Any]]] = {}
    out[RefOperatingUnit] = [dict(org_id=r["org_id"], name=r["name"], short_code=r["short_code"],
                                  ledger_id=r["ledger_id"], ledger_name=r["ledger_name"],
                                  currency_code=r["currency_code"], chart_of_accounts_id=r["chart_of_accounts_id"],
                                  country=r["country"], bill_to_text=r["bill_to_text"], date_to=r["date_to"])
                             for r in data.operating_units]
    out[RefSupplier] = [dict(vendor_id=r["vendor_id"], vendor_name=r["vendor_name"],
                             vendor_name_alt=r["vendor_name_alt"], name_key=name_key(r["vendor_name"]),
                             vendor_number=r["vendor_number"], party_id=r["party_id"], vendor_type=r["vendor_type"],
                             tax_id=(r["tax_id"] or "").upper().replace(" ", "") or None,
                             enabled=_yn(r["enabled_flag"]), end_date_active=r["end_date_active"],
                             terms_id=r["terms_id"], invoice_currency_code=r["invoice_currency_code"],
                             payment_method=r["payment_method"], pay_group=r["pay_group"],
                             po_required=(r["po_required_flag"] or "Y") != "N",
                             default_cost_centre=r["default_cost_centre"],
                             hold_all_payments=_yn(r["hold_all_payments_flag"]))
                        for r in data.suppliers]
    sites = []
    for r in data.supplier_sites:
        emails = [e for e in [r["email_address"], r["remittance_email"]] if e]
        domains = sorted({d for e in emails for part in e.replace(";", ",").split(",")
                          if (d := email_domain(part.strip()))})
        sites.append(dict(
            vendor_site_id=r["vendor_site_id"], vendor_id=r["vendor_id"], vendor_site_code=r["vendor_site_code"],
            org_id=r["org_id"],
            address=", ".join(p for p in [r["address_line1"], r["address_line2"], r["address_line3"], r["city"],
                                          r["state"] or r["province"], r["zip"], r["country"]] if p),
            address_line1=r["address_line1"], city=r["city"], state=r["state"] or r["province"], zip=r["zip"],
            country=r["country"], email_address=r["email_address"], remittance_email=r["remittance_email"],
            email_domains=domains, pay_site=_yn(r["pay_site_flag"]), purchasing_site=_yn(r["purchasing_site_flag"]),
            primary_pay_site=_yn(r["primary_pay_site_flag"]), terms_id=r["terms_id"],
            invoice_currency_code=r["invoice_currency_code"], tolerance_id=r["tolerance_id"],
            match_option=r["match_option"], liability_ccid=r["liability_ccid"], inactive_date=r["inactive_date"]))
    out[RefSupplierSite] = sites
    out[RefSupplierBank] = [dict(ext_bank_account_id=r["ext_bank_account_id"], vendor_id=r["vendor_id"],
                                 bank_account_num=r["bank_account_num"], iban=r["iban"],
                                 currency_code=r["currency_code"], country_code=r["country_code"],
                                 bank_name=r["bank_name"], end_date=r["end_date"])
                            for r in data.supplier_banks]
    out[RefTerms] = [dict(term_id=r["term_id"], name=r["name"], description=r["description"],
                          enabled=_yn(r["enabled_flag"]), due_days=r["due_days"],
                          due_day_of_month=r["due_day_of_month"], discount_percent=r["discount_percent"],
                          discount_days=r["discount_days"], installments=r["installments"] or 1)
                     for r in data.terms]
    out[RefCurrency] = [dict(currency_code=r["currency_code"], symbol=r["symbol"], precision=r["precision"],
                             territory=r["territory"], enabled=_yn(r["enabled_flag"])) for r in data.currencies]
    out[RefCcid] = [dict(ccid=r["ccid"], concatenated=r["concatenated"], segment1=r["segment1"],
                         segment2=r["segment2"], segment3=r["segment3"], segment4=r["segment4"],
                         account_type=r["account_type"],
                         enabled=_yn(r["enabled_flag"]) and _yn(r["detail_posting_allowed_flag"]),
                         end_date_active=r["end_date_active"], company_desc=r["company_desc"],
                         cost_centre_desc=r["cost_centre_desc"], account_desc=r["account_desc"])
                    for r in data.ccids]
    out[RefCategory] = [dict(category_id=r["category_id"], code=r["code"], description=r["description"],
                             enabled=_yn(r["enabled_flag"]) and r["disable_date"] is None) for r in data.categories]
    out[RefUom] = [dict(unit_of_measure=r["unit_of_measure"], uom_code=r["uom_code"], uom_class=r["uom_class"],
                        disable_date=r["disable_date"]) for r in data.uoms]
    out[RefTaxRate] = [dict(tax_rate_id=r["tax_rate_id"], tax_rate_code=r["tax_rate_code"],
                            tax_regime_code=r["tax_regime_code"], tax=r["tax"], percentage_rate=r["percentage_rate"],
                            active=_yn(r["active_flag"]) and r["effective_to"] is None) for r in data.tax_rates]
    out[RefTolerance] = [dict(tolerance_id=r["tolerance_id"], name=r["name"], price_tolerance=r["price_tolerance"],
                              quantity_tolerance=r["quantity_tolerance"],
                              qty_received_tolerance=r["qty_received_tolerance"]) for r in data.tolerances]
    out[RefGlPeriod] = [dict(ledger_id=r["ledger_id"], period_name=r["period_name"], start_date=r["start_date"],
                             end_date=r["end_date"], closing_status=r["closing_status"]) for r in data.gl_periods]
    return out


def _sync_history(session: Session, adapter: ErpReadPort) -> int:
    last = session.scalar(select(func.max(GlHistoryLine.invoice_distribution_id))) or 0
    batch: list[dict[str, Any]] = []
    n = 0
    for h in adapter.get_gl_history():
        if h.invoice_distribution_id <= last:
            continue
        batch.append(dict(invoice_distribution_id=h.invoice_distribution_id, vendor_id=h.vendor_id,
                          org_id=h.org_id, description=h.description or "", ccid=h.ccid,
                          concatenated=h.concatenated, natural_account=h.natural_account,
                          category_code=h.category_code, amount=Decimal(h.amount), invoice_date=h.invoice_date,
                          source="ebs"))
        if len(batch) >= 2000:
            session.execute(insert(GlHistoryLine), batch)
            n += len(batch)
            batch = []
    if batch:
        session.execute(insert(GlHistoryLine), batch)
        n += len(batch)
    return n


def run_sync(session: Session, adapter: ErpReadPort, *, triggered_by: str = "system") -> RefSyncRun:
    """Replace all ref_* tables in the session's transaction. The caller commits."""
    run = RefSyncRun(status="running", triggered_by=triggered_by)
    session.add(run)
    session.flush()
    try:
        data = adapter.load_reference_data()
        rows = _rows(data)
        counts: dict[str, int] = {}
        for model in reversed(REF_TABLES):
            session.execute(delete(model))
        for model in REF_TABLES:
            if rows[model]:
                session.execute(insert(model), rows[model])
            counts[model.__tablename__] = len(rows[model])
        counts["gl_history_line_new"] = _sync_history(session, adapter)
        run.status = "ok"
        run.counts = counts
        run.finished_at = datetime.now(UTC)
        audit(session, "ref_sync.completed", actor=triggered_by, run_id=run.id, counts=counts)
        log.info("ref_sync.completed", counts=counts)
    except Exception as exc:
        run.status = "error"
        run.error = str(exc)[:2000]
        run.finished_at = datetime.now(UTC)
        raise
    return run


def last_sync(session: Session) -> RefSyncRun | None:
    return session.scalars(select(RefSyncRun).where(RefSyncRun.status == "ok")
                           .order_by(RefSyncRun.id.desc()).limit(1)).first()
