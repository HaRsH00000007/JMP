"""Text-only plan: issued when fewer than two stops could be located, so there is no route to measure.

Everything printed is either submitted (the stops, verbatim, and the journey brief), verified (the hazard
library and the pan-India emergency numbers) or an explicit instruction to verify. Nothing is measured and
nothing is estimated: no distance, duration, road type, segment, hazard position, score or decision. The
whole hazard library is printed as a checklist because, without a route, none of it can be ruled in or out.
The LLM is never involved.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from markupsafe import Markup
from pydantic import BaseModel

from app import rules_config
from app.domain.facts import DirectoryRow, UnverifiedStop, VerificationItem
from app.domain.route import GeocodeResult
from app.rendering.html_renderer import _assets, _env, template_dir
from app.rendering.pdf_renderer import PdfResult, render_pdf
from app.services.emergency import supplied_rows
from app.services.hazard_engine.engine import display_band_for
from app.services.hazard_library import HazardLibrary, short_control
from app.services.pipeline import JourneyOptions
from app.versions import VersionStamp

PAGE_TOTAL = 3
RISK_LEVEL = "NOT ASSESSED"
DECISION = "TEXT-ONLY — ROUTE NOT MEASURED"


class TextOnlyStop(BaseModel):
    seq: int
    kind: Literal["start", "stop", "end"]
    input_text: str
    located_as: str | None  # the geocoder's name for it, when it was found
    note: str


class TextOnlyHazard(BaseModel):
    code: str
    name: str
    band: str
    control: str


class TextOnlyMeta(BaseModel):
    journey_code: str
    version_label: str
    generated_at: str
    company: str
    company_title: str
    department: str
    disclaimer: str
    page_total: int = PAGE_TOTAL
    versions: dict[str, str]
    providers: dict[str, str]
    narrative_source: str = "none"
    model: str = "none"
    demo_data: bool = False
    demo_narrative: bool = False
    show_demo_watermark: bool = False


class TextOnlyReport(BaseModel):
    kind: Literal["text_only"] = "text_only"
    meta: TextOnlyMeta
    route_name: str
    reason: str
    travel: dict[str, str]
    stops: list[TextOnlyStop]
    hazards: list[TextOnlyHazard]
    verification: list[VerificationItem]
    emergency_directory: list[DirectoryRow]
    emergency_protocols: list[dict[str, Any]]
    emergency_note: str
    hospital_network_url: str | None


def build_text_only(inputs: list[str], opts: JourneyOptions, library: HazardLibrary, *, journey_code: str,
                    versions: VersionStamp, located: list[tuple[str, GeocodeResult]],
                    unverified: list[UnverifiedStop], collapsed: list[str], reason: str,
                    geocoder: str, generated_at: datetime | None = None) -> TextOnlyReport:
    brand = rules_config.emergency_static()["brand"]
    cfg = rules_config.emergency_static()
    gen = generated_at or datetime.now(timezone.utc)
    found = dict(located)
    missing = {u.input_text: u.reason for u in unverified}
    last = len(inputs) - 1
    stops = []
    for i, text in enumerate(inputs):
        g = found.get(text)
        if text in collapsed:
            note = "Located, but at the same point as the previous stop"
        elif g is not None:
            note = "Located — not enough located stops to measure a route"
        else:
            note = missing.get(text, "Not located")
        stops.append(TextOnlyStop(seq=i + 1, kind="start" if i == 0 else ("end" if i == last else "stop"),
                                  input_text=text, located_as=g.name if g else None, note=note))
    vehicle = opts.vehicle_type
    hazards = [TextOnlyHazard(code=h.code, name=h.name, band=display_band_for(h.severity_band, h.matrix_zone),
                              control=short_control(h, vehicle)) for h in library.hazards]
    verification = [VerificationItem(item=f'Confirm the exact address of the {s.kind} "{s.input_text}"',
                                     reason="Not located on the map" if s.located_as is None
                                     else f"Located as {s.located_as}")
                    for s in stops]
    verification += [
        VerificationItem(item="Route, distance and driving time for the whole journey",
                         reason="Not measured — obtain from live navigation before departure"),
        VerificationItem(item="Which of the listed hazards apply to the chosen route",
                         reason="No route data, so no hazard could be confirmed or ruled out"),
        VerificationItem(item="Nearest hospital and police station along the route",
                         reason="Not searched — no located route to search along"),
    ]
    if not opts.vehicle_type_specified:
        verification.append(VerificationItem(item="Vehicle type (controls assume 4-Wheeler)",
                                             reason="Vehicle type not specified in the journey brief"))
    travel = {
        "Vehicle": vehicle + ("" if opts.vehicle_type_specified else " (assumed)"),
        "Travel date": opts.travel_date.strftime("%d %b %Y") if opts.travel_date else "Not supplied",
        "Departure": opts.depart_time or "Not supplied",
        "Manager": opts.manager_name or "Not supplied",
    }
    return TextOnlyReport(
        meta=TextOnlyMeta(
            journey_code=journey_code, version_label=f"v{versions.template_version} — {gen.strftime('%d %b %Y')}",
            generated_at=gen.isoformat(), company=brand["company"], company_title=brand["company_title"],
            department=brand["department"], disclaimer=" ".join(brand["disclaimer"].split()),
            versions=versions.as_dict(), providers={"geocode": geocoder, "route": "none (not measured)"}),
        route_name=f"{inputs[0]} → {inputs[-1]}" if len(inputs) > 1 else inputs[0],
        reason=reason, travel=travel, stops=stops, hazards=hazards, verification=verification,
        emergency_directory=supplied_rows(nearest_hospital=opts.nearest_hospital, nearest_police=opts.nearest_police,
                                          emergency_contact=opts.emergency_contact, manager_name=opts.manager_name)
        + [DirectoryRow(**r) for r in cfg.get("static_directory_rows", [])],
        emergency_protocols=cfg["protocols"], emergency_note=cfg["directory_note"].strip(),
        hospital_network_url=library.hospital_network_url)


def render_text_only(r: TextOnlyReport) -> tuple[str, PdfResult]:
    d = str(template_dir())
    styles, fonts = _assets(d)
    html = _env(d).get_template("text_only.html.j2").render(r=r, styles_css=Markup(styles), fonts_css=Markup(fonts))
    v = r.meta.versions
    meta = {
        "Title": f"Journey Management Plan {r.meta.journey_code} (text-only)",
        "Author": f"{r.meta.company_title} – {r.meta.department}",
        "Subject": f"{r.route_name} | route not measured",
        "Keywords": " ".join(f"{k}={val}" for k, val in sorted(v.items())) + " kind=text_only",
        "Creator": "JMP Generator",
    }
    return html, render_pdf(html, metadata=meta, expected_pages=r.meta.page_total)
