# Setup

Two ways to run it: **Docker** (full stack, closest to production) or **bare Python** (single process, no
Redis needed). Both start in demo mode with no credentials.

## 1. Docker (recommended)

Requirements: Docker Desktop (or Docker Engine + Compose v2).

```bash
cp .env.example .env
docker compose up --build          # first build ≈ 3–5 min (installs Chromium)
```

| Service | Purpose | Port |
|---|---|---|
| `frontend` | React dashboard behind nginx, proxies `/api` to the API | 8080 |
| `api` | FastAPI; runs migrations + hazard ingestion on start | 8000 |
| `worker` | Celery worker for queues `pipeline,render,bulk` | — |
| `worker-llm` | Celery worker for queue `llm` — its `--concurrency` caps parallel Claude calls | — |
| `beat` | Celery beat: re-dispatches stuck jobs every 10 min | — |
| `postgres` / `redis` | Database and broker | — |

Useful commands:

```bash
docker compose ps                      # health
docker compose logs -f api worker      # follow logs
docker compose down                    # stop (keeps volumes)
docker compose down -v                 # stop and delete database + stored PDFs
```

## 2. Bare Python (no Docker, no Redis)

Requirements: Python 3.11+ and Node 20+ (frontend only).

```bash
cd backend
python -m venv .venv
.venv/Scripts/python -m pip install -e .            # Linux/macOS: .venv/bin/python
.venv/Scripts/python -m playwright install chromium # PDF engine

# SQLite + in-process workers is the default in .env.example:
#   DATABASE_URL=sqlite:///./var/jmp_dev.db
#   JOB_EXECUTION=thread
.venv/Scripts/python -m alembic upgrade head
.venv/Scripts/python -m app.cli.ingest_hazards
.venv/Scripts/python -m uvicorn app.main:app --reload --port 8000
```

Frontend (second terminal):

```bash
cd frontend
npm install
npm run dev            # http://localhost:5173, proxies /api to :8000
```

`JOB_EXECUTION=thread` runs the pipeline in a thread pool inside the API process — fine for development
and demos, not for production (no retry durability across restarts). Use `celery` in production.

## 3. Configuration

Everything comes from environment variables (`.env`). `.env.example` documents every key; the ones that
change behaviour most:

| Variable | Default | Notes |
|---|---|---|
| `LLM_PROVIDER` | `mock` | `anthropic` for real narratives; `mock` writes template prose and watermarks the PDF |
| `ANTHROPIC_API_KEY` | — | Required when `LLM_PROVIDER=anthropic` |
| `ANTHROPIC_MODEL` | `claude-opus-5` | See [cost-model.md](cost-model.md) before changing |
| `ANTHROPIC_CACHE_TTL` | `5m` | `1h` pays 2× on cache writes; worth it only at ≥3 requests/hour (see cost-model) |
| `LLM_CONCURRENCY` | `4` | Parallel Claude calls (the `worker-llm` concurrency) |
| `BULK_LLM_MODE` | `realtime` | `batch` uses the Message Batches API: 50% cheaper, up to 24 h |
| `GEOCODER` / `ROUTE_PROVIDER` / `FEATURE_PROVIDER` / `ELEVATION_PROVIDER` / `PLACES_PROVIDER` | `mock` | Provider profile, see below |
| `JOB_EXECUTION` | `celery` | `celery` \| `thread` \| `sync` |
| `API_AUTH_TOKEN` | empty | When set, every `/api/v1` call (except `/health`) needs `Authorization: Bearer <token>` |
| `HAZARD_POINTER_COUNT` | `5` | Hazards on the pointer page (3–8) |
| `USD_TO_INR` | `88.0` | FX rate used for INR cost reporting |
| `REPORT_DEMO_WATERMARK` | `true` | Prints the DEMO banner when route data or narrative is mocked. Hiding it does not change what the document records |

### Provider profiles (D-09)

**Demo (default)** — no network, no keys. Uses the demo gazetteer in
`backend/app/providers/mock_gazetteer.json`. Unknown locations fail with `GEOCODE_NOT_FOUND` (nothing is
invented) and all output is watermarked.

```env
GEOCODER=mock
ROUTE_PROVIDER=mock
FEATURE_PROVIDER=mock
ELEVATION_PROVIDER=mock
PLACES_PROVIDER=mock
```

**Open data (free, no API key)** — real geocoding, routing, road features and elevation:

```env
GEOCODER=nominatim
ROUTE_PROVIDER=osrm
FEATURE_PROVIDER=overpass
ELEVATION_PROVIDER=open_meteo
PLACES_PROVIDER=overpass
PROVIDER_USER_AGENT=JMP-Generator/1.0 (your EHS team, your-real-contact@yourcompany.com)
```

Verified working against the public endpoints. Two caveats:

* The public servers are rate-limited and **not** for bulk production use — a single route analysis takes
  around 150 s, almost all of it Overpass. For production, self-host OSRM, Nominatim and Overpass and point
  `OSRM_BASE_URL`, `NOMINATIM_BASE_URL` and `OVERPASS_URL` at them (see [deployment.md](deployment.md)).
* Nominatim rejects placeholder user agents (anything with `example.com`). Put a real contact in
  `PROVIDER_USER_AGENT`, as their usage policy requires.

**Commercial** — Google or Mapbox for geocoding and routing (a key is needed; OSM still supplies road
features and elevation):

```env
GEOCODER=google            # or mapbox
ROUTE_PROVIDER=google      # or mapbox
ROUTE_PROVIDER_API_KEY=...
FEATURE_PROVIDER=overpass
ELEVATION_PROVIDER=open_meteo
```

These implementations follow the documented REST APIs but were **not exercised against live keys**, since
none were available during the build. Test them on a handful of journeys before bulk use. Note also the
open licensing question in D-09: Google's terms restrict caching its content and displaying it on a
non-Google map, and this report draws its own SVG map from route geometry — confirm that is permitted
under your agreement, or use Mapbox/OSM.

## 4. First run checklist

```bash
curl http://localhost:8000/api/v1/health/ready      # database, storage, redis, hazard library
curl http://localhost:8000/api/v1/settings          # effective configuration, versions
curl -X POST http://localhost:8000/api/v1/journeys/generate \
     -H "Content-Type: application/json" \
     -d @samples/demo_individual_journey.json       # returns {"job_id": ...}
curl http://localhost:8000/api/v1/jobs/<job_id>     # poll until "completed"
curl -o jmp.pdf http://localhost:8000/api/v1/documents/<document_id>/download
```

## 5. Updating the hazard library

The Excel workbook is authoritative. To load a new revision:

```bash
python -m app.cli.ingest_hazards --file "/source/JMP Template- 25 Hazards.xlsx" --version 1.1
```

Ingestion is idempotent for the same file and version, and refuses a *different* file under an existing
version (versions are immutable). It also refuses to load if the workbook's risk-matrix image differs from
the transcription in `backend/config/risk_matrix.yaml`, which must then be re-checked by EHS. Existing
documents keep their pinned library version.
