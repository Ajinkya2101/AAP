"""Phase 10: the minimal UI (plan 9) renders every page and its forms drive the same
flows as the API: approve with a clerk correction, reject, the upload queue, delete."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.integration]


@pytest.fixture(scope="module")
def ui(clean_aap, tmp_path_factory):
    try:
        import weasyprint  # noqa: F401
    except (ImportError, OSError):
        pytest.skip("WeasyPrint system libraries not available (run the e2e tests in the app container)")
    from fastapi.testclient import TestClient
    from tools.invoicegen.__main__ import generate

    from aap.api.main import app
    from aap.db.session import session_scope
    from aap.ingest.intake import intake
    from aap.pipeline.stages import run_pipeline_inline

    out = tmp_path_factory.mktemp("ui")
    manifest = generate(out, count=0, seed=11, scenarios=True, install_truth=True, dsn=clean_aap["EBS_RO_DSN"])
    docs = {}
    for item in manifest["items"]:
        scen = item["documents"][0]["expected"].get("scenario") if item["documents"] else None
        if scen in ("S1", "S3", "S4", "S11"):
            with session_scope() as s:
                doc_id = intake(s, (out / item["file"]).read_bytes(), filename=Path(item["file"]).name,
                                source="upload", uploaded_by="e2e").document.id
            docs[scen] = run_pipeline_inline(doc_id)[-1]
    client = TestClient(app)
    client.cookies.set("aap_user", "ui-test")
    return client, docs


def test_pages_render(ui):
    client, docs = ui
    r = client.get("/ui")
    assert r.status_code == 200 and "NW-INV-20457" in r.text and "ON_HOLD" in r.text
    for part in ("Invoices processed", "Need Attention", "Received per day", "Top suppliers",
                 "Upload invoices", "Records", "Delete selected", "Breakdown"):
        assert part in r.text, part
    assert r.text.index("Records") < r.text.index("Breakdown")              # the queue comes before the charts
    assert client.get("/ui/partials/documents?status=ON_HOLD").text.count("data-delete") == 1
    assert client.get("/ui?status=ON_HOLD&q=nw").status_code == 200
    assert "NW-INV-20457" in client.get("/ui/partials/documents").text
    assert "Open review reasons" in client.get("/ui/partials/stats").text
    assert "Created in Oracle" in client.get("/ui/partials/figures").text
    # search: invoice number, supplier or file name, case-insensitive; "%" is a literal
    found = client.get("/ui/partials/documents?q=nw-inv-20457").text
    assert found.count("data-delete") == 1 and "1–1 of 1" in found
    assert client.get("/ui/partials/documents?q=northwind").text.count("data-delete") >= 1
    assert "No invoices match" in client.get("/ui/partials/documents?q=%25").text
    waiting = client.get("/ui/partials/documents?status=WAITING").text
    assert waiting.count("data-delete") == 4 and "CREATED" not in waiting.split("<table>")[1]
    assert client.get("/ui/partials/documents?page=99").text.count("data-delete") == 4   # clamped to the last page
    page = client.get(f"/ui/documents/{docs['S1']}").text
    for part in ("QTY REC", "Approve and post to Oracle", "Agent decisions", "three_way", "History"):
        assert part in page, part
    assert "Delete record" in page
    assert "Approve and post to Oracle" in client.get(f"/ui/documents/{docs['S3']}").text   # no email to wait for
    assert client.get("/ui/outbox").status_code == 404
    admin = client.get("/ui/admin").text
    for part in ("Job queue", "GL history lines", "Eval runs"):
        assert part in admin, part
    assert "Mailbox" not in admin
    assert client.get("/static/htmx.min.js").status_code == 200


def test_approve_with_correction_then_reject(ui, monkeypatch):
    from sqlalchemy import select

    from aap.db.models import Document, InvoiceVersion
    from aap.db.session import session_scope

    client, docs = ui
    queued = []
    monkeypatch.setattr("aap.worker.tasks.enqueue_stage", lambda stage, doc: queued.append((stage, doc)))
    s4 = docs["S4"]
    # No agent pairing for S4 (sent straight to review): approving without corrections is refused.
    r = client.post(f"/ui/documents/{s4}/decision", data={"action": "approve"}, follow_redirects=False)
    assert r.status_code == 303 and "neither%20a%20PO%20line" in r.headers["location"]
    r = client.post(f"/ui/documents/{s4}/decision",
                    data={"action": "approve", "po_line_1": "1", "po_line_2": "2", "note": "resend is fine"},
                    follow_redirects=False)
    assert r.status_code == 303 and "Approved" in r.headers["location"]
    assert queued == [("submit", s4)]
    with session_scope() as s:
        assert s.get(Document, s4).status == "APPROVED"
        v = s.scalars(select(InvoiceVersion).where(InvoiceVersion.document_id == s4, InvoiceVersion.kind == "approved")).one()
        assert v.created_by == "ui-test"
        assert [ln["po_line_number"]["method"] for ln in v.canonical_invoice["lines"][:2]] == ["user", "user"]

    s1 = docs["S1"]
    r = client.post(f"/ui/documents/{s1}/decision", data={"action": "reject"}, follow_redirects=False)
    assert "reason%20is%20required" in r.headers["location"]
    client.post(f"/ui/documents/{s1}/decision", data={"action": "reject", "note": "not ours"})
    with session_scope() as s:
        assert s.get(Document, s1).status == "REJECTED"


def test_upload_queue(ui, monkeypatch):
    """The upload queue sends one file per request and then follows each document's status."""
    from aap.config import get_settings
    from aap.db.models import Document
    from aap.db.session import session_scope
    from aap.filestore import FileStore

    client, docs = ui
    monkeypatch.setattr(get_settings(), "allow_duplicate_uploads", False)
    queued = []
    monkeypatch.setattr("aap.worker.tasks.enqueue_stage", lambda stage, doc: queued.append((stage, doc)))
    with session_scope() as s:
        existing = FileStore().absolute(s.get(Document, docs["S1"]).storage_path).read_bytes()
    new = ("new.txt", b"INVOICE 77120\nNorthwind Traders\nTotal due: 120.00 USD\n", "text/plain")

    r = client.post("/ui/upload/file", files={"file": new}).json()
    assert r["created"] and r["status"] == "RECEIVED" and not r["email"]
    (stage, new_id), = queued
    assert stage == "parse" and str(new_id) == r["id"]
    with session_scope() as s:
        doc = s.get(Document, new_id)
        assert (doc.filename, doc.source, doc.uploaded_by) == ("new.txt", "upload", "ui-test")

    again = client.post("/ui/upload/file", files={"file": ("again.pdf", existing, "application/pdf")}).json()
    assert not again["created"] and again["id"] == str(docs["S1"])
    empty = client.post("/ui/upload/file", files={"file": ("empty.pdf", b"", "application/pdf")})
    assert empty.status_code == 200 and empty.json() == {"filename": "empty.pdf", "error": "file is empty"}
    assert len(queued) == 1

    # Live status: still in the pipeline, at rest, and gone.
    import uuid

    gone = uuid.uuid4()
    status = {x["id"]: x for x in client.get("/ui/upload/status", params={"ids": [r["id"], str(docs["S1"]), str(gone)]}).json()}
    assert status[r["id"]]["status"] == "RECEIVED" and status[r["id"]]["moving"]
    assert not status[str(docs["S1"])]["moving"]
    assert status[str(gone)] == {"id": str(gone), "status": "DELETED", "moving": False, "children": []}
    assert client.get("/ui/upload/status").json() == []

    # Without JavaScript the form posts every file at once and comes back with a summary.
    files = [("files", ("again.pdf", existing, "application/pdf")), ("files", ("empty.pdf", b"", "application/pdf"))]
    r = client.post("/ui/upload", files=files, follow_redirects=False)
    assert r.status_code == 303 and "Uploaded%201%20of%202%20files" in r.headers["location"]
    page = client.get("/ui").text
    assert "Upload invoices" in page and 'id="upload-queue"' in page


def test_upload_duplicates_allowed_for_testing(ui, monkeypatch):
    from aap.config import get_settings
    from aap.db.models import Document
    from aap.db.session import session_scope
    from aap.filestore import FileStore

    client, docs = ui
    monkeypatch.setattr(get_settings(), "allow_duplicate_uploads", True)
    queued = []
    monkeypatch.setattr("aap.worker.tasks.enqueue_stage", lambda stage, doc: queued.append((stage, doc)))
    with session_scope() as s:
        existing = FileStore().absolute(s.get(Document, docs["S1"]).storage_path).read_bytes()

    r = client.post("/ui/upload/file", files={"file": ("again.pdf", existing, "application/pdf")}).json()
    assert r["created"] and r["id"] != str(docs["S1"])
    (stage, new_id), = queued
    assert stage == "parse" and str(new_id) == r["id"]


def test_delete(ui):
    """Delete from the records table (HTMX) and from the detail page (plain form): the document,
    its derived rows and its files go; the audit trail keeps a `document.deleted` event."""
    import json
    import uuid

    from sqlalchemy import func, select

    from aap.db.models import AuditEvent, Document, InvoiceVersion, Page, ReviewTask
    from aap.db.session import session_scope
    from aap.filestore import FileStore

    client, docs = ui
    s11, s3 = docs["S11"], docs["S3"]
    store = FileStore()
    with session_scope() as s:
        original = store.absolute(s.get(Document, s11).storage_path)
    pages = store.root / "pages" / str(s11)
    assert original.exists() and pages.exists()

    r = client.post(f"/ui/documents/{s11}/delete", headers={"HX-Request": "true"})
    assert r.status_code == 200 and json.loads(r.headers["HX-Trigger"]) == {"documents-changed": {"deleted": 1}}
    with session_scope() as s:
        assert s.get(Document, s11) is None
        for model in (InvoiceVersion, Page, ReviewTask):
            assert s.scalar(select(func.count()).select_from(model).where(model.document_id == s11)) == 0
        event = s.scalars(select(AuditEvent).where(AuditEvent.document_id == s11,
                                                   AuditEvent.event_type == "document.deleted")).one()
        assert event.actor == "ui-test"
    assert not original.exists() and not pages.exists()
    assert "COHO-5520" not in client.get("/ui/partials/documents").text
    assert client.get(f"/ui/documents/{s11}").status_code == 404

    # Delete selected: the ticked records in one request; one that is already gone is skipped.
    r = client.post("/ui/documents/delete", data={"ids": [str(s3), str(s11)]}, headers={"HX-Request": "true"})
    assert json.loads(r.headers["HX-Trigger"]) == {"documents-changed": {"deleted": 1}}
    r = client.post("/ui/documents/delete", headers={"HX-Request": "true"})          # nothing ticked
    assert json.loads(r.headers["HX-Trigger"]) == {"documents-changed": {"deleted": 0}}
    with session_scope() as s:
        assert s.get(Document, s3) is None

    s4 = docs["S4"]                                                                  # detail page: plain form
    r = client.post(f"/ui/documents/{s4}/delete", follow_redirects=False)
    assert r.status_code == 303 and "Record%20deleted" in r.headers["location"]
    r = client.post(f"/ui/documents/{s4}/delete", follow_redirects=False)
    assert "no%20longer%20exists" in r.headers["location"]
    assert client.delete(f"/api/documents/{uuid.uuid4()}").status_code == 404
