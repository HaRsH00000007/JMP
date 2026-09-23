from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.db.models import BulkJobItem, GenerationJob, Journey
from app.db.session import session_scope
from app.errors import NotFoundError
from app.services import jobs

router = APIRouter()


def job_view(db: Any, job: GenerationJob) -> dict[str, Any]:
    journey = db.get(Journey, job.journey_id)
    bulk_id = None
    if job.bulk_job_item_id:
        it = db.get(BulkJobItem, job.bulk_job_item_id)
        bulk_id = str(it.bulk_job_id) if it else None
    route = [s.raw_text for s in sorted(journey.stops, key=lambda s: s.seq)] if journey else []
    return {
        "job_id": str(job.id), "kind": job.kind, "status": job.status, "stage": job.stage, "attempt": job.attempt,
        "journey_id": str(job.journey_id), "journey_code": journey.journey_code if journey else None, "route": route,
        "queued_at": job.queued_at.isoformat(), "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "document_id": str(job.document_id) if job.document_id else None, "bulk_job_id": bulk_id,
        "error": {"code": job.error_code, "message": job.error_message} if job.error_code else None,
    }


@router.get("/jobs")
def list_jobs(status: str | None = None, kind: str | None = None, page: int = Query(1, ge=1),
              size: int = Query(25, ge=1, le=200)) -> dict[str, Any]:
    with session_scope() as db:
        q = select(GenerationJob)
        if status:
            q = q.where(GenerationJob.status == status)
        if kind:
            q = q.where(GenerationJob.kind == kind)
        total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
        rows = db.scalars(q.order_by(GenerationJob.queued_at.desc()).offset((page - 1) * size).limit(size)).all()
        return {"total": total, "page": page, "size": size, "items": [job_view(db, j) for j in rows]}


@router.get("/jobs/{job_id}")
def get_job(job_id: uuid.UUID) -> dict[str, Any]:
    with session_scope() as db:
        job = db.get(GenerationJob, job_id)
        if job is None:
            raise NotFoundError("Job not found")
        return job_view(db, job)


@router.post("/jobs/{job_id}/retry", status_code=202)
def retry(job_id: uuid.UUID) -> dict[str, Any]:
    jobs.retry_job(job_id)
    return get_job(job_id)


@router.post("/jobs/{job_id}/cancel")
def cancel(job_id: uuid.UUID) -> dict[str, Any]:
    jobs.cancel_job(job_id)
    return get_job(job_id)
