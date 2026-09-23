"""Provider interfaces (architecture.md §3). Implementations must be stateless apart from HTTP clients."""

from __future__ import annotations

import time
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

import httpx

from app.domain.route import ElevationProfile, FeatureSet, GeocodeResult, Place, PlaceKind, RouteResult
from app.errors import ProviderError
from app.settings import settings

Coord = tuple[float, float]


@runtime_checkable
class Geocoder(Protocol):
    name: str

    def geocode(self, query: str, region_hint: str | None = None) -> GeocodeResult: ...


@runtime_checkable
class RouteProvider(Protocol):
    name: str

    def route(self, waypoints: Sequence[Coord], *, depart_at: datetime | None = None,
              alternatives: bool = False) -> RouteResult: ...


@runtime_checkable
class RoadFeatureProvider(Protocol):
    name: str

    def features_along(self, polyline: Sequence[Coord]) -> FeatureSet: ...


@runtime_checkable
class ElevationProvider(Protocol):
    name: str

    def profile(self, polyline: Sequence[Coord], sample_m: float) -> ElevationProfile: ...


@runtime_checkable
class PlacesProvider(Protocol):
    name: str

    def nearby(self, point: Coord, kind: PlaceKind, radius_m: int) -> list[Place]: ...


class HttpMixin:
    """Shared HTTP behaviour: timeouts, user agent, bounded retries on 429/5xx, no header logging."""

    _client: httpx.Client | None = None

    def http(self) -> httpx.Client:
        if self._client is None:
            s = settings()
            self._client = httpx.Client(timeout=s.provider_timeout_s,
                                        headers={"User-Agent": s.provider_user_agent})
        return self._client

    def request_json(self, method: str, url: str, *, attempts: int = 3, **kwargs: Any) -> Any:
        last: Exception | None = None
        for i in range(attempts):
            try:
                resp = self.http().request(method, url, **kwargs)
            except httpx.HTTPError as exc:  # network / timeout
                last = exc
            else:
                if resp.status_code in (429, 502, 503, 504):
                    last = ProviderError(f"{self.__class__.__name__}: HTTP {resp.status_code}")
                elif resp.status_code >= 400:
                    # Never echo the URL: it may carry an API key as a query parameter.
                    raise ProviderError(f"{self.__class__.__name__}: HTTP {resp.status_code}",
                                        details={"body": resp.text[:300]})
                else:
                    return resp.json()
            time.sleep(min(8.0, 1.5 * (2 ** i)))
        raise ProviderError(f"{self.__class__.__name__} unavailable: {type(last).__name__}")
