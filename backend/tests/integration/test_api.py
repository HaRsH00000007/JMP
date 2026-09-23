"""API end-to-end (sync execution): health, individual generation → PDF, validation errors, jobs, documents,
bulk validate/create/progress/ZIP/manifests, failed-route isolation, retry, cancel, batch mode, migrations."""

from __future__ import annotations

import io
import json
import zipfile

import openpyxl
import pymupdf
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import ZIRAKPUR

pytestmark = pytest.mark.render


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app()) as c:
        yield c


def test_health_and_ready(client):
    assert client.get("/api/v1/health").json()["status"] == "ok"
    r = client.get("/api/v1/health/ready")
    assert r.status_code == 200 and r.json()["hazard_library"] == "1.0"


def test_individual_generation_end_to_end(client):
    body = {"start_location": ZIRAKPUR[0], "stops": ZIRAKPUR[1:3], "end_location": ZIRAKPUR[3],
            "vehicle_type": "4W", "travel_date": "2026-08-10", "depart_time": "09:30", "manager_name": "Line Manager",
            "nearest_hospital": "Supplied Hospital, Dera Bassi"}
    r = client.post("/api/v1/journeys/generate", json=body)
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    job = client.get(f"/api/v1/jobs/{job_id}").json()
    assert job["status"] == "completed", job
    assert job["journey_code"].startswith("DAN-JMP-PB-")
    assert job["route"] == ZIRAKPUR
    doc = client.get(f"/api/v1/documents/{job['document_id']}").json()
    assert doc["page_count"] == 8
    assert doc["versions"] == {"template": "1.1", "hazard_library": "1.0", "prompt": "1.0", "schema": "1.0",
                               "scoring": "1.0", "rules": "1.0", "app": "1.0.0"}
    assert doc["report_json"]["meta"]["demo_data"] is True
    rows = doc["report_json"]["emergency_directory"]
    assert any(r["name"] == "Supplied Hospital, Dera Bassi" and r["status"] == "PROVIDED" for r in rows)
    pdf = client.get(f"/api/v1/documents/{job['document_id']}/download")
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    assert len(pymupdf.open(stream=pdf.content, filetype="pdf")) == 8
    lst = client.get("/api/v1/documents", params={"q": "lalru"}).json()
    assert any(d["document_id"] == job["document_id"] for d in lst["items"])
    usage = client.get("/api/v1/usage/summary").json()
    assert usage["documents"] >= 1


def test_idempotency_key_returns_same_job(client):
    body = {"start_location": "Zirakpur", "end_location": "Lalru"}
    a = client.post("/api/v1/journeys/generate", json=body, headers={"Idempotency-Key": "abc-123"}).json()
    b = client.post("/api/v1/journeys/generate", json=body, headers={"Idempotency-Key": "abc-123"}).json()
    assert a["job_id"] == b["job_id"]


def test_validation_error_envelope(client):
    r = client.post("/api/v1/journeys/generate", json={"start_location": "", "end_location": "Lalru"})
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "VALIDATION_ERROR" and err["details"][0]["field"] == "start_location"


def test_unknown_location_fails_job_with_code(client):
    r = client.post("/api/v1/journeys/generate", json={"start_location": "Nowhere Land 123", "end_location": "Lalru"})
    job = client.get(f"/api/v1/jobs/{r.json()['job_id']}").json()
    assert job["status"] == "failed" and job["error"]["code"] == "GEOCODE_NOT_FOUND"
    assert client.post(f"/api/v1/jobs/{job['job_id']}/retry").json()["status"] == "failed"  # still not geocodable


def test_rerender_and_delete(client):
    lst = client.get("/api/v1/documents").json()["items"]
    doc_id = lst[0]["document_id"]
    new = client.post(f"/api/v1/documents/{doc_id}/rerender")
    assert new.status_code == 201 and new.json()["page_count"] == 8
    assert client.delete(f"/api/v1/documents/{new.json()['document_id']}").status_code == 204
    assert client.get(f"/api/v1/documents/{new.json()['document_id']}").status_code == 404


def _csv(rows: list[str]) -> bytes:
    return ("route_id,start_location,stop_1,stop_2,end_location,vehicle_type,travel_date\n" + "\n".join(rows)).encode()


BULK_ROWS = [
    'B-001,"Paras Downtown Zirakpur, Punjab","Chandigarh City Center Zirakpur, Punjab",,"Danone Nutricia India Plant, Lalru, Punjab",4W,2026-08-10',
    "B-002,Mohali,Kharar,,Kurali,2W,2026-01-12",
    "B-003,Atlantis Unknown Place,Lalru,,Ambala,4W,",              # geocode failure (row-level)
    "B-004,Zirakpur,,Dera Bassi,Lalru,4W,",                          # invalid: gap
    "B-005,Panchkula,Pinjore,Kalka,Baddi,4W,2026-03-02",
]


def test_bulk_validate_preview(client):
    r = client.post("/api/v1/bulk-jobs/validate", files={"file": ("routes.csv", _csv(BULK_ROWS), "text/csv")})
    s = r.json()
    assert s["total_rows"] == 5 and s["valid_rows"] == 4 and s["invalid_rows"] == 1
    assert s["errors"][0]["route_id"] == "B-004" and s["preview"][0]["stops"] == ["Chandigarh City Center Zirakpur, Punjab"]
    assert s["upload_id"]


def test_bulk_end_to_end_with_failure_isolation(client):
    up = client.post("/api/v1/bulk-jobs/validate", files={"file": ("routes.csv", _csv(BULK_ROWS), "text/csv")}).json()
    r = client.post("/api/v1/bulk-jobs", data={"upload_id": up["upload_id"]})
    assert r.status_code == 202
    created = r.json()
    assert created["total_routes"] == 5 and created["valid_routes"] == 4
    bj = client.get(f"/api/v1/bulk-jobs/{created['job_id']}").json()
    assert bj["status"] == "completed_with_errors"
    assert (bj["processed"], bj["successful"], bj["failed"], bj["percentage"]) == (4, 3, 1, 100)
    items = client.get(f"/api/v1/bulk-jobs/{bj['job_id']}/items").json()["items"]
    by = {i["route_id"]: i for i in items}
    assert by["B-003"]["status"] == "failed" and by["B-003"]["error_code"] == "GEOCODE_NOT_FOUND"
    assert by["B-004"]["status"] == "invalid"
    assert all(by[k]["status"] == "succeeded" for k in ("B-001", "B-002", "B-005"))
    z = client.get(f"/api/v1/bulk-jobs/{bj['job_id']}/download")
    assert z.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(z.content))
    names = zf.namelist()
    pdfs = [n for n in names if n.startswith("pdfs/")]
    assert len(pdfs) == 3 and {"manifest.csv", "manifest.xlsx", "summary.json"} <= set(names)
    manifest = zf.read("manifest.csv").decode("utf-8-sig").splitlines()
    assert len(manifest) == 6  # header + every row, including failed and invalid
    summary = json.loads(zf.read("summary.json"))
    assert summary["successful"] == 3 and summary["failure_reasons"] == {"GEOCODE_NOT_FOUND": 1,
                                                                         "VALIDATION_ERROR": 1}
    assert len(summary["document_ids"]) == 3 and summary["processing_seconds"] >= 0
    xl = client.get(f"/api/v1/bulk-jobs/{bj['job_id']}/manifest", params={"format": "xlsx"})
    wb = openpyxl.load_workbook(io.BytesIO(xl.content))
    assert wb["Manifest"].max_row == 6
    # retry-failed re-runs only the failed row (still fails: location unknown), counters stay consistent
    rr = client.post(f"/api/v1/bulk-jobs/{bj['job_id']}/retry-failed").json()
    assert rr["requeued"] == 1
    again = client.get(f"/api/v1/bulk-jobs/{bj['job_id']}").json()
    assert (again["processed"], again["successful"], again["failed"]) == (4, 3, 1)


def test_bulk_formula_injection_neutralised(client):
    rows = ['"=HYPERLINK(""x"")",Zirakpur,,,Lalru,4W,']
    r = client.post("/api/v1/bulk-jobs", files={"file": ("x.csv", _csv(rows), "text/csv")}).json()
    content = client.get(f"/api/v1/bulk-jobs/{r['job_id']}/manifest", params={"format": "csv"}).content
    import csv as csvmod

    rows_out = list(csvmod.reader(io.StringIO(content.decode("utf-8-sig"))))
    assert rows_out[1][1].startswith("'=HYPERLINK")  # neutralised: never starts with '='
    assert not any(cell.startswith("=") for row in rows_out for cell in row)


def test_bulk_idempotency_key(client):
    data = _csv(["I-1,Zirakpur,,,Lalru,4W,"])
    a = client.post("/api/v1/bulk-jobs", files={"file": ("a.csv", data, "text/csv")},
                    headers={"Idempotency-Key": "bulk-k1"}).json()
    b = client.post("/api/v1/bulk-jobs", files={"file": ("a.csv", data, "text/csv")},
                    headers={"Idempotency-Key": "bulk-k1"}).json()
    assert a["job_id"] == b["job_id"]


def test_reference_endpoints(client):
    hz = client.get("/api/v1/hazards").json()
    assert len(hz["hazards"]) == 25 and hz["hazards"][7]["rpn"] == "D5"
    st = client.get("/api/v1/settings").json()
    assert st["llm"]["model"] == "claude-opus-5" and st["llm"]["api_key_configured"] is False
    cm = client.get("/api/v1/usage/cost-model").json()
    assert {p["model"] for p in cm["projections"]} == {"claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"}
    assert client.get("/api/v1/bulk-jobs/sample.csv").status_code == 200


def test_auth_token_enforced(monkeypatch):
    from pydantic import SecretStr

    from app.settings import get_settings, set_settings

    s = get_settings().model_copy(update={"api_auth_token": SecretStr("s3cret")})
    set_settings(s)
    try:
        with TestClient(create_app()) as c:
            assert c.get("/api/v1/jobs").status_code == 401
            assert c.get("/api/v1/jobs", headers={"Authorization": "Bearer s3cret"}).status_code == 200
            assert c.get("/api/v1/health").status_code == 200
    finally:
        set_settings(None)


def test_docs_urls_and_redirects(client):
    """The docs live under /api; /docs and / redirect there instead of 404-ing."""
    assert client.get("/api/docs").status_code == 200
    assert client.get("/api/openapi.json").status_code == 200
    for path in ("/", "/docs", "/openapi.json"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code in (307, 308), path
        assert r.headers["location"].startswith("/api/"), path
