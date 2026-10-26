"""Worker entry point: one Procrastinate worker per queue, each with its own concurrency."""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime

from aap.config import get_settings
from aap.logging import configure_logging, get_logger

from .app import QUEUES, app, mark_worker_mode

log = get_logger(__name__)


async def recover_orphaned_jobs() -> int:
    """Jobs a dead worker left `doing` (native crash, OOM kill) are never picked up again, and
    requeue_stalled skips their documents. There is one worker container, so at its start every
    `doing` job is orphaned: put it back to `todo`. Stages are idempotent."""
    jobs = list(await app.job_manager.list_jobs_async(status="doing"))
    for job in jobs:
        await app.job_manager.retry_job_by_id_async(job.id, retry_at=datetime.now(UTC))
        log.warning("worker.recover_job", job_id=job.id, task=job.task_name, args=job.task_kwargs)
    return len(jobs)


async def _main() -> None:
    mark_worker_mode()
    async with app.open_async():
        if get_settings().worker_recover_on_start:
            await recover_orphaned_jobs()
        log.info("worker.start", queues=QUEUES, llm_backend=get_settings().llm_backend)
        await asyncio.gather(*[
            app.run_worker_async(queues=[q], concurrency=n, name=f"worker-{q}", install_signal_handlers=(i == 0))
            for i, (q, n) in enumerate(QUEUES.items())
        ])


def main() -> int:
    configure_logging(get_settings().log_level)
    asyncio.run(_main())
    return 0


if __name__ == "__main__":
    sys.exit(main())
