"""Real-provider parsing with stubbed HTTP (no network): Nominatim, Overpass, Open-Meteo."""

from __future__ import annotations

import pytest

from app.errors import ErrorCode, GeocodeError
from app.providers import osm


def _stub(monkeypatch, cls, payload):
    calls = []

    def fake(self, method, url, **kw):
        calls.append((method, url, kw))
        return payload(kw) if callable(payload) else payload

    monkeypatch.setattr(cls, "request_json", fake)
    monkeypatch.setattr(osm._NOMINATIM_RL, "min_interval", 0)
    return calls


def test_nominatim_match_confidence_and_not_found(monkeypatch):
    _stub(monkeypatch, osm.NominatimGeocoder, [{
        "lat": "30.5882", "lon": "76.8439", "name": "Dera Bassi", "display_name": "Dera Bassi, SAS Nagar, Punjab, India",
        "importance": 0.14, "category": "place", "type": "town",
        "address": {"town": "Dera Bassi", "state": "Punjab", "state_district": "SAS Nagar"}}])
    g = osm.NominatimGeocoder().geocode("Dera Bassi, Punjab")
    assert g.state == "Punjab" and g.locality == "Dera Bassi" and g.confidence >= 0.9  # low importance is fine
    _stub(monkeypatch, osm.NominatimGeocoder, [])
    with pytest.raises(GeocodeError) as ei:
        osm.NominatimGeocoder().geocode("Nowhere")
    assert ei.value.code == ErrorCode.GEOCODE_NOT_FOUND


def test_nominatim_ambiguous(monkeypatch):
    _stub(monkeypatch, osm.NominatimGeocoder, [
        {"lat": "30.0", "lon": "76.0", "display_name": "Rampur, Punjab, India", "importance": 0.30,
         "address": {"village": "Rampur", "state": "Punjab"}},
        {"lat": "28.8", "lon": "79.0", "display_name": "Rampur, Uttar Pradesh, India", "importance": 0.31,
         "address": {"city": "Rampur", "state": "Uttar Pradesh"}}])
    with pytest.raises(GeocodeError) as ei:
        osm.NominatimGeocoder().geocode("Rampur")
    assert ei.value.code == ErrorCode.GEOCODE_AMBIGUOUS and len(ei.value.details["candidates"]) == 2


def test_overpass_features_and_road_samples(monkeypatch):
    route = [(30.0000, 76.0000), (30.0000, 76.0200)]
    before = [{"lat": 30.0, "lon": 76.0 + i * 0.001} for i in range(11)]           # 76.000 … 76.010
    after = [{"lat": 30.0, "lon": 76.011 + i * 0.001} for i in range(10)]          # 76.011 … 76.020
    payload = {"elements": [
        {"type": "way", "id": 1, "tags": {"highway": "trunk", "ref": "NH7", "maxspeed": "80"}, "geometry": before},
        {"type": "way", "id": 3, "tags": {"highway": "trunk", "ref": "NH7", "maxspeed": "80"}, "geometry": after},
        {"type": "way", "id": 2, "tags": {"highway": "trunk", "ref": "NH7", "bridge": "yes"},
         "geometry": [{"lat": 30.0, "lon": 76.010}, {"lat": 30.0, "lon": 76.011}]},
        {"type": "node", "id": 10, "lat": 30.0, "lon": 76.005, "tags": {"railway": "level_crossing", "crossing:barrier": "no"}},
        {"type": "node", "id": 11, "lat": 30.0, "lon": 76.012, "tags": {"highway": "traffic_signals"}},
        {"type": "node", "id": 12, "lat": 30.001, "lon": 76.015, "tags": {"place": "town", "name": "Testpur"}},
    ]}
    _stub(monkeypatch, osm.OverpassFeatureProvider, payload)
    fs = osm.OverpassFeatureProvider().features_along(route)
    kinds = {f.kind for f in fs.features}
    assert {"level_crossing", "busy_junction", "bridge", "settlement", "built_up"} <= kinds
    lc = next(f for f in fs.features if f.kind == "level_crossing")
    assert lc.lat == 30.0 and lc.tags["crossing:barrier"] == "no"  # node coordinates present (out body)
    assert fs.road_samples and all(s.highway == "trunk" for s in fs.road_samples)
    assert osm.categorize(fs.road_samples[0].highway, fs.road_samples[0].ref) == "national_highway"


def test_open_meteo_profile(monkeypatch):
    _stub(monkeypatch, osm.OpenMeteoElevation,
          lambda kw: {"elevation": [300.0 + i for i in range(len(kw["params"]["latitude"].split(",")))]})
    prof = osm.OpenMeteoElevation().profile([(30.0, 76.0), (30.0, 76.05)], 250)
    assert len(prof.samples) > 10 and prof.samples[0][1] == 300.0


def test_same_name_town_and_district_is_not_ambiguous(monkeypatch):
    """A town and its own district share a name and sit a few km apart — that is not ambiguity."""
    _stub(monkeypatch, osm.NominatimGeocoder, [
        {"lat": "29.9457", "lon": "78.1642", "display_name": "Haridwar, Uttarakhand, India", "importance": 0.55,
         "address": {"city": "Haridwar", "state": "Uttarakhand"}},
        {"lat": "29.8700", "lon": "78.1000", "display_name": "Haridwar District, Uttarakhand, India",
         "importance": 0.55, "address": {"state_district": "Haridwar", "state": "Uttarakhand"}}])
    g = osm.NominatimGeocoder().geocode("Haridwar")
    assert g.state == "Uttarakhand" and g.confidence >= 0.9


def test_result_outside_the_named_city_is_rejected(monkeypatch):
    """"Apex Hospital, Agra" must not be accepted as a hospital in Nashik — even though that hospital sits
    on a road called "Agra Mumbai Road", which a display_name substring check would match."""
    _stub(monkeypatch, osm.NominatimGeocoder, [
        {"lat": "20.0176", "lon": "73.8268", "importance": 0.3,
         "display_name": "Apex Superspeciality Hospital, Agra Mumbai Road Flyover Ramp, Nashik, Maharashtra",
         "address": {"road": "Agra Mumbai Road Flyover Ramp", "city": "Nashik", "state": "Maharashtra"}}])
    g = osm.NominatimGeocoder().geocode("Apex Hospital, Agra")
    assert g.confidence < 0.5, "a result outside the named city must fall below the acceptance threshold"


def test_geocode_all_rejects_low_confidence(monkeypatch):
    """The pipeline turns a below-threshold match into GEOCODE_AMBIGUOUS instead of routing to it."""
    from types import SimpleNamespace

    from app.domain.route import GeocodeResult
    from app.errors import ErrorCode, JmpError
    from app.services.route_service import geocode_all

    fake = SimpleNamespace(name="fake", geocode=lambda q, hint=None: GeocodeResult(
        query=q, name="Somewhere Else", lat=20.0, lng=73.8, provider="fake", confidence=0.3,
        candidates=[{"name": "Somewhere Else"}]))
    with pytest.raises(JmpError) as ei:
        geocode_all(["Apex Hospital, Agra"], SimpleNamespace(geocoder=fake))
    assert ei.value.code == ErrorCode.GEOCODE_AMBIGUOUS
