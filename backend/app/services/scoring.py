"""ScoringEngine — journey score, overall risk level, decision and fatigue, all from config/scoring.yaml.

Arithmetic is exact and reproducible: score = Σ weight × dimension score, rounded half-up
(the reference report's 67.25 → 67)."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app import rules_config
from app.domain import geo
from app.domain.facts import DimensionScore, FatigueStint, HazardMatch, RouteFacts, Scores

SEASONAL_CODES = {"HZ-12", "HZ-13", "HZ-14", "HZ-18"}


def round_half_up(x: float) -> int:
    return int(Decimal(str(x)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def metrics(facts: RouteFacts, hazards: list[HazardMatch]) -> dict[str, float]:
    codes = {h.code for h in hazards}
    return {
        "intermediate_stops": facts.intermediate_stops,
        "road_type_count_minus_one": max(0, facts.road_type_count - 1),
        "route_km_over_100": max(0.0, facts.distance_km - 100),
        "built_up_share_pct": facts.exposures.built_up_share_pct,
        "busy_junctions": facts.exposures.busy_junctions,
        "level_crossings": facts.exposures.level_crossings,
        "highway_share_pct": facts.exposures.highway_share_pct,
        "driving_hours_upper": facts.duration_range_min[1] / 60,
        "night_driving": 1.0 if facts.travel.night_overlap else 0.0,
        "seasonal_hazards": float(len(codes & SEASONAL_CODES)),
        "hilly_region": 1.0 if facts.hilly_region else 0.0,
        "travel_date_unknown": 1.0 if facts.travel.travel_month is None else 0.0,
        "remote_area": 1.0 if "HZ-22" in codes else 0.0,
    }


def score_dimension(cfg: dict[str, Any], m: dict[str, float]) -> tuple[int, dict[str, float]]:
    val = float(cfg["base"])
    used: dict[str, float] = {}
    for term in cfg.get("terms", []):
        x = min(float(m[term["metric"]]), float(term.get("cap", 1e12)))
        used[term["metric"]] = round(x, 2)
        val += float(term["coef"]) * x
    val = max(float(cfg["min"]), min(float(cfg["max"]), val))
    return round_half_up(val), used


def _first_match(rules: list[dict[str, Any]], score: float, high: int, risk: str | None, key: str) -> str:
    for r in rules:
        if "if_score_lt" in r and not score < r["if_score_lt"]:
            continue
        if "if_high_hazards_gte" in r and not high >= r["if_high_hazards_gte"]:
            continue
        if "if_risk_level_in" in r and risk not in r["if_risk_level_in"]:
            continue
        return str(r[key])
    raise ValueError(f"No {key} rule matched — the last rule in scoring.yaml must be unconditional")


def fatigue(facts: RouteFacts) -> tuple[str, str, list[FatigueStint]]:
    cfg = rules_config.scoring_rules()["fatigue"]
    levels = cfg["levels"]
    names = [lv["level"] for lv in levels]
    lo_f = facts.duration_range_min[0] / max(facts.duration_min, 1e-6)
    hi_f = facts.duration_range_min[1] / max(facts.duration_min, 1e-6)
    total_hi_h = facts.duration_range_min[1] / 60
    wps = facts.waypoints
    legs = facts.legs

    def level_for(hours: float) -> str:
        for lv in levels:
            if hours <= lv["max_hours"]:
                return lv["level"]
        return levels[-1]["level"]

    # split point
    split_idx: int | None = None  # leg index after which the second stint starts
    if total_hi_h > cfg["split_if_total_hours_gt"] and len(legs) > 1:
        if facts.is_round_trip:
            far = max(range(1, len(wps) - 1), key=lambda i: geo.haversine((wps[0].lat, wps[0].lng),
                                                                          (wps[i].lat, wps[i].lng)))
            split_idx = far  # legs[0..far-1] outbound
        else:
            half = facts.duration_min / 2
            acc, best, best_d = 0.0, 1, 1e9
            for i, leg in enumerate(legs[:-1], start=1):
                acc += leg.duration_min
                if abs(acc - half) < best_d:
                    best, best_d = i, abs(acc - half)
            split_idx = best
    groups = [legs] if split_idx is None else [legs[:split_idx], legs[split_idx:]]
    stints: list[FatigueStint] = []
    for gi, g in enumerate(groups):
        mins = sum(leg.duration_min for leg in g)
        a, b = g[0].from_seq, g[-1].to_seq
        if split_idx is None:
            label_prefix = "Full journey"
        elif facts.is_round_trip:
            label_prefix = "Outbound" if gi == 0 else "Return"
        else:
            label_prefix = "First stint" if gi == 0 else "Second stint"
        chain = " → ".join(w.short_name for w in wps[a:b + 1])
        if len(wps[a:b + 1]) > 4:
            chain = f"{wps[a].short_name} → {wps[b].short_name}"
        lvl = level_for(mins * hi_f / 60)
        if gi == len(groups) - 1 and gi > 0 and total_hi_h > cfg["later_stint_bump_if_total_hours_gt"]:
            lvl = names[min(names.index(lvl) + 1, len(names) - 1)]
        night = bool(facts.travel.night_overlap) and gi == len(groups) - 1
        if night:
            lvl = cfg["night_level"]
        stints.append(FatigueStint(id=f"F{gi + 1}", label=f"{label_prefix} ({chain})", from_seq=a + 1, to_seq=b + 1,
                                   hours_low=round(mins * lo_f / 60, 2), hours_high=round(mins * hi_f / 60, 2),
                                   level=lvl, night=night))
    overall = max((s.level for s in stints), key=names.index)
    overall_plain = "MODERATE" if overall == "LOW–MODERATE" else overall
    trend = stints[0].level if len(stints) == 1 or stints[0].level == stints[-1].level else \
        f"{stints[0].level} → {stints[-1].level}"
    return overall_plain, trend, stints


def compute_scores(facts: RouteFacts, hazards: list[HazardMatch]) -> Scores:
    cfg = rules_config.scoring_rules()
    m = metrics(facts, hazards)
    dims: list[DimensionScore] = []
    total = Decimal("0")
    for key, dcfg in cfg["dimensions"].items():
        sc, used = score_dimension(dcfg, m)
        contribution = Decimal(str(dcfg["weight"])) * Decimal(sc)
        total += contribution
        dims.append(DimensionScore(id=key, label=dcfg["label"], weight=float(dcfg["weight"]), score=sc,
                                   contribution=float(contribution.quantize(Decimal("0.01"))), inputs=used))
    weight_sum = sum(Decimal(str(d["weight"])) for d in cfg["dimensions"].values())
    if weight_sum != Decimal("1"):
        raise ValueError(f"scoring.yaml weights must sum to 1.0 (got {weight_sum})")
    total_f = float(total)
    total_i = round_half_up(total_f)
    high = sum(1 for h in hazards if h.display_band == "HIGH")
    risk = _first_match(cfg["risk_level"], total_i, high, None, "level")
    decision = _first_match(cfg["decision"], total_i, high, risk, "decision")
    f_level, f_trend, stints = fatigue(facts)
    return Scores(dimensions=dims, total_exact=round(total_f, 2), total=total_i, risk_level=risk, decision=decision,
                  high_hazards=high, fatigue_level=f_level, fatigue_trend=f_trend, fatigue_stints=stints,
                  rest_minutes=str(cfg["fatigue"]["rest_minutes"]))
