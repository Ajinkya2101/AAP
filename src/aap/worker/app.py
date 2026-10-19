"""Procrastinate app (AD-04: PostgreSQL job queue, no Redis).

Queues (plan 7): ingest (8), llm (4), agents (4), erp (2), periodic (1).

One app everywhere. In the worker the async pool is open and sync code (sync
tasks) defers through it. Anywhere else (API, CLI) `defer()` opens the app's
sync connector once, on first use.
"""

from __future__ import annotations

import threading

from procrastinate import App, PsycopgConnector

from aap.config import get_settings

QUEUES = {"ingest": 8, "llm": 4, "agents": 4, "erp": 2, "periodic": 1}

# kwargs={} : the LISTEN connection expands pool.kwargs, which psycopg_pool leaves as None.
app = App(connector=PsycopgConnector(conninfo=get_settings().aap_dsn, kwargs={}), import_paths=["aap.worker.tasks"])

_lock = threading.Lock()
_worker_mode = False
_sync_open = False


def mark_worker_mode() -> None:
    """Called by the worker entry point: the async pool serves sync defers."""
    global _worker_mode
    _worker_mode = True


def _ensure_open() -> None:
    global _sync_open
    if _worker_mode or _sync_open:
        return
    with _lock:
        if not _sync_open:
            app.open()
            _sync_open = True


def defer(task_name: str, queue: str, *, schedule_in: dict[str, int] | None = None, **kwargs: object) -> int:
    _ensure_open()
    return app.configure_task(name=task_name, queue=queue, schedule_in=schedule_in).defer(**kwargs)
