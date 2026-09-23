"""Loads the versioned YAML rule files in backend/config (cached per process)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.settings import settings


@lru_cache(maxsize=32)
def _load(path: str, mtime: float) -> dict[str, Any]:  # mtime busts the cache when a file changes
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a mapping")
    return data


def load_config(name: str) -> dict[str, Any]:
    path = Path(settings().config_dir) / name
    return _load(str(path), path.stat().st_mtime)


def hazard_rules() -> dict[str, Any]:
    return load_config("hazard_rules.yaml")


def scoring_rules() -> dict[str, Any]:
    return load_config("scoring.yaml")


def seasons() -> dict[str, Any]:
    return load_config("seasons.yaml")


def risk_matrix() -> dict[str, Any]:
    return load_config("risk_matrix.yaml")


def emergency_static() -> dict[str, Any]:
    return load_config("emergency_static.yaml")


def hazard_display() -> dict[str, Any]:
    return load_config("hazard_display.yaml")


def state_info(state_name: str | None) -> dict[str, Any]:
    cfg = seasons()
    states: dict[str, Any] = cfg.get("states", {})
    if state_name:
        if state_name in states:
            return {"name": state_name, **states[state_name]}
        low = state_name.lower()
        for k, v in states.items():
            if k.lower() == low:
                return {"name": k, **v}
    return {"name": state_name or "India", **cfg["default"]}
