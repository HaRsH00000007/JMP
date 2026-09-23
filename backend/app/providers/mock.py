"""Deterministic mock providers backed by the demo gazetteer (mock_gazetteer.json).

Used when no provider credentials/network are available, and in tests. Everything they return is
DEMO DATA: the report renderer watermarks any document produced with a mock provider. Unknown
locations are NOT invented — the mock geocoder raises GEOCODE_NOT_FOUND like a real one would.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Sequence
from datetime import datetime
from functools import lru_cache
from pathlib import Path
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
    RoadSpan,
    RouteLeg,
    RouteResult,
)
from app.errors import ErrorCode, GeocodeError, RouteError

Coord = tuple[float, float]
_SPEED_KMH = {"expressway": 70, "national_highway": 55, "state_highway": 45, "arterial": 32, "local": 24,
              "unpaved": 18, "unknown": 30}


@lru_cache(maxsize=1)
def gazetteer() -> dict[str, Any]:
    with open(Path(__file__).with_name("mock_gazetteer.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower()).split(",")[0].strip()


def _norm_full(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def _stable_int(*parts: object) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


def _places_near(p: Coord, radius_m: float) -> list[dict[str, Any]]:
    return [pl for pl in gazetteer()["places"] if geo.haversine(p, (pl["lat"], pl["lng"])) <= radius_m]


class MockGeocoder:
    name = "mock"

    def geocode(self, query: str, region_hint: str | None = None) -> GeocodeResult:
        q = _norm_full(query)
        best: tuple[int, dict[str, Any]] | None = None
        for pl in gazetteer()["places"]:
            for alias in [pl["name"], *pl.get("aliases", [])]:
                a = _norm_full(alias)
                if not a:
                    continue
                if q == a or q.startswith(a + " ") or (len(a) > 8 and a in q):
                    score = len(a)
                    if best is None or score > best[0]:
                        best = (score, pl)
        if best is None:
            raise GeocodeError(f"Location not found: {query!r}", code=ErrorCode.GEOCODE_NOT_FOUND)
        pl = best[1]
        return GeocodeResult(query=query, name=pl["name"], lat=pl["lat"], lng=pl["lng"], state=pl["state"],
                             district=pl.get("district"), locality=pl.get("locality"),
                             place_types=list(pl.get("types", [])), confidence=0.99, provider=self.name)


def _leg_tags(a: Coord, b: Coord) -> tuple[set[str], set[str]]:
    ta: set[str] = set()
    tb: set[str] = set()
    for pl in _places_near(a, 1500):
        ta |= set(pl.get("tags", []))
    for pl in _places_near(b, 1500):
        tb |= set(pl.get("tags", []))
    return ta, tb


def _curve(a: Coord, b: Coord, bend: float, n: int, hilly: bool) -> list[Coord]:
    """Deterministic gently curved path (quadratic Bézier; switchbacks when hilly)."""
    mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    dx, dy = b[0] - a[0], b[1] - a[1]
    cx, cy = mx - dy * bend, my + dx * bend
    pts: list[Coord] = []
    for i in range(n + 1):
        t = i / n
        x = (1 - t) ** 2 * a[0] + 2 * (1 - t) * t * cx + t ** 2 * b[0]
        y = (1 - t) ** 2 * a[1] + 2 * (1 - t) * t * cy + t ** 2 * b[1]
        if hilly and 0.15 < t < 0.85:
            amp = 0.0045 * math.sin(t * math.pi * 14)
            x += -dy / (math.hypot(dx, dy) or 1) * amp
            y += dx / (math.hypot(dx, dy) or 1) * amp
        pts.append((round(x, 6), round(y, 6)))
    return pts


def _spans(length_m: float, ta: set[str], tb: set[str]) -> list[RoadSpan]:
    tags = ta | tb
    km = length_m / 1000
    if "hilly" in tags:
        return [RoadSpan(start_m=0, end_m=length_m, category="state_highway", ref="SH (demo)")]
    if "highway" in ta and "highway" in tb and km > 6:
        edge = min(1200.0, length_m * 0.15)
        return [
            RoadSpan(start_m=0, end_m=edge, category="local"),
            RoadSpan(start_m=edge, end_m=length_m - edge, category="national_highway", ref="NH (demo)"),
            RoadSpan(start_m=length_m - edge, end_m=length_m, category="local"),
        ]
    if km > 10:
        edge = min(1500.0, length_m * 0.15)
        return [
            RoadSpan(start_m=0, end_m=edge, category="local"),
            RoadSpan(start_m=edge, end_m=length_m - edge, category="state_highway", ref="SH (demo)"),
            RoadSpan(start_m=length_m - edge, end_m=length_m, category="local"),
        ]
    if "urban" in tags and km > 3:
        return [RoadSpan(start_m=0, end_m=length_m, category="arterial")]
    if "village_roads" in tags:
        return [RoadSpan(start_m=0, end_m=length_m, category="local", name="village road (demo)")]
    return [RoadSpan(start_m=0, end_m=length_m, category="local")]


class MockRouteProvider:
    name = "mock"

    def route(self, waypoints: Sequence[Coord], *, depart_at: datetime | None = None,
              alternatives: bool = False) -> RouteResult:
        if len(waypoints) < 2:
            raise RouteError("At least two waypoints are required")
        legs: list[RouteLeg] = []
        alts: list[AlternativeRoute] = []
        for i in range(len(waypoints) - 1):
            a, b = tuple(waypoints[i]), tuple(waypoints[i + 1])
            straight = geo.haversine(a, b)
            if straight < 1:
                raise RouteError("Consecutive waypoints are identical", code=ErrorCode.ROUTE_NOT_FOUND)
            ta, tb = _leg_tags(a, b)
            hilly = "hilly" in (ta | tb)
            bend = ((_stable_int(a, b) % 21) - 10) / 100.0  # -0.10 … +0.10
            n = max(8, int(straight / 150))
            pts = _curve(a, b, bend, n, hilly)
            length_m = geo.length(pts)
            spans = _spans(length_m, ta, tb)
            dur = sum((s.end_m - s.start_m) / 1000 / _SPEED_KMH[s.category] * 3600 for s in spans)
            legs.append(RouteLeg(from_seq=i, to_seq=i + 1, distance_m=round(length_m, 1), duration_s=round(dur, 1),
                                 geometry=pts, maneuvers=[pts[0], pts[-1]], spans=spans))
            if alternatives and length_m > 5000:
                alt_pts = _curve(a, b, -0.28 if bend >= 0 else 0.28, n, hilly)
                alt_len = geo.length(alt_pts)
                alts.append(AlternativeRoute(distance_m=round(alt_len, 1),
                                             duration_s=round(dur * alt_len / length_m * 1.05, 1),
                                             geometry=alt_pts, via=["local district roads (demo)"]))
        return RouteResult(provider=self.name, request_id=f"mock-{_stable_int(*waypoints):08x}", legs=legs,
                           alternatives=alts, warnings=["DEMO DATA: mock route provider"])


class MockFeatureProvider:
    """Places synthetic features next to demo gazetteer places according to their tags."""

    name = "mock"

    def features_along(self, polyline: Sequence[Coord]) -> FeatureSet:
        coords = [tuple(p) for p in polyline]
        idx = geo.RouteIndex(coords)
        total = idx.total
        feats: list[RoadFeature] = []
        seen: set[str] = set()
        for pl in gazetteer()["places"]:
            pos, off = idx.project((pl["lat"], pl["lng"]))
            if off > 1500 or pl["name"] in seen:
                continue
            seen.add(pl["name"])
            tags = set(pl.get("tags", []))

            def at(delta_m: float) -> Coord:
                return geo.point_at(coords, idx.cum, min(max(pos + delta_m, 0), total))

            if "urban" in tags:
                c = at(0)
                feats.append(RoadFeature(kind="built_up", lat=c[0], lng=c[1], name=pl["name"], length_m=3000,
                                         source=self.name))
                feats.append(RoadFeature(kind="settlement", lat=c[0], lng=c[1], name=pl["locality"],
                                         source=self.name))
                for k, d in enumerate((-600, 0, 700)):
                    c = at(d)
                    feats.append(RoadFeature(kind="busy_junction", lat=c[0], lng=c[1],
                                             name=f"{pl['locality']} junction {k + 1}",
                                             tags={"highway": "traffic_signals"}, source=self.name))
            if "market" in tags:
                for d in (-300, -100, 150, 350, 500):
                    c = at(d)
                    feats.append(RoadFeature(kind="pedestrian_poi", lat=c[0], lng=c[1],
                                             name=f"{pl['locality']} market area", source=self.name))
            if "rail_crossing" in tags:
                c = at(900)
                feats.append(RoadFeature(kind="level_crossing", lat=c[0], lng=c[1],
                                         name=f"Level crossing near {pl['locality']}",
                                         tags={"railway": "level_crossing", "crossing:barrier": "no"},
                                         source=self.name))
            if "industrial" in tags:
                c = at(-800)
                feats.append(RoadFeature(kind="industrial", lat=c[0], lng=c[1], name=f"{pl['locality']} industrial area",
                                         length_m=1500, source=self.name))
            if "bridge" in tags:
                c = at(-1500)
                feats.append(RoadFeature(kind="bridge", lat=c[0], lng=c[1], name=f"Bridge near {pl['locality']}",
                                         length_m=350, source=self.name))
            if "forest" in tags:
                c = at(-3000)
                feats.append(RoadFeature(kind="forest", lat=c[0], lng=c[1], name=f"Forest stretch near {pl['locality']}",
                                         length_m=4000, source=self.name))
            if "village_roads" in tags:
                c = at(1200)
                feats.append(RoadFeature(kind="rural_junction", lat=c[0], lng=c[1],
                                         name=f"Village road crossing near {pl['locality']}", source=self.name))
                feats.append(RoadFeature(kind="settlement", lat=c[0], lng=c[1], name=pl["locality"], source=self.name))
        return FeatureSet(provider=self.name, features=feats, warnings=["DEMO DATA: mock feature provider"])


class MockElevationProvider:
    name = "mock"

    def profile(self, polyline: Sequence[Coord], sample_m: float) -> ElevationProfile:
        samples = geo.resample([tuple(p) for p in polyline], sample_m)
        out: list[tuple[float, float]] = []
        for d, c in samples:
            hilly = any("hilly" in pl.get("tags", []) for pl in _places_near(c, 25000))
            base = 280.0 if c[0] > 25 else 12.0
            if hilly:
                elev = base + 900 + 450 * math.sin(d / 2200.0) + 0.02 * d
            else:
                elev = base + 3 * math.sin(d / 5000.0)
            out.append((round(d, 1), round(elev, 1)))
        return ElevationProfile(provider=self.name, samples=out)


class MockPlacesProvider:
    name = "mock"

    def nearby(self, point: Coord, kind: PlaceKind, radius_m: int) -> list[Place]:
        out = []
        for f in gazetteer()["facilities"]:
            if f["kind"] != kind:
                continue
            d = geo.haversine(point, (f["lat"], f["lng"]))
            if d <= radius_m:
                out.append(Place(kind=kind, name=f["name"], lat=f["lat"], lng=f["lng"], distance_m=round(d),
                                 source=self.name))
        return sorted(out, key=lambda p: p.distance_m or 0)
