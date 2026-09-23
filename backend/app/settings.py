"""Application configuration. Every value comes from the environment (or .env); nothing secret is hard-coded."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(REPO_DIR / ".env", BACKEND_DIR / ".env"), extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    app_version: str = "1.0.0"
    log_level: str = "INFO"
    log_json: bool = True

    # --- persistence -------------------------------------------------------------------------
    database_url: str = f"sqlite:///{(BACKEND_DIR / 'var' / 'jmp_dev.db').as_posix()}"
    redis_url: str = "redis://localhost:6379/0"
    # How pipeline stages execute:
    #   celery — Redis broker + Celery workers (production, docker-compose)
    #   thread — in-process thread pool inside the API (single-process local dev without Redis)
    #   sync   — inline, blocking (tests)
    job_execution: Literal["celery", "thread", "sync"] = "celery"
    stage_max_retries: int = 3
    stage_retry_base_s: float = 5.0
    stuck_job_timeout_min: int = 30

    storage_backend: Literal["local", "s3"] = "local"
    storage_root: Path = BACKEND_DIR / "var" / "storage"
    s3_bucket: str | None = None
    s3_region: str | None = None
    s3_endpoint_url: str | None = None
    s3_access_key_id: SecretStr | None = None
    s3_secret_access_key: SecretStr | None = None

    # comma-separated list (kept as a string: pydantic-settings would otherwise expect JSON for lists)
    cors_origins_csv: str = Field(default="http://localhost:5173,http://127.0.0.1:5173,http://localhost:8080",
                                  validation_alias="CORS_ORIGINS")

    # --- auth (D-15: API-key auth in v1; SSO/OIDC is a deployment decision) --------------------
    api_auth_token: SecretStr | None = None  # when set, every /api/v1 call needs "Authorization: Bearer <token>"

    # --- providers (D-09) ----------------------------------------------------------------------
    geocoder: Literal["mock", "nominatim", "google", "mapbox"] = "mock"
    route_provider: Literal["mock", "osrm", "google", "mapbox"] = "mock"
    feature_provider: Literal["mock", "overpass", "none"] = "mock"
    elevation_provider: Literal["mock", "open_meteo", "none"] = "mock"
    places_provider: Literal["mock", "overpass", "none"] = "mock"
    route_provider_api_key: SecretStr | None = None  # Google / Mapbox key
    osrm_base_url: str = "https://router.project-osrm.org"
    nominatim_base_url: str = "https://nominatim.openstreetmap.org"
    overpass_url: str = "https://overpass-api.de/api/interpreter"
    open_meteo_url: str = "https://api.open-meteo.com/v1/elevation"
    provider_user_agent: str = "JMP-Generator/1.0 (EHS journey planning)"
    provider_timeout_s: float = 30.0
    geocode_region_hint: str = "in"
    route_cache_ttl_days: int = 30

    # --- Claude (D-13) --------------------------------------------------------------------------
    llm_provider: Literal["anthropic", "mock"] = "anthropic"
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-opus-5"
    anthropic_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    anthropic_cache_ttl: Literal["5m", "1h"] = "5m"
    anthropic_max_tokens: int = 16000
    anthropic_timeout_s: float = 180.0
    anthropic_sdk_max_retries: int = 2
    anthropic_fallbacks_enabled: bool = True
    llm_max_attempts: int = 3
    llm_concurrency: int = 4  # worker concurrency of the "llm" queue
    bulk_llm_mode: Literal["realtime", "batch"] = "realtime"
    batch_poll_interval_s: int = 60
    usd_to_inr: float = 88.0  # configurable FX rate for INR cost reporting

    # --- report / rules -------------------------------------------------------------------------
    hazard_pointer_count: int = 5
    max_stops: int = 13
    bulk_max_rows: int = 2000
    bulk_max_bytes: int = 5 * 1024 * 1024
    hazard_source_xlsx: Path = REPO_DIR / "source" / "JMP Template- 25 Hazards.xlsx"
    hazard_library_version: str = "1.0"
    hazard_display_text: Literal["verbatim", "approved_corrections"] = "verbatim"  # D-06
    config_dir: Path = BACKEND_DIR / "config"
    templates_dir: Path = BACKEND_DIR / "templates"
    template_version: str = "1.1"
    # Prints "DEMO — NOT FOR OPERATIONAL USE" on every page when the route data or the narrative came from
    # a mock provider. Turning this off does NOT make such a report real: the document still records
    # demo_data/demo_narrative in its stored JSON and in the Reports list. Keep it on unless you have a
    # deliberate reason (e.g. internal layout previews).
    report_demo_watermark: bool = True
    pdf_render_timeout_ms: int = 60000

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_origins_csv.split(",") if o.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()


# Explicit override hook (tests); production code always calls settings().
_OVERRIDE: Settings | None = None


def settings() -> Settings:
    return _OVERRIDE or get_settings()


def set_settings(s: Settings | None) -> None:
    global _OVERRIDE
    _OVERRIDE = s
