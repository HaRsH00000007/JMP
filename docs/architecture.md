# JMP Generator — Architecture

Companion docs: `pdf-analysis.md` · `hazard-library.md` · `api-design.md` · `generation-flow.md` · `decisions.md`

## 1. Principles

1. **The LLM writes prose, never facts.** Numbers, hazards, bands, scores, decisions, contacts and page structure are all produced by application code. Claude returns a small, schema-validated *narrative* object. The final report object is **assembled by code**: deterministic data plus the validated narrative.
2. **Every stage is persisted and replayable.** Each stage's output (geocode, route, features, hazards, scores, narrative, HTML, PDF) is stored with version stamps. A report can be re-rendered without new API calls, and any number can be audited back to its input.
3. **Isolated layers.** Routing ↔ hazard engine ↔ LLM ↔ rendering communicate only through typed Pydantic models.
   - Presentation never computes risk.
   - The LLM layer never calls routing.
   - The PDF layer never calls the LLM.
4. **Async by default.** HTTP endpoints only validate input, persist it and enqueue work. All provider, LLM and rendering calls run in workers.

## 2. System overview

```text
┌──────────────┐    REST/JSON     ┌──────────────────────┐
│ React (Vite) │ ───────────────▶ │ FastAPI  (api)       │── Postgres (state, audit, library)
│  dashboard   │ ◀─── polling ─── │  validate · persist  │── Object storage (CSV, HTML, PDF, ZIP)
└──────────────┘                  │  enqueue             │
                                  └─────────┬────────────┘
                                            │ Redis (broker)
              ┌─────────────────────────────┼──────────────────────────────┐
              ▼                             ▼                              ▼
     queue: pipeline               queue: llm                     queue: render
  ┌─────────────────────┐   ┌──────────────────────────┐   ┌──────────────────────────┐
  │ RouteService        │   │ NarrativeService         │   │ TemplateRenderer (Jinja2)│
  │  ├ Geocoder         │   │  └ AnthropicClient       │   │ PdfRenderer (Playwright, │
  │  ├ RouteProvider    │   │    (cached static prefix,│   │   long-lived Chromium)   │
  │  ├ RoadFeatureProv. │   │     structured output,   │   └──────────────────────────┘
  │  ├ ElevationProv.   │   │     usage + cost log)    │
  │  └ PlacesProvider   │   └──────────────────────────┘        queue: bulk
  │ HazardEngine        │                                  ┌──────────────────────────┐
  │ ScoringEngine       │                                  │ fan-out · progress ·     │
  └─────────────────────┘                                  │ ZIP + manifest finalizer │
                                                           └──────────────────────────┘
```

### Components

| Component | Tech | Responsibility |
|---|---|---|
| `api` | FastAPI, Pydantic v2, SQLAlchemy 2 (async), Alembic | Validation, persistence, job creation, downloads, CSV pre-validation |
| `worker` | **Celery 5 + Redis** [ASSUMPTION — see below] | Pipeline stages, retries, rate limiting, bulk fan-out |
| `postgres` | PostgreSQL 16 | Source of truth: jobs, journeys, library, usage, audit |
| `storage` | `StorageBackend` interface: `LocalStorage` (dev) / `S3Storage` (prod) | CSV uploads, HTML snapshots, PDFs, ZIPs, manifests |
| `frontend` | React 18 + TypeScript + Vite, TanStack Query, React Router | Dashboard, forms, progress polling |

**Why Celery.** It provides:

- per-queue concurrency, so PDF rendering and the LLM scale independently
- task-level retries with backoff
- rate limits
- chords, for "all bulk items done → finalize"

The alternative is a Postgres-only queue (fewer moving parts, weaker tooling). Celery doesn't support Windows as a production worker host, so development and production both run in **Docker (Linux)**. Local dev uses `docker compose up`.

## 3. Provider abstraction

```python
class Geocoder(Protocol):
    async def geocode(self, query: str, region_hint: str | None) -> GeocodeResult: ...
    # GeocodeResult: lat, lng, formatted_address, admin_area (state/district), place_types, confidence

class RouteProvider(Protocol):
    async def route(self, waypoints: list[LatLng], *, depart_at: datetime | None,
                    alternatives: bool) -> RouteResult: ...
    # RouteResult: legs[] (distance_m, duration_s, polyline, steps[road_name, ref, maneuver]),
    #              total distance/duration, alternatives[], provider, provider_request_id

class RoadFeatureProvider(Protocol):          # OSM/Overpass: level crossings, bridges, road class, landuse, lit…
    async def features_along(self, polyline: LineString, buffer_m: int) -> list[RoadFeature]: ...

class ElevationProvider(Protocol):
    async def profile(self, polyline: LineString, sample_m: int) -> list[ElevationSample]: ...

class PlacesProvider(Protocol):               # nearest hospital / police / fuel → always REQUIRES VERIFICATION
    async def nearby(self, point: LatLng, kind: PlaceKind, radius_m: int) -> list[Place]: ...
```

**Implementations:**

- `GoogleMapsRouteProvider` (Routes API)
- `MapboxRouteProvider`
- `OverpassFeatureProvider`
- `GoogleElevationProvider` / `OpenElevationProvider`
- a `Mock*` for every provider, backed by fixtures (used in tests and when no keys are set)

Selection is by env var (`ROUTE_PROVIDER=google|mapbox|mock`). Every provider response is cached in `route_cache`, keyed by `sha256(provider + normalized input + depart-date bucket)`. Bulk CSVs with repeated routes don't pay twice.

**Licensing risk → [DECISION D-09].** Google Maps Platform terms restrict:

- storing or caching Google content beyond short windows
- displaying Google-derived geometry on non-Google maps

Our PDF draws its own SVG route diagram. With Google, the options are to confirm that is permitted, to use Google Static Maps imagery instead, or to use Mapbox/OSM-based routing, whose terms are friendlier to derived schematic output. Road features come from OSM in any case (ODbL — attribution line in the PDF).

## 4. Database schema (PostgreSQL)

UUID primary keys. `created_at`/`updated_at` on every table. JSONB for stage snapshots. Enum columns use Postgres enums.

```text
users                    id, email (unique), full_name, role (admin|preparer|reviewer|approver|viewer),
                         is_active, auth_subject (SSO) — D-15

hazard_library_versions  id, version, source_filename, source_sha256, is_active, ingested_at
hazards                  id, library_version_id→, code, sr_no, name, severity, probability, rpn_code,
                         severity_band, matrix_zone, control_2w_raw, control_4w_raw,
                         control_2w_items jsonb, control_4w_items jsonb, control_short_2w, control_short_4w,
                         detection_profile jsonb           UNIQUE(library_version_id, code)
risk_matrix_cells        library_version_id→, probability, severity, zone        PK(all three)

journeys                 id, journey_code ("DAN-JMP-WB-014", D-12), created_by→users,
                         source (individual|bulk), bulk_job_item_id→ nullable,
                         input jsonb (exact request), input_hash,
                         vehicle_type, travel_date, depart_time, manager_name, emergency_contact,  (D-08)
                         is_round_trip, status
journey_stops            id, journey_id→, seq (0 = start … n = end), kind (start|stop|end),
                         raw_text, geocoded_name, lat, lng, admin_area jsonb, place_types text[],
                         geocode_confidence, provider            UNIQUE(journey_id, seq)

route_cache              key (pk), provider, payload jsonb, created_at, expires_at
route_analysis           id, journey_id→ (1:1 current), provider, provider_request_id,
                         distance_m, duration_s, distance_range jsonb, duration_range jsonb,
                         geometry (encoded polyline), legs jsonb, segments jsonb,
                         road_type_split jsonb, exposures jsonb, alternatives jsonb,
                         features jsonb (located OSM features), elevation_summary jsonb,
                         rules_version, computed_at
journey_hazards          id, journey_id→, hazard_id→ (pins library version), applicable bool,
                         evidence (DETECTED|INFERRED|VERIFY), display_band, rank,
                         locations jsonb [{km_from, km_to, lat, lng, label}], evidence_detail jsonb,
                         narrative_context (from LLM, nullable)
journey_scores           journey_id→, dimension, weight, score, contribution, inputs jsonb,
                         scoring_version                   (D-03)

generation_jobs          id, kind (individual|bulk_item|rerender), journey_id→, status
                         (queued|processing|completed|failed|cancelled), stage (geocode|route|features|
                         hazards|scoring|narrative|render|pdf|done), attempt, error_code, error_message,
                         queued_at, started_at, finished_at, celery_task_id
generation_usage         id, generation_job_id→, journey_id→, provider ('anthropic'), model,
                         prompt_version, schema_version, request_id, attempt,
                         input_tokens, cache_creation_input_tokens, cache_read_input_tokens,
                         output_tokens, estimated_cost_usd numeric(12,6), duration_ms,
                         stop_reason, outcome (ok|invalid_json|schema_error|refusal|api_error)
jmp_documents            id, journey_id→, generation_job_id→, document_code, status,
                         report_json jsonb (the exact object rendered), narrative_json jsonb,
                         template_version, hazard_library_version, prompt_version, schema_version,
                         scoring_version, rules_version, app_version, model,
                         html_path, pdf_path, pdf_sha256, pdf_bytes, page_count,
                         render_ms, pdf_ms, created_at, deleted_at (soft delete)

bulk_jobs                id, created_by→, original_filename, csv_path, csv_sha256, status
                         (validating|queued|processing|finalizing|completed|completed_with_errors|failed|cancelled),
                         total_rows, valid_rows, invalid_rows, processed, succeeded, failed,
                         zip_path, manifest_csv_path, manifest_xlsx_path, llm_mode (realtime|batch),
                         started_at, finished_at
bulk_job_items           id, bulk_job_id→, row_number, route_ref (CSV route_id), raw_row jsonb,
                         validation_errors jsonb, status (invalid|queued|processing|succeeded|failed),
                         journey_id→ nullable, document_id→ nullable, error_code, error_message,
                         attempts, finished_at             UNIQUE(bulk_job_id, row_number)

audit_events             id, actor_id→, entity, entity_id, action, detail jsonb, at
```

Key indexes:

- `generation_jobs(status, queued_at)`
- `bulk_job_items(bulk_job_id, status)`
- `journeys(input_hash)`
- `jmp_documents(journey_id)`
- a trigram index on `journey_stops.raw_text` for report search

**Reproducibility.** `jmp_documents.report_json` together with the version columns is everything the renderer consumed. `route_analysis`, `journey_hazards` and `generation_usage` hold everything upstream of it.

## 5. Versioning

| Version | Where defined | Recorded on |
|---|---|---|
| `app_version` | package metadata | `jmp_documents` |
| `template_version` | `settings.template_version` (currently **1.1**) | `jmp_documents`, printed in PDF metadata + page-8 version line |
| `hazard_library_version` | `hazard_library_versions.version` | `jmp_documents`, `journey_hazards` via FK |
| `prompt_version` | `app/llm/prompts/<version>/` | `jmp_documents`, `generation_usage` |
| `schema_version` | `NarrativeV1` model constant | same as above |
| `rules_version` / `scoring_version` | `config/hazard_rules.yaml`, `config/scoring.yaml` | `route_analysis`, `journey_scores`, `jmp_documents` |

A static-prefix change (prompt, library or schema) **must** bump `prompt_version`. This is also what keeps cache behaviour predictable.

## 6. Observability & security

- **Logging:** structured JSON (`structlog`). Every log line carries `job_id`, `journey_id`, `bulk_job_id`, `stage`, `attempt`.
- **Anthropic usage:** logged per call. The token and cost fields go into `generation_usage`. See `generation-flow.md` §4.
- **Metrics:** OpenTelemetry traces across api → worker stages. Prometheus metrics: stage durations, queue depth, cache-hit ratio, cost per document, failure counts by `error_code`.
- **Redaction:** a log filter redacts `authorization`, `x-api-key` and anything matching key patterns. Provider clients never log request headers.
- **Secrets:** env vars only (`ANTHROPIC_API_KEY`, `DATABASE_URL`, `REDIS_URL`, `ROUTE_PROVIDER_API_KEY`, `STORAGE_*`). A `.env.example` is committed, `.env` is git-ignored, and Pydantic `Settings` fails fast on missing values.
- **Downloads:** served through the API with an authorization check (or short-lived signed URLs for S3). Storage paths are never exposed.
- **Uploads:** CSVs are size-limited (default 5 MB / 2,000 rows [ASSUMPTION]). Parsing uses the stdlib `csv` module with no evaluation. Cells beginning with `= + - @` are neutralised when written back into the XLSX manifest (formula injection).

## 7. Folder structure

```text
JMP/
├── docs/                         architecture, pdf-analysis, hazard-library, api-design, generation-flow, decisions
├── source/                       master PDF + hazard XLSX (moved here only after approval; read-only inputs)
├── docker-compose.yml            api, worker-pipeline, worker-llm, worker-render, postgres, redis, frontend
├── .env.example
├── backend/
│   ├── pyproject.toml
│   ├── alembic/
│   ├── config/                   hazard_rules.yaml, scoring.yaml, seasons.yaml, emergency_static.yaml
│   ├── app/
│   │   ├── main.py               FastAPI app factory
│   │   ├── settings.py           Pydantic Settings
│   │   ├── api/v1/               journeys.py, jobs.py, documents.py, bulk_jobs.py, hazards.py, usage.py
│   │   ├── schemas/              Pydantic API models (request/response)
│   │   ├── domain/               pure models: Journey, RouteAnalysis, HazardMatch, ReportModel
│   │   ├── db/                   models.py, session.py, repositories/
│   │   ├── providers/
│   │   │   ├── routing/          base.py, google.py, mapbox.py, mock.py
│   │   │   ├── features/         overpass.py, mock.py
│   │   │   ├── elevation/        …
│   │   │   └── places/           …
│   │   ├── services/
│   │   │   ├── route_service.py        geocode → route → features → segments/exposures
│   │   │   ├── hazard_engine/          detectors/*.py (one per hazard), ranking.py
│   │   │   ├── scoring.py              dimensions, weights, decision, fatigue
│   │   │   ├── report_assembler.py     deterministic data + narrative → ReportModel
│   │   │   └── bulk_service.py         CSV parse/validate, fan-out, finalize
│   │   ├── llm/
│   │   │   ├── client.py               AnthropicClient wrapper (retry, usage, cost)
│   │   │   ├── narrative_service.py
│   │   │   ├── schemas.py              NarrativeV1 (Pydantic)
│   │   │   ├── pricing.py
│   │   │   └── prompts/v1/             system.md, rules.md, terminology.md (static, cached)
│   │   ├── rendering/
│   │   │   ├── html_renderer.py        Jinja2
│   │   │   ├── pdf_renderer.py         Playwright
│   │   │   └── svg/                    route_map.py, hazard_pointer.py, alt_route.py, gauge.py
│   │   ├── storage/                    base.py, local.py, s3.py
│   │   ├── workers/                    celery_app.py, tasks_pipeline.py, tasks_llm.py, tasks_render.py, tasks_bulk.py
│   │   ├── cli/                        ingest_hazards.py, render_fixture.py
│   │   └── observability/              logging.py, redaction.py, metrics.py
│   ├── templates/jmp/v1/
│   │   ├── report.html.j2              one document, 8 <section class="page">
│   │   ├── partials/                   page01_snapshot … page08_emergency, components/
│   │   ├── styles.css                  tokens from pdf-analysis §3, @page A4
│   │   └── fonts/                      Carlito, Arimo (woff2)
│   └── tests/
│       ├── fixtures/                   pyraganda_reference.json, provider responses, sample CSVs
│       ├── unit/ · integration/ · render/
│       └── conftest.py
└── frontend/
    ├── package.json · vite.config.ts · tsconfig.json
    └── src/
        ├── api/                        typed client (generated from OpenAPI)
        ├── pages/                      Dashboard, IndividualJmp, BulkJmp, Jobs, Reports, Settings
        ├── components/                 StopListEditor, CsvPreviewTable, ProgressBar, StatusPill…
        └── hooks/                      useJobPolling…
```

## 8. Development plan

Each phase ends with a demo and a set of passing tests.

| Phase | Deliverable | Exit criteria |
|---|---|---|
| **1** | ✅ These docs | Architecture approved; decisions D-01…D-22 answered or deferred with defaults |
| **2** | Backend skeleton, DB models + Alembic, hazard ingestion CLI, `POST /journeys/generate` (persist + enqueue), jobs API, docker-compose | Ingest reproduces the 25 hazards exactly (test asserts every S/P/RPN); journey validation tests |
| **3** | Provider interfaces, Mock providers, Google or Mapbox + Overpass implementations, RouteService, HazardEngine, ScoringEngine | Pyraganda fixture yields a plausible hazard set (rail crossing, NH analog, pedestrian…); ranking and score tests |
| **4** | Anthropic layer: static prefix, `NarrativeV1`, structured output, validation + retry, usage/cost logging | Recorded-response tests. Live smoke test shows `cache_read_input_tokens > 0` from the 2nd call onward |
| **5** | HTML/CSS template (8 pages incl. hazard pointer), SVG generators | Side-by-side visual review against the master PDF; overflow test on 2-stop and 15-stop fixtures |
| **6** | Playwright PDF renderer, storage, download endpoint | A4, correct page count, fonts embedded, deterministic output for the same input |
| **7** | React frontend: Individual JMP, Jobs, Reports, Settings (read-only config view) | End-to-end individual generation from the UI |
| **8** | Bulk CSV: validate/preview, fan-out, progress, ZIP + manifest, optional Batch API mode | 250-row synthetic CSV with seeded failures; failed rows isolated; ZIP + manifest correct |
| **9** | Hardening: test gaps, load test, auth, retention job, runbook | Coverage targets; bulk throughput and cost per JMP measured and documented |

---

## 9. As-built notes (implementation complete)

All nine phases are implemented. Where the build differs from the plan above, this is what changed and why.

| Planned | Built | Why |
|---|---|---|
| Async SQLAlchemy | **Sync** SQLAlchemy 2.0 | Celery workers are synchronous, and the sync API path runs in FastAPI's threadpool. One session style, no colour mismatch; Playwright is sync too |
| 12 separately queued stages | **3 persisted stages** — `analyse` (2–8), `narrate` (9), `render` (10–12) | Each stage is a single unit of paid work with its own queue and retry. Finer progress is still recorded in `generation_jobs.stage` |
| Celery only | `JOB_EXECUTION=celery \| thread \| sync` | `thread` gives a Redis-free single-process dev mode; `sync` makes the test suite deterministic. Production stays `celery` |
| Mock + Google/Mapbox providers | Added a **free OSM profile** (Nominatim, OSRM, Overpass, Open-Meteo) | Gives a working non-mock installation with no API keys, and made it possible to verify the provider layer against real data |
| Chromium per worker thread | **One renderer thread per process** owning one browser | Playwright's sync API cannot run on a thread with an asyncio loop, and one browser per request thread is wasteful. Scaling is by process |
| `route_name`, `journey_type` from Claude | Computed deterministically | Less LLM surface to validate; both are derivable from the geocoded stops |
| `users` table drives auth | Table exists; auth is a **static bearer token** (`API_AUTH_TOKEN`) | D-15 (SSO, roles, digital sign-off) is still an open decision; the token is the minimum safe default |
| — | Added `uploaded_csvs`, `journey_code_sequences` | Two-step CSV validate→start flow, and per-state journey ID sequences (D-12) |

**Storage layout.** `documents/YYYY/MM/<code>_<id>.pdf` and `.html`; `bulk/<job>/…` for the input CSV, ZIP
and manifests; `uploads/<id>.csv` for validated-but-unstarted uploads.

**Operational CLIs.** `python -m app.cli.ingest_hazards` (Excel → DB, idempotent) and
`python -m app.cli.maintenance purge|reap` (retention D-19; stuck-job recovery). Celery beat runs the
reaper every 10 minutes.

**Verified end-to-end** on the full Docker stack: one individual journey in ~5 s, and 120 bulk journeys in
48 s producing 113 PDFs with 2 isolated row failures. See [testing.md](testing.md).
