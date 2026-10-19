"""Clerk corrections of what the model read from the invoice (the printed values).

A correction is a new extraction_run (backend "user", R-05: append-only) that holds the
corrected SourceInvoice and, in raw_output, each changed value next to what the model
read. The document goes back to EXTRACTED, so stages 6-8 and the agents run again on the
corrected values. The pair (model run, user run) is the label for fine-tuning the
extraction model (aap.finetune.export).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from aap.audit import audit
from aap.db.models import Document, ExtractionRun
from aap.pipeline.status import Status, set_status
from aap.schemas.fields import Field, Method
from aap.schemas.source import HEADER_FIELDS, LINE_FIELDS, SourceInvoice, SourceLine, _clean

from .decision import APPROVABLE, DecisionError, _check, _close_tasks

USER_BACKEND = "user"


def latest_extraction(session: Session, document_id: Any) -> ExtractionRun | None:
    return session.scalars(select(ExtractionRun).where(ExtractionRun.document_id == document_id,
                                                       ExtractionRun.status == "ok")
                           .order_by(ExtractionRun.created_at.desc()).limit(1)).first()


def _user_field(value: Any, by: str) -> Field[str]:
    raw = None if value is None else str(value)
    return Field[str](value=_clean(raw), source_value=raw if _clean(raw) else None, method=Method.user,
                      note=f"corrected by {by}")


def _line_values(ln: SourceLine) -> dict[str, str | None]:
    return {name: getattr(ln, name).value for name in LINE_FIELDS}


def apply_source_corrections(src: SourceInvoice, by: str, *, header: dict[str, Any] | None = None,
                             lines: dict[str, dict[str, Any]] | None = None,
                             remove_lines: list[int] | None = None,
                             add_lines: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Changes `src` in place. Returns the changes: [{"path", "model", "corrected"}]; a value
    equal to what is already there is not a change."""
    changes: list[dict[str, Any]] = []
    for name, value in (header or {}).items():
        if name not in HEADER_FIELDS:
            raise DecisionError(f"unknown header field {name!r}")
        new, old = _user_field(value, by), getattr(src, name).value
        if new.value != old:
            setattr(src, name, new)
            changes.append({"path": name, "model": old, "corrected": new.value})
    by_no = {ln.line_no: ln for ln in src.lines}
    for key, change in (lines or {}).items():
        ln = by_no.get(int(key))
        if ln is None:
            raise DecisionError(f"invoice has no line {key}")
        for name, value in change.items():
            if name not in LINE_FIELDS:
                raise DecisionError(f"unknown line field {name!r}")
            new, old = _user_field(value, by), getattr(ln, name).value
            if new.value != old:
                setattr(ln, name, new)
                changes.append({"path": f"lines[{ln.line_no}].{name}", "model": old, "corrected": new.value})
    for n in remove_lines or []:
        ln = by_no.get(int(n))
        if ln is None:
            raise DecisionError(f"invoice has no line {n}")
        src.lines.remove(ln)
        changes.append({"path": f"lines[{ln.line_no}]", "model": _line_values(ln), "corrected": None})
    for new_line in add_lines or []:
        unknown = set(new_line) - set(LINE_FIELDS) - {"position"}
        if unknown:
            raise DecisionError(f"unknown line field(s) {sorted(unknown)}")
        ln = SourceLine(line_no=0, **{name: _user_field(new_line.get(name), by) for name in LINE_FIELDS})
        if not any(_line_values(ln).values()):
            continue
        # "position" is the line's place in the printed order (1 = first); default: after the last line.
        at = min(max(int(new_line.get("position") or len(src.lines) + 1), 1), len(src.lines) + 1)
        src.lines.insert(at - 1, ln)
        changes.append({"path": "lines[+]", "model": None, "corrected": {"position": at, **_line_values(ln)}})
    for i, ln in enumerate(src.lines, start=1):
        ln.line_no = i
    return changes


def correct_extraction(session: Session, doc: Document, *, by: str, header: dict[str, Any] | None = None,
                       lines: dict[str, dict[str, Any]] | None = None, remove_lines: list[int] | None = None,
                       add_lines: list[dict[str, Any]] | None = None, note: str | None = None) -> ExtractionRun:
    """Records the correction and sets the document to EXTRACTED. The caller queues "normalize"."""
    _check(doc, APPROVABLE)
    base = latest_extraction(session, doc.id)
    if base is None:
        raise DecisionError("no extraction result to correct")
    src = SourceInvoice.model_validate(base.source_invoice)
    changes = apply_source_corrections(src, by, header=header, lines=lines, remove_lines=remove_lines,
                                       add_lines=add_lines)
    if not changes:
        raise DecisionError("nothing was changed")
    run = ExtractionRun(document_id=doc.id, backend=USER_BACKEND, model=f"correction by {by}"[:200],
                        prompt_version=base.prompt_version, route_summary=base.route_summary, requests=0,
                        raw_output=[{"corrects_run": str(base.id), "by": by, "note": note, "changes": changes}],
                        source_invoice=src.model_dump(mode="json"), status="ok", duration_ms=0)
    session.add(run)
    session.flush()
    from_status = doc.status
    _close_tasks(session, doc, by)
    set_status(session, doc, Status.EXTRACTED, reason=note or f"{len(changes)} value(s) corrected by {by}", actor=by)
    audit(session, "extraction.corrected", document_id=doc.id, actor=by, run_id=run.id, corrects_run=base.id,
          from_status=from_status, changes=changes, note=note)
    return run
