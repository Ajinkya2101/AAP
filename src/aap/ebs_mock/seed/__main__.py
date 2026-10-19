from __future__ import annotations

import argparse
import sys
from datetime import date

from aap.config import get_settings

from . import SeedError, seed_database


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    p = argparse.ArgumentParser(prog="python -m aap.ebs_mock.seed", description="Seed the mock Oracle EBS database")
    p.add_argument("--force", action="store_true", help="drop and rebuild even if already seeded")
    p.add_argument("--scale", choices=["small", "default", "large"], default=settings.seed_scale)
    p.add_argument("--seed", type=int, default=settings.seed)
    p.add_argument("--anchor", type=date.fromisoformat, default=settings.seed_anchor_date,
                   help="business 'today' of the mock data (SEED_ANCHOR_DATE, default 2026-09-28)")
    p.add_argument("--dsn", default=settings.ebs_owner_dsn)
    args = p.parse_args(argv)
    try:
        seed_database(args.dsn, seed=args.seed, scale=args.scale, force=args.force, anchor=args.anchor)
    except SeedError as exc:
        print(f"[seed] ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
