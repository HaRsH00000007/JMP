"""Pure geometry helpers (no I/O). Coordinates are (lat, lng) in degrees; distances in metres."""

from __future__ import annotations

import math
from bisect import bisect_right
from collections.abc import Sequence

EARTH_R = 6_371_008.8
Coord = tuple[float, float]


def haversine(a: Coord, b: Coord) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_R * math.asin(min(1.0, math.sqrt(h)))


def bearing(a: Coord, b: Coord) -> float:
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    dlon = math.radians(b[1] - a[1])
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def turn_angle(b1: float, b2: float) -> float:
    d = abs(b2 - b1) % 360
    return 360 - d if d > 180 else d


def cumulative(coords: Sequence[Coord]) -> list[float]:
    out = [0.0]
    for i in range(1, len(coords)):
        out.append(out[-1] + haversine(coords[i - 1], coords[i]))
    return out


def length(coords: Sequence[Coord]) -> float:
    return cumulative(coords)[-1] if coords else 0.0


def interpolate(a: Coord, b: Coord, t: float) -> Coord:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def point_at(coords: Sequence[Coord], cum: Sequence[float], dist: float) -> Coord:
    if dist <= 0:
        return coords[0]
    if dist >= cum[-1]:
        return coords[-1]
    i = bisect_right(cum, dist) - 1
    seg = cum[i + 1] - cum[i]
    t = 0.0 if seg == 0 else (dist - cum[i]) / seg
    return interpolate(coords[i], coords[i + 1], t)


def resample(coords: Sequence[Coord], step_m: float) -> list[tuple[float, Coord]]:
    """Return [(distance_along, coord)] every `step_m` metres (always including both ends)."""
    if len(coords) < 2:
        return [(0.0, coords[0])] if coords else []
    cum = cumulative(coords)
    total = cum[-1]
    out: list[tuple[float, Coord]] = []
    d = 0.0
    while d < total:
        out.append((d, point_at(coords, cum, d)))
        d += step_m
    out.append((total, coords[-1]))
    return out


def _to_xy(origin: Coord, p: Coord) -> tuple[float, float]:
    lat0 = math.radians(origin[0])
    x = math.radians(p[1] - origin[1]) * EARTH_R * math.cos(lat0)
    y = math.radians(p[0] - origin[0]) * EARTH_R
    return x, y


def project_to_route(coords: Sequence[Coord], cum: Sequence[float], p: Coord) -> tuple[float, float]:
    """Nearest position on the polyline: returns (distance_along_route_m, offset_m)."""
    best = (0.0, float("inf"))
    for i in range(len(coords) - 1):
        a, b = coords[i], coords[i + 1]
        ax, ay = 0.0, 0.0
        bx, by = _to_xy(a, b)
        px, py = _to_xy(a, p)
        seg2 = bx * bx + by * by
        t = 0.0 if seg2 == 0 else max(0.0, min(1.0, (px * bx + py * by) / seg2))
        cx, cy = ax + t * bx, ay + t * by
        off = math.hypot(px - cx, py - cy)
        if off < best[1]:
            best = (cum[i] + t * (cum[i + 1] - cum[i]), off)
    return best


class RouteIndex:
    """Fast-ish projection of many points onto a long polyline using a coarse grid of segments."""

    def __init__(self, coords: Sequence[Coord], cell_deg: float = 0.01) -> None:
        self.coords = list(coords)
        self.cum = cumulative(self.coords)
        self.cell = cell_deg
        self.grid: dict[tuple[int, int], list[int]] = {}
        for i in range(len(self.coords) - 1):
            a, b = self.coords[i], self.coords[i + 1]
            for cx in range(int(min(a[0], b[0]) // cell_deg) - 1, int(max(a[0], b[0]) // cell_deg) + 2):
                for cy in range(int(min(a[1], b[1]) // cell_deg) - 1, int(max(a[1], b[1]) // cell_deg) + 2):
                    self.grid.setdefault((cx, cy), []).append(i)

    @property
    def total(self) -> float:
        return self.cum[-1] if self.cum else 0.0

    def project(self, p: Coord) -> tuple[float, float]:
        key = (int(p[0] // self.cell), int(p[1] // self.cell))
        cand = self.grid.get(key)
        if not cand:
            return (0.0, float("inf"))
        best = (0.0, float("inf"))
        for i in cand:
            a, b = self.coords[i], self.coords[i + 1]
            bx, by = _to_xy(a, b)
            px, py = _to_xy(a, p)
            seg2 = bx * bx + by * by
            t = 0.0 if seg2 == 0 else max(0.0, min(1.0, (px * bx + py * by) / seg2))
            off = math.hypot(px - t * bx, py - t * by)
            if off < best[1]:
                best = (self.cum[i] + t * (self.cum[i + 1] - self.cum[i]), off)
        return best


def douglas_peucker(coords: Sequence[Coord], tolerance_m: float) -> list[Coord]:
    if len(coords) < 3:
        return list(coords)
    keep = [False] * len(coords)
    keep[0] = keep[-1] = True
    stack = [(0, len(coords) - 1)]
    while stack:
        s, e = stack.pop()
        a, b = coords[s], coords[e]
        bx, by = _to_xy(a, b)
        seg2 = bx * bx + by * by
        dmax, idx = 0.0, -1
        for i in range(s + 1, e):
            px, py = _to_xy(a, coords[i])
            if seg2 == 0:
                d = math.hypot(px, py)
            else:
                t = max(0.0, min(1.0, (px * bx + py * by) / seg2))
                d = math.hypot(px - t * bx, py - t * by)
            if d > dmax:
                dmax, idx = d, i
        if dmax > tolerance_m and idx > 0:
            keep[idx] = True
            stack.extend([(s, idx), (idx, e)])
    return [c for c, k in zip(coords, keep, strict=True) if k]


def decode_polyline(encoded: str, precision: int = 5) -> list[Coord]:
    """Google / Mapbox encoded polyline → [(lat, lng)]."""
    coords: list[Coord] = []
    index = lat = lng = 0
    factor = 10 ** precision
    while index < len(encoded):
        for is_lng in (False, True):
            shift = result = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if is_lng:
                lng += delta
            else:
                lat += delta
        coords.append((lat / factor, lng / factor))
    return coords


def bbox(coords: Sequence[Coord], pad_deg: float = 0.0) -> tuple[float, float, float, float]:
    lats = [c[0] for c in coords]
    lngs = [c[1] for c in coords]
    return (min(lats) - pad_deg, min(lngs) - pad_deg, max(lats) + pad_deg, max(lngs) + pad_deg)
