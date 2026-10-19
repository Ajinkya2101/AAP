"""Stages 2-8 as idempotent units of work (R-04).

Each stage reads the previous stage's output from the database, writes its own
output (new rows only, R-05) plus audit events, moves the document status and
returns the next (stage, document_id) pairs. The worker runs one stage per job;
run_pipeline_inline() runs them in-process (tests, CLI, no worker).
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aap.adapters import get_read_adapter
from aap.audit import audit
from aap.config import get_settings
from aap.db.models import (
    Document,
    EmailMessage,
    ExtractionRun,
    FieldEvidence,
    InvoiceVersion,
    Page,
    ReviewTask,
    ValidationResult,
)
from aap.db.session import session_scope
from aap.extraction.extract import PROMPT_VERSION, PageInput, extract
from aap.extraction.source_check import source_check
from aap.filestore import FileStore
from aap.ingest.parse import ParseError, parse_document
from aap.ingest.route import route_page
from aap.ingest.unpack import unpack
from aap.llm import get_llm
from aap.llm.client import LlmError
from aap.logging import get_logger
from aap.mapping.map import MapContext, map_invoice
from aap.mapping.reference import load_reference
from aap.normalize.normalize import normalize
from aap.schemas.canonical import CanonicalInvoice
from aap.schemas.source import SourceInvoice
from aap.validation.engine import RuleContext, run_rules, summarize

from .status import Status, set_status

log = get_logger(__name__)

NextSteps = list[tuple[str, uuid.UUID]]


class StageSkipped(Exception):
    """The document is not in the state this stage expects (already done or closed)."""


def _lock(session: Session, document_id: uuid.UUID) -> Document:
    doc = session.scalar(select(Document).where(Document.id == document_id).with_for_update())
    if doc is None:
        raise StageSkipped(f"document {document_id} not found")
    return doc


def _expect(doc: Document, *statuses: Status) -> None:
    if doc.status not in {s.value for s in statuses}:
        raise StageSkipped(f"document {doc.id} is {doc.status}, stage expects {[s.value for s in statuses]}")


def latest_version(session: Session, document_id: uuid.UUID, kinds: list[str]) -> InvoiceVersion | None:
    return session.scalars(select(InvoiceVersion).where(InvoiceVersion.document_id == document_id,
                                                        InvoiceVersion.kind.in_(kinds))
                           .order_by(InvoiceVersion.version.desc()).limit(1)).first()


def _new_version(session: Session, document_id: uuid.UUID, kind: str, inv: CanonicalInvoice, *,
                 parent: InvoiceVersion | None = None, extraction_run_id: uuid.UUID | None = None,
                 created_by: str = "pipeline") -> InvoiceVersion:
    n = session.scalar(select(func.coalesce(func.max(InvoiceVersion.version), 0))
                       .where(InvoiceVersion.document_id == document_id)) or 0
    v = InvoiceVersion(id=uuid.uuid4(), document_id=document_id, version=n + 1, kind=kind,
                       parent_version_id=parent.id if parent else None, extraction_run_id=extraction_run_id,
                       canonical_invoice=inv.model_dump(mode="json"), created_by=created_by)
    session.add(v)
    session.flush()
    return v


# ----------------------------------------------------------------- stage 2
def stage_unpack(session: Session, doc: Document) -> NextSteps:
    _expect(doc, Status.RECEIVED)
    result = unpack(session, doc)
    return [("parse", c.id) for c in result.children if c.status == Status.RECEIVED.value]


# ----------------------------------------------------------------- stage 3
def stage_parse(session: Session, doc: Document) -> NextSteps:
    _expect(doc, Status.RECEIVED)
    if doc.file_type in ("eml", "msg"):
        return stage_unpack(session, doc)
    existing = session.scalar(select(func.count()).select_from(Page).where(Page.document_id == doc.id))
    if not existing:
        store = FileStore()
        try:
            pages = parse_document(doc.file_type, store.read(doc.storage_path))
        except ParseError as exc:
            set_status(session, doc, Status.FAILED, reason=f"parse error: {exc}")
            return []
        for p in pages:
            image_path = store.put_page_image(str(doc.id), p.page_no, p.image_png) if p.image_png else None
            session.add(Page(document_id=doc.id, page_no=p.page_no, kind=p.kind, text=p.text, words=p.words,
                             tables=p.tables, image_path=image_path, width=p.width, height=p.height,
                             image_width=p.image_width, image_height=p.image_height))
        audit(session, "parse.completed", document_id=doc.id, pages=len(pages),
              kinds=sorted({p.kind for p in pages}))
    set_status(session, doc, Status.PARSED)
    return [("route", doc.id)]


# ----------------------------------------------------------------- stage 4
def stage_route(session: Session, doc: Document) -> NextSteps:
    from aap.config import get_settings

    _expect(doc, Status.PARSED)
    threshold = get_settings().text_quality_threshold
    routes = {}
    for page in session.scalars(select(Page).where(Page.document_id == doc.id).order_by(Page.page_no)):
        route, score, details = route_page(page.kind, page.text or "", page.words or [],
                                           float(page.height) if page.height else None, threshold)
        page.route, page.text_quality_score, page.quality_details = route, score, details
        routes[page.page_no] = route
    audit(session, "route.completed", document_id=doc.id, routes=routes)
    set_status(session, doc, Status.ROUTED)
    return [("extract", doc.id)]


# ----------------------------------------------------------------- stage 5
def page_inputs(session: Session, doc: Document, store: FileStore | None = None) -> list[PageInput]:
    store = store or FileStore()
    out = []
    for p in session.scalars(select(Page).where(Page.document_id == doc.id).order_by(Page.page_no)):
        out.append(PageInput(page_no=p.page_no, kind=p.kind, route=p.route or "image_only", text=p.text or "",
                             words=p.words or [], image_png=store.read(p.image_path) if p.image_path else None,
                             width=float(p.width) if p.width else None, height=float(p.height) if p.height else None,
                             image_width=p.image_width, image_height=p.image_height))
    return out


def stage_extract(session: Session, doc: Document) -> NextSteps:
    _expect(doc, Status.ROUTED)
    pages = page_inputs(session, doc)
    llm = get_llm()
    t0 = time.monotonic()
    try:
        out = extract(pages, llm, sha256=doc.sha256)
    except LlmError as exc:
        # This stage's transaction rolls back (and holds the document lock): run_stage records the
        # failed run, with the model's raw output, after it has ended.
        exc.failed_run = dict(document_id=doc.id, backend=llm.backend, model=get_settings().extract_model,  # type: ignore[attr-defined]
                              prompt_version=PROMPT_VERSION, route_summary={str(p.page_no): p.route for p in pages},
                              raw_output=[{"raw": exc.raw}] if exc.raw else None, status="error",
                              error=str(exc)[:2000], duration_ms=int((time.monotonic() - t0) * 1000))
        raise
    src = SourceInvoice.from_extracted(out.extracted)
    stats = source_check(src, pages, out.evidence)
    src.flags = [*(doc.flags or []), *out.flags]
    run = ExtractionRun(document_id=doc.id, backend=out.backend, model=out.model, prompt_version=PROMPT_VERSION,
                        route_summary={str(p.page_no): p.route for p in pages}, requests=out.requests,
                        raw_output=out.raw, source_invoice=src.model_dump(mode="json"), status="ok",
                        duration_ms=int((time.monotonic() - t0) * 1000))
    session.add(run)
    session.flush()
    audit(session, "extract.completed", document_id=doc.id, run_id=run.id, backend=out.backend, model=out.model,
          prompt_version=PROMPT_VERSION, requests=out.requests, source_check=stats, llm_output=out.raw)
    set_status(session, doc, Status.EXTRACTED)
    return [("normalize", doc.id)]


# ----------------------------------------------------------------- stage 6
def stage_normalize(session: Session, doc: Document) -> NextSteps:
    _expect(doc, Status.EXTRACTED)
    run = session.scalars(select(ExtractionRun).where(ExtractionRun.document_id == doc.id,
                                                      ExtractionRun.status == "ok")
                          .order_by(ExtractionRun.created_at.desc()).limit(1)).first()
    if run is None:
        set_status(session, doc, Status.FAILED, reason="no extraction result")
        return []
    src = SourceInvoice.model_validate(run.source_invoice)
    inv = normalize(src)
    v = _new_version(session, doc.id, "normalized", inv, extraction_run_id=run.id)
    audit(session, "normalize.completed", document_id=doc.id, version=v.version, country_hint=inv.country_hint)
    set_status(session, doc, Status.NORMALIZED)
    return [("map", doc.id)]


# ----------------------------------------------------------------- stage 7
def _sender(session: Session, doc: Document) -> str | None:
    if doc.email_message_id:
        msg = session.get(EmailMessage, doc.email_message_id)
        return msg.from_addr if msg else None
    return None


def _write_evidence(session: Session, version: InvoiceVersion, inv: CanonicalInvoice) -> None:
    for path, f in inv.iter_fields():
        if f.value is None and not f.flags and f.source_value is None:
            continue
        session.add(FieldEvidence(invoice_version_id=version.id, field_path=path,
                                  value=None if f.value is None else str(f.value), source_value=f.source_value,
                                  page=f.page, bbox=f.bbox, method=str(f.method), flags=list(f.flags),
                                  confidence=f.confidence))


def stage_map(session: Session, doc: Document) -> NextSteps:
    _expect(doc, Status.NORMALIZED)
    base = latest_version(session, doc.id, ["normalized"])
    if base is None:
        set_status(session, doc, Status.FAILED, reason="no normalized version")
        return []
    ctx = MapContext(session=session, llm=get_llm(), ref=load_reference(session), document=doc,
                     sender=_sender(session, doc))
    inv = map_invoice(ctx, CanonicalInvoice.model_validate(base.canonical_invoice))
    v = _new_version(session, doc.id, "mapped", inv, parent=base, extraction_run_id=base.extraction_run_id)
    _write_evidence(session, v, inv)
    audit(session, "map.completed", document_id=doc.id, version=v.version, vendor_id=inv.vendor_id.value,
          vendor_site_id=inv.vendor_site_id.value, org_id=inv.org_id.value)
    set_status(session, doc, Status.MAPPED)
    return [("validate", doc.id)]


# ----------------------------------------------------------------- stage 8
def validate_version(session: Session, doc: Document, version: InvoiceVersion, *,
                     today: date | None = None) -> dict[str, list[str]]:
    try:
        adapter = get_read_adapter()
    except Exception:  # noqa: BLE001 - validation still runs; live checks report "not available"
        adapter = None
    inv = CanonicalInvoice.model_validate(version.canonical_invoice)
    ctx = RuleContext(inv=inv, session=session, ref=load_reference(session), adapter=adapter, document=doc,
                      today=today or date.today())
    results = run_rules(ctx)
    run_no = (session.scalar(select(func.coalesce(func.max(ValidationResult.run_no), 0))
                             .where(ValidationResult.invoice_version_id == version.id)) or 0) + 1
    for r in results:
        session.add(ValidationResult(invoice_version_id=version.id, run_no=run_no, rule_id=r.rule_id,
                                     rule_name=r.name, result=r.result, message=r.message,
                                     field_paths=r.field_paths, details=r.details))
    summary = summarize(results)
    audit(session, "validate.completed", document_id=doc.id, version=version.version, run_no=run_no, **summary)
    return summary


def stage_validate(session: Session, doc: Document) -> NextSteps:
    _expect(doc, Status.MAPPED)
    version = latest_version(session, doc.id, ["mapped", "user", "vendor_reply"])
    if version is None:
        set_status(session, doc, Status.FAILED, reason="no mapped version")
        return []
    validate_version(session, doc, version)
    set_status(session, doc, Status.VALIDATED)
    return [("agents", doc.id)]


def stage_agents(document_id: uuid.UUID, *, final_attempt: bool = True) -> NextSteps:
    """Agent layer (plan 8). Runs outside the stage lock: the graph manages its own transactions."""
    from aap.agents.runner import run_agents

    with session_scope() as session:
        doc = session.get(Document, document_id)
        if doc is None or doc.status not in (Status.VALIDATED.value, Status.AGENTS_RUNNING.value):
            raise StageSkipped(f"document {document_id} is {doc.status if doc else 'missing'}, agents expect VALIDATED")
    try:
        run_agents(document_id)
    except Exception as exc:
        log.exception("agents.error", document_id=str(document_id), final=final_attempt)
        if not final_attempt:
            raise
        # The invoice is not lost: it goes to review with the error.
        with session_scope() as session:
            doc = session.get(Document, document_id)
            if doc is not None:
                session.add(ReviewTask(document_id=doc.id, reason_codes=["AGENTS_ERROR"],
                                       summary=f"The agents stopped: {type(exc).__name__}: {exc}"[:2000],
                                       suggested_action="review_manually", status="open"))
                set_status(session, doc, Status.IN_REVIEW, reason="agents error", actor="agents")
    return []


STAGES: dict[str, Callable[[Session, Document], NextSteps]] = {
    "parse": stage_parse,
    "route": stage_route,
    "extract": stage_extract,
    "normalize": stage_normalize,
    "map": stage_map,
    "validate": stage_validate,
}
LLM_STAGES = {"extract", "map"}
AGENT_STAGES = {"agents"}
ERP_STAGES = {"submit", "result"}


def run_stage(stage: str, document_id: uuid.UUID | str, *, final_attempt: bool = True) -> NextSteps:
    """Runs one stage in its own transaction. On error: FAILED on the final attempt, else re-raise (retry)."""
    doc_id = uuid.UUID(str(document_id))
    if stage == "agents":
        try:
            return stage_agents(doc_id, final_attempt=final_attempt)
        except StageSkipped as skip:
            log.info("stage.skipped", stage=stage, document_id=str(doc_id), reason=str(skip))
            return []
    if stage == "result":
        from aap.submit.submit import check_result

        # Still running in Oracle -> poll again (the worker defers it by POLL_SECONDS).
        try:
            return [("result", doc_id)] if check_result(doc_id) == "submitted" else []
        except LookupError:   # the document was deleted while Oracle was importing
            log.info("stage.skipped", stage=stage, document_id=str(doc_id), reason="document not found")
            return []
    try:
        with session_scope() as session:
            doc = _lock(session, doc_id)
            if stage == "submit":
                from aap.submit.submit import stage_submit

                steps = stage_submit(session, doc)
            else:
                steps = STAGES[stage](session, doc)
            log.info("stage.done", stage=stage, document_id=str(doc_id), status=doc.status)
            return steps
    except StageSkipped as skip:
        log.info("stage.skipped", stage=stage, document_id=str(doc_id), reason=str(skip))
        return []
    except Exception as exc:
        if failed_run := getattr(exc, "failed_run", None):
            with session_scope() as session:
                session.add(ExtractionRun(**failed_run))
        final = final_attempt or not getattr(exc, "retryable", True)
        log.exception("stage.error", stage=stage, document_id=str(doc_id), final=final)
        if not final:
            raise
        with session_scope() as session:
            doc = session.get(Document, doc_id)
            if doc is None:
                return []
            if stage in ERP_STAGES:
                # Oracle unreachable: the approved invoice is not re-extracted (FAILED restarts at stage 3);
                # it goes to review and the clerk approves again (new version, new idempotency key).
                session.add(ReviewTask(document_id=doc.id, reason_codes=["ORACLE_UNAVAILABLE"],
                                       summary=f"Submitting to Oracle failed: {type(exc).__name__}: {exc}"[:2000],
                                       suggested_action="retry_submission", status="open"))
                set_status(session, doc, Status.ORACLE_ERROR, reason=f"{stage}: {type(exc).__name__}"[:2000],
                           actor="submit")
            else:
                set_status(session, doc, Status.FAILED, reason=f"{stage}: {type(exc).__name__}: {exc}"[:2000])
        return []


def run_pipeline_inline(document_id: uuid.UUID | str) -> list[uuid.UUID]:
    """Runs all stages in-process for a document and its children. Returns every document id touched."""
    return run_pipeline_inline_from(document_id, "parse")


def run_pipeline_inline_from(document_id: uuid.UUID | str, stage: str) -> list[uuid.UUID]:
    queue: list[tuple[str, uuid.UUID]] = [(stage, uuid.UUID(str(document_id)))]
    touched: list[uuid.UUID] = []
    while queue:
        stage, doc_id = queue.pop(0)
        if doc_id not in touched:
            touched.append(doc_id)
        if stage == "result":
            time.sleep(0.5)   # inline polling of the Oracle import
        queue.extend(run_stage(stage, doc_id))
    return touched


def document_summary(session: Session, document_id: uuid.UUID) -> dict[str, Any]:
    doc = session.get(Document, document_id)
    if doc is None:
        return {}
    version = latest_version(session, document_id, ["mapped", "user", "vendor_reply"])
    results = []
    if version is not None:
        last_run = session.scalar(select(func.max(ValidationResult.run_no))
                                  .where(ValidationResult.invoice_version_id == version.id))
        results = list(session.scalars(select(ValidationResult).where(
            ValidationResult.invoice_version_id == version.id, ValidationResult.run_no == last_run)))
    return {
        "document_id": str(doc.id), "status": doc.status, "flags": doc.flags, "file_type": doc.file_type,
        "filename": doc.filename, "error": doc.error,
        "invoice": version.canonical_invoice if version else None,
        "version": version.version if version else None,
        "validation": [{"rule_id": r.rule_id, "result": r.result, "message": r.message} for r in results],
    }
