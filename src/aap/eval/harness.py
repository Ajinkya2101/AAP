"""Eval harness (plan 12, Phase 6): a generated set with truth sidecars through stages 1-8.

Documents are ingested with source="eval" and uploaded_by="eval:<run id>": the duplicate
check (C10) only compares them with documents of the same run, the stalled-job sweeper
leaves them alone, and the pipeline stops at VALIDATED. Results go to
eval_run (metrics JSON) and eval_result (one row per document and field).

With agents=True each scored invoice then also runs through the agents (PO matching, GL
coding), so `python -m aap.eval.model_use --eval-run <id>` can report how often every
decision point asked the model. The extraction scores are not affected. The Oracle-ready JSON
built after the agents is then compared with the truth (eval/shadow.py: the shadow comparison,
and eval/gl.py: the GL metrics), which on a client gold set means: with what AP keyed.
"""

from __future__ import annotations

import json
import re
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy import select

from aap.common.coa import layout_from_settings
from aap.config import get_settings
from aap.db.models import AgentRun, Document, EvalResult, EvalRun, ExtractionRun
from aap.db.session import session_scope
from aap.extraction.extract import PROMPT_VERSION
from aap.ingest.intake import intake
from aap.pipeline.stages import document_summary, latest_version, run_stage

from .metrics import (
    DocResult,
    field_results,
    flatten_source,
    invented_line_values,
    line_match,
    summarize,
    validation_results,
)
from .shadow import ShadowResult, shadow_compare

EVAL_SOURCE = "eval"
CONTAINERS = {"eml", "msg"}


def _drive(doc_id: uuid.UUID) -> list[uuid.UUID]:
    """Stages 2-8 in-process; the agents are not part of the extraction eval."""
    queue, touched = [("parse", doc_id)], []
    while queue:
        stage, d = queue.pop(0)
        if d not in touched:
            touched.append(d)
        queue.extend((s, x) for s, x in run_stage(stage, d) if s != "agents")
    return touched


def _install_truth(dataset: Path) -> None:
    """The stub backend answers from TRUTH_DIR; copy the set's sidecars there."""
    target = get_settings().truth_path
    target.mkdir(parents=True, exist_ok=True)
    for f in (dataset / "truth").glob("*.truth.json"):
        if not (target / f.name).exists():
            shutil.copyfile(f, target / f.name)


def _score(doc: Document, truth: dict[str, Any]) -> DocResult:
    with session_scope() as s:
        run = s.scalars(select(ExtractionRun).where(ExtractionRun.document_id == doc.id)
                        .order_by(ExtractionRun.created_at.desc()).limit(1)).first()
        version = latest_version(s, doc.id, ["mapped"])
        summary = document_summary(s, doc.id) if version else {}
        d = s.get(Document, doc.id)
        status, error = d.status, d.error
        source = flatten_source(run.source_invoice) if run and run.status == "ok" else None
        result = DocResult(name=doc.filename or str(doc.id), tier="scanned" if truth.get("scanned") else "digital",
                           status=status, duration_ms=run.duration_ms if run else None,
                           error=error or (run.error if run else None))
        result.fields = field_results(truth, source, version.canonical_invoice if version else None)
        result.fields += validation_results(truth, summary.get("validation") or [])
    truth_lines = (truth.get("source") or {}).get("lines") or []
    pred_lines = (source or {}).get("lines") or []
    result.line_truth, result.line_pred = len(truth_lines), len(pred_lines)
    result.line_tp = line_match(truth_lines, pred_lines)
    result.line_invented = invented_line_values(truth_lines, pred_lines)
    result.supplier, result.split = truth.get("vendor_number"), truth.get("split")
    result.holdout = bool(truth.get("supplier_holdout"))
    result.shadow = _shadow(doc.id, truth)
    return result


def _shadow(doc_id: uuid.UUID, truth: dict[str, Any]) -> ShadowResult | None:
    """The Oracle-ready JSON against the truth, once the agents have run (before that the JSON would
    have no PO pairing or GL accounts, and comparing it would be meaningless)."""
    from aap.pipeline.oracle_json import OracleJsonError, oracle_invoice, to_json

    with session_scope() as s:
        if s.scalars(select(AgentRun.id).where(AgentRun.document_id == doc_id, AgentRun.status == "done")
                     .limit(1)).first() is None:
            return None
        try:
            doc_json = to_json(oracle_invoice(s, doc_id))
        except OracleJsonError:
            return None
    return shadow_compare(truth, doc_json, layout_from_settings())


def _shadow_rows(run_id: uuid.UUID, doc_id: uuid.UUID, r: DocResult) -> list[EvalResult]:
    if r.shadow is None:
        return []
    rows = [EvalResult(eval_run_id=run_id, document_id=doc_id, tier=r.tier, field_path=f"shadow.{f.name}",
                       expected=f.expected, actual=f.actual, correct=f.correct) for f in r.shadow.fields]
    rows.append(EvalResult(eval_run_id=run_id, document_id=doc_id, tier=r.tier, field_path="shadow.equal",
                           expected="equal", actual=r.shadow.state, correct=r.shadow.equal))
    return rows


UNFINISHED = {"RECEIVED", "PARSED", "ROUTED", "EXTRACTED", "NORMALIZED", "MAPPED"}
# Failures caused by the environment, not by the model: redone on resume.
TRANSIENT = re.compile(r"Timeout|APIConnectionError|ConnectionError|OperationalError")


def _run_documents(run_id: uuid.UUID) -> list[Document]:
    with session_scope() as sess:
        roots = sess.scalars(select(Document.id).where(Document.uploaded_by == f"eval:{run_id}",
                                                       Document.parent_id.is_(None))).all()
        docs = sess.scalars(select(Document).where((Document.id.in_(roots)) | (Document.parent_id.in_(roots)))
                            .order_by(Document.created_at)).all()
        for d in docs:
            sess.expunge(d)
    return list(docs)


def _prepare_resume(run_id: uuid.UUID) -> set[str]:
    """Files already scored in this run. A file whose pipeline was cut off or failed on an
    infrastructure error (e.g. the Docker VM stopped) is set aside (uploaded_by "...:abandoned")
    and runs again."""
    docs = _run_documents(run_id)
    by_root: dict[uuid.UUID, list[Document]] = {}
    for d in docs:
        by_root.setdefault(d.parent_id or d.id, []).append(d)
    done, abandoned = set(), []
    for root_id, group in by_root.items():
        root = next(d for d in group if d.id == root_id)
        if any(d.status in UNFINISHED or (d.status == "FAILED" and TRANSIENT.search(d.error or ""))
               for d in group):
            abandoned += [d.id for d in group]
        else:
            done.add(root.filename)
    if abandoned:
        with session_scope() as sess:
            for d in sess.scalars(select(Document).where(Document.id.in_(abandoned))):
                d.uploaded_by = f"eval:{run_id}:abandoned"
    return done


def run_eval(dataset: Path, *, name: str, only: set[str] | None = None, limit: int | None = None,
             resume: uuid.UUID | None = None, agents: bool = False, split: str | None = None,
             log: Callable[[str], None] = print) -> tuple[uuid.UUID, dict[str, Any]]:
    s = get_settings()
    manifest = json.loads((dataset / "manifest.json").read_text(encoding="utf-8"))
    truths = {p.name.split(".")[0]: json.loads(p.read_text(encoding="utf-8"))
              for p in (dataset / "truth").glob("*.truth.json")}
    if s.llm_backend == "stub":
        _install_truth(dataset)
    done: set[str] = set()
    if resume:
        with session_scope() as sess:
            if sess.get(EvalRun, resume) is None:
                raise LookupError(f"eval run {resume} not found")
        run_id, done = resume, _prepare_resume(resume)
        log(f"[eval] resuming run {run_id}: {len(done)} file(s) already scored")
    else:
        with session_scope() as sess:
            run = EvalRun(name=name, backend=s.llm_backend,
                          model=s.extract_model if s.llm_backend == "vllm" else "stub",
                          prompt_version=PROMPT_VERSION, dataset=str(dataset))
            sess.add(run)
            sess.flush()
            run_id = run.id
    items = manifest["items"]
    if only:
        items = [i for i in items if {d["expected"].get("scenario") for d in i.get("documents") or []} & only]
    if split:                       # client gold sets: test / dev / train (clientdata/goldset.py)
        items = [i for i in items if i.get("split") == split]
    if limit:
        items = items[:limit]
    docs: list[DocResult] = []
    for n, item in enumerate(items, 1):
        path = dataset / item["file"]
        if path.name in done:
            continue
        with session_scope() as sess:
            doc_id = intake(sess, path.read_bytes(), filename=path.name, source=EVAL_SOURCE,
                            uploaded_by=f"eval:{run_id}", allow_duplicate=True).document.id
        for d in _drive(doc_id):
            with session_scope() as sess:
                doc = sess.get(Document, d)
                sess.expunge(doc)
            if doc.file_type in CONTAINERS or doc.sha256 not in truths:
                continue
            r = _score(doc, truths[doc.sha256])
            docs.append(r)
            wrong = [f.path for f in r.fields if not f.correct]
            log(f"[eval] {n}/{len(items)} {r.name}: {r.status} lines {r.line_tp}/{r.line_truth}"
                + (f" wrong: {', '.join(wrong)}" if wrong else "") + (f" error: {r.error[:120]}" if r.error else ""))
            with session_scope() as sess:
                sess.add_all(EvalResult(eval_run_id=run_id, document_id=d, tier=r.tier, field_path=f.path,
                                        expected=f.expected, actual=f.actual, correct=f.correct) for f in r.fields)
                sess.add(EvalResult(eval_run_id=run_id, document_id=d, tier=r.tier, field_path="lines.tp",
                                    expected=str(r.line_truth), actual=f"{r.line_tp}/{r.line_pred}",
                                    correct=r.line_tp == r.line_truth == r.line_pred))
            if agents and r.status == "VALIDATED":
                run_stage("agents", d)
                r.shadow = _shadow(d, truths[doc.sha256])
                if r.shadow is not None:
                    with session_scope() as sess:
                        sess.add_all(_shadow_rows(run_id, d, r))
                    log(f"[eval]   json {r.shadow.state}, {'equal to' if r.shadow.equal else 'differs from'} "
                        f"the truth" + "".join(f"; {f.name} {f.actual} (truth {f.expected})"
                                               for f in r.shadow.fields if not f.correct and f.core))
    if resume:   # the report covers the whole run, not only this session
        docs = [_score(d, truths[d.sha256]) for d in _run_documents(run_id)
                if d.file_type not in CONTAINERS and d.sha256 in truths]
    metrics = summarize(docs)
    with session_scope() as sess:
        sess.get(EvalRun, run_id).metrics = metrics
    return run_id, metrics


def rescore(run_id: uuid.UUID, *, log: Callable[[str], None] = print) -> tuple[uuid.UUID, dict[str, Any]]:
    """Score a finished run again from its stored results (metric changes need no model run).
    Writes a new eval_run "<name> (rescored)"."""
    with session_scope() as sess:
        old = sess.get(EvalRun, run_id)
        if old is None:
            raise LookupError(run_id)
        dataset, name = Path(old.dataset), old.name
        roots = sess.scalars(select(Document.id).where(Document.uploaded_by == f"eval:{run_id}",
                                                       Document.parent_id.is_(None))).all()
        kids = sess.scalars(select(Document).where((Document.id.in_(roots)) | (Document.parent_id.in_(roots)))
                            .order_by(Document.created_at)).all()
        for d in kids:
            sess.expunge(d)
        new = EvalRun(name=f"{name} (rescored)", backend=old.backend, model=old.model,
                      prompt_version=old.prompt_version, dataset=old.dataset)
        sess.add(new)
        sess.flush()
        new_id = new.id
    truths = {p.name.split(".")[0]: json.loads(p.read_text(encoding="utf-8"))
              for p in (dataset / "truth").glob("*.truth.json")}
    docs = [_score(d, truths[d.sha256]) for d in kids if d.file_type not in CONTAINERS and d.sha256 in truths]
    metrics = summarize(docs)
    with session_scope() as sess:
        sess.get(EvalRun, new_id).metrics = metrics
    log(f"[eval] rescored {len(docs)} documents of run {run_id}")
    return new_id, metrics
