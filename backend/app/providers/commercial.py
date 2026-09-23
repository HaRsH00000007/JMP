"""Google Maps Platform and Mapbox providers (require ROUTE_PROVIDER_API_KEY).

These follow the providers' documented REST APIs but have NOT been exercised against live keys in this
repository (no credentials were available at build time) — see docs/troubleshooting.md. The key is sent
only in headers or query parameters and is never logged (HttpMixin never echoes URLs).

Licensing (D-09): Google terms restrict caching Google content and displaying it on non-Google maps. The
PDF draws a schematic SVG from route geometry — confirm this is permitted under your Google agreement
before enabling ROUTE_PROVIDER=google in production. Mapbox/OSM-based routing avoids the question.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from app.domain import geo
from app.domain.route import AlternativeRoute, GeocodeResult, RoadSpan, RouteLeg, RouteResult
from app.errors import ErrorCode, GeocodeError, ProviderError, RouteError
from app.providers.base import HttpMixin
from app.providers.osm import categorize, parse_osrm_route
from app.settings import settings

Coord = tuple[float, float]
_REF_RE = re.compile(r"\b(NH|SH)[\s-]*(\d+[A-Z]?)\b", re.I)


def _key() -> str:
    k = settings().route_provider_api_key
    if k is None or not k.get_secret_value():
        raise ProviderError("ROUTE_PROVIDER_API_KEY is not configured", code=ErrorCode.PROVIDER_UNAVAILABLE)
    return k.get_secret_value()


# ------------------------------------------------------------------------------------------- Google
class GoogleGeocoder(HttpMixin):
    name = "google"

    def geocode(self, query: str, region_hint: str | None = None) -> GeocodeResult:
        data = self.request_json("GET", "https://maps.googleapis.com/maps/api/geocode/json",
                                 params={"address": query, "region": region_hint or settings().geocode_region_hint,
                                         "key": _key()})
        status = data.get("status")
        if status == "ZERO_RESULTS":
            raise GeocodeError(f"Location not found: {query!r}", code=ErrorCode.GEOCODE_NOT_FOUND)
        if status != "OK":
            raise ProviderError(f"Google geocoding status {status}")
        results = data["results"]
        top = results[0]
        cands = [{"name": r["formatted_address"], "lat": r["geometry"]["location"]["lat"],
                  "lng": r["geometry"]["location"]["lng"]} for r in results[:5]]
        if len(results) > 1 and top.get("partial_match"):
            raise GeocodeError(f"Ambiguous location: {query!r}", code=ErrorCode.GEOCODE_AMBIGUOUS,
                               details={"candidates": cands})
        comps = {t: c["long_name"] for c in top["address_components"] for t in c["types"]}
        loc_type = top["geometry"].get("location_type")
        conf = {"ROOFTOP": 0.99, "RANGE_INTERPOLATED": 0.9, "GEOMETRIC_CENTER": 0.8}.get(loc_type, 0.6)
        if top.get("partial_match"):
            conf = min(conf, 0.6)
        return GeocodeResult(
            query=query, name=top["formatted_address"].split(",")[0],
            lat=top["geometry"]["location"]["lat"], lng=top["geometry"]["location"]["lng"],
            state=comps.get("administrative_area_level_1"),
            district=comps.get("administrative_area_level_3") or comps.get("administrative_area_level_2"),
            locality=comps.get("locality") or comps.get("sublocality"),
            place_types=top.get("types", []), confidence=conf, provider=self.name, candidates=cands)


class GoogleRoutesProvider(HttpMixin):
    """Routes API v2 computeRoutes. Google computes alternatives only when there are no intermediates."""

    name = "google"
    FIELD_MASK = ",".join([
        "routes.distanceMeters", "routes.duration", "routes.legs.distanceMeters", "routes.legs.duration",
        "routes.legs.steps.distanceMeters", "routes.legs.steps.polyline.encodedPolyline",
        "routes.legs.steps.navigationInstruction.instructions", "routes.legs.steps.startLocation",
    ])

    def route(self, waypoints: Sequence[Coord], *, depart_at: datetime | None = None,
              alternatives: bool = False) -> RouteResult:
        def wp(c: Coord) -> dict[str, Any]:
            return {"location": {"latLng": {"latitude": c[0], "longitude": c[1]}}}

        body: dict[str, Any] = {
            "origin": wp(waypoints[0]), "destination": wp(waypoints[-1]),
            "intermediates": [wp(c) for c in waypoints[1:-1]],
            "travelMode": "DRIVE", "routingPreference": "TRAFFIC_UNAWARE",
            "computeAlternativeRoutes": bool(alternatives and len(waypoints) == 2),
            "languageCode": "en-IN", "units": "METRIC",
        }
        data = self.request_json("POST", "https://routes.googleapis.com/directions/v2:computeRoutes", json=body,
                                 headers={"X-Goog-Api-Key": _key(), "X-Goog-FieldMask": self.FIELD_MASK})
        routes = data.get("routes") or []
        if not routes:
            raise RouteError("Google could not route between the waypoints", code=ErrorCode.ROUTE_NOT_FOUND)
        legs = self._legs(routes[0])
        alts = []
        for r in routes[1:]:
            alegs = self._legs(r)
            geom: list[Coord] = []
            for leg in alegs:
                geom.extend(leg.geometry if not geom else leg.geometry[1:])
            alts.append(AlternativeRoute(distance_m=float(r.get("distanceMeters", 0)),
                                         duration_s=float(str(r.get("duration", "0s")).rstrip("s")),
                                         geometry=geom))
        return RouteResult(provider=self.name, legs=legs, alternatives=alts)

    def _legs(self, route: dict[str, Any]) -> list[RouteLeg]:
        legs = []
        for i, leg in enumerate(route.get("legs", [])):
            geom: list[Coord] = []
            spans: list[RoadSpan] = []
            man: list[Coord] = []
            pos = 0.0
            for st in leg.get("steps", []):
                pts = geo.decode_polyline(st.get("polyline", {}).get("encodedPolyline", ""), 5)
                geom.extend(pts if not geom else pts[1:])
                sl = st.get("startLocation", {}).get("latLng")
                if sl:
                    man.append((sl["latitude"], sl["longitude"]))
                instr = st.get("navigationInstruction", {}).get("instructions", "")
                m = _REF_RE.search(instr)
                ref = f"{m.group(1).upper()}-{m.group(2)}" if m else None
                cat = categorize(None, ref)
                cat = cat if cat != "unknown" else "local"
                dist = float(st.get("distanceMeters", 0))
                if dist:
                    if spans and spans[-1].category == cat and spans[-1].ref == ref:
                        spans[-1].end_m = pos + dist
                    else:
                        spans.append(RoadSpan(start_m=pos, end_m=pos + dist, category=cat, ref=ref))
                pos += dist
            legs.append(RouteLeg(from_seq=i, to_seq=i + 1, distance_m=float(leg.get("distanceMeters", 0)),
                                 duration_s=float(str(leg.get("duration", "0s")).rstrip("s")), geometry=geom,
                                 maneuvers=man, spans=spans))
        if not any(leg.geometry for leg in legs):
            raise RouteError("Route geometry unavailable", code=ErrorCode.ROUTE_GEOMETRY_UNAVAILABLE)
        return legs


# ------------------------------------------------------------------------------------------- Mapbox
class MapboxGeocoder(HttpMixin):
    name = "mapbox"

    def geocode(self, query: str, region_hint: str | None = None) -> GeocodeResult:
        data = self.request_json("GET", "https://api.mapbox.com/search/geocode/v6/forward",
                                 params={"q": query, "country": region_hint or settings().geocode_region_hint,
                                         "limit": 5, "access_token": _key()})
        feats = data.get("features") or []
        if not feats:
            raise GeocodeError(f"Location not found: {query!r}", code=ErrorCode.GEOCODE_NOT_FOUND)
        top = feats[0]
        props = top.get("properties", {})
        ctx = props.get("context", {})
        lng, lat = top["geometry"]["coordinates"]
        conf = {"exact": 0.99, "high": 0.9, "medium": 0.7, "low": 0.5}.get(
            props.get("match_code", {}).get("confidence", "medium"), 0.7)
        cands = [{"name": f["properties"].get("full_address") or f["properties"].get("name"),
                  "lat": f["geometry"]["coordinates"][1], "lng": f["geometry"]["coordinates"][0]} for f in feats]
        if conf <= 0.5 and len(feats) > 1:
            raise GeocodeError(f"Ambiguous location: {query!r}", code=ErrorCode.GEOCODE_AMBIGUOUS,
                               details={"candidates": cands})
        return GeocodeResult(query=query, name=props.get("name", query), lat=lat, lng=lng,
                             state=ctx.get("region", {}).get("name"), district=ctx.get("district", {}).get("name"),
                             locality=ctx.get("place", {}).get("name"), place_types=[props.get("feature_type", "")],
                             confidence=conf, provider=self.name, candidates=cands)


class MapboxRouteProvider(HttpMixin):
    name = "mapbox"

    def route(self, waypoints: Sequence[Coord], *, depart_at: datetime | None = None,
              alternatives: bool = False) -> RouteResult:
        if len(waypoints) > 25:
            raise RouteError("Mapbox Directions supports at most 25 waypoints")
        coords = ";".join(f"{lng:.6f},{lat:.6f}" for lat, lng in waypoints)
        data = self.request_json(
            "GET", f"https://api.mapbox.com/directions/v5/mapbox/driving/{coords}",
            params={"steps": "true", "geometries": "polyline6", "overview": "false",
                    "alternatives": "true" if alternatives and len(waypoints) == 2 else "false",
                    "access_token": _key()})
        if data.get("code") != "Ok" or not data.get("routes"):
            raise RouteError(f"Mapbox could not route: {data.get('code')}", code=ErrorCode.ROUTE_NOT_FOUND)
        legs = parse_osrm_route(data["routes"][0])
        alts = []
        for r in data["routes"][1:]:
            alegs = parse_osrm_route(r)
            geom: list[Coord] = []
            for leg in alegs:
                geom.extend(leg.geometry if not geom else leg.geometry[1:])
            alts.append(AlternativeRoute(distance_m=float(r["distance"]), duration_s=float(r["duration"]),
                                         geometry=geom))
        return RouteResult(provider=self.name, legs=legs, alternatives=alts)
