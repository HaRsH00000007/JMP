from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import select, text

from app.db.models import HazardLibraryVersion
from app.db.session import session_scope
from app.settings import settings
from app.storage import get_storage

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": settings().app_version}


@router.get("/health/ready")
def ready() -> JSONResponse:
    checks: dict[str, Any] = {}
    ok = True
    try:
        with session_scope() as db:
            db.execute(text("SELECT 1"))
            lib = db.scalar(select(HazardLibraryVersion).where(HazardLibraryVersion.is_active.is_(True)))
            checks["database"] = "ok"
            checks["hazard_library"] = lib.version if lib else "missing"
            ok = ok and lib is not None
    except Exception as exc:  # noqa: BLE001
        checks["database"] = f"error: {type(exc).__name__}"
        ok = False
    try:
        st = get_storage()
        st.put_bytes("health/probe.txt", b"ok", "text/plain")
        checks["storage"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["storage"] = f"error: {type(exc).__name__}"
        ok = False
    if settings().job_execution == "celery":
        try:
            import redis

            redis.Redis.from_url(settings().redis_url, socket_timeout=2).ping()
            checks["redis"] = "ok"
        except Exception as exc:  # noqa: BLE001
            checks["redis"] = f"error: {type(exc).__name__}"
            ok = False
    checks["llm_provider"] = settings().llm_provider
    checks["llm_configured"] = settings().llm_provider == "mock" or bool(settings().anthropic_api_key)
    return JSONResponse(status_code=200 if ok else 503, content={"status": "ready" if ok else "degraded", **checks})
