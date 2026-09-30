"""ReportModel → HTML (Jinja2, StrictUndefined). Builds the SVG figures from real geometry. Never calls the LLM."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from markupsafe import Markup

from app import rules_config
from app.rendering.svg.icons import HAZARD_ICON, icon
from app.rendering.svg.maps import (
    MapMarker,
    MapNode,
    SpinePointer,
    SpineStop,
    alt_route_mini,
    gauge,
    hazard_spine,
    route_map,
)
from app.services.report_assembler import ReportModel
from app.settings import settings


def _hm(minutes: float) -> str:
    m = int(round(minutes))
    return f"{m // 60}h{m % 60:02d}m"


def template_dir() -> Path:
    return Path(settings().templates_dir) / "jmp" / f"v{settings().template_version.split('.')[0]}"


@lru_cache(maxsize=4)
def _env(path: str) -> Environment:
    env = Environment(loader=FileSystemLoader(path), undefined=StrictUndefined,
                      autoescape=select_autoescape(["html", "j2"], default_for_string=True, default=True),
                      trim_blocks=True, lstrip_blocks=True)
    env.filters["hm"] = _hm
    env.globals["icon"] = icon
    env.globals["hazard_icon"] = HAZARD_ICON
    return env


@lru_cache(maxsize=4)
def _assets(path: str) -> tuple[str, str]:
    d = Path(path)
    styles = (d / "styles.css").read_text(encoding="utf-8")
    fonts = (d / "fonts" / "fonts.css").read_text(encoding="utf-8")
    fonts_uri = (d / "fonts").resolve().as_uri()
    fonts = re.sub(r"url\(([^)/:]+)\)", lambda m: f"url({fonts_uri}/{m.group(1)})", fonts)
    return styles, fonts


@dataclass
class DrawnStop:
    number: int      # the stop's number as printed in the stops table (submitted order)
    label: str       # the stop exactly as submitted
    lat: float
    lng: float
    km: float
    kind: str        # start | stop | end | institutional | unplaced


def _nearest_index(geometry: list[tuple[float, float]], lat: float, lng: float) -> int:
    return min(range(len(geometry)), key=lambda i: (geometry[i][0] - lat) ** 2 + (geometry[i][1] - lng) ** 2)


def drawn_stops(r: ReportModel) -> list[DrawnStop]:
    """Every submitted stop, for the drawings only, numbered and labelled as submitted.

    A located stop is drawn at its point. A stop that was not located has no coordinates, so it is drawn
    hollow, in its submitted order, spaced along the drawn route line between the located stops either side
    of it. That placement is a drawing aid only: it is never used for a distance, a time or a hazard, and
    the map legend and caption say it is not a verified position.
    """
    route = r.route
    wps = {w.seq: w for w in route.waypoints}
    if not route.itinerary or not route.geometry:  # older documents; or no geometry (route_map reports that)
        return [DrawnStop(w.seq, w.short_name, w.lat, w.lng, w.km_from_start,
                          "institutional" if w.is_institutional else w.kind) for w in route.waypoints]
    geom = list(route.geometry)
    gidx = {seq: _nearest_index(geom, w.lat, w.lng) for seq, w in wps.items()}
    total = max(route.distance_km, 0.0)
    out: list[DrawnStop] = []
    stops = route.itinerary
    for i, s in enumerate(stops):
        if s.waypoint_seq is not None and s.status != "not_located":
            w = wps[s.waypoint_seq]
            kind = "institutional" if w.is_institutional else s.kind
            out.append(DrawnStop(s.position, s.input_text, w.lat, w.lng, w.km_from_start, kind))
            continue
        # the run of unlocated stops this one sits in, and the located stops either side of it
        a = i
        while a > 0 and stops[a - 1].status == "not_located":
            a -= 1
        b = i
        while b < len(stops) - 1 and stops[b + 1].status == "not_located":
            b += 1
        prev_w = wps[stops[a - 1].waypoint_seq] if a > 0 else None  # type: ignore[index]
        next_w = wps[stops[b + 1].waypoint_seq] if b < len(stops) - 1 else None  # type: ignore[index]
        i0 = gidx[prev_w.seq] if prev_w else 0
        i1 = gidx[next_w.seq] if next_w else len(geom) - 1
        k0 = prev_w.km_from_start if prev_w else 0.0
        k1 = next_w.km_from_start if next_w else total
        frac = (i - a + 1) / (b - a + 2)
        gi = int(round(i0 + (i1 - i0) * frac))
        lat, lng = geom[max(0, min(len(geom) - 1, gi))]
        out.append(DrawnStop(s.position, s.input_text, lat, lng, k0 + (k1 - k0) * frac, "unplaced"))
    return out


def build_figures(r: ReportModel) -> dict[str, object]:
    route = r.route
    placed = drawn_stops(r)
    nodes = [MapNode(seq=d.number, label=d.label, lat=d.lat, lng=d.lng, kind=d.kind) for d in placed]
    unplaced = any(d.kind == "unplaced" for d in placed)
    markers: list[MapMarker] = []
    for i, row in enumerate(r.pointers, start=1):
        for loc in row.hazard.locations[:3]:
            if loc.lat or loc.lng:
                markers.append(MapMarker(lat=loc.lat, lng=loc.lng, band=row.hazard.display_band, label=row.display_name,
                                         number=i))
    map_p1 = route_map(route.geometry, nodes, markers, width=440, height=250, style="light", font=6.6,
                       show_legend=False, show_north=False,
                       caption=("Route visualisation from provider geometry — not to scale"
                               + (" · hollow = stop not located, shown in order" if unplaced else "")))
    # page 3 shares the page with the segment table: give rows back from the map as the table grows
    p3_height = max(190, 300 - 24 * max(0, len(r.segments) - 4))
    map_p3 = route_map(route.geometry, nodes, markers, width=620, height=p3_height, style="dark", font=7.2,
                       caption=("Schematic route diagram — stop sequence and measured hazard positions only"
                               + (" · hollow stops: not located, drawn in order, not measured" if unplaced else "")))
    alts = {}
    for a in r.alternatives:
        wps = {w.seq: w for w in route.waypoints}
        alts[a.alt.id] = alt_route_mini(a.alt.primary_geometry, a.alt.geometry, wps[a.alt.from_seq].short_name,
                                        wps[a.alt.to_seq].short_name)
    stops = [SpineStop(seq=d.number, label=f"{d.number:02d} {d.label}", km=d.km, kind=d.kind) for d in placed]
    pointers = []
    for i, row in enumerate(r.pointers, start=1):
        h = row.hazard
        located = [loc for loc in h.locations if loc.lat or loc.lng]
        pointers.append(SpinePointer(
            number=i, band=h.display_band, title=row.display_name, context=row.route_context,
            control=h.short_control, evidence=h.evidence,
            km_from=None if h.route_wide or not located else min(loc.km_from for loc in located),
            km_to=None if h.route_wide or not located else max(loc.km_to for loc in located),
            spans=[(loc.km_from, loc.km_to) for loc in located]))
    # page 6 shares the page with the verification table: give rows back from the spine as that list grows
    spine_h = max(360, 470 - 24 * max(0, len(r.verification) - 5))
    spine = hazard_spine(stops, pointers, route.distance_km, height=spine_h)
    return {"map_p1": map_p1, "map_p3": map_p3, "alts": alts, "gauge": gauge(r.scores.total), "spine": spine}


def render_html(r: ReportModel) -> str:
    d = str(template_dir())
    env = _env(d)
    styles, fonts = _assets(d)
    tmpl = env.get_template("report.html.j2")
    return tmpl.render(r=r, svg=build_figures(r), styles_css=Markup(styles), fonts_css=Markup(fonts),
                       risk_method=rules_config.risk_matrix().get("display_band_method", "severity_band"))
