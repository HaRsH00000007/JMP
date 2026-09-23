"""Test fixtures: isolated SQLite DB migrated with Alembic, synchronous job execution, mock providers,
mock narrative by default, temp storage. No network, no API keys."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

BACKEND = Path(__file__).resolve().parent.parent
REPO = BACKEND.parent
FIXTURES = Path(__file__).parent / "fixtures"
_TMP = Path(tempfile.mkdtemp(prefix="jmp-tests-"))

os.environ.update({
    "APP_ENV": "test",
    "DATABASE_URL": f"sqlite:///{(_TMP / 'test.db').as_posix()}",
    "JOB_EXECUTION": "sync",
    "LLM_PROVIDER": "mock",
    "GEOCODER": "mock", "ROUTE_PROVIDER": "mock", "FEATURE_PROVIDER": "mock", "ELEVATION_PROVIDER": "mock",
    "PLACES_PROVIDER": "mock",
    "STORAGE_BACKEND": "local", "STORAGE_ROOT": str(_TMP / "storage"),
    "LOG_JSON": "false", "LOG_LEVEL": "WARNING",
    "STAGE_RETRY_BASE_S": "0",
    # pinned so the suite does not depend on the developer's .env
    "REPORT_DEMO_WATERMARK": "true",
    "TEMPLATE_VERSION": "1.1",
    "ANTHROPIC_API_KEY": "",
    "API_AUTH_TOKEN": "",
})


@pytest.fixture(scope="session", autouse=True)
def _database() -> Any:
    from alembic import command
    from alembic.config import Config

    from app.db.session import reset_engine, session_scope
    from app.services import hazard_library
    from app.settings import get_settings

    get_settings.cache_clear()
    reset_engine()
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    command.upgrade(cfg, "head")
    with session_scope() as db:
        hazard_library.ingest(db, REPO / "source" / "JMP Template- 25 Hazards.xlsx", "1.0")
    yield
    from app.rendering.pdf_renderer import shutdown_renderer

    shutdown_renderer()
    reset_engine()
    shutil.rmtree(_TMP, ignore_errors=True)


@pytest.fixture()
def db() -> Any:
    from app.db.session import session_scope

    with session_scope() as s:
        yield s


@pytest.fixture()
def library(db: Any) -> Any:
    from app.services.hazard_library import load_active_library

    return load_active_library(db)


@pytest.fixture()
def providers() -> Any:
    from app.providers.registry import get_providers

    return get_providers()


ZIRAKPUR = ["Paras Downtown Zirakpur, Punjab", "Chandigarh City Center Zirakpur, Punjab",
            "SBP Housing Park Society Derabassi, Punjab", "Danone Nutricia India Plant, Lalru, Punjab"]
PYRAGANDA = ["Pyraganda", "Chakdah", "Madanpur", "Palpara", "Kalyani", "Naihati", "Jagaddal", "Kakinara",
             "Shyamnagar", "Kalyani Junction", "Kalyani North", "Manipal Hospital, Kalyani", "Pyraganda"]


@pytest.fixture()
def reference_facts(library: Any, providers: Any, db: Any) -> Any:
    """The master-PDF-shaped 13-point loop, from the demo gazetteer (test data only)."""
    from datetime import date

    from app.services.pipeline import JourneyOptions, compute_facts

    return compute_facts(PYRAGANDA, JourneyOptions(vehicle_type="4W", vehicle_type_specified=True,
                                                   travel_date=date(2026, 8, 8), depart_time="09:00"),
                         providers, library, db)


@pytest.fixture()
def zirakpur_facts(library: Any, providers: Any, db: Any) -> Any:
    from datetime import date

    from app.services.pipeline import JourneyOptions, compute_facts

    return compute_facts(ZIRAKPUR, JourneyOptions(vehicle_type="4W", vehicle_type_specified=True,
                                                  travel_date=date(2026, 8, 10), depart_time="09:30"),
                         providers, library, db)


# ------------------------------------------------------------------------------ fake Anthropic
class FakeMessages:
    """Queue of canned responses; records every request's kwargs."""

    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def fake_response(text: str, *, stop_reason: str = "end_turn", input_tokens: int = 2400,
                  cache_read: int = 0, cache_write: int = 0, output_tokens: int = 3100,
                  model: str = "claude-opus-5") -> Any:
    usage = SimpleNamespace(input_tokens=input_tokens, cache_read_input_tokens=cache_read,
                            cache_creation_input_tokens=cache_write, output_tokens=output_tokens,
                            cache_creation=SimpleNamespace(ephemeral_5m_input_tokens=cache_write,
                                                           ephemeral_1h_input_tokens=0))
    return SimpleNamespace(content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
                           usage=usage, stop_reason=stop_reason, model=model, id="msg_test", _request_id="req_test")


@pytest.fixture()
def fake_client_factory() -> Any:
    def make(responses: list[Any]) -> Any:
        return SimpleNamespace(messages=FakeMessages(responses))

    return make


def valid_narrative_json(facts: Any) -> str:
    """A narrative a well-behaved model would return for these facts (built by the mock writer)."""
    from app.llm.facts import build_facts_payload
    from app.llm.mock_provider import build_mock_narrative

    return json.dumps(build_mock_narrative(build_facts_payload(facts)))
