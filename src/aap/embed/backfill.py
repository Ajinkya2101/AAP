"""Fill gl_history_line.embedding for rows that have none (periodic job + CLI).

Many history rows share a description, so each distinct text is embedded once.
    python -m aap.embed.backfill        # until done
"""

from __future__ import annotations

import sys
import time

from sqlalchemy import text

from aap.db.session import session_scope
from aap.logging import get_logger

from . import get_embedder

log = get_logger(__name__)


def backfill(max_texts: int = 2000, batch: int = 256) -> int:
    """Embed up to `max_texts` distinct descriptions. Returns the number embedded (0 = done or no model)."""
    emb = get_embedder()
    if emb is None:
        return 0
    done = 0
    while done < max_texts:
        with session_scope() as s:
            texts = [r[0] for r in s.execute(text(
                "SELECT DISTINCT description FROM gl_history_line WHERE embedding IS NULL LIMIT :n"),
                {"n": min(batch, max_texts - done)})]
        if not texts:
            break
        vecs = emb.embed(texts)
        with session_scope() as s:
            s.execute(text("UPDATE gl_history_line SET embedding = CAST(:v AS vector) "
                           "WHERE description = :d AND embedding IS NULL"),
                      [{"d": t, "v": "[" + ",".join(f"{x:.6f}" for x in v) + "]"} for t, v in zip(texts, vecs, strict=True)])
        done += len(texts)
    if done:
        log.info("embed.backfill", texts=done)
    return done


def main() -> int:
    t0 = time.monotonic()
    total = 0
    while n := backfill():
        total += n
        print(f"[embed] {total} distinct texts embedded ({time.monotonic() - t0:.0f}s)", flush=True)
    if get_embedder() is None:
        print("[embed] model not available (EMBED_ENABLED=false or files missing)")
        return 1
    print(f"[embed] done: {total} texts in {time.monotonic() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
