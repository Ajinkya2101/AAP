"""R1.2: jobs left `doing` by a crashed worker go back to `todo` when the worker starts."""

import asyncio

from procrastinate import App, testing

import aap.worker.__main__ as worker_main


def test_orphaned_doing_jobs_are_requeued(monkeypatch):
    app = App(connector=testing.InMemoryConnector())

    @app.task(name="t", queue="q")
    def t(x: int) -> None: ...

    async def scenario() -> list[str]:
        async with app.open_async():
            await t.defer_async(x=1)
            await t.defer_async(x=2)
            await app.job_manager.fetch_job(queues=None)          # a worker took job 1, then died
            monkeypatch.setattr(worker_main, "app", app)
            assert await worker_main.recover_orphaned_jobs() == 1
            return sorted(j.status for j in await app.job_manager.list_jobs_async())

    assert asyncio.run(scenario()) == ["todo", "todo"]
