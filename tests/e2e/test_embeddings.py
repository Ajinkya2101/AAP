"""Phase 7: bge-m3 embeddings for GL history + pgvector similarity (WORKED-EXAMPLE 2).

Needs the ONNX model in EMBED_MODEL_DIR (compose `models` volume, mounted in `worker`):
    docker compose run --rm --no-deps -e PG_TEST_HOST=aap-testpg worker python -m pytest tests/e2e/test_embeddings.py
Skipped elsewhere; the agents then use the RapidFuzz fallback.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.integration]


@pytest.fixture(scope="module")
def embedded(clean_aap):
    from aap.embed import get_embedder, model_present
    from aap.embed.backfill import backfill

    if not model_present():
        pytest.skip("bge-m3 ONNX model not available (EMBED_MODEL_DIR)")
    assert get_embedder() is not None
    total = 0
    while n := backfill(max_texts=5000):
        total += n
    assert total > 0
    return clean_aap


def test_all_history_embedded(embedded):
    from sqlalchemy import func, select

    from aap.db.models import GlHistoryLine
    from aap.db.session import session_scope

    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(GlHistoryLine).where(GlHistoryLine.embedding.is_(None))) == 0


def test_vector_similarity_for_aws_storage(embedded):
    from sqlalchemy import select

    from aap.agents.tools.gl import _similar_fuzzy, similar_history
    from aap.db.models import RefSupplier
    from aap.db.session import session_scope

    with session_scope() as s:
        aws = s.scalar(select(RefSupplier.vendor_id).where(RefSupplier.vendor_name.like("Amazon Web Services%")))
        hits = similar_history(s, aws, "Amazon S3 Standard - storage (GB-month)")
        fuzzy = _similar_fuzzy(s, aws, "Amazon S3 Standard - storage (GB-month)", 5)
    assert hits and hits != fuzzy                         # the vector path answered
    assert hits[0].description.startswith("Amazon S3")   # nearest past line is an S3 storage line
    assert hits == sorted(hits, key=lambda h: -h.similarity)
    # WORKED-EXAMPLE 2: S3 history is mixed (6430 x3, 6420 x1) -> not auto, the model/clerk decides.
    assert {h.natural_account for h in hits if h.description.startswith("Amazon S3")} >= {"6430"}


def test_s2_agents_with_embeddings(embedded, tmp_path):
    """S2 through the pipeline + agents with the vector path: EC2 line -> 6420 from exact
    history, S3 line -> mixed history -> model -> GL_REVIEW (same outcome as with RapidFuzz)."""
    try:
        import weasyprint  # noqa: F401
    except (ImportError, OSError):
        pytest.skip("WeasyPrint not available")
    from pathlib import Path

    from sqlalchemy import select
    from tools.invoicegen.__main__ import generate

    from aap.db.models import AgentRun, ReviewTask
    from aap.db.session import session_scope
    from aap.ingest.intake import intake
    from aap.pipeline.stages import run_pipeline_inline

    manifest = generate(tmp_path, count=0, seed=11, scenarios=True, install_truth=True, dsn=embedded["EBS_RO_DSN"])
    item = next(i for i in manifest["items"] if i["documents"] and i["documents"][0]["expected"].get("scenario") == "S2")
    with session_scope() as s:
        doc_id = intake(s, (tmp_path / item["file"]).read_bytes(), filename=Path(item["file"]).name,
                        source="upload", uploaded_by="e2e").document.id
    run_pipeline_inline(doc_id)
    with session_scope() as s:
        run = s.scalars(select(AgentRun).where(AgentRun.document_id == doc_id)).one()
        gl = {g["line"]: g for g in run.outcome["gl_lines"]}
        reasons = {r for t in s.scalars(select(ReviewTask).where(ReviewTask.document_id == doc_id,
                                                                 ReviewTask.status == "open")) for r in t.reason_codes}
    assert gl[1]["method"] == "exact_history" and gl[1]["account"].split("-")[2] == "6420"
    assert gl[2]["method"] == "llm" and not gl[2]["auto"]
    assert "GL_REVIEW" in reasons
