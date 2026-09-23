from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, File, Form, Header, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from sqlalchemy import func, select

from app.db.models import BulkJob, BulkJobItem, GenerationUsage, UploadedCsv
from app.db.session import session_scope
from app.errors import InputValidationError, NotFoundError
from app.services import bulk
from app.services.bulk_csv import parse_csv, sample_csv
from app.settings import settings
from app.storage import get_storage

router = APIRouter()


async def _read(file: UploadFile) -> bytes:
    data = await file.read(settings().bulk_max_bytes + 1)
    if len(data) > settings().bulk_max_bytes:
        raise InputValidationError(f"CSV exceeds {settings().bulk_max_bytes // (1024 * 1024)} MB limit")
    return data


@router.get("/bulk-jobs/sample.csv")
def sample() -> Response:
    return Response(content=sample_csv(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="jmp_bulk_template.csv"'})


@router.post("/bulk-jobs/validate")
async def validate(file: UploadFile = File(...)) -> dict[str, Any]:
    data = await _read(file)
    parsed = parse_csv(data)
    upload_id = uuid.uuid4()
    key = get_storage().put_bytes(f"uploads/{upload_id}.csv", data, "text/csv")
    with session_scope() as db:
        db.add(UploadedCsv(id=upload_id, original_filename=file.filename or "upload.csv", csv_path=key,
                           csv_sha256=hashlib.sha256(data).hexdigest()))
    return {"upload_id": str(upload_id), **parsed.summary()}


@router.post("/bulk-jobs", status_code=202)
async def create(file: UploadFile | None = File(default=None), upload_id: str | None = Form(default=None),
                 llm_mode: str | None = Form(default=None),
                 idempotency_key: str | None = Header(default=None)) -> dict[str, Any]:
    if llm_mode not in (None, "", "realtime", "batch"):
        raise InputValidationError("llm_mode must be 'realtime' or 'batch'")
    if file is not None:
        data, filename = await _read(file), file.filename or "upload.csv"
    elif upload_id:
        with session_scope() as db:
            up = db.get(UploadedCsv, uuid.UUID(upload_id))
            if up is None:
                raise NotFoundError("upload_id not found (validated uploads are kept 24 h)")
            created = up.created_at if up.created_at.tzinfo else up.created_at.replace(tzinfo=timezone.utc)
            if created < datetime.now(timezone.utc) - timedelta(hours=24):
                raise NotFoundError("upload_id has expired; upload the CSV again")
            key, filename = up.csv_path, up.original_filename
        data = get_storage().get_bytes(key)
    else:
        raise InputValidationError("Provide either a CSV file or an upload_id from /bulk-jobs/validate")
    parsed = parse_csv(data)
    with session_scope() as db:
        bj = bulk.create_bulk_job(db, parsed, filename=filename, csv_bytes=data, llm_mode=llm_mode or None,
                                  idempotency_key=idempotency_key)
        bid, status, new = bj.id, bj.status, bj.started_at is None and bj.status == "queued"
        body = {"job_id": str(bj.id), "total_routes": bj.total_rows, "valid_routes": bj.valid_rows,
                "invalid_routes": bj.invalid_rows, "status": status, "llm_mode": bj.llm_mode}
    if new:
        await run_in_threadpool(bulk.start, bid)
    return body


def bulk_view(db: Any, bj: BulkJob) -> dict[str, Any]:
    usage = db.execute(select(
        func.coalesce(func.sum(GenerationUsage.input_tokens), 0),
        func.coalesce(func.sum(GenerationUsage.cache_read_input_tokens), 0),
        func.coalesce(func.sum(GenerationUsage.cache_creation_input_tokens), 0),
        func.coalesce(func.sum(GenerationUsage.output_tokens), 0),
        func.coalesce(func.sum(GenerationUsage.estimated_cost_usd), 0),
        func.coalesce(func.sum(GenerationUsage.estimated_cost_inr), 0),
    ).where(GenerationUsage.bulk_job_id == bj.id)).one()
    started = bj.started_at
    elapsed = eta = None
    if started:
        started = started if started.tzinfo else started.replace(tzinfo=timezone.utc)
        end = bj.finished_at or datetime.now(timezone.utc)
        end = end if end.tzinfo else end.replace(tzinfo=timezone.utc)
        elapsed = round((end - started).total_seconds())
        if bj.processed and bj.processed < bj.valid_rows and not bj.finished_at:
            eta = round(elapsed / bj.processed * (bj.valid_rows - bj.processed))
    pct = 100 if bj.valid_rows == 0 else int(100 * bj.processed / bj.valid_rows)
    done = bj.zip_path is not None
    return {
        "job_id": str(bj.id), "status": bj.status, "llm_mode": bj.llm_mode, "filename": bj.original_filename,
        "total": bj.total_rows, "valid": bj.valid_rows, "invalid": bj.invalid_rows, "processed": bj.processed,
        "successful": bj.succeeded, "failed": bj.failed, "percentage": pct,
        "created_at": bj.created_at.isoformat(), "started_at": bj.started_at.isoformat() if bj.started_at else None,
        "finished_at": bj.finished_at.isoformat() if bj.finished_at else None,
        "elapsed_seconds": elapsed, "eta_seconds": eta, "error": bj.error_message,
        "downloads": {
            "zip": f"/api/v1/bulk-jobs/{bj.id}/download" if done else None,
            "manifest_csv": f"/api/v1/bulk-jobs/{bj.id}/manifest?format=csv" if done else None,
            "manifest_xlsx": f"/api/v1/bulk-jobs/{bj.id}/manifest?format=xlsx" if done else None,
            "summary_json": f"/api/v1/bulk-jobs/{bj.id}/manifest?format=json" if done else None,
        },
        "usage": {"input_tokens": int(usage[0]), "cache_read_input_tokens": int(usage[1]),
                  "cache_creation_input_tokens": int(usage[2]), "output_tokens": int(usage[3]),
                  "estimated_cost_usd": float(usage[4]), "estimated_cost_inr": float(usage[5])},
    }


@router.get("/bulk-jobs")
def list_bulk(page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    with session_scope() as db:
        total = db.scalar(select(func.count()).select_from(BulkJob)) or 0
        rows = db.scalars(select(BulkJob).order_by(BulkJob.created_at.desc()).offset((page - 1) * size)
                          .limit(size)).all()
        return {"total": total, "page": page, "size": size, "items": [bulk_view(db, b) for b in rows]}


@router.get("/bulk-jobs/{bulk_id}")
def get_bulk(bulk_id: uuid.UUID) -> dict[str, Any]:
    with session_scope() as db:
        bj = db.get(BulkJob, bulk_id)
        if bj is None:
            raise NotFoundError("Bulk job not found")
        return bulk_view(db, bj)


@router.get("/bulk-jobs/{bulk_id}/items")
def items(bulk_id: uuid.UUID, status: str | None = None, page: int = Query(1, ge=1),
          size: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
    with session_scope() as db:
        q = select(BulkJobItem).where(BulkJobItem.bulk_job_id == bulk_id)
        if status:
            q = q.where(BulkJobItem.status == status)
        total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
        rows = db.scalars(q.order_by(BulkJobItem.row_number).offset((page - 1) * size).limit(size)).all()
        return {"total": total, "page": page, "size": size, "items": [{
            "row_number": r.row_number, "route_id": r.route_ref, "status": r.status,
            "document_id": str(r.document_id) if r.document_id else None,
            "job_id": str(r.generation_job_id) if r.generation_job_id else None,
            "error_code": r.error_code, "error_message": r.error_message,
            "validation_errors": r.validation_errors,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None} for r in rows]}


@router.post("/bulk-jobs/{bulk_id}/retry-failed", status_code=202)
def retry_failed(bulk_id: uuid.UUID) -> dict[str, Any]:
    n = bulk.retry_failed(bulk_id)
    return {"requeued": n, **get_bulk(bulk_id)}


@router.post("/bulk-jobs/{bulk_id}/cancel")
def cancel(bulk_id: uuid.UUID) -> dict[str, Any]:
    n = bulk.cancel(bulk_id)
    return {"cancelled": n, **get_bulk(bulk_id)}


@router.get("/bulk-jobs/{bulk_id}/download")
def download(bulk_id: uuid.UUID) -> Response:
    with session_scope() as db:
        bj = db.get(BulkJob, bulk_id)
        if bj is None or not bj.zip_path:
            raise NotFoundError("ZIP not available yet")
        key = bj.zip_path
    return Response(content=get_storage().get_bytes(key), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="JMP_Batch_{bulk_id}.zip"'})


@router.get("/bulk-jobs/{bulk_id}/manifest")
def manifest(bulk_id: uuid.UUID, format: str = Query("csv", pattern="^(csv|xlsx|json)$")) -> Response:  # noqa: A002
    with session_scope() as db:
        bj = db.get(BulkJob, bulk_id)
        if bj is None or not bj.manifest_csv_path:
            raise NotFoundError("Manifest not available yet")
        key = {"csv": bj.manifest_csv_path, "xlsx": bj.manifest_xlsx_path, "json": bj.summary_json_path}[format]
    media = {"csv": "text/csv", "json": "application/json",
             "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}[format]
    name = "summary.json" if format == "json" else f"manifest.{format}"
    return Response(content=get_storage().get_bytes(key), media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="JMP_Batch_{bulk_id}_{name}"'})
