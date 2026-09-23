from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import Response
from sqlalchemy import func, or_, select

from app.db.models import AuditEvent, BulkJobItem, GenerationUsage, JmpDocument
from app.db.session import session_scope
from app.errors import NotFoundError
from app.services.pipeline import render_document
from app.services.report_assembler import ReportModel
from app.settings import settings
from app.storage import date_prefix, get_storage

router = APIRouter()


def doc_summary(d: JmpDocument) -> dict[str, Any]:
    return {
        "document_id": str(d.id), "document_code": d.document_code, "journey_id": str(d.journey_id),
        "route_name": d.route_name, "region": d.region, "risk_level": d.risk_level, "decision": d.decision,
        "journey_score": d.journey_score, "page_count": d.page_count, "pdf_bytes": d.pdf_bytes,
        "narrative_source": d.narrative_source, "model": d.model, "created_at": d.created_at.isoformat(),
        "demo_data": bool((d.report_json or {}).get("meta", {}).get("demo_data")),
    }


def _get(db: Any, doc_id: uuid.UUID) -> JmpDocument:
    d = db.get(JmpDocument, doc_id)
    if d is None or d.deleted_at is not None:
        raise NotFoundError("Document not found")
    return d


@router.get("/documents")
def list_documents(q: str | None = None, risk: str | None = None, region: str | None = None,
                   bulk_job_id: uuid.UUID | None = None, date_from: date | None = Query(None, alias="from"),
                   date_to: date | None = Query(None, alias="to"), page: int = Query(1, ge=1),
                   size: int = Query(25, ge=1, le=200)) -> dict[str, Any]:
    with session_scope() as db:
        stmt = select(JmpDocument).where(JmpDocument.deleted_at.is_(None))
        if q:
            like = f"%{q.lower()}%"
            stmt = stmt.where(or_(JmpDocument.search_text.like(like), JmpDocument.document_code.ilike(like)))
        if risk:
            stmt = stmt.where(JmpDocument.risk_level == risk.upper())
        if region:
            stmt = stmt.where(JmpDocument.region.ilike(f"%{region}%"))
        if bulk_job_id:
            stmt = stmt.join(BulkJobItem, BulkJobItem.document_id == JmpDocument.id).where(
                BulkJobItem.bulk_job_id == bulk_job_id)
        if date_from:
            stmt = stmt.where(JmpDocument.created_at >= datetime.combine(date_from, datetime.min.time(), timezone.utc))
        if date_to:
            stmt = stmt.where(JmpDocument.created_at <= datetime.combine(date_to, datetime.max.time(), timezone.utc))
        total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = db.scalars(stmt.order_by(JmpDocument.created_at.desc()).offset((page - 1) * size).limit(size)).all()
        return {"total": total, "page": page, "size": size, "items": [doc_summary(d) for d in rows]}


@router.get("/documents/{doc_id}")
def get_document(doc_id: uuid.UUID) -> dict[str, Any]:
    with session_scope() as db:
        d = _get(db, doc_id)
        usage = db.execute(select(
            func.count(), func.coalesce(func.sum(GenerationUsage.input_tokens), 0),
            func.coalesce(func.sum(GenerationUsage.cache_read_input_tokens), 0),
            func.coalesce(func.sum(GenerationUsage.cache_creation_input_tokens), 0),
            func.coalesce(func.sum(GenerationUsage.output_tokens), 0),
            func.coalesce(func.sum(GenerationUsage.estimated_cost_usd), 0),
            func.coalesce(func.sum(GenerationUsage.estimated_cost_inr), 0),
        ).where(GenerationUsage.generation_job_id == d.generation_job_id)).one()
        return {
            **doc_summary(d),
            "versions": {"template": d.template_version, "hazard_library": d.hazard_library_version,
                         "prompt": d.prompt_version, "schema": d.schema_version, "scoring": d.scoring_version,
                         "rules": d.rules_version, "app": d.app_version},
            "providers": d.providers, "pdf_sha256": d.pdf_sha256, "render_ms": d.render_ms, "pdf_ms": d.pdf_ms,
            "usage": {"calls": usage[0], "input_tokens": int(usage[1]), "cache_read_input_tokens": int(usage[2]),
                      "cache_creation_input_tokens": int(usage[3]), "output_tokens": int(usage[4]),
                      "estimated_cost_usd": float(usage[5]), "estimated_cost_inr": float(usage[6])},
            "report_json": d.report_json,
        }


def _pdf_response(doc_id: uuid.UUID, inline: bool) -> Response:
    with session_scope() as db:
        d = _get(db, doc_id)
        key, code = d.pdf_path, d.document_code
    if not key:
        raise NotFoundError("PDF not available")
    data = get_storage().get_bytes(key)
    disp = "inline" if inline else "attachment"
    return Response(content=data, media_type="application/pdf",
                    headers={"Content-Disposition": f'{disp}; filename="{code}.pdf"'})


@router.get("/documents/{doc_id}/download")
def download(doc_id: uuid.UUID) -> Response:
    return _pdf_response(doc_id, inline=False)


@router.get("/documents/{doc_id}/preview")
def preview(doc_id: uuid.UUID) -> Response:
    return _pdf_response(doc_id, inline=True)


@router.post("/documents/{doc_id}/rerender", status_code=201)
def rerender(doc_id: uuid.UUID) -> dict[str, Any]:
    """Re-render from the stored report_json with the current template — no LLM or provider calls."""
    with session_scope() as db:
        d = _get(db, doc_id)
        report = ReportModel.model_validate(d.report_json)
        base = {k: getattr(d, k) for k in ("journey_id", "generation_job_id", "document_code", "route_name", "region",
                                           "risk_level", "decision", "journey_score", "search_text", "narrative_json",
                                           "hazard_library_version", "prompt_version", "schema_version",
                                           "scoring_version", "rules_version", "model", "narrative_source",
                                           "providers")}
    report.meta.versions["template_version"] = settings().template_version
    html, pdf = render_document(report)
    new_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    st = get_storage()
    prefix = f"documents/{date_prefix(now)}/{base['document_code']}_{new_id.hex[:8]}"
    html_key = st.put_bytes(prefix + ".html", html.encode(), "text/html; charset=utf-8")
    pdf_key = st.put_bytes(prefix + ".pdf", pdf.pdf, "application/pdf")
    with session_scope() as db:
        nd = JmpDocument(id=new_id, **base, report_json=report.model_dump(mode="json"),
                         template_version=settings().template_version, app_version=settings().app_version,
                         html_path=html_key, pdf_path=pdf_key, pdf_sha256=hashlib.sha256(pdf.pdf).hexdigest(),
                         pdf_bytes=len(pdf.pdf), page_count=pdf.page_count, pdf_ms=pdf.render_ms, status="completed")
        db.add(nd)
        db.add(AuditEvent(entity="jmp_document", entity_id=str(new_id), action="rerender",
                          detail={"from_document": str(doc_id)}))
        db.flush()
        return doc_summary(nd)


@router.delete("/documents/{doc_id}", status_code=204)
def delete_document(doc_id: uuid.UUID) -> Response:
    with session_scope() as db:
        d = _get(db, doc_id)
        d.deleted_at = datetime.now(timezone.utc)
        db.add(AuditEvent(entity="jmp_document", entity_id=str(doc_id), action="soft_delete", detail={}))
    return Response(status_code=204)

