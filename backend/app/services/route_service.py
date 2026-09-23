"""Stages 2–5: geocode → route → features/elevation → deterministic route analysis.

All geographic facts come from providers; this module only measures and classifies them. Provider
responses are cached in `route_cache` (keyed by provider + normalized input) so repeated bulk rows
don't pay twice.
"""

from __future__ import annotations

import calendar
import hashlib
import json
from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta, timezone
from typing import Any, TypeVar

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import rules_config
from app.db.models import RouteCache
from app.db.session import session_scope
from app.domain import geo
from app.domain.facts import (
    Alternative,
    Exposures,
    LegFact,
    LocatedFeature,
    RoadTypeShare,
    RouteFacts,
    Segment,
    TravelContext,
    Waypoint,
)
from app.domain.route import ElevationProfile, FeatureSet, GeocodeResult, RouteResult
from app.domain.solar import fmt_hhmm, local_sun_times
from app.errors import ErrorCode, GeocodeError, RouteError
from app.observability.logging import get_logger
from app.providers.osm import categorize
from app.providers.registry import Providers
from app.settings import settings

log = get_logger("jmp.route")
M = TypeVar("M", bound=BaseModel)
INSTITUTIONAL_TYPES = {"hospital", "clinic", "school", "college", "university", "doctors", "health"}


# --------------------------------------------------------------------------------------- caching
def _cache_key(kind: str, provider: str, parts: Any) -> str:
    raw = json.dumps([kind, provider, parts], sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def cached(session: Session | None, kind: str, provider: str, parts: Any, model: type[M],
           fn: Callable[[], M]) -> M:
    """Provider-response cache.

    Deliberately uses its OWN short transactions instead of the caller's session: a provider call can take
    minutes (Overpass), and holding a write transaction open across it serialises every other worker — on
    SQLite they block on the single writer lock until they time out. Read, release, call the provider, then
    write in a second short transaction. `session` is kept in the signature only to mean "caching enabled".
    """
    if session is None or provider == "mock":
        return fn()
    key = _cache_key(kind, provider, parts)
    now = datetime.now(timezone.utc)
    with session_scope() as db:                      # short read, transaction closed immediately
        row = db.get(RouteCache, key)
        if row is not None:
            exp = row.expires_at
            if exp is not None and exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp is None or exp > now:
                return model.model_validate(row.payload)

    value = fn()                                     # network call with NO transaction held

    payload = json.loads(value.model_dump_json())
    ttl = timedelta(days=settings().route_cache_ttl_days)
    try:
        with session_scope() as db:                  # short write
            existing = db.get(RouteCache, key)
            if existing is None:
                db.add(RouteCache(key=key, provider=provider, kind=kind, payload=payload,
                                  expires_at=now + ttl))
            else:
                existing.payload, existing.expires_at = payload, now + ttl
    except Exception:  # noqa: BLE001 - caching is an optimisation; never fail a journey over it
        log.warning("route_cache_write_failed", kind=kind, provider=provider)
    return value


# --------------------------------------------------------------------------------------- geocoding
def geocode_all(texts: Sequence[str], providers: Providers, session: Session | None = None) -> list[GeocodeResult]:
    out: list[GeocodeResult] = []
    for t in texts:
        g = cached(session, "geocode", providers.geocoder.name, t.strip().lower(), GeocodeResult,
                   lambda t=t: providers.geocoder.geocode(t))
        if g.confidence < 0.5:
            raise GeocodeError(f"Low-confidence location: {t!r}", code=ErrorCode.GEOCODE_AMBIGUOUS,
                               details={"candidates": g.candidates})
        out.append(g)
    return out


# ---------------------------------------------------------------------------------------- helpers
def _short(g: GeocodeResult, text: str) -> str:
    name = g.name or text
    name = name.split(",")[0].strip()
    return name if len(name) <= 30 else name[:29].rstrip() + "…"


def _intervals_union(intervals: list[tuple[float, float]]) -> float:
    total, cur = 0.0, None
    for a, b in sorted(intervals):
        if cur is None or a > cur[1]:
            if cur is not None:
                total += cur[1] - cur[0]
            cur = [a, b]
        else:
            cur[1] = max(cur[1], b)
    if cur is not None:
        total += cur[1] - cur[0]
    return total


def _level(value: float, thresholds: dict[str, float]) -> str:
    if value >= thresholds["HIGH"]:
        return "HIGH"
    if value >= thresholds["MODERATE"]:
        return "MODERATE"
    return "LOW"


def _category_at(cat_track: list[tuple[float, float, str, str | None]], km: float) -> str:
    for a, b, cat, _ in cat_track:
        if a <= km <= b:
            return cat
    return "unknown"


# ---------------------------------------------------------------------------------------- analysis
def analyse_route(
    *,
    inputs: list[str],
    geocoded: list[GeocodeResult],
    providers: Providers,
    vehicle_type: str,
    vehicle_type_specified: bool,
    travel_date: date | None,
    depart_time: str | None,
    session: Session | None = None,
) -> tuple[RouteFacts, RouteResult, FeatureSet | None, ElevationProfile | None]:
    rules = rules_config.hazard_rules()
    cats_cfg = rules["road_categories"]
    waypoints_ll = [(g.lat, g.lng) for g in geocoded]
    for i in range(len(waypoints_ll) - 1):
        if geo.haversine(waypoints_ll[i], waypoints_ll[i + 1]) < 1:
            raise RouteError(f"Consecutive locations resolve to the same point: {inputs[i]!r} / {inputs[i + 1]!r}")

    # ---- route (primary) ----
    route = cached(session, "route", providers.router.name, [[round(a, 6), round(b, 6)] for a, b in waypoints_ll],
                   RouteResult, lambda: providers.router.route(waypoints_ll, alternatives=len(waypoints_ll) == 2))
    full = route.geometry
    if len(full) < 2:
        raise RouteError("Route geometry unavailable from provider", code=ErrorCode.ROUTE_GEOMETRY_UNAVAILABLE)
    idx = geo.RouteIndex(full)
    total_geom_m = idx.total
    total_km = route.distance_m / 1000
    total_min = route.duration_s / 60
    scale = (route.distance_m / total_geom_m) if total_geom_m else 1.0  # geometry → provider distance

    # leg boundaries (km along route, provider-distance scale)
    legs: list[LegFact] = []
    cat_track: list[tuple[float, float, str, str | None]] = []  # (km_from, km_to, category, ref)
    pos_km = 0.0
    for leg in route.legs:
        lkm = leg.distance_m / 1000
        leg_cat_len: dict[str, float] = {}
        leg_len_m = sum(s.end_m - s.start_m for s in leg.spans) or leg.distance_m
        for s in leg.spans:
            f = lkm / (leg_len_m / 1000) if leg_len_m else 1
            a = pos_km + s.start_m / 1000 * f
            b = pos_km + s.end_m / 1000 * f
            cat_track.append((a, b, s.category, s.ref))
            leg_cat_len[s.category] = leg_cat_len.get(s.category, 0) + (b - a)
        dom = max(leg_cat_len, key=leg_cat_len.get) if leg_cat_len else "unknown"
        legs.append(LegFact(from_seq=leg.from_seq, to_seq=leg.to_seq, distance_km=round(lkm, 2),
                            duration_min=round(leg.duration_s / 60, 1), km_from=round(pos_km, 2),
                            km_to=round(pos_km + lkm, 2), dominant_category=dom))
        pos_km += lkm

    # ---- features & elevation ----
    fs: FeatureSet | None = None
    if providers.features is not None:
        simplified_key = [[round(a, 4), round(b, 4)] for a, b in geo.douglas_peucker(full, 30)]
        fs = cached(session, "features", providers.features.name, simplified_key, FeatureSet,
                    lambda: providers.features.features_along(full))  # type: ignore[union-attr]
    elev: ElevationProfile | None = None
    if providers.elevation is not None:
        step = rules["sampling"]["elevation_step_m"]
        simplified_key = [[round(a, 4), round(b, 4)] for a, b in geo.douglas_peucker(full, 30)]
        elev = cached(session, "elevation", providers.elevation.name, [step, simplified_key], ElevationProfile,
                      lambda: providers.elevation.profile(full, step))  # type: ignore[union-attr]

    # ---- road-type track: prefer OSM road samples when they cover most of the route ----
    samples = fs.road_samples if fs else []
    known = [s for s in samples if s.highway]
    if samples and len(known) >= 0.5 * len(samples):
        step_km = rules["sampling"]["road_sample_step_m"] / 1000
        cat_track = []
        for s in samples:
            km = idx.project((s.lat, s.lng))[0] * scale / 1000
            cat = categorize(s.highway, s.ref, s.surface)
            if cat == "unknown":
                cat = "local"
            cat_track.append((max(0.0, km - step_km / 2), km + step_km / 2, cat, s.ref))

    cat_len: dict[str, float] = {}
    cat_refs: dict[str, list[str]] = {}
    for a, b, cat, ref in cat_track:
        cat_len[cat] = cat_len.get(cat, 0) + (b - a)
        if ref and ref not in cat_refs.setdefault(cat, []) and "demo" not in ref:
            cat_refs[cat].append(ref)
    tot_len = sum(cat_len.values()) or 1
    order = rules["category_order"] + ["unknown"]
    road_types = [
        RoadTypeShare(category=c, label=cats_cfg.get(c, {}).get("label", "Unclassified road"), km=round(cat_len[c], 1),
                      pct=int(round(100 * cat_len[c] / tot_len)), refs=cat_refs.get(c, [])[:3])
        for c in order if cat_len.get(c, 0) / tot_len >= 0.02
    ]
    # renormalise rounded pcts to 100
    if road_types:
        diff = 100 - sum(r.pct for r in road_types)
        road_types[max(range(len(road_types)), key=lambda i: road_types[i].km)].pct += diff

    # ---- locate features on the route ----
    feat_cfg = rules["sampling"]
    located: list[LocatedFeature] = []
    for f in (fs.features if fs else []):
        km_m, off = idx.project((f.lat, f.lng))
        limit = 1500 if f.kind in ("settlement", "built_up", "forest", "industrial", "pedestrian_poi") else \
            max(feat_cfg["feature_buffer_m"] * 2, 60)
        if off > limit:
            continue
        located.append(LocatedFeature(kind=f.kind, km=round(km_m * scale / 1000, 2), lat=f.lat, lng=f.lng,
                                      name=f.name, length_m=f.length_m, tags=f.tags))
    located.sort(key=lambda x: x.km)

    # ---- waypoints ----
    wps: list[Waypoint] = []
    for i, (g, text) in enumerate(zip(geocoded, inputs, strict=True)):
        kind = "start" if i == 0 else ("end" if i == len(inputs) - 1 else "stop")
        km = legs[i - 1].km_to if i > 0 else 0.0
        wps.append(Waypoint(seq=i + 1, kind=kind, input_text=text, name=g.name, short_name=_short(g, text),
                            lat=g.lat, lng=g.lng, state=g.state, district=g.district, locality=g.locality,
                            place_types=g.place_types, km_from_start=round(km, 2),
                            is_institutional=bool(INSTITUTIONAL_TYPES & {t.lower() for t in g.place_types}),
                            geocode_confidence=g.confidence, geocode_provider=g.provider))
    is_round = geo.haversine(waypoints_ll[0], waypoints_ll[-1]) < 300

    # ---- exposures ----
    if samples:
        built_share = 100 * sum(1 for s in samples if s.built_up) / len(samples)
    else:
        ivs = [(f.km - (f.length_m or 0) / 2000, f.km + (f.length_m or 0) / 2000)
               for f in located if f.kind == "built_up"]
        built_share = 100 * min(1.0, _intervals_union(ivs) / total_km) if total_km else 0
    hw_share = 100 * (cat_len.get("expressway", 0) + cat_len.get("national_highway", 0)) / tot_len
    busy = sum(1 for f in located if f.kind == "busy_junction")
    lcs = [f for f in located if f.kind == "level_crossing"]
    settlements = sorted({f.name for f in located if f.kind == "settlement" and f.name})
    industrial = sum(1 for f in located if f.kind == "industrial")
    ex_cfg = rules["exposure_levels"]
    traffic_metric = busy / max(total_km / 10, 1) + built_share / 10
    hcv_level = _level(hw_share, ex_cfg["hcv"])
    if industrial and hcv_level == "LOW":
        hcv_level = "MODERATE"

    def _nearest_place(km: float) -> str:
        best = min(wps, key=lambda w: abs(w.km_from_start - km))
        return best.locality or best.short_name

    lc_places: list[str] = []
    for f in lcs:
        p = _nearest_place(f.km)
        if p not in lc_places:
            lc_places.append(p)

    # ---- travel context (season / night) ----
    states = []
    for w in wps:
        if w.state and w.state not in states:
            states.append(w.state)
    st_infos = [rules_config.state_info(s) for s in states] or [rules_config.state_info(None)]
    hilly = any(si.get("hilly") for si in st_infos)
    month = travel_date.month if travel_date else None
    seasonal: list[str] = []
    if month:
        if any(month in si.get("monsoon_months", []) for si in st_infos):
            seasonal.append("monsoon")
        if any(month in si.get("fog_months", []) for si in st_infos):
            seasonal.append("fog")
    night: bool | None = None
    sunset_s: str | None = None
    hi_min = total_min * rules["ranges"]["duration_factor"][1] + rules["ranges"]["dwell_minutes_per_stop"] * max(
        0, len(wps) - 2)
    if travel_date and depart_time:
        mid = full[len(full) // 2]
        st = local_sun_times(travel_date, mid[0], mid[1])
        if st:
            sunrise, sunset = st
            sunset_s = fmt_hhmm(sunset)
            hh, mm = (int(x) for x in depart_time.split(":"))
            start_h = hh + mm / 60
            end_h = start_h + hi_min / 60
            buf = rules["detectors"]["HZ-23"]["sunset_buffer_min"] / 60
            night = start_h < sunrise or end_h > (sunset - buf) or end_h > 24
    travel = TravelContext(vehicle_type=vehicle_type, vehicle_type_specified=vehicle_type_specified,  # type: ignore[arg-type]
                           travel_date=travel_date.isoformat() if travel_date else None, travel_month=month,
                           travel_month_name=calendar.month_name[month] if month else None,
                           depart_time=depart_time, night_overlap=night, sunset_local=sunset_s)

    exposures = Exposures(
        built_up_share_pct=int(round(built_share)), highway_share_pct=int(round(hw_share)),
        pedestrian_level=_level(built_share, ex_cfg["pedestrian"]), hcv_level=hcv_level,
        traffic_level=_level(traffic_metric, ex_cfg["traffic"]), busy_junctions=busy, level_crossings=len(lcs),
        level_crossing_places=lc_places, settlements=len(set(settlements) | {w.locality for w in wps if w.locality}),
        settlement_names=settlements[:12], industrial_areas=industrial, seasonal_flags=seasonal)

    # ---- segments (merge consecutive legs with the same dominant road category) ----
    segs = _build_segments(legs, wps, cat_track, cats_cfg, rules["segments"]["max_segments"])

    # ---- complexity ----
    cx = rules["complexity"]
    n_types = len(road_types)
    if len(wps) >= cx["high_if"]["waypoints_gte"] or n_types >= cx["high_if"]["road_types_gte"] or hilly:
        complexity = "HIGH"
    elif len(wps) <= cx["low_if"]["waypoints_lte"] and n_types <= cx["low_if"]["road_types_lte"]:
        complexity = "LOW"
    else:
        complexity = "MODERATE"
    loop_word = "loop" if is_round else "route"
    complexity_basis = f"{len(wps)}-pt {loop_word}, {n_types} road type{'s' if n_types != 1 else ''}"

    # ---- alternatives (D-11): provider alternatives for the whole route (2 points) or longest legs ----
    alternatives = _alternatives(route, wps, providers, session)

    # ---- elevation summary ----
    elev_summary: dict[str, float] = {}
    if elev and elev.samples:
        es = [e for _, e in elev.samples]
        elev_summary = {"min_m": round(min(es), 1), "max_m": round(max(es), 1), "relief_m": round(max(es) - min(es), 1)}

    # ---- names ----
    start, end = wps[0], wps[-1]
    route_name = (f"{start.short_name} Circular Loop" if is_round else f"{start.short_name} → {end.short_name}")
    districts = []
    for w in wps:
        if w.district and w.district not in districts:
            districts.append(w.district)
    region_label = " / ".join(states) if states else "India"
    if districts and len(districts) <= 3:
        region_label += " (" + " & ".join(districts) + ")"
    n_stops = len(wps) - 2
    parts = ["Round-trip" if is_round else "One-way"]
    parts.append(f"multi-stop ({n_stops} stops)" if n_stops > 1 else ("single-stop" if n_stops == 1 else "direct"))
    jt = " ".join(parts)
    if any(w.is_institutional for w in wps):
        jt += " + institutional call"
    if any("industrial" in [t.lower() for t in w.place_types] for w in wps):
        jt += " + site visit"

    geometry_simplified = geo.douglas_peucker(full, 20)
    return (
        RouteFacts(
            waypoints=wps, legs=legs, geometry=[(round(a, 6), round(b, 6)) for a, b in geometry_simplified],
            distance_km=round(total_km, 2), duration_min=round(total_min, 1),
            distance_range_km=(int(round(total_km * rules["ranges"]["distance_factor"][0])),
                               int(round(total_km * rules["ranges"]["distance_factor"][1]))),
            duration_range_min=(int(round(total_min * rules["ranges"]["duration_factor"][0])), int(round(hi_min))),
            waypoint_count=len(wps), intermediate_stops=n_stops, is_round_trip=is_round,
            road_types=road_types, road_type_count=n_types, segments=segs, exposures=exposures,
            complexity=complexity, complexity_basis=complexity_basis, alternatives=alternatives,
            features=located, elevation=elev_summary, states=states, region_label=region_label,
            route_name=route_name, journey_type=jt, travel=travel, hilly_region=hilly,
            providers=providers.describe(), is_demo_data=providers.is_demo,
            provider_warnings=list(route.warnings) + (fs.warnings if fs else []),
        ),
        route, fs, elev,
    )


def _build_segments(legs: list[LegFact], wps: list[Waypoint], cat_track: list[tuple[float, float, str, str | None]],
                    cats_cfg: dict[str, Any], max_segments: int) -> list[Segment]:
    groups: list[list[LegFact]] = []
    for leg in legs:
        if groups and groups[-1][-1].dominant_category == leg.dominant_category:
            groups[-1].append(leg)
        else:
            groups.append([leg])
    while len(groups) > max_segments:  # merge the shortest group into its neighbour
        i = min(range(len(groups)), key=lambda k: sum(x.distance_km for x in groups[k]))
        j = i - 1 if i > 0 else i + 1
        a, b = sorted((i, j))
        groups[a:b + 1] = [groups[a] + groups[b]]
    segs: list[Segment] = []
    by_seq = {w.seq: w for w in wps}
    for n, g in enumerate(groups, start=1):
        km_from, km_to = g[0].km_from, g[-1].km_to
        seq_chain = [g[0].from_seq + 1] + [leg.to_seq + 1 for leg in g]
        names = [by_seq[s].short_name for s in seq_chain]
        shares: dict[str, float] = {}
        refs: dict[str, list[str]] = {}
        for a, b, cat, ref in cat_track:
            ov = min(b, km_to) - max(a, km_from)
            if ov > 0:
                shares[cat] = shares.get(cat, 0) + ov
                if ref and "demo" not in ref and ref not in refs.setdefault(cat, []):
                    refs[cat].append(ref)
        tot = sum(shares.values()) or 1
        main = [c for c, v in sorted(shares.items(), key=lambda kv: -kv[1]) if v / tot >= 0.2][:2] or ["unknown"]
        chars = []
        for c in main:
            label = cats_cfg.get(c, {}).get("label", "Road")
            if refs.get(c):
                label += f" ({', '.join(refs[c][:2])})"
            chars.append(label)
        segs.append(Segment(
            id=f"S{n}", from_seq=seq_chain[0], to_seq=seq_chain[-1], label=" → ".join(names),
            road_character=" / ".join(chars), dominant_category=main[0], km_from=round(km_from, 2),
            km_to=round(km_to, 2), distance_km=round(km_to - km_from, 2),
            duration_min=round(sum(leg.duration_min for leg in g), 1)))
    return segs


def _alternatives(route: RouteResult, wps: list[Waypoint], providers: Providers,
                  session: Session | None) -> list[Alternative]:
    out: list[Alternative] = []
    tol = 25.0
    if len(wps) == 2:
        for k, alt in enumerate(route.alternatives[:2]):
            prim = route.legs[0]
            out.append(Alternative(
                id="AB"[k], from_seq=1, to_seq=2, leg_label=f"{wps[0].short_name} → {wps[1].short_name}",
                distance_km=round(alt.distance_m / 1000, 2),
                delta_km=round((alt.distance_m - prim.distance_m) / 1000, 1),
                delta_min=round((alt.duration_s - prim.duration_s) / 60, 1), via=alt.via,
                geometry=geo.douglas_peucker(alt.geometry, tol), primary_geometry=geo.douglas_peucker(prim.geometry, tol)))
        return out
    # multi-stop: ask the provider for alternatives on the two longest legs (2-point requests)
    longest = sorted(route.legs, key=lambda leg: -leg.distance_m)[:2]
    for leg in sorted(longest, key=lambda leg: leg.from_seq):
        if leg.distance_m < 3000 or len(out) >= 2:
            continue
        a, b = wps[leg.from_seq], wps[leg.to_seq]
        pts = [(a.lat, a.lng), (b.lat, b.lng)]
        try:
            res = cached(session, "route", providers.router.name, [[round(x, 6), round(y, 6)] for x, y in pts] + ["alt"],
                         RouteResult, lambda pts=pts: providers.router.route(pts, alternatives=True))
        except Exception:  # alternatives are optional (D-11) — never fail the journey for them
            continue
        if not res.alternatives:
            continue
        alt = res.alternatives[0]
        base = res.legs[0]
        out.append(Alternative(
            id="AB"[len(out)], from_seq=a.seq, to_seq=b.seq, leg_label=f"{a.short_name} → {b.short_name}",
            distance_km=round(alt.distance_m / 1000, 2), delta_km=round((alt.distance_m - base.distance_m) / 1000, 1),
            delta_min=round((alt.duration_s - base.duration_s) / 60, 1), via=alt.via,
            geometry=geo.douglas_peucker(alt.geometry, tol), primary_geometry=geo.douglas_peucker(base.geometry, tol)))
    return out
