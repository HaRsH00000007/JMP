"""Coordinates supplied with the request (an app's place picker) are routed from as-is, never geocoded."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest

from app.domain.route import SUPPLIED_PROVIDER, GeocodeResult, SuppliedPoint
from app.errors import InputValidationError
from app.providers.fallback import OsmWithGoogleFallback
from app.schemas.journeys import normalize_request
from app.services.pipeline import JourneyOptions, compute_facts
from app.services.route_service import geocode_all, geocode_best_effort

# Close to, but deliberately not on, the demo gazetteer's own points: a geocoded stop would land elsewhere.
ZIRAKPUR = SuppliedPoint(lat=30.6400, lng=76.8200, state="Punjab", district="SAS Nagar (Mohali)")
DERA_BASSI = SuppliedPoint(lat=30.5950, lng=76.8450, state="Punjab")
LALRU = SuppliedPoint(lat=30.4880, lng=76.7990, state="Punjab")

OPTS = JourneyOptions(vehicle_type="4W", vehicle_type_specified=True, travel_date=date(2026, 12, 15),
                      depart_time="09:30")


@pytest.fixture()
def no_geocoding(providers: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """The provider set with a geocoder that fails the test if it is ever asked anything."""
    def refuse(query: str, region_hint: str | None = None) -> GeocodeResult:
        raise AssertionError(f"geocoder was called for {query!r}")
    monkeypatch.setattr(providers.geocoder, "geocode", refuse)
    return providers


def test_supplied_points_are_routed_without_any_geocoder_call(no_geocoding: Any, library: Any, db: Any) -> None:
    facts = compute_facts(["Zirakpur", "Dera Bassi", "Lalru"], OPTS, no_geocoding, library, db,
                          points=[ZIRAKPUR, DERA_BASSI, LALRU])
    wps = facts.route.waypoints
    assert [(w.lat, w.lng) for w in wps] == [(p.lat, p.lng) for p in (ZIRAKPUR, DERA_BASSI, LALRU)]
    assert all(w.geocode_provider == SUPPLIED_PROVIDER and w.geocode_confidence == 1.0 for w in wps)
    assert [w.input_text for w in wps] == ["Zirakpur", "Dera Bassi", "Lalru"]
    assert facts.route.distance_km > 0


def test_state_from_the_picker_feeds_the_regional_rules(no_geocoding: Any, library: Any, db: Any) -> None:
    """Punjab in December is fog season; without the state the national default has no fog months."""
    facts = compute_facts(["Zirakpur", "Dera Bassi", "Lalru"], OPTS, no_geocoding, library, db,
                          points=[ZIRAKPUR, DERA_BASSI, LALRU])
    assert facts.route.states == ["Punjab"]
    assert "fog" in facts.route.exposures.seasonal_flags
    assert facts.route.region_label.startswith("Punjab")


def test_wrong_address_does_not_override_supplied_coordinates(no_geocoding: Any, library: Any, db: Any) -> None:
    """The picker's point is authoritative: an address no geocoder could find is still routed from it."""
    facts = compute_facts(["Nowhere Clinic XYZ 404", "Dera Bassi", "Lalru"], OPTS, no_geocoding, library, db,
                          points=[ZIRAKPUR, DERA_BASSI, LALRU], allow_unverified_stops=True)
    first = facts.route.waypoints[0]
    assert (first.lat, first.lng) == (ZIRAKPUR.lat, ZIRAKPUR.lng)
    assert first.input_text == "Nowhere Clinic XYZ 404" and not facts.route.unverified_stops


def test_supplied_point_is_never_rejected_as_an_outlier(providers: Any) -> None:
    """A geocoded stop 1,000 km from the rest is dropped as a mis-resolution; a placed one is not."""
    far = SuppliedPoint(lat=19.0760, lng=72.8777, state="Maharashtra")  # Mumbai, ~1,400 km away
    best = geocode_best_effort(["Zirakpur", "Dera Bassi", "Client site", "Lalru"], providers,
                               points=[None, None, far, None])
    assert "Client site" in best.texts and not best.unverified
    assert best.located[2].provider == SUPPLIED_PROVIDER


def test_mixed_journey_geocodes_only_the_stops_without_points(providers: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str] = []
    real = providers.geocoder.geocode

    def spy(query: str, region_hint: str | None = None) -> GeocodeResult:
        asked.append(query)
        return real(query, region_hint)
    monkeypatch.setattr(providers.geocoder, "geocode", spy)
    out = geocode_all(["Zirakpur", "Dera Bassi", "Lalru"], providers, points=[ZIRAKPUR, None, LALRU])
    assert asked == ["Dera Bassi"]
    assert out[0].provider == SUPPLIED_PROVIDER and out[1].provider == "mock"


def test_osm_google_chain_is_bypassed_for_points_and_kept_for_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """No OSM or Google request (and no Google charge) for a placed stop; the fallback still serves text."""
    chain = OsmWithGoogleFallback()
    calls = {"osm": 0, "google": 0}

    def osm(query: str, region_hint: str | None = None) -> GeocodeResult:
        calls["osm"] += 1
        return GeocodeResult(query=query, name="weak OSM match", lat=30.5, lng=76.8, provider="nominatim",
                             confidence=0.4)

    def google(query: str, region_hint: str | None = None) -> GeocodeResult:
        calls["google"] += 1
        return GeocodeResult(query=query, name="Dera Bassi", lat=30.597, lng=76.843, provider="google",
                             confidence=0.99)
    monkeypatch.setattr(chain.primary, "geocode", osm)
    monkeypatch.setattr(chain.fallback, "geocode", google)
    providers = SimpleNamespace(geocoder=chain)

    out = geocode_all(["Zirakpur", "Lalru"], providers, points=[ZIRAKPUR, LALRU])  # type: ignore[arg-type]
    assert calls == {"osm": 0, "google": 0}
    assert all(g.provider == SUPPLIED_PROVIDER for g in out)

    out = geocode_all(["Zirakpur", "Dera Bassi"], providers, points=[ZIRAKPUR, None])  # type: ignore[arg-type]
    assert calls == {"osm": 1, "google": 1} and out[1].provider == "google"


BASE = {"start_location": "Zirakpur", "stops": ["Dera Bassi"], "end_location": "Lalru"}


def test_request_carries_points_aligned_with_locations() -> None:
    req = normalize_request({**BASE, "start_point": {"lat": 30.64, "lng": 76.82, "state": " Punjab "},
                             "stop_points": [None]})
    assert req.points == [SuppliedPoint(lat=30.64, lng=76.82, state="Punjab"), None, None]
    assert normalize_request(BASE).points is None


@pytest.mark.parametrize("extra, field", [
    ({"start_point": {"lat": 90.5, "lng": 76.8}}, "start_point.lat"),
    ({"end_point": {"lat": 30.5, "lng": -180.1}}, "end_point.lng"),
    ({"start_point": {"lat": "30.64", "lng": 76.8}}, "start_point.lat"),
    ({"start_point": {"lat": True, "lng": 76.8}}, "start_point.lat"),
    ({"start_point": {"lat": 30.64}}, "start_point.lng"),
    ({"start_point": {"lng": 76.8}}, "start_point.lat"),
    ({"stop_points": [None, None]}, "journey"),
])
def test_invalid_points_are_rejected(extra: dict[str, Any], field: str) -> None:
    with pytest.raises(InputValidationError) as exc:
        normalize_request({**BASE, **extra})
    assert any(d["field"] == field for d in exc.value.details)
