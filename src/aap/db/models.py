"""SQLAlchemy models for the `aap` database (plan section 4).

Transaction tables are append-only by convention (R-05): new rows, never edits,
except for `document.status` and job bookkeeping columns. `audit_event` is
INSERT-only at the grant level.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, list[Any]: JSONB}


def _uuid() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# =============================================================== documents
class Document(Base):
    __tablename__ = "document"
    id: Mapped[uuid.UUID] = _uuid()
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    file_type: Mapped[str] = mapped_column(String(20))          # pdf png jpg tiff xlsx xls csv eml msg txt html
    mime_type: Mapped[str | None] = mapped_column(String(100))
    filename: Mapped[str | None] = mapped_column(String(500))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    storage_path: Mapped[str] = mapped_column(String(500))
    source: Mapped[str] = mapped_column(String(20))              # upload | unpack | eval
    status: Mapped[str] = mapped_column(String(30), index=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document.id"), index=True)
    email_message_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("email_message.id"))
    role: Mapped[str | None] = mapped_column(String(20))        # email | attachment | body | invoice
    flags: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default="[]")
    error: Mapped[str | None] = mapped_column(Text)
    uploaded_by: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


class DocumentStatusHistory(Base):
    __tablename__ = "document_status_history"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document.id"), index=True)
    from_status: Mapped[str | None] = mapped_column(String(30))
    to_status: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


class Page(Base):
    __tablename__ = "page"
    __table_args__ = (UniqueConstraint("document_id", "page_no"),)
    id: Mapped[uuid.UUID] = _uuid()
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document.id"), index=True)
    page_no: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20))                # pdf | image | sheet | text
    route: Mapped[str | None] = mapped_column(String(20))        # text_and_image | image_only | text_only
    text_quality_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 3))
    quality_details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    text: Mapped[str | None] = mapped_column(Text)
    words: Mapped[list[Any] | None] = mapped_column(JSONB)
    tables: Mapped[list[Any] | None] = mapped_column(JSONB)
    image_path: Mapped[str | None] = mapped_column(String(500))
    width: Mapped[float | None] = mapped_column(Numeric(10, 2))
    height: Mapped[float | None] = mapped_column(Numeric(10, 2))
    image_width: Mapped[int | None] = mapped_column(Integer)
    image_height: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = _created()


class ExtractionRun(Base):
    __tablename__ = "extraction_run"
    id: Mapped[uuid.UUID] = _uuid()
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document.id"), index=True)
    backend: Mapped[str] = mapped_column(String(20))             # vllm | stub
    model: Mapped[str] = mapped_column(String(200))
    prompt_version: Mapped[str] = mapped_column(String(20))
    route_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    requests: Mapped[int] = mapped_column(Integer, default=1)
    raw_output: Mapped[list[Any] | None] = mapped_column(JSONB)  # one entry per LLM request
    source_invoice: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20))              # ok | error
    error: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = _created()


class InvoiceVersion(Base):
    __tablename__ = "invoice_version"
    __table_args__ = (UniqueConstraint("document_id", "version"),)
    id: Mapped[uuid.UUID] = _uuid()
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20))                # normalized | mapped | user | vendor_reply
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("invoice_version.id"))
    extraction_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("extraction_run.id"))
    canonical_invoice: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = _created()


class FieldEvidence(Base):
    __tablename__ = "field_evidence"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    invoice_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("invoice_version.id"), index=True)
    field_path: Mapped[str] = mapped_column(String(200))
    value: Mapped[str | None] = mapped_column(Text)
    source_value: Mapped[str | None] = mapped_column(Text)
    page: Mapped[int | None] = mapped_column(Integer)
    bbox: Mapped[list[Any] | None] = mapped_column(JSONB)
    method: Mapped[str] = mapped_column(String(20))
    flags: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default="[]")
    confidence: Mapped[float | None] = mapped_column(Numeric(5, 4))


class ValidationResult(Base):
    __tablename__ = "validation_result"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    invoice_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("invoice_version.id"), index=True)
    run_no: Mapped[int] = mapped_column(Integer, default=1)
    rule_id: Mapped[str] = mapped_column(String(20))
    rule_name: Mapped[str] = mapped_column(String(100))
    result: Mapped[str] = mapped_column(String(10))              # pass | warning | fail | skip
    message: Mapped[str | None] = mapped_column(Text)
    field_paths: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default="[]")
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created()


class Submission(Base):
    __tablename__ = "submission"
    id: Mapped[uuid.UUID] = _uuid()
    invoice_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("invoice_version.id"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True)
    method: Mapped[str] = mapped_column(String(30))              # ebs_open_interface | ebs_rest | fusion_rest
    group_id: Mapped[str | None] = mapped_column(String(80))
    oracle_request_id: Mapped[int | None] = mapped_column(BigInteger)
    oracle_invoice_id: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20))              # pending | submitted | created | error
    oracle_errors: Mapped[list[Any] | None] = mapped_column(JSONB)
    interface_invoice_id: Mapped[int | None] = mapped_column(BigInteger)    # AP_INVOICES_INTERFACE.INVOICE_ID
    oracle_holds: Mapped[list[Any] | None] = mapped_column(JSONB)           # holds Oracle placed on import
    polls: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


# ========================================================== uploaded emails
# An uploaded .eml / .msg file (stage 2 unpack): its headers and attachments.
class EmailMessage(Base):
    __tablename__ = "email_message"
    id: Mapped[uuid.UUID] = _uuid()
    direction: Mapped[str] = mapped_column(String(10))           # inbound
    message_id: Mapped[str | None] = mapped_column(String(998), index=True)
    in_reply_to: Mapped[str | None] = mapped_column(String(998))
    references: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    from_addr: Mapped[str | None] = mapped_column(String(500))
    to_addrs: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    cc_addrs: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    subject: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    raw_path: Mapped[str | None] = mapped_column(String(500))
    classification: Mapped[str | None] = mapped_column(String(30))  # invoice | auto_reply | other
    thread_key: Mapped[str | None] = mapped_column(String(100), index=True)
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document.id", use_alter=True,
                                                                     name="fk_email_message_document"))
    imap_uid: Mapped[int | None] = mapped_column(BigInteger)
    headers: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created()


class EmailAttachment(Base):
    __tablename__ = "email_attachment"
    id: Mapped[uuid.UUID] = _uuid()
    email_message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("email_message.id"), index=True)
    filename: Mapped[str | None] = mapped_column(String(500))
    content_type: Mapped[str | None] = mapped_column(String(200))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document.id"))
    skipped_reason: Mapped[str | None] = mapped_column(String(100))


# ================================================================== agents
class AgentRun(Base):
    __tablename__ = "agent_run"
    id: Mapped[uuid.UUID] = _uuid()
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document.id"), index=True)
    thread_id: Mapped[str] = mapped_column(String(100), index=True)
    invoice_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("invoice_version.id"))
    graph_version: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(30))
    outcome: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentDecision(Base):
    __tablename__ = "agent_decision"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_run.id"), index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document.id"), index=True)
    node: Mapped[str] = mapped_column(String(50))                # also stage-7 mapping choices: map.supplier ...
    inputs: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    candidates: Mapped[list[Any] | None] = mapped_column(JSONB)
    prompt: Mapped[str | None] = mapped_column(Text)
    raw_output: Mapped[str | None] = mapped_column(Text)
    chosen: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    confidence: Mapped[float | None] = mapped_column(Numeric(5, 4))
    reason: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = _created()


class ReviewTask(Base):
    __tablename__ = "review_task"
    id: Mapped[uuid.UUID] = _uuid()
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document.id"), index=True)
    email_message_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("email_message.id"))
    reason_codes: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    summary: Mapped[str | None] = mapped_column(Text)
    suggested_action: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | done
    resolved_by: Mapped[str | None] = mapped_column(String(100))
    resolution: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created()
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Hold(Base):
    __tablename__ = "hold"
    id: Mapped[uuid.UUID] = _uuid()
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document.id"), index=True)
    invoice_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("invoice_version.id"))
    hold_lookup_code: Mapped[str] = mapped_column(String(25))
    line_number: Mapped[int | None] = mapped_column(Integer)
    po_line_location_id: Mapped[int | None] = mapped_column(BigInteger)
    summary: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20))              # proposed | active | released
    created_at: Mapped[datetime] = _created()
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ================================================================= mapping
class ValueMap(Base):
    __tablename__ = "value_map"
    __table_args__ = (
        Index("value_map_lookup", "map_type", "source_key", "vendor_id"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    map_type: Mapped[str] = mapped_column(String(30))            # supplier site terms currency uom category tax_rate
    source_value: Mapped[str] = mapped_column(Text)
    source_key: Mapped[str] = mapped_column(Text)                # normalized lookup key
    vendor_id: Mapped[int | None] = mapped_column(BigInteger)    # scope (null = any vendor)
    oracle_value: Mapped[str] = mapped_column(Text)
    oracle_id: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | retired
    created_by: Mapped[str] = mapped_column(String(100))
    approved_by: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = _created()


class ValueMapProposal(Base):
    __tablename__ = "value_map_proposal"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    map_type: Mapped[str] = mapped_column(String(30))
    source_value: Mapped[str] = mapped_column(Text)
    source_key: Mapped[str] = mapped_column(Text)
    vendor_id: Mapped[int | None] = mapped_column(BigInteger)
    oracle_value: Mapped[str] = mapped_column(Text)
    oracle_id: Mapped[int | None] = mapped_column(BigInteger)
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document.id"))
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | approved | rejected
    proposed_by: Mapped[str] = mapped_column(String(100))
    decided_by: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = _created()
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ====================================================== reference data copy
class RefSyncRun(Base):
    __tablename__ = "ref_sync_run"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = _created()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20))              # running | ok | error
    counts: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    triggered_by: Mapped[str] = mapped_column(String(100))


class RefOperatingUnit(Base):
    __tablename__ = "ref_operating_unit"
    org_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(240))
    short_code: Mapped[str | None] = mapped_column(String(30))
    ledger_id: Mapped[int] = mapped_column(BigInteger)
    ledger_name: Mapped[str | None] = mapped_column(String(100))
    currency_code: Mapped[str] = mapped_column(String(15))
    chart_of_accounts_id: Mapped[int | None] = mapped_column(BigInteger)
    country: Mapped[str | None] = mapped_column(String(60))
    bill_to_text: Mapped[str | None] = mapped_column(Text)
    date_to: Mapped[date | None] = mapped_column(Date)


class RefSupplier(Base):
    __tablename__ = "ref_supplier"
    vendor_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    vendor_name: Mapped[str] = mapped_column(String(240))
    vendor_name_alt: Mapped[str | None] = mapped_column(String(320))
    name_key: Mapped[str] = mapped_column(String(240), index=True)  # normalized for matching
    vendor_number: Mapped[str] = mapped_column(String(30))
    party_id: Mapped[int | None] = mapped_column(BigInteger)
    vendor_type: Mapped[str | None] = mapped_column(String(30))
    tax_id: Mapped[str | None] = mapped_column(String(30), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean)
    end_date_active: Mapped[date | None] = mapped_column(Date)
    terms_id: Mapped[int | None] = mapped_column(BigInteger)
    invoice_currency_code: Mapped[str | None] = mapped_column(String(15))
    payment_method: Mapped[str | None] = mapped_column(String(25))
    pay_group: Mapped[str | None] = mapped_column(String(25))
    po_required: Mapped[bool] = mapped_column(Boolean, default=True)   # DFF ATTRIBUTE1
    default_cost_centre: Mapped[str | None] = mapped_column(String(25))  # DFF ATTRIBUTE2
    hold_all_payments: Mapped[bool] = mapped_column(Boolean, default=False)


class RefSupplierSite(Base):
    __tablename__ = "ref_supplier_site"
    vendor_site_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    vendor_id: Mapped[int] = mapped_column(BigInteger, index=True)
    vendor_site_code: Mapped[str] = mapped_column(String(15))
    org_id: Mapped[int] = mapped_column(BigInteger)
    address: Mapped[str | None] = mapped_column(Text)
    address_line1: Mapped[str | None] = mapped_column(String(240))
    city: Mapped[str | None] = mapped_column(String(60))
    state: Mapped[str | None] = mapped_column(String(150))
    zip: Mapped[str | None] = mapped_column(String(20))
    country: Mapped[str | None] = mapped_column(String(25))
    email_address: Mapped[str | None] = mapped_column(Text)
    remittance_email: Mapped[str | None] = mapped_column(Text)
    email_domains: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    pay_site: Mapped[bool] = mapped_column(Boolean)
    purchasing_site: Mapped[bool] = mapped_column(Boolean)
    primary_pay_site: Mapped[bool] = mapped_column(Boolean)
    terms_id: Mapped[int | None] = mapped_column(BigInteger)
    invoice_currency_code: Mapped[str | None] = mapped_column(String(15))
    tolerance_id: Mapped[int | None] = mapped_column(BigInteger)
    match_option: Mapped[str | None] = mapped_column(String(25))
    liability_ccid: Mapped[int | None] = mapped_column(BigInteger)
    inactive_date: Mapped[date | None] = mapped_column(Date)


class RefSupplierBank(Base):
    __tablename__ = "ref_supplier_bank"
    ext_bank_account_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    vendor_id: Mapped[int] = mapped_column(BigInteger, index=True)
    bank_account_num: Mapped[str] = mapped_column(String(100))
    iban: Mapped[str | None] = mapped_column(String(50))
    currency_code: Mapped[str | None] = mapped_column(String(15))
    country_code: Mapped[str | None] = mapped_column(String(2))
    bank_name: Mapped[str | None] = mapped_column(String(360))
    end_date: Mapped[date | None] = mapped_column(Date)


class RefTerms(Base):
    __tablename__ = "ref_terms"
    term_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    description: Mapped[str | None] = mapped_column(String(240))
    enabled: Mapped[bool] = mapped_column(Boolean)
    due_days: Mapped[int | None] = mapped_column(Integer)
    due_day_of_month: Mapped[int | None] = mapped_column(Integer)
    discount_percent: Mapped[Decimal | None] = mapped_column(Numeric)
    discount_days: Mapped[int | None] = mapped_column(Integer)
    installments: Mapped[int] = mapped_column(Integer, default=1)


class RefCurrency(Base):
    __tablename__ = "ref_currency"
    currency_code: Mapped[str] = mapped_column(String(15), primary_key=True)
    symbol: Mapped[str | None] = mapped_column(String(12))
    precision: Mapped[int | None] = mapped_column(Integer)
    territory: Mapped[str | None] = mapped_column(String(2))
    enabled: Mapped[bool] = mapped_column(Boolean)


class RefCcid(Base):
    __tablename__ = "ref_ccid"
    ccid: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    concatenated: Mapped[str] = mapped_column(String(100), index=True)
    segment1: Mapped[str] = mapped_column(String(25))
    segment2: Mapped[str] = mapped_column(String(25))
    segment3: Mapped[str] = mapped_column(String(25))
    segment4: Mapped[str] = mapped_column(String(25))
    account_type: Mapped[str] = mapped_column(String(1))
    enabled: Mapped[bool] = mapped_column(Boolean)
    end_date_active: Mapped[date | None] = mapped_column(Date)
    company_desc: Mapped[str | None] = mapped_column(String(240))
    cost_centre_desc: Mapped[str | None] = mapped_column(String(240))
    account_desc: Mapped[str | None] = mapped_column(String(240))


class RefCategory(Base):
    __tablename__ = "ref_category"
    category_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(160), index=True)
    description: Mapped[str | None] = mapped_column(String(240))
    enabled: Mapped[bool] = mapped_column(Boolean)


class RefUom(Base):
    __tablename__ = "ref_uom"
    unit_of_measure: Mapped[str] = mapped_column(String(25), primary_key=True)
    uom_code: Mapped[str] = mapped_column(String(3))
    uom_class: Mapped[str | None] = mapped_column(String(10))
    disable_date: Mapped[date | None] = mapped_column(Date)


class RefTaxRate(Base):
    __tablename__ = "ref_tax_rate"
    tax_rate_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tax_rate_code: Mapped[str] = mapped_column(String(150))
    tax_regime_code: Mapped[str] = mapped_column(String(30))
    tax: Mapped[str] = mapped_column(String(30))
    percentage_rate: Mapped[Decimal | None] = mapped_column(Numeric)
    active: Mapped[bool] = mapped_column(Boolean)


class RefTolerance(Base):
    __tablename__ = "ref_tolerance"
    tolerance_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    price_tolerance: Mapped[Decimal | None] = mapped_column(Numeric)
    quantity_tolerance: Mapped[Decimal | None] = mapped_column(Numeric)
    qty_received_tolerance: Mapped[Decimal | None] = mapped_column(Numeric)


class RefGlPeriod(Base):
    __tablename__ = "ref_gl_period"
    ledger_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    period_name: Mapped[str] = mapped_column(String(15), primary_key=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    closing_status: Mapped[str] = mapped_column(String(1))


# ================================================================= vectors
class GlHistoryLine(Base):
    __tablename__ = "gl_history_line"
    __table_args__ = (
        Index("gl_history_line_embedding_hnsw", "embedding", postgresql_using="hnsw",
              postgresql_with={"m": 16, "ef_construction": 64},
              postgresql_ops={"embedding": "vector_cosine_ops"}),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    invoice_distribution_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    vendor_id: Mapped[int] = mapped_column(BigInteger, index=True)
    org_id: Mapped[int | None] = mapped_column(BigInteger)
    description: Mapped[str] = mapped_column(Text)
    ccid: Mapped[int] = mapped_column(BigInteger)
    concatenated: Mapped[str | None] = mapped_column(String(100))
    natural_account: Mapped[str | None] = mapped_column(String(25))
    category_code: Mapped[str | None] = mapped_column(String(160))
    amount: Mapped[Decimal | None] = mapped_column(Numeric)
    invoice_date: Mapped[date | None] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(20), default="ebs")  # ebs | review
    embedding: Mapped[Any | None] = mapped_column(Vector(1024))
    created_at: Mapped[datetime] = _created()


# ==================================================================== eval
class EvalRun(Base):
    __tablename__ = "eval_run"
    id: Mapped[uuid.UUID] = _uuid()
    name: Mapped[str] = mapped_column(String(200))
    backend: Mapped[str] = mapped_column(String(20))
    model: Mapped[str | None] = mapped_column(String(200))
    prompt_version: Mapped[str | None] = mapped_column(String(20))
    dataset: Mapped[str] = mapped_column(String(200))
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created()


class EvalResult(Base):
    __tablename__ = "eval_result"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    eval_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("eval_run.id"), index=True)
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document.id"))
    tier: Mapped[str | None] = mapped_column(String(20))
    field_path: Mapped[str] = mapped_column(String(200))
    expected: Mapped[str | None] = mapped_column(Text)
    actual: Mapped[str | None] = mapped_column(Text)
    correct: Mapped[bool] = mapped_column(Boolean)


# =================================================================== audit
class AuditEvent(Base):
    __tablename__ = "audit_event"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    actor: Mapped[str] = mapped_column(String(100))
    event_type: Mapped[str] = mapped_column(String(60), index=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created()
