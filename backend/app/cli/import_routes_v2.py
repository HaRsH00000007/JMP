"""Convert the "JMP - Routes Data" workbook (V2 layout) into the bulk-upload CSV, one sheet at a time.

    python -m app.cli.import_routes_v2 "<workbook>.xlsx" --sheet Sheet1 --out routes.csv
                                       [--resolve] [--keep-unresolved] [--limit N] [--report review.csv]

Unlike the Microsoft Forms export (see import_forms.py), this layout is one journey per row, with the
locations in fixed columns and a free-text hazard declaration between them:

    Employee · Mobile · Email · Manager · Start · Stop 1 · Stop 2 · [hazards] · Stop 3 · [hazards] …

Sheet1 ends the chain with "End Location" and Sheet2 with "Stop 6", so both are read as the final stop.

The addresses here carry far less context than the Forms export did — "bus stand", "Railway station",
"120Ft road" — and there is no City column to lean on. The start location is used as that context instead,
but only where it demonstrably helps: with --resolve each stop is tried bare and again with the start
location appended, and whichever actually resolves is kept. Nothing is ever guessed or substituted.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import openpyxl

import csv as _csv

from app.cli.import_forms import Journey, _clean, _is_placeholder, _with_city, write_report

# Location columns, 1-based, in the order they form the journey. Identical in both sheets; the last is
# "End Location" in Sheet1 and "Stop 6" in Sheet2, which is the same thing for our purposes.
LOCATION_COLUMNS = (5, 6, 7, 9, 11, 13, 15)
NAME_COL, EMAIL_COL, MANAGER_COL = 1, 3, 4


def extract(path: Path, sheet: str, limit: int | None = None) -> list[Journey]:
    wb = openpyxl.load_workbook(path, data_only=True)
    if sheet not in wb.sheetnames:
        raise SystemExit(f"No sheet {sheet!r}; the workbook has {wb.sheetnames}")
    ws = wb[sheet]
    digits = "".join(ch for ch in sheet if ch.isdigit())
    prefix = f"S{digits}" if digits else "".join(ch for ch in sheet if ch.isalnum())[:3].upper()

    out: list[Journey] = []
    row = 2
    seq = 0
    while row <= ws.max_row:
        cells = [_clean(ws.cell(row, c).value) for c in LOCATION_COLUMNS]
        if not any(cells) and not _clean(ws.cell(row, NAME_COL).value):
            row += 1
            continue
        seq += 1
        if limit is not None and seq > limit:
            break
        j = Journey(route_id=f"{prefix}R{seq:03d}", source_row=row, block=1,
                    employee=_clean(ws.cell(row, NAME_COL).value),
                    email=_clean(ws.cell(row, EMAIL_COL).value),
                    city=cells[0],  # the start location doubles as the context for the other stops
                    criteria=_hazards(ws, row))
        # V2 names the manager explicitly; the report prints that, not the driver.
        j.manager = _clean(ws.cell(row, MANAGER_COL).value)

        chain: list[str] = []
        for pos, raw in enumerate(cells):
            if not raw:
                continue
            if _is_placeholder(raw):
                j.notes.append(f"dropped non-location in column {LOCATION_COLUMNS[pos]}: {raw!r}")
                continue
            chain.append(raw)
        collapsed: list[str] = []
        for loc in chain:
            if collapsed and collapsed[-1].lower() == loc.lower():
                j.notes.append(f"collapsed repeated stop: {loc!r}")
                continue
            collapsed.append(loc)
        j.locations = collapsed
        if len(collapsed) < 2:
            j.skipped = f"needs at least 2 distinct locations, got {len(collapsed)}"
        out.append(j)
        row += 1
    return out


def resolve_v2(journeys: list[Journey], min_confidence: float, region_hint: str = "in") -> None:
    """Locate every stop, treating a weak match as no match at all.

    The shared Forms resolver keeps a low-confidence hit and merely notes it. That is too generous for this
    data: the stops are short and context-free ("Railway station", "bus stand", "120Ft road"), and a bare
    landmark matches something almost anywhere in India. In testing, "Railway station" on a Jalandhar
    journey resolved to Chennai — 2,000 km away, at 0.60 confidence, which the pipeline would have
    accepted and routed through.

    A stop below the floor is therefore recorded UNRESOLVED, so it is printed verbatim and excluded from
    the measured route rather than silently relocating the journey. With a commercial geocoder configured
    these become fallback lookups instead (GEOCODER=osm_google); without one, refusing is the only safe
    option.
    """
    from app.errors import JmpError
    from app.providers.registry import get_providers

    geocoder = get_providers().geocoder
    cache: dict[str, tuple[str, float, str] | None] = {}

    def try_one(text: str) -> tuple[str, float, str] | None:
        if text not in cache:
            try:
                g = geocoder.geocode(text, region_hint)
                cache[text] = (text, g.confidence, f"{g.locality or g.name}, {g.state or ''}".strip(", "))
            except JmpError:
                cache[text] = None
        return cache[text]

    for j in journeys:
        if not j.ok:
            continue
        kept: list[str] = []
        for loc in j.locations:
            withctx = _with_city(loc, j.city)
            options = [c for c in (try_one(loc), try_one(withctx) if withctx != loc else None) if c]
            best = max(options, key=lambda c: c[1], default=None)
            if best is None:
                j.notes.append(f"UNRESOLVED: {loc!r}")
                kept.append(loc)
                continue
            if best[1] < min_confidence:
                j.notes.append(f"UNRESOLVED (weak {best[1]:.2f} -> {best[2]}): {loc!r}")
                kept.append(loc)
                continue
            if best[0] != loc:
                j.notes.append(f"context added to resolve: {loc!r} -> {best[0]!r}")
            kept.append(best[0])
        dedup: list[str] = []
        for loc in kept:
            if dedup and dedup[-1].lower() == loc.lower():
                j.notes.append(f"collapsed repeated stop after resolution: {loc!r}")
                continue
            dedup.append(loc)
        j.locations = dedup
        unresolved = sum(1 for n in j.notes if n.startswith("UNRESOLVED"))
        if len(dedup) - unresolved < 2:
            j.skipped = (f"only {len(dedup) - unresolved} of {len(dedup)} stops could be located with "
                         f"confidence >= {min_confidence:.2f} — not enough to measure a route")


def write_csv_v2(journeys: list[Journey], out: Path, vehicle: str, travel_date: str, depart_time: str) -> int:
    """Same columns as the Forms importer, except manager_name carries the manager rather than the driver."""
    max_stops = max((len(j.locations) - 2 for j in journeys if j.ok), default=0)
    cols = ["route_id", "start_location", *[f"stop_{i}" for i in range(1, max_stops + 1)], "end_location",
            "vehicle_type", "travel_date", "depart_time", "manager_name"]
    n = 0
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = _csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for j in journeys:
            if not j.ok:
                continue
            row = {"route_id": j.route_id, "start_location": j.locations[0], "end_location": j.locations[-1],
                   "vehicle_type": vehicle, "travel_date": travel_date, "depart_time": depart_time,
                   "manager_name": getattr(j, "manager", "") or j.employee}
            for i, stop in enumerate(j.locations[1:-1], start=1):
                row[f"stop_{i}"] = stop
            w.writerow(row)
            n += 1
    return n


def _hazards(ws, row: int) -> str:
    """The driver's own free-text hazard declarations, kept for the review report only.

    They are never sent to the generator: hazards come from Danone's 25-hazard library, applied to the
    measured route. A driver's expectation is useful context for a reviewer, not an input to the
    assessment.
    """
    seen: list[str] = []
    for col in (8, 10, 12, 14):
        v = _clean(ws.cell(row, col).value)
        if v and v not in seen:
            seen.append(v)
    return " | ".join(seen)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("xlsx", type=Path)
    ap.add_argument("--sheet", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--report", type=Path)
    ap.add_argument("--limit", type=int, help="only the first N journeys")
    ap.add_argument("--vehicle-type", default="4W", choices=["2W", "4W"])
    ap.add_argument("--travel-date", default="")
    ap.add_argument("--depart-time", default="")
    ap.add_argument("--keep-unresolved", action="store_true",
                    help="write rows even when an address did not resolve (they fail inside the tool)")
    ap.add_argument("--min-confidence", type=float, default=0.8,
                    help="a match below this is treated as not found (default 0.8)")
    ap.add_argument("--resolve", action="store_true",
                    help="check every location against the configured geocoder before writing")
    a = ap.parse_args(argv)

    journeys = extract(a.xlsx, a.sheet, a.limit)
    if a.resolve:
        resolve_v2(journeys, a.min_confidence)
        if a.keep_unresolved:
            for j in journeys:
                if j.skipped.startswith("only "):
                    continue  # cannot be routed at all; leave it skipped
                j.skipped = ""
    written = write_csv_v2(journeys, a.out, a.vehicle_type, a.travel_date, a.depart_time)
    if a.report:
        write_report(journeys, a.report)

    unresolved = [j for j in journeys if any(n.startswith("UNRESOLVED") for n in j.notes)]
    skipped = [j for j in journeys if not j.ok]
    print(f"sheet                : {a.sheet}")
    print(f"journeys found       : {len(journeys)}")
    print(f"written to CSV       : {written} -> {a.out}")
    print(f"has unverified addr  : {len(unresolved)}")
    print(f"skipped              : {len(skipped)}")
    for j in skipped:
        print(f"  ! {j.route_id} (row {j.source_row}): {j.skipped}")
    if a.report:
        print(f"review report        : {a.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
