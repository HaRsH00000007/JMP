"""Deterministic journey facts produced by the route/hazard/scoring engines (stages 2–8).

These models are the single source of every number, band, hazard and decision printed in the report.
Claude never sets any of these fields; it only receives a compact projection of them (llm/facts.py).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Classification = Literal["VERIFIED", "PROVIDED", "ASSESSMENT", "REQUIRES_VERIFICATION"]


class Waypoint(BaseModel):
    seq: int
    kind: Literal["start", "stop", "end"]
    input_text: str
    name: str
    short_name: str
    lat: float
    lng: float
    state: str | None = None
    district: str | None = None
    locality: str | None = None
    place_types: list[str] = Field(default_factory=list)
    km_from_start: float = 0.0
    is_institutional: bool = False
    geocode_confidence: float = 1.0
    geocode_provider: str = ""


class LegFact(BaseModel):
    from_seq: int
    to_seq: int
    distance_km: float
    duration_min: float
    km_from: float
    km_to: float
    dominant_category: str


class RoadTypeShare(BaseModel):
    category: str
    label: str
    km: float
    pct: int
    refs: list[str] = Field(default_factory=list)


class Segment(BaseModel):
    id: str
    from_seq: int
    to_seq: int
    label: str
    road_character: str
    dominant_category: str
    km_from: float
    km_to: float
    distance_km: float
    duration_min: float
    risk: str = "LOW"
    hazard_codes: list[str] = Field(default_factory=list)


class LocatedFeature(BaseModel):
    kind: str
    km: float
    lat: float
    lng: float
    name: str | None = None
    length_m: float | None = None
    tags: dict[str, str] = Field(default_factory=dict)


class Exposures(BaseModel):
    built_up_share_pct: int
    highway_share_pct: int
    pedestrian_level: str
    hcv_level: str
    traffic_level: str
    busy_junctions: int
    level_crossings: int
    level_crossing_places: list[str] = Field(default_factory=list)
    settlements: int
    settlement_names: list[str] = Field(default_factory=list)
    industrial_areas: int = 0
    seasonal_flags: list[str] = Field(default_factory=list)


class Alternative(BaseModel):
    id: str  # "A", "B"
    from_seq: int
    to_seq: int
    leg_label: str
    distance_km: float
    delta_km: float
    delta_min: float
    via: list[str] = Field(default_factory=list)
    geometry: list[tuple[float, float]] = Field(default_factory=list)  # simplified alternative
    primary_geometry: list[tuple[float, float]] = Field(default_factory=list)  # simplified primary leg


class TravelContext(BaseModel):
    vehicle_type: Literal["2W", "4W"]
    vehicle_type_specified: bool
    travel_date: str | None = None
    travel_month: int | None = None
    travel_month_name: str | None = None
    depart_time: str | None = None
    night_overlap: bool | None = None  # None = unknown (no date/time supplied)
    sunset_local: str | None = None


class RouteFacts(BaseModel):
    waypoints: list[Waypoint]
    legs: list[LegFact]
    geometry: list[tuple[float, float]]  # simplified route polyline for maps (real provider geometry)
    distance_km: float
    duration_min: float
    distance_range_km: tuple[int, int]
    duration_range_min: tuple[int, int]
    waypoint_count: int
    intermediate_stops: int
    is_round_trip: bool
    road_types: list[RoadTypeShare]
    road_type_count: int
    segments: list[Segment]
    exposures: Exposures
    complexity: str
    complexity_basis: str
    alternatives: list[Alternative]
    features: list[LocatedFeature]
    elevation: dict[str, float] = Field(default_factory=dict)
    states: list[str]
    region_label: str
    route_name: str
    journey_type: str
    travel: TravelContext
    hilly_region: bool
    providers: dict[str, str]
    is_demo_data: bool
    provider_warnings: list[str] = Field(default_factory=list)


class HazardLocation(BaseModel):
    km_from: float
    km_to: float
    lat: float
    lng: float
    label: str | None = None


class HazardMatch(BaseModel):
    code: str
    name: str
    severity: int
    probability: str
    rpn_code: str
    severity_band: str
    matrix_zone: str
    display_band: str
    evidence: Literal["DETECTED", "INFERRED", "VERIFY"]
    applicable: bool
    basis: str  # deterministic explanation of why it applies
    locations: list[HazardLocation] = Field(default_factory=list)
    route_wide: bool = False
    extent_km: float = 0.0
    occurrences: int = 0
    rank: int | None = None
    analog_label: str | None = None
    short_control: str = ""
    control_items: list[str] = Field(default_factory=list)
    segment_ids: list[str] = Field(default_factory=list)


class VerificationItem(BaseModel):
    item: str
    reason: str
    source: Literal["rule", "llm"] = "rule"


class DimensionScore(BaseModel):
    id: str
    label: str
    weight: float
    score: int
    contribution: float
    inputs: dict[str, float] = Field(default_factory=dict)


class FatigueStint(BaseModel):
    id: str
    label: str
    from_seq: int
    to_seq: int
    hours_low: float
    hours_high: float
    level: str
    night: bool = False


class Scores(BaseModel):
    dimensions: list[DimensionScore]
    total_exact: float
    total: int
    risk_level: str
    decision: str
    high_hazards: int
    fatigue_level: str
    fatigue_trend: str
    fatigue_stints: list[FatigueStint]
    rest_minutes: str


class DirectoryRow(BaseModel):
    type: str
    name: str
    location: str
    status: Literal["VERIFIED", "PROVIDED", "REQUIRES_VERIFICATION"]


class EmergencyInfo(BaseModel):
    directory: list[DirectoryRow]
    protocols: list[dict[str, object]]
    note: str
    hospital_network_url: str | None = None


class JourneyFacts(BaseModel):
    """Everything computed before the narrative stage — persisted on generation_jobs.report_facts."""

    route: RouteFacts
    hazards: list[HazardMatch]  # applicable hazards, ranked
    not_applicable: list[str]
    verification: list[VerificationItem]
    scores: Scores
    emergency: EmergencyInfo
    hazard_library_version: str
