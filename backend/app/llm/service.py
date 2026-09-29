"""NarrativeService — builds the request, validates the answer, retries safely, records usage.

Retry policy (generation-flow.md §2.5): on schema/semantic failure only the failing fields are asked for again
(patch retry), after the unchanged cached prefix (so the cache still hits), up to LLM_MAX_ATTEMPTS; an
unparseable answer is asked for again in full. Nothing partially valid is ever returned: every merged answer is
validated as a whole.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from app.domain.facts import JourneyFacts
from app.errors import ErrorCode, LlmError
from app.llm.client import AnthropicNarrativeProvider, LlmCallResult, NarrativeProvider
from app.llm.facts import build_facts_payload, facts_message
from app.llm.mock_provider import MockNarrativeProvider
from app.llm.pricing import cost_usd
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
        # Without the API's schema constraint (see AnthropicNarrativeProvider) the object can arrive inside
        # a ```json fence or with a stray line around it; take the outermost object and validate it as usual.
        start, end = (text or "").find("{"), (text or "").rfind("}")
        try:
            if start < 0 or end <= start:
                raise ValueError("no JSON object in output")
            raw = json.loads(text[start:end + 1])
        except ValueError:
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


def _raw_object(text: str | None) -> dict[str, Any] | None:
    """The JSON object in a reply (tolerating a fence or a stray line around it), or None."""
    text = text or ""
    for candidate in (text, text[text.find("{"):text.rfind("}") + 1] if "{" in text else ""):
        try:
            obj = json.loads(candidate)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        if isinstance(obj, dict):
            return obj
    return None


def failing_fields(errors: list[str]) -> list[str]:
    """Top-level narrative fields named by validation errors ("hazard_notes.3.route_context: …" -> hazard_notes)."""
    out: list[str] = []
    for e in errors:
        m = re.match(r"([a-z_]+)", e)
        if m and m.group(1) in NarrativeV1.model_fields and m.group(1) not in out:
            out.append(m.group(1))
    return out


def patch_request(payload: dict[str, Any], previous: dict[str, Any], fields: list[str], errors: list[str]) -> str:
    """A retry that asks only for the failing fields: the facts, those fields' current values and the errors —
    not the whole previous answer, and not a whole new one back."""
    current = json.dumps({k: previous.get(k) for k in fields}, ensure_ascii=False, separators=(",", ":"))
    return (facts_message(payload)
            + "\n\nYour previous narrative was valid except for these fields:\n" + current
            + "\n\nValidation errors:\n- " + "\n- ".join(errors[:20])
            + f"\n\nReturn ONE JSON object containing ONLY these top-level keys, corrected: {fields}. "
            + "Keep the same shape and every rule; stay well inside each word limit.")


def model_for_attempt(attempt: int) -> tuple[str | None, str | None]:
    """(model, effort) for a call; None = the provider's configured default."""
    s = settings()
    if not s.llm_cost_optimized:
        return None, None
    if s.llm_escalate_final_attempt and attempt == s.llm_max_attempts and attempt > 1:
        return s.anthropic_model, s.anthropic_effort
    return s.llm_economy_model, s.llm_economy_effort


def generate_narrative(jf: JourneyFacts, library: HazardLibrary, *, provider: NarrativeProvider | None = None,
                       on_attempt: UsageSink | None = None) -> tuple[NarrativeV1, str, str]:
    """Returns (narrative, provider_name, model).

    Retries are for blocking failures only and are patch-shaped: after a failure that still produced a JSON
    object, the next call is sent the facts plus only the failing fields and their errors, returns only those
    fields, and they are merged into the previous answer before the whole narrative is validated again.
    """
    s = settings()
    provider = provider or get_narrative_provider()
    prefix = build_static_prefix(library)
    payload = build_facts_payload(jf)
    messages: list[dict[str, Any]] = [{"role": "user", "content": facts_message(payload)}]
    max_tokens = s.anthropic_max_tokens
    last_errors: list[str] = []
    previous: dict[str, Any] | None = None   # the last parsed answer, when retrying by patch
    patch_fields: list[str] = []
    spent_usd = Decimal("0")
    for attempt in range(1, s.llm_max_attempts + 1):
        # A document that keeps failing validation is the expensive failure mode: each retry pays for the
        # whole answer again, and a handful of them can cost more than the batch they sit in. Stop once the
        # budget for one document is gone and fail it, rather than spending the batch on the worst row.
        if s.llm_max_cost_per_document_usd > 0 and spent_usd >= Decimal(str(s.llm_max_cost_per_document_usd)):
            log.warning("llm_document_budget_exhausted", attempt=attempt, spent_usd=float(spent_usd),
                        cap_usd=s.llm_max_cost_per_document_usd)
            raise LlmError(
                f"Narrative abandoned after ${spent_usd:.4f} on {attempt - 1} attempt(s), over the "
                f"${s.llm_max_cost_per_document_usd:.2f} per-document ceiling",
                code=ErrorCode.LLM_INVALID_OUTPUT, details=last_errors[:20])
        model, effort = model_for_attempt(attempt)
        try:
            res = provider.call(prefix, messages, max_tokens=max_tokens, model=model, effort=effort)
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
            spent_usd += cost_usd(res.model, res.usage)
            max_tokens = int(max_tokens * 1.5)
            last_errors = rec.errors
            continue
        text = res.text
        if previous is not None and patch_fields:
            patch = _raw_object(text)
            if patch is not None:
                text = json.dumps({**previous, **{k: v for k, v in patch.items() if k in patch_fields}},
                                  ensure_ascii=False)
        spent_usd += cost_usd(res.model, res.usage)
        narrative, outcome, errors = parse_and_validate(text, payload, library, prefix)
        rec = AttemptRecord(attempt, res, outcome, errors, prefix.sha256)
        if on_attempt:
            on_attempt(rec)
        log.info("llm_attempt", attempt=attempt, outcome=outcome, model=res.model, errors=len(errors),
                 input_tokens=res.usage.input_tokens, cache_read_input_tokens=res.usage.cache_read,
                 cache_creation_input_tokens=res.usage.cache_creation, output_tokens=res.usage.output_tokens,
                 duration_ms=res.duration_ms, prefix_sha256=prefix.sha256[:12], patch=bool(patch_fields))
        if narrative is not None:
            return narrative, provider.name, res.model
        last_errors = errors
        parsed = _raw_object(text)
        fields = failing_fields(errors) if parsed is not None else []
        if parsed is not None and fields and outcome in ("schema_error", "validation_error"):
            previous, patch_fields = parsed, fields
            messages = [{"role": "user", "content": patch_request(payload, parsed, fields, errors)}]
        else:  # unparseable answer: ask again for the whole object
            previous, patch_fields = None, []
            messages = [
                {"role": "user", "content": facts_message(payload)},
                {"role": "assistant", "content": res.text or "{}"},
                {"role": "user", "content": "Your previous JSON failed validation:\n- " + "\n- ".join(errors[:20])
                 + "\nReturn a corrected, complete JSON object that fixes every issue."},
            ]
    raise LlmError(f"Narrative failed validation after {s.llm_max_attempts} attempts",
                   code=ErrorCode.LLM_INVALID_OUTPUT, details=last_errors[:20])
