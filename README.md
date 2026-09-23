# JMP — Journey Management Plan Generator

Generates standardised Journey Management Plan PDFs for road-safety planning, from a list of journey
stops. Built for Danone India EHS.

**The application computes every fact; Claude writes only prose.** Route geometry, distances, road types,
which of the 25 library hazards apply, their severity/probability/RPN values, risk bands, the journey
score, fatigue levels and the final decision are all produced by deterministic code from provider data and
the supplied hazard library. Claude receives those results and returns a small, schema-validated narrative,
which is checked for invented numbers, contacts and hazards before anything is rendered.

```
Journey input ─▶ geocode ─▶ route ─▶ road features / elevation ─▶ deterministic analysis
                                                                          │
                 25-hazard library (Excel, verbatim) ─▶ hazard engine ─▶ scoring ─▶ emergency directory
                                                                          │
                                       Claude (cached prefix, structured output, validated) ─▶ narrative
                                                                          │
                                       ReportModel ─▶ Jinja2 + SVG ─▶ Playwright ─▶ 8-page A4 PDF
```

## What it does

* **Individual JMP** — a start, up to 13 stops and an end produce one JMP PDF.
* **Bulk JMP** — a CSV of hundreds of journeys is validated, processed row-by-row in the background, and
  delivered as a ZIP with all PDFs plus CSV / XLSX / JSON manifests. One failed row never stops the batch.
* **Forms import** — converts a Microsoft Forms "High Risk Routes" export into the bulk CSV, expanding the
  repeating route blocks and verifying every address against the geocoder first.
* **Fixed report** — 8 A4 pages, including a route hazard-pointer page drawn from measured positions.
* **Auditable** — every document stores its inputs, route facts, hazards, scores, Claude output, token
  usage, cost and six independent version stamps.

## Quick start

```bash
cp .env.example .env
docker compose up --build         # first build ≈ 3–5 min (installs Chromium)
```

UI <http://localhost:8080> · API docs <http://localhost:8000/api/docs>

Without Docker (single process, SQLite, no Redis) — see [docs/setup.md](docs/setup.md).

Defaults run in demo mode: no API keys needed, mock route data, and every PDF watermarked
*DEMO — NOT FOR OPERATIONAL USE*.

## Supplying the source files

Two inputs are **not in this repository** because they are client documents (the master plan is marked
Confidential):

| Put here | What it is |
|---|---|
| `source/JMP Template- 25 Hazards.xlsx` | The 25-hazard library. **Required** — the app refuses to start generating without it |
| `source/Danone_JMP_…pdf` | The master visual reference. Optional; only used as the design source |

Then load the library (idempotent; the API container does this automatically):

```bash
python -m app.cli.ingest_hazards
```

Ingestion stores every value verbatim and refuses a workbook whose RPN codes, severities or risk-matrix
image don't match what was reviewed. Real journey data (`data/`) is likewise excluded — it contains
employee names, e-mail addresses and home addresses.

## Configuration that matters

| Variable | Default | Notes |
|---|---|---|
| `LLM_PROVIDER` | `mock` | `anthropic` + `ANTHROPIC_API_KEY` for real narratives |
| `ANTHROPIC_MODEL` | `claude-opus-5` | ≈ $0.13 per JMP; see [docs/cost-model.md](docs/cost-model.md) |
| `GEOCODER` / `ROUTE_PROVIDER` / … | `mock` | `mock`, the free OSM profile, or Google/Mapbox — see below |
| `BULK_LLM_MODE` | `realtime` | `batch` uses the Message Batches API: 50% cheaper, up to 24 h |
| `JOB_EXECUTION` | `celery` | `celery` (production) · `thread` (single process) · `sync` (tests) |

Full list in `.env.example`.

### Choosing a geocoder — read this before bulk runs

Address quality decides everything. Measured on real submissions:

| Provider | Setup | Result on real Indian field-visit addresses |
|---|---|---|
| `mock` | none | Demo gazetteer only; anything else fails by design |
| **OSM** (`nominatim` + `osrm` + `overpass`) | free, no key | **~20% resolve.** Private clinics, colonies and landmark phrasing ("opposite X hospital", "Home vytila", "DB point") are not in OpenStreetMap. Public servers also cap Overpass at 2 concurrent queries, so bulk is slow |
| **Google** (`google` + key) | paid, ≈ $5/1,000 lookups | Handles typos, POIs and landmark phrasing — the realistic choice for this data. Written to spec but not yet exercised against a live key |

The system never guesses a location: an address it cannot verify fails that row with `GEOCODE_NOT_FOUND`
rather than routing a driver somewhere invented.

## Repository layout

| Path | What it is |
|---|---|
| `backend/` | FastAPI app, hazard/route/scoring engines, Claude layer, report renderer, Celery workers |
| `backend/config/` | All business rules as versioned YAML (hazard detection, scoring, seasons, risk matrix) |
| `backend/templates/` | The fixed 8-page report (Jinja2 + CSS + embedded fonts) |
| `frontend/` | React + TypeScript dashboard (Vite) |
| `samples/` | Demo journey JSONs and a 120-row bulk CSV that runs without any credentials |
| `docs/` | Analysis, architecture, API, generation flow, decisions, setup, testing, cost model |

## Documentation

| Doc | Contents |
|---|---|
| [docs/setup.md](docs/setup.md) | Running locally, configuration, provider profiles |
| [docs/architecture.md](docs/architecture.md) | Components, database schema, versioning, as-built notes |
| [docs/pdf-analysis.md](docs/pdf-analysis.md) | Master PDF structure, palette, page-by-page mapping |
| [docs/hazard-library.md](docs/hazard-library.md) | The 25 hazards, risk matrix, detection method per hazard |
| [docs/api-design.md](docs/api-design.md) | Every endpoint, payloads, error codes |
| [docs/generation-flow.md](docs/generation-flow.md) | Pipeline stages, Claude caching/validation, rendering, bulk |
| [docs/cost-model.md](docs/cost-model.md) | Measured token profile and cost per JMP / 100 / 1,000 |
| [docs/testing.md](docs/testing.md) | Test map and how to run it |
| [docs/development.md](docs/development.md) | Working on the code, adding hazards/rules/providers |
| [docs/deployment.md](docs/deployment.md) | Production topology, scaling, secrets, retention |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Every error code and what it means |
| [docs/decisions.md](docs/decisions.md) | 22 business decisions, defaults implemented, what needs EHS sign-off |

## Tests

```bash
cd backend && .venv/Scripts/python -m pytest      # 119 tests, ~35 s
```

No network, no API keys, no Redis: providers are mocked, Claude is a fake client, the database is SQLite
migrated through Alembic. Chromium is required because PDF output is tested for real — page count, A4
size, section order, and that content never silently overflows.

## Status

All nine build phases are complete and tested. Before operational use, **12 of the 22 decisions in
[docs/decisions.md](docs/decisions.md) need Danone EHS sign-off** — chiefly the risk-band method
(severity-only vs the Danone matrix), the scoring rubric and decision thresholds, the shortened control
wording, and the seasonal windows. All are implemented as configurable defaults and every report states
that EHS validation applies.

Not yet exercised against live credentials: the Anthropic API (the Claude path is covered by a fake client)
and the Google/Mapbox providers.
