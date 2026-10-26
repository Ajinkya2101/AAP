"""Minimal UI (plan 9): server-rendered Jinja2 + HTMX, no build step.

Dashboard (figures for everything processed so far, manual upload, the records with
search, paging and delete; the upload queue with live pipeline status), invoice detail (approve / reject with per-line corrections, delete) and admin.
Every action calls the same code as the JSON API. No authentication in the dev build:
"acting as" is a cookie.
"""

from __future__ import annotations

import json
import uuid
from collections import Counter
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from aap.config import get_settings
from aap.db.models import (
    AgentDecision,
    Document,
    DocumentStatusHistory,
    EvalRun,
    GlHistoryLine,
    Hold,
    InvoiceVersion,
    RefSyncRun,
    ReviewTask,
    Submission,
    ValueMapProposal,
)
from aap.db.session import session_scope
from aap.pipeline.stages import document_summary
from aap.schemas.canonical import CanonicalInvoice

router = APIRouter(prefix="/ui", include_in_schema=False)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
USER_COOKIE = "aap_user"

# Statuses a person acts on come first in the filter chips.
STATUS_ORDER = ["IN_REVIEW", "ON_HOLD", "ORACLE_ERROR", "FAILED", "APPROVED", "SUBMITTED",
                "CREATED", "REJECTED", "AGENTS_RUNNING", "VALIDATED", "RECEIVED", "PARSED", "ROUTED", "EXTRACTED",
                "NORMALIZED", "MAPPED", "NOT_INVOICE", "DUPLICATE"]
# The correction form: printed values (SourceInvoice), in reading order.
SOURCE_HEADER = ["document_type", "vendor_name", "vendor_tax_id", "vendor_email", "vendor_address", "location_ref",
                 "invoice_number", "invoice_date", "due_date", "po_number", "payment_terms", "currency", "subtotal",
                 "tax_label", "tax_rate", "tax_amount", "freight_amount", "total", "bank_account", "bank_routing", "iban",
                 "bank_change_notice"]
SOURCE_LINE = ["description", "item_code", "quantity", "uom", "unit_price", "amount"]
HEADER_FIELDS = ["invoice_type", "vendor_name", "vendor_id", "vendor_site_code", "vendor_tax_id", "invoice_num",
                 "invoice_date", "due_date", "gl_date", "currency_code", "terms_name", "po_number", "location_ref",
                 "cost_center", "subtotal", "tax_amount", "freight_amount", "invoice_amount", "bank_account",
                 "bank_routing", "iban", "bank_change_notice", "org_id"]


def _db() -> Session:
    with session_scope() as session:
        yield session  # type: ignore[misc]


DB = Annotated[Session, Depends(_db)]


def _user(request: Request) -> str:
    return request.cookies.get(USER_COOKIE) or "clerk"


def _render(request: Request, session: Session, name: str, nav: str, **ctx: Any) -> HTMLResponse:
    return templates.TemplateResponse(request, name, {
        "nav": nav, "user": _user(request), "flash": request.query_params.get("msg"),
        "flash_kind": request.query_params.get("kind", ""), **ctx})


def _back(url: str, msg: str | None = None, kind: str = "") -> RedirectResponse:
    if msg:
        url += ("&" if "?" in url else "?") + f"msg={quote(msg)}" + (f"&kind={kind}" if kind else "")
    return RedirectResponse(url, status_code=303)


# -------------------------------------------------------------- dashboard
PAGE_SIZE = 50
NEEDS_PERSON = ("IN_REVIEW", "ON_HOLD", "ORACLE_ERROR")
DONE = ("CREATED", "REJECTED", "NOT_INVOICE", "DUPLICATE", "FAILED")
WAITING = "WAITING"          # records filter: every status that waits for a person
DAYS = 14


def records_url(status: str | None = None, q: str | None = None, page: int = 1, base: str = "/ui") -> str:
    """Link to the records with a filter, a search and a page (templates use it for chips and paging)."""
    params = {k: v for k, v in (("status", status), ("q", q), ("page", page if page > 1 else None)) if v}
    return base + ("?" + urlencode(params) if params else "")


templates.env.globals["records_url"] = records_url


def _invoices():  # noqa: ANN202
    """Invoices only (email containers are shown through their attachments; eval runs are separate)."""
    return (Document.source != "eval", Document.file_type.not_in(["eml", "msg"]))


def _status_counts(session: Session) -> list[tuple[str, int]]:
    counts = dict(session.execute(select(Document.status, func.count()).where(*_invoices())
                                  .group_by(Document.status)).all())
    return [(s, counts[s]) for s in STATUS_ORDER if s in counts] + \
        [(s, n) for s, n in counts.items() if s not in STATUS_ORDER]


def _latest_versions():  # noqa: ANN202
    """Subquery: (document_id, version) of each document's newest invoice version."""
    return (select(InvoiceVersion.document_id, func.max(InvoiceVersion.version).label("v"))
            .group_by(InvoiceVersion.document_id).subquery())


def _records(session: Session, status: str | None, q: str | None, page: int) -> dict[str, Any]:
    """One page of the records: filtered by status, searched by invoice number, supplier or file name."""
    counts = _status_counts(session)
    sub = _latest_versions()
    inv = InvoiceVersion.canonical_invoice
    rows_q = (select(Document, inv)
              .outerjoin(sub, sub.c.document_id == Document.id)
              .outerjoin(InvoiceVersion, (InvoiceVersion.document_id == Document.id)
                         & (InvoiceVersion.version == sub.c.v))
              .where(*_invoices()))
    if status:
        rows_q = rows_q.where(Document.status.in_(NEEDS_PERSON) if status == WAITING else Document.status == status)
    q = (q or "").strip()
    if q:
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        rows_q = rows_q.where(or_(*(col.ilike(like, escape="\\") for col in (
            Document.filename, inv["invoice_num"]["value"].astext, inv["vendor_name"]["value"].astext))))
    matching = session.scalar(select(func.count()).select_from(rows_q.with_only_columns(Document.id).subquery())) or 0
    pages = max(1, -(-matching // PAGE_SIZE))
    page = min(max(page, 1), pages)       # a delete can empty the last page
    rows = session.execute(rows_q.order_by(Document.created_at.desc(), Document.id)
                           .limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE)).all()
    reasons: dict[uuid.UUID, list[str]] = {}
    for t in session.scalars(select(ReviewTask).where(ReviewTask.document_id.in_([d.id for d, _ in rows]),
                                                      ReviewTask.status == "open")):
        reasons.setdefault(t.document_id, []).extend(t.reason_codes)

    def val(invoice: dict[str, Any] | None, k: str) -> Any:
        return ((invoice or {}).get(k) or {}).get("value")

    docs = [{"id": d.id, "created_at": d.created_at, "status": d.status, "file_type": d.file_type, "source": d.source,
             "filename": d.filename, "vendor": val(i, "vendor_name"), "invoice_num": val(i, "invoice_num"),
             "amount": val(i, "invoice_amount"), "currency": val(i, "currency_code"),
             "reasons": list(dict.fromkeys(reasons.get(d.id, [])))} for d, i in rows]
    by_status = dict(counts)
    return {"docs": docs, "status": status, "q": q, "page": page, "pages": pages, "matching": matching,
            "first": (page - 1) * PAGE_SIZE + 1 if docs else 0, "last": (page - 1) * PAGE_SIZE + len(docs),
            "counts": counts, "total": sum(by_status.values()),
            "waiting": sum(by_status.get(s, 0) for s in NEEDS_PERSON), "WAITING": WAITING}


def _bars(pairs: list[tuple[str, int]]) -> list[dict[str, Any]]:
    """Rows for a bar list: the longest bar is the full width."""
    top = max((n for _, n in pairs), default=0)
    return [{"label": k, "n": n, "pct": round(100 * n / top, 1) if top else 0} for k, n in pairs]


def _figures(session: Session) -> dict[str, Any]:
    """The figure strip: counts over every invoice record (cheap: one grouped count)."""
    by_status = dict(_status_counts(session))
    return {"total": sum(by_status.values()), "needs": sum(by_status.get(s, 0) for s in NEEDS_PERSON),
            "moving": sum(n for s, n in by_status.items() if s not in NEEDS_PERSON and s not in DONE),
            "posted": by_status.get("CREATED", 0), "failed": by_status.get("FAILED", 0), "WAITING": WAITING}


def _stats(session: Session) -> dict[str, Any]:
    """The breakdown under the records: per day, review reasons, suppliers, currencies, file types."""
    today = session.scalar(select(func.current_date()))
    start = today - timedelta(days=DAYS - 1)
    day = func.date(Document.created_at)
    per_day = dict(session.execute(select(day, func.count()).where(*_invoices(), day >= start).group_by(day)).all())
    days = [(start + timedelta(days=i), per_day.get(start + timedelta(days=i), 0)) for i in range(DAYS)]
    top_day = max(n for _, n in days)

    reasons: Counter[str] = Counter()
    for codes in session.scalars(select(ReviewTask.reason_codes).join(Document, Document.id == ReviewTask.document_id)
                                 .where(ReviewTask.status == "open", *_invoices())):
        reasons.update(codes or [])

    sub = _latest_versions()
    inv = InvoiceVersion.canonical_invoice
    suppliers: dict[tuple[str, str], list[Any]] = {}
    currencies: dict[str, list[Any]] = {}
    for vendor, amount, currency in session.execute(
            select(inv["vendor_name"]["value"].astext, inv["invoice_amount"]["value"].astext,
                   inv["currency_code"]["value"].astext)
            .join(sub, (sub.c.document_id == InvoiceVersion.document_id) & (sub.c.v == InvoiceVersion.version))
            .join(Document, Document.id == InvoiceVersion.document_id).where(*_invoices())):
        try:
            value = Decimal(amount) if amount else Decimal(0)
        except InvalidOperation:
            value = Decimal(0)
        for bucket, key in ((suppliers, (vendor or "(not identified)", currency or "")), (currencies, currency or "")):
            row = bucket.setdefault(key, [0, Decimal(0)])
            row[0] += 1
            row[1] += value
    file_types = session.execute(select(Document.file_type, func.count()).where(*_invoices())
                                 .group_by(Document.file_type).order_by(func.count().desc())).all()
    return {
        "reasons": _bars(reasons.most_common(8)),
        # One file type is not a comparison: the section is shown only when there are several.
        "file_types": _bars([(t, n) for t, n in file_types]) if len(file_types) > 1 else [],
        "days": [{"date": d, "n": n, "pct": round(100 * n / top_day, 1) if top_day else 0} for d, n in days],
        "days_total": sum(n for _, n in days), "top_day": top_day,
        "suppliers": [{"vendor": k[0], "currency": k[1], "n": v[0], "amount": v[1]}
                      for k, v in sorted(suppliers.items(), key=lambda kv: (-kv[1][0], kv[0]))[:8]],
        "currencies": [{"currency": k or "(none)", "n": v[0], "amount": v[1]}
                       for k, v in sorted(currencies.items(), key=lambda kv: -kv[1][0])],
    }


@router.get("", response_class=HTMLResponse)
def dashboard(request: Request, session: DB, status: str | None = None, q: str | None = None,
              page: int = 1) -> HTMLResponse:
    return _render(request, session, "dashboard.html", "dashboard", figures=_figures(session), stats=_stats(session),
                   max_upload_mb=get_settings().max_upload_mb, **_records(session, status, q, page))


@router.get("/partials/figures", response_class=HTMLResponse)
def figures_partial(request: Request, session: DB) -> HTMLResponse:
    return templates.TemplateResponse(request, "_figures.html", {"figures": _figures(session)})


@router.get("/partials/stats", response_class=HTMLResponse)
def stats_partial(request: Request, session: DB) -> HTMLResponse:
    return templates.TemplateResponse(request, "_stats.html", {"stats": _stats(session)})


@router.get("/partials/documents", response_class=HTMLResponse)
def documents_partial(request: Request, session: DB, status: str | None = None, q: str | None = None,
                      page: int = 1) -> HTMLResponse:
    return templates.TemplateResponse(request, "_documents.html", _records(session, status, q, page))


# ----------------------------------------------------------------- upload
# Statuses the automatic pipeline is still working through (the upload queue keeps polling these).
PIPELINE = {"RECEIVED", "PARSED", "ROUTED", "EXTRACTED", "NORMALIZED", "MAPPED", "VALIDATED", "AGENTS_RUNNING"}


def _take(file: UploadFile, by: str) -> dict[str, Any]:
    """Intake of one uploaded file, the same as POST /api/documents, in its own transaction
    (a rejected file does not undo the others). New documents are queued for stage 3."""
    from aap.ingest.filetypes import CONTAINERS
    from aap.ingest.intake import IntakeError, intake
    from aap.worker.tasks import enqueue_stage

    row: dict[str, Any] = {"filename": file.filename}
    try:
        with session_scope() as session:
            res = intake(session, file.file.read(), filename=file.filename, source="upload", uploaded_by=by,
                         allow_duplicate=get_settings().allow_duplicate_uploads)
            row.update(id=res.document.id, created=res.created, email=res.document.file_type in CONTAINERS,
                       status=res.document.status)
    except IntakeError as exc:
        row["error"] = str(exc)
    if row.get("created"):
        enqueue_stage("parse", row["id"])
    return row


@router.post("/upload/file")
def upload_file(request: Request, file: Annotated[UploadFile, File()]) -> dict[str, Any]:
    """One file of the upload queue. A file that is not accepted is an answer, not an HTTP error:
    {"filename", "error"} or {"filename", "id", "created", "email", "status"}."""
    row = _take(file, _user(request))
    if "id" in row:
        row["id"] = str(row["id"])
    return row


@router.get("/upload/status")
def upload_status(session: DB, ids: Annotated[list[uuid.UUID] | None, Query()] = None) -> list[dict[str, Any]]:
    """Where the queued files are in the pipeline. An email answers with its attachments;
    `moving` is false once nothing more happens without a person."""
    ids = (ids or [])[:100]
    docs = {d.id: d for d in session.scalars(select(Document).where(Document.id.in_(ids)))}
    children: dict[uuid.UUID, list[Document]] = {}
    for child in session.scalars(select(Document).where(Document.parent_id.in_(list(docs)))
                                 .order_by(Document.created_at)):
        children.setdefault(child.parent_id, []).append(child)
    out = []
    for i in ids:
        doc = docs.get(i)
        if doc is None:
            out.append({"id": str(i), "status": "DELETED", "moving": False, "children": []})
            continue
        kids = [{"id": str(c.id), "status": c.status, "filename": c.filename, "moving": c.status in PIPELINE}
                for c in children.get(i, [])]
        out.append({"id": str(i), "status": doc.status, "children": kids,
                    "moving": doc.status in PIPELINE or any(k["moving"] for k in kids)})
    return out


@router.post("/upload")
def upload(request: Request, files: Annotated[list[UploadFile], File()]) -> RedirectResponse:
    """The upload form without JavaScript: every file at once, then back to the dashboard with a summary."""
    by = _user(request)
    results = [_take(f, by) for f in files if f.filename]   # the browser sends one empty part when nothing was chosen
    if not results:
        return _back("/ui", "Choose at least one file", "error")
    failed = sum("error" in r for r in results)
    return _back("/ui", f"Uploaded {len(results) - failed} of {len(results)} files"
                 + (f"; {failed} not accepted" if failed else ""), "error" if failed else "")


@router.post("/whoami")
def whoami(request: Request, user: Annotated[str, Form()]) -> RedirectResponse:
    resp = RedirectResponse(request.headers.get("referer") or "/ui", status_code=303)
    resp.set_cookie(USER_COOKIE, user.strip()[:50] or "clerk", samesite="lax")
    return resp


# ----------------------------------------------------------------- detail
@router.get("/documents/{document_id}", response_class=HTMLResponse)
def document_page(request: Request, session: DB, document_id: uuid.UUID) -> HTMLResponse:
    from aap.submit.decision import APPROVABLE, DecisionError, approved_invoice

    doc = session.get(Document, document_id)
    if doc is None:
        raise HTTPException(404, "document not found")
    summary = document_summary(session, doc.id)
    inv = CanonicalInvoice.model_validate(summary["invoice"]) if summary.get("invoice") else None
    preview = None
    if inv is not None and doc.status in APPROVABLE:
        try:
            preview = approved_invoice(session, doc)[0]
        except DecisionError:
            preview = None
    source = None
    if doc.status in APPROVABLE:
        from aap.schemas.source import SourceInvoice
        from aap.submit.correct import latest_extraction

        run = latest_extraction(session, doc.id)
        source = SourceInvoice.model_validate(run.source_invoice) if run else None
    return _render(
        request, session, "document.html", "dashboard", doc=doc, inv=inv, preview=preview,
        source=source, source_header=SOURCE_HEADER, source_line=SOURCE_LINE,
        can_decide=preview is not None, validation=summary.get("validation") or [],
        header=[(k, getattr(inv, k)) for k in HEADER_FIELDS] if inv else [],
        tasks=session.scalars(select(ReviewTask).where(ReviewTask.document_id == doc.id, ReviewTask.status == "open")
                              .order_by(ReviewTask.created_at)).all(),
        decisions=session.scalars(select(AgentDecision).where(AgentDecision.document_id == doc.id)
                                  .order_by(AgentDecision.id)).all(),
        holds=session.scalars(select(Hold).where(Hold.document_id == doc.id)).all(),
        submissions=session.scalars(select(Submission).join(InvoiceVersion,
                                                            InvoiceVersion.id == Submission.invoice_version_id)
                                    .where(InvoiceVersion.document_id == doc.id)
                                    .order_by(Submission.created_at)).all(),
        history=session.scalars(select(DocumentStatusHistory).where(DocumentStatusHistory.document_id == doc.id)
                                .order_by(DocumentStatusHistory.id)).all())


@router.post("/documents/{document_id}/decision")
async def decide(request: Request, document_id: uuid.UUID) -> RedirectResponse:
    from aap.submit.decision import DecisionError, approve, approved_invoice, reject
    from aap.worker.tasks import enqueue_stage

    form = await request.form()
    action, note, by = form.get("action"), (form.get("note") or "").strip() or None, _user(request)
    url = f"/ui/documents/{document_id}"
    corrections: dict[str, Any] = {"lines": {}}
    for key, value in form.items():
        value = str(value).strip()
        kind, _, n = key.rpartition("_")
        if kind in ("po_line", "account") and n.isdigit():
            corrections["lines"].setdefault(n, {})
            if kind == "po_line" and value:
                if not value.isdigit():
                    return _back(url, f"PO line for line {n} must be a number", "error")
                corrections["lines"][n]["po_line_number"] = int(value)
            elif kind == "account" and value:
                corrections["lines"][n]["dist_account"] = value
    # A line with a PO line keeps it; the GL account is only for lines without one.
    for change in corrections["lines"].values():
        if "po_line_number" in change:
            change.pop("dist_account", None)
    with session_scope() as session:
        doc = session.get(Document, document_id)
        if doc is None:
            raise HTTPException(404, "document not found")
        try:
            if action == "approve":
                _keep_changes_only(corrections, approved_invoice(session, doc)[0])
                approve(session, doc, by=by, note=note, corrections=corrections)
            elif action == "reject":
                if not note:
                    return _back(url, "A reason is required to reject", "error")
                reject(session, doc, by=by, reason=note)
            else:
                return _back(url, "Unknown action", "error")
        except DecisionError as exc:
            return _back(url, str(exc), "error")
    if action == "approve":
        enqueue_stage("submit", document_id)
        return _back(url, "Approved: submitting to Oracle")
    return _back("/ui", "Rejected")


@router.post("/documents/{document_id}/corrections")
async def correct(request: Request, document_id: uuid.UUID) -> RedirectResponse:
    """The "Correct what was read" form. Every printed value is posted back; only the ones the
    clerk changed are a correction (apply_source_corrections compares them)."""
    from aap.submit.correct import correct_extraction
    from aap.submit.decision import DecisionError
    from aap.worker.tasks import enqueue_stage

    form = await request.form()
    url = f"/ui/documents/{document_id}"
    header: dict[str, Any] = {}
    lines: dict[str, dict[str, Any]] = {}
    remove: list[int] = []
    new_line: dict[str, Any] = {}
    for key, value in form.items():
        value = str(value)
        kind, _, rest = key.partition("_")
        if kind == "h":
            header[rest] = value
        elif kind == "l":
            n, _, name = rest.partition("_")
            lines.setdefault(n, {})[name] = value
        elif kind == "rm" and rest.isdigit():
            remove.append(int(rest))
        elif kind == "new" and value.strip():
            new_line[rest] = value
    for n in remove:
        lines.pop(str(n), None)
    with session_scope() as session:
        doc = session.get(Document, document_id)
        if doc is None:
            raise HTTPException(404, "document not found")
        try:
            run = correct_extraction(session, doc, by=_user(request), header=header, lines=lines,
                                     remove_lines=remove, add_lines=[new_line] if new_line else None,
                                     note=(str(form.get("note") or "")).strip() or None)
        except (DecisionError, ValueError) as exc:
            return _back(url, str(exc), "error")
        changed = len(run.raw_output[0]["changes"])
    enqueue_stage("normalize", document_id)
    return _back(url, f"{changed} value(s) corrected: checking the invoice again")


def _keep_changes_only(corrections: dict[str, Any], proposal: CanonicalInvoice) -> None:
    """The form posts the agents' proposal back; only what the clerk changed is a correction."""
    lines = {ln.line_number: ln for ln in proposal.lines}
    for n in list(corrections["lines"]):
        change, ln = corrections["lines"][n], lines.get(int(n))
        if ln is not None:
            if change.get("po_line_number") == ln.po_line_number.value:
                change.pop("po_line_number", None)
            if change.get("dist_account") and change["dist_account"] == ln.dist_account.value:
                change.pop("dist_account", None)
        if not change:
            del corrections["lines"][n]


@router.post("/documents/{document_id}/retry")
def retry_document(document_id: uuid.UUID) -> RedirectResponse:
    from aap.api import main as api

    with session_scope() as session:
        try:
            api.retry(session, document_id)
        except HTTPException as exc:
            return _back("/ui/admin", str(exc.detail), "error")
    return _back("/ui/admin", "Restarted")


def _changed(deleted: int) -> Response:
    """HTMX answer to a delete: the figures, the records and the breakdown refresh."""
    return Response(headers={"HX-Trigger": json.dumps({"documents-changed": {"deleted": deleted}})})


@router.post("/documents/delete", response_model=None)
def delete_documents(request: Request, ids: Annotated[list[uuid.UUID] | None, Form()] = None) -> Response:
    """Delete the records ticked in the table. Each has its own transaction; one that went with an
    earlier one (an email's last attachment takes the email along) or in another tab is skipped."""
    from aap.pipeline.delete import delete_document as run
    from aap.pipeline.delete import purge_files

    by, done, gone = _user(request), 0, set()
    for document_id in ids or []:
        if document_id in gone:
            continue
        with session_scope() as session:
            try:
                deleted = run(session, document_id, by=by)
            except LookupError:
                continue
            session.commit()
            purge_files(session, deleted)
        gone.update(deleted.ids)
        done += 1
    if request.headers.get("HX-Request"):
        return _changed(done)
    return _back("/ui", f"Deleted {done} record(s)")


@router.post("/documents/{document_id}/delete", response_model=None)
def delete_document(request: Request, document_id: uuid.UUID) -> Response:
    """Delete from the records table (HTMX: the figures and the table refresh) or from the
    detail page (plain form: redirect to the dashboard)."""
    from aap.pipeline.delete import delete_document as run
    from aap.pipeline.delete import purge_files

    htmx = bool(request.headers.get("HX-Request"))
    with session_scope() as session:
        try:
            deleted = run(session, document_id, by=_user(request))
        except LookupError:
            if htmx:   # already gone (deleted in another tab): just refresh
                return _changed(0)
            return _back("/ui", "That record no longer exists", "error")
        session.commit()
        purge_files(session, deleted)
    if htmx:
        return _changed(1)
    n = len(deleted.ids)
    return _back("/ui", "Record deleted" if n == 1 else f"Record deleted with {n - 1} related file(s)")


# ------------------------------------------------------------------ admin
@router.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request, session: DB) -> HTMLResponse:
    jobs: dict[str, dict[str, int]] = {}
    for queue, status, n in session.execute(text(
            "SELECT queue_name, status::text, count(*) FROM procrastinate_jobs GROUP BY 1, 2 ORDER BY 1")):
        jobs.setdefault(queue, {})[status] = n
    gl = session.execute(select(func.count(), func.count(GlHistoryLine.embedding),
                                func.count().filter(GlHistoryLine.source == "review"))).one()
    return _render(
        request, session, "admin.html", "admin", jobs=jobs,
        job_states=["todo", "doing", "succeeded", "failed", "cancelled", "aborted"],
        refsync=session.scalars(select(RefSyncRun).order_by(RefSyncRun.id.desc()).limit(1)).first(),
        gl={"total": gl[0], "embedded": gl[1], "review": gl[2]},
        proposals=session.scalars(select(ValueMapProposal).where(ValueMapProposal.status == "pending")
                                  .order_by(ValueMapProposal.id.desc()).limit(100)).all(),
        failed=session.scalars(select(Document).where(Document.status == "FAILED", Document.source != "eval")
                               .order_by(Document.updated_at.desc()).limit(50)).all(),
        evals=session.scalars(select(EvalRun).order_by(EvalRun.created_at.desc()).limit(20)).all())


@router.post("/admin/proposals/{proposal_id}")
def admin_proposal(request: Request, proposal_id: int, action: Annotated[str, Form()]) -> RedirectResponse:
    from aap.mapping.proposals import ProposalError, decide_proposal

    with session_scope() as session:
        try:
            p = decide_proposal(session, proposal_id, by=_user(request), approve=action == "approve")
        except (LookupError, ProposalError) as exc:
            return _back("/ui/admin", str(exc), "error")
        verb = "now maps to" if p.status == "approved" else "will not map to"
        msg = f'"{p.source_value}" {verb} {p.oracle_value}'
    return _back("/ui/admin", msg)


@router.post("/admin/refsync", response_class=HTMLResponse)
def admin_refsync() -> HTMLResponse:
    from aap.worker.app import defer

    return HTMLResponse(f"queued (job {defer('aap.refsync', 'periodic', triggered_by='admin-ui')})")
