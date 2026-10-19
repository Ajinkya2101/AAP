"""Mock EBS concurrent manager: runs pending FND_CONCURRENT_REQUESTS.

Requests are created by apps.fnd_request_submit_request (PL/pgSQL), which also
sends NOTIFY fnd_concurrent_manager. This loop LISTENs, falls back to polling,
moves each request P -> R -> C and runs the program (only APXIIMPT is mocked).
A random delay (EBS_IMPORT_DELAY_MIN..MAX seconds) exercises the adapter's polling.
"""

from __future__ import annotations

import random
import signal
import sys
import time
from typing import Any

import psycopg
from psycopg.rows import dict_row

from aap.config import get_settings
from aap.ebs_mock.ddl_loader import ddl_applied
from aap.ebs_mock.import_program import run_import

POLL_SECONDS = 2.0
_running = True


def _stop(*_: Any) -> None:
    global _running
    _running = False


def claim_next(conn: psycopg.Connection) -> dict[str, Any] | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""
            SELECT r.*, p.concurrent_program_name
            FROM fnd.fnd_concurrent_requests r
            JOIN fnd.fnd_concurrent_programs p
              ON p.application_id = r.program_application_id AND p.concurrent_program_id = r.concurrent_program_id
            WHERE r.phase_code = 'P' AND r.requested_start_date <= localtimestamp(0)
            ORDER BY r.request_id
            FOR UPDATE OF r SKIP LOCKED
            LIMIT 1""")
        req = cur.fetchone()
        if req:
            cur.execute("""UPDATE fnd.fnd_concurrent_requests
                           SET phase_code = 'R', status_code = 'R', actual_start_date = localtimestamp(0)
                           WHERE request_id = %s""", (req["request_id"],))
    conn.commit()
    return req


def execute_request(conn: psycopg.Connection, req: dict[str, Any], delay: float = 0.0) -> str:
    """Runs one claimed request and completes it. Returns the final status code."""
    if delay:
        time.sleep(delay)
    status, text = "C", "Normal completion"
    try:
        if req["concurrent_program_name"] == "APXIIMPT":
            org = int(req["argument1"]) if (req["argument1"] or "").isdigit() else None
            result = run_import(conn, group_id=req["argument3"], source=req["argument2"], org_id=org,
                                request_id=req["request_id"])
            text = result.completion_text
            status = "G" if result.rejected else "C"
        else:
            text = f"Program {req['concurrent_program_name']} is not simulated; completed without action"
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - report any program failure as EBS would
        conn.rollback()
        status, text = "E", f"Error: {exc}"[:240]
    conn.execute("""UPDATE fnd.fnd_concurrent_requests
                    SET phase_code = 'C', status_code = %s, completion_text = %s,
                        actual_completion_date = localtimestamp(0), last_update_date = localtimestamp(0)
                    WHERE request_id = %s""", (status, text[:240], req["request_id"]))
    conn.commit()
    return status


def run_pending(dsn: str, *, delay_range: tuple[float, float] = (0.0, 0.0)) -> int:
    """Process every pending request once (used by tests and `aap-mock import-run --all`)."""
    n = 0
    with psycopg.connect(dsn) as conn:
        while (req := claim_next(conn)) is not None:
            execute_request(conn, req, random.uniform(*delay_range))
            n += 1
    return n


def main() -> int:
    settings = get_settings()
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    delay = (settings.ebs_import_delay_min, settings.ebs_import_delay_max)
    print(f"[ebs-cm] starting; import delay {delay[0]}-{delay[1]}s", flush=True)
    while _running:
        try:
            with psycopg.connect(settings.ebs_owner_dsn, autocommit=False) as conn:
                while _running and not ddl_applied(conn):
                    conn.rollback()
                    time.sleep(POLL_SECONDS)
                conn.rollback()
                listen = psycopg.connect(settings.ebs_owner_dsn, autocommit=True)
                listen.execute("LISTEN fnd_concurrent_manager")
                while _running:
                    req = claim_next(conn)
                    if req is None:
                        for _ in listen.notifies(timeout=POLL_SECONDS, stop_after=1):
                            pass
                        continue
                    print(f"[ebs-cm] request {req['request_id']} {req['concurrent_program_name']} "
                          f"args={req['argument_text']}", flush=True)
                    status = execute_request(conn, req, random.uniform(*delay))
                    print(f"[ebs-cm] request {req['request_id']} completed status={status}", flush=True)
                listen.close()
        except psycopg.OperationalError as exc:
            print(f"[ebs-cm] database unavailable ({exc}); retrying", flush=True)
            time.sleep(5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
