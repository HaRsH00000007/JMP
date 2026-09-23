"""Celery application. Queues:

    pipeline — analyse stage (providers + deterministic engines)
    llm      — narrate stage; run this worker with --concurrency=$LLM_CONCURRENCY to cap parallel Claude calls
    render   — Playwright PDF rendering (CPU/memory heavy; 2–4 per container)
    bulk     — bulk fan-out, batch polling, finalize (ZIP/manifests)
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.observability.logging import configure_logging
from app.settings import settings

s = settings()
configure_logging(s.log_level, s.log_json)

celery = Celery("jmp", broker=s.redis_url, include=["app.workers.tasks"])
celery.conf.update(
    task_acks_late=True,                 # redeliver if a worker dies mid-task (tasks are idempotent)
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,        # fair dispatch for long tasks
    task_default_queue="pipeline",
    task_ignore_result=True,             # state lives in Postgres, not the result backend
    broker_transport_options={"visibility_timeout": 3600},
    task_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    beat_schedule={
        "reap-stuck-jobs": {"task": "jmp.reap_stuck", "schedule": crontab(minute="*/10"), "options": {"queue": "bulk"}},
    },
)
