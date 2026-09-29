"""OSM-first, Google-fallback geocoding: when the fallback fires and when it must not."""

from __future__ import annotations

import pytest

from app.domain.route import GeocodeResult
from app.errors import ErrorCode, GeocodeError, ProviderError
from app.providers.fallback import OsmWithGoogleFallback
from app.settings import get_settings, set_settings


def _result(name: str, provider: str, confidence: float) -> GeocodeResult:
    return GeocodeResult(query="q", name=name, lat=27.2, lng=78.0, provider=provider, confidence=confidence)


@pytest.fixture
def geo(monkeypatch):
    g = OsmWithGoogleFallback()
    calls: dict[str, int] = {"osm": 0, "google": 0}

    def osm_returns(value):
        def f(query, region_hint=None):
            calls["osm"] += 1
            if isinstance(value, Exception):
                raise value
            return value
        monkeypatch.setattr(g.primary, "geocode", f)

    def google_returns(value):
        def f(query, region_hint=None):
            calls["google"] += 1
            if isinstance(value, Exception):
                raise value
            return value
        monkeypatch.setattr(g.fallback, "geocode", f)

    return g, calls, osm_returns, google_returns


def test_confident_osm_match_never_reaches_google(geo):
    """Google is billed per request, so a good OSM answer must not cost anything."""
    g, calls, osm_returns, google_returns = geo
    osm_returns(_result("Zirakpur", "nominatim", 0.95))
    google_returns(_result("Zirakpur", "google", 0.99))
    out = g.geocode("Zirakpur")
    assert out.provider == "nominatim" and calls["google"] == 0


def test_low_confidence_falls_through(geo):
    """The failure that matters: OSM answers confidently wrong rather than not at all.

    "Apex Hospital, Agra" returned a hospital in Nashik, 1,000 km away. That is a low-confidence match,
    not an error, so a not-found-only fallback would keep the wrong coordinate.
    """
    g, calls, osm_returns, google_returns = geo
    osm_returns(_result("Apex Hospital, Nashik", "nominatim", 0.6))
    google_returns(_result("Apex Hospital, Agra", "google", 0.99))
    out = g.geocode("Apex Hospital, Agra")
    assert out.provider == "google" and calls["google"] == 1


def test_not_found_and_ambiguous_fall_through(geo):
    g, calls, osm_returns, google_returns = geo
    for err in (GeocodeError("nope", code=ErrorCode.GEOCODE_NOT_FOUND),
                GeocodeError("which?", code=ErrorCode.GEOCODE_AMBIGUOUS)):
        calls["google"] = 0
        osm_returns(err)
        google_returns(_result("Gadinglaj", "google", 0.99))
        assert g.geocode("Gadinglaj").provider == "google"
        assert calls["google"] == 1


def test_unusable_google_keeps_the_weak_osm_match(geo):
    """Enabling the fallback must never resolve fewer addresses than leaving it off."""
    g, calls, osm_returns, google_returns = geo
    osm_returns(_result("Somewhere", "nominatim", 0.6))
    google_returns(ProviderError("ROUTE_PROVIDER_API_KEY is not configured"))
    out = g.geocode("Somewhere")
    assert out.provider == "nominatim" and out.confidence == 0.6


def test_both_fail_reports_googles_error(geo):
    g, calls, osm_returns, google_returns = geo
    osm_returns(GeocodeError("nope", code=ErrorCode.GEOCODE_NOT_FOUND))
    google_returns(GeocodeError("still nope", code=ErrorCode.GEOCODE_NOT_FOUND))
    with pytest.raises(GeocodeError):
        g.geocode("Prayag nursing , Phulpoor")


def test_confidence_floor_is_configurable(geo):
    g, calls, osm_returns, google_returns = geo
    osm_returns(_result("Somewhere", "nominatim", 0.85))
    google_returns(_result("Somewhere", "google", 0.99))
    assert g.geocode("Somewhere").provider == "nominatim"      # 0.85 >= default floor 0.80
    set_settings(get_settings().model_copy(update={"geocode_fallback_min_confidence": 0.9}))
    try:
        assert g.geocode("Somewhere").provider == "google"     # same match, stricter floor
    finally:
        set_settings(None)


def test_cache_namespace_is_its_own():
    """Reusing the plain OSM namespace would serve back low-confidence hits cached before this existed."""
    assert OsmWithGoogleFallback.name == "osm_google"
