"""Bulk CSV parsing and validation (api-design.md §3). Pure functions — no I/O beyond the bytes given.

Contract: route_id,start_location,stop_1..stop_N,end_location[,vehicle_type,travel_date,depart_time,
manager_name,emergency_contact,nearest_hospital,nearest_police]. Header matching is case-insensitive with
aliases ("Starting Location", "Stop 1", "End Location"). A gap in stop columns is a row error.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from typing import Any

from app.errors import InputValidationError
from app.schemas.journeys import JourneyRequest, normalize_request
from app.settings import settings

_ALIASES = {
    "route_id": {"route_id", "routeid", "route id", "id", "journey id", "journey_id", "route ref", "route_ref"},
    "start_location": {"start_location", "start location", "starting location", "start", "origin", "from"},
    "end_location": {"end_location", "end location", "ending location", "end", "destination", "to"},
    "vehicle_type": {"vehicle_type", "vehicle type", "vehicle"},
    "travel_date": {"travel_date", "travel date", "tentative date of travel", "date"},
    "depart_time": {"depart_time", "departure time", "depart time", "start time"},
    "manager_name": {"manager_name", "manager", "name of the manager", "manager name"},
    "emergency_contact": {"emergency_contact", "emergency contact", "emergency contact details"},
    "nearest_hospital": {"nearest_hospital", "nearest hospital"},
    "nearest_police": {"nearest_police", "nearest police", "nearest police station"},
}
_STOP_RE = re.compile(r"^(?:stop|waypoint|via)[\s_#-]*(\d+)$")


def _norm_header(h: str) -> str:
    return " ".join(h.replace("﻿", "").strip().lower().replace("_", " ").split())


@dataclass
class ParsedRow:
    row_number: int  # 1-based data row (header excluded)
    route_ref: str
    raw: dict[str, str]
    request: JourneyRequest | None
    errors: list[dict[str, str]] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return self.request is not None and not self.errors


@dataclass
class ParsedCsv:
    columns: list[str]
    mapping: dict[str, str]
    stop_columns: list[str]
    rows: list[ParsedRow]

    @property
    def valid_rows(self) -> list[ParsedRow]:
        return [r for r in self.rows if r.valid]

    def summary(self, preview: int = 20) -> dict[str, Any]:
        return {
            "total_rows": len(self.rows),
            "valid_rows": len(self.valid_rows),
            "invalid_rows": len(self.rows) - len(self.valid_rows),
            "columns_detected": self.columns,
            "errors": [{"row": r.row_number, "route_id": r.route_ref, **e} for r in self.rows for e in r.errors][:500],
            "preview": [
                {"row": r.row_number, "route_id": r.route_ref,
                 "start": r.request.start_location if r.request else r.raw.get(self.mapping.get("start_location", ""), ""),
                 "stops": r.request.stops if r.request else [],
                 "end": r.request.end_location if r.request else r.raw.get(self.mapping.get("end_location", ""), ""),
                 "valid": r.valid}
                for r in self.rows[:preview]
            ],
        }


def parse_csv(data: bytes) -> ParsedCsv:
    s = settings()
    if len(data) > s.bulk_max_bytes:
        raise InputValidationError(f"CSV exceeds {s.bulk_max_bytes // (1024 * 1024)} MB limit")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InputValidationError("CSV must be UTF-8 encoded") from exc
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration as exc:
        raise InputValidationError("CSV is empty") from exc
    columns = [h.strip() for h in header]
    mapping: dict[str, str] = {}
    stops: list[tuple[int, str]] = []
    for col in columns:
        n = _norm_header(col)
        m = _STOP_RE.match(n)
        if m:
            stops.append((int(m.group(1)), col))
            continue
        for canon, aliases in _ALIASES.items():
            if n in aliases and canon not in mapping:
                mapping[canon] = col
    missing = [c for c in ("start_location", "end_location") if c not in mapping]
    if missing:
        raise InputValidationError("CSV header is missing required column(s)", details={"missing": missing,
                                                                                          "columns": columns})
    stops.sort()
    stop_cols = [c for _, c in stops]
    rows: list[ParsedRow] = []
    for i, values in enumerate(reader, start=1):
        if not any(v.strip() for v in values):
            continue  # blank line
        if len(rows) >= s.bulk_max_rows:
            raise InputValidationError(f"CSV exceeds {s.bulk_max_rows} data rows")
        raw = {col: (values[j].strip() if j < len(values) else "") for j, col in enumerate(columns)}
        ref = raw.get(mapping.get("route_id", ""), "") or f"ROW-{i:04d}"
        errors: list[dict[str, str]] = []
        stop_vals = [raw.get(c, "") for c in stop_cols]
        # gaps: a filled stop after an empty one
        seen_empty = None
        for c, v in zip(stop_cols, stop_vals, strict=True):
            if not v:
                seen_empty = seen_empty or c
            elif seen_empty:
                errors.append({"field": c, "issue": f"stop after empty column {seen_empty} (gap in stop sequence)"})
                break
        req = None
        if not errors:
            body = {
                "start_location": raw.get(mapping["start_location"], ""),
                "stops": [v for v in stop_vals if v],
                "end_location": raw.get(mapping["end_location"], ""),
            }
            for opt in ("vehicle_type", "travel_date", "depart_time", "manager_name", "emergency_contact",
                        "nearest_hospital", "nearest_police"):
                if opt in mapping and raw.get(mapping[opt]):
                    body[opt] = raw[mapping[opt]]
            try:
                req = normalize_request(body)
            except InputValidationError as exc:
                for d in exc.details or [{"field": "row", "issue": exc.message}]:
                    errors.append({"field": str(d.get("field", "row")), "issue": str(d.get("issue", exc.message))})
        rows.append(ParsedRow(row_number=i, route_ref=ref[:100], raw=raw, request=req if not errors else None,
                              errors=errors))
    if not rows:
        raise InputValidationError("CSV has a header but no data rows")
    refs = [r.route_ref for r in rows]
    for r in rows:
        if refs.count(r.route_ref) > 1 and "route_id" in mapping:
            r.errors.append({"field": mapping["route_id"], "issue": f"duplicate route_id {r.route_ref!r}"})
            r.request = None
    return ParsedCsv(columns=columns, mapping=mapping, stop_columns=stop_cols, rows=rows)


def sample_csv() -> str:
    return (
        "route_id,start_location,stop_1,stop_2,stop_3,end_location,vehicle_type,travel_date,depart_time,manager_name\n"
        "R-001,\"Paras Downtown Zirakpur, Punjab\",\"Chandigarh City Center Zirakpur, Punjab\","
        "\"SBP Housing Park Society Derabassi, Punjab\",,\"Danone Nutricia India Plant, Lalru, Punjab\",4W,,09:30,\n"
    )
