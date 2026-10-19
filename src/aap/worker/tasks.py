"""Pipeline tasks. One job = one stage for one document (R-04)."""

from __future__ import annotations

import uuid

from procrastinate import JobContext, RetryStrategy

from aap.logging import get_logger

from .app import app, defer

log = get_logger(__name__)

# ARCHITECTURE 11: retry 3 times with 10, 30, 90 s waits, then FAILED.
MAX_ATTEMPTS = 4
RETRY = RetryStrategy(max_attempts=MAX_ATTEMPTS, wait=10, exponential_wait=3)


def enqueue_stage(stage: str, document_id: uuid.UUID | str) -> None:
    from aap.pipeline.stages import AGENT_STAGES, ERP_STAGES, LLM_STAGES

    if stage == "result":   # poll the Oracle import later instead of sleeping in a worker
        from aap.submit.submit import POLL_SECONDS

        defer("aap.stage.erp", "erp", schedule_in={"seconds": POLL_SECONDS}, stage=stage,
              document_id=str(document_id))
        return
    queue = ("llm" if stage in LLM_STAGES else "agents" if stage in AGENT_STAGES
             else "erp" if stage in ERP_STAGES else "ingest")
    defer(f"aap.stage.{queue}", queue, stage=stage, document_id=str(document_id))


def _run(context: JobContext, stage: str, document_id: str) -> None:
    from aap.pipeline.stages import run_stage

    attempts = getattr(context.job, "attempts", 0) or 0
    final = attempts + 1 >= MAX_ATTEMPTS
    for next_stage, doc_id in run_stage(stage, document_id, final_attempt=final):
        enqueue_stage(next_stage, doc_id)


@app.task(name="aap.stage.ingest", queue="ingest", retry=RETRY, pass_context=True)
def ingest_stage(context: JobContext, stage: str, document_id: str) -> None:
    _run(context, stage, document_id)


@app.task(name="aap.stage.llm", queue="llm", retry=RETRY, pass_context=True)
def llm_stage(context: JobContext, stage: str, document_id: str) -> None:
    _run(context, stage, document_id)


@app.task(name="aap.stage.agents", queue="agents", retry=RETRY, pass_context=True)
def agents_stage(context: JobContext, stage: str, document_id: str) -> None:
    _run(context, stage, document_id)


@app.task(name="aap.stage.erp", queue="erp", retry=RETRY, pass_context=True)
def erp_stage(context: JobContext, stage: str, document_id: str) -> None:
    _run(context, stage, document_id)


@app.task(name="aap.refsync", queue="periodic", retry=RetryStrategy(max_attempts=3, wait=60))
def refsync(triggered_by: str = "manual") -> dict:
    from aap.adapters import get_read_adapter
    from aap.db.session import session_scope
    from aap.refsync.sync import run_sync

    with session_scope() as session:
        run = run_sync(session, get_read_adapter(), triggered_by=triggered_by)
        return {"run_id": run.id, "counts": run.counts}


NEXT_STAGE = {"RECEIVED": "parse", "PARSED": "route", "ROUTED": "extract", "EXTRACTED": "normalize",
              "NORMALIZED": "map", "MAPPED": "validate", "VALIDATED": "agents", "APPROVED": "submit",
              "SUBMITTED": "result"}
STALL_MINUTES = 5


@app.periodic(cron="* * * * *", periodic_id="requeue-stalled")
@app.task(name="aap.requeue_stalled", queue="periodic")
def requeue_stalled(timestamp: int) -> int:
    """Re-enqueue documents that sit in an intermediate status without a queued job
    (e.g. the API committed the intake but the enqueue failed). Stages are idempotent."""
    from sqlalchemy import text

    from aap.db.session import session_scope

    with session_scope() as s:
        rows = s.execute(text("""
            SELECT d.id, d.status FROM document d
            WHERE d.status = ANY(:statuses) AND d.source <> 'eval'
              AND d.updated_at < now() - make_interval(mins => :mins)
              AND NOT EXISTS (SELECT 1 FROM procrastinate_jobs j
                              WHERE j.status IN ('todo', 'doing') AND j.args->>'document_id' = d.id::text)
            LIMIT 200"""), {"statuses": list(NEXT_STAGE), "mins": STALL_MINUTES}).all()
    for doc_id, status in rows:
        log.warning("requeue.stalled", document_id=str(doc_id), status=status)
        enqueue_stage(NEXT_STAGE[status], doc_id)
    return len(rows)


@app.periodic(cron="*/5 * * * *", periodic_id="recheck-holds")
@app.task(name="aap.agents.recheck_holds", queue="periodic", lock="recheck-holds")
def recheck_holds(timestamp: int) -> list[str]:
    """Plan 8: ON_HOLD invoices go back to the agents when Oracle's receipts change."""
    from aap.agents.recheck import recheck_holds as run

    return run()


@app.periodic(cron="*/5 * * * *", periodic_id="embed-backfill")
@app.task(name="aap.embed.backfill", queue="periodic", lock="embed-backfill")
def embed_backfill(timestamp: int) -> int:
    """bge-m3 embeddings for new GL history rows (no-op until the model is downloaded)."""
    from aap.embed.backfill import backfill

    return backfill()


@app.periodic(cron="0 2 * * *", periodic_id="nightly-refsync")
@app.task(name="aap.refsync.nightly", queue="periodic")
def refsync_nightly(timestamp: int) -> None:
    refsync(triggered_by="nightly")
