"""Stable error codes (api-design.md §5) and the domain exception hierarchy."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    UNAUTHORIZED = "UNAUTHORIZED"
    GEOCODE_NOT_FOUND = "GEOCODE_NOT_FOUND"
    GEOCODE_AMBIGUOUS = "GEOCODE_AMBIGUOUS"
    ROUTE_NOT_FOUND = "ROUTE_NOT_FOUND"
    ROUTE_GEOMETRY_UNAVAILABLE = "ROUTE_GEOMETRY_UNAVAILABLE"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    LLM_UNAVAILABLE = "LLM_UNAVAILABLE"
    LLM_INVALID_OUTPUT = "LLM_INVALID_OUTPUT"
    LLM_REFUSAL = "LLM_REFUSAL"
    LLM_NOT_CONFIGURED = "LLM_NOT_CONFIGURED"
    RENDER_OVERFLOW = "RENDER_OVERFLOW"
    PDF_RENDER_FAILED = "PDF_RENDER_FAILED"
    HAZARD_LIBRARY_MISSING = "HAZARD_LIBRARY_MISSING"
    INTERNAL_ERROR = "INTERNAL_ERROR"


RETRYABLE = {
    ErrorCode.PROVIDER_UNAVAILABLE,
    ErrorCode.LLM_UNAVAILABLE,
    ErrorCode.PDF_RENDER_FAILED,
    ErrorCode.INTERNAL_ERROR,
}


class JmpError(Exception):
    """Base error carrying a stable code. `retryable` drives worker retry behaviour."""

    code: ErrorCode = ErrorCode.INTERNAL_ERROR
    http_status: int = 500

    def __init__(self, message: str, *, code: ErrorCode | None = None, details: Any = None,
                 http_status: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if http_status is not None:
            self.http_status = http_status
        self.details = details

    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE


class InputValidationError(JmpError):
    code = ErrorCode.VALIDATION_ERROR
    http_status = 422


class NotFoundError(JmpError):
    code = ErrorCode.NOT_FOUND
    http_status = 404


class ConflictError(JmpError):
    code = ErrorCode.CONFLICT
    http_status = 409


class ProviderError(JmpError):
    code = ErrorCode.PROVIDER_UNAVAILABLE
    http_status = 502


class GeocodeError(JmpError):
    code = ErrorCode.GEOCODE_NOT_FOUND
    http_status = 422


class RouteError(JmpError):
    code = ErrorCode.ROUTE_NOT_FOUND
    http_status = 422


class LlmError(JmpError):
    code = ErrorCode.LLM_UNAVAILABLE
    http_status = 502


class RenderError(JmpError):
    code = ErrorCode.PDF_RENDER_FAILED
    http_status = 500
