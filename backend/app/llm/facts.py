"""Builds the VARIABLE user message: a compact, deterministic projection of JourneyFacts.

Only what the prose needs is sent — no raw geometry, no provider payloads, no full library (the library
lives in the cached prefix; candidates are referenced by code).
"""

from __future__ import annotations

import json
from typing import Any

from app.domain.facts import JourneyFacts


def _hm(minutes: float) -> str:
    m = int(round(minutes))
    return f"{m // 60}h{m % 60:02d}m"


def build_facts_payload(jf: JourneyFacts) -> dict[str, Any]:
    r = jf.route
    s = jf.scores
    payload: dict[str, Any] = {
        "is_demo_data": r.is_demo_data,
        "route_name": r.route_name,
        "region": r.region_label,
        "journey_type": r.journey_type,
        "is_round_trip": r.is_round_trip,
        "waypoints": [
            {"seq": w.seq, "kind": w.kind, "name": w.short_name, "locality": w.locality, "km_from_start": w.km_from_start,
             "institutional": w.is_institutional}
            for w in r.waypoints
        ],
        "totals": {
            "distance_km_range": list(r.distance_range_km),
            "driving_time_range": [_hm(r.duration_range_min[0]), _hm(r.duration_range_min[1])],
            "driving_hours_range": [round(r.duration_range_min[0] / 60, 2), round(r.duration_range_min[1] / 60, 2)],
            "waypoints": r.waypoint_count,
            "intermediate_stops": r.intermediate_stops,
            "complexity": r.complexity,
            "complexity_basis": r.complexity_basis,
        },
        "road_types": [{"label": t.label, "pct": t.pct, "refs": t.refs} for t in r.road_types],
        "segments": [
            {"id": sg.id, "label": sg.label, "road_character": sg.road_character, "km": [sg.km_from, sg.km_to],
             "risk": sg.risk, "hazards": sg.hazard_codes}
            for sg in r.segments
        ],
        "exposures": {
            "built_up_share_pct": r.exposures.built_up_share_pct, "highway_share_pct": r.exposures.highway_share_pct,
            "pedestrian": r.exposures.pedestrian_level, "hcv": r.exposures.hcv_level,
            "traffic": r.exposures.traffic_level, "busy_junctions": r.exposures.busy_junctions,
            "level_crossings": r.exposures.level_crossings,
            "level_crossing_places": r.exposures.level_crossing_places,
            "settlements": r.exposures.settlements, "seasonal": r.exposures.seasonal_flags,
        } if r.features_available else {
            # no map-feature provider ran: these were never measured, so they are not sent as zeros
            "map_features": "NOT ASSESSED - no map-feature data; do not state or imply presence or absence of "
                            "level crossings, bridges, junctions, built-up areas, settlements, schools or markets",
            "built_up_share_pct": "not assessed", "highway_share_pct": r.exposures.highway_share_pct,
            "pedestrian": "not assessed", "hcv": r.exposures.hcv_level, "traffic": "not assessed",
            "busy_junctions": "not assessed", "level_crossings": "not assessed", "level_crossing_places": [],
            "settlements": "not assessed", "seasonal": r.exposures.seasonal_flags,
        },
        "travel": {
            "vehicle_type": r.travel.vehicle_type, "vehicle_type_specified": r.travel.vehicle_type_specified,
            "month": r.travel.travel_month_name, "depart_time": r.travel.depart_time,
            "night_overlap": r.travel.night_overlap, "sunset": r.travel.sunset_local,
            "hilly_region": r.hilly_region,
        },
        "candidate_hazards": [
            {"code": h.code, "rank": h.rank, "band": h.display_band, "evidence": h.evidence, "basis": h.basis,
             "analog_label": h.analog_label, "segments": h.segment_ids, "route_wide": h.route_wide,
             "locations": [loc.label for loc in h.locations[:4]]}
            for h in jf.hazards
        ],
        "alternatives": [
            {"id": a.id, "leg": a.leg_label, "delta_km": a.delta_km, "delta_min": a.delta_min, "via": a.via}
            for a in r.alternatives
        ],
        "scores": {
            "journey_score": s.total, "risk_level": s.risk_level, "decision": s.decision,
            "high_band_hazards": s.high_hazards,
            "dimensions": [{"id": d.id, "weight_pct": int(round(d.weight * 100)), "score": d.score, "inputs": d.inputs}
                           for d in s.dimensions],
        },
        "fatigue": {
            "overall": s.fatigue_level, "trend": s.fatigue_trend, "rest_minutes": s.rest_minutes,
            "stints": [{"id": f.id, "label": f.label, "hours": [f.hours_low, f.hours_high], "level": f.level,
                        "night": f.night} for f in s.fatigue_stints],
        },
        "verification_items": [v.item for v in jf.verification],
        "emergency_directory_types": sorted({d.type for d in jf.emergency.directory}),
    }
    payload = _compact(payload)
    # Tell the model up front which hazard phrases the validator will reject for this route, instead of
    # finding out on a paid retry (the most common retry cause after word limits).
    payload["excluded_hazard_terms"] = excluded_hazard_terms(payload)
    return payload


def _compact(obj: Any) -> Any:
    """Drop None values (and the keys holding them) — they carry no information for the narrative. Lists and
    empty lists are kept: the validator checks coverage against them."""
    if isinstance(obj, dict):
        return {k: _compact(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_compact(v) for v in obj]
    return obj


def excluded_hazard_terms(payload: dict[str, Any]) -> list[str]:
    """Phrases naming non-candidate library hazards that do not already appear in the facts (validator rule)."""
    from app.llm.validator import _HAZARD_PHRASES, facts_text_for_validation

    candidates = {h["code"] for h in payload["candidate_hazards"]}
    facts_text = facts_text_for_validation(payload)
    return sorted({ph for code, phrases in _HAZARD_PHRASES.items() if code not in candidates
                   for ph in phrases if ph not in facts_text})


def facts_message(payload: dict[str, Any]) -> str:
    return ("Journey facts (authoritative; write the narrative for these only):\n"
            + json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
