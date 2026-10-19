"""Compose `migrate` one-shot (plan section 10):

1. mock EBS: DDL + seed if empty (python -m aap.ebs_mock.seed --force re-seeds)
2. aap: alembic upgrade head
3. Procrastinate schema (if missing)
4. default value maps (if empty)
5. first reference sync (if never run)
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config

from aap.config import get_settings
from aap.db.session import sqlalchemy_url
from aap.filestore import FileStore


def wait_for_db(dsn: str, timeout: float = 90) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            with psycopg.connect(dsn, connect_timeout=5):
                return
        except psycopg.OperationalError:
            if time.monotonic() > deadline:
                raise
            time.sleep(2)


def alembic_config(dsn: str) -> Config:
    here = Path(__file__).resolve().parent
    cfg = Config()
    cfg.set_main_option("script_location", str(here / "migrations"))
    cfg.attributes["url"] = sqlalchemy_url(dsn)
    return cfg


def upgrade_aap(owner_dsn: str) -> None:
    command.upgrade(alembic_config(owner_dsn), "head")


def apply_procrastinate_schema(owner_dsn: str) -> bool:
    from procrastinate.schema import SchemaManager

    with psycopg.connect(owner_dsn) as conn:
        exists = conn.execute("SELECT to_regclass('public.procrastinate_jobs') IS NOT NULL").fetchone()[0]
        if exists:
            return False
        conn.execute(SchemaManager.get_schema())  # type: ignore[arg-type]
        conn.commit()
    return True


def main() -> int:
    s = get_settings()
    print("[migrate] waiting for PostgreSQL", flush=True)
    wait_for_db(s.ebs_owner_dsn)
    wait_for_db(s.aap_owner_dsn)

    from aap.ebs_mock.seed import seed_database

    seed_database(s.ebs_owner_dsn, seed=s.seed, scale=s.seed_scale, force=False, anchor=s.seed_anchor_date)

    print("[migrate] alembic upgrade head", flush=True)
    upgrade_aap(s.aap_owner_dsn)
    if apply_procrastinate_schema(s.aap_owner_dsn):
        print("[migrate] procrastinate schema applied", flush=True)
    from aap.agents.runner import setup_checkpointer

    setup_checkpointer(s.aap_owner_dsn)   # idempotent: LangGraph tracks its own migrations

    FileStore().ensure_dirs()

    from aap.adapters.ebs_mock import MockEbsAdapter
    from aap.db.session import session_scope
    from aap.mapping.defaults import load_default_value_maps
    from aap.refsync.sync import last_sync, run_sync

    with session_scope(s.aap_owner_dsn) as session:
        n = load_default_value_maps(session)
        if n:
            print(f"[migrate] loaded {n} default value maps", flush=True)
    with session_scope(s.aap_owner_dsn) as session:
        if last_sync(session) is None:
            adapter = MockEbsAdapter(s.ebs_ro_dsn)
            try:
                run = run_sync(session, adapter, triggered_by="migrate")
                print(f"[migrate] reference sync: {run.counts}", flush=True)
            finally:
                adapter.close()
    print("[migrate] done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
