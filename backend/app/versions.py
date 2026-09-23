"""Independent version stamps recorded on every generated document (architecture.md §5)."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from app import rules_config
from app.settings import settings

PROMPT_VERSION = "1.0"   # bump whenever any static prompt block changes (enforced by a test)
SCHEMA_VERSION = "1.0"   # NarrativeV1


@dataclass(frozen=True)
class VersionStamp:
    app_version: str
    template_version: str
    hazard_library_version: str
    prompt_version: str
    schema_version: str
    scoring_version: str
    rules_version: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def current_versions(hazard_library_version: str) -> VersionStamp:
    s = settings()
    return VersionStamp(
        app_version=s.app_version,
        template_version=s.template_version,
        hazard_library_version=hazard_library_version,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
        scoring_version=str(rules_config.scoring_rules()["version"]),
        rules_version=str(rules_config.hazard_rules()["version"]),
    )
