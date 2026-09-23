"""Geometry helpers, mock providers, route analysis, segmentation, stop order."""

from __future__ import annotations

import pytest

from app.domain import geo
from app.errors import ErrorCode, GeocodeError, RouteError
from app.providers.mock import MockGeocoder, MockRouteProvider
from app.providers.osm import categorize, parse_osrm_route
from app.services.route_service import geocode_all
from tests.conftest import PYRAGANDA, ZIRAKPUR


def test_haversine_and_polyline_decode():
    assert 111_000 < geo.haversine((0, 0), (1, 0)) < 111_400
    # Google's documented example polyline
    pts = geo.decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@")
    assert pts == [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]


def test_douglas_peucker_keeps_endpoints():
    line = [(0, 0), (0.0001, 0.5), (0, 1)]
    out = geo.douglas_peucker(line, 1_000_000)
    assert out[0] == line[0] and out[-1] == line[-1]


def test_route_index_projection():
    line = [(30.0, 76.0), (30.0, 76.1)]
    idx = geo.RouteIndex(line)
    d, off = idx.project((30.0005, 76.05))
    assert abs(d - idx.total / 2) < 50 and 40 < off < 70


def test_mock_geocoder_known_and_unknown():
    g = MockGeocoder().geocode("Danone Nutricia India Plant, Lalru, Punjab")
    assert g.state == "Punjab" and g.locality == "Lalru"
    with pytest.raises(GeocodeError) as ei:
        MockGeocoder().geocode("Atlantis Business Park, Nowhere")
    assert ei.value.code == ErrorCode.GEOCODE_NOT_FOUND


def test_mock_route_geometry_and_identical_waypoints():
    r = MockRouteProvider().route([(30.644, 76.818), (30.472, 76.804)])
    assert len(r.geometry) > 10 and r.distance_m > 15_000
    with pytest.raises(RouteError):
        MockRouteProvider().route([(30.644, 76.818), (30.644, 76.818)])


def test_geocode_all_preserves_order(providers):
    gs = geocode_all(ZIRAKPUR, providers)
    assert [g.locality for g in gs] == ["Zirakpur", "Zirakpur", "Dera Bassi", "Lalru"]


def test_route_facts_zirakpur(zirakpur_facts):
    r = zirakpur_facts.route
    assert [w.seq for w in r.waypoints] == [1, 2, 3, 4]
    assert [w.input_text for w in r.waypoints] == ZIRAKPUR  # stop order preserved exactly
    assert r.intermediate_stops == 2 and r.waypoint_count == 4 and not r.is_round_trip
    lo, hi = r.distance_range_km
    assert lo <= r.distance_km * 1.0 + 1 and hi >= r.distance_km
    assert sum(t.pct for t in r.road_types) == 100
    assert r.segments and r.segments[0].km_from == 0
    assert abs(r.segments[-1].km_to - r.distance_km) < 0.05
    for a, b in zip(r.segments, r.segments[1:]):
        assert a.km_to == b.km_from
    assert r.is_demo_data  # mock providers are always flagged


def test_round_trip_reference_loop(reference_facts):
    r = reference_facts.route
    assert r.is_round_trip and r.waypoint_count == 13 and r.intermediate_stops == 11
    assert r.route_name.endswith("Circular Loop")
    assert any(w.is_institutional for w in r.waypoints)  # Manipal Hospital
    assert r.exposures.level_crossings >= 1
    assert "West Bengal" in r.states


def test_osrm_parser_road_spans_and_categories():
    route = {"legs": [{"distance": 3000, "duration": 300, "steps": [
        {"distance": 1000, "name": "Main Road", "ref": "", "geometry": "_p~iF~ps|U_ulLnnqC",
         "maneuver": {"location": [-120.2, 38.5]}, "intersections": []},
        {"distance": 2000, "name": "Kalka Highway", "ref": "NH5", "geometry": "_ulLnnqC_mqNvxq`@",
         "maneuver": {"location": [-120.95, 40.7]}, "intersections": [{"classes": []}]},
    ]}]}
    legs = parse_osrm_route(route, precision=5)
    assert [s.category for s in legs[0].spans] == ["arterial", "national_highway"]
    assert categorize("motorway", None) == "expressway"
    assert categorize("primary", "SH 12") == "state_highway"
    assert categorize("residential", None) == "local"
    assert categorize("track", None, "dirt") == "unpaved"


def test_reference_loop_alternatives_are_provider_derived(reference_facts):
    for a in reference_facts.route.alternatives:
        assert a.id in ("A", "B") and len(a.geometry) >= 2 and len(a.primary_geometry) >= 2


def test_pyraganda_constant_is_test_data_only():
    # guard: the reference journey must never be hard-coded in production modules
    import pathlib

    app_dir = pathlib.Path(__file__).resolve().parents[2] / "app"
    offenders = [p for p in app_dir.rglob("*.py") if "Pyraganda" in p.read_text(encoding="utf-8")]
    assert offenders == [], offenders
    assert PYRAGANDA[0] == "Pyraganda"
