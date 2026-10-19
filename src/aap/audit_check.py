"""Audit completeness check (plan 12, Phase 10: "every decision is traceable").

    python -m aap.audit_check [--since 2026-09-01]     # exit 1 when a problem is found

Checks, per document (eval documents excluded):
  status   the status history is an unbroken chain from RECEIVED to the current status,
           and every transition has its audit event
  agents   every agent / mapping decision has its audit event; every finished run has an outcome
  review   a document Need Attention (IN_REVIEW / ON_HOLD / ORACLE_ERROR) has an open review task
  approval every approved invoice version has an `invoice.approved` event by a named person
  oracle   every submission sent has `submit.sent`; created/rejected ones have their result event
  mail     every uploaded email has its raw .eml / .msg in the filestore
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from aap.db.models import (
    AgentDecision,
    AgentRun,
    AuditEvent,
    Document,
    DocumentStatusHistory,
    EmailMessage,
    InvoiceVersion,
    ReviewTask,
    Submission,
)
from aap.db.session import session_scope
from aap.filestore import FileStore

NEEDS_PERSON = {"IN_REVIEW", "ON_HOLD", "ORACLE_ERROR"}


def check(session: Session, since: date | None = None) -> list[str]:
    problems: list[str] = []
    q = select(Document).where(Document.source != "eval")
    if since:
        q = q.where(Document.created_at >= since)
    docs = {d.id: d for d in session.scalars(q)}
    if not docs:
        return problems
    ids = list(docs)

    events: dict[Any, Counter[str]] = defaultdict(Counter)
    approvers: dict[Any, list[str]] = defaultdict(list)
    for doc_id, etype, actor in session.execute(select(AuditEvent.document_id, AuditEvent.event_type, AuditEvent.actor)
                                                .where(AuditEvent.document_id.in_(ids))):
        events[doc_id][etype] += 1
        if etype == "invoice.approved":
            approvers[doc_id].append(actor)

    # status chain
    history: dict[Any, list[DocumentStatusHistory]] = defaultdict(list)
    for h in session.scalars(select(DocumentStatusHistory).where(DocumentStatusHistory.document_id.in_(ids))
                             .order_by(DocumentStatusHistory.id)):
        history[h.document_id].append(h)
    for doc_id, doc in docs.items():
        chain = history[doc_id]
        current = "RECEIVED"
        for h in chain:
            if h.from_status != current:
                problems.append(f"{doc_id} status: {h.from_status}->{h.to_status} does not follow {current}")
            current = h.to_status
        if current != doc.status:
            problems.append(f"{doc_id} status: document is {doc.status} but history ends at {current}")
        if events[doc_id]["document.status"] < len(chain):
            problems.append(f"{doc_id} status: {len(chain)} transitions, "
                            f"{events[doc_id]['document.status']} audit events")

    # agents
    per_node: dict[Any, Counter[str]] = defaultdict(Counter)
    for doc_id, node in session.execute(select(AgentDecision.document_id, AgentDecision.node)
                                        .where(AgentDecision.document_id.in_(ids))):
        per_node[doc_id][node] += 1
    for doc_id, nodes in per_node.items():
        for node, n in nodes.items():
            # agent nodes audit as agent.<node>; model choices (aap.mapping.choose) under their own name
            event = node if "." in node else f"agent.{node}"
            if events[doc_id][event] < n:
                problems.append(f"{doc_id} decisions: {n} {node} decision(s), {events[doc_id][event]} audit events")
    for run in session.scalars(select(AgentRun).where(AgentRun.document_id.in_(ids), AgentRun.status == "done")):
        if not run.outcome:
            problems.append(f"{run.document_id} agents: run {run.id} finished without an outcome")

    # review
    open_tasks = {t for (t,) in session.execute(select(ReviewTask.document_id).where(
        ReviewTask.document_id.in_(ids), ReviewTask.status == "open"))}
    for doc_id, doc in docs.items():
        if doc.status in NEEDS_PERSON and doc_id not in open_tasks:
            problems.append(f"{doc_id} review: {doc.status} without an open review task")

    # approval
    approved = Counter(d for (d,) in session.execute(select(InvoiceVersion.document_id).where(
        InvoiceVersion.document_id.in_(ids), InvoiceVersion.kind == "approved")))
    for doc_id, n in approved.items():
        if len(approvers[doc_id]) < n:
            problems.append(f"{doc_id} approval: {n} approved version(s), {len(approvers[doc_id])} approval events")
        if any(not a for a in approvers[doc_id]):
            problems.append(f"{doc_id} approval: approved without a named person")

    # oracle
    for sub, doc_id in session.execute(select(Submission, InvoiceVersion.document_id)
                                       .join(InvoiceVersion, InvoiceVersion.id == Submission.invoice_version_id)
                                       .where(InvoiceVersion.document_id.in_(ids))):
        if sub.status != "pending" and not events[doc_id]["submit.sent"]:
            problems.append(f"{doc_id} oracle: submission {sub.idempotency_key} has no submit.sent event")
        if sub.status == "created" and not events[doc_id]["submit.created"]:
            problems.append(f"{doc_id} oracle: created in Oracle without a submit.created event")
        if sub.status == "error" and sub.oracle_errors != ["TIMEOUT"] and not events[doc_id]["submit.rejected"]:
            problems.append(f"{doc_id} oracle: rejected by Oracle without a submit.rejected event")

    # mail
    store = FileStore()
    for m in session.scalars(select(EmailMessage).where(EmailMessage.direction == "inbound",
                                                        EmailMessage.document_id.in_(ids))):
        if not m.raw_path or not store.absolute(m.raw_path).exists():
            problems.append(f"{m.document_id} mail: inbound email {m.id} has no stored .eml")
    return problems


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m aap.audit_check")
    p.add_argument("--since", type=date.fromisoformat, default=None)
    a = p.parse_args(argv)
    with session_scope() as s:
        problems = check(s, a.since)
        n = s.query(Document).filter(Document.source != "eval").count()
    for line in problems:
        print(line)
    print(f"audit check: {n} documents, {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
