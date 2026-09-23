"""Emergency directory (stage 8). Sources, in order of trust:

1. VERIFIED  — pan-India numbers from config/emergency_static.yaml (112, 108)
2. PROVIDED  — values supplied with the journey (nearest hospital/police, emergency contact) and
               institutional waypoints the user chose to visit
3. REQUIRES_VERIFICATION — facilities found by the PlacesProvider (names only, never phone numbers)

The LLM is never involved.
"""

from __future__ import annotations

from app import rules_config
from app.domain.facts import DirectoryRow, EmergencyInfo, RouteFacts
from app.providers.registry import Providers


def build_emergency(facts: RouteFacts, providers: Providers, *, nearest_hospital: str | None,
                    nearest_police: str | None, emergency_contact: str | None, manager_name: str | None,
                    hospital_network_url: str | None) -> EmergencyInfo:
    cfg = rules_config.emergency_static()
    rows: list[DirectoryRow] = [DirectoryRow(**r) for r in cfg["verified_numbers"]]
    if emergency_contact:
        rows.append(DirectoryRow(type="Emergency contact", name=emergency_contact,
                                 location=f"Journey contact{f' (manager: {manager_name})' if manager_name else ''}",
                                 status="PROVIDED"))
    if nearest_hospital:
        rows.append(DirectoryRow(type="Hospital", name=nearest_hospital, location="Supplied with journey",
                                 status="PROVIDED"))
    if nearest_police:
        rows.append(DirectoryRow(type="Police", name=nearest_police, location="Supplied with journey",
                                 status="PROVIDED"))
    for w in facts.waypoints:
        if w.is_institutional and "hospital" in [t.lower() for t in w.place_types]:
            rows.append(DirectoryRow(type="Hospital", name=w.name, location=f"Route waypoint {w.seq}",
                                     status="PROVIDED"))

    if providers.places is not None:
        seen = {r.name for r in rows}
        mid = facts.geometry[len(facts.geometry) // 2]
        probes = [("hospital", (facts.waypoints[0].lat, facts.waypoints[0].lng), "near start"),
                  ("hospital", mid, "mid-route"),
                  ("police", (facts.waypoints[0].lat, facts.waypoints[0].lng), "near start"),
                  ("police", (facts.waypoints[-1].lat, facts.waypoints[-1].lng), "near destination"),
                  ("fuel", mid, "mid-route")]
        labels = {"hospital": "Hospital", "police": "Police", "fuel": "Fuel"}
        for kind, pt, where in probes:
            if (kind == "hospital" and nearest_hospital) or (kind == "police" and nearest_police):
                continue
            try:
                found = providers.places.nearby(pt, kind, 8000)  # type: ignore[arg-type]
            except Exception:  # directory enrichment is best-effort; never fail the journey
                found = []
            for p in found[:1]:
                if p.name in seen:
                    continue
                seen.add(p.name)
                km = f"{(p.distance_m or 0) / 1000:.1f} km {where}"
                rows.append(DirectoryRow(type=labels[kind], name=p.name, location=km,
                                         status="REQUIRES_VERIFICATION"))
    rows.extend(DirectoryRow(**r) for r in cfg.get("static_directory_rows", []))
    return EmergencyInfo(directory=rows[:10], protocols=cfg["protocols"], note=cfg["directory_note"].strip(),
                         hospital_network_url=hospital_network_url)
