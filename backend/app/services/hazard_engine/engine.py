"""HazardEngine: runs the 25 detectors, attaches library values verbatim, ranks deterministically, and
assigns segment risk. The LLM never selects hazards — it receives the ranked result."""

from __future__ import annotations

from app import rules_config
from app.domain.facts import HazardMatch, RouteFacts, VerificationItem
from app.domain.route import ElevationProfile, FeatureSet, RouteResult
from app.services.hazard_engine.detectors import REGISTRY, DetectionContext
from app.services.hazard_library import HazardLibrary, control_items, short_control


def display_band_for(severity_band: str, matrix_zone: str) -> str:
    cfg = rules_config.risk_matrix()
    if cfg.get("display_band_method") == "matrix_zone":
        return cfg["zone_labels"][matrix_zone]
    return severity_band


def _overlaps(a0: float, a1: float, b0: float, b1: float) -> bool:
    if a0 == a1:  # point location: inclusive of the start, exclusive of the end (boundary belongs to next)
        return b0 <= a0 < b1 or (a0 == b1 and b1 == b0)
    return a0 < b1 and a1 > b0


def rank_key(h: HazardMatch) -> tuple:
    r = rules_config.hazard_rules()["ranking"]
    return (
        r["band_order"].index(h.display_band),
        r["zone_order"].index(h.matrix_zone),
        r["probability_order"].index(h.probability),
        -h.severity,
        r["evidence_order"].index(h.evidence) if h.evidence in r["evidence_order"] else 9,
        -(h.extent_km + h.occurrences * 0.5),
        int(h.code.split("-")[1]),
    )


def run_hazard_engine(
    library: HazardLibrary,
    facts: RouteFacts,
    route: RouteResult,
    features: FeatureSet | None,
    elevation: ElevationProfile | None,
) -> tuple[list[HazardMatch], list[str], list[VerificationItem]]:
    rules = rules_config.hazard_rules()
    ctx = DetectionContext(facts=facts, route=route, features=features, elevation=elevation, rules=rules)
    vehicle = facts.travel.vehicle_type
    matches: list[HazardMatch] = []
    not_applicable: list[str] = []
    verification: list[VerificationItem] = []

    for hz in library.hazards:
        profile = rules["detectors"].get(hz.code)
        if not profile:
            not_applicable.append(hz.code)
            continue
        det = REGISTRY[profile["kind"]](ctx, profile)
        if det.verify_item:
            verification.append(VerificationItem(item=det.verify_item, reason=det.verify_reason or "", source="rule"))
        if not det.applicable:
            not_applicable.append(hz.code)
            continue
        m = HazardMatch(
            code=hz.code, name=hz.name, severity=hz.severity, probability=hz.probability, rpn_code=hz.rpn_code,
            severity_band=hz.severity_band, matrix_zone=hz.matrix_zone,
            display_band=display_band_for(hz.severity_band, hz.matrix_zone),
            evidence=det.evidence, applicable=True, basis=det.basis, locations=det.locations,  # type: ignore[arg-type]
            route_wide=det.route_wide or not det.locations, extent_km=det.extent_km, occurrences=det.occurrences,
            analog_label=det.analog, short_control=short_control(hz, vehicle),
            control_items=control_items(hz, vehicle),
        )
        m.segment_ids = [s.id for s in facts.segments if any(_overlaps(loc.km_from, loc.km_to, s.km_from, s.km_to)
                                                             for loc in m.locations)]
        matches.append(m)

    matches.sort(key=rank_key)
    for i, m in enumerate(matches, start=1):
        m.rank = i

    # segment risk: highest display band among hazards located in the segment (route-wide ones excluded)
    order = rules["ranking"]["band_order"]
    for s in facts.segments:
        located = [m for m in matches if s.id in m.segment_ids and not m.route_wide]
        s.hazard_codes = [m.code for m in located]
        s.risk = min((m.display_band for m in located), key=order.index, default="LOW")

    # data-quality verification items
    for w in facts.waypoints:
        if w.geocode_confidence < 0.8:
            verification.append(VerificationItem(item=f"Exact location of '{w.input_text}'",
                                                 reason="Geocoding confidence below 0.8", source="rule"))
    if facts.alternatives:
        verification.append(VerificationItem(item="Alternative routes (Page 4) against live navigation on the day",
                                             reason="Alternatives are contingencies only", source="rule"))
    if not facts.travel.vehicle_type_specified:
        verification.append(VerificationItem(item="Vehicle type (controls assume 4-Wheeler)",
                                             reason="Vehicle type not specified in the journey brief", source="rule"))
    if facts.is_demo_data:
        verification.insert(0, VerificationItem(item="Entire route analysis — generated from DEMO provider data",
                                                reason="Mock providers were used; not for operational use",
                                                source="rule"))
    cap = rules["report_caps"]["verification_items_max"]
    return matches, not_applicable, verification[:cap]
