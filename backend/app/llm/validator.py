"""Semantic validation of Claude's narrative BEFORE it is accepted (generation-flow.md §2.5).

Schema validity is not enough for a safety document. This checks:
  1. referential coverage — every supplied hazard/segment/alternative/dimension/stint covered exactly once,
     no unknown IDs;
  2. anti-fabrication — no phone numbers / emergency numbers / URLs / e-mails; every number traceable to
     the facts payload, the hazard library, or the standard guidance list; no non-candidate hazard names.
Returns a list of human-readable errors (empty = accepted). The errors are fed back to the model on retry.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.llm.schemas import NarrativeV1, prose_fields
from app.services.hazard_library import HazardLibrary

_NUM = re.compile(r"(?<![A-Za-z])\d+(?:[.,]\d+)?")
_PHONE = re.compile(r"(\+?\d[\d\s\-()]{6,}\d)")
_URL = re.compile(r"(https?://|www\.|\b[a-z0-9-]+\.(com|in|org|net|gov|co)\b)", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_EMERGENCY = re.compile(r"\b(100|101|102|108|112|1033|1073|1091|1098|1800)\b")
# a helpline-style number in a calling context is always rejected, even if the digits are a supplied fact
_CALL_CONTEXT = re.compile(r"\b(call|dial|phone|ring|helpline|hotline|contact|tel)\b\W+(\w+\W+){0,2}?\d{3,}", re.I)

# Distinctive phrases for each library hazard (used to catch non-candidate hazards in prose)
_HAZARD_PHRASES = {
    "HZ-01": ["hairpin", "sharp turn"], "HZ-02": ["steep uphill"], "HZ-03": ["downhill slope"],
    "HZ-04": ["narrow road"], "HZ-05": ["pothole"], "HZ-06": ["speed breaker"], "HZ-07": ["culvert"],
    "HZ-08": ["level crossing", "railway crossing"], "HZ-09": ["blind curve"], "HZ-10": ["road marking"],
    "HZ-11": ["roadside construction"], "HZ-12": ["waterlogging", "flooded patch"], "HZ-13": ["landslide", "rockfall"],
    "HZ-14": ["fog-prone", "fog prone"], "HZ-15": ["busy intersection"], "HZ-16": ["village road crossing"],
    "HZ-17": ["overhead obstacle", "low bridge"], "HZ-18": ["slippery", "muddy patch"],
    "HZ-19": ["animal crossing", "forest road"], "HZ-20": ["pedestrian-prone"], "HZ-21": ["ghat"],
    "HZ-22": ["dead zone", "no network"], "HZ-23": ["poorly lit"], "HZ-24": ["national highway"],
    "HZ-25": ["dangerous dip"],
}


def _norm_num(s: str) -> str:
    s = s.replace(",", "")
    try:
        f = float(s)
    except ValueError:
        return s
    return str(int(f)) if f == int(f) else f"{f:g}"


def allowed_numbers(payload: dict[str, Any], library_numbers: frozenset[str]) -> set[str]:
    text = json.dumps(payload, ensure_ascii=False)
    nums = {_norm_num(n) for n in _NUM.findall(text)}
    # accept rounded forms of supplied decimals (e.g. 3.75 h -> "3.8" or "4")
    for n in list(nums):
        try:
            f = float(n)
        except ValueError:
            continue
        nums.add(_norm_num(str(round(f))))
        nums.add(_norm_num(str(round(f, 1))))
    nums |= {_norm_num(n) for n in library_numbers}
    return nums


def validate_narrative(n: NarrativeV1, payload: dict[str, Any], library: HazardLibrary,
                       library_numbers: frozenset[str]) -> list[str]:
    errors: list[str] = []

    def coverage(name: str, got: list[str], expected: list[str]) -> None:
        unknown = sorted(set(got) - set(expected))
        missing = sorted(set(expected) - set(got))
        dupes = sorted({g for g in got if got.count(g) > 1})
        if unknown:
            errors.append(f"{name}: unknown id(s) {unknown} — use only the supplied ids {expected}")
        if missing:
            errors.append(f"{name}: missing entries for {missing}")
        if dupes:
            errors.append(f"{name}: duplicate entries for {dupes}")

    coverage("hazard_notes", [h.hazard_code for h in n.hazard_notes], [h["code"] for h in payload["candidate_hazards"]])
    coverage("segment_notes", [s.segment_id for s in n.segment_notes], [s["id"] for s in payload["segments"]])
    coverage("alternative_notes", [a.alt_id for a in n.alternative_notes], [a["id"] for a in payload["alternatives"]])
    coverage("dimension_remarks", [d.dimension for d in n.dimension_remarks],
             [d["id"] for d in payload["scores"]["dimensions"]])
    coverage("fatigue_notes", [f.stint_id for f in n.fatigue_notes], [f["id"] for f in payload["fatigue"]["stints"]])

    for phase in ("before_departure", "during_journey", "at_stops", "return_journey"):
        pr = getattr(n.priority_recommendations, phase)
        if len(pr) != 3:
            errors.append(f"priority_recommendations.{phase}: needs exactly 3 items (got {len(pr)})")
        dr = getattr(n.driver_readiness, phase)
        if not 1 <= len(dr) <= 3:
            errors.append(f"driver_readiness.{phase}: needs 1–3 items (got {len(dr)})")
    if len(n.additional_verification) > 6:
        errors.append(f"additional_verification: at most 6 items (got {len(n.additional_verification)})")

    allowed = allowed_numbers(payload, library_numbers)
    facts_text = json.dumps(payload, ensure_ascii=False).lower()
    candidates = {h["code"] for h in payload["candidate_hazards"]}
    for path, text in prose_fields(n):
        if _EMAIL.search(text):
            errors.append(f"{path}: contains an e-mail address — remove it")
        if _URL.search(text):
            errors.append(f"{path}: contains a URL/web address — remove it")
        if _PHONE.search(text):
            errors.append(f"{path}: contains a phone-like number — remove it")
        emergency = [m for m in _EMERGENCY.findall(text) if _norm_num(m) not in allowed]
        if emergency or _CALL_CONTEXT.search(text):
            errors.append(f"{path}: contains an emergency/helpline number — the template prints these; remove it")
        bad = sorted({m for m in (_norm_num(x) for x in _NUM.findall(text)) if m not in allowed})
        if bad:
            errors.append(f"{path}: number(s) {bad} are not in the supplied facts — remove or use supplied values")
        low = text.lower()
        for code, phrases in _HAZARD_PHRASES.items():
            if code in candidates:
                continue
            for ph in phrases:
                if ph in low and ph not in facts_text:
                    errors.append(f"{path}: mentions '{ph}' ({library.get(code).name}) which is not a candidate hazard")
    return errors
