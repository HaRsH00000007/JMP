"""Convert a Microsoft Forms "High Risk Routes" export (.xlsx) into the bulk-upload CSV.

    python -m app.cli.import_forms "<export>.xlsx" --out routes.csv [--limit 10] [--report review.csv]

The export is not one journey per row: each submission repeats a block of route columns (starting point,
three stops, last stop, city, criteria) up to ten times, so one row can hold ten journeys. This expands
every block into its own CSV row.

What it normalises (all of it reported, never silent):
  * with --resolve, checks each location against the geocoder and keeps whichever of "<text>" or
    "<text>, <City>" actually resolves (never appends blindly: "Noida" is not in "Ghaziabad");
  * collapses consecutive identical stops (forms often require three stops, so people repeat one);
  * drops placeholder answers that are not places ("NA", "This is mandatory route to go through", …).

What it deliberately does NOT do: invent, guess or substitute a location. Rows that cannot form a valid
journey are written to the review report with a reason, and left out of the CSV.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import openpyxl

START_HEADER = "Route - Starting Point"
CITY_HEADER = "City Name"
NAME_HEADER = "Name of Nutrition Advisor"
CRITERIA_HEADER = "Select the applicable high risk criteria"
LAST_STOP_HEADER = "Last Stop - Destination"

# Answers that are not places. Matched case-insensitively against the whole cell.
PLACEHOLDERS = {
    "na", "n/a", "nil", "none", "no", "not applicable", "same", "same as above", "-", "--", ".",
    "this is mandatory route to go through", "mandatory route", "not required", "no stop", "nostop",
}
_MIN_LOCATION_LEN = 3


def _clean(v: Any) -> str:
    return " ".join(str(v).split()) if v not in (None, "") else ""


def _is_placeholder(text: str) -> bool:
    t = text.strip().lower().rstrip(".")
    return t in PLACEHOLDERS or len(t) < _MIN_LOCATION_LEN


def _with_city(location: str, city: str) -> str:
    """Append the city unless it is already mentioned (case-insensitive, word-ish match)."""
    if not city or not location:
        return location
    if re.search(rf"\b{re.escape(city.strip().lower())}\b", location.lower()):
        return location
    return f"{location}, {city.strip()}"


@dataclass
class Journey:
    route_id: str
    source_row: int
    block: int
    employee: str
    email: str
    city: str
    criteria: str
    locations: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    skipped: str = ""

    @property
    def ok(self) -> bool:
        return not self.skipped


def extract(path: Path, limit: int | None = None) -> list[Journey]:
    ws = openpyxl.load_workbook(path, data_only=True)[openpyxl.load_workbook(path).sheetnames[0]]
    hdr = [_clean(ws.cell(1, c).value) for c in range(1, ws.max_column + 1)]
    starts = [i for i, h in enumerate(hdr) if h.startswith(START_HEADER)]
    if not starts:
        raise SystemExit(f"No '{START_HEADER}…' column found — is this the right export?")
    email_col = next((i for i, h in enumerate(hdr) if h.lower() == "email"), None)

    out: list[Journey] = []
    last_row = ws.max_row if limit is None else min(ws.max_row, 1 + limit)
    for r in range(2, last_row + 1):
        email = _clean(ws.cell(r, email_col + 1).value) if email_col is not None else ""
        for bi, s in enumerate(starts, start=1):
            start_raw = _clean(ws.cell(r, s + 1).value)
            if not start_raw:
                continue
            # the block layout is: [name][city][start][stop1][stop2][stop3][last stop][criteria]
            name = _clean(ws.cell(r, s - 1).value)
            city = _clean(ws.cell(r, s).value)
            stops_raw = [_clean(ws.cell(r, s + 1 + k).value) for k in (1, 2, 3)]
            end_raw = _clean(ws.cell(r, s + 5).value)
            criteria = _clean(ws.cell(r, s + 6).value).strip(";").replace(";", "; ")
            j = Journey(route_id=f"R{r - 1:03d}" + ("" if bi == 1 else f"-{bi}"), source_row=r, block=bi,
                        employee=name, email=email, city=city, criteria=criteria)

            chain_raw = [start_raw, *stops_raw, end_raw]
            chain: list[str] = []
            for pos, raw in enumerate(chain_raw):
                if not raw:
                    continue
                if _is_placeholder(raw):
                    j.notes.append(f"dropped non-location {('start','stop 1','stop 2','stop 3','last stop')[pos]}: {raw!r}")
                    continue
                chain.append(raw)
            # collapse consecutive duplicates (the form pushes people to repeat a stop)
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
    return out


def resolve(journeys: list[Journey], region_hint: str = "in", keep_unresolved: bool = False) -> None:
    """Check every location against the geocoder, trying "<text>" then "<text>, <City>". Keeps the form that
    resolves (preferring the higher-confidence one) and flags any location that resolves to neither, so a
    journey is never sent to bulk generation with an address that is going to fail or land in another state."""
    from app.errors import JmpError
    from app.providers.registry import get_providers

    geocoder = get_providers().geocoder
    cache: dict[str, tuple[str, float, str] | None] = {}

    def try_one(text: str) -> tuple[str, float, str] | None:
        if text in cache:
            return cache[text]
        try:
            g = geocoder.geocode(text, region_hint)
            cache[text] = (text, g.confidence, f"{g.locality or g.name}, {g.state or ''}".strip(", "))
        except JmpError:
            cache[text] = None
        return cache[text]

    for j in journeys:
        if not j.ok:
            continue
        resolved: list[str] = []
        for loc in j.locations:
            plain = try_one(loc)
            withcity = try_one(_with_city(loc, j.city)) if _with_city(loc, j.city) != loc else None
            best = max([c for c in (plain, withcity) if c], key=lambda c: c[1], default=None)
            if best is None:
                j.notes.append(f"UNRESOLVED: {loc!r}")
                resolved.append(loc)
                continue
            if best[0] != loc:
                j.notes.append(f"city added to resolve: {loc!r} -> {best[0]!r}")
            if best[1] < 0.8:
                j.notes.append(f"low confidence ({best[1]:.2f}) {best[0]!r} -> {best[2]}")
            resolved.append(best[0])
        j.locations = resolved
        # collapsing may be needed again once two entries resolved to the same text
        dedup: list[str] = []
        for loc in resolved:
            if dedup and dedup[-1].lower() == loc.lower():
                j.notes.append(f"collapsed repeated stop after resolution: {loc!r}")
                continue
            dedup.append(loc)
        j.locations = dedup
        unresolved = sum(1 for n in j.notes if n.startswith("UNRESOLVED"))
        if unresolved and not keep_unresolved:
            j.skipped = f"{unresolved} location(s) could not be geocoded — fix the address before generating"
        elif unresolved:
            # kept on purpose: the row goes to bulk and fails there as GEOCODE_NOT_FOUND, which is the
            # behaviour worth testing (one bad row must not stop the batch)
            j.notes.append(f"KEPT with {unresolved} unverified location(s) — expect this row to fail in bulk")
        elif len(dedup) < 2:
            j.skipped = f"needs at least 2 distinct locations, got {len(dedup)}"


def write_csv(journeys: list[Journey], out: Path, vehicle: str, travel_date: str, depart_time: str) -> int:
    max_stops = max((len(j.locations) - 2 for j in journeys if j.ok), default=0)
    cols = ["route_id", "start_location", *[f"stop_{i}" for i in range(1, max_stops + 1)], "end_location",
            "vehicle_type", "travel_date", "depart_time", "manager_name"]
    n = 0
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for j in journeys:
            if not j.ok:
                continue
            row = {"route_id": j.route_id, "start_location": j.locations[0], "end_location": j.locations[-1],
                   "vehicle_type": vehicle, "travel_date": travel_date, "depart_time": depart_time,
                   "manager_name": j.employee}
            for i, stop in enumerate(j.locations[1:-1], start=1):
                row[f"stop_{i}"] = stop
            w.writerow(row)
            n += 1
    return n


def write_report(journeys: list[Journey], out: Path) -> None:
    with open(out, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["route_id", "source_row", "block", "employee", "email", "city", "included",
                    "reason_if_skipped", "locations", "high_risk_criteria_declared", "normalisation_notes"])
        for j in journeys:
            w.writerow([j.route_id, j.source_row, j.block, j.employee, j.email, j.city,
                        "yes" if j.ok else "NO", j.skipped, " → ".join(j.locations), j.criteria,
                        "; ".join(j.notes)])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("xlsx", type=Path)
    ap.add_argument("--out", type=Path, required=True, help="bulk-upload CSV to write")
    ap.add_argument("--report", type=Path, help="review CSV: every journey, what changed, what was skipped")
    ap.add_argument("--limit", type=int, help="only the first N submission rows")
    ap.add_argument("--vehicle-type", default="4W", choices=["2W", "4W"])
    ap.add_argument("--travel-date", default="", help="YYYY-MM-DD applied to every row (the export has none)")
    ap.add_argument("--depart-time", default="", help="HH:MM applied to every row")
    ap.add_argument("--keep-unresolved", action="store_true",
                    help="write rows even when an address did not resolve (they will fail inside the tool)")
    ap.add_argument("--resolve", action="store_true",
                    help="check every location against the configured geocoder before writing (recommended)")
    a = ap.parse_args(argv)

    journeys = extract(a.xlsx, a.limit)
    if a.resolve:
        resolve(journeys, keep_unresolved=a.keep_unresolved)
    written = write_csv(journeys, a.out, a.vehicle_type, a.travel_date, a.depart_time)
    if a.report:
        write_report(journeys, a.report)
    skipped = [j for j in journeys if not j.ok]
    changed = [j for j in journeys if j.ok and j.notes]
    print(f"submission rows read : {len({j.source_row for j in journeys})}")
    print(f"journeys found       : {len(journeys)}")
    print(f"written to CSV       : {written} -> {a.out}")
    print(f"needed normalisation : {len(changed)}")
    print(f"skipped              : {len(skipped)}")
    for j in skipped:
        print(f"  ! {j.route_id} (row {j.source_row}): {j.skipped}")
    if a.report:
        print(f"review report        : {a.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
