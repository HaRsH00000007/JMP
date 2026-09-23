"""Route maps, alternative-route minis, hazard-pointer spine and score gauge — as inline SVG.

Maps are drawn from the provider's route geometry (projected, simplified, fitted to the box). Nothing is
invented: if the geometry is missing, `route_map` raises and the job fails with
ROUTE_GEOMETRY_UNAVAILABLE. Hazard markers are only drawn where a detector measured a location.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from html import escape

from markupsafe import Markup

from app.errors import ErrorCode, RenderError

Coord = tuple[float, float]

NAVY = "#1A396A"
NAVY_DEEP = "#0A2545"
ROUTE = "#2F5597"
ROUTE_LIGHT = "#A8C4E6"
GREEN = "#2E7D32"
RED = "#C0392B"
AMBER = "#E7A61F"
MUTED = "#6A717F"
BAND_FILL = {"HIGH": "#D03237", "MEDIUM": "#E7A61F", "LOW": "#2D7C31"}


@dataclass
class Projector:
    minx: float
    miny: float
    scale: float
    ox: float
    oy: float
    h: float
    kx: float

    def __call__(self, p: Coord) -> tuple[float, float]:
        x = (p[1] * self.kx - self.minx) * self.scale + self.ox
        y = self.h - ((p[0] - self.miny) * self.scale + self.oy)
        return round(x, 1), round(y, 1)


def fit(points: Sequence[Coord], w: float, h: float, pad: float) -> Projector:
    if not points:
        raise RenderError("Route geometry unavailable", code=ErrorCode.ROUTE_GEOMETRY_UNAVAILABLE)
    lat0 = sum(p[0] for p in points) / len(points)
    kx = math.cos(math.radians(lat0))
    xs = [p[1] * kx for p in points]
    ys = [p[0] for p in points]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    spanx = max(maxx - minx, 1e-4)
    spany = max(maxy - miny, 1e-4)
    scale = min((w - 2 * pad) / spanx, (h - 2 * pad) / spany)
    ox = pad + ((w - 2 * pad) - spanx * scale) / 2
    oy = pad + ((h - 2 * pad) - spany * scale) / 2
    return Projector(minx=minx, miny=miny, scale=scale, ox=ox, oy=oy, h=h, kx=kx)


def _path(pts: Sequence[tuple[float, float]]) -> str:
    return "M" + " L".join(f"{x},{y}" for x, y in pts)


def _rects_overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


@dataclass
class MapNode:
    seq: int
    label: str
    lat: float
    lng: float
    kind: str  # start | stop | end | institutional


@dataclass
class MapMarker:
    lat: float
    lng: float
    band: str
    label: str
    number: int | None = None


def route_map(geometry: Sequence[Coord], nodes: Sequence[MapNode], markers: Sequence[MapMarker], *, width: int,
              height: int, style: str = "light", font: float = 7.0, show_legend: bool = True,
              show_north: bool = True, caption: str | None = None) -> Markup:
    if len(geometry) < 2:
        raise RenderError("Route geometry unavailable", code=ErrorCode.ROUTE_GEOMETRY_UNAVAILABLE)
    proj = fit(list(geometry) + [(n.lat, n.lng) for n in nodes], width, height, pad=34)
    pts = [proj(p) for p in geometry]
    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="100%" '
        f'preserveAspectRatio="xMidYMid meet" font-family="Arimo, Arial, sans-serif">'
    ]
    line_col = ROUTE_LIGHT if style == "light" else ROUTE
    lw = 5 if style == "light" else 2.6
    parts.append(f'<path d="{_path(pts)}" fill="none" stroke="{line_col}" stroke-width="{lw}" '
                 f'stroke-linejoin="round" stroke-linecap="round"/>')
    if style != "light":  # direction chevrons every ~18% of the path
        n = len(pts)
        for k in range(1, 6):
            i = int(n * k / 6)
            if 0 < i < n - 1:
                (x1, y1), (x2, y2) = pts[i - 1], pts[i + 1]
                ang = math.degrees(math.atan2(y2 - y1, x2 - x1))
                x, y = pts[i]
                parts.append(f'<path d="M-3,-3 L3,0 L-3,3" transform="translate({x},{y}) rotate({ang:.0f})" '
                             f'fill="none" stroke="{ROUTE}" stroke-width="1.4"/>')

    occupied: list[tuple[float, float, float, float]] = []
    placed: list[tuple[float, float]] = []
    node_r = 6.5 if style == "light" else 8.5
    for nd in nodes:
        x, y = proj((nd.lat, nd.lng))
        for px, py in placed:  # separate coincident nodes (round trips)
            if abs(px - x) < node_r * 1.6 and abs(py - y) < node_r * 1.6:
                x += node_r * 1.9
        placed.append((x, y))
        occupied.append((x - node_r, y - node_r, x + node_r, y + node_r))

    for m in markers:
        x, y = proj((m.lat, m.lng))
        col = BAND_FILL.get(m.band, RED)
        parts.append(f'<g transform="translate({x},{y})"><path d="M0,-8 L7,5 L-7,5 Z" fill="{col}" '
                     f'stroke="#fff" stroke-width="1.2"/>'
                     + (f'<text y="3.4" text-anchor="middle" font-size="6.2" font-weight="700" fill="#fff">'
                        f'{m.number}</text>' if m.number is not None else "") + "</g>")
        occupied.append((x - 7, y - 8, x + 7, y + 5))

    for nd, (x, y) in zip(nodes, placed, strict=True):
        fill = {"start": GREEN, "end": GREEN, "institutional": RED}.get(nd.kind, NAVY if style != "light" else "#2F6DB5")
        parts.append(f'<circle cx="{x}" cy="{y}" r="{node_r}" fill="{fill}" stroke="#fff" stroke-width="1.5"/>')
        if style != "light":
            parts.append(f'<text x="{x}" y="{y + 2.6}" text-anchor="middle" font-size="{font - 0.6}" font-weight="700" '
                         f'fill="#fff">{nd.seq:02d}</text>')
        text = nd.label if style != "light" else f"{nd.seq} {nd.label}"
        tw = len(text) * font * 0.52
        th = font + 2
        best = None
        for dx, dy, anchor in ((node_r + 3, 3, "start"), (-node_r - 3, 3, "end"), (0, -node_r - 4, "middle"),
                               (0, node_r + font + 2, "middle"), (node_r + 2, -node_r - 2, "start"),
                               (-node_r - 2, -node_r - 2, "end"), (node_r + 2, node_r + font, "start"),
                               (-node_r - 2, node_r + font, "end")):
            tx, ty = x + dx, y + dy
            x0 = tx if anchor == "start" else (tx - tw if anchor == "end" else tx - tw / 2)
            box = (x0, ty - th + 2, x0 + tw, ty + 2)
            inside = box[0] >= 2 and box[2] <= width - 2 and box[1] >= 2 and box[3] <= height - 2
            hits = sum(_rects_overlap(box, o) for o in occupied)
            score = hits * 10 + (0 if inside else 25)
            if best is None or score < best[0]:
                best = (score, tx, ty, anchor, box)
        assert best is not None
        _, tx, ty, anchor, box = best
        occupied.append(box)
        weight = "700" if nd.kind in ("start", "end", "institutional") else "400"
        parts.append(f'<text x="{tx:.1f}" y="{ty:.1f}" text-anchor="{anchor}" font-size="{font}" '
                     f'font-weight="{weight}" fill="{NAVY_DEEP}" paint-order="stroke" stroke="#fff" '
                     f'stroke-width="2.4">{escape(text)}</text>')

    if show_north:
        parts.append(f'<g transform="translate({width - 22},26)"><circle r="11" fill="#fff" stroke="{MUTED}" '
                     f'stroke-width=".8"/><path d="M0,-8 L3,3 L0,1 L-3,3 Z" fill="{NAVY}"/>'
                     f'<text y="-13" text-anchor="middle" font-size="7" fill="{MUTED}">N</text></g>')
    if show_legend:
        lx, ly = 8, height - 44
        items = [(GREEN, "circle", "Start / End"), (NAVY if style != "light" else "#2F6DB5", "circle", "Waypoint"),
                 (RED, "circle", "Institutional stop"), (BAND_FILL["HIGH"], "tri", "Hazard (see pointers)")]
        parts.append(f'<rect x="{lx}" y="{ly}" width="150" height="40" rx="3" fill="#fff" fill-opacity=".92" '
                     f'stroke="#C7D4E7" stroke-width=".8"/>')
        for i, (col, shape, text) in enumerate(items):
            cx = lx + 10 + (i % 2) * 72
            cy = ly + 12 + (i // 2) * 16
            if shape == "circle":
                parts.append(f'<circle cx="{cx}" cy="{cy}" r="4" fill="{col}"/>')
            else:
                parts.append(f'<path d="M{cx},{cy - 5} L{cx + 4.5},{cy + 3} L{cx - 4.5},{cy + 3} Z" fill="{col}"/>')
            parts.append(f'<text x="{cx + 7}" y="{cy + 2.5}" font-size="6.2" fill="{MUTED}">{escape(text)}</text>')
    if caption:
        parts.append(f'<text x="{width - 6}" y="{height - 5}" text-anchor="end" font-size="6" font-style="italic" '
                     f'fill="{MUTED}">{escape(caption)}</text>')
    parts.append("</svg>")
    return Markup("".join(parts))


def alt_route_mini(primary: Sequence[Coord], alt: Sequence[Coord], from_label: str, to_label: str, *, width: int = 300,
                   height: int = 150) -> Markup:
    if len(primary) < 2 or len(alt) < 2:
        raise RenderError("Alternative geometry unavailable", code=ErrorCode.ROUTE_GEOMETRY_UNAVAILABLE)
    proj = fit(list(primary) + list(alt), width, height, pad=26)
    p1 = [proj(p) for p in primary]
    p2 = [proj(p) for p in alt]
    a, b = p1[0], p1[-1]
    return Markup(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="100%" '
        f'font-family="Arimo, Arial, sans-serif">'
        f'<path d="{_path(p1)}" fill="none" stroke="{ROUTE}" stroke-width="2.6" stroke-linejoin="round"/>'
        f'<path d="{_path(p2)}" fill="none" stroke="{AMBER}" stroke-width="2.2" stroke-dasharray="7 5" '
        f'stroke-linejoin="round"/>'
        f'<circle cx="{a[0]}" cy="{a[1]}" r="6" fill="{GREEN}" stroke="#fff" stroke-width="1.4"/>'
        f'<circle cx="{b[0]}" cy="{b[1]}" r="6" fill="{GREEN}" stroke="#fff" stroke-width="1.4"/>'
        f'<text x="{a[0]}" y="{a[1] - 9}" text-anchor="middle" font-size="7.5" font-weight="700" fill="{NAVY_DEEP}" '
        f'paint-order="stroke" stroke="#fff" stroke-width="2.2">{escape(from_label)}</text>'
        f'<text x="{b[0]}" y="{b[1] - 9}" text-anchor="middle" font-size="7.5" font-weight="700" fill="{NAVY_DEEP}" '
        f'paint-order="stroke" stroke="#fff" stroke-width="2.2">{escape(to_label)}</text>'
        f'<text x="6" y="{height - 5}" font-size="6" font-style="italic" fill="{MUTED}">Primary (solid) — '
        f'Alternative (dashed) — from route geometry</text></svg>')


def gauge(score: int, *, size: int = 150, color: str = AMBER) -> Markup:
    r = size / 2 - 12
    c = size / 2
    circ = 2 * math.pi * r
    frac = max(0.0, min(1.0, score / 100))
    return Markup(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" width="{size}" height="{size}">'
        f'<circle cx="{c}" cy="{c}" r="{r}" fill="none" stroke="#F3E3B5" stroke-width="16"/>'
        f'<circle cx="{c}" cy="{c}" r="{r}" fill="none" stroke="{color}" stroke-width="16" '
        f'stroke-dasharray="{circ * frac:.1f} {circ:.1f}" transform="rotate(-90 {c} {c})" stroke-linecap="butt"/>'
        f'<circle cx="{c}" cy="{c}" r="{r - 12}" fill="#fff"/>'
        f'<text x="{c}" y="{c + 5}" text-anchor="middle" font-family="Arimo, Arial, sans-serif" font-size="24" '
        f'font-weight="700" fill="{NAVY_DEEP}">{score}</text>'
        f'<text x="{c}" y="{c + 19}" text-anchor="middle" font-family="Arimo, Arial, sans-serif" font-size="8" '
        f'fill="{MUTED}">/ 100 SCORE</text></svg>')


@dataclass
class SpineStop:
    seq: int
    label: str
    km: float
    kind: str


@dataclass
class SpinePointer:
    number: int
    band: str
    title: str
    context: str
    control: str
    km_from: float | None  # None = route-wide
    km_to: float | None
    evidence: str
    spans: list[tuple[float, float]] | None = None  # each measured location (km_from, km_to)


def _wrap(text: str, max_chars: int, max_lines: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + (1 if cur else 0) > max_chars:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}" if cur else w
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][: max_chars - 1].rstrip() + "…"
    return lines


def hazard_spine(stops: Sequence[SpineStop], pointers: Sequence[SpinePointer], total_km: float, *, width: int = 540,
                 height: int = 470) -> Markup:
    """Vertical START→END spine with stops at their km positions and hazard callouts at measured positions.
    Route-wide hazards (no location) are shown in a separate band, never at an invented position."""
    top, bottom = 34, height - 30
    spine_x = 128
    total = max(total_km, 0.1)

    def ky(km: float) -> float:
        return top + (bottom - top) * max(0.0, min(1.0, km / total))

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="100%" '
             f'font-family="Arimo, Arial, sans-serif">']
    parts.append(f'<line x1="{spine_x}" y1="{top}" x2="{spine_x}" y2="{bottom}" stroke="{ROUTE_LIGHT}" '
                 f'stroke-width="10" stroke-linecap="round"/>')
    parts.append(f'<line x1="{spine_x}" y1="{top}" x2="{spine_x}" y2="{bottom}" stroke="{ROUTE}" stroke-width="2"/>')
    # km ticks
    step = next(s for s in (1, 2, 5, 10, 20, 25, 50, 100, 200, 500) if total / s <= 10)
    k = 0.0
    while k <= total + 1e-6:
        y = ky(k)
        parts.append(f'<line x1="2" y1="{y:.1f}" x2="{spine_x - 6}" y2="{y:.1f}" stroke="#E3EAF4" '
                     f'stroke-width=".6"/><text x="2" y="{y - 1.5:.1f}" font-size="5.6" '
                     f'fill="{MUTED}">{k:g} km</text>')
        k += step
    # stops
    last_label_y = -99.0
    for st in stops:
        y = ky(st.km)
        col = GREEN if st.kind in ("start", "end") else (RED if st.kind == "institutional" else NAVY)
        r = 7 if st.kind in ("start", "end") else 4.5
        parts.append(f'<circle cx="{spine_x}" cy="{y:.1f}" r="{r}" fill="{col}" stroke="#fff" stroke-width="1.5"/>')
        if st.kind in ("start", "end"):
            parts.append(f'<text x="{spine_x}" y="{y + 2.3:.1f}" text-anchor="middle" font-size="5.2" '
                         f'font-weight="700" fill="#fff">{"S" if st.kind == "start" else "E"}</text>')
        if y - last_label_y >= 9:
            parts.append(f'<text x="{spine_x - 12}" y="{y - 5:.1f}" text-anchor="end" font-size="6.6" '
                         f'font-weight="{"700" if st.kind in ("start", "end") else "400"}" fill="{NAVY_DEEP}">'
                         f'{escape(st.label[:24])}</text>')
            last_label_y = y
    # callouts for located pointers
    located = [p for p in pointers if p.km_from is not None]
    box_x, box_w, box_h, gap = 186, width - 194, 58, 8
    slots: list[float] = []
    for p in sorted(located, key=lambda q: q.km_from or 0):
        target = ky(((p.km_from or 0) + (p.km_to or p.km_from or 0)) / 2) - box_h / 2
        y = max(top - 10, target)
        if slots and y < slots[-1] + box_h + gap:
            y = slots[-1] + box_h + gap
        slots.append(y)
    overflow = slots and slots[-1] + box_h > height - 4
    if overflow:  # compress evenly to fit
        n = len(slots)
        avail = (height - 4) - (top - 10)
        box_h = min(box_h, (avail - gap * (n - 1)) / n)
        slots = [top - 10 + i * (box_h + gap) for i in range(n)]
    for p, y in zip(sorted(located, key=lambda q: q.km_from or 0), slots, strict=True):
        col = BAND_FILL.get(p.band, RED)
        spans = p.spans or [(p.km_from or 0, p.km_to or p.km_from or 0)]
        anchor_y, ax = None, spine_x + 19
        for k0, k1 in spans[:8]:
            y0, y1 = ky(k0), ky(k1)
            if k1 - k0 > total * 0.01:
                parts.append(f'<path d="M{spine_x + 9},{y0:.1f} h6 V{y1:.1f} h-6" fill="none" stroke="{col}" '
                             f'stroke-width="2.2"/>')
                if anchor_y is None:
                    anchor_y, ax = (y0 + y1) / 2, spine_x + 15
            else:
                parts.append(f'<g transform="translate({spine_x + 13},{y0:.1f})"><path d="M0,-6 L5.5,4 L-5.5,4 Z" '
                             f'fill="{col}"/></g>')
                if anchor_y is None:
                    anchor_y, ax = y0, spine_x + 19
        assert anchor_y is not None
        cy = y + box_h / 2
        parts.append(f'<path d="M{ax},{anchor_y:.1f} C{ax + 25},{anchor_y:.1f} {box_x - 25},{cy:.1f} {box_x},{cy:.1f}" '
                     f'fill="none" stroke="{col}" stroke-width="1.1"/>')
        parts.append(_callout(p, box_x, y, box_w, box_h, col))
    # route-wide band
    wide = [p for p in pointers if p.km_from is None]
    if wide:
        parts.append(f'<text x="{spine_x}" y="{height - 8}" text-anchor="middle" font-size="6" fill="{MUTED}">'
                     f'{len(wide)} route-wide</text>')
    parts.append("</svg>")
    return Markup("".join(parts))


def _callout(p: SpinePointer, x: float, y: float, w: float, h: float, col: str) -> str:
    title_lines = _wrap(p.title, 58, 1)
    ctx_lines = _wrap(p.context, 92, 2 if h >= 50 else 1)
    ctl_lines = _wrap("Control: " + p.control, 92, 1)
    out = [f'<g><rect x="{x}" y="{y:.1f}" width="{w}" height="{h:.1f}" rx="4" fill="#fff" stroke="#C7D4E7" '
           f'stroke-width=".9"/><rect x="{x}" y="{y:.1f}" width="4" height="{h:.1f}" rx="2" fill="{col}"/>',
           f'<circle cx="{x + 16}" cy="{y + 13:.1f}" r="8" fill="{col}"/><text x="{x + 16}" y="{y + 16:.1f}" '
           f'text-anchor="middle" font-size="8" font-weight="700" fill="#fff">{p.number}</text>',
           f'<text x="{x + 30}" y="{y + 12:.1f}" font-size="6.4" font-weight="700" fill="{col}">{p.band}'
           f'<tspan fill="{MUTED}" font-weight="400"> · {escape(p.evidence.title())}</tspan></text>',
           f'<text x="{x + 30}" y="{y + 21:.1f}" font-size="8" font-weight="700" fill="{NAVY_DEEP}">'
           f'{escape(title_lines[0] if title_lines else "")}</text>']
    ty = y + 31
    for line in ctx_lines:
        if ty > y + h - 12:
            break
        out.append(f'<text x="{x + 12}" y="{ty:.1f}" font-size="6.6" fill="#1E2836">{escape(line)}</text>')
        ty += 8.4
    for line in ctl_lines:
        if ty > y + h - 3:
            break
        out.append(f'<text x="{x + 12}" y="{ty:.1f}" font-size="6.4" font-style="italic" fill="{NAVY}">'
                   f'{escape(line)}</text>')
    out.append("</g>")
    return "".join(out)
