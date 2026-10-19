"""Shared fixtures.

Integration / e2e tests need a PostgreSQL cluster initialised with
docker/postgres/init (roles + databases). Either:
  - set PG_TEST_HOST / PG_TEST_PORT to a running cluster (e.g. the compose postgres), or
  - have Docker available: a pgvector container is started with testcontainers.
Everything else in tests/unit runs without a database.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Cluster:
    host: str
    port: int

    def dsn(self, user: str, db: str) -> str:
        return f"postgresql://{user}:{user}@{self.host}:{self.port}/{db}"


def _reset_caches() -> None:
    from aap.adapters import get_read_adapter
    from aap.config import get_settings
    from aap.db import session
    from aap.llm import get_llm

    get_settings.cache_clear()
    session.get_engine.cache_clear()
    session._factory.cache_clear()
    get_llm.cache_clear()
    get_read_adapter.cache_clear()
    from aap.agents.runner import reset_graph

    reset_graph()


@pytest.fixture(scope="session")
def cluster() -> Iterator[Cluster]:
    host = os.environ.get("PG_TEST_HOST")
    if host:
        yield Cluster(host, int(os.environ.get("PG_TEST_PORT", "5432")))
        return
    try:
        from testcontainers.community.postgres import PostgresContainer
    except ImportError:  # pragma: no cover
        try:
            from testcontainers.postgres import PostgresContainer
        except ImportError:
            pytest.skip("testcontainers not installed and PG_TEST_HOST not set")
    try:
        container = (PostgresContainer("pgvector/pgvector:pg16", username="postgres", password="postgres",
                                       dbname="postgres")
                     .with_volume_mapping(str(ROOT / "docker" / "postgres" / "init"), "/docker-entrypoint-initdb.d", "ro"))
        container.start()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no PostgreSQL for integration tests: {exc}")
    try:
        yield Cluster(container.get_container_host_ip(), int(container.get_exposed_port(5432)))
    finally:
        container.stop()


@pytest.fixture(scope="session")
def env(cluster: Cluster, tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, str]]:
    filestore = tmp_path_factory.mktemp("filestore")
    values = {
        "AAP_DSN": cluster.dsn("aap_app", "aap"),
        "AAP_OWNER_DSN": cluster.dsn("aap_owner", "aap"),
        "EBS_OWNER_DSN": cluster.dsn("ebs_owner", "ebs"),
        "EBS_RO_DSN": cluster.dsn("apps_ro", "ebs"),
        "EBS_INTF_DSN": cluster.dsn("apps_intf", "ebs"),
        # TEST_LLM_BACKEND=vllm runs the e2e scenarios against the real model (worker container).
        "LLM_BACKEND": os.environ.get("TEST_LLM_BACKEND", "stub"),
        "FILESTORE_DIR": str(filestore),
        "SEED_SCALE": "small",
        "EBS_IMPORT_DELAY_MIN": "0",
        "EBS_IMPORT_DELAY_MAX": "0",
    }
    old = {k: os.environ.get(k) for k in values}
    os.environ.update(values)
    _reset_caches()
    yield values
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    _reset_caches()


@pytest.fixture(scope="session")
def seeded_ebs(env: dict[str, str]) -> dict[str, int]:
    from aap.ebs_mock.seed import seed_database

    return seed_database(env["EBS_OWNER_DSN"], seed=42, scale="small", force=True, log=lambda *_: None)


@pytest.fixture(scope="session")
def aap_db(env: dict[str, str], seeded_ebs: dict[str, int]) -> dict[str, str]:
    """Migrated aap database with default value maps and a reference sync."""
    from aap.agents.runner import setup_checkpointer
    from aap.db.migrate import apply_procrastinate_schema, upgrade_aap

    upgrade_aap(env["AAP_OWNER_DSN"])
    apply_procrastinate_schema(env["AAP_OWNER_DSN"])
    setup_checkpointer(env["AAP_OWNER_DSN"])
    reset_aap(env)
    return env


def reset_aap(env: dict[str, str]) -> None:
    """Empty the aap tables (a reused cluster keeps data between runs; e2e modules
    deliver the same scenario invoices), then reload value maps and the reference copy."""
    import psycopg

    from aap.adapters.ebs_mock import MockEbsAdapter
    from aap.db.session import session_scope
    from aap.filestore import FileStore
    from aap.mapping.defaults import load_default_value_maps
    from aap.refsync.sync import run_sync

    with psycopg.connect(env["AAP_OWNER_DSN"]) as conn:
        tables = [r[0] for r in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
            "AND tablename NOT IN ('alembic_version', 'checkpoint_migrations')")]
        if tables:
            conn.execute("TRUNCATE " + ", ".join(f'"{t}"' for t in tables) + " RESTART IDENTITY CASCADE")
    FileStore().ensure_dirs()
    with session_scope(env["AAP_OWNER_DSN"]) as s:
        load_default_value_maps(s)
    adapter = MockEbsAdapter(env["EBS_RO_DSN"])
    try:
        with session_scope(env["AAP_OWNER_DSN"]) as s:
            run_sync(s, adapter, triggered_by="tests")
    finally:
        adapter.close()


@pytest.fixture(scope="module")
def clean_aap(aap_db: dict[str, str]) -> dict[str, str]:
    """Module-level clean slate (e2e modules): modules post invoices and receive goods in
    EBS, so re-seed it, then empty aap and sync the reference copy again."""
    from aap.ebs_mock.seed import seed_database

    seed_database(aap_db["EBS_OWNER_DSN"], seed=42, scale="small", force=True, log=lambda *_: None)
    reset_aap(aap_db)
    return aap_db
