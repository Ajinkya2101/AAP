"""Load the client replica: its own databases next to the synthetic ones (`ebs` / `aap` stay as they are).

    uv run python -m aap.clientdata.replica --local --create-databases ^
        --from client-data\\kk\\2026-11-02-dhivya --from client-data\\kk\\<next delivery> ^
        [--exclude test-data-kk\\<gold set>\\history_exclude.json]

1. (--create-databases) `ebs_kk` and `aap_kk`, with the same roles and grants as `ebs` / `aap`
   (docker/postgres/init), as the superuser (POSTGRES_PASSWORD).
2. Every delivery is checked (aap.clientdata.check) and merged, later deliveries winning.
3. `ebs_kk`: the mock EBS schema, foundation from the seed, the rest from the extracts
   (aap.ebs_mock.client_load). Rebuilt on every run: deliveries are the source, not the database.
4. `aap_kk`: migrations, job queue and checkpoint tables, default value maps, the store table (when
   delivered) as `location` value maps, then the reference sync from `ebs_kk`.

Settings come from .env.kk (the DSNs of the two databases and the 8-segment account layout); --local
rewrites the container host `postgres:5432` to `localhost:5432` for a run from the host. To run the
application on the replica, start the stack with .env.kk's values instead of .env's.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

ENV_FILE = Path(".env.kk")
DSN_KEYS = ("AAP_DSN", "AAP_OWNER_DSN", "EBS_OWNER_DSN", "EBS_RO_DSN", "EBS_INTF_DSN")


def load_env(path: Path, *, local: bool) -> dict[str, str]:
    """KEY=VALUE lines of `path` into os.environ (they win over the process environment)."""
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"')
    if local:
        values = {k: re.sub(r"@postgres:5432/", "@127.0.0.1:5432/", v) if k.endswith("_DSN") else v
                  for k, v in values.items()}
    os.environ.update(values)
    from aap.config import get_settings

    get_settings.cache_clear()
    return values


def _db(dsn: str) -> str:
    return str(conninfo_to_dict(dsn).get("dbname"))


def superuser_dsn(dsn: str) -> str:
    """postgres@ the same server as `dsn` (password POSTGRES_PASSWORD, as docker-compose.yml)."""
    p = conninfo_to_dict(dsn)
    return make_conninfo(host=p.get("host"), port=p.get("port"), user="postgres",
                         password=os.environ.get("POSTGRES_PASSWORD", "postgres"), dbname="postgres")


def create_databases(superuser_dsn: str, ebs_db: str, aap_db: str) -> list[str]:
    """The replica databases with the roles and grants of docker/postgres/init. Existing ones are kept."""
    created = []
    with psycopg.connect(superuser_dsn, autocommit=True) as conn:
        have = {r[0] for r in conn.execute("SELECT datname FROM pg_database")}
        for db, owner in ((ebs_db, "ebs_owner"), (aap_db, "aap_owner")):
            if db in have:
                continue
            conn.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(db), sql.Identifier(owner)))
            created.append(db)
        conn.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(ebs_db)))
        conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO ebs_owner, apps_ro, apps_intf").format(sql.Identifier(ebs_db)))
        conn.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(aap_db)))
        conn.execute(sql.SQL("GRANT CONNECT, TEMPORARY ON DATABASE {} TO aap_owner, aap_app").format(sql.Identifier(aap_db)))
        conn.execute(sql.SQL("ALTER ROLE apps_ro IN DATABASE {} SET search_path = apps").format(sql.Identifier(ebs_db)))
        conn.execute(sql.SQL("ALTER ROLE apps_intf IN DATABASE {} SET search_path = apps, ap").format(sql.Identifier(ebs_db)))
    with psycopg.connect(make_conninfo(superuser_dsn, dbname=aap_db), autocommit=True) as conn:
        for stmt in ("CREATE EXTENSION IF NOT EXISTS vector", "CREATE EXTENSION IF NOT EXISTS pg_trgm",
                     "ALTER SCHEMA public OWNER TO aap_owner", "REVOKE CREATE ON SCHEMA public FROM PUBLIC",
                     "GRANT USAGE ON SCHEMA public TO aap_app",
                     "ALTER DEFAULT PRIVILEGES FOR ROLE aap_owner IN SCHEMA public "
                     "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO aap_app",
                     "ALTER DEFAULT PRIVILEGES FOR ROLE aap_owner IN SCHEMA public "
                     "GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO aap_app",
                     "ALTER DEFAULT PRIVILEGES FOR ROLE aap_owner IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO aap_app",
                     "ALTER DEFAULT PRIVILEGES FOR ROLE aap_owner IN SCHEMA public GRANT USAGE ON TYPES TO aap_app"):
            conn.execute(stmt)  # type: ignore[arg-type]
    with psycopg.connect(make_conninfo(superuser_dsn, dbname=ebs_db), autocommit=True) as conn:
        conn.execute("ALTER SCHEMA public OWNER TO ebs_owner")
        conn.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
    return created


def sync_aap(stores: list[tuple[str, str]]) -> dict[str, Any]:
    """aap_kk: schema, queue, checkpoints, default value maps, store table, reference sync."""
    from aap.adapters.ebs_mock import MockEbsAdapter
    from aap.agents.runner import setup_checkpointer
    from aap.common.coa import layout_from_settings
    from aap.config import get_settings
    from aap.db import session as db_session
    from aap.db.migrate import apply_procrastinate_schema, upgrade_aap
    from aap.db.session import session_scope
    from aap.mapping.defaults import load_default_value_maps
    from aap.mapping.locations import load_locations
    from aap.refsync.sync import run_sync

    s = get_settings()
    db_session.get_engine.cache_clear()
    db_session._factory.cache_clear()
    upgrade_aap(s.aap_owner_dsn)
    apply_procrastinate_schema(s.aap_owner_dsn)
    setup_checkpointer(s.aap_owner_dsn)
    out: dict[str, Any] = {}
    with session_scope(s.aap_owner_dsn) as session:
        out["default_value_maps"] = load_default_value_maps(session)
        if stores:
            out["stores_added"], out["stores_replaced"] = load_locations(session, stores, by="replica-load")
    adapter = MockEbsAdapter(s.ebs_ro_dsn, coa=layout_from_settings())
    try:
        with session_scope(s.aap_owner_dsn) as session:
            out["refsync"] = run_sync(session, adapter, triggered_by="replica-load").counts
    finally:
        adapter.close()
    return out


def run(deliveries: list[Path], *, exclude: Path | None = None, create: bool = False, admin_dsn: str | None = None,
        sync: bool = True, log=print) -> dict[str, Any]:  # noqa: ANN001
    from aap.clientdata.check import check_delivery
    from aap.common.coa import layout_from_settings
    from aap.config import get_settings
    from aap.ebs_mock.client_load import build_replica, load_replica, merge_deliveries
    from aap.mapping.locations import location_key

    s = get_settings()
    ebs_db, aap_db = _db(s.ebs_owner_dsn), _db(s.aap_owner_dsn)
    if ebs_db in ("ebs",) or aap_db in ("aap",):
        raise SystemExit(f"refusing to load the replica into {ebs_db} / {aap_db}: those are the synthetic "
                         "databases. Use .env.kk (ebs_kk / aap_kk).")
    if create:
        created = create_databases(admin_dsn or superuser_dsn(s.ebs_owner_dsn), ebs_db, aap_db)
        log(f"[replica] databases created: {created or 'none (already there)'}")
    for path in deliveries:
        report, _ = check_delivery(path, pdf_scan=False)
        log(f"[replica] check {path}: {'pass' if report.passed else 'FAIL'} "
            f"({len(report.errors)} errors, {len(report.warnings)} warnings); tables {report.tables}")
        if not report.passed:
            for f in report.errors:
                log(f"[replica]   error: {f.message} {f.examples}")
            raise SystemExit("fix the delivery (or leave it out) before loading: python -m aap.clientdata.check")
    coa = layout_from_settings()
    d = merge_deliveries(deliveries)
    r = build_replica(d, coa, exclude=exclude)
    result = load_replica(s.ebs_owner_dsn, r)
    log(f"[replica] {ebs_db}: " + ", ".join(f"{t.split('.')[1]} {n}" for t, n in result["counts"].items()))
    for note in result["notes"]:
        log(f"[replica]   note: {note}")
    if result["invariant_failures"]:
        log(f"[replica]   invariants not met: {result['invariant_failures']}")
    if sync:
        stores = [(location_key(str(x["STORE"])), str(x["COST_CENTER"])) for x in d.rows("STORES")]
        result["aap"] = sync_aap(stores)
        log(f"[replica] {aap_db}: {result['aap']}")
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m aap.clientdata.replica", description=__doc__.split("\n")[0])
    ap.add_argument("--from", dest="deliveries", type=Path, action="append", required=True,
                    help="a delivery folder; repeat for several (later ones win)")
    ap.add_argument("--exclude", type=Path, default=None, help="a gold set's history_exclude.json")
    ap.add_argument("--env-file", type=Path, default=ENV_FILE)
    ap.add_argument("--local", action="store_true", help="run from the host: postgres:5432 -> 127.0.0.1:5432")
    ap.add_argument("--create-databases", action="store_true")
    ap.add_argument("--superuser-dsn", default=None, help="default: postgres:postgres on the same server")
    ap.add_argument("--no-sync", action="store_true", help="load ebs_kk only")
    a = ap.parse_args(argv)
    if not a.env_file.exists():
        print(f"{a.env_file} not found (copy .env.kk.example)", file=sys.stderr)
        return 2
    load_env(a.env_file, local=a.local)
    run(a.deliveries, exclude=a.exclude, create=a.create_databases, admin_dsn=a.superuser_dsn,
        sync=not a.no_sync)
    return 0


if __name__ == "__main__":
    sys.exit(main())
