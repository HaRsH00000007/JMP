# Testing

112 automated tests, ~40 s on a laptop. No network, no API keys, no Redis: providers are mocked, Claude is
a fake client, the database is SQLite migrated through Alembic, and jobs run synchronously
(`JOB_EXECUTION=sync`). Chromium (Playwright) is required because PDF output is tested for real.

```bash
cd backend
.venv/Scripts/python -m pytest                 # everything
.venv/Scripts/python -m pytest tests/unit      # fast, no browser
.venv/Scripts/python -m pytest -m render       # PDF + API end-to-end only
.venv/Scripts/python -m pytest -k hazard -v    # by name
```

## What is covered

| Area | File | Key assertions |
|---|---|---|
| Hazard ingestion | `unit/test_hazard_library.py` | All 25 hazards match an independent transcription of the Excel (name, severity, probability, RPN); 2W and 4W control text byte-identical to the cells (typos included); severity bands and matrix zones (19 red / 6 yellow); idempotent re-ingest; refuses a changed file under an existing version, an inconsistent RPN formula, an out-of-range severity |
| Journey & CSV validation | `unit/test_validation.py` | Whitespace/vehicle/time normalisation, stop order preserved, empty/duplicate/too many stops, round trips, input hashing; CSV header aliases, BOM, stop-column gaps, duplicate route IDs, non-UTF-8, empty file |
| Geometry & routing | `unit/test_route_engine.py` | Haversine, polyline decoding (Google's documented example), simplification, projection; mock geocode hit/miss; route facts (ranges, contiguous segments, road-type split = 100%); OSRM step parsing and road categorisation; **a guard that the reference journey never appears in `app/`** |
| OSM providers | `unit/test_osm_providers.py` | Nominatim match-based confidence and ambiguity; Overpass node coordinates, level crossings, bridges, settlements, road samples; Open-Meteo profile — all with stubbed HTTP |
| Hazard engine | `unit/test_hazard_engine.py` | Every library hazard has a detector; detections on the demo route; seasonal/night hazards move to the verification list when the date is unknown; 2W vs 4W control sets; ranking order; segment risk from located hazards only; matrix-zone display mode |
| Scoring | `unit/test_scoring.py` | **The master PDF's arithmetic: weights × scores = 67.25 → 67**, weights sum to 1, formula caps and clamps, decision/risk rule precedence, fatigue stint splitting, recomputation is stable |
| Claude layer | `unit/test_llm.py` | Byte-identical cached prefix across journeys, single `cache_control` breakpoint, library only in the prefix, no geometry in the user message; valid JSON accepted; invalid JSON retried with errors appended after the cached prefix; missing fields, word limits, unknown hazard/segment IDs, invented numbers, phone/emergency/URL/e-mail, non-candidate hazard names all rejected; refusal not retried; `max_tokens` retried with a larger budget; API error → retryable mapping; usage rows and USD/INR cost persisted; secret redaction |
| Pricing | `unit/test_llm.py` | Cache-write/read multipliers, batch 50%, per-1,000 projection |
| PDF | `render/test_pdf.py` | Exactly 8 A4 pages, section order, footers `Page N of 8` (none on page 1), dynamic values printed, RPN values verbatim, hazard-pointer page contents, maps drawn from geometry, demo watermark, no sign-off block (v1.1), version stamps in PDF metadata, no overflow on 2-stop and 15-stop journeys, **overflow detected rather than clipped**, missing geometry fails cleanly |
| API | `integration/test_api.py` | Health/ready; individual generation → completed job → 8-page PDF download; version stamps; PROVIDED hospital in the directory; idempotency key; validation error envelope; unknown location → `GEOCODE_NOT_FOUND`; rerender; soft delete; bulk validate/preview; **bulk end-to-end with failure isolation** (3 succeed, 1 row fails, 1 invalid) with ZIP + manifests + summary; formula-injection neutralised; bulk idempotency; reference endpoints; bearer-token auth |
| Batch & ops | `integration/test_batch_and_ops.py` | Message Batches submission shape (1-hour TTL, shared prefix, no fallbacks), per-row realtime fallback on an errored result, batch usage rows; cancellation; counters increment once on redelivery; reaper ignores completed jobs; migrations upgrade **and** downgrade; retention purge (dry run, then files gone, audit metadata kept) |

## Acceptance runs (manual, against the Docker stack)

Both were executed on the full stack (PostgreSQL + Redis + Celery + nginx):

1. **Individual** — `samples/demo_individual_journey.json` → completed in ~5 s → 8-page, 480 KB PDF.
   A job failed earlier at the narrative stage was retried and resumed from that stage, keeping its ID.
2. **Bulk, 120 journeys** — `samples/bulk_demo_120_journeys.csv` → 115 valid / 5 invalid → **48 s**
   wall-clock → 113 PDFs, 2 row failures (deliberately unresolvable locations), ZIP 39.9 MB containing
   `pdfs/`, `manifest.csv`, `manifest.xlsx`, `summary.json`. Manifest has one row per input row including
   failures. Database: 115 documents, all 8 pages.
3. **Live OSM providers** — a real Zirakpur → Dera Bassi → Lalru route via Nominatim/OSRM/Overpass/
   Open-Meteo: NH7/NH152 detected from OSM tags, 4 bridges, monsoon window inferred, real hospitals/fuel
   returned as REQUIRES VERIFICATION, 8-page PDF rendered.
4. **Frontend** — all six pages screenshotted through nginx with no console errors.

## Conventions

* Deterministic tests only: fixed dates, fixed demo gazetteer, no randomness, no sleeps.
* `tests/conftest.py` owns the environment; a test that needs different settings uses
  `set_settings(get_settings().model_copy(update=...))` and restores it.
* The Pyraganda loop is **test data**, and `test_pyraganda_constant_is_test_data_only` fails the build if
  it ever appears in `app/`.

## Not covered automatically

* Live Anthropic calls (no key was available at build time). The Claude path is covered by a fake client;
  `LLM_PROVIDER=anthropic` with a real key should be smoke-tested once before go-live — confirm
  `cache_read_input_tokens > 0` on the second request via `GET /api/v1/usage/summary`.
* Live Google/Mapbox providers (no keys available).
* Load/soak testing beyond the 120-journey batch.
