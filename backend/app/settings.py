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
    geocoder: Literal["mock", "nominatim", "nominatim_photon", "photon", "google", "mapbox",
                      "osm_google"] = "mock"
    route_provider: Literal["mock", "osrm", "google", "mapbox"] = "mock"
    feature_provider: Literal["mock", "overpass", "none"] = "mock"
    elevation_provider: Literal["mock", "open_meteo", "opentopodata", "none"] = "mock"
    places_provider: Literal["mock", "overpass", "none"] = "mock"
    route_provider_api_key: SecretStr | None = None  # Google / Mapbox key
    osrm_base_url: str = "https://router.project-osrm.org"
    nominatim_base_url: str = "https://nominatim.openstreetmap.org"
    overpass_url: str = "https://overpass-api.de/api/interpreter"
    # Mirrors tried in order when the primary is down or shedding load. Same API, same data.
    overpass_fallback_urls_csv: str = Field(
        default="https://overpass.kumi.systems/api/interpreter,https://overpass.private.coffee/api/interpreter",
        validation_alias="OVERPASS_FALLBACK_URLS")
    open_meteo_url: str = "https://api.open-meteo.com/v1/elevation"
    opentopodata_url: str = "https://api.opentopodata.org/v1/srtm90m"
    photon_url: str = "https://photon.komoot.io/api/"
    provider_user_agent: str = "JMP-Generator/1.0 (EHS journey planning)"
    provider_timeout_s: float = 30.0
    # Overpass needs its own, longer timeout: these queries legitimately run for a minute or more, so the
    # 30 s that suits a geocoder would cut off healthy responses and retry them forever.
    overpass_timeout_s: float = 90.0
    geocode_region_hint: str = "in"
    # osm_google only: a match at or above this confidence is accepted from OpenStreetMap; below it,
    # Google is asked as well. Not-found and ambiguous always fall through regardless. The floor is the
    # point of the fallback — OSM's costly failure is a confident wrong answer, not a missing one.
    geocode_fallback_min_confidence: float = 0.8
    # A located stop further than this from every other stop on the same journey is treated as not
    # located. Short stop names ("Railway station", "Town market") match real places countrywide, at high
    # confidence, and one accepted match turns a local itinerary into a cross-country route. 0 disables it,
    # which is what genuinely long-haul planning needs.
    geocode_max_stop_separation_km: float = 300.0
    # Elevation providers charge per coordinate, so this — not the route length — is what a long route costs
    # against an hourly quota. Above it the sampling step widens; 0 disables the cap.
    elevation_max_samples: int = 200
    # Keep a journey when some stops cannot be geocoded: route through the ones that resolved, print the
    # rest verbatim as stops requiring verification. Never invents a coordinate, and the start and end must
    # still resolve. Off by default — with it on, a plan's measurements cover only part of the itinerary.
    allow_unverified_stops: bool = False
    # With ALLOW_UNVERIFIED_STOPS on, a journey where fewer than two stops can be located has no route to
    # measure. Instead of failing the row, issue a text-only plan: every stop printed verbatim, no distance,
    # score, segment or hazard position, and the whole hazard library as a checklist to confirm. Off by default.
    text_only_fallback: bool = False
    # With ALLOW_UNVERIFIED_STOPS on, a stop that cannot be found as written is placed at an area named in its
    # own text, else at the row's city — area results only, never a facility — and marked approximate. This
    # keeps rows on the full plan layout; distances to such stops are area-level. Off by default.
    area_level_fallback: bool = False
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
    # Cost-optimized narrative mode (LLM_COST_OPTIMIZED=true): routine narratives use LLM_ECONOMY_MODEL at
    # LLM_ECONOMY_EFFORT (the task is bounded prose over computed facts, checked by a strict validator); only the
    # final retry of a narrative that keeps failing validation escalates to ANTHROPIC_MODEL / ANTHROPIC_EFFORT.
    # Off = every call uses ANTHROPIC_MODEL at ANTHROPIC_EFFORT, as before.
    llm_cost_optimized: bool = False
    llm_economy_model: str = "claude-sonnet-5"
    llm_economy_effort: Literal["low", "medium", "high", "xhigh", "max"] = "low"
    llm_escalate_final_attempt: bool = True
    # Render the document once with a template narrative before paying for the real one, so a journey whose
    # layout cannot fit (long stop lists, many verification rows) fails before any Claude spend.
    llm_preflight_render: bool = True
    llm_max_attempts: int = 3
    # Ceiling on what ONE document may spend across all its attempts. A row that keeps failing
    # validation pays for the whole answer again each retry, so a few bad rows can outspend the batch
    # they sit in. At the cap the document fails instead of retrying. 0 disables the ceiling.
    llm_max_cost_per_document_usd: float = 0.60
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
    # Pins the output subfolder (e.g. "2026/23_9_26") instead of deriving it from today's date, so the rest
    # of a batch started on an earlier day is delivered alongside the documents already produced. Empty =
    # group by the run date, which is what you want normally.
    output_folder: str = ""

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_origins_csv.split(",") if o.strip()]

    @property
    def overpass_fallback_urls(self) -> list[str]:
        return [u.strip() for u in self.overpass_fallback_urls_csv.split(",") if u.strip()]

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
