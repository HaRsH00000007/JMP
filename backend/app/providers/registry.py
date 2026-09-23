"""Selects provider implementations from settings (ROUTE_PROVIDER, GEOCODER, …)."""

from __future__ import annotations

from dataclasses import dataclass

from app.providers import commercial, mock, osm
from app.providers.base import ElevationProvider, Geocoder, PlacesProvider, RoadFeatureProvider, RouteProvider
from app.settings import settings


@dataclass
class Providers:
    geocoder: Geocoder
    router: RouteProvider
    features: RoadFeatureProvider | None
    elevation: ElevationProvider | None
    places: PlacesProvider | None

    @property
    def is_demo(self) -> bool:
        parts = [self.geocoder, self.router, self.features, self.elevation, self.places]
        return any(getattr(p, "name", "") == "mock" for p in parts if p is not None)

    def describe(self) -> dict[str, str]:
        def n(p: object) -> str:
            return getattr(p, "name", "none") if p is not None else "none"

        return {"geocoder": n(self.geocoder), "route": n(self.router), "features": n(self.features),
                "elevation": n(self.elevation), "places": n(self.places)}


_override: Providers | None = None


def set_providers(p: Providers | None) -> None:
    """Test hook."""
    global _override
    _override = p


def get_providers() -> Providers:
    if _override is not None:
        return _override
    s = settings()
    geocoder: Geocoder = {
        "mock": mock.MockGeocoder, "nominatim": osm.NominatimGeocoder,
        "google": commercial.GoogleGeocoder, "mapbox": commercial.MapboxGeocoder,
    }[s.geocoder]()
    router: RouteProvider = {
        "mock": mock.MockRouteProvider, "osrm": osm.OsrmRouteProvider,
        "google": commercial.GoogleRoutesProvider, "mapbox": commercial.MapboxRouteProvider,
    }[s.route_provider]()
    features = {"mock": mock.MockFeatureProvider, "overpass": osm.OverpassFeatureProvider, "none": None}[
        s.feature_provider]
    elevation = {"mock": mock.MockElevationProvider, "open_meteo": osm.OpenMeteoElevation, "none": None}[
        s.elevation_provider]
    places = {"mock": mock.MockPlacesProvider, "overpass": osm.OverpassPlacesProvider, "none": None}[
        s.places_provider]
    return Providers(geocoder=geocoder, router=router, features=features() if features else None,
                     elevation=elevation() if elevation else None, places=places() if places else None)
