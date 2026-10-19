"""Worker entry point: one Procrastinate worker per queue, each with its own concurrency."""

from __future__ import annotations

import asyncio
import sys

from aap.config import get_settings
from aap.logging import configure_logging, get_logger

from .app import QUEUES, app, mark_worker_mode

log = get_logger(__name__)


async def _main() -> None:
    mark_worker_mode()
    async with app.open_async():
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
