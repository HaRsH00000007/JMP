"""ReportModel → HTML (Jinja2, StrictUndefined). Builds the SVG figures from real geometry. Never calls the LLM."""

from __future__ import annotations

import re
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


def build_figures(r: ReportModel) -> dict[str, object]:
    route = r.route
    nodes = []
    for w in route.waypoints:
        kind = "institutional" if w.is_institutional else w.kind
        nodes.append(MapNode(seq=w.seq, label=w.short_name, lat=w.lat, lng=w.lng, kind=kind))
    markers: list[MapMarker] = []
    for i, row in enumerate(r.pointers, start=1):
        for loc in row.hazard.locations[:3]:
            if loc.lat or loc.lng:
                markers.append(MapMarker(lat=loc.lat, lng=loc.lng, band=row.hazard.display_band, label=row.display_name,
                                         number=i))
    map_p1 = route_map(route.geometry, nodes, markers, width=440, height=250, style="light", font=6.6,
                       show_legend=False, show_north=False,
                       caption="Route visualisation from provider geometry — not to scale")
    # page 3 shares the page with the segment table: give rows back from the map as the table grows
    p3_height = max(190, 300 - 24 * max(0, len(r.segments) - 4))
    map_p3 = route_map(route.geometry, nodes, markers, width=620, height=p3_height, style="dark", font=7.2,
                       caption="Schematic route diagram — stop sequence and measured hazard positions only")
    alts = {}
    for a in r.alternatives:
        wps = {w.seq: w for w in route.waypoints}
        alts[a.alt.id] = alt_route_mini(a.alt.primary_geometry, a.alt.geometry, wps[a.alt.from_seq].short_name,
                                        wps[a.alt.to_seq].short_name)
    stops = [SpineStop(seq=w.seq, label=f"{w.seq:02d} {w.short_name}"[:24], km=w.km_from_start,
                       kind="institutional" if w.is_institutional else w.kind) for w in route.waypoints]
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
    spine = hazard_spine(stops, pointers, route.distance_km)
    return {"map_p1": map_p1, "map_p3": map_p3, "alts": alts, "gauge": gauge(r.scores.total), "spine": spine}


def render_html(r: ReportModel) -> str:
    d = str(template_dir())
    env = _env(d)
    styles, fonts = _assets(d)
    tmpl = env.get_template("report.html.j2")
    return tmpl.render(r=r, svg=build_figures(r), styles_css=Markup(styles), fonts_css=Markup(fonts),
                       risk_method=rules_config.risk_matrix().get("display_band_method", "severity_band"))
