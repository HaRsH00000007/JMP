"""Journey request validation (api-design.md §1). Shared by the individual API and the CSV importer so a
row is valid in bulk exactly when the same journey is valid individually."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.errors import InputValidationError
from app.settings import settings

_WS = re.compile(r"\s+")
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _clean(s: str) -> str:
    return _WS.sub(" ", s or "").strip()


class JourneyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    start_location: str = Field(min_length=3, max_length=300)
    stops: list[str] = Field(default_factory=list)
    end_location: str = Field(min_length=3, max_length=300)
    vehicle_type: Literal["2W", "4W"] | None = None
    travel_date: date | None = None
    depart_time: str | None = None
    manager_name: str | None = Field(default=None, max_length=200)
    emergency_contact: str | None = Field(default=None, max_length=200)
    nearest_hospital: str | None = Field(default=None, max_length=300)
    nearest_police: str | None = Field(default=None, max_length=300)
    idempotency_key: str | None = Field(default=None, max_length=128)

    @field_validator("start_location", "end_location", mode="before")
    @classmethod
    def _norm_loc(cls, v: Any) -> Any:
        return _clean(v) if isinstance(v, str) else v

    @field_validator("stops", mode="before")
    @classmethod
    def _norm_stops(cls, v: Any) -> Any:
        if isinstance(v, list):
            return [_clean(x) if isinstance(x, str) else x for x in v]
        return v

    @field_validator("vehicle_type", mode="before")
    @classmethod
    def _norm_vehicle(cls, v: Any) -> Any:
        if isinstance(v, str):
            t = v.strip().upper().replace("-", "").replace(" ", "")
            if not t:
                return None
            return {"2W": "2W", "2WHEELER": "2W", "TWOWHEELER": "2W", "4W": "4W", "4WHEELER": "4W",
                    "FOURWHEELER": "4W", "CAR": "4W", "BIKE": "2W", "MOTORCYCLE": "2W", "SCOOTER": "2W"}.get(t, v)
        return v

    @field_validator("depart_time")
    @classmethod
    def _time(cls, v: str | None) -> str | None:
        if v in (None, ""):
            return None
        v = v.strip()
        if re.match(r"^\d:\d\d$", v):
            v = "0" + v
        if not _TIME.match(v):
            raise ValueError("must be HH:MM (24-hour)")
        return v

    @field_validator("manager_name", "emergency_contact", "nearest_hospital", "nearest_police", "idempotency_key",
                     mode="before")
    @classmethod
    def _opt(cls, v: Any) -> Any:
        return (_clean(v) or None) if isinstance(v, str) else v

    @model_validator(mode="after")
    def _checks(self) -> JourneyRequest:
        s = settings()
        if len(self.stops) > s.max_stops:
            raise ValueError(f"at most {s.max_stops} stops are allowed")
        for i, st in enumerate(self.stops):
            if not st:
                raise ValueError(f"stops[{i}] is empty")
            if len(st) < 3 or len(st) > 300:
                raise ValueError(f"stops[{i}] must be 3–300 characters")
        seq = [self.start_location, *self.stops, self.end_location]
        for i in range(len(seq) - 1):
            if seq[i].lower() == seq[i + 1].lower():
                raise ValueError(f"consecutive locations {i + 1} and {i + 2} are identical")
        return self

    @property
    def locations(self) -> list[str]:
        return [self.start_location, *self.stops, self.end_location]

    @property
    def is_round_trip_text(self) -> bool:
        return self.start_location.lower() == self.end_location.lower()

    def input_hash(self) -> str:
        body = self.model_dump(mode="json", exclude={"idempotency_key"})
        body["_locs"] = [x.lower() for x in self.locations]
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def normalize_request(body: dict[str, Any]) -> JourneyRequest:
    try:
        return JourneyRequest.model_validate(body)
    except ValidationError as exc:
        details = []
        for e in exc.errors():
            loc = ".".join(str(p) for p in e["loc"]) or "journey"
            msg = e["msg"].removeprefix("Value error, ")
            details.append({"field": loc, "issue": msg})
        raise InputValidationError("Journey input is invalid", details=details) from exc


class JobAccepted(BaseModel):
    job_id: str
    journey_id: str
    status: str


class JobView(BaseModel):
    job_id: str
    kind: str
    status: str
    stage: str
    attempt: int
    journey_id: str
    journey_code: str | None
    route: list[str]
    queued_at: str
    started_at: str | None
    finished_at: str | None
    document_id: str | None
    bulk_job_id: str | None = None
    error: dict[str, Any] | None = None
