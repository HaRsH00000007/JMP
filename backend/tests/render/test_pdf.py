"""PDF rendering: fixed 8-page A4 structure, headers/footers, dynamic data, hazard pointer page, maps,
overflow protection. Launches Chromium (Playwright)."""

from __future__ import annotations

from datetime import date

import pymupdf
import pytest

from app.errors import ErrorCode, RenderError
from app.llm.mock_provider import MockNarrativeProvider
from app.llm.service import generate_narrative
from app.rendering.html_renderer import render_html
from app.rendering.pdf_renderer import render_pdf
from app.services.pipeline import JourneyOptions, build_report, compute_facts, render_document
from app.versions import current_versions

pytestmark = pytest.mark.render

A4_W, A4_H = 595.3, 841.9


def _report(facts, library, code="DAN-JMP-WB-001"):
    n, src, model = generate_narrative(facts, library, provider=MockNarrativeProvider())
    return build_report(facts, n, journey_code=code, versions=current_versions(library.version),
                        narrative_source=src, model=model, pointer_count=5)


@pytest.fixture(scope="module")
def rendered():
    from app.db.session import session_scope
    from app.providers.registry import get_providers
    from app.services.hazard_library import load_active_library
    from tests.conftest import PYRAGANDA

    with session_scope() as db:
        lib = load_active_library(db)
        facts = compute_facts(PYRAGANDA, JourneyOptions(vehicle_type="4W", vehicle_type_specified=True,
                                                        travel_date=date(2026, 8, 8), depart_time="09:00"),
                              get_providers(), lib, db)
    report = _report(facts, lib)
    html, pdf = render_document(report)
    doc = pymupdf.open(stream=pdf.pdf, filetype="pdf")
    pages = [p.get_text() for p in doc]
    return {"report": report, "html": html, "pdf": pdf, "doc": doc, "pages": pages}


def test_eight_a4_pages(rendered):
    doc = rendered["doc"]
    assert rendered["pdf"].page_count == 8 == len(doc)
    for p in doc:
        assert abs(p.rect.width - A4_W) < 2 and abs(p.rect.height - A4_H) < 2


def test_page_titles_and_order(rendered):
    p = [t.upper() for t in rendered["pages"]]
    assert "MANAGEMENT JOURNEY SNAPSHOT" in p[0]
    assert "EXECUTIVE SUMMARY" in p[1] and "JOURNEY DASHBOARD" in p[1] and "JOURNEY TIMELINE" in p[1]
    assert "ROUTE INTELLIGENCE" in p[2] and "PRIMARY ROUTE BREAKDOWN" in p[2]
    assert "ALTERNATIVE ROUTE ANALYSIS" in p[3] and "ROUTE COMPARISON" in p[3]
    assert "ROUTE RISK ANALYSIS" in p[4] and "TOP ROUTE-SPECIFIC HAZARDS" in p[4]
    assert "ROUTE HAZARD POINTERS" in p[5] and "REQUIRES OPERATIONAL VERIFICATION" in p[5]
    assert "JOURNEY ASSESSMENT" in p[6] and "FATIGUE ASSESSMENT" in p[6] and "PRIORITY RECOMMENDATIONS" in p[6]
    assert "EMERGENCY DIRECTORY" in p[7] and "KEY JOURNEY HAZARDS" in p[7]
    assert "SIGN-OFF" not in p[7]  # removed in template v1.1 at Danone's request


def test_footers_and_page_numbers(rendered):
    for i, text in enumerate(rendered["pages"][1:], start=2):
        flat = " ".join(text.split())
        assert f"Page {i} of 8" in flat, i
        assert "Journey Management Plan · Confidential" in flat
    assert "of 8" not in " ".join(rendered["pages"][0].split())  # page 1 has no footer (master PDF)


def test_dynamic_route_data_printed(rendered):
    r = rendered["report"]
    all_text = " ".join(" ".join(t.split()) for t in rendered["pages"])
    assert r.meta.journey_code in rendered["pages"][0]
    lo, hi = r.route.distance_range_km
    assert f"{lo}–{hi} km" in all_text
    assert f"{r.scores.total}" in rendered["pages"][0]
    for w in r.route.waypoints[:5]:
        assert w.short_name in all_text
    for row in r.hazard_table:  # every library value printed verbatim
        assert row.hazard.rpn_code in rendered["pages"][4]


def test_hazard_pointer_page(rendered):
    r = rendered["report"]
    page = " ".join(rendered["pages"][5].split())
    assert 3 <= len(r.pointers) <= 5
    for row in r.pointers:
        assert row.display_name[:18] in page
    shown = {row.hazard.code for row in r.pointers}
    others = {row.hazard.code for row in r.hazard_table} - shown
    assert len(shown) < 25 and (not others or len(r.hazard_table) > len(r.pointers))


def test_route_maps_drawn_from_geometry(rendered):
    html = rendered["html"]
    assert html.count("<svg") >= 5
    assert "ROUTE MAP" in html and 'stroke-linejoin="round"' in html
    assert '<div class="demo-ribbon">' in html  # mock data is watermarked by default


def test_demo_watermark_can_be_disabled_but_facts_are_kept(rendered, library, zirakpur_facts):
    """REPORT_DEMO_WATERMARK=false hides the banner; the document still records that it was demo data."""
    from app.settings import get_settings, set_settings

    set_settings(get_settings().model_copy(update={"report_demo_watermark": False}))
    try:
        report = _report(zirakpur_facts, library, code="DAN-JMP-PB-900")
        html = render_html(report)
        assert report.meta.show_demo_watermark is False
        assert '<div class="demo-ribbon">' not in html  # the CSS rule stays; the element is gone
        assert "NOT FOR OPERATIONAL USE" not in html
        # the facts are never erased — audit trail and the Reports listing still show it as demo
        assert report.meta.demo_data is True and report.meta.demo_narrative is True
    finally:
        set_settings(None)
    assert rendered["report"].meta.show_demo_watermark is True  # default stays on


def test_versions_and_no_sign_off(rendered):
    p8 = " ".join(rendered["pages"][7].split())
    for k in ("Prepared By", "Reviewed By", "Approved By", "Name / Date / Signature"):
        assert k not in p8  # no sign-off block (template v1.1)
    assert "Template v1.1" in p8 and "Hazard library v1.0" in p8 and "Prompt v1.0" in p8
    assert "112" in p8 and "VERIFIED" in p8
    meta = rendered["doc"].metadata
    assert "template_version=1.1" in meta["keywords"] and "prompt_version=1.0" in meta["keywords"]


def test_no_overflow_on_short_and_long_routes(library, providers, db):
    short = compute_facts(["Zirakpur", "Lalru"], JourneyOptions(), providers, library, db)
    long_ = compute_facts(["Pyraganda", "Chakdah", "Madanpur", "Palpara", "Kalyani", "Naihati", "Jagaddal",
                           "Kakinara", "Shyamnagar", "Barrackpore", "Barasat", "Kalyani Junction", "Kalyani North",
                           "Manipal Hospital, Kalyani", "Ranaghat"],
                          JourneyOptions(travel_date=date(2026, 7, 1), depart_time="07:00"), providers, library, db)
    for facts in (short, long_):
        _, pdf = render_document(_report(facts, library))
        assert pdf.page_count == 8


def test_overflow_is_detected_not_clipped(rendered):
    html = rendered["html"].replace("<p class=\"body\">", "<p class=\"body\">" + "overflow " * 3000, 1)
    with pytest.raises(RenderError) as ei:
        render_pdf(html, metadata={"Title": "x"})
    assert ei.value.code == ErrorCode.RENDER_OVERFLOW


def test_missing_geometry_fails_gracefully(rendered):
    report = rendered["report"].model_copy(deep=True)
    report.route.geometry = []
    with pytest.raises(RenderError) as ei:
        render_html(report)
    assert ei.value.code == ErrorCode.ROUTE_GEOMETRY_UNAVAILABLE
