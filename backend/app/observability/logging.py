"""Structured logging with secret redaction.

Every log event passes through `redact_secrets`, which masks values under sensitive keys and any string
that looks like a credential. Provider clients never log request headers.
"""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

import structlog

_SENSITIVE_KEYS = re.compile(r"(api[_-]?key|authorization|x-api-key|secret|password|token|credential)", re.I)
_SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{8,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),  # Google API keys
    re.compile(r"pk\.[A-Za-z0-9]{20,}\.[A-Za-z0-9_\-]{10,}"),  # Mapbox tokens
    re.compile(r"(?i)(key|token)=([A-Za-z0-9._\-]{12,})"),
]
REDACTED = "***REDACTED***"


def redact_text(text: str) -> str:
    for pat in _SECRET_PATTERNS:
        if pat.groups >= 2:
            text = pat.sub(lambda m: f"{m.group(1)}={REDACTED}", text)
        else:
            text = pat.sub(REDACTED, text)
    return text


def _redact(value: Any, key: str | None = None) -> Any:
    if key is not None and _SENSITIVE_KEYS.search(key) and not key.endswith("_tokens") and key not in {
        "input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"
    }:
        return REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {k: _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_redact(v) for v in value)
    return value


def redact_secrets(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    return {k: _redact(v, k) for k, v in event_dict.items()}


class _RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_text(record.msg)
        if record.args:
            record.args = tuple(redact_text(a) if isinstance(a, str) else a for a in record.args)  # type: ignore[assignment]
        return True


_configured = False


def configure_logging(level: str = "INFO", json_output: bool = True) -> None:
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_RedactingFilter())
    logging.basicConfig(level=level, handlers=[handler], format="%(message)s", force=True)
    # Third-party HTTP loggers can print URLs containing keys (e.g. Google ?key=): keep them quiet.
    for noisy in ("httpx", "httpcore", "anthropic", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    renderer: Any = structlog.processors.JSONRenderer() if json_output else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            redact_secrets,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _configured = True


def get_logger(name: str = "jmp") -> Any:
    return structlog.get_logger(name)
