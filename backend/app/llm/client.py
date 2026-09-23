"""Anthropic client wrapper — the only module that talks to the Claude API.

* static system prefix with one cache_control breakpoint (cache writes on first call, reads after)
* structured output via output_config.format (JSON schema) + effort
* server-side refusal fallbacks ("default" mode) when enabled
* typed error mapping: retryable (429 / 5xx / overloaded / network / timeout) vs not
* usage extraction including cache-creation split by TTL
The API key comes from settings and is never logged.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import anthropic

from app.errors import ErrorCode, LlmError
from app.llm.pricing import TokenUsage
from app.llm.schemas import api_json_schema
from app.llm.static_prefix import StaticPrefix
from app.settings import settings

FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass
class LlmCallResult:
    text: str
    usage: TokenUsage
    stop_reason: str | None
    request_id: str | None
    duration_ms: int
    model: str
    service_tier: str = "standard"
    extra: dict[str, Any] = field(default_factory=dict)


class NarrativeProvider(Protocol):
    name: str
    model: str

    def call(self, prefix: StaticPrefix, messages: list[dict[str, Any]], *, max_tokens: int) -> LlmCallResult: ...


def usage_from_response(usage: Any) -> TokenUsage:
    cc = getattr(usage, "cache_creation", None)
    c5 = int(getattr(cc, "ephemeral_5m_input_tokens", 0) or 0) if cc is not None else 0
    c1 = int(getattr(cc, "ephemeral_1h_input_tokens", 0) or 0) if cc is not None else 0
    total_cc = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    if c5 + c1 == 0 and total_cc:
        if settings().anthropic_cache_ttl == "1h":
            c1 = total_cc
        else:
            c5 = total_cc
    return TokenUsage(input_tokens=int(getattr(usage, "input_tokens", 0) or 0), cache_creation_5m=c5,
                      cache_creation_1h=c1, cache_read=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
                      output_tokens=int(getattr(usage, "output_tokens", 0) or 0))


def first_text(content: Any) -> str:
    for block in content or []:
        if getattr(block, "type", None) == "text":
            return str(block.text)
    return ""


def request_params(prefix: StaticPrefix, messages: list[dict[str, Any]], *, model: str, max_tokens: int,
                   for_batch: bool = False) -> dict[str, Any]:
    s = settings()
    params: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": prefix.system_param("1h" if for_batch else s.anthropic_cache_ttl),
        "messages": messages,
        "output_config": {"effort": s.anthropic_effort, "format": {"type": "json_schema", "schema": api_json_schema()}},
    }
    return params


class AnthropicNarrativeProvider:
    name = "anthropic"

    def __init__(self, client: Any | None = None, model: str | None = None) -> None:
        s = settings()
        self.model = model or s.anthropic_model
        if client is None:
            key = s.anthropic_api_key.get_secret_value() if s.anthropic_api_key else None
            if not key:
                raise LlmError("ANTHROPIC_API_KEY is not configured (set LLM_PROVIDER=mock for demo narrative)",
                               code=ErrorCode.LLM_NOT_CONFIGURED)
            client = anthropic.Anthropic(api_key=key, max_retries=s.anthropic_sdk_max_retries,
                                         timeout=s.anthropic_timeout_s)
        self.client = client

    def call(self, prefix: StaticPrefix, messages: list[dict[str, Any]], *, max_tokens: int) -> LlmCallResult:
        s = settings()
        params = request_params(prefix, messages, model=self.model, max_tokens=max_tokens)
        if s.anthropic_fallbacks_enabled:
            params["extra_headers"] = {"anthropic-beta": FALLBACK_BETA}
            params["extra_body"] = {"fallbacks": "default"}
        t0 = time.monotonic()
        try:
            resp = self.client.messages.create(**params)
        except anthropic.RateLimitError as exc:
            raise LlmError("Anthropic rate limit", code=ErrorCode.LLM_UNAVAILABLE) from exc
        except anthropic.APITimeoutError as exc:
            raise LlmError("Anthropic request timed out", code=ErrorCode.LLM_UNAVAILABLE) from exc
        except anthropic.APIConnectionError as exc:
            raise LlmError("Anthropic connection error", code=ErrorCode.LLM_UNAVAILABLE) from exc
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError, anthropic.NotFoundError) as exc:
            raise LlmError(f"Anthropic configuration error ({type(exc).__name__})",
                           code=ErrorCode.LLM_NOT_CONFIGURED) from exc
        except anthropic.BadRequestError as exc:
            raise LlmError(f"Anthropic rejected the request: {exc.message}", code=ErrorCode.LLM_NOT_CONFIGURED) from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500 or exc.status_code == 429:
                raise LlmError(f"Anthropic server error {exc.status_code}", code=ErrorCode.LLM_UNAVAILABLE) from exc
            raise LlmError(f"Anthropic API error {exc.status_code}", code=ErrorCode.LLM_NOT_CONFIGURED) from exc
        dur = int((time.monotonic() - t0) * 1000)
        stop = getattr(resp, "stop_reason", None)
        return LlmCallResult(text=first_text(resp.content), usage=usage_from_response(resp.usage), stop_reason=stop,
                             request_id=getattr(resp, "_request_id", None) or getattr(resp, "id", None),
                             duration_ms=dur, model=getattr(resp, "model", self.model) or self.model)
