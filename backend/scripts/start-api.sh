#!/bin/sh
# API container entrypoint: migrate, ingest the hazard library (idempotent), serve.
set -e
python -m alembic upgrade head
python -m app.cli.ingest_hazards
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers "${API_WORKERS:-2}" --proxy-headers
