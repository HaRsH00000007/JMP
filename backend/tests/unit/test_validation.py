"""Journey validation and CSV validation."""

from __future__ import annotations

import pytest

from app.errors import InputValidationError
from app.schemas.journeys import normalize_request
from app.services.bulk_csv import parse_csv


def _req(**kw):
    body = {"start_location": "Zirakpur", "stops": ["Dera Bassi"], "end_location": "Lalru", **kw}
    return normalize_request(body)


def test_valid_journey_normalises_whitespace_and_keeps_order():
    r = normalize_request({"start_location": "  Paras   Downtown ", "stops": ["B  stop", "C stop"],
                           "end_location": "D end", "vehicle_type": "car", "depart_time": "9:05"})
    assert r.locations == ["Paras Downtown", "B stop", "C stop", "D end"]
    assert r.vehicle_type == "4W"
    assert r.depart_time == "09:05"


@pytest.mark.parametrize("body,field", [
    ({"start_location": "", "end_location": "Lalru"}, "start_location"),
    ({"start_location": "Zirakpur", "end_location": "ab"}, "end_location"),
    ({"start_location": "Zirakpur", "stops": [""], "end_location": "Lalru"}, "stops[0] is empty"),
    ({"start_location": "Zirakpur", "stops": ["Zirakpur"], "end_location": "Lalru"}, "identical"),
    ({"start_location": "Zirakpur", "end_location": "Lalru", "depart_time": "25:00"}, "depart_time"),
    ({"start_location": "Zirakpur", "end_location": "Lalru", "vehicle_type": "truck"}, "vehicle_type"),
    ({"start_location": "Zirakpur", "end_location": "Lalru", "unknown": 1}, "unknown"),
])
def test_invalid_journeys(body, field):
    with pytest.raises(InputValidationError) as ei:
        normalize_request(body)
    assert any(field in (d["field"] + " " + d["issue"]) for d in ei.value.details)


def test_too_many_stops():
    with pytest.raises(InputValidationError):
        _req(stops=[f"Stop {i}" for i in range(20)])


def test_round_trip_allowed():
    r = normalize_request({"start_location": "Pyraganda", "stops": ["Chakdah"], "end_location": "Pyraganda"})
    assert r.is_round_trip_text


def test_input_hash_stable_and_order_sensitive():
    a = _req()
    b = _req()
    c = normalize_request({"start_location": "Lalru", "stops": ["Dera Bassi"], "end_location": "Zirakpur"})
    assert a.input_hash() == b.input_hash() != c.input_hash()


# ------------------------------------------------------------------------------------------ CSV
def test_csv_with_aliases_gaps_and_errors():
    data = (
        "﻿Route ID,Starting Location,Stop 1,Stop 2,End Location,Vehicle Type\n"
        "R1,Zirakpur,Dera Bassi,,Lalru,4W\n"
        "R2,Zirakpur,,Dera Bassi,Lalru,\n"          # gap
        "R3,,,,Lalru,\n"                            # missing start
        "R4,Zirakpur,Dera Bassi,Lalru,Ambala,bike\n"
        ",,,,,\n"                                   # blank row ignored
    ).encode()
    p = parse_csv(data)
    assert len(p.rows) == 4
    assert [r.valid for r in p.rows] == [True, False, False, True]
    assert p.rows[0].request.stops == ["Dera Bassi"]
    assert "gap" in p.rows[1].errors[0]["issue"]
    assert p.rows[3].request.vehicle_type == "2W"
    s = p.summary()
    assert s["valid_rows"] == 2 and s["invalid_rows"] == 2


def test_csv_missing_required_columns():
    with pytest.raises(InputValidationError, match="missing required"):
        parse_csv(b"route_id,foo\n1,2\n")


def test_csv_duplicate_route_ids_invalid():
    p = parse_csv(b"route_id,start_location,end_location\nA,Zirakpur,Lalru\nA,Zirakpur,Ambala\n")
    assert not any(r.valid for r in p.rows)


def test_csv_rejects_non_utf8_and_empty():
    with pytest.raises(InputValidationError):
        parse_csv("start_location,end_location\nZür,Ä\n".encode("utf-16"))
    with pytest.raises(InputValidationError):
        parse_csv(b"")
    with pytest.raises(InputValidationError):
        parse_csv(b"start_location,end_location\n")


# ------------------------------------------------------------------------------------ storage layout
def test_storage_date_prefix_is_year_then_ddmmyy():
    """Outputs are grouped per day: documents/<YYYY>/<DD_MM_YY>/… (and bulk/<YYYY>/<DD_MM_YY>/<job>/…)."""
    from datetime import datetime, timezone

    from app.storage import date_prefix

    assert date_prefix(datetime(2026, 9, 23, tzinfo=timezone.utc)) == "2026/23_09_26"
    assert date_prefix(datetime(2027, 1, 5, tzinfo=timezone.utc)) == "2027/05_01_27"
    assert len(date_prefix().split("/")) == 2
