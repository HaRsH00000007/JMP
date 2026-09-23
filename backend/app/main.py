"""FastAPI application factory."""

from __future__ import annotations

import secrets
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1 import router as v1_router
from app.errors import ErrorCode, JmpError
from app.observability.logging import configure_logging, get_logger
from app.settings import settings


def _error(status: int, code: str, message: str, details: object = None) -> JSONResponse:
    body: dict[str, object] = {"error": {"code": code, "message": message}}
    if details is not None:
        body["error"]["details"] = details  # type: ignore[index]
    return JSONResponse(status_code=status, content=body)


def create_app() -> FastAPI:
    s = settings()
    configure_logging(s.log_level, s.log_json)
    log = get_logger("jmp.api")
    # Everything the API serves lives under /api so a single proxy rule covers it (see frontend/nginx.conf).
    app = FastAPI(title="JMP — Journey Management Plan Generator", version=s.app_version,
                  docs_url="/api/docs", redoc_url="/api/redoc", openapi_url="/api/openapi.json")
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_credentials=False,
                       allow_methods=["GET", "POST", "DELETE"], allow_headers=["Authorization", "Content-Type",
                                                                               "Idempotency-Key"],
                       expose_headers=["Content-Disposition"])

    @app.middleware("http")
    async def auth_and_log(request: Request, call_next):  # noqa: ANN001, ANN202
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        path = request.url.path
        token = settings().api_auth_token
        protected = path.startswith("/api/v1") and not path.startswith("/api/v1/health") and request.method != "OPTIONS"
        if token is not None and token.get_secret_value() and protected:
            supplied = request.headers.get("authorization", "")
            expected = f"Bearer {token.get_secret_value()}"
            if not secrets.compare_digest(supplied.encode(), expected.encode()):
                return _error(401, ErrorCode.UNAUTHORIZED, "Missing or invalid bearer token")
        t0 = time.monotonic()
        response = await call_next(request)
        response.headers["x-request-id"] = rid
        if path.startswith("/api/v1") and not path.endswith("/health"):
            log.info("http_request", method=request.method, path=path, status=response.status_code,
                     ms=int((time.monotonic() - t0) * 1000), request_id=rid)
        return response

    @app.exception_handler(JmpError)
    async def jmp_error(_: Request, exc: JmpError) -> JSONResponse:
        return _error(exc.http_status, str(exc.code), exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [{"field": ".".join(str(p) for p in e["loc"] if p != "body"),
                    "issue": str(e["msg"]).removeprefix("Value error, ")} for e in exc.errors()]
        return _error(422, ErrorCode.VALIDATION_ERROR, "Request is invalid", details)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = ErrorCode.NOT_FOUND if exc.status_code == 404 else ErrorCode.VALIDATION_ERROR
        return _error(exc.status_code, str(code), str(exc.detail))

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error", error=type(exc).__name__)
        return _error(500, ErrorCode.INTERNAL_ERROR, "Internal server error")

    # Convenience redirects: the docs are at /api/docs, but these are the paths people type out of habit.
    @app.get("/", include_in_schema=False)
    @app.get("/docs", include_in_schema=False)
    def _docs_redirect() -> RedirectResponse:
        return RedirectResponse("/api/docs")

    @app.get("/openapi.json", include_in_schema=False)
    def _openapi_redirect() -> RedirectResponse:
        return RedirectResponse("/api/openapi.json")

    app.include_router(v1_router, prefix="/api/v1")
    return app


app = create_app()
