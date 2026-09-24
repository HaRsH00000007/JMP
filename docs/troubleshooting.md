# Troubleshooting

Every failure is recorded on the job (`GET /api/v1/jobs/{id}` → `error.code`) and, for bulk rows, in the
manifest. Codes are stable; this is what each one means.

## Job error codes

| Code | Meaning | What to do |
|---|---|---|
| `VALIDATION_ERROR` | Input rejected before any work (empty stop, duplicate consecutive locations, bad time, CSV gap) | Fix the input; `details` names the field |
| `GEOCODE_NOT_FOUND` | The geocoder found nothing | Use a fuller address. In demo mode only gazetteer places resolve — this is deliberate, the mock never invents a location. On real field data, see `ALLOW_UNVERIFIED_STOPS` in [setup.md](setup.md) to keep the journey and list the address as unverified instead of failing the row |
| `GEOCODE_AMBIGUOUS` | Several strong candidates far apart, or a low-confidence match | Disambiguate ("Rampur, Punjab"). `details.candidates` lists what was found |
| `ROUTE_NOT_FOUND` | The provider could not route between two points | Check the stop order and that points are reachable by road |
| `ROUTE_GEOMETRY_UNAVAILABLE` | A route came back without geometry | Provider issue; maps are never faked, so the job fails instead |
| `PROVIDER_UNAVAILABLE` | Routing/features/elevation 5xx, timeout or rate limit | Retried automatically with backoff. Persistent → self-host or switch provider (see setup.md) |
| `LLM_UNAVAILABLE` | Anthropic 429/5xx/timeout/network | Retried automatically. Persistent → check status and `LLM_CONCURRENCY` against your rate limits |
| `LLM_NOT_CONFIGURED` | No/invalid API key, or the request was rejected as invalid | Set `ANTHROPIC_API_KEY`, or `LLM_PROVIDER=mock` for demos |
| `LLM_INVALID_OUTPUT` | Narrative failed validation after `LLM_MAX_ATTEMPTS` | `error.message` lists the exact validation errors. Not retried blindly — see below |
| `LLM_REFUSAL` | The model declined | Not retried. Check the journey text for something that reads as unsafe |
| `RENDER_OVERFLOW` | Content does not fit the fixed A4 layout | The report is never silently clipped. `details` names the page and element; usually a very long place name or an unusually long narrative |
| `PDF_RENDER_FAILED` | Chromium crashed or timed out | Retried once with a fresh browser. Persistent → check container memory (~400 MB per concurrent render) |
| `HAZARD_LIBRARY_MISSING` | No active library, or it changed mid-job | Run `python -m app.cli.ingest_hazards` |
| `INTERNAL_ERROR` | Unexpected | Check logs by `job_id` |

Retry a failed job from its failed stage: `POST /api/v1/jobs/{id}/retry` (the earlier stages' results are
reused — a retry after the narrative stage does not re-run routing). For bulk:
`POST /api/v1/bulk-jobs/{id}/retry-failed`.

## Symptoms

**"Demo mode" banner / PDFs watermarked.** `LLM_PROVIDER=mock` or a mock route provider is configured.
Set real providers and an API key; see [setup.md](setup.md) §3.

**Nominatim returns 403.** Their policy rejects placeholder user agents. Put a real contact in
`PROVIDER_USER_AGENT` (anything containing `example.com` is blocked).

**Overpass is very slow (100 s+) or returns 429.** The public endpoint is shared and rate-limited, and the
client keeps itself inside the two concurrent slots `overpass-api.de` publishes, so concurrent rows queue.
When a server is down or shedding load the next mirror in `OVERPASS_FALLBACK_URLS` is tried before the row
fails. Self-host Overpass for production, or set `FEATURE_PROVIDER=none` to run without OSM road features —
the report then relies on provider road names only, and hazards that need features will not be detected
(they appear as verification items instead).

**Bulk rows fail with `PROVIDER_UNAVAILABLE` and Open-Meteo says "Hourly API request limit exceeded".**
Elevation is billed per coordinate, not per request, so route length is what spends the quota: at the
configured 250 m step a 146 km route costs ~585 of a 5,000/hour free tier, i.e. about eight routes an hour.
`ELEVATION_MAX_SAMPLES` (default 200) widens the step on long routes to stay inside it, and any document
whose profile was coarsened says so in its route warnings. Nothing but waiting clears an exhausted hour —
it resets on the hour, and no retry inside it will succeed.

**Many bulk rows fail with `PROVIDER_UNAVAILABLE` naming Open-Meteo.** Its free tier is limited per minute
and a long route is fetched in 100-point chunks, so several workers together trip it within seconds. The
client serialises elevation requests, spaces them, and retries a 429 for up to 5 attempts honouring
`Retry-After`. If it still fails, the limit is being shared with something else on the same IP: run the
batch in smaller pieces, or set `ELEVATION_PROVIDER=none` (gradient hazards then become verification items
rather than measured findings).

**`cache_read_input_tokens` stays 0 across requests.** The cached prefix is being invalidated. It is
byte-stable by construction (a test asserts it), so check: the model changed, `ANTHROPIC_EFFORT` changed,
the hazard library version changed, or requests are more than the TTL apart (`ANTHROPIC_CACHE_TTL=5m` by
default — see [cost-model.md](cost-model.md) §4).

**Repeated `LLM_INVALID_OUTPUT`.** The validator's message names the rule that failed. Common causes:
numbers in the prose that are not in the facts (the model inferred something), or a hazard name that is not
a candidate. This is working as intended — the report is not rendered from unverified text. If a
legitimate value is being rejected, it belongs in the facts payload (`app/llm/facts.py`), not in an
exception to the validator.

**Bulk job stuck at "processing" with no progress.** Check the workers are running
(`docker compose ps`), then queue depth in Redis. Jobs stuck in `processing` for longer than
`STUCK_JOB_TIMEOUT_MIN` are re-dispatched by beat; force it with `python -m app.cli.maintenance reap`.

**Percentages look wrong in bulk.** `percentage` is over *valid* rows; invalid rows are reported
separately (they were never processed) and still appear in the manifest.

**Playwright error "Sync API inside asyncio loop".** Should not happen — renders go through a dedicated
renderer thread. If it appears, something is calling `pdf_renderer._render_pdf` directly instead of
`render_pdf`.

**Migrations fail on SQLite with "no such table".** The initial migration must run before ingestion:
`alembic upgrade head` then `python -m app.cli.ingest_hazards`.

**Hazard ingestion refuses the workbook.** Either the file differs from one already ingested under that
version (use a new version), or the risk-matrix image changed. The matrix is transcribed by hand into
`backend/config/risk_matrix.yaml` and pinned by SHA-256 — a changed image must be re-transcribed and
re-reviewed by EHS before it is trusted.
