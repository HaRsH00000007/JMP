"""Generation-job orchestration (generation-flow.md §1).

Three persisted stages per journey — each reads its inputs from the DB and writes its outputs back, so a
retry resumes from the failed stage without repeating paid work:

    analyse  (stages 2–8: providers + deterministic engines)   queue "pipeline"
    narrate  (stage 9: Claude, validated)                      queue "llm"
    render   (stages 10–12: assemble → HTML → PDF → storage)   queue "render"

Execution mode (JOB_EXECUTION): celery | thread | sync. A failure is recorded on the job (and on its bulk
item) and never propagates to other jobs.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app import rules_config
from app.db.models import (
    GenerationJob,
    GenerationUsage,
    Hazard,
    JmpDocument,
    JobStage,
    JobStatus,
    Journey,
    JourneyCodeSequence,
    JourneyHazard,
    JourneyScore,
    JourneyStop,
    RouteAnalysis,
)
from app.db.session import session_scope
from app.domain.facts import JourneyFacts
from app.errors import ErrorCode, JmpError, NotFoundError
from app.llm.pricing import cost_usd, usd_to_inr
from app.llm.schemas import NarrativeV1
from app.llm.service import AttemptRecord, generate_narrative
from app.observability.logging import get_logger
from app.providers.registry import get_providers
from app.schemas.journeys import JourneyRequest
from app.services import hazard_library
from app.services.pipeline import JourneyOptions, build_report, compute_facts, render_document
from app.settings import settings
from app.storage import date_prefix, get_storage
from app.versions import current_versions

log = get_logger("jmp.jobs")
STAGE_QUEUE = {"analyse": "pipeline", "narrate": "llm", "render": "render"}


class RetryStage(Exception):
    def __init__(self, error: JmpError, countdown: float) -> None:
        super().__init__(error.message)
        self.error = error
        self.countdown = countdown


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------------------------ job creation
def create_journey_job(db: Session, req: JourneyRequest, *, source: str = "individual",
                       bulk_item_id: uuid.UUID | None = None, actor_id: uuid.UUID | None = None) -> GenerationJob:
    if req.idempotency_key and source == "individual":
        since = utcnow() - timedelta(hours=24)
        existing = db.scalar(select(Journey).where(Journey.idempotency_key == req.idempotency_key,
                                                   Journey.created_at >= since))
        if existing is not None:
            job = db.scalar(select(GenerationJob).where(GenerationJob.journey_id == existing.id)
                            .order_by(GenerationJob.created_at.desc()))
            if job is not None:
                return job
    journey = Journey(
        created_by_id=actor_id, source=source, bulk_job_item_id=bulk_item_id,
        input=req.model_dump(mode="json", exclude_none=True), input_hash=req.input_hash(),
        idempotency_key=req.idempotency_key, vehicle_type=req.vehicle_type or "4W", travel_date=req.travel_date,
        depart_time=req.depart_time, manager_name=req.manager_name, emergency_contact=req.emergency_contact,
        nearest_hospital=req.nearest_hospital, nearest_police=req.nearest_police,
        is_round_trip=req.is_round_trip_text, status=JobStatus.queued)
    locs = req.locations
    for i, text in enumerate(locs):
        journey.stops.append(JourneyStop(seq=i, kind="start" if i == 0 else ("end" if i == len(locs) - 1 else "stop"),
                                         raw_text=text))
    db.add(journey)
    db.flush()
    job = GenerationJob(kind="bulk_item" if bulk_item_id else "individual", journey_id=journey.id,
                        bulk_job_item_id=bulk_item_id, status=JobStatus.queued, stage=JobStage.queued)
    db.add(job)
    db.flush()
    return job


# --------------------------------------------------------------------------------------- dispatch
_executor: ThreadPoolExecutor | None = None


def _pool() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=max(2, settings().llm_concurrency), thread_name_prefix="jmp-job")
    return _executor


def dispatch(stage: str, job_id: uuid.UUID | str, *, countdown: float = 0) -> None:
    mode = settings().job_execution
    if mode == "celery":
        from app.workers.celery_app import celery

        celery.send_task("jmp.stage", args=[stage, str(job_id), 1], queue=STAGE_QUEUE[stage], countdown=countdown)
    elif mode == "thread":
        _pool().submit(run_chain, stage, str(job_id))
    else:
        run_chain(stage, str(job_id))


def run_chain(stage: str | None, job_id: str) -> None:
    """thread/sync execution: run stages in order with local retries (Celery does this with task retries)."""
    attempt = 1
    while stage and stage != "await_batch":
        try:
            nxt = run_stage(stage, job_id, attempt)
        except RetryStage as r:
            attempt += 1
            time.sleep(0 if settings().job_execution == "sync" else min(r.countdown, 30))
            continue
        stage, attempt = nxt, 1


def run_stage(stage: str, job_id: str, attempt: int = 1) -> str | None:
    fn = {"analyse": stage_analyse, "narrate": stage_narrate, "render": stage_render}[stage]
    s = settings()
    try:
        return fn(uuid.UUID(job_id), attempt)
    except JmpError as exc:
        err = exc
    except Exception as exc:  # unexpected: record, retry once
        log.exception("stage_unexpected_error", stage=stage, job_id=job_id)
        err = JmpError(f"{type(exc).__name__}: {exc}"[:500], code=ErrorCode.INTERNAL_ERROR)
    max_attempts = 2 if err.code == ErrorCode.INTERNAL_ERROR else s.stage_max_retries
    if err.retryable and attempt < max_attempts:
        countdown = s.stage_retry_base_s * (2 ** (attempt - 1))
        log.warning("stage_retry", stage=stage, job_id=job_id, attempt=attempt, code=err.code, countdown=countdown)
        _touch(uuid.UUID(job_id), attempt=attempt)
        raise RetryStage(err, countdown)
    fail_job(uuid.UUID(job_id), err.code, err.message, err.details)
    return None


# ------------------------------------------------------------------------------------ job state
def _touch(job_id: uuid.UUID, **fields: Any) -> None:
    with session_scope() as db:
        db.execute(update(GenerationJob).where(GenerationJob.id == job_id).values(updated_at=utcnow(), **fields))


def _begin(db: Session, job_id: uuid.UUID, stage: str, attempt: int) -> GenerationJob | None:
    job = db.get(GenerationJob, job_id)
    if job is None:
        raise NotFoundError(f"job {job_id} not found")
    if job.status in (JobStatus.cancelled, JobStatus.completed):
        return None
    job.status = JobStatus.processing
    job.stage = stage
    job.attempt = attempt
    job.started_at = job.started_at or utcnow()
    job.error_code = job.error_message = None
    journey = db.get(Journey, job.journey_id)
    if journey is not None:
        journey.status = JobStatus.processing
    return job


def fail_job(job_id: uuid.UUID, code: str, message: str, details: Any = None) -> None:
    item_id = None
    with session_scope() as db:
        job = db.get(GenerationJob, job_id)
        if job is None:
            return
        job.status = JobStatus.failed
        job.error_code = str(code)
        msg = message
        if details:
            msg += " | " + (str(details)[:1500])
        job.error_message = msg[:4000]
        job.finished_at = utcnow()
        journey = db.get(Journey, job.journey_id)
        if journey is not None:
            journey.status = JobStatus.failed
        item_id = job.bulk_job_item_id
    log.warning("job_failed", job_id=str(job_id), code=str(code), message=message[:300])
    if item_id is not None:
        from app.services import bulk

        bulk.item_finished(item_id, ok=False, error_code=str(code), error_message=message)


# ------------------------------------------------------------------------------------------ stages
def _options(j: Journey) -> JourneyOptions:
    return JourneyOptions(vehicle_type=j.vehicle_type or "4W", vehicle_type_specified=bool(j.input.get("vehicle_type")),
                          travel_date=j.travel_date, depart_time=j.depart_time, manager_name=j.manager_name,
                          emergency_contact=j.emergency_contact, nearest_hospital=j.nearest_hospital,
                          nearest_police=j.nearest_police)


def _assign_code(db: Session, journey: Journey, jf: JourneyFacts) -> str:
    if journey.journey_code:
        return journey.journey_code
    info = rules_config.state_info(jf.route.states[0] if jf.route.states else None)
    code = str(info.get("code", "IN"))
    q = select(JourneyCodeSequence).where(JourneyCodeSequence.state_code == code)
    if not settings().is_sqlite:
        q = q.with_for_update()
    seq = db.scalar(q)
    if seq is None:
        seq = JourneyCodeSequence(state_code=code, last_value=0)
        db.add(seq)
    seq.last_value += 1
    prefix = rules_config.emergency_static()["brand"]["id_prefix"]
    journey.journey_code = f"{prefix}-{code}-{seq.last_value:03d}"
    db.flush()
    return journey.journey_code


def stage_analyse(job_id: uuid.UUID, attempt: int = 1) -> str | None:
    with session_scope() as db:
        job = _begin(db, job_id, JobStage.analyse, attempt)
        if job is None:
            return None
        journey = db.get(Journey, job.journey_id)
        assert journey is not None
        inputs = [s.raw_text for s in sorted(journey.stops, key=lambda s: s.seq)]
        opts = _options(journey)
        library = hazard_library.load_active_library(db)
    providers = get_providers()
    with session_scope() as db:  # provider cache writes in their own transaction
        jf = compute_facts(inputs, opts, providers, library, db)
    with session_scope() as db:
        job = db.get(GenerationJob, job_id)
        journey = db.get(Journey, job.journey_id)  # type: ignore[union-attr]
        assert job is not None and journey is not None
        stops = sorted(journey.stops, key=lambda s: s.seq)
        for st, w in zip(stops, jf.route.waypoints, strict=True):
            st.geocoded_name, st.lat, st.lng = w.name, w.lat, w.lng
            st.admin_area = {"state": w.state, "district": w.district, "locality": w.locality}
            st.place_types, st.geocode_confidence, st.provider = w.place_types, w.geocode_confidence, w.geocode_provider
        _assign_code(db, journey, jf)
        db.execute(delete(RouteAnalysis).where(RouteAnalysis.journey_id == journey.id))
        r = jf.route
        db.add(RouteAnalysis(
            journey_id=journey.id, provider=r.providers.get("route", ""), distance_m=int(r.distance_km * 1000),
            duration_s=int(r.duration_min * 60), distance_range={"km": list(r.distance_range_km)},
            duration_range={"min": list(r.duration_range_min)}, geometry=[list(p) for p in r.geometry],
            legs=[leg.model_dump() for leg in r.legs], segments=[s.model_dump() for s in r.segments],
            road_type_split=[t.model_dump() for t in r.road_types], exposures=r.exposures.model_dump(),
            alternatives=[a.model_dump(mode="json") for a in r.alternatives],
            features=[f.model_dump() for f in r.features], elevation_summary=r.elevation,
            facts=r.model_dump(mode="json", exclude={"geometry", "features"}),
            rules_version=str(rules_config.hazard_rules()["version"])))
        db.execute(delete(JourneyHazard).where(JourneyHazard.journey_id == journey.id))
        by_code = {h.code: h for h in jf.hazards}
        for hz in library.hazards:
            m = by_code.get(hz.code)
            db.add(JourneyHazard(
                journey_id=journey.id, hazard_id=uuid.UUID(hz.id), applicable=m is not None,
                evidence=m.evidence if m else None, display_band=m.display_band if m else None,
                rank=m.rank if m else None, locations=[loc.model_dump() for loc in m.locations] if m else [],
                evidence_detail={"basis": m.basis, "route_wide": m.route_wide} if m else {}))
        db.execute(delete(JourneyScore).where(JourneyScore.journey_id == journey.id))
        scoring_version = str(rules_config.scoring_rules()["version"])
        for d in jf.scores.dimensions:
            db.add(JourneyScore(journey_id=journey.id, dimension=d.id, weight=d.weight, score=d.score,
                                contribution=d.contribution, inputs=d.inputs, scoring_version=scoring_version))
        job.report_facts = jf.model_dump(mode="json")
        job.stage = JobStage.narrative
        item_id = job.bulk_job_item_id
    if item_id is not None:
        from app.services import bulk

        if bulk.item_uses_batch(item_id):
            with session_scope() as db:
                db.execute(update(GenerationJob).where(GenerationJob.id == job_id)
                           .values(stage=JobStage.awaiting_batch))
            bulk.on_item_analysed(item_id)
            return "await_batch"
    return "narrate"


def load_library_for(db: Session, version: str) -> hazard_library.HazardLibrary:
    lib = hazard_library.load_active_library(db)
    if lib.version != version:
        raise JmpError(f"Hazard library changed during generation ({version} → {lib.version}); re-run the job",
                       code=ErrorCode.HAZARD_LIBRARY_MISSING)
    return lib


def record_usage(job_id: uuid.UUID, journey_id: uuid.UUID, bulk_job_id: uuid.UUID | None, rec: AttemptRecord,
                 *, batch: bool = False) -> None:
    from app.llm.schemas import SCHEMA_VERSION
    from app.versions import PROMPT_VERSION

    call = rec.call
    model = call.model if call else settings().anthropic_model
    usd = cost_usd(model, call.usage, batch=batch) if call else Decimal("0")
    with session_scope() as db:
        db.add(GenerationUsage(
            generation_job_id=job_id, journey_id=journey_id, bulk_job_id=bulk_job_id,
            provider="mock" if model == "mock" else "anthropic", model=model,
            service_tier="batch" if batch else "standard", prompt_version=PROMPT_VERSION,
            schema_version=SCHEMA_VERSION, static_prefix_sha256=rec.prefix_sha256,
            request_id=call.request_id if call else None, attempt=rec.attempt,
            input_tokens=call.usage.input_tokens if call else 0,
            cache_creation_input_tokens=call.usage.cache_creation if call else 0,
            cache_creation_5m_tokens=call.usage.cache_creation_5m if call else 0,
            cache_creation_1h_tokens=call.usage.cache_creation_1h if call else 0,
            cache_read_input_tokens=call.usage.cache_read if call else 0,
            output_tokens=call.usage.output_tokens if call else 0,
            estimated_cost_usd=usd, estimated_cost_inr=usd_to_inr(usd, settings().usd_to_inr),
            duration_ms=call.duration_ms if call else 0, stop_reason=call.stop_reason if call else None,
            outcome=rec.outcome, validation_errors=rec.errors[:20] or None))


def _bulk_job_id_for(db: Session, job: GenerationJob) -> uuid.UUID | None:
    if job.bulk_job_item_id is None:
        return None
    from app.db.models import BulkJobItem

    item = db.get(BulkJobItem, job.bulk_job_item_id)
    return item.bulk_job_id if item else None


def stage_narrate(job_id: uuid.UUID, attempt: int = 1) -> str | None:
    with session_scope() as db:
        job = _begin(db, job_id, JobStage.narrative, attempt)
        if job is None:
            return None
        if job.narrative_json:  # already produced (e.g. by the batch path) — idempotent
            return "render"
        jf = JourneyFacts.model_validate(job.report_facts)
        library = load_library_for(db, jf.hazard_library_version)
        journey_id, bulk_job_id = job.journey_id, _bulk_job_id_for(db, job)

    def sink(rec: AttemptRecord) -> None:
        record_usage(job_id, journey_id, bulk_job_id, rec)

    narrative, source, model = generate_narrative(jf, library, on_attempt=sink)
    store_narrative(job_id, narrative, source, model)
    return "render"


def store_narrative(job_id: uuid.UUID, narrative: NarrativeV1, source: str, model: str) -> None:
    with session_scope() as db:
        job = db.get(GenerationJob, job_id)
        assert job is not None
        job.narrative_json = {"narrative": narrative.model_dump(mode="json"), "source": source, "model": model}
        job.stage = JobStage.render
        notes = {h.hazard_code: h.route_context for h in narrative.hazard_notes}
        rows = db.execute(select(JourneyHazard, Hazard.code).join(Hazard, Hazard.id == JourneyHazard.hazard_id)
                          .where(JourneyHazard.journey_id == job.journey_id)).all()
        for jh, code in rows:
            if code in notes:
                jh.narrative_context = notes[code]


def stage_render(job_id: uuid.UUID, attempt: int = 1) -> str | None:
    with session_scope() as db:
        job = _begin(db, job_id, JobStage.render, attempt)
        if job is None:
            return None
        if job.document_id:  # already rendered (redelivery) — idempotent
            return None
        journey = db.get(Journey, job.journey_id)
        assert journey is not None
        jf = JourneyFacts.model_validate(job.report_facts)
        nj = job.narrative_json or {}
        narrative = NarrativeV1.model_validate(nj["narrative"])
        source, model = nj.get("source", "anthropic"), nj.get("model", settings().anthropic_model)
        code = journey.journey_code or "DAN-JMP-XX-000"
        journey_id, item_id = journey.id, job.bulk_job_item_id
    versions = current_versions(jf.hazard_library_version)
    report = build_report(jf, narrative, journey_code=code, versions=versions, narrative_source=source, model=model,
                          pointer_count=settings().hazard_pointer_count)
    t0 = time.monotonic()
    html, pdf = render_document(report)
    doc_id = uuid.uuid4()
    now = utcnow()
    storage = get_storage()
    base = f"documents/{date_prefix(now)}/{code}_{doc_id.hex[:8]}"
    html_key = storage.put_bytes(base + ".html", html.encode("utf-8"), "text/html; charset=utf-8")
    pdf_key = storage.put_bytes(base + ".pdf", pdf.pdf, "application/pdf")
    total_ms = int((time.monotonic() - t0) * 1000)
    r = report.route
    with session_scope() as db:
        db.add(JmpDocument(
            id=doc_id, journey_id=journey_id, generation_job_id=job_id, document_code=code, status="completed",
            route_name=r.route_name, region=r.region_label, risk_level=report.scores.risk_level,
            decision=report.scores.decision, journey_score=report.scores.total,
            search_text=" ".join([code, r.route_name, r.region_label, *[w.input_text for w in r.waypoints]]).lower(),
            report_json=report.model_dump(mode="json"), narrative_json=narrative.model_dump(mode="json"),
            template_version=versions.template_version, hazard_library_version=versions.hazard_library_version,
            prompt_version=versions.prompt_version, schema_version=versions.schema_version,
            scoring_version=versions.scoring_version, rules_version=versions.rules_version,
            app_version=versions.app_version, model=model, narrative_source=source, providers=r.providers,
            html_path=html_key, pdf_path=pdf_key, pdf_sha256=hashlib.sha256(pdf.pdf).hexdigest(),
            pdf_bytes=len(pdf.pdf), page_count=pdf.page_count, render_ms=total_ms - pdf.render_ms,
            pdf_ms=pdf.render_ms))
        job = db.get(GenerationJob, job_id)
        assert job is not None
        job.document_id = doc_id
        job.status = JobStatus.completed
        job.stage = JobStage.done
        job.finished_at = utcnow()
        journey = db.get(Journey, journey_id)
        if journey is not None:
            journey.status = JobStatus.completed
    log.info("job_completed", job_id=str(job_id), document_id=str(doc_id), code=code, pdf_ms=pdf.render_ms,
             pages=pdf.page_count)
    if item_id is not None:
        from app.services import bulk

        bulk.item_finished(item_id, ok=True, document_id=doc_id)
    return None


# ------------------------------------------------------------------------------------- operations
def retry_job(job_id: uuid.UUID) -> GenerationJob:
    with session_scope() as db:
        job = db.get(GenerationJob, job_id)
        if job is None:
            raise NotFoundError("job not found")
        if job.status != JobStatus.failed:
            raise JmpError("Only failed jobs can be retried", code=ErrorCode.CONFLICT, http_status=409)
        stage = "analyse" if not job.report_facts else ("narrate" if not job.narrative_json else "render")
        job.status, job.error_code, job.error_message, job.finished_at = JobStatus.queued, None, None, None
        jid = job.id
    dispatch(stage, jid)
    with session_scope() as db:
        return db.get(GenerationJob, jid)  # type: ignore[return-value]


def cancel_job(job_id: uuid.UUID) -> None:
    with session_scope() as db:
        job = db.get(GenerationJob, job_id)
        if job is None:
            raise NotFoundError("job not found")
        if job.status in (JobStatus.completed, JobStatus.failed, JobStatus.cancelled):
            raise JmpError(f"Job is already {job.status}", code=ErrorCode.CONFLICT, http_status=409)
        job.status = JobStatus.cancelled
        job.finished_at = utcnow()


def reap_stuck_jobs() -> int:
    """Re-dispatch jobs stuck in 'processing' past the timeout (worker crash). Returns the count."""
    cutoff = utcnow() - timedelta(minutes=settings().stuck_job_timeout_min)
    todo: list[tuple[str, uuid.UUID]] = []
    with session_scope() as db:
        for job in db.scalars(select(GenerationJob).where(GenerationJob.status == JobStatus.processing,
                                                          GenerationJob.updated_at < cutoff)):
            if job.stage == JobStage.awaiting_batch:
                continue
            stage = "analyse" if not job.report_facts else ("narrate" if not job.narrative_json else "render")
            job.updated_at = utcnow()
            todo.append((stage, job.id))
    for stage, jid in todo:
        dispatch(stage, jid)
    return len(todo)

