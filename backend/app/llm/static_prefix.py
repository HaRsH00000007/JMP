"""Builds the STATIC, cacheable system prefix (generation-flow.md §2.2).

The prefix must be byte-identical across calls for the prompt cache to hit, so everything here is
deterministic: files read verbatim, library serialized with sorted keys and fixed separators, no
timestamps or IDs. Its SHA-256 is logged with every call, and a test asserts it is stable and that
PROMPT_VERSION changes whenever it changes.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app import rules_config
from app.llm.schemas import api_json_schema
from app.services.hazard_library import HazardLibrary
from app.versions import PROMPT_VERSION

PROMPT_DIR = Path(__file__).parent / "prompts" / f"v{PROMPT_VERSION.split('.')[0]}"
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def _dump(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def library_payload(lib: HazardLibrary) -> dict[str, Any]:
    matrix = rules_config.risk_matrix()
    return {
        "library_version": lib.version,
        "hazards": [
            {"code": h.code, "name": h.name, "severity": h.severity, "probability": h.probability,
             "rpn": h.rpn_code, "severity_band": h.severity_band, "matrix_zone": h.matrix_zone,
             "controls_2w": list(h.control_2w_items), "controls_4w": list(h.control_4w_items)}
            for h in lib.hazards
        ],
        "risk_matrix": {
            "display_band_method": matrix["display_band_method"],
            "severity_band": {str(k): v for k, v in matrix["severity_band"].items()},
            "zone_labels": matrix["zone_labels"],
            "likelihood": matrix["likelihood"],
            "consequence": {str(k): v for k, v in matrix["consequence"].items()},
        },
    }


@dataclass(frozen=True)
class StaticPrefix:
    blocks: tuple[str, ...]
    sha256: str
    library_numbers: frozenset[str]

    def system_param(self, ttl: str) -> list[dict[str, Any]]:
        cache: dict[str, Any] = {"type": "ephemeral"}
        if ttl == "1h":
            cache["ttl"] = "1h"
        out: list[dict[str, Any]] = [{"type": "text", "text": b} for b in self.blocks]
        out[-1]["cache_control"] = cache  # single breakpoint after the last static block
        return out


@lru_cache(maxsize=8)
def _build(lib_key: str, lib_json: str) -> StaticPrefix:
    system = (PROMPT_DIR / "system.md").read_text(encoding="utf-8")
    rules = (PROMPT_DIR / "rules.md").read_text(encoding="utf-8")
    guide = (PROMPT_DIR / "output_guide.md").read_text(encoding="utf-8")
    schema = _dump(api_json_schema())
    blocks = (
        system,
        rules,
        "# Danone 25-Hazard Library and risk matrix (authoritative; values verbatim)\n\n" + lib_json,
        guide + "\n\n# Output JSON schema\n\n" + schema,
    )
    digest = hashlib.sha256("\n\x1e".join(blocks).encode("utf-8")).hexdigest()
    lib = json.loads(lib_json)
    nums: set[str] = set()
    for h in lib["hazards"]:
        for text in h["controls_2w"] + h["controls_4w"]:
            nums.update(_NUM_RE.findall(text))
    nums.update(_NUM_RE.findall(rules.split("# Standard guidance you may quote", 1)[-1].split("# Length", 1)[0]))
    return StaticPrefix(blocks=blocks, sha256=digest, library_numbers=frozenset(nums))


def build_static_prefix(lib: HazardLibrary) -> StaticPrefix:
    return _build(lib.version_id, _dump(library_payload(lib)))
