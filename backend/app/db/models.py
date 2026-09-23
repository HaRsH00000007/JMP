"""PostgreSQL schema (architecture.md §4). Enum-like columns are VARCHAR + Python enums (portable, migration-friendly)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, JsonType, TimestampMixin, utcnow, uuid_pk


# ------------------------------------------------------------------------------------------ enums
class UserRole(StrEnum):
    admin = "admin"
    preparer = "preparer"
    reviewer = "reviewer"
    approver = "approver"
    viewer = "viewer"


class JobStatus(StrEnum):
    queued = "queued"
    processing = "processing"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class JobStage(StrEnum):
    queued = "queued"
    geocode = "geocode"
    route = "route"
    features = "features"
    analyse = "analyse"
    hazards = "hazards"
    scoring = "scoring"
    emergency = "emergency"
    narrative = "narrative"
    awaiting_batch = "awaiting_batch"
    assemble = "assemble"
    render = "render"
    pdf = "pdf"
    done = "done"


class BulkStatus(StrEnum):
    queued = "queued"
    processing = "processing"
    finalizing = "finalizing"
    completed = "completed"
    completed_with_errors = "completed_with_errors"
    failed = "failed"
    cancelled = "cancelled"


class BulkItemStatus(StrEnum):
    invalid = "invalid"
    queued = "queued"
    processing = "processing"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class Evidence(StrEnum):
    DETECTED = "DETECTED"
    INFERRED = "INFERRED"
    VERIFY = "VERIFY"


# ------------------------------------------------------------------------------------------ users
class User(TimestampMixin, Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String(320), unique=True)
    full_name: Mapped[str | None] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20), default=UserRole.preparer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    auth_subject: Mapped[str | None] = mapped_column(String(255), unique=True)


# ------------------------------------------------------------------------------------ hazard library
class HazardLibraryVersion(Base):
    __tablename__ = "hazard_library_versions"
    id: Mapped[uuid.UUID] = uuid_pk()
    version: Mapped[str] = mapped_column(String(20), unique=True)
    source_filename: Mapped[str] = mapped_column(String(255))
    source_sha256: Mapped[str] = mapped_column(String(64))
    risk_matrix_image_sha256: Mapped[str | None] = mapped_column(String(64))
    header_fields: Mapped[list[Any]] = mapped_column(JsonType, default=list)  # Excel "Header" sheet particulars
    hospital_network_url: Mapped[str | None] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    hazards: Mapped[list[Hazard]] = relationship(back_populates="library_version", order_by="Hazard.sr_no")
    matrix_cells: Mapped[list[RiskMatrixCell]] = relationship(back_populates="library_version")


class Hazard(Base):
    __tablename__ = "hazards"
    __table_args__ = (UniqueConstraint("library_version_id", "code"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    library_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hazard_library_versions.id"), index=True)
    code: Mapped[str] = mapped_column(String(10))  # HZ-01 … HZ-25
    sr_no: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(200))  # verbatim
    severity: Mapped[int] = mapped_column(Integer)  # verbatim
    probability: Mapped[str] = mapped_column(String(1))  # verbatim
    rpn_code: Mapped[str] = mapped_column(String(4))  # verbatim (asserted = probability+severity)
    severity_band: Mapped[str] = mapped_column(String(10))  # derived: D-01 severity rule
    matrix_zone: Mapped[str] = mapped_column(String(10))  # derived: Danone matrix lookup
    control_2w_raw: Mapped[str] = mapped_column(Text)  # verbatim cell text
    control_4w_raw: Mapped[str] = mapped_column(Text)
    control_2w_items: Mapped[list[Any]] = mapped_column(JsonType)  # parsed bullets (text unchanged)
    control_4w_items: Mapped[list[Any]] = mapped_column(JsonType)
    control_short_2w: Mapped[str | None] = mapped_column(Text)  # EHS-approved one-liner (D-07), NULL until approved
    control_short_4w: Mapped[str | None] = mapped_column(Text)
    detection_profile: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)

    library_version: Mapped[HazardLibraryVersion] = relationship(back_populates="hazards")


class RiskMatrixCell(Base):
    __tablename__ = "risk_matrix_cells"
    library_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hazard_library_versions.id"), primary_key=True)
    probability: Mapped[str] = mapped_column(String(1), primary_key=True)
    severity: Mapped[int] = mapped_column(Integer, primary_key=True)
    zone: Mapped[str] = mapped_column(String(10))

    library_version: Mapped[HazardLibraryVersion] = relationship(back_populates="matrix_cells")


# ------------------------------------------------------------------------------------------ journeys
class JourneyCodeSequence(Base):
    """Per-state sequence for DAN-JMP-{STATE}-{NNN} (D-12)."""

    __tablename__ = "journey_code_sequences"
    state_code: Mapped[str] = mapped_column(String(8), primary_key=True)
    last_value: Mapped[int] = mapped_column(Integer, default=0)


class Journey(TimestampMixin, Base):
    __tablename__ = "journeys"
    id: Mapped[uuid.UUID] = uuid_pk()
    journey_code: Mapped[str | None] = mapped_column(String(40), unique=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    source: Mapped[str] = mapped_column(String(20), default="individual")  # individual | bulk
    bulk_job_item_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    input: Mapped[dict[str, Any]] = mapped_column(JsonType)  # exact request
    input_hash: Mapped[str] = mapped_column(String(64), index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), index=True)
    vehicle_type: Mapped[str] = mapped_column(String(4), default="4W")
    travel_date: Mapped[date | None] = mapped_column(Date)
    depart_time: Mapped[str | None] = mapped_column(String(5))
    manager_name: Mapped[str | None] = mapped_column(String(200))
    emergency_contact: Mapped[str | None] = mapped_column(String(200))
    nearest_hospital: Mapped[str | None] = mapped_column(String(300))
    nearest_police: Mapped[str | None] = mapped_column(String(300))
    is_round_trip: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default=JobStatus.queued)

    stops: Mapped[list[JourneyStop]] = relationship(back_populates="journey", order_by="JourneyStop.seq",
                                                    cascade="all, delete-orphan")


class JourneyStop(Base):
    __tablename__ = "journey_stops"
    __table_args__ = (UniqueConstraint("journey_id", "seq"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    journey_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("journeys.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(10))  # start | stop | end
    raw_text: Mapped[str] = mapped_column(String(300))
    geocoded_name: Mapped[str | None] = mapped_column(String(500))
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    admin_area: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    place_types: Mapped[list[Any] | None] = mapped_column(JsonType)
    geocode_confidence: Mapped[float | None] = mapped_column(Float)
    provider: Mapped[str | None] = mapped_column(String(30))

    journey: Mapped[Journey] = relationship(back_populates="stops")


class RouteCache(Base):
    __tablename__ = "route_cache"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(30))
    kind: Mapped[str] = mapped_column(String(20))  # geocode | route | features | elevation | places
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RouteAnalysis(Base):
    __tablename__ = "route_analysis"
    id: Mapped[uuid.UUID] = uuid_pk()
    journey_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("journeys.id", ondelete="CASCADE"), unique=True)
    provider: Mapped[str] = mapped_column(String(30))
    provider_request_id: Mapped[str | None] = mapped_column(String(120))
    distance_m: Mapped[int] = mapped_column(Integer)
    duration_s: Mapped[int] = mapped_column(Integer)
    distance_range: Mapped[dict[str, Any]] = mapped_column(JsonType)
    duration_range: Mapped[dict[str, Any]] = mapped_column(JsonType)
    geometry: Mapped[list[Any]] = mapped_column(JsonType)  # [[lat, lng], …] as returned by the provider
    legs: Mapped[list[Any]] = mapped_column(JsonType)
    segments: Mapped[list[Any]] = mapped_column(JsonType)
    road_type_split: Mapped[list[Any]] = mapped_column(JsonType)
    exposures: Mapped[dict[str, Any]] = mapped_column(JsonType)
    alternatives: Mapped[list[Any]] = mapped_column(JsonType)
    features: Mapped[list[Any]] = mapped_column(JsonType)
    elevation_summary: Mapped[dict[str, Any]] = mapped_column(JsonType)
    facts: Mapped[dict[str, Any]] = mapped_column(JsonType)  # full deterministic analysis snapshot
    rules_version: Mapped[str] = mapped_column(String(20))
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class JourneyHazard(Base):
    __tablename__ = "journey_hazards"
    __table_args__ = (UniqueConstraint("journey_id", "hazard_id"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    journey_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("journeys.id", ondelete="CASCADE"), index=True)
    hazard_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hazards.id"))  # pins the library version
    applicable: Mapped[bool] = mapped_column(Boolean)
    evidence: Mapped[str | None] = mapped_column(String(10))
    display_band: Mapped[str | None] = mapped_column(String(10))
    rank: Mapped[int | None] = mapped_column(Integer)
    locations: Mapped[list[Any]] = mapped_column(JsonType, default=list)
    evidence_detail: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    narrative_context: Mapped[str | None] = mapped_column(Text)


class JourneyScore(Base):
    __tablename__ = "journey_scores"
    __table_args__ = (UniqueConstraint("journey_id", "dimension"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    journey_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("journeys.id", ondelete="CASCADE"), index=True)
    dimension: Mapped[str] = mapped_column(String(40))
    weight: Mapped[float] = mapped_column(Float)
    score: Mapped[float] = mapped_column(Float)
    contribution: Mapped[float] = mapped_column(Float)
    inputs: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    scoring_version: Mapped[str] = mapped_column(String(20))


# -------------------------------------------------------------------------------------- generation
class GenerationJob(TimestampMixin, Base):
    __tablename__ = "generation_jobs"
    __table_args__ = (Index("ix_generation_jobs_status_queued", "status", "queued_at"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[str] = mapped_column(String(20))  # individual | bulk_item | rerender
    journey_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("journeys.id", ondelete="CASCADE"), index=True)
    bulk_job_item_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    status: Mapped[str] = mapped_column(String(20), default=JobStatus.queued)
    stage: Mapped[str] = mapped_column(String(20), default=JobStage.queued)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(40))
    error_message: Mapped[str | None] = mapped_column(Text)
    narrative_json: Mapped[dict[str, Any] | None] = mapped_column(JsonType)  # validated LLM output (stage 9)
    report_facts: Mapped[dict[str, Any] | None] = mapped_column(JsonType)  # deterministic facts (stages 5–8)
    document_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    celery_task_id: Mapped[str | None] = mapped_column(String(64))


class GenerationUsage(Base):
    __tablename__ = "generation_usage"
    id: Mapped[uuid.UUID] = uuid_pk()
    generation_job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("generation_jobs.id", ondelete="SET NULL"),
                                                                index=True)
    journey_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    bulk_job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    provider: Mapped[str] = mapped_column(String(20), default="anthropic")
    model: Mapped[str] = mapped_column(String(60))
    service_tier: Mapped[str] = mapped_column(String(20), default="standard")  # standard | batch
    prompt_version: Mapped[str] = mapped_column(String(20))
    schema_version: Mapped[str] = mapped_column(String(20))
    static_prefix_sha256: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(120))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_creation_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_creation_5m_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_creation_1h_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal("0"))
    estimated_cost_inr: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=Decimal("0"))
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    stop_reason: Mapped[str | None] = mapped_column(String(30))
    outcome: Mapped[str] = mapped_column(String(30))  # ok | invalid_json | schema_error | validation_error | refusal | api_error
    validation_errors: Mapped[list[Any] | None] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class JmpDocument(TimestampMixin, Base):
    __tablename__ = "jmp_documents"
    id: Mapped[uuid.UUID] = uuid_pk()
    journey_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("journeys.id", ondelete="CASCADE"), index=True)
    generation_job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    document_code: Mapped[str] = mapped_column(String(60), index=True)
    status: Mapped[str] = mapped_column(String(20), default="completed")
    route_name: Mapped[str | None] = mapped_column(String(200))
    region: Mapped[str | None] = mapped_column(String(120), index=True)
    risk_level: Mapped[str | None] = mapped_column(String(20), index=True)
    decision: Mapped[str | None] = mapped_column(String(40))
    journey_score: Mapped[int | None] = mapped_column(Integer)
    search_text: Mapped[str | None] = mapped_column(Text)
    report_json: Mapped[dict[str, Any]] = mapped_column(JsonType)  # exact object rendered
    narrative_json: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    template_version: Mapped[str] = mapped_column(String(20))
    hazard_library_version: Mapped[str] = mapped_column(String(20))
    prompt_version: Mapped[str] = mapped_column(String(20))
    schema_version: Mapped[str] = mapped_column(String(20))
    scoring_version: Mapped[str] = mapped_column(String(20))
    rules_version: Mapped[str] = mapped_column(String(20))
    app_version: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(60))
    narrative_source: Mapped[str] = mapped_column(String(20), default="anthropic")  # anthropic | mock
    providers: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    html_path: Mapped[str | None] = mapped_column(String(500))
    pdf_path: Mapped[str | None] = mapped_column(String(500))
    pdf_sha256: Mapped[str | None] = mapped_column(String(64))
    pdf_bytes: Mapped[int | None] = mapped_column(Integer)
    page_count: Mapped[int | None] = mapped_column(Integer)
    render_ms: Mapped[int | None] = mapped_column(Integer)
    pdf_ms: Mapped[int | None] = mapped_column(Integer)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --------------------------------------------------------------------------------------------- bulk
class BulkJob(TimestampMixin, Base):
    __tablename__ = "bulk_jobs"
    id: Mapped[uuid.UUID] = uuid_pk()
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    original_filename: Mapped[str] = mapped_column(String(255))
    csv_path: Mapped[str] = mapped_column(String(500))
    csv_sha256: Mapped[str] = mapped_column(String(64), index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(30), default=BulkStatus.queued)
    llm_mode: Mapped[str] = mapped_column(String(10), default="realtime")
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    valid_rows: Mapped[int] = mapped_column(Integer, default=0)
    invalid_rows: Mapped[int] = mapped_column(Integer, default=0)
    analysed: Mapped[int] = mapped_column(Integer, default=0)  # batch mode: items ready for the LLM batch
    processed: Mapped[int] = mapped_column(Integer, default=0)
    succeeded: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    anthropic_batch_id: Mapped[str | None] = mapped_column(String(120))
    zip_path: Mapped[str | None] = mapped_column(String(500))
    manifest_csv_path: Mapped[str | None] = mapped_column(String(500))
    manifest_xlsx_path: Mapped[str | None] = mapped_column(String(500))
    summary_json_path: Mapped[str | None] = mapped_column(String(500))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BulkJobItem(TimestampMixin, Base):
    __tablename__ = "bulk_job_items"
    __table_args__ = (
        UniqueConstraint("bulk_job_id", "row_number"),
        Index("ix_bulk_job_items_job_status", "bulk_job_id", "status"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    bulk_job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bulk_jobs.id", ondelete="CASCADE"))
    row_number: Mapped[int] = mapped_column(Integer)
    route_ref: Mapped[str] = mapped_column(String(100))
    raw_row: Mapped[dict[str, Any]] = mapped_column(JsonType)
    normalized_input: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    validation_errors: Mapped[list[Any] | None] = mapped_column(JsonType)
    status: Mapped[str] = mapped_column(String(20), default=BulkItemStatus.queued)
    journey_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    generation_job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    document_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    error_code: Mapped[str | None] = mapped_column(String(40))
    error_message: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    counted: Mapped[bool] = mapped_column(Boolean, default=False)  # guards double-counting on redelivery
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UploadedCsv(Base):
    """A validated-but-not-yet-started upload (POST /bulk-jobs/validate), kept 24 h."""

    __tablename__ = "uploaded_csvs"
    id: Mapped[uuid.UUID] = uuid_pk()
    original_filename: Mapped[str] = mapped_column(String(255))
    csv_path: Mapped[str] = mapped_column(String(500))
    csv_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[uuid.UUID] = uuid_pk()
    actor: Mapped[str | None] = mapped_column(String(320))
    entity: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(40))
    detail: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
