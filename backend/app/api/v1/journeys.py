from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Header
from fastapi.responses import JSONResponse

from app.db.session import session_scope
from app.schemas.journeys import JobAccepted, normalize_request
from app.services import jobs

router = APIRouter()


@router.post("/journeys/generate", status_code=202, response_model=JobAccepted)
def generate(body: dict[str, Any] = Body(...), idempotency_key: str | None = Header(default=None)) -> JSONResponse:
    if idempotency_key and "idempotency_key" not in body:
        body = {**body, "idempotency_key": idempotency_key}
    req = normalize_request(body)
    with session_scope() as db:
        job = jobs.create_journey_job(db, req)
        job_id, journey_id, status = job.id, job.journey_id, job.status
        is_new = job.status == "queued" and job.stage == "queued" and job.started_at is None
    if is_new:
        jobs.dispatch("analyse", job_id)
    return JSONResponse(status_code=202, content=JobAccepted(job_id=str(job_id), journey_id=str(journey_id),
                                                             status=str(status)).model_dump())
