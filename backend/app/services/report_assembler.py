"""ReportAssembler (stage 10): deterministic facts + validated narrative → ReportModel.

ReportModel is exactly what the template renders and what jmp_documents.report_json stores. Every number,
band, hazard and decision in it comes from JourneyFacts; the narrative contributes prose only. Each block
carries its data classification.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from app import rules_config
from app.domain.facts import (
    Alternative,
    DimensionScore,
    DirectoryRow,
    FatigueStint,
    HazardMatch,
    JourneyFacts,
    RouteFacts,
    Scores,
    Segment,
    VerificationItem,
)
from app.llm.schemas import NarrativeV1
from app.settings import settings
from app.versions import VersionStamp

PAGE_TOTAL = 8


class HazardRow(BaseModel):
    hazard: HazardMatch
    route_context: str
    card_location: str
    card_description: str
    qualifier: str
    display_name: str  # library name, or explicit analog label


class SegmentRow(BaseModel):
    segment: Segment
    key_exposure: str


class AlternativeRow(BaseModel):
    alt: Alternative
    advantage: str
    limitation: str
    use_when: str


class DimensionRow(BaseModel):
    dim: DimensionScore
    remark: str


class FatigueRow(BaseModel):
    stint: FatigueStint
    basis: str


class ReportMeta(BaseModel):
    journey_code: str
    document_code: str
    version_label: str
    generated_at: str
    company: str
    company_title: str
    department: str
    disclaimer: str
    page_total: int = PAGE_TOTAL
    versions: dict[str, str]
    providers: dict[str, str]
    narrative_source: str
    model: str
    demo_data: bool          # audit fact: route data came from mock providers
    demo_narrative: bool     # audit fact: narrative came from the template writer, not Claude
    show_demo_watermark: bool = False   # display only (REPORT_DEMO_WATERMARK)
    classification_note: str = (
        "Data classification used throughout this report: VERIFIED/PROVIDED (source documents and supplied "
        "values) · ASSESSMENT (derived route analysis) · REQUIRES OPERATIONAL VERIFICATION (to be confirmed "
        "before travel).")


class ReportModel(BaseModel):
    meta: ReportMeta
    route: RouteFacts
    scores: Scores
    hazard_table: list[HazardRow]
    top_hazards: list[HazardRow]
    exposure_cards: list[HazardRow]
    key_hazards: list[HazardRow]
    pointers: list[HazardRow]
    segments: list[SegmentRow]
    alternatives: list[AlternativeRow]
    dimensions: list[DimensionRow]
    fatigue: list[FatigueRow]
    verification: list[VerificationItem]
    emergency_directory: list[DirectoryRow]
    emergency_protocols: list[dict[str, Any]]
    emergency_note: str
    hospital_network_url: str | None
    narrative: dict[str, Any]
    highlight_captions: dict[str, str]
    classification: dict[str, str] = Field(default_factory=lambda: {
        "route": "ASSESSMENT", "hazard_values": "VERIFIED", "hazard_applicability": "ASSESSMENT",
        "scores": "ASSESSMENT", "narrative": "ASSESSMENT", "verification": "REQUIRES_VERIFICATION",
        "alternatives": "REQUIRES_VERIFICATION"})


def assemble_report(jf: JourneyFacts, narrative: NarrativeV1, *, journey_code: str, versions: VersionStamp,
                    narrative_source: str, model: str, pointer_count: int,
                    generated_at: datetime | None = None) -> ReportModel:
    caps = rules_config.hazard_rules()["report_caps"]
    brand = rules_config.emergency_static()["brand"]
    gen = generated_at or datetime.now(timezone.utc)
    notes = {h.hazard_code: h for h in narrative.hazard_notes}

    rows = []
    for h in jf.hazards:
        n = notes[h.code]
        rows.append(HazardRow(hazard=h, route_context=n.route_context, card_location=n.card_location,
                              card_description=n.card_description, qualifier=n.qualifier,
                              display_name=h.analog_label or h.name))
    pointer_n = max(caps["hazard_pointers_min"], min(pointer_count, caps["hazard_pointers_default"] + 3))
    seg_notes = {s.segment_id: s.key_exposure for s in narrative.segment_notes}
    alt_notes = {a.alt_id: a for a in narrative.alternative_notes}
    dim_notes = {d.dimension: d.remark for d in narrative.dimension_remarks}
    fat_notes = {f.stint_id: f.basis for f in narrative.fatigue_notes}
    verification = list(jf.verification) + [
        VerificationItem(item=v.item, reason=v.reason, source="llm") for v in narrative.additional_verification
    ]
    narrative_dict = narrative.model_dump()
    for k in ("hazard_notes", "segment_notes", "alternative_notes", "dimension_remarks", "fatigue_notes",
              "additional_verification", "highlight_captions"):
        narrative_dict.pop(k, None)

    return ReportModel(
        meta=ReportMeta(
            journey_code=journey_code, document_code=journey_code,
            version_label=f"v{versions.template_version} — {gen.strftime('%d %b %Y')}",
            generated_at=gen.isoformat(), company=brand["company"], company_title=brand["company_title"],
            department=brand["department"], disclaimer=" ".join(brand["disclaimer"].split()),
            versions=versions.as_dict(), providers=jf.route.providers, narrative_source=narrative_source,
            model=model, demo_data=jf.route.is_demo_data, demo_narrative=narrative_source == "mock",
            show_demo_watermark=(jf.route.is_demo_data or narrative_source == "mock")
            and settings().report_demo_watermark),
        route=jf.route,
        scores=jf.scores,
        hazard_table=rows[:caps["hazard_table_max"]],
        top_hazards=rows[:caps["top_hazards_max"]],
        exposure_cards=rows[:caps["exposure_cards_max"]],
        key_hazards=rows[:caps["key_hazards_max"]],
        pointers=rows[:pointer_n],
        segments=[SegmentRow(segment=s, key_exposure=seg_notes[s.id]) for s in jf.route.segments],
        alternatives=[AlternativeRow(alt=a, advantage=alt_notes[a.id].advantage,
                                     limitation=alt_notes[a.id].limitation, use_when=alt_notes[a.id].use_when)
                      for a in jf.route.alternatives],
        dimensions=[DimensionRow(dim=d, remark=dim_notes[d.id]) for d in jf.scores.dimensions],
        fatigue=[FatigueRow(stint=f, basis=fat_notes[f.id]) for f in jf.scores.fatigue_stints],
        verification=verification[:caps["verification_items_max"] + 6],
        emergency_directory=jf.emergency.directory,
        emergency_protocols=jf.emergency.protocols,
        emergency_note=jf.emergency.note,
        hospital_network_url=jf.emergency.hospital_network_url,
        narrative=narrative_dict,
        highlight_captions=narrative.highlight_captions.model_dump(),
    )
