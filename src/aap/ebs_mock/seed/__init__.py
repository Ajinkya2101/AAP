"""Deterministic mock EBS seeder.

    python -m aap.ebs_mock.seed [--force] [--scale small|default|large]
"""

from __future__ import annotations

import time
from datetime import date
from typing import Any

import psycopg

from aap.ebs_mock.ddl_loader import apply_ddl, ddl_applied, refresh_apis

from . import foundation, gl, invoices, items, org, po, scenarios, suppliers, terms_tax
from .context import SCALES, TABLE_ORDER, SeedContext
from .invariants import check

DEFAULT_ANCHOR = date(2026, 9, 28)


class SeedError(RuntimeError):
    pass


def build(seed: int = 42, scale: str = "default", anchor: date = DEFAULT_ANCHOR) -> SeedContext:
    """Generate all rows in memory (no database access)."""
    ctx = SeedContext(seed=seed, scale=SCALES[scale], anchor=anchor)
    foundation.generate(ctx)
    org.generate(ctx)
    gl.generate(ctx)
    terms_tax.generate(ctx)
    items.generate(ctx)
    po.generate_line_types(ctx)
    scenarios.reserve_ids(ctx)
    suppliers.generate(ctx, reserved_names=scenarios.SCENARIO_VENDOR_NAMES)
    scenarios.generate(ctx)
    po.generate(ctx)
    invoices.generate(ctx)
    po.generate_releases(ctx)
    return ctx


def _columns(rows: list[dict[str, Any]]) -> list[str]:
    cols: dict[str, None] = {}
    for r in rows:
        for k in r:
            cols.setdefault(k, None)
    return list(cols)


def load(conn: psycopg.Connection, ctx: SeedContext) -> dict[str, int]:
    counts: dict[str, int] = {}
    with conn.cursor() as cur:
        for table in TABLE_ORDER:
            rows = ctx.rows.get(table) or []
            if not rows:
                continue
            cols = _columns(rows)
            with cur.copy(f"COPY {table} ({', '.join(cols)}) FROM STDIN") as copy:  # type: ignore[arg-type]
                for r in rows:
                    copy.write_row([r.get(c) for c in cols])
            counts[table] = len(rows)
        for seq, value in ctx.sequence_high_water().items():
            cur.execute("SELECT setval(%s, %s)", (seq, value))
    return counts


def is_seeded(conn: psycopg.Connection) -> bool:
    if not ddl_applied(conn):
        return False
    row = conn.execute("SELECT count(*) FROM ap.ap_suppliers").fetchone()
    return bool(row and row[0] > 0)


def seed_database(dsn: str, *, seed: int = 42, scale: str = "default", force: bool = False,
                  anchor: date = DEFAULT_ANCHOR, log=print) -> dict[str, int]:
    """Create the mock EBS schema (if needed) and load seed data. Idempotent unless force."""
    with psycopg.connect(dsn) as conn:
        if is_seeded(conn) and not force:
            refresh_apis(conn)
            conn.commit()
            log("[seed] mock EBS already seeded (APIs refreshed); use --force to rebuild")
            return {}
        t0 = time.monotonic()
        log(f"[seed] applying DDL (drop existing: {force or ddl_applied(conn)})")
        apply_ddl(conn, drop_existing=True)
        log(f"[seed] generating data: seed={seed} scale={scale} anchor={anchor}")
        ctx = build(seed=seed, scale=scale, anchor=anchor)
        counts = load(conn, ctx)
        failures = check(conn)
        if failures:
            conn.rollback()
            raise SeedError(f"seed invariants failed: {failures}")
        conn.commit()
        log(f"[seed] loaded {sum(counts.values())} rows in {time.monotonic() - t0:.1f}s")
        for table, n in counts.items():
            log(f"[seed]   {table:40s} {n:>8d}")
        return counts
