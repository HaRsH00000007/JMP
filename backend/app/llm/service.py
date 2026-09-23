"""NarrativeService — builds the request, validates the answer, retries safely, records usage.

Retry policy (generation-flow.md §2.5): on schema/semantic failure the previous output and the exact
validation errors are appended as new turns AFTER the unchanged cached prefix (so the cache still hits),
up to LLM_MAX_ATTEMPTS. Nothing partially valid is ever returned.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.domain.facts import JourneyFacts
from app.errors import ErrorCode, LlmError
from app.llm.client import AnthropicNarrativeProvider, LlmCallResult, NarrativeProvider
from app.llm.facts import build_facts_payload, facts_message
from app.llm.mock_provider import MockNarrativeProvider
from app.llm.schemas import NarrativeV1
from app.llm.static_prefix import StaticPrefix, build_static_prefix
from app.llm.validator import validate_narrative
from app.observability.logging import get_logger
from app.services.hazard_library import HazardLibrary
from app.settings import settings

log = get_logger("jmp.llm")


@dataclass
class AttemptRecord:
    attempt: int
    call: LlmCallResult | None
    outcome: str  # ok | invalid_json | schema_error | validation_error | refusal | max_tokens | api_error
    errors: list[str]
    prefix_sha256: str


UsageSink = Callable[[AttemptRecord], None]


def get_narrative_provider() -> NarrativeProvider:
    return MockNarrativeProvider() if settings().llm_provider == "mock" else AnthropicNarrativeProvider()


def parse_and_validate(text: str, payload: dict[str, Any], library: HazardLibrary,
                       prefix: StaticPrefix) -> tuple[NarrativeV1 | None, str, list[str]]:
    try:
        raw = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        return None, "invalid_json", [f"Output is not valid JSON: {exc}"]
    try:
        narrative = NarrativeV1.model_validate(raw)
    except ValidationError as exc:
        errs = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:25]]
        return None, "schema_error", errs
    errs = validate_narrative(narrative, payload, library, prefix.library_numbers)
    if errs:
        return None, "validation_error", errs
    return narrative, "ok", []


def generate_narrative(jf: JourneyFacts, library: HazardLibrary, *, provider: NarrativeProvider | None = None,
                       on_attempt: UsageSink | None = None) -> tuple[NarrativeV1, str, str]:
    """Returns (narrative, provider_name, model)."""
    s = settings()
    provider = provider or get_narrative_provider()
    prefix = build_static_prefix(library)
    payload = build_facts_payload(jf)
    messages: list[dict[str, Any]] = [{"role": "user", "content": facts_message(payload)}]
    max_tokens = s.anthropic_max_tokens
    last_errors: list[str] = []
    for attempt in range(1, s.llm_max_attempts + 1):
        try:
            res = provider.call(prefix, messages, max_tokens=max_tokens)
        except LlmError as exc:
            rec = AttemptRecord(attempt, None, "api_error", [exc.message], prefix.sha256)
            if on_attempt:
                on_attempt(rec)
            raise
        if res.stop_reason == "refusal":
            rec = AttemptRecord(attempt, res, "refusal", ["model declined the request"], prefix.sha256)
            if on_attempt:
                on_attempt(rec)
            raise LlmError("Claude declined to generate the narrative", code=ErrorCode.LLM_REFUSAL)
        if res.stop_reason == "max_tokens":
            rec = AttemptRecord(attempt, res, "max_tokens", ["output truncated at max_tokens"], prefix.sha256)
            if on_attempt:
                on_attempt(rec)
            max_tokens = int(max_tokens * 1.5)
            last_errors = rec.errors
            continue
        narrative, outcome, errors = parse_and_validate(res.text, payload, library, prefix)
        rec = AttemptRecord(attempt, res, outcome, errors, prefix.sha256)
        if on_attempt:
            on_attempt(rec)
        log.info("llm_attempt", attempt=attempt, outcome=outcome, model=res.model, errors=len(errors),
                 input_tokens=res.usage.input_tokens, cache_read_input_tokens=res.usage.cache_read,
                 cache_creation_input_tokens=res.usage.cache_creation, output_tokens=res.usage.output_tokens,
                 duration_ms=res.duration_ms, prefix_sha256=prefix.sha256[:12])
        if narrative is not None:
            return narrative, provider.name, res.model
        last_errors = errors
        messages = messages + [
            {"role": "assistant", "content": res.text or "{}"},
            {"role": "user", "content": "Your previous JSON failed validation:\n- " + "\n- ".join(errors[:20])
             + "\nReturn a corrected, complete JSON object that fixes every issue."},
        ]
    raise LlmError(f"Narrative failed validation after {s.llm_max_attempts} attempts",
                   code=ErrorCode.LLM_INVALID_OUTPUT, details=last_errors[:20])
