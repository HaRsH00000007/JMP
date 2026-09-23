#!/bin/sh
# Celery worker entrypoint. QUEUES and CONCURRENCY are set per service in docker-compose.yml.
set -e
exec celery -A app.workers.celery_app worker --loglevel="${LOG_LEVEL:-INFO}" \
  --queues="${QUEUES:-pipeline,render,bulk}" --concurrency="${CONCURRENCY:-4}" \
  --prefetch-multiplier=1 --max-tasks-per-child="${MAX_TASKS_PER_CHILD:-200}"
