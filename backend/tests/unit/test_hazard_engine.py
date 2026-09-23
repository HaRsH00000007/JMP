"""Hazard matching, evidence levels, ranking, bands and verification items."""

from __future__ import annotations

from datetime import date

import pytest

from app import rules_config
from app.services.hazard_engine import rank_key, run_hazard_engine
from app.services.hazard_engine.detectors import REGISTRY
from app.services.pipeline import JourneyOptions, compute_facts
from tests.conftest import ZIRAKPUR


def test_every_library_hazard_has_a_detector():
    rules = rules_config.hazard_rules()
    for n in range(1, 26):
        prof = rules["detectors"][f"HZ-{n:02d}"]
        assert prof["kind"] in REGISTRY


def test_zirakpur_hazards(zirakpur_facts):
    codes = {h.code: h for h in zirakpur_facts.hazards}
    assert "HZ-24" in codes and codes["HZ-24"].evidence == "DETECTED"  # NH stretch
    assert "HZ-08" in codes and codes["HZ-08"].locations  # level crossings near Lalru
    assert "HZ-12" in codes and codes["HZ-12"].evidence == "INFERRED"  # August in Punjab monsoon window
    assert "HZ-12" in codes and codes["HZ-12"].route_wide
    assert "HZ-14" not in codes  # fog not in season in August
    assert "HZ-11" not in codes  # construction is verify-only
    assert any("construction" in v.item.lower() for v in zirakpur_facts.verification)
    for h in zirakpur_facts.hazards:  # library values attached verbatim
        assert h.rpn_code == f"{h.probability}{h.severity}"


def test_ranking_is_deterministic_and_band_ordered(zirakpur_facts):
    hz = zirakpur_facts.hazards
    assert [h.rank for h in hz] == list(range(1, len(hz) + 1))
    assert hz == sorted(hz, key=rank_key)
    order = ["HIGH", "MEDIUM", "LOW"]
    bands = [order.index(h.display_band) for h in hz]
    assert bands == sorted(bands)


def test_unknown_travel_date_moves_seasonal_hazards_to_verification(library, providers, db):
    jf = compute_facts(ZIRAKPUR, JourneyOptions(), providers, library, db)
    codes = {h.code for h in jf.hazards}
    assert "HZ-12" not in codes and "HZ-14" not in codes and "HZ-23" not in codes
    items = " ".join(v.item for v in jf.verification).lower()
    assert "monsoon" in items and "sunset" in items and "vehicle type" in items


def test_fog_season_and_night(library, providers, db):
    jf = compute_facts(ZIRAKPUR, JourneyOptions(vehicle_type="2W", vehicle_type_specified=True,
                                                travel_date=date(2026, 1, 10), depart_time="17:30"),
                       providers, library, db)
    codes = {h.code: h for h in jf.hazards}
    assert "HZ-14" in codes and "HZ-23" in codes
    assert jf.route.travel.night_overlap is True
    assert any("Safety Gear" in i for i in codes["HZ-24"].control_items)  # 2W control set used


def test_matrix_zone_display_method(monkeypatch, library, zirakpur_facts):
    orig = rules_config.risk_matrix()
    monkeypatch.setattr(rules_config, "risk_matrix", lambda: {**orig, "display_band_method": "matrix_zone"})
    from app.services.hazard_engine.engine import display_band_for

    assert display_band_for("MEDIUM", "RED") == "HIGH"
    assert display_band_for("LOW", "YELLOW") == "MEDIUM"


def test_segment_risk_uses_located_hazards_only(zirakpur_facts):
    order = ["HIGH", "MEDIUM", "LOW"]
    for s in zirakpur_facts.route.segments:
        located = [h for h in zirakpur_facts.hazards if s.id in h.segment_ids and not h.route_wide]
        expected = min((h.display_band for h in located), key=order.index, default="LOW")
        assert s.risk == expected


def test_demo_data_flag_adds_verification_item(zirakpur_facts):
    assert zirakpur_facts.verification[0].item.startswith("Entire route analysis")


@pytest.mark.parametrize("code", ["HZ-01", "HZ-02", "HZ-13", "HZ-21"])
def test_hilly_route_triggers_terrain_hazards(library, providers, db, code):
    jf = compute_facts(["Kalka", "Solan", "Shimla"], JourneyOptions(travel_date=date(2026, 5, 2), depart_time="08:00"),
                       providers, library, db)
    codes = {h.code for h in jf.hazards}
    assert jf.route.hilly_region
    if code in ("HZ-13",):
        assert code in codes
    else:
        assert code in codes or code in jf.not_applicable  # geometry-dependent; must be decided, never invented
