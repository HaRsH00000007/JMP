"""Real providers built on open data, usable without API keys:

* NominatimGeocoder      — OSM geocoding (usage policy: ≤1 req/s, identify with a User-Agent)
* OsrmRouteProvider      — OSRM routing (public demo server or self-hosted)
* OverpassFeatureProvider— OSM road attributes & point/area features along the route
* OverpassPlacesProvider — hospitals / police / fuel near a point
* OpenMeteoElevation     — Copernicus DEM elevation via Open-Meteo

Public endpoints are for development/demo volumes; production bulk use should point the *_URL settings
at self-hosted OSRM / Nominatim / Overpass instances (see docs/deployment.md).
"""

from __future__ import annotations

import math
import re
import threading
import time
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from app.domain import geo
from app.domain.route import (
    AlternativeRoute,
    ElevationProfile,
    FeatureSet,
    GeocodeResult,
    Place,
    PlaceKind,
    RoadFeature,
    RoadSample,
    RoadSpan,
    RouteLeg,
    RouteResult,
)
from app.errors import ErrorCode, GeocodeError, ProviderError, RouteError
from app.providers.base import HttpMixin
from app.rules_config import hazard_rules
from app.settings import settings

Coord = tuple[float, float]
_REF_RE = re.compile(r"\b(NH|SH|MDR)[\s-]*(\d+[A-Z]?)\b", re.I)


def categorize(highway: str | None, ref: str | None, surface: str | None = None) -> str:
    """Map OSM highway/ref/surface to a road category using config/hazard_rules.yaml."""
    cats = hazard_rules()["road_categories"]
    refs = [r.strip().upper() for r in re.split(r"[;,]", ref or "") if r.strip()]
    if highway in cats["expressway"]["highway"]:
        return "expressway"
    if any(r.startswith("NH") for r in refs) or highway in cats["national_highway"]["highway"]:
        return "national_highway"
    if any(r.startswith("SH") for r in refs):
        return "state_highway"
    if surface and surface in cats["unpaved"].get("surfaces", []):
        return "unpaved"
    for key in ("arterial", "local", "unpaved"):
        if highway in cats[key].get("highway", []):
            return key
    return "unknown"


class _RateLimiter:
    def __init__(self, min_interval_s: float) -> None:
        self.min_interval = min_interval_s
        self._last = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            delta = time.monotonic() - self._last
            if delta < self.min_interval:
                time.sleep(self.min_interval - delta)
            self._last = time.monotonic()


_NOMINATIM_RL = _RateLimiter(1.1)
# overpass-api.de publishes a 2-slot-per-IP limit (see /api/status). Exceeding it means queued or rejected
# queries, which in a bulk run shows up as everything crawling. Gate ourselves to stay inside it.
_OVERPASS_SLOTS = threading.Semaphore(2)


class NominatimGeocoder(HttpMixin):
    name = "nominatim"

    def geocode(self, query: str, region_hint: str | None = None) -> GeocodeResult:
        s = settings()
        _NOMINATIM_RL.wait()
        params = {"q": query, "format": "jsonv2", "addressdetails": 1, "limit": 5}
        if region_hint or s.geocode_region_hint:
            params["countrycodes"] = region_hint or s.geocode_region_hint
        data = self.request_json("GET", f"{s.nominatim_base_url}/search", params=params)
        if not data:
            raise GeocodeError(f"Location not found: {query!r}", code=ErrorCode.GEOCODE_NOT_FOUND)
        top = data[0]
        cands = [
            {"name": d.get("display_name"), "lat": float(d["lat"]), "lng": float(d["lon"]),
             "importance": d.get("importance")}
            for d in data[:5]
        ]
        # D-17: genuinely ambiguous = two comparable candidates in DIFFERENT administrative areas (the
        # "Apex Hospital, Agra -> Nashik" case). A town and its own district share a name and sit a few km
        # apart with near-identical importance; that is not ambiguity, so compare the state/region first.
        if len(data) > 1:
            def _area(d: dict[str, Any]) -> str:
                a = d.get("address", {})
                return (a.get("state") or a.get("region") or a.get("country") or "").strip().lower()

            d0 = geo.haversine((float(data[0]["lat"]), float(data[0]["lon"])),
                               (float(data[1]["lat"]), float(data[1]["lon"])))
            imp0 = float(data[0].get("importance") or 0)
            imp1 = float(data[1].get("importance") or 0)
            different_area = _area(data[0]) != _area(data[1])
            if d0 > 5000 and abs(imp0 - imp1) < 0.02 and different_area:
                raise GeocodeError(f"Ambiguous location: {query!r}", code=ErrorCode.GEOCODE_AMBIGUOUS,
                                   details={"candidates": cands})
        addr = top.get("address", {})
        # Nominatim's `importance` is popularity, not match quality: judge confidence by whether the returned
        # place actually contains the queried place name (< 0.8 adds an "exact location" verification item).
        display = (top.get("display_name") or "").lower()
        wanted = query.split(",")[0].strip().lower()
        matched = wanted and wanted in display
        confidence = 0.95 if matched else 0.6
        # Context check: if the query names a place ("…, Agra"), the result must actually be in it.
        # Without this, "Apex Hospital, Agra" happily returns a hospital in Nashik, 1,000 km away.
        # Match against the structured place fields only — the free-text display_name includes road names,
        # and the Nashik hospital above sits on a road literally called "Agra Mumbai Road".
        place_fields = ("city", "town", "village", "municipality", "suburb", "city_district", "county",
                        "state_district", "district", "state", "region")
        area_text = " | ".join(str(addr[k]).lower() for k in place_fields if addr.get(k))
        context = [c.strip().lower() for c in query.split(",")[1:] if len(c.strip()) > 2]
        if context and not any(c in area_text for c in context):
            confidence = 0.3  # below the acceptance threshold -> reported with candidates, never used silently
        return GeocodeResult(
            query=query,
            name=top.get("name") or top.get("display_name", query).split(",")[0],
            lat=float(top["lat"]), lng=float(top["lon"]),
            state=addr.get("state"),
            district=addr.get("state_district") or addr.get("county"),
            locality=addr.get("city") or addr.get("town") or addr.get("village") or addr.get("suburb"),
            place_types=[t for t in (top.get("category"), top.get("type")) if t],
            confidence=confidence,
            provider=self.name,
            candidates=cands,
        )


def parse_osrm_route(route: dict[str, Any], from_seq0: int = 0, precision: int = 6) -> list[RouteLeg]:
    """Parse an OSRM-format route (OSRM and Mapbox Directions share it) into legs with road spans."""
    legs: list[RouteLeg] = []
    for i, leg in enumerate(route.get("legs", [])):
        geom: list[Coord] = []
        maneuvers: list[Coord] = []
        spans: list[RoadSpan] = []
        pos = 0.0
        for step in leg.get("steps", []):
            g = step.get("geometry")
            pts = geo.decode_polyline(g, precision) if isinstance(g, str) else []
            if pts:
                geom.extend(pts if not geom else pts[1:])
            loc = step.get("maneuver", {}).get("location")
            if loc:
                maneuvers.append((loc[1], loc[0]))
            dist = float(step.get("distance", 0))
            classes = set()
            for inter in step.get("intersections", []) or []:
                classes |= set(inter.get("classes", []) or [])
            ref = step.get("ref") or None
            name = step.get("name") or None
            if not ref and name and (m := _REF_RE.search(name)):
                ref = f"{m.group(1).upper()}-{m.group(2)}"
            highway = "motorway" if "motorway" in classes else None
            cat = categorize(highway, ref)
            if cat == "unknown":
                cat = "local" if not name else "arterial"
            if dist > 0:
                if spans and spans[-1].category == cat and spans[-1].ref == ref:
                    spans[-1].end_m = pos + dist
                else:
                    spans.append(RoadSpan(start_m=pos, end_m=pos + dist, category=cat, name=name, ref=ref))
            pos += dist
        legs.append(RouteLeg(from_seq=from_seq0 + i, to_seq=from_seq0 + i + 1,
                             distance_m=float(leg.get("distance", 0)), duration_s=float(leg.get("duration", 0)),
                             geometry=geom, maneuvers=maneuvers, spans=spans))
    return legs


def _via_names(alt_legs: list[RouteLeg], primary_legs: list[RouteLeg]) -> list[str]:
    prim = {(s.ref or s.name) for leg in primary_legs for s in leg.spans if (s.ref or s.name)}
    out: list[str] = []
    for leg in alt_legs:
        for s in sorted(leg.spans, key=lambda x: x.start_m - x.end_m):
            label = s.ref or s.name
            if label and label not in prim and label not in out:
                out.append(label)
    return out[:3]


class OsrmRouteProvider(HttpMixin):
    name = "osrm"

    def route(self, waypoints: Sequence[Coord], *, depart_at: datetime | None = None,
              alternatives: bool = False) -> RouteResult:
        if len(waypoints) < 2:
            raise RouteError("At least two waypoints are required")
        coords = ";".join(f"{lng:.6f},{lat:.6f}" for lat, lng in waypoints)
        params = {"overview": "false", "steps": "true", "geometries": "polyline6",
                  "alternatives": "true" if alternatives and len(waypoints) == 2 else "false"}
        data = self.request_json("GET", f"{settings().osrm_base_url}/route/v1/driving/{coords}", params=params)
        if data.get("code") != "Ok" or not data.get("routes"):
            raise RouteError(f"OSRM could not route: {data.get('code')}", code=ErrorCode.ROUTE_NOT_FOUND)
        legs = parse_osrm_route(data["routes"][0])
        if not any(leg.geometry for leg in legs):
            raise RouteError("Route geometry unavailable", code=ErrorCode.ROUTE_GEOMETRY_UNAVAILABLE)
        alts = []
        for r in data["routes"][1:]:
            alt_legs = parse_osrm_route(r)
            geom: list[Coord] = []
            for leg in alt_legs:
                geom.extend(leg.geometry if not geom else leg.geometry[1:])
            alts.append(AlternativeRoute(distance_m=float(r["distance"]), duration_s=float(r["duration"]),
                                         geometry=geom, via=_via_names(alt_legs, legs)))
        return RouteResult(provider=self.name, request_id=None, legs=legs, alternatives=alts)


# ------------------------------------------------------------------------------------------ Overpass
def _line_arg(coords: Sequence[Coord], max_points: int = 400) -> str:
    pts = geo.douglas_peucker(list(coords), 15.0)
    if len(pts) > max_points:
        step = math.ceil(len(pts) / max_points)
        pts = pts[::step] + [pts[-1]]
    return ",".join(f"{lat:.5f},{lng:.5f}" for lat, lng in pts)


def _point_in_polygon(p: Coord, poly: Sequence[Coord]) -> bool:
    x, y = p[1], p[0]
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i][1], poly[i][0]
        xj, yj = poly[j][1], poly[j][0]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


class _SegmentGrid:
    """Grid index of OSM way segments for nearest-way lookups."""

    def __init__(self, ways: list[dict[str, Any]], cell: float = 0.002) -> None:
        self.cell = cell
        self.grid: dict[tuple[int, int], list[tuple[int, Coord, Coord]]] = {}
        self.ways = ways
        for wi, w in enumerate(ways):
            g = [(pt["lat"], pt["lon"]) for pt in w.get("geometry", []) if pt]
            for a, b in zip(g, g[1:]):
                for cx in range(int(min(a[0], b[0]) // cell), int(max(a[0], b[0]) // cell) + 1):
                    for cy in range(int(min(a[1], b[1]) // cell), int(max(a[1], b[1]) // cell) + 1):
                        self.grid.setdefault((cx, cy), []).append((wi, a, b))

    def nearest(self, p: Coord, max_m: float) -> tuple[dict[str, Any] | None, float]:
        cx, cy = int(p[0] // self.cell), int(p[1] // self.cell)
        best: tuple[dict[str, Any] | None, float] = (None, float("inf"))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for wi, a, b in self.grid.get((cx + dx, cy + dy), []):
                    _, off = geo.project_to_route([a, b], [0.0, geo.haversine(a, b)], p)
                    if off < best[1]:
                        best = (self.ways[wi], off)
        return best if best[1] <= max_m else (None, best[1])


_PLACE_RADIUS_M = {"city": 4000, "town": 2000, "suburb": 1000, "village": 500, "neighbourhood": 600}


class OverpassFeatureProvider(HttpMixin):
    name = "overpass"

    def features_along(self, polyline: Sequence[Coord]) -> FeatureSet:
        rules = hazard_rules()
        samp = rules["sampling"]
        coords = [tuple(p) for p in polyline]
        line = _line_arg(coords)
        fb, ab = samp["feature_buffer_m"], samp["area_buffer_m"]
        query = f"""[out:json][timeout:90];
way(around:{fb},{line})[highway];
out tags geom;
node(around:{fb},{line})[railway=level_crossing];
out body;
node(around:{fb},{line})[highway=traffic_signals];
out body;
node(around:{fb},{line})[traffic_calming];
out body;
node(around:{fb},{line})[maxheight];
out body;
node(around:250,{line})[amenity~"^(school|marketplace|bus_station|college|place_of_worship|hospital)$"];
out body;
way(around:{ab},{line})[landuse~"^(forest|industrial)$"];
out tags geom;
way(around:{ab},{line})[natural=wood];
out tags geom;
node(around:3000,{line})[place~"^(city|town|suburb|village|neighbourhood)$"];
out body;
"""
        with _OVERPASS_SLOTS:
            data = self.request_json("POST", settings().overpass_url, data={"data": query}, attempts=3)
        elements = data.get("elements", [])
        idx = geo.RouteIndex(coords)
        roads = [e for e in elements if e["type"] == "way" and "highway" in e.get("tags", {})]
        areas = [e for e in elements if e["type"] == "way" and "highway" not in e.get("tags", {})]
        nodes = [e for e in elements if e["type"] == "node"]

        # --- settlements (for built-up detection) ---
        settlements = []
        feats: list[RoadFeature] = []
        for n in nodes:
            t = n.get("tags", {})
            if "place" in t:
                settlements.append(((n["lat"], n["lon"]), _PLACE_RADIUS_M.get(t["place"], 500), t.get("name")))
                pos, off = idx.project((n["lat"], n["lon"]))
                if off <= 1500:
                    feats.append(RoadFeature(kind="settlement", lat=n["lat"], lng=n["lon"], name=t.get("name"),
                                             tags={"place": t["place"]}, source=self.name))

        # --- road samples by nearest-way matching ---
        grid = _SegmentGrid(roads)
        samples: list[RoadSample] = []
        matched_way_ids: dict[int, float] = {}
        for d, c in geo.resample(coords, samp["road_sample_step_m"]):
            way, _off = grid.nearest(c, 25)
            t = way.get("tags", {}) if way else {}
            built = any(geo.haversine(c, sc) <= r for sc, r, _ in settlements) or t.get("highway") in (
                "residential", "living_street")
            lanes = t.get("lanes")
            samples.append(RoadSample(
                lat=c[0], lng=c[1], highway=t.get("highway"), ref=t.get("ref"), name=t.get("name"),
                surface=t.get("surface"), smoothness=t.get("smoothness"),
                lanes=int(lanes) if lanes and str(lanes).isdigit() else None,
                maxspeed=t.get("maxspeed"), lit=t.get("lit"), built_up=built))
            if way is not None:
                matched_way_ids.setdefault(way["id"], d)

        # --- bridges on the matched route ways ---
        for way in roads:
            t = way.get("tags", {})
            if way["id"] in matched_way_ids and t.get("bridge") in ("yes", "viaduct"):
                g = [(p["lat"], p["lon"]) for p in way.get("geometry", [])]
                mid = g[len(g) // 2]
                feats.append(RoadFeature(kind="bridge", lat=mid[0], lng=mid[1], name=t.get("name") or t.get("ref"),
                                         tags={"bridge": t["bridge"]}, length_m=round(geo.length(g)),
                                         source=self.name))
            # rural junctions: minor roads touching the route outside built-up areas
            if way["id"] not in matched_way_ids and t.get("highway") in ("unclassified", "track"):
                g = [(p["lat"], p["lon"]) for p in way.get("geometry", [])]
                for end in (g[0], g[-1]) if g else ():
                    pos, off = idx.project(end)
                    if off <= 12 and not any(geo.haversine(end, sc) <= r for sc, r, _ in settlements):
                        feats.append(RoadFeature(kind="rural_junction", lat=end[0], lng=end[1],
                                                 tags={"highway": t["highway"]}, source=self.name))
                        break

        for n in nodes:
            t = n.get("tags", {})
            p = (n["lat"], n["lon"])
            if t.get("railway") == "level_crossing":
                feats.append(RoadFeature(kind="level_crossing", lat=p[0], lng=p[1], name=t.get("name"),
                                         tags={k: v for k, v in t.items() if k.startswith("crossing") or k == "railway"},
                                         source=self.name))
            elif t.get("highway") == "traffic_signals":
                feats.append(RoadFeature(kind="busy_junction", lat=p[0], lng=p[1], tags={"highway": "traffic_signals"},
                                         source=self.name))
            elif "traffic_calming" in t:
                feats.append(RoadFeature(kind="traffic_calming", lat=p[0], lng=p[1],
                                         tags={"traffic_calming": t["traffic_calming"]}, source=self.name))
            elif "maxheight" in t:
                feats.append(RoadFeature(kind="overhead_restriction", lat=p[0], lng=p[1],
                                         tags={"maxheight": t["maxheight"]}, source=self.name))
            elif "amenity" in t:
                feats.append(RoadFeature(kind="pedestrian_poi", lat=p[0], lng=p[1], name=t.get("name"),
                                         tags={"amenity": t["amenity"]}, source=self.name))

        # --- areas: forest / industrial extent along the route ---
        step = samp["road_sample_step_m"]
        for a in areas:
            t = a.get("tags", {})
            kind = "forest" if (t.get("landuse") == "forest" or t.get("natural") == "wood") else "industrial"
            poly = [(p["lat"], p["lon"]) for p in a.get("geometry", [])]
            if len(poly) < 3:
                continue
            inside = [s for s in samples if _point_in_polygon((s.lat, s.lng), poly)]
            if inside:
                mid = inside[len(inside) // 2]
                feats.append(RoadFeature(kind=kind, lat=mid.lat, lng=mid.lng, name=t.get("name"),
                                         length_m=len(inside) * step, source=self.name))

        # built-up stretches as area features (for exposure maths)
        run: list[RoadSample] = []
        for s in [*samples, RoadSample(lat=0, lng=0, built_up=False)]:
            if s.built_up:
                run.append(s)
            elif run:
                mid = run[len(run) // 2]
                feats.append(RoadFeature(kind="built_up", lat=mid.lat, lng=mid.lng, length_m=len(run) * step,
                                         source=self.name))
                run = []
        return FeatureSet(provider=self.name, features=feats, road_samples=samples)


class OverpassPlacesProvider(HttpMixin):
    name = "overpass"
    _AMENITY = {"hospital": "hospital", "police": "police", "fuel": "fuel"}

    def nearby(self, point: Coord, kind: PlaceKind, radius_m: int) -> list[Place]:
        q = (f'[out:json][timeout:30];nwr(around:{radius_m},{point[0]:.5f},{point[1]:.5f})'
             f'[amenity={self._AMENITY[kind]}];out center tags 20;')
        with _OVERPASS_SLOTS:
            data = self.request_json("POST", settings().overpass_url, data={"data": q})
        out = []
        for e in data.get("elements", []):
            lat = e.get("lat") or e.get("center", {}).get("lat")
            lng = e.get("lon") or e.get("center", {}).get("lon")
            name = e.get("tags", {}).get("name")
            if lat is None or not name:
                continue  # unnamed facilities are not useful in a directory
            out.append(Place(kind=kind, name=name, lat=lat, lng=lng,
                             distance_m=round(geo.haversine(point, (lat, lng))), source=self.name))
        return sorted(out, key=lambda p: p.distance_m or 0)


class OpenMeteoElevation(HttpMixin):
    name = "open_meteo"

    def profile(self, polyline: Sequence[Coord], sample_m: float) -> ElevationProfile:
        samples = geo.resample([tuple(p) for p in polyline], sample_m)
        out: list[tuple[float, float]] = []
        for i in range(0, len(samples), 100):
            chunk = samples[i:i + 100]
            params = {"latitude": ",".join(f"{c[0]:.5f}" for _, c in chunk),
                      "longitude": ",".join(f"{c[1]:.5f}" for _, c in chunk)}
            data = self.request_json("GET", settings().open_meteo_url, params=params)
            elev = data.get("elevation")
            if not isinstance(elev, list) or len(elev) != len(chunk):
                raise ProviderError("Open-Meteo returned an unexpected elevation payload")
            out.extend((round(d, 1), float(e)) for (d, _), e in zip(chunk, elev, strict=True))
        return ElevationProfile(provider=self.name, samples=out)
