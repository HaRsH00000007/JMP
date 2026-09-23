# Development guide

How to work on the code and where to change what. Setup is in [setup.md](setup.md).

## Layering rules

These keep the safety properties of the system intact:

1. **Route processing never imports the LLM layer, and the LLM layer never imports providers.**
2. **The PDF renderer never calls Claude.** It receives a validated `ReportModel` and nothing else.
3. **Hazard rules are not in the presentation layer.** Templates print what the engine decided.
4. **Claude never sets a number.** If a value can be computed, compute it in `services/` and pass it in
   the facts payload.
5. **Never invent geography.** A provider that cannot resolve something raises; no fallback guesses.

## Where things live

```
backend/app/
  settings.py            every env var
  rules_config.py        loads backend/config/*.yaml
  versions.py            the six version stamps
  domain/                pure models & maths (geo, solar, facts, route) — no I/O
  providers/             base protocols, mock, osm (free), commercial (Google/Mapbox), registry
  services/
    hazard_library.py    Excel ingestion + verbatim access
    route_service.py     geocode → route → features → analysis (caching)
    hazard_engine/       detectors.py (one per kind) + engine.py (ranking, segment risk)
    scoring.py           dimensions, risk level, decision, fatigue
    emergency.py         directory with classifications
    report_assembler.py  facts + narrative → ReportModel
    pipeline.py          the pure stage functions
    jobs.py              persisted stages, retries, dispatch
    bulk.py              CSV fan-out, counters, finalize (ZIP/manifests), batch mode
  llm/                   static_prefix, facts, schemas (NarrativeV1), validator, client, batch, pricing
  rendering/             html_renderer, pdf_renderer, svg/ (maps, icons)
  api/v1/                FastAPI routers
  workers/               Celery app + task wrappers
  cli/                   ingest_hazards, maintenance
backend/config/          hazard_rules, scoring, seasons, risk_matrix, emergency_static, hazard_display
backend/templates/jmp/v1 report.html.j2, partials/, styles.css, fonts/
```

## Common changes

### Tune a hazard rule

`backend/config/hazard_rules.yaml` → the `detectors` block. Bump `version` (it is stamped on every
document as `rules_version`). No code change unless you need a new *kind* of detector.

### Add a detector kind

Add a function in `services/hazard_engine/detectors.py` decorated with `@detector("my_kind")`, returning a
`Detection`. Point a hazard's `kind` at it in `hazard_rules.yaml`. `test_every_library_hazard_has_a_detector`
enforces that all 25 hazards resolve.

### Change scoring, decision or fatigue rules

`backend/config/scoring.yaml`. Dimension scores are linear: `base + Σ coef × min(metric, cap)`, clamped.
Available metrics are whatever `scoring.metrics()` returns — add one there if you need it. Bump `version`.

### Change the report layout

`backend/templates/jmp/v1/`. Structure is fixed in the partials; `styles.css` holds the palette from the
master PDF. Bump `TEMPLATE_VERSION` (`.env` / `settings.template_version`) for any visual change, then run
`pytest -m render` — the overflow check fails the build if content no longer fits A4.

### Change the prompt

`backend/app/llm/prompts/v1/*.md`. **Any edit changes the cached prefix**, so bump `PROMPT_VERSION` in
`app/versions.py`. Adding or removing a narrative field also means bumping `SCHEMA_VERSION` in
`app/llm/schemas.py` and updating `validator.py` coverage checks.

### Add a provider

Implement the protocol in `app/providers/base.py`, register it in `registry.py`, add the literal to the
matching setting in `settings.py`. Providers must raise `GeocodeError` / `RouteError` / `ProviderError`
rather than returning approximations.

## Running pieces in isolation

```bash
# analysis only, no LLM, no PDF
python -c "from app.db.session import session_scope; ..."   # see tests/conftest.py fixtures

python -m app.cli.ingest_hazards --version 1.0
python -m app.cli.maintenance purge --dry-run
python -m app.cli.maintenance reap
celery -A app.workers.celery_app worker -Q pipeline,render,bulk -c 4
celery -A app.workers.celery_app worker -Q llm -c 4
```

## Conventions

* Type hints everywhere; Pydantic models at every boundary; dataclasses for internal structures.
* Errors carry a stable `ErrorCode`; `retryable` decides whether a worker retries.
* Logging is structured (`structlog`) and redacted — never log headers, keys or full provider URLs.
* Tests are deterministic and offline; see [testing.md](testing.md).
* Keep functions honest about uncertainty: if data cannot establish something, return `VERIFY` and let it
  reach the verification list rather than guessing.
