"""Hazard detectors (hazard-library.md §6). One function per detector *kind*; parameters come from
config/hazard_rules.yaml. A detector never invents a location: hazards without a measured position are
reported as route-wide. Outcomes:

* applicable + DETECTED  — located feature(s) from provider data
* applicable + INFERRED  — deterministic rule on context (season, region, road class, time)
* VERIFY                 — cannot be established from data; goes to the verification list only
* not applicable
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.domain import geo
from app.domain.facts import HazardLocation, LocatedFeature, RouteFacts
from app.domain.route import ElevationProfile, FeatureSet, RouteResult


@dataclass
class Detection:
    applicable: bool
    evidence: str = "INFERRED"  # DETECTED | INFERRED | VERIFY
    basis: str = ""
    locations: list[HazardLocation] = field(default_factory=list)
    route_wide: bool = False
    extent_km: float = 0.0
    occurrences: int = 0
    verify_item: str | None = None
    verify_reason: str | None = None
    analog: str | None = None

    @classmethod
    def no(cls, basis: str = "") -> Detection:
        return cls(applicable=False, basis=basis)

    @classmethod
    def verify(cls, item: str, reason: str) -> Detection:
        return cls(applicable=False, evidence="VERIFY", verify_item=item, verify_reason=reason)


@dataclass
class DetectionContext:
    facts: RouteFacts
    route: RouteResult
    features: FeatureSet | None
    elevation: ElevationProfile | None
    rules: dict[str, Any]
    _curv_cache: dict[tuple[float, float], list[tuple[float, float, float]]] = field(default_factory=dict)

    # -- helpers ---------------------------------------------------------------------------------
    def feats(self, kind: str) -> list[LocatedFeature]:
        return [f for f in self.facts.features if f.kind == kind]

    def place_near(self, km: float) -> str:
        wps = self.facts.waypoints
        best = min(wps, key=lambda w: abs(w.km_from_start - km))
        return best.locality or best.short_name

    def loc(self, f: LocatedFeature, label: str | None = None) -> HazardLocation:
        half = (f.length_m or 0) / 2000
        return HazardLocation(km_from=round(max(0.0, f.km - half), 2), km_to=round(f.km + half, 2), lat=f.lat,
                              lng=f.lng, label=label or f.name or f"near {self.place_near(f.km)}")

    def coord_at_km(self, km: float) -> tuple[float, float]:
        geom = self.facts.geometry
        cum = geo.cumulative(geom)
        frac = km / self.facts.distance_km if self.facts.distance_km else 0.0
        return geo.point_at(geom, cum, frac * cum[-1])

    def share(self, *cats: str) -> float:
        return sum(r.pct for r in self.facts.road_types if r.category in cats)

    def km_of(self, *cats: str) -> float:
        return sum(r.km for r in self.facts.road_types if r.category in cats)

    def season_active(self, season: str) -> bool | None:
        if self.facts.travel.travel_month is None:
            return None
        return season in self.facts.exposures.seasonal_flags

    def curves(self, min_turn: float, window_m: float, ignore_m: float) -> list[tuple[float, float, float]]:
        """Sharp-turn events [(km, lat, lng)] on the full geometry, excluding turns at routing maneuvers."""
        key = (min_turn, window_m)
        if key in self._curv_cache:
            return self._curv_cache[key]
        step = self.rules["sampling"]["resample_m"]
        full = self.route.geometry
        pts = geo.resample(full, step)
        w = max(1, int(window_m / step / 2))
        idx = geo.RouteIndex(full)
        scale = (self.route.distance_m / idx.total) if idx.total else 1.0
        man_km = sorted(idx.project(tuple(m))[0] for leg in self.route.legs for m in leg.maneuvers)
        events: list[tuple[float, float, float]] = []
        last = -1e9
        for i in range(w, len(pts) - w):
            b1 = geo.bearing(pts[i - w][1], pts[i][1])
            b2 = geo.bearing(pts[i][1], pts[i + w][1])
            if geo.turn_angle(b1, b2) < min_turn:
                continue
            d = pts[i][0]
            if any(abs(d - m) <= ignore_m for m in man_km):
                continue
            if d - last < window_m * 2:
                continue
            last = d
            events.append((round(d * scale / 1000, 2), pts[i][1][0], pts[i][1][1]))
        self._curv_cache[key] = events
        return events


Detector = Callable[[DetectionContext, dict[str, Any]], Detection]
REGISTRY: dict[str, Detector] = {}


def detector(kind: str) -> Callable[[Detector], Detector]:
    def wrap(fn: Detector) -> Detector:
        REGISTRY[kind] = fn
        return fn

    return wrap


def _cap_locations(locs: list[HazardLocation], n: int = 8) -> list[HazardLocation]:
    return locs[:n]


# ------------------------------------------------------------------------------------- detectors
@detector("curvature")
def _curvature(ctx: DetectionContext, p: dict[str, Any]) -> Detection:
    ev = ctx.curves(p["min_turn_deg"], p["window_m"], p["ignore_near_maneuver_m"])
    if len(ev) < p["min_count"]:
        return Detection.no()
    locs = [HazardLocation(km_from=k, km_to=k, lat=a, lng=b, label=f"near {ctx.place_near(k)}") for k, a, b in ev]
    return Detection(True, "DETECTED", f"{len(ev)} sharp bend(s) ≥{p['min_turn_deg']}° in route geometry",
                     _cap_locations(locs), occurrences=len(ev))


@detector("gradient")
def _gradient(ctx: DetectionContext, p: dict[str, Any]) -> Detection:
    if not ctx.elevation or len(ctx.elevation.samples) < 3:
        return Detection.no("no elevation data")
    s = ctx.elevation.samples
    total_geom = s[-1][0] or 1
    scale = ctx.facts.distance_km * 1000 / total_geom
    events: list[HazardLocation] = []
    j = 0
    last_end = -1.0
    for i in range(len(s)):
        while j < len(s) and s[j][0] - s[i][0] < p["window_m"]:
            j += 1
        if j >= len(s):
            break
        grade = 100 * (s[j][1] - s[i][1]) / (s[j][0] - s[i][0])
        hit = grade >= p["min_grade_pct"] if p["direction"] == "up" else grade <= -p["min_grade_pct"]
        if hit and s[i][0] > last_end:
            k0, k1 = s[i][0] * scale / 1000, s[j][0] * scale / 1000
            events.append(HazardLocation(km_from=round(k0, 2), km_to=round(k1, 2), lat=0, lng=0,
                                         label=f"{abs(grade):.0f}% grade near {ctx.place_near(k0)}"))
            last_end = s[j][0]
    if len(events) < p["min_count"]:
        return Detection.no()
    for e in events:
        e.lat, e.lng = ctx.coord_at_km(e.km_from)
    ext = sum(e.km_to - e.km_from for e in events)
    word = "uphill" if p["direction"] == "up" else "downhill"
    return Detection(True, "DETECTED", f"{len(events)} {word} stretch(es) ≥{p['min_grade_pct']}% from elevation data",
                     _cap_locations(events), extent_km=round(ext, 2), occurrences=len(events))


@detector("narrow_road")
def _narrow(ctx: DetectionContext, p: dict[str, Any]) -> Detection:
    samples = ctx.features.road_samples if ctx.features else []
    if samples:
        narrow = [s for s in samples if (s.lanes == 1) or s.highway in ("residential", "unclassified", "track",
                                                                        "living_street")]
        share = 100 * len(narrow) / len(samples)
        km = share / 100 * ctx.facts.distance_km
        if share >= p["min_share_pct"] and km >= p["min_km"]:
            return Detection(True, "DETECTED", f"{share:.0f}% of route on single-lane/minor roads (OSM)",
                             route_wide=True, extent_km=round(km, 1))
        return Detection.no()
    km = ctx.km_of("local", "unpaved")
    share = ctx.share("local", "unpaved")
    if share >= p["min_share_pct"] and km >= p["min_km"]:
        return Detection(True, "INFERRED", f"{share:.0f}% of route on township/local roads", route_wide=True,
                         extent_km=round(km, 1))
    return Detection.no()


@detector("surface_quality")
def _surface(ctx: DetectionContext, p: dict[str, Any]) -> Detection:
    samples = ctx.features.road_samples if ctx.features else []
    bad = [s for s in samples if s.smoothness in ("bad", "very_bad", "horrible", "very_horrible", "impassable")]
    if bad:
        return Detection(True, "DETECTED", f"{len(bad)} sample(s) tagged with poor road smoothness (OSM)",
                         route_wide=True, occurrences=len(bad))
    share = ctx.share("local", "unpaved")
    if share >= p["min_local_or_unpaved_share_pct"]:
        return Detection(True, "INFERRED", f"{share:.0f}% of route on township/local or unpaved roads",
                         route_wide=True, extent_km=round(ctx.km_of("local", "unpaved"), 1))
    return Detection.no()


@detector("speed_breaker")
def _speed_breaker(ctx: DetectionContext, p: dict[str, Any]) -> Detection:
    marked = ctx.feats("traffic_calming")
    if marked:
        return Detection(True, "DETECTED", f"{len(marked)} mapped traffic-calming point(s); unmarked breakers likely",
                         _cap_locations([ctx.loc(f) for f in marked]), occurrences=len(marked))
    if ctx.share("local") >= p["min_local_share_pct"] and ctx.facts.exposures.built_up_share_pct >= p[
            "min_built_up_share_pct"]:
        return Detection(True, "INFERRED", "Township/local roads through built-up areas", route_wide=True)
    return Detection.no()


@detector("point_feature")
def _point(ctx: DetectionContext, p: dict[str, Any]) -> Detection:
    fs = ctx.feats(p["feature"])
    if len(fs) < p["min_count"]:
        return Detection.no()
    locs = [ctx.loc(f) for f in fs]
    labels = {"bridge": "bridge/culvert crossing(s)", "busy_junction": "signalised/major junction(s)",
              "rural_junction": "village road crossing(s)", "overhead_restriction": "height-restricted point(s)"}
    return Detection(True, "DETECTED", f"{len(fs)} {labels.get(p['feature'], p['feature'])} on route",
                     _cap_locations(locs), occurrences=len(fs),
                     extent_km=round(sum((f.length_m or 0) for f in fs) / 1000, 2))


@detector("level_crossing")
def _level_crossing(ctx: DetectionContext, p: dict[str, Any]) -> Detection:
    fs = ctx.feats("level_crossing")
    if len(fs) < p["min_count"]:
        return Detection.no()
    unmanned = [f for f in fs if f.tags.get("crossing:barrier") in ("no",) or f.tags.get("crossing") == "uncontrolled"]
    unknown = [f for f in fs if "crossing:barrier" not in f.tags and "crossing" not in f.tags]
    places = ", ".join(dict.fromkeys(ctx.place_near(f.km) for f in fs))
    d = Detection(True, "DETECTED", f"{len(fs)} railway level crossing(s) on route ({places})",
                  _cap_locations([ctx.loc(f, f"Level crossing near {ctx.place_near(f.km)}") for f in fs]),
                  occurrences=len(fs))
    if unknown or not unmanned:
        d.verify_item = f"Manned/unmanned status of level crossing(s) near {places}"
        d.verify_reason = "Barrier/attendant status is not recorded in route data"
    return d


@detector("blind_curve")
def _blind(ctx: DetectionContext, p: dict[str, Any]) -> Detection:
    ctx_ok = (ctx.facts.hilly_region and "hilly" in p["context"]) or (
        ctx.facts.exposures.built_up_share_pct >= 30 and "built_up" in p["context"])
    if not ctx_ok:
        return Detection.no()
    ev = ctx.curves(p["min_turn_deg"], p["window_m"], p["ignore_near_maneuver_m"])
    if len(ev) < p["min_count"]:
        return Detection.no()
    locs = [HazardLocation(km_from=k, km_to=k, lat=a, lng=b, label=f"near {ctx.place_near(k)}") for k, a, b in ev]
    return Detection(True, "INFERRED", f"{len(ev)} curve(s) ≥{p['min_turn_deg']}° in built-up/hilly context",
                     _cap_locations(locs), occurrences=len(ev))


@detector("missing_signage")
def _signage(ctx: DetectionContext, p: dict[str, Any]) -> Detection:
    samples = [s for s in (ctx.features.road_samples if ctx.features else [])
               if s.highway and s.highway not in ("residential", "service", "track", "living_street")]
    if not samples:
        return Detection.no("no road attribute data")
    km = len(samples) * ctx.rules["sampling"]["road_sample_step_m"] / 1000
    missing = 100 * sum(1 for s in samples if not s.maxspeed) / len(samples)
    if km >= p["min_nonlocal_km"] and missing >= p["min_missing_maxspeed_share_pct"]:
        return Detection(True, "INFERRED", f"No posted speed limit recorded on {missing:.0f}% of main-road stretches",
                         route_wide=True, extent_km=round(km, 1))
    return Detection.no()


@detector("verify_only")
def _verify_only(ctx: DetectionContext, p: dict[str, Any]) -> Detection:
    return Detection.verify("Roadside construction / diversions on the day of travel", p["reason"])


@detector("season")
def _season(ctx: DetectionContext, p: dict[str, Any]) -> Detection:
    active = ctx.season_active(p["season"])
    name = {"monsoon": "Monsoon", "fog": "Fog"}[p["season"]]
    if active is None:
        return Detection.verify(f"{name}-season exposure for the actual travel date",
                                "Travel date not supplied — seasonal hazards cannot be assessed")
    if not active:
        return Detection.no()
    month = ctx.facts.travel.travel_month_name
    states = ", ".join(ctx.facts.states)
    return Detection(True, "INFERRED", f"{month} falls within the {p['season']} window for {states}", route_wide=True)


@detector("landslide")
def _landslide(ctx: DetectionContext, p: dict[str, Any]) -> Detection:
    relief = ctx.facts.elevation.get("relief_m", 0)
    if p.get("requires_hilly_region") and not ctx.facts.hilly_region:
        return Detection.no()
    if relief >= p["min_relief_m"]:
        return Detection(True, "INFERRED", f"Hilly region with {relief:.0f} m elevation relief along route",
                         route_wide=True)
    return Detection.no()


@detector("slippery")
def _slippery(ctx: DetectionContext, p: dict[str, Any]) -> Detection:
    active = ctx.season_active(p["season"])
    if active is None:
        return Detection.no("travel date unknown (covered by monsoon verification item)")
    if not active:
        return Detection.no()
    if ctx.share("unpaved") >= p["min_unpaved_share_pct"] or ctx.share("local") >= p["min_local_share_pct"]:
        return Detection(True, "INFERRED", "Monsoon month on township/unpaved stretches", route_wide=True)
    return Detection.no()


@detector("area_feature")
def _area(ctx: DetectionContext, p: dict[str, Any]) -> Detection:
    fs = ctx.feats(p["feature"])
    km = sum((f.length_m or 0) for f in fs) / 1000
    if km < p["min_km"]:
        return Detection.no()
    return Detection(True, "DETECTED", f"{km:.1f} km of route through forest/wooded land",
                     _cap_locations([ctx.loc(f) for f in fs]), extent_km=round(km, 1), occurrences=len(fs))


@detector("pedestrian")
def _pedestrian(ctx: DetectionContext, p: dict[str, Any]) -> Detection:
    share = ctx.facts.exposures.built_up_share_pct
    pois = ctx.feats("pedestrian_poi")
    if share < p["min_built_up_share_pct"] and len(pois) < p["min_poi_count"]:
        return Detection.no()
    built = ctx.feats("built_up")
    locs = [ctx.loc(f, f"{ctx.place_near(f.km)} built-up area") for f in built]
    return Detection(True, "DETECTED",
                     f"{share}% of route through built-up areas; {len(pois)} pedestrian-generating site(s)",
                     _cap_locations(locs), route_wide=not locs,
                     extent_km=round(share / 100 * ctx.facts.distance_km, 1), occurrences=len(pois))


@detector("ghat")
def _ghat(ctx: DetectionContext, p: dict[str, Any]) -> Detection:
    bridges = [f for f in ctx.feats("bridge") if (f.length_m or 0) >= p["major_bridge_min_m"]]
    if bridges:
        return Detection(True, "DETECTED", f"{len(bridges)} major bridge(s) ≥{p['major_bridge_min_m']} m",
                         _cap_locations([ctx.loc(f) for f in bridges]), occurrences=len(bridges))
    if p.get("requires_hilly_region") and not ctx.facts.hilly_region:
        return Detection.no()
    curves = ctx.curves(45, 60, 45)
    grade = _gradient(ctx, {"min_grade_pct": p["min_grade_pct"], "window_m": 500, "direction": "up", "min_count": 1})
    if len(curves) >= p["min_sharp_curves"] and grade.applicable:
        return Detection(True, "DETECTED", f"Hilly section with {len(curves)} bends and ≥{p['min_grade_pct']}% grades",
                         grade.locations, extent_km=grade.extent_km, occurrences=len(curves))
    return Detection.no()


@detector("remote_area")
def _remote(ctx: DetectionContext, p: dict[str, Any]) -> Detection:
    if ctx.facts.distance_km >= p["min_route_km"] and ctx.facts.exposures.built_up_share_pct <= p[
            "max_built_up_share_pct"]:
        return Detection(True, "INFERRED", "Long stretches away from settlements (coverage data not available)",
                         route_wide=True)
    return Detection.no()


@detector("night")
def _night(ctx: DetectionContext, p: dict[str, Any]) -> Detection:
    n = ctx.facts.travel.night_overlap
    if n is None:
        return Detection.verify("Journey completes before sunset / no night driving",
                                "Travel date or departure time not supplied")
    if not n:
        return Detection.no()
    return Detection(True, "INFERRED",
                     f"Planned journey window extends past sunset ({ctx.facts.travel.sunset_local}) or before sunrise",
                     route_wide=True)


@detector("highway")
def _highway(ctx: DetectionContext, p: dict[str, Any]) -> Detection:
    km = ctx.km_of(*p["categories"])
    if km < p["min_km"]:
        return Detection.no()
    refs = [r for rt in ctx.facts.road_types if rt.category in p["categories"] for r in rt.refs]
    segs = [s for s in ctx.facts.segments if s.dominant_category in p["categories"]]
    locs = []
    for s in segs:
        c = ctx.coord_at_km((s.km_from + s.km_to) / 2)
        locs.append(HazardLocation(km_from=s.km_from, km_to=s.km_to, lat=c[0], lng=c[1], label=s.label))
    label = ", ".join(refs[:2]) if refs else "highway sections"
    analog = None
    if ctx.km_of("expressway") >= p["min_km"]:
        analog = ctx.rules.get("analog_labels", {}).get("HZ-24", {}).get("expressway")
    return Detection(True, "DETECTED", f"{km:.1f} km on national highway / expressway ({label})",
                     _cap_locations(locs), route_wide=not locs, extent_km=round(km, 1), analog=analog)


@detector("dip")
def _dip(ctx: DetectionContext, p: dict[str, Any]) -> Detection:
    if ctx.facts.hilly_region or not ctx.elevation or len(ctx.elevation.samples) < 5:
        return Detection.no()
    s = ctx.elevation.samples
    step = max(1.0, s[1][0] - s[0][0])
    w = max(1, int(p["window_m"] / 2 / step))
    scale = ctx.facts.distance_km * 1000 / (s[-1][0] or 1)
    events = []
    for i in range(w, len(s) - w):
        before = max(e for _, e in s[i - w:i])
        after = max(e for _, e in s[i + 1:i + w + 1])
        if min(before, after) - s[i][1] >= p["min_depth_m"]:
            k = s[i][0] * scale / 1000
            lat, lng = ctx.coord_at_km(k)
            events.append(HazardLocation(km_from=round(k, 2), km_to=round(k, 2), lat=lat, lng=lng,
                                         label=f"dip near {ctx.place_near(k)}"))
    if not events:
        return Detection.no()
    return Detection(True, "INFERRED", f"{len(events)} short dip(s) ≥{p['min_depth_m']} m in elevation profile",
                     _cap_locations(events), occurrences=len(events))
