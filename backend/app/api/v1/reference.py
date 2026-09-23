from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app import rules_config
from app.db.models import GenerationUsage, HazardLibraryVersion, JmpDocument
from app.db.session import session_scope
from app.llm.pricing import PRICING_VERSION, RATES, projection
from app.services import hazard_library
from app.settings import settings
from app.versions import PROMPT_VERSION, SCHEMA_VERSION

router = APIRouter()


@router.get("/hazards")
def hazards() -> dict[str, Any]:
    with session_scope() as db:
        lib = hazard_library.load_active_library(db)
    return {"version": lib.version, "header_fields": list(lib.header_fields),
            "hospital_network_url": lib.hospital_network_url,
            "hazards": [{"code": h.code, "sr_no": h.sr_no, "name": h.name, "severity": h.severity,
                         "probability": h.probability, "rpn": h.rpn_code, "severity_band": h.severity_band,
                         "matrix_zone": h.matrix_zone, "controls_2w": list(h.control_2w_items),
                         "controls_4w": list(h.control_4w_items),
                         "detection": rules_config.hazard_rules()["detectors"].get(h.code, {})} for h in lib.hazards],
            "risk_matrix": rules_config.risk_matrix()}


@router.get("/hazard-library/versions")
def versions() -> list[dict[str, Any]]:
    with session_scope() as db:
        return [{"version": v.version, "source_filename": v.source_filename, "source_sha256": v.source_sha256,
                 "risk_matrix_image_sha256": v.risk_matrix_image_sha256, "is_active": v.is_active,
                 "ingested_at": v.ingested_at.isoformat()}
                for v in db.scalars(select(HazardLibraryVersion).order_by(HazardLibraryVersion.ingested_at))]


@router.get("/settings")
def effective_settings() -> dict[str, Any]:
    s = settings()
    return {
        "app_version": s.app_version, "template_version": s.template_version,
        "hazard_library_version": s.hazard_library_version, "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION, "scoring_version": rules_config.scoring_rules()["version"],
        "rules_version": rules_config.hazard_rules()["version"],
        "providers": {"geocoder": s.geocoder, "route": s.route_provider, "features": s.feature_provider,
                      "elevation": s.elevation_provider, "places": s.places_provider},
        "llm": {"provider": s.llm_provider, "model": s.anthropic_model, "effort": s.anthropic_effort,
                "cache_ttl": s.anthropic_cache_ttl, "max_attempts": s.llm_max_attempts,
                "concurrency": s.llm_concurrency, "bulk_mode": s.bulk_llm_mode,
                "fallbacks_enabled": s.anthropic_fallbacks_enabled,
                "api_key_configured": bool(s.anthropic_api_key)},
        "report": {"hazard_pointer_count": s.hazard_pointer_count, "max_stops": s.max_stops,
                   "display_band_method": rules_config.risk_matrix().get("display_band_method"),
                   "hazard_display_text": s.hazard_display_text,
                   "short_controls_approved": bool(rules_config.hazard_display().get("approved"))},
        "bulk": {"max_rows": s.bulk_max_rows, "max_bytes": s.bulk_max_bytes},
        "execution": s.job_execution, "storage": s.storage_backend, "usd_to_inr": s.usd_to_inr,
        "auth_required": bool(s.api_auth_token),
    }


@router.get("/usage/summary")
def usage_summary(date_from: date | None = Query(None, alias="from"),
                  date_to: date | None = Query(None, alias="to")) -> dict[str, Any]:
    with session_scope() as db:
        q = select(
            GenerationUsage.model, func.count(), func.coalesce(func.sum(GenerationUsage.input_tokens), 0),
            func.coalesce(func.sum(GenerationUsage.cache_creation_input_tokens), 0),
            func.coalesce(func.sum(GenerationUsage.cache_read_input_tokens), 0),
            func.coalesce(func.sum(GenerationUsage.output_tokens), 0),
            func.coalesce(func.sum(GenerationUsage.estimated_cost_usd), 0),
            func.coalesce(func.sum(GenerationUsage.estimated_cost_inr), 0),
            func.coalesce(func.avg(GenerationUsage.duration_ms), 0),
        ).group_by(GenerationUsage.model)
        dq = select(func.count()).select_from(JmpDocument).where(JmpDocument.deleted_at.is_(None))
        if date_from:
            start = datetime.combine(date_from, time.min, timezone.utc)
            q = q.where(GenerationUsage.created_at >= start)
            dq = dq.where(JmpDocument.created_at >= start)
        if date_to:
            end = datetime.combine(date_to, time.max, timezone.utc)
            q = q.where(GenerationUsage.created_at <= end)
            dq = dq.where(JmpDocument.created_at <= end)
        rows = db.execute(q).all()
        docs = db.scalar(dq) or 0
    by_model = []
    tot_usd = tot_inr = 0.0
    for m, calls, inp, cw, cr, out, usd, inr, avg_ms in rows:
        denom = inp + cw + cr
        by_model.append({"model": m, "calls": calls, "input_tokens": int(inp), "cache_creation_input_tokens": int(cw),
                         "cache_read_input_tokens": int(cr), "output_tokens": int(out),
                         "cache_hit_ratio": round(cr / denom, 3) if denom else 0.0,
                         "estimated_cost_usd": round(float(usd), 4), "estimated_cost_inr": round(float(inr), 2),
                         "avg_latency_ms": int(avg_ms)})
        tot_usd += float(usd)
        tot_inr += float(inr)
    per = tot_usd / docs if docs else 0.0
    return {"documents": docs, "by_model": by_model,
            "totals": {"estimated_cost_usd": round(tot_usd, 4), "estimated_cost_inr": round(tot_inr, 2)},
            "actual_cost_per_jmp_usd": round(per, 5),
            "actual_cost_per_100_jmp_usd": round(per * 100, 3), "actual_cost_per_1000_jmp_usd": round(per * 1000, 2),
            "actual_cost_per_jmp_inr": round(per * settings().usd_to_inr, 3)}


@router.get("/usage/cost-model")
def cost_model(prefix_tokens: int = 7500, variable_tokens: int = 2500, output_tokens: int = 4500,
               cache_hit_rate: float = Query(1.0, ge=0, le=1)) -> dict[str, Any]:
    s = settings()
    out = []
    for model in ("claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"):
        for batch in (False, True):
            out.append(projection(model, prefix_tokens=prefix_tokens, variable_tokens=variable_tokens,
                                  output_tokens=output_tokens, cache_ttl="1h" if batch else s.anthropic_cache_ttl,
                                  batch=batch, usd_inr=s.usd_to_inr, cache_hit_rate=cache_hit_rate))
    return {"pricing_version": PRICING_VERSION, "configured_model": s.anthropic_model,
            "rates_usd_per_mtok": {k: {"input": float(v.input), "output": float(v.output)} for k, v in RATES.items()
                                   if k != "mock"}, "projections": out}
