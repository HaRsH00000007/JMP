"""Score arithmetic, rubric, risk level, decision and fatigue — all deterministic and config-driven."""

from __future__ import annotations

from decimal import Decimal

from app import rules_config
from app.services.scoring import _first_match, compute_scores, round_half_up, score_dimension


def test_reference_pdf_arithmetic():
    """Master PDF page 6: weights × scores = 67.25 → 67 (rounded half-up)."""
    cfg = rules_config.scoring_rules()["dimensions"]
    ref_scores = {"route_complexity": 60, "traffic_congestion": 65, "hcv_ped_rail_exposure": 55, "driver_fatigue": 70,
                  "environmental": 65, "preparedness": 95}
    total = sum(Decimal(str(cfg[k]["weight"])) * v for k, v in ref_scores.items())
    assert total == Decimal("67.25")
    assert round_half_up(float(total)) == 67
    assert round_half_up(66.5) == 67 and round_half_up(66.49) == 66


def test_weights_sum_to_one():
    cfg = rules_config.scoring_rules()["dimensions"]
    assert sum(Decimal(str(d["weight"])) for d in cfg.values()) == Decimal("1")


def test_dimension_formula_clamps_and_caps():
    dcfg = {"base": 100, "terms": [{"metric": "x", "coef": -10, "cap": 3}], "min": 20, "max": 100}
    assert score_dimension(dcfg, {"x": 1})[0] == 90
    assert score_dimension(dcfg, {"x": 50})[0] == 70  # capped at 3
    dcfg2 = {"base": 10, "terms": [], "min": 20, "max": 100}
    assert score_dimension(dcfg2, {})[0] == 20


def test_decision_rules_first_match():
    rules = rules_config.scoring_rules()["decision"]
    assert _first_match(rules, 45, 0, "HIGH", "decision") == "REQUIRES REVIEW"
    assert _first_match(rules, 80, 2, "MODERATE", "decision") == "APPROVED WITH CONTROLS"
    assert _first_match(rules, 90, 0, "LOW", "decision") == "APPROVED"
    risk = rules_config.scoring_rules()["risk_level"]
    assert _first_match(risk, 45, 0, None, "level") == "HIGH"
    assert _first_match(risk, 70, 0, None, "level") == "MODERATE"
    assert _first_match(risk, 90, 1, None, "level") == "MODERATE"
    assert _first_match(risk, 90, 0, None, "level") == "LOW"


def test_scores_for_reference_loop(reference_facts):
    s = reference_facts.scores
    assert len(s.dimensions) == 6
    assert abs(sum(d.contribution for d in s.dimensions) - s.total_exact) < 0.05
    assert s.total == round_half_up(s.total_exact)
    assert s.decision in ("APPROVED", "APPROVED WITH CONTROLS", "REQUIRES REVIEW")
    # round trip > 1.5 h driving: outbound + return stints, return bumped for accumulated workload
    labels = [f.label.split(" (")[0] for f in s.fatigue_stints]
    assert labels == ["Outbound", "Return"]


def test_scores_recomputable_from_facts(zirakpur_facts):
    again = compute_scores(zirakpur_facts.route, zirakpur_facts.hazards)
    assert again == zirakpur_facts.scores
