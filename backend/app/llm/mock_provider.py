"""Deterministic MOCK narrative provider (LLM_PROVIDER=mock).

For local development and demos without an Anthropic key. It writes plain template sentences from the
facts payload only (so it passes the same validator as Claude), reports zero tokens, and documents
generated with it are labelled narrative_source="mock" and watermarked in the PDF.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.llm.client import LlmCallResult
from app.llm.pricing import TokenUsage
from app.llm.static_prefix import StaticPrefix

_FACTS_RE = re.compile(r"\{.*\}", re.S)


def _lower_band(b: str) -> str:
    return b.lower()


def _clip(text: str, words: int) -> str:
    parts = text.split()
    return " ".join(parts[:words])


class MockNarrativeProvider:
    name = "mock"
    model = "mock"

    def call(self, prefix: StaticPrefix, messages: list[dict[str, Any]], *, max_tokens: int) -> LlmCallResult:
        content = messages[0]["content"]
        text = content if isinstance(content, str) else content[0]["text"]
        payload = json.loads(_FACTS_RE.search(text).group(0))  # type: ignore[union-attr]
        return LlmCallResult(text=json.dumps(build_mock_narrative(payload)), usage=TokenUsage(), stop_reason="end_turn",
                             request_id="mock", duration_ms=0, model="mock")


def build_mock_narrative(p: dict[str, Any]) -> dict[str, Any]:
    t = p["totals"]
    s = p["scores"]
    hz = p["candidate_hazards"]
    wps = p["waypoints"]
    start, end = wps[0]["name"], wps[-1]["name"]
    roads = ", ".join(r["label"].lower() for r in p["road_types"][:3]) or "mixed roads"
    top = [h for h in hz if h["band"] == "HIGH"][:3] or hz[:2]
    names = {h["code"]: h["basis"] for h in hz}
    top_txt = "; ".join(names[h["code"]] for h in top) if top else "no HIGH-band hazards"
    km = t["distance_km_range"]
    trip = "round trip" if p["is_round_trip"] else "one-way journey"
    stops = t["intermediate_stops"]
    fat = p["fatigue"]
    vehicle = p["travel"]["vehicle_type"]
    gear = ("Check helmet, gloves, protective jacket, knee guards and footwear" if vehicle == "2W"
            else "Confirm front and rear seat belts are worn")

    def seg_label(seg_id: str) -> str:
        for sg in p["segments"]:
            if sg["id"] == seg_id:
                return sg["label"]
        return "the route"

    hazard_notes = []
    for h in hz:
        where = h["locations"][0] if h["locations"] else ("route-wide" if h["route_wide"] else seg_label(
            h["segments"][0]) if h["segments"] else "route-wide")
        hazard_notes.append({
            "hazard_code": h["code"],
            "route_context": _clip(f"{h['basis']} ({where})", 15),
            "card_location": _clip(where, 6),
            "card_description": _clip(h["basis"] + ".", 18),
            "qualifier": _clip(f"– {where}", 8),
        })
    return {
        "schema_version": "1.0",
        "executive_summary": _clip(
            f"This plan assesses a {trip} from {start} to {end} of about {km[0]}–{km[1]} km with {stops} "
            f"intermediate stop{'s' if stops != 1 else ''}. The route uses {roads}. Key concerns: {top_txt}. "
            f"Overall risk is assessed as {s['risk_level'].lower()} and the journey decision is "
            f"{s['decision'].lower()}, subject to EHS validation.", 110),
        "journey_assessment": _clip(
            f"The risk profile is shaped by {s['high_band_hazards']} HIGH-band hazard(s): {top_txt}. These are "
            f"managed through the listed controls, speed discipline and pre-trip checks. The journey covers "
            f"{t['waypoints']} waypoints and {len(p['road_types'])} road type(s); fatigue is assessed as "
            f"{fat['overall'].lower()} ({fat['trend']}).", 130),
        "decision_rationale": _clip(
            f"The decision of {s['decision'].lower()} follows from a journey score of {s['journey_score']} and "
            f"{s['high_band_hazards']} HIGH-band hazard(s), each managed by the prescribed controls. Scoring and "
            f"open verification items remain subject to Danone EHS validation.", 70),
        "decision_note_short": _clip(
            f"Journey may proceed subject to the prescribed controls and pre-trip checks. Subject to Danone EHS "
            f"validation.", 45),
        "hazard_notes": hazard_notes,
        "segment_notes": [{"segment_id": sg["id"], "key_exposure": _clip(
            f"{sg['road_character']}; {sg['risk'].lower()} assessed risk", 10)} for sg in p["segments"]],
        "highlight_captions": {
            "traffic": _clip(f"{p['exposures']['traffic'].title()} traffic exposure", 5),
            "hcv": _clip(f"{p['exposures']['hcv'].title()} HCV exposure", 5),
            "railway": "Level crossings on route" if p["exposures"]["level_crossings"] else "No crossings detected",
            "pedestrian": _clip(f"{p['exposures']['pedestrian'].title()} pedestrian exposure", 5),
            "urban_density": "Settlements along route",
            "environmental": "Seasonal conditions apply" if p["exposures"]["seasonal"] else "No seasonal flags",
        },
        "alternative_notes": [{"alt_id": a["id"], "advantage": "Avoids the primary leg if it is obstructed",
                               "limitation": "Longer distance and unverified road condition",
                               "use_when": "Primary leg is blocked or heavily congested on the day"}
                              for a in p["alternatives"]],
        "route_recommendation": _clip(
            "Remain on the primary route by default. Hold any alternatives as day-of contingencies, used only "
            "when an obstruction on the primary route is confirmed through live navigation.", 60),
        "dimension_remarks": [{"dimension": d["id"], "remark": f"Scored {d['score']} from the supplied route inputs"}
                              for d in s["dimensions"]],
        "fatigue_notes": [{"stint_id": f["id"], "basis": _clip(
            f"About {f['hours'][1]} hours of driving; assessed {f['level'].lower()}", 14)} for f in fat["stints"]],
        "fatigue_management": _clip(
            f"Take a {fat['rest_minutes']} minute rest break at a planned stop before the later stint.", 40),
        "driver_readiness": {
            "before_departure": ["Complete the pre-trip vehicle check", "Share the itinerary with the line manager",
                                 gear],
            "during_journey": ["Maintain speed discipline and safe following distance"],
            "at_stops": ["Park only in designated areas", "Reassess alertness before moving on"],
            "return_journey": ["Recheck weather and road alerts", "Confirm safe arrival with the manager"],
        },
        "priority_recommendations": {
            "before_departure": ["Confirm travel date and share the itinerary with the manager",
                                 "Check the IMD weather advisory and local closure alerts",
                                 "Complete the pre-trip vehicle inspection and emergency kit check"],
            "during_journey": ["Keep a 3-second following distance from heavy vehicles",
                               "Hold 20–30 km/h through town centres and market areas",
                               ("Approach level crossings at 10–20 km/h and never beat a barrier"
                                if any(h["code"] == "HZ-08" for h in hz)
                                else "Give way to main-road traffic at every junction")],
            "at_stops": ["Take the planned rest break before the later stint",
                         "Use designated parking and avoid roadside stops",
                         "Check in with the line manager at planned stops"],
            "return_journey": ["Reassess alertness and hydration before the return drive",
                               "Confirm no new weather or closure alerts",
                               "Notify the line manager on final arrival"],
        },
        "additional_verification": [],
    }
