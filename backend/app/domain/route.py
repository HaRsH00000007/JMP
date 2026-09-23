"""Provider-neutral route data model. Providers fill these; nothing downstream knows which provider ran."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

RoadCategory = Literal["expressway", "national_highway", "state_highway", "arterial", "local", "unpaved", "unknown"]


class GeocodeResult(BaseModel):
    query: str
    name: str
    lat: float
    lng: float
    state: str | None = None
    district: str | None = None
    locality: str | None = None
    place_types: list[str] = Field(default_factory=list)
    confidence: float = 1.0
    provider: str
    candidates: list[dict[str, Any]] = Field(default_factory=list)  # for GEOCODE_AMBIGUOUS responses


class RoadSpan(BaseModel):
    """A stretch of a leg on one road, in metres from the start of the leg."""

    start_m: float
    end_m: float
    category: RoadCategory = "unknown"
    name: str | None = None
    ref: str | None = None


class RouteLeg(BaseModel):
    from_seq: int
    to_seq: int
    distance_m: float
    duration_s: float
    geometry: list[tuple[float, float]]
    maneuvers: list[tuple[float, float]] = Field(default_factory=list)
    spans: list[RoadSpan] = Field(default_factory=list)


class AlternativeRoute(BaseModel):
    distance_m: float
    duration_s: float
    geometry: list[tuple[float, float]]
    via: list[str] = Field(default_factory=list)  # road names/refs that differ from the primary


class RouteResult(BaseModel):
    provider: str
    request_id: str | None = None
    legs: list[RouteLeg]
    alternatives: list[AlternativeRoute] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def distance_m(self) -> float:
        return sum(leg.distance_m for leg in self.legs)

    @property
    def duration_s(self) -> float:
        return sum(leg.duration_s for leg in self.legs)

    @property
    def geometry(self) -> list[tuple[float, float]]:
        out: list[tuple[float, float]] = []
        for leg in self.legs:
            pts = leg.geometry if not out else leg.geometry[1:]
            out.extend(tuple(p) for p in pts)  # type: ignore[misc]
        return out


FeatureKind = Literal[
    "level_crossing", "bridge", "busy_junction", "rural_junction", "overhead_restriction",
    "traffic_calming", "pedestrian_poi", "forest", "built_up", "industrial", "settlement",
]


class RoadFeature(BaseModel):
    kind: FeatureKind
    lat: float
    lng: float
    name: str | None = None
    tags: dict[str, str] = Field(default_factory=dict)
    length_m: float | None = None  # for area / linear features: approximate extent along the route
    source: str = "unknown"


class RoadSample(BaseModel):
    """Road attributes at a point on the route (from OSM way matching)."""

    lat: float
    lng: float
    highway: str | None = None
    ref: str | None = None
    name: str | None = None
    surface: str | None = None
    smoothness: str | None = None
    lanes: int | None = None
    maxspeed: str | None = None
    lit: str | None = None
    built_up: bool = False


class FeatureSet(BaseModel):
    provider: str
    features: list[RoadFeature] = Field(default_factory=list)
    road_samples: list[RoadSample] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ElevationProfile(BaseModel):
    provider: str
    samples: list[tuple[float, float]] = Field(default_factory=list)  # (distance_along_m, elevation_m)


PlaceKind = Literal["hospital", "police", "fuel"]


class Place(BaseModel):
    kind: PlaceKind
    name: str
    lat: float
    lng: float
    distance_m: float | None = None
    source: str
