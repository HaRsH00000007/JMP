"""Message Batches API path for bulk jobs (BULK_LLM_MODE=batch): 50% token price, results typically within
an hour (max 24 h). Same static prefix (1-hour TTL, since batch requests may be processed minutes apart),
same schema, same validator. Server-side refusal fallbacks are not available on the Batches API, so a
refused/invalid/errored row falls back to the realtime path for that row only.
"""

from __future__ import annotations

import uuid
from typing import Any

import anthropic
from sqlalchemy import select

from app.db.models import BulkJobItem, GenerationJob, JobStage
from app.db.session import session_scope
from app.domain.facts import JourneyFacts
from app.errors import ErrorCode, LlmError
from app.llm.client import LlmCallResult, first_text, request_params, usage_from_response
from app.llm.facts import build_facts_payload, facts_message
from app.llm.service import AttemptRecord, parse_and_validate
from app.llm.static_prefix import build_static_prefix
from app.observability.logging import get_logger
from app.settings import settings

log = get_logger("jmp.batch")
_client_override: Any = None


def set_client(client: Any) -> None:
    """Test hook."""
    global _client_override
    _client_override = client


def _client() -> Any:
    if _client_override is not None:
        return _client_override
    s = settings()
    if not s.anthropic_api_key:
        raise LlmError("ANTHROPIC_API_KEY is not configured", code=ErrorCode.LLM_NOT_CONFIGURED)
    return anthropic.Anthropic(api_key=s.anthropic_api_key.get_secret_value(), max_retries=s.anthropic_sdk_max_retries,
                               timeout=s.anthropic_timeout_s)


def _waiting(db: Any, bulk_id: uuid.UUID) -> list[GenerationJob]:
    return list(db.scalars(
        select(GenerationJob).join(BulkJobItem, BulkJobItem.generation_job_id == GenerationJob.id)
        .where(BulkJobItem.bulk_job_id == bulk_id, GenerationJob.stage == JobStage.awaiting_batch)))


def submit_bulk_batch(bulk_id: uuid.UUID) -> str:
    from app.services.jobs import load_library_for

    s = settings()
    requests = []
    with session_scope() as db:
        for gj in _waiting(db, bulk_id):
            jf = JourneyFacts.model_validate(gj.report_facts)
            lib = load_library_for(db, jf.hazard_library_version)
            prefix = build_static_prefix(lib)
            messages = [{"role": "user", "content": facts_message(build_facts_payload(jf))}]
            params = request_params(prefix, messages, model=s.anthropic_model, max_tokens=s.anthropic_max_tokens,
                                    for_batch=True)
            requests.append({"custom_id": str(gj.id), "params": params})
    if not requests:
        raise LlmError("No rows awaiting the batch", code=ErrorCode.INTERNAL_ERROR)
    try:
        batch = _client().messages.batches.create(requests=requests)
    except anthropic.APIError as exc:
        raise LlmError(f"Batch submission failed: {type(exc).__name__}", code=ErrorCode.LLM_UNAVAILABLE) from exc
    log.info("batch_submitted", bulk_id=str(bulk_id), batch_id=batch.id, requests=len(requests))
    return str(batch.id)


def process_bulk_batch(bulk_id: uuid.UUID) -> bool:
    from app.db.models import BulkJob
    from app.services import jobs

    with session_scope() as db:
        bj = db.get(BulkJob, bulk_id)
        if bj is None or not bj.anthropic_batch_id or bj.anthropic_batch_id == "submitting":
            return False
        batch_id = bj.anthropic_batch_id
    client = _client()
    status = client.messages.batches.retrieve(batch_id)
    if status.processing_status != "ended":
        return False
    for result in client.messages.batches.results(batch_id):
        job_id = uuid.UUID(result.custom_id)
        with session_scope() as db:
            gj = db.get(GenerationJob, job_id)
            if gj is None or gj.stage != JobStage.awaiting_batch:
                continue  # already handled (idempotent re-poll)
            jf = JourneyFacts.model_validate(gj.report_facts)
            lib = jobs.load_library_for(db, jf.hazard_library_version)
            journey_id = gj.journey_id
            item = db.get(BulkJobItem, gj.bulk_job_item_id) if gj.bulk_job_item_id else None
            gj.stage = JobStage.narrative
        prefix = build_static_prefix(lib)
        rtype = result.result.type
        if rtype == "succeeded":
            msg = result.result.message
            call = LlmCallResult(text=first_text(msg.content), usage=usage_from_response(msg.usage),
                                 stop_reason=msg.stop_reason, request_id=getattr(msg, "id", None), duration_ms=0,
                                 model=msg.model, service_tier="batch")
            if msg.stop_reason == "refusal":
                narrative, outcome, errors = None, "refusal", ["model declined the request"]
            else:
                narrative, outcome, errors = parse_and_validate(call.text, build_facts_payload(jf), lib, prefix)
            jobs.record_usage(job_id, journey_id, item.bulk_job_id if item else None,
                              AttemptRecord(1, call, outcome, errors, prefix.sha256), batch=True)
            if narrative is not None:
                jobs.store_narrative(job_id, narrative, "anthropic", call.model)
                jobs.dispatch("render", job_id)
                continue
            log.info("batch_result_invalid_fallback_realtime", job_id=str(job_id), outcome=outcome)
        else:
            log.info("batch_result_not_succeeded_fallback_realtime", job_id=str(job_id), type=rtype)
        jobs.dispatch("narrate", job_id)  # realtime path for this row only
    return True
