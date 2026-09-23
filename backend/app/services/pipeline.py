"""Pure pipeline steps shared by the Celery tasks, the CLI and the tests.

compute_facts   stages 2–8  (geocode → route → features → analyse → hazards → scoring → emergency)
narrate         stage 9     (Claude or mock, validated)
render_document stages 10–12 (assemble → HTML → PDF)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from app.domain.facts import JourneyFacts
from app.llm.schemas import NarrativeV1
from app.providers.registry import Providers
from app.rendering.html_renderer import render_html
from app.rendering.pdf_renderer import PdfResult, render_pdf
from app.services.emergency import build_emergency
from app.services.hazard_engine import run_hazard_engine
from app.services.hazard_library import HazardLibrary
from app.services.report_assembler import ReportModel, assemble_report
from app.services.route_service import analyse_route, geocode_all
from app.services.scoring import compute_scores
from app.versions import VersionStamp


@dataclass
class JourneyOptions:
    vehicle_type: str = "4W"
    vehicle_type_specified: bool = False
    travel_date: date | None = None
    depart_time: str | None = None
    manager_name: str | None = None
    emergency_contact: str | None = None
    nearest_hospital: str | None = None
    nearest_police: str | None = None


def compute_facts(inputs: list[str], opts: JourneyOptions, providers: Providers, library: HazardLibrary,
                  session: Session | None = None, geocoded: list | None = None) -> JourneyFacts:
    geocoded = geocoded or geocode_all(inputs, providers, session)
    facts, route, fs, elev = analyse_route(
        inputs=inputs, geocoded=geocoded, providers=providers, vehicle_type=opts.vehicle_type,
        vehicle_type_specified=opts.vehicle_type_specified, travel_date=opts.travel_date,
        depart_time=opts.depart_time, session=session)
    hazards, not_applicable, verification = run_hazard_engine(library, facts, route, fs, elev)
    scores = compute_scores(facts, hazards)
    emergency = build_emergency(facts, providers, nearest_hospital=opts.nearest_hospital,
                                nearest_police=opts.nearest_police, emergency_contact=opts.emergency_contact,
                                manager_name=opts.manager_name, hospital_network_url=library.hospital_network_url)
    return JourneyFacts(route=facts, hazards=hazards, not_applicable=not_applicable, verification=verification,
                        scores=scores, emergency=emergency, hazard_library_version=library.version)


def build_report(jf: JourneyFacts, narrative: NarrativeV1, *, journey_code: str, versions: VersionStamp,
                 narrative_source: str, model: str, pointer_count: int) -> ReportModel:
    return assemble_report(jf, narrative, journey_code=journey_code, versions=versions,
                           narrative_source=narrative_source, model=model, pointer_count=pointer_count)


def render_document(report: ReportModel) -> tuple[str, PdfResult]:
    html = render_html(report)
    v = report.meta.versions
    meta = {
        "Title": f"Journey Management Plan {report.meta.journey_code}",
        "Author": f"{report.meta.company_title} – {report.meta.department}",
        "Subject": f"{report.route.route_name} | {report.route.region_label}",
        "Keywords": " ".join(f"{k}={val}" for k, val in sorted(v.items())) + f" model={report.meta.model}",
        "Creator": "JMP Generator",
    }
    return html, render_pdf(html, metadata=meta, expected_pages=report.meta.page_total)
