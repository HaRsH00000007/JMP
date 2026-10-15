"""Generate endpoint with coordinates from the requester's place picker: routed from them, full 8-page PDF."""

from __future__ import annotations

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app.main import create_app

pytestmark = pytest.mark.render

POINTS = {
    "start_point": {"lat": 30.6400, "lng": 76.8200, "state": "Punjab", "district": "SAS Nagar (Mohali)"},
    "stop_points": [{"lat": 30.5950, "lng": 76.8450, "state": "Punjab"}],
    "end_point": {"lat": 30.4880, "lng": 76.7990, "state": "Punjab"},
}


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app()) as c:
        yield c


def test_generation_from_supplied_points_produces_the_8_page_plan(client) -> None:
    body = {"start_location": "Paras Downtown, Zirakpur", "stops": ["Bus stand, Dera Bassi"],
            "end_location": "Danone plant gate, Lalru", "travel_date": "2026-12-15", "depart_time": "09:30",
            **POINTS}
    r = client.post("/api/v1/journeys/generate", json=body)
    assert r.status_code == 202, r.text
    job = client.get(f"/api/v1/jobs/{r.json()['job_id']}").json()
    assert job["status"] == "completed", job
    assert job["journey_code"].startswith("DAN-JMP-PB-")

    doc = client.get(f"/api/v1/documents/{job['document_id']}").json()
    assert doc["page_count"] == 8
    wps = doc["report_json"]["route"]["waypoints"]
    assert [(w["lat"], w["lng"]) for w in wps] == [(30.64, 76.82), (30.595, 76.845), (30.488, 76.799)]
    assert {w["geocode_provider"] for w in wps} == {"frontend"}
    assert [w["input_text"] for w in wps] == [body["start_location"], *body["stops"], body["end_location"]]

    pdf = client.get(f"/api/v1/documents/{job['document_id']}/download")
    assert len(pymupdf.open(stream=pdf.content, filetype="pdf")) == 8


def test_invalid_points_are_rejected_by_the_api(client) -> None:
    r = client.post("/api/v1/journeys/generate", json={
        "start_location": "Zirakpur", "end_location": "Lalru", "start_point": {"lat": 120, "lng": 76.8}})
    assert r.status_code == 422
    assert r.json()["error"]["details"][0]["field"] == "start_point.lat"
