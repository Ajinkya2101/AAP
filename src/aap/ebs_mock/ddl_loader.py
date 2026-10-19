"""Applies the mock EBS DDL files in dependency order."""

from __future__ import annotations

from importlib import resources

import psycopg

DDL_FILES = [
    "00_schemas.sql",
    "fnd.sql",
    "hr.sql",
    "gl.sql",
    "hz_iby_zx.sql",
    "ap.sql",
    "po.sql",
    "inv.sql",
    "apps_views.sql",
    "grants.sql",
    "apps_api.sql",
]
# Idempotent files, re-applied on every migrate so an already-seeded database gets them.
REFRESH_FILES = ["apps_api.sql"]

OWNER_SCHEMAS = ["apps", "fnd", "hr", "gl", "hz", "iby", "zx", "ap", "po", "inv"]

WHO_COLUMNS = """created_by bigint NOT NULL DEFAULT -1,
    creation_date timestamp(0) NOT NULL DEFAULT localtimestamp(0),
    last_updated_by bigint NOT NULL DEFAULT -1,
    last_update_date timestamp(0) NOT NULL DEFAULT localtimestamp(0),
    last_update_login bigint"""

DFF_COLUMNS = "attribute_category varchar(150),\n    " + ",\n    ".join(
    f"attribute{i} varchar(150)" for i in range(1, 16)
)


def render(name: str) -> str:
    text = resources.files("aap.ebs_mock.ddl").joinpath(name).read_text(encoding="utf-8")
    return text.replace("@WHO@", WHO_COLUMNS).replace("@DFF@", DFF_COLUMNS)


def ddl_applied(conn: psycopg.Connection) -> bool:
    row = conn.execute("SELECT to_regclass('ap.ap_invoices_all') IS NOT NULL").fetchone()
    return bool(row and row[0])


def drop_all(conn: psycopg.Connection) -> None:
    for schema in OWNER_SCHEMAS:
        conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")


def apply_ddl(conn: psycopg.Connection, *, drop_existing: bool = False) -> None:
    """Create all mock EBS objects. Runs in the caller's transaction."""
    if drop_existing:
        drop_all(conn)
    for name in DDL_FILES:
        conn.execute(render(name))  # type: ignore[arg-type]


def refresh_apis(conn: psycopg.Connection) -> None:
    """Re-apply the idempotent API files (CREATE OR REPLACE + grants)."""
    for name in REFRESH_FILES:
        conn.execute(render(name))  # type: ignore[arg-type]
