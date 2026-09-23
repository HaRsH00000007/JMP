"""Bulk CSV orchestration (generation-flow.md §4).

create → fanout → independent per-row jobs → guarded counters → finalize (ZIP + manifests).

* Isolation: every row is its own job chain; a failure is recorded on that row only.
* Counters: each item is counted exactly once (conditional UPDATE on `counted`), so redelivered tasks can't
  double-count; finalize runs once (conditional status transition processing → finalizing).
* Cache warm-up (realtime mode): the first valid row runs to completion alone, writing the Claude prompt
  cache, before the remaining rows are released (then limited by the llm queue concurrency).
* Batch mode (BULK_LLM_MODE=batch): rows are analysed in parallel, their narrative requests go to the
  Message Batches API in one submission (50% token price), results are validated like realtime output;
  invalid/errored results fall back to realtime generation for that row only.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from openpyxl import Workbook
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.db.models import (
    BulkItemStatus,
    BulkJob,
    BulkJobItem,
    BulkStatus,
    GenerationJob,
    GenerationUsage,
    JmpDocument,
    JobStage,
    JobStatus,
)
from app.db.session import session_scope
from app.errors import ConflictError, ErrorCode, JmpError, NotFoundError
from app.observability.logging import get_logger
from app.schemas.journeys import normalize_request
from app.services import jobs
from app.services.bulk_csv import ParsedCsv
from app.settings import settings
from app.storage import date_prefix, get_storage

log = get_logger("jmp.bulk")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------------------- creation
def create_bulk_job(db: Session, parsed: ParsedCsv, *, filename: str, csv_bytes: bytes, llm_mode: str | None = None,
                    idempotency_key: str | None = None, actor_id: uuid.UUID | None = None) -> BulkJob:
    sha = hashlib.sha256(csv_bytes).hexdigest()
    if idempotency_key:
        existing = db.scalar(select(BulkJob).where(BulkJob.idempotency_key == idempotency_key))
        if existing is not None:
            if existing.csv_sha256 != sha:
                raise ConflictError("idempotency_key was already used with a different CSV")
            return existing
    mode = llm_mode or settings().bulk_llm_mode
    if mode == "batch" and settings().llm_provider == "mock":
        mode = "realtime"  # the mock provider has no batch API
    bulk_id = uuid.uuid4()
    run_dir = f"bulk/{date_prefix()}/{bulk_id}"
    key = get_storage().put_bytes(f"{run_dir}/input.csv", csv_bytes, "text/csv")
    valid = parsed.valid_rows
    job = BulkJob(id=bulk_id, created_by_id=actor_id, original_filename=filename[:255], csv_path=key, csv_sha256=sha,
                  idempotency_key=idempotency_key, status=BulkStatus.queued, llm_mode=mode,
                  total_rows=len(parsed.rows), valid_rows=len(valid), invalid_rows=len(parsed.rows) - len(valid))
    db.add(job)
    db.flush()
    for r in parsed.rows:
        db.add(BulkJobItem(
            bulk_job_id=bulk_id, row_number=r.row_number, route_ref=r.route_ref, raw_row=r.raw,
            normalized_input=r.request.model_dump(mode="json", exclude_none=True) if r.request else None,
            validation_errors=r.errors or None,
            status=BulkItemStatus.queued if r.valid else BulkItemStatus.invalid,
            error_code=None if r.valid else ErrorCode.VALIDATION_ERROR,
            error_message=None if r.valid else "; ".join(f"{e['field']}: {e['issue']}" for e in r.errors)[:2000]))
    db.flush()
    return job


def start(bulk_id: uuid.UUID) -> None:
    mode = settings().job_execution
    if mode == "celery":
        from app.workers.celery_app import celery

        celery.send_task("bulk.fanout", args=[str(bulk_id)], queue="bulk")
    elif mode == "thread":
        jobs._pool().submit(fanout, bulk_id)
    else:
        fanout(bulk_id)


# ---------------------------------------------------------------------------------------- fan-out
def fanout(bulk_id: uuid.UUID | str) -> None:
    bulk_id = uuid.UUID(str(bulk_id))
    with session_scope() as db:
        bj = db.get(BulkJob, bulk_id)
        if bj is None or bj.status not in (BulkStatus.queued, BulkStatus.processing):
            return
        bj.status = BulkStatus.processing
        bj.started_at = bj.started_at or utcnow()
        llm_mode = bj.llm_mode
        if bj.valid_rows == 0:
            pass
        items = db.scalars(select(BulkJobItem).where(BulkJobItem.bulk_job_id == bulk_id,
                                                     BulkJobItem.status == BulkItemStatus.queued)
                           .order_by(BulkJobItem.row_number)).all()
        job_ids: list[uuid.UUID] = []
        for it in items:
            if it.generation_job_id is None:
                req = normalize_request(it.normalized_input or {})
                gj = jobs.create_journey_job(db, req, source="bulk", bulk_item_id=it.id)
                it.generation_job_id, it.journey_id = gj.id, gj.journey_id
            it.status = BulkItemStatus.processing
            it.attempts += 1
            job_ids.append(it.generation_job_id)  # type: ignore[arg-type]
        no_valid = bj.valid_rows == 0
    if no_valid:
        maybe_finalize(bulk_id)
        return
    if not job_ids:
        return
    if llm_mode == "realtime":
        # warm the prompt cache: first row runs to completion alone (inline), then release the rest
        first, rest = job_ids[0], job_ids[1:]
        jobs.run_chain("analyse", str(first))
        for jid in rest:
            jobs.dispatch("analyse", jid)
    else:
        for jid in job_ids:
            jobs.dispatch("analyse", jid)


def item_uses_batch(item_id: uuid.UUID) -> bool:
    with session_scope() as db:
        it = db.get(BulkJobItem, item_id)
        if it is None:
            return False
        bj = db.get(BulkJob, it.bulk_job_id)
        return bool(bj and bj.llm_mode == "batch")


# ---------------------------------------------------------------------------------------- progress
def item_finished(item_id: uuid.UUID, *, ok: bool, document_id: uuid.UUID | None = None,
                  error_code: str | None = None, error_message: str | None = None) -> None:
    with session_scope() as db:
        res = db.execute(update(BulkJobItem).where(BulkJobItem.id == item_id, BulkJobItem.counted.is_(False))
                         .values(counted=True, status=BulkItemStatus.succeeded if ok else BulkItemStatus.failed,
                                 document_id=document_id, error_code=None if ok else error_code,
                                 error_message=None if ok else (error_message or "")[:2000], finished_at=utcnow()))
        if res.rowcount != 1:
            return  # already counted (redelivery)
        it = db.get(BulkJobItem, item_id)
        assert it is not None
        bulk_id = it.bulk_job_id
        db.execute(update(BulkJob).where(BulkJob.id == bulk_id).values(
            processed=BulkJob.processed + 1,
            succeeded=BulkJob.succeeded + (1 if ok else 0),
            failed=BulkJob.failed + (0 if ok else 1),
            updated_at=utcnow()))
    maybe_finalize(bulk_id)
    maybe_submit_batch(bulk_id)


def maybe_finalize(bulk_id: uuid.UUID) -> None:
    with session_scope() as db:
        bj = db.get(BulkJob, bulk_id)
        if bj is None or bj.processed < bj.valid_rows:
            return
        res = db.execute(update(BulkJob).where(BulkJob.id == bulk_id, BulkJob.status == BulkStatus.processing)
                         .values(status=BulkStatus.finalizing))
        if res.rowcount != 1:
            return
    mode = settings().job_execution
    if mode == "celery":
        from app.workers.celery_app import celery

        celery.send_task("bulk.finalize", args=[str(bulk_id)], queue="bulk")
    else:
        finalize(bulk_id)


# ---------------------------------------------------------------------------------------- finalize
MANIFEST_COLUMNS = ["row_number", "route_id", "status", "document_code", "pdf_filename", "error_code",
                    "error_message", "generated_at", "risk_level", "journey_score", "decision"]


def _safe_cell(v: Any) -> Any:
    """Neutralise spreadsheet formula injection in text cells."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


def _manifest_rows(db: Session, bulk_id: uuid.UUID) -> list[dict[str, Any]]:
    items = db.scalars(select(BulkJobItem).where(BulkJobItem.bulk_job_id == bulk_id)
                       .order_by(BulkJobItem.row_number)).all()
    doc_ids = [i.document_id for i in items if i.document_id]
    docs = {d.id: d for d in db.scalars(select(JmpDocument).where(JmpDocument.id.in_(doc_ids)))} if doc_ids else {}
    rows = []
    for it in items:
        d = docs.get(it.document_id) if it.document_id else None
        status = {"succeeded": "SUCCESS", "failed": "FAILED", "invalid": "INVALID", "cancelled": "CANCELLED"}.get(
            it.status, it.status.upper())
        rows.append({
            "row_number": it.row_number, "route_id": it.route_ref, "status": status,
            "document_code": d.document_code if d else "",
            "pdf_filename": _pdf_name(it.route_ref, d.document_code) if d else "",
            "error_code": it.error_code or "", "error_message": it.error_message or "",
            "generated_at": d.created_at.isoformat() if d else "",
            "risk_level": d.risk_level if d else "", "journey_score": d.journey_score if d else "",
            "decision": d.decision if d else "",
        })
    return rows


def _pdf_name(route_ref: str, code: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in route_ref)[:60]
    return f"{safe}_{code}.pdf"


def finalize(bulk_id: uuid.UUID | str) -> None:
    bulk_id = uuid.UUID(str(bulk_id))
    storage = get_storage()
    try:
        with session_scope() as db:
            bj = db.get(BulkJob, bulk_id)
            if bj is None or bj.status != BulkStatus.finalizing:
                return
            rows = _manifest_rows(db, bulk_id)
            docs = {d.id: d for d in db.scalars(
                select(JmpDocument).join(BulkJobItem, BulkJobItem.document_id == JmpDocument.id)
                .where(BulkJobItem.bulk_job_id == bulk_id))}
            item_docs = [(it.route_ref, docs[it.document_id]) for it in db.scalars(
                select(BulkJobItem).where(BulkJobItem.bulk_job_id == bulk_id, BulkJobItem.document_id.is_not(None))
                .order_by(BulkJobItem.row_number)) if it.document_id in docs]
            usage = db.execute(select(
                func.coalesce(func.sum(GenerationUsage.input_tokens), 0),
                func.coalesce(func.sum(GenerationUsage.cache_creation_input_tokens), 0),
                func.coalesce(func.sum(GenerationUsage.cache_read_input_tokens), 0),
                func.coalesce(func.sum(GenerationUsage.output_tokens), 0),
                func.coalesce(func.sum(GenerationUsage.estimated_cost_usd), 0),
                func.coalesce(func.sum(GenerationUsage.estimated_cost_inr), 0),
            ).where(GenerationUsage.bulk_job_id == bulk_id)).one()
            run_dir = bj.csv_path.rsplit("/", 1)[0] if "/" in bj.csv_path else f"bulk/{date_prefix()}/{bulk_id}"
            started = bj.started_at or bj.created_at
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            failures: dict[str, int] = {}
            for r in rows:
                if r["status"] in ("FAILED", "INVALID"):
                    failures[r["error_code"] or "UNKNOWN"] = failures.get(r["error_code"] or "UNKNOWN", 0) + 1
            summary = {
                "bulk_job_id": str(bulk_id), "source_file": bj.original_filename, "llm_mode": bj.llm_mode,
                "total_rows": bj.total_rows, "valid_rows": bj.valid_rows, "invalid_rows": bj.invalid_rows,
                "successful": bj.succeeded, "failed": bj.failed,
                "failure_reasons": failures,
                "document_ids": [str(d.id) for _, d in item_docs],
                "processing_seconds": round((utcnow() - started).total_seconds(), 1),
                "usage": {"input_tokens": int(usage[0]), "cache_creation_input_tokens": int(usage[1]),
                          "cache_read_input_tokens": int(usage[2]), "output_tokens": int(usage[3]),
                          "estimated_cost_usd": float(usage[4]), "estimated_cost_inr": float(usage[5])},
                "generated_at": utcnow().isoformat(),
            }
            pdf_keys = [(_pdf_name(ref, d.document_code), d.pdf_path) for ref, d in item_docs if d.pdf_path]
        # ---- build files outside the transaction ----
        csv_buf = io.StringIO()
        w = csv.DictWriter(csv_buf, fieldnames=MANIFEST_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({k: _safe_cell(r[k]) for k in MANIFEST_COLUMNS})
        csv_bytes = csv_buf.getvalue().encode("utf-8-sig")
        wb = Workbook()
        ws = wb.active
        ws.title = "Manifest"
        ws.append(MANIFEST_COLUMNS)
        for r in rows:
            ws.append([_safe_cell(r[k]) for k in MANIFEST_COLUMNS])
        ws2 = wb.create_sheet("Summary")
        for k, v in summary.items():
            ws2.append([k, json.dumps(v) if isinstance(v, (dict, list)) else v])
        xbuf = io.BytesIO()
        wb.save(xbuf)
        summary_bytes = json.dumps(summary, indent=2).encode("utf-8")
        with TemporaryDirectory(prefix="jmp-zip-") as tmp:
            zpath = Path(tmp) / f"JMP_Batch_{bulk_id}.zip"
            with zipfile.ZipFile(zpath, "w", compression=zipfile.ZIP_STORED) as z:
                for name, key in pdf_keys:
                    z.writestr(f"pdfs/{name}", storage.get_bytes(key))
                z.writestr("manifest.csv", csv_bytes)
                z.writestr("manifest.xlsx", xbuf.getvalue())
                z.writestr("summary.json", summary_bytes)
            zip_key = storage.put_file(f"{run_dir}/JMP_Batch_{bulk_id}.zip", zpath, "application/zip")
        man_csv = storage.put_bytes(f"{run_dir}/manifest.csv", csv_bytes, "text/csv")
        man_xlsx = storage.put_bytes(f"{run_dir}/manifest.xlsx", xbuf.getvalue(),
                                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        sum_key = storage.put_bytes(f"{run_dir}/summary.json", summary_bytes, "application/json")
        with session_scope() as db:
            bj = db.get(BulkJob, bulk_id)
            assert bj is not None
            bj.zip_path, bj.manifest_csv_path, bj.manifest_xlsx_path, bj.summary_json_path = (
                zip_key, man_csv, man_xlsx, sum_key)
            bj.status = BulkStatus.completed if (bj.failed == 0 and bj.invalid_rows == 0) else \
                BulkStatus.completed_with_errors
            if bj.valid_rows > 0 and bj.succeeded == 0:
                bj.status = BulkStatus.completed_with_errors
            bj.finished_at = utcnow()
        log.info("bulk_finalized", bulk_id=str(bulk_id), pdfs=len(pdf_keys))
    except Exception as exc:  # finalize failure must be visible, and retryable via /retry-failed
        log.exception("bulk_finalize_failed", bulk_id=str(bulk_id))
        with session_scope() as db:
            bj = db.get(BulkJob, bulk_id)
            if bj is not None:
                bj.status = BulkStatus.failed
                bj.error_message = f"Finalize failed: {type(exc).__name__}: {exc}"[:2000]
        raise


# ------------------------------------------------------------------------------------- operations
def retry_failed(bulk_id: uuid.UUID) -> int:
    with session_scope() as db:
        bj = db.get(BulkJob, bulk_id)
        if bj is None:
            raise NotFoundError("bulk job not found")
        if bj.status in (BulkStatus.processing, BulkStatus.queued, BulkStatus.finalizing):
            raise ConflictError("Bulk job is still running")
        failed = db.scalars(select(BulkJobItem).where(BulkJobItem.bulk_job_id == bulk_id,
                                                      BulkJobItem.status.in_([BulkItemStatus.failed,
                                                                              BulkItemStatus.cancelled]))).all()
        for it in failed:
            it.status, it.counted, it.error_code, it.error_message = BulkItemStatus.queued, False, None, None
            it.generation_job_id = None  # a fresh journey/job is created for the retry (old one kept for audit)
            it.journey_id = None
            it.document_id = None
        n = len(failed)
        if n:
            bj.processed -= n
            bj.failed -= n
            bj.status = BulkStatus.queued
            bj.finished_at = None
            bj.error_message = None
            bj.anthropic_batch_id = None
            bj.analysed = 0
        elif bj.status == BulkStatus.failed:  # finalize failed earlier — just finalize again
            bj.status = BulkStatus.finalizing
    if n:
        start(bulk_id)
    else:
        with session_scope() as db:
            bj = db.get(BulkJob, bulk_id)
            if bj is not None and bj.status == BulkStatus.finalizing:
                finalize(bulk_id)
    return n


def cancel(bulk_id: uuid.UUID) -> int:
    with session_scope() as db:
        bj = db.get(BulkJob, bulk_id)
        if bj is None:
            raise NotFoundError("bulk job not found")
        if bj.status not in (BulkStatus.queued, BulkStatus.processing):
            raise ConflictError(f"Bulk job is {bj.status}")
        items = db.scalars(select(BulkJobItem).where(BulkJobItem.bulk_job_id == bulk_id,
                                                     BulkJobItem.status.in_([BulkItemStatus.queued,
                                                                             BulkItemStatus.processing]))).all()
        ids = []
        for it in items:
            if it.generation_job_id:
                gj = db.get(GenerationJob, it.generation_job_id)
                if gj is not None and (gj.status == JobStatus.queued
                                       or gj.stage in (JobStage.queued, JobStage.awaiting_batch)):
                    gj.status = JobStatus.cancelled
                    ids.append(it.id)
                continue  # in-flight jobs finish normally
            ids.append(it.id)
        if bj.status == BulkStatus.queued:
            bj.status = BulkStatus.processing  # so the cancelled rows can finalize the job
            bj.started_at = bj.started_at or utcnow()
    for iid in ids:
        item_finished(iid, ok=False, error_code="CANCELLED", error_message="Cancelled by user")
    with session_scope() as db:
        db.execute(update(BulkJobItem).where(BulkJobItem.id.in_(ids)).values(status=BulkItemStatus.cancelled))
    return len(ids)


# ------------------------------------------------------------------------------------ batch mode
def on_item_analysed(item_id: uuid.UUID) -> None:
    with session_scope() as db:
        it = db.get(BulkJobItem, item_id)
        if it is None:
            return
        db.execute(update(BulkJob).where(BulkJob.id == it.bulk_job_id).values(analysed=BulkJob.analysed + 1))
        bulk_id = it.bulk_job_id
    maybe_submit_batch(bulk_id)


def maybe_submit_batch(bulk_id: uuid.UUID) -> None:
    """Submit once every valid row is either analysed (awaiting batch) or already terminal."""
    with session_scope() as db:
        bj = db.get(BulkJob, bulk_id)
        if bj is None or bj.llm_mode != "batch" or bj.anthropic_batch_id is not None:
            return
        waiting = db.scalar(select(func.count()).select_from(GenerationJob)
                            .join(BulkJobItem, BulkJobItem.generation_job_id == GenerationJob.id)
                            .where(BulkJobItem.bulk_job_id == bulk_id,
                                   GenerationJob.stage == JobStage.awaiting_batch,
                                   GenerationJob.status == JobStatus.processing)) or 0
        if waiting == 0 or bj.analysed + bj.failed < bj.valid_rows:
            return
        res = db.execute(update(BulkJob).where(BulkJob.id == bulk_id, BulkJob.anthropic_batch_id.is_(None))
                         .values(anthropic_batch_id="submitting"))
        if res.rowcount != 1:
            return
    from app.llm import batch

    try:
        batch_id = batch.submit_bulk_batch(bulk_id)
    except JmpError as exc:
        log.warning("batch_submit_failed_fallback_realtime", bulk_id=str(bulk_id), error=exc.message)
        _fallback_waiting_to_realtime(bulk_id)
        return
    with session_scope() as db:
        db.execute(update(BulkJob).where(BulkJob.id == bulk_id).values(anthropic_batch_id=batch_id))
    schedule_poll(bulk_id)


def schedule_poll(bulk_id: uuid.UUID) -> None:
    mode = settings().job_execution
    if mode == "celery":
        from app.workers.celery_app import celery

        celery.send_task("bulk.poll_batch", args=[str(bulk_id)], queue="bulk",
                         countdown=settings().batch_poll_interval_s)
    elif mode == "thread":
        def loop() -> None:
            while not poll_batch(bulk_id):
                time.sleep(settings().batch_poll_interval_s)

        jobs._pool().submit(loop)
    else:
        while not poll_batch(bulk_id):
            time.sleep(0.01)


def poll_batch(bulk_id: uuid.UUID | str) -> bool:
    """Returns True when the batch has ended and results were processed."""
    from app.llm import batch

    bulk_id = uuid.UUID(str(bulk_id))
    done = batch.process_bulk_batch(bulk_id)
    if not done and settings().job_execution == "celery":
        schedule_poll(bulk_id)
    return done


def _fallback_waiting_to_realtime(bulk_id: uuid.UUID) -> None:
    with session_scope() as db:
        ids = [gj.id for gj in db.scalars(
            select(GenerationJob).join(BulkJobItem, BulkJobItem.generation_job_id == GenerationJob.id)
            .where(BulkJobItem.bulk_job_id == bulk_id, GenerationJob.stage == JobStage.awaiting_batch))]
        db.execute(update(BulkJob).where(BulkJob.id == bulk_id).values(llm_mode="realtime"))
    for jid in ids:
        jobs.dispatch("narrate", jid)
