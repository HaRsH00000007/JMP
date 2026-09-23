# JMP backend: FastAPI API + Celery workers (same image, different command).
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# Dependencies first (layer cache)
COPY pyproject.toml ./
RUN pip install --upgrade pip \
 && python - <<'EOF'
import tomllib, subprocess, sys
deps = tomllib.load(open("pyproject.toml", "rb"))["project"]["dependencies"]
subprocess.check_call([sys.executable, "-m", "pip", "install", *deps, "boto3>=1.34"])
EOF
# Chromium + its system libraries for PDF rendering
RUN python -m playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/*

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY config ./config
COPY templates ./templates
COPY scripts ./scripts

RUN useradd --create-home --uid 10001 jmp && mkdir -p /data/storage && chown -R jmp:jmp /data /app
USER jmp

EXPOSE 8000
CMD ["sh", "scripts/start-api.sh"]
