"""GL history tools for non-PO lines (WORKED-EXAMPLE 2, step 7).

1. exact: the supplier used this exact line text before.
2. similar: the most similar past lines of the supplier: bge-m3 cosine similarity via
   pgvector (gl_history_line.embedding, HNSW); RapidFuzz when the model or the
   embeddings are not there yet (first start, EMBED_ENABLED=false, tests).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from rapidfuzz import fuzz, process
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aap.common.text import text_key
from aap.db.models import GlHistoryLine, RefCcid


@dataclass
class HistoryHit:
    description: str
    natural_account: str
    concatenated: str
    similarity: float


def exact_history(session: Session, vendor_id: int, description: str) -> list[GlHistoryLine]:
    return list(session.scalars(select(GlHistoryLine).where(
        GlHistoryLine.vendor_id == vendor_id,
        func.lower(GlHistoryLine.description) == description.lower())))


def similar_history(session: Session, vendor_id: int, description: str, k: int = 5) -> list[HistoryHit]:
    hits = _similar_vector(session, vendor_id, description, k)
    return hits if hits is not None else _similar_fuzzy(session, vendor_id, description, k)


def _similar_vector(session: Session, vendor_id: int, description: str, k: int) -> list[HistoryHit] | None:
    from aap.embed import get_embedder

    emb = get_embedder()
    if emb is None:
        return None
    # Only when this supplier's history is embedded; otherwise the fuzzy path is better than a partial one.
    missing = session.scalar(select(func.count()).select_from(GlHistoryLine).where(
        GlHistoryLine.vendor_id == vendor_id, GlHistoryLine.embedding.is_(None)))
    if missing:
        return None
    q = emb.embed([description])[0].tolist()
    dist = GlHistoryLine.embedding.cosine_distance(q)
    # Nearest distinct texts (the same description may appear many times).
    sub = (select(GlHistoryLine.description, GlHistoryLine.natural_account, GlHistoryLine.concatenated,
                  dist.label("d"))
           .where(GlHistoryLine.vendor_id == vendor_id).order_by(dist).limit(k * 8).subquery())
    rows = session.execute(select(sub.c.description, sub.c.natural_account, sub.c.concatenated,
                                  func.min(sub.c.d).label("d"))
                           .group_by(sub.c.description, sub.c.natural_account, sub.c.concatenated)
                           .order_by(func.min(sub.c.d)).limit(k)).all()
    return [HistoryHit(r.description, r.natural_account, r.concatenated, round(1 - float(r.d), 3)) for r in rows]


def _similar_fuzzy(session: Session, vendor_id: int, description: str, k: int) -> list[HistoryHit]:
    rows = session.execute(select(GlHistoryLine.description, GlHistoryLine.natural_account,
                                  GlHistoryLine.concatenated)
                           .where(GlHistoryLine.vendor_id == vendor_id).distinct()).all()
    if not rows:
        return []
    choices = {i: text_key(r.description) for i, r in enumerate(rows)}
    top = process.extract(text_key(description), choices, scorer=fuzz.token_sort_ratio, limit=k)
    return [HistoryHit(rows[i].description, rows[i].natural_account, rows[i].concatenated, round(sc / 100, 3))
            for _, sc, i in top]


def majority(values: list[str]) -> tuple[str, int, int]:
    c = Counter(values)
    value, n = c.most_common(1)[0]
    return value, n, len(values)


def account_description(session: Session, natural_account: str) -> str:
    return session.scalar(select(RefCcid.account_desc).where(RefCcid.segment3 == natural_account).limit(1)) or ""


def template_ccid(session: Session, vendor_id: int, natural_account: str) -> str | None:
    """Company and cost centre the supplier's lines usually use, with this natural account."""
    rows = session.scalars(select(GlHistoryLine.concatenated).where(
        GlHistoryLine.vendor_id == vendor_id, GlHistoryLine.natural_account == natural_account)).all()
    if rows:
        return majority([r for r in rows if r])[0]
    any_row = session.scalar(select(GlHistoryLine.concatenated).where(GlHistoryLine.vendor_id == vendor_id).limit(1))
    if not any_row:
        return None
    s1, s2, _, s4 = any_row.split("-")
    return f"{s1}-{s2}-{natural_account}-{s4}"
