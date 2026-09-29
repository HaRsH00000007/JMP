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


def test_elevation_sampling_is_capped_on_long_routes(library, providers, db, monkeypatch):
    """Elevation is billed per coordinate, so a long route at 250 m is what exhausts an hourly quota.

    146 km at 250 m is 585 points; a 5,000/hour free tier then covers eight routes, which is how a 55-row
    run lost its remaining rows to "Hourly API request limit exceeded". Long routes sample coarser, and the
    document says so, because it does change what the elevation profile can resolve.
    """
    from app.services.pipeline import JourneyOptions, compute_facts

    asked: list[float] = []
    real = providers.elevation.profile

    def spy(polyline, sample_m):
        asked.append(sample_m)
        return real(polyline, sample_m)

    monkeypatch.setattr(providers.elevation, "profile", spy)
    facts = compute_facts(["Pyraganda", "Ranaghat"], JourneyOptions(), providers, library, db)
    km = facts.route.distance_km
    assert asked, "elevation was never requested"
    assert km * 1000 / asked[-1] <= 201, f"{km:.0f} km asked for {km * 1000 / asked[-1]:.0f} samples"
    if asked[-1] > 250:
        assert any("stay inside the provider's request budget" in w for w in facts.route.provider_warnings)


def test_area_fragments_never_offer_facility_road_or_person_words():
    """The area-level fallback may only try area names found in the stop's own text — never a hospital,
    a road ("Fatehabad Road" is not the town of Fatehabad), a number or the city itself."""
    from app.services.route_service import _area_fragments

    frags = _area_fragments("Shanti manglik Hospital Fatehabad Road Tajganj agra", "Agra")
    assert "Tajganj" in frags
    assert not any(w in f.lower() for f in frags for w in ("hospital", "road", "fatehabad", "agra"))
    assert _area_fragments("Dr bharat sethi / kankhal / pilot baba hospital", "Haridwar")[0] == "kankhal"
    assert all(not any(ch.isdigit() for ch in f) for f in _area_fragments("93/7 Avas vikas colony sikandra Agra"))


def test_a_stop_is_never_placed_far_from_its_city():
    """With a city hint, every accepted stop must lie within CITY_RADIUS_M of that city — a same-named road or
    hospital elsewhere in India ("Church road" in Coimbatore for a Ramnad route) is rejected, not used."""
    from app.domain.route import GeocodeResult
    from app.errors import GeocodeError
    from app.services import route_service as rs

    class FakeGeocoder:
        name = "fake"
        places = {
            "ramnad": GeocodeResult(query="Ramnad", name="Ramanathapuram", lat=9.36, lng=78.83,
                                    place_types=["place", "city"], confidence=0.95, provider="fake"),
            "church road, ramnad": GeocodeResult(query="x", name="Church Road", lat=10.99, lng=76.99,
                                                 place_types=["highway", "residential"], confidence=0.95,
                                                 provider="fake"),
        }

        def geocode(self, query, region_hint=None):
            g = self.places.get(query.strip().lower())
            if g is None:
                raise GeocodeError(f"Location not found: {query!r}")
            return g

    providers = type("P", (), {"geocoder": FakeGeocoder()})()
    g = rs.geocode_one("Church road", providers, None, "Ramnad", area_fallback=True)
    assert g.name.startswith("Ramanathapuram") and g.confidence <= rs.APPROXIMATE_CONFIDENCE


def test_html_copy_is_kept_out_of_the_documents_folder():
    """The documents folder is the hand-over folder and holds only PDFs; the HTML audit copy goes to html/."""
    from app.storage import html_key_for

    assert html_key_for("documents/2026/23_9_26/R041_DAN-JMP-TN-015_01c0f245") == \
        "html/2026/23_9_26/R041_DAN-JMP-TN-015_01c0f245.html"


def test_a_stop_that_lands_in_another_state_is_not_routed_to(library, providers, db, monkeypatch):
    """Confidence cannot catch a good match for the wrong place.

    On a Jalandhar day trip, "Railway station" resolved to Chennai and "Town market" to a Kerala village at
    0.95 confidence. Both were accepted, turning a local itinerary into a 3,200 km route through three
    states — with every distance, segment and hazard position taken from it.
    """
    from app.domain.route import GeocodeResult
    from app.services import route_service
    from app.services.pipeline import JourneyOptions, compute_facts

    pts = {
        "Jalandhar":      (31.326, 75.576),   # Punjab
        "Jandu singha":   (31.271, 75.700),   # Punjab, ~13 km away
        "bus stand":      (31.340, 75.580),   # Punjab
        "Railway station": (13.082, 80.270),  # Chennai — 2,300 km away, confidently matched
        "Town market":    (11.600, 76.100),   # Kerala — likewise
    }

    def fake(text, providers_, session=None, city_hint=None, *, area_fallback=False):
        lat, lng = pts[text]
        return GeocodeResult(query=text, name=text, lat=lat, lng=lng, provider="test", confidence=0.95)

    monkeypatch.setattr(route_service, "geocode_one", fake)
    facts = compute_facts(list(pts), JourneyOptions(), providers, library, db, allow_unverified_stops=True)

    routed = [w.input_text for w in facts.route.waypoints]
    assert routed == ["Jalandhar", "Jandu singha", "bus stand"], routed
    rejected = {u.input_text for u in facts.route.unverified_stops}
    assert rejected == {"Railway station", "Town market"}
    assert any("km from the nearest other stop" in u.reason for u in facts.route.unverified_stops)
    assert facts.route.distance_km < 100, "the cross-country legs are still in the measurement"


def test_outlier_guard_leaves_a_genuine_long_journey_alone(library, providers, db, monkeypatch):
    """Stops spread along a real long route are each near a neighbour, so none is an outlier."""
    from app.domain.route import GeocodeResult
    from app.services import route_service

    chain = [("A", 28.6, 77.2), ("B", 28.9, 77.6), ("C", 29.3, 78.1), ("D", 29.8, 78.5)]

    def fake(text, providers_, session=None, city_hint=None, *, area_fallback=False):
        lat, lng = next((la, ln) for nm, la, ln in chain if nm == text)
        return GeocodeResult(query=text, name=text, lat=lat, lng=lng, provider="test", confidence=0.95)

    monkeypatch.setattr(route_service, "geocode_one", fake)
    out = route_service.geocode_best_effort([c[0] for c in chain], providers, None)
    assert [t for t in out.texts] == ["A", "B", "C", "D"]
    assert out.unverified == []


def test_a_wrong_start_is_caught_even_when_the_journey_returns_to_it(library, providers, db, monkeypatch):
    """A round trip repeats its start, and two copies of a wrong point sit 0 km apart.

    Measured against all stops, each copy's nearest neighbour is the other, so the pair looks perfectly
    well-connected and the error hides. "Harinagar" resolved to Madhya Pradesh on a round trip whose other
    stops were all in Delhi, and the plan came out at 1,010 km.
    """
    from app.domain.route import GeocodeResult
    from app.services import route_service

    pts = {"Harinagar": (23.2, 78.5),        # Madhya Pradesh — wrong, and used twice
           "Punjabi Bagh": (28.67, 77.13),   # Delhi
           "Paschim Vihar": (28.67, 77.10),  # Delhi
           "Tilak Nagar": (28.64, 77.09)}    # Delhi

    def fake(text, providers_, session=None, city_hint=None, *, area_fallback=False):
        lat, lng = pts[text]
        return GeocodeResult(query=text, name=text, lat=lat, lng=lng, provider="test", confidence=0.95)

    monkeypatch.setattr(route_service, "geocode_one", fake)
    out = route_service.geocode_best_effort(
        ["Harinagar", "Punjabi Bagh", "Paschim Vihar", "Tilak Nagar", "Harinagar"], providers, None)
    assert out.texts == ["Punjabi Bagh", "Paschim Vihar", "Tilak Nagar"], out.texts
    assert {u.input_text for u in out.unverified} == {"Harinagar"}
