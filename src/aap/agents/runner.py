"""Start and resume the agent graph for a document (thread_id = document_id).

Checkpoints live in the aap database (langgraph-checkpoint-postgres); the tables
are created by `migrate` (aap_owner). The graph has no interrupts: one run goes
from triage to a review task.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from sqlalchemy import select

from aap.config import get_settings
from aap.db.models import AgentRun, Document, InvoiceVersion
from aap.db.session import session_scope
from aap.logging import get_logger
from aap.pipeline.status import Status, set_status

from .graph import GRAPH_VERSION, build

log = get_logger(__name__)
_lock = threading.Lock()
_graph = None
_pool: ConnectionPool | None = None


def checkpointer_pool(dsn: str) -> ConnectionPool:
    return ConnectionPool(dsn, kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
                          min_size=1, max_size=5, open=True)


def setup_checkpointer(owner_dsn: str) -> None:
    """Create the checkpoint tables (run as the schema owner, e.g. in migrate)."""
    pool = checkpointer_pool(owner_dsn)
    try:
        PostgresSaver(pool).setup()
    finally:
        pool.close()


def get_graph():  # noqa: ANN201
    global _graph, _pool
    with _lock:
        if _graph is None:
            _pool = checkpointer_pool(get_settings().aap_dsn)
            _graph = build(PostgresSaver(_pool))
        return _graph


def reset_graph() -> None:
    """Tests: drop the cached graph and pool (settings changed)."""
    global _graph, _pool
    with _lock:
        if _pool is not None:
            _pool.close()
        _graph, _pool = None, None


def _config(document_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": document_id}}


def forget_thread(document_id: uuid.UUID | str) -> None:
    """Drop a document's checkpoints (the document was deleted)."""
    get_graph().checkpointer.delete_thread(str(document_id))


def run_agents(document_id: uuid.UUID | str) -> str:
    """Start the graph on the latest invoice version. Returns the resulting document status."""
    doc_id = str(document_id)
    with session_scope() as s:
        doc = s.get(Document, uuid.UUID(doc_id))
        if doc is None:
            raise LookupError(doc_id)
        version = s.scalars(select(InvoiceVersion).where(
            InvoiceVersion.document_id == doc.id, InvoiceVersion.kind.in_(["mapped", "user", "vendor_reply"]))
            .order_by(InvoiceVersion.version.desc()).limit(1)).first()
        if version is None:
            raise LookupError(f"no invoice version for {doc_id}")
        run = AgentRun(document_id=doc.id, thread_id=doc_id, invoice_version_id=version.id,
                       graph_version=GRAPH_VERSION, status="running")
        s.add(run)
        set_status(s, doc, Status.AGENTS_RUNNING, actor="agents")
        s.flush()
        state = {"document_id": doc_id, "version_id": str(version.id), "run_id": str(run.id),
                 "reasons": [], "notes": [], "pairs": [], "unmatched": [], "variances": [], "gl_lines": [],
                 "outcome": None, "suggested_action": None, "po_number": None, "po_source": None, "route": None}
    get_graph().invoke(state, _config(doc_id))
    return _status(doc_id)


def _status(doc_id: str) -> str:
    with session_scope() as s:
        doc = s.get(Document, uuid.UUID(doc_id))
        return doc.status if doc else "UNKNOWN"
