"""NarrativeV1 — the ONLY structure Claude produces. Prose fields keyed to application-supplied IDs.

Word/item limits are enforced client-side by these validators (the structured-output API strips
unsupported constraints such as maxLength), and again by llm/validator.py semantic checks.
"""

from __future__ import annotations

import copy
import re
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0"


def _words(limit: int):  # noqa: ANN202
    def check(v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("must not be empty")
        n = len(v.split())
        if n > limit:
            raise ValueError(f"has {n} words; maximum is {limit}")
        return v

    return AfterValidator(check)


def W(limit: int, desc: str) -> Any:  # noqa: N802
    return Annotated[str, _words(limit), Field(description=f"{desc} (max {limit} words)")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HazardNote(_Strict):
    hazard_code: str = Field(description="One of the supplied candidate hazard codes, e.g. HZ-08")
    route_context: W(15, "Where/why this hazard applies on THIS route, using supplied place names")  # type: ignore[valid-type]
    card_location: W(6, "Short location label for the page-1 exposure card")  # type: ignore[valid-type]
    card_description: W(18, "One-line description for the page-1 exposure card")  # type: ignore[valid-type]
    qualifier: W(8, "Short qualifier appended to the hazard name in ranked lists")  # type: ignore[valid-type]


class SegmentNote(_Strict):
    segment_id: str = Field(description="One of the supplied segment ids, e.g. S1")
    key_exposure: W(10, "Key exposure on this segment")  # type: ignore[valid-type]


class HighlightCaptions(_Strict):
    traffic: W(5, "Caption for the traffic tile")  # type: ignore[valid-type]
    hcv: W(5, "Caption for the HCV exposure tile")  # type: ignore[valid-type]
    railway: W(5, "Caption for the railway tile")  # type: ignore[valid-type]
    pedestrian: W(5, "Caption for the pedestrian tile")  # type: ignore[valid-type]
    urban_density: W(5, "Caption for the urban density tile")  # type: ignore[valid-type]
    environmental: W(5, "Caption for the environmental tile")  # type: ignore[valid-type]


class AlternativeNote(_Strict):
    alt_id: str = Field(description="One of the supplied alternative ids (A or B)")
    advantage: W(14, "Advantage vs the primary route")  # type: ignore[valid-type]
    limitation: W(14, "Limitation vs the primary route")  # type: ignore[valid-type]
    use_when: W(14, "Trigger condition for using this contingency")  # type: ignore[valid-type]


class DimensionRemark(_Strict):
    dimension: Literal["route_complexity", "traffic_congestion", "hcv_ped_rail_exposure", "driver_fatigue",
                       "environmental", "preparedness"]
    remark: W(14, "Remark explaining the supplied dimension score")  # type: ignore[valid-type]


class FatigueNote(_Strict):
    stint_id: str = Field(description="One of the supplied fatigue stint ids, e.g. F1")
    basis: W(14, "Basis for the supplied fatigue level")  # type: ignore[valid-type]


Bullet14 = Annotated[str, _words(14)]
Bullet16 = Annotated[str, _words(16)]


class DriverReadiness(_Strict):
    before_departure: list[Bullet14] = Field(description="1–3 items")
    during_journey: list[Bullet14] = Field(description="1–3 items")
    at_stops: list[Bullet14] = Field(description="1–3 items")
    return_journey: list[Bullet14] = Field(description="1–3 items")


class PriorityRecommendations(_Strict):
    before_departure: list[Bullet16] = Field(description="exactly 3 items")
    during_journey: list[Bullet16] = Field(description="exactly 3 items")
    at_stops: list[Bullet16] = Field(description="exactly 3 items")
    return_journey: list[Bullet16] = Field(description="exactly 3 items")


class VerificationSuggestion(_Strict):
    item: W(14, "What must be confirmed before travel")  # type: ignore[valid-type]
    reason: W(14, "Why it cannot be assumed")  # type: ignore[valid-type]


class NarrativeV1(_Strict):
    schema_version: Literal["1.0"]
    executive_summary: W(110, "Executive summary paragraph")  # type: ignore[valid-type]
    journey_assessment: W(130, "Journey assessment paragraph")  # type: ignore[valid-type]
    decision_rationale: W(70, "Justification for the supplied final decision")  # type: ignore[valid-type]
    decision_note_short: W(45, "Short decision note for page 1")  # type: ignore[valid-type]
    hazard_notes: list[HazardNote]
    segment_notes: list[SegmentNote]
    highlight_captions: HighlightCaptions
    alternative_notes: list[AlternativeNote]
    route_recommendation: W(60, "Route recommendation paragraph")  # type: ignore[valid-type]
    dimension_remarks: list[DimensionRemark]
    fatigue_notes: list[FatigueNote]
    fatigue_management: W(40, "Fatigue-management sentence")  # type: ignore[valid-type]
    driver_readiness: DriverReadiness
    priority_recommendations: PriorityRecommendations
    additional_verification: list[VerificationSuggestion] = Field(description="0–6 items")


_UNSUPPORTED = {"maxLength", "minLength", "maxItems", "minItems", "minimum", "maximum", "exclusiveMinimum",
                "exclusiveMaximum", "multipleOf", "pattern", "default", "title"}


def api_json_schema() -> dict[str, Any]:
    """JSON schema sent as output_config.format — deterministic, stripped of unsupported keywords."""

    def clean(node: Any) -> Any:
        if isinstance(node, dict):
            out = {k: clean(v) for k, v in node.items() if k not in _UNSUPPORTED}
            if out.get("type") == "object":
                out["additionalProperties"] = False
            return out
        if isinstance(node, list):
            return [clean(v) for v in node]
        return node

    return clean(copy.deepcopy(NarrativeV1.model_json_schema()))


def prose_fields(n: NarrativeV1) -> list[tuple[str, str]]:
    """Flatten every free-text value with its JSON path (used by the semantic validator)."""
    out: list[tuple[str, str]] = []

    def walk(prefix: str, value: Any) -> None:
        if isinstance(value, BaseModel):
            for k, v in value:
                walk(f"{prefix}.{k}" if prefix else k, v)
        elif isinstance(value, list):
            for i, v in enumerate(value):
                walk(f"{prefix}[{i}]", v)
        elif isinstance(value, str):
            key = prefix.rsplit(".", 1)[-1]
            if key not in {"schema_version", "hazard_code", "segment_id", "alt_id", "dimension", "stint_id"}:
                out.append((prefix, value))

    walk("", n)
    return out


_WS = re.compile(r"\s+")
