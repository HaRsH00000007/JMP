"""Celery task wrappers — thin: all logic lives in app.services (testable without a broker)."""

from __future__ import annotations

from celery import Task

from app.services import bulk, jobs
from app.workers.celery_app import celery


@celery.task(bind=True, name="jmp.stage", max_retries=None)
def stage_task(self: Task, stage: str, job_id: str, attempt: int = 1) -> None:
    try:
        nxt = jobs.run_stage(stage, job_id, attempt)
    except jobs.RetryStage as r:
        raise self.retry(args=[stage, job_id, attempt + 1], countdown=r.countdown,
                         queue=jobs.STAGE_QUEUE[stage]) from None
    if nxt and nxt != "await_batch":
        jobs.dispatch(nxt, job_id)


@celery.task(name="bulk.fanout")
def fanout_task(bulk_id: str) -> None:
    bulk.fanout(bulk_id)


@celery.task(name="bulk.finalize")
def finalize_task(bulk_id: str) -> None:
    bulk.finalize(bulk_id)


@celery.task(name="bulk.poll_batch")
def poll_batch_task(bulk_id: str) -> None:
    bulk.poll_batch(bulk_id)


@celery.task(name="jmp.reap_stuck")
def reap_stuck_task() -> int:
    return jobs.reap_stuck_jobs()
