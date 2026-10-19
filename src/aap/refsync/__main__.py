"""python -m aap.refsync [--full]

Runs the reference sync now. --full also reloads gl_history_line from scratch
(needed after the mock EBS was re-seeded, because the history copy is incremental).
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import delete

from aap.adapters.ebs_mock import MockEbsAdapter
from aap.config import get_settings
from aap.db.models import GlHistoryLine
from aap.db.session import session_scope

from .sync import run_sync


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m aap.refsync")
    p.add_argument("--full", action="store_true", help="also reload the GL history copy")
    a = p.parse_args(argv)
    s = get_settings()
    adapter = MockEbsAdapter(s.ebs_ro_dsn)
    try:
        with session_scope(s.aap_owner_dsn) as session:
            if a.full:
                session.execute(delete(GlHistoryLine).where(GlHistoryLine.source == "ebs"))
            run = run_sync(session, adapter, triggered_by="cli")
            print(f"[refsync] {run.status}: {run.counts}")
    finally:
        adapter.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
