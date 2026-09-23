# Deployment

The compose file is a working production-shaped topology; this notes what to change for a real
environment.

## Topology

```
            ┌────────────┐
  users ───▶│ nginx / LB │──▶ frontend (static) and /api ──▶ api (uvicorn, N replicas)
            └────────────┘                                        │
                                                    Postgres ◀────┼────▶ Redis (broker)
                                                    Object store ◀┘        ▲
                                                                           │
                     worker (pipeline,render,bulk)  ×N   ·   worker-llm (llm) ×1..N   ·   beat ×1
```

* **api** — stateless; scale horizontally. Runs migrations and hazard ingestion at start; both are
  idempotent, but with several replicas prefer running them as a one-off job before rollout.
* **worker** — CPU/memory bound (Chromium). One Chromium per process; size `--concurrency` to cores and
  ~400 MB per concurrent render.
* **worker-llm** — its `--concurrency` is the hard cap on parallel Claude requests. Set it from your
  account's rate limits, not from CPU.
* **beat** — exactly one replica (re-dispatches stuck jobs).

## Required changes for production

| Area | Change |
|---|---|
| Secrets | Inject `ANTHROPIC_API_KEY`, `POSTGRES_PASSWORD`, `ROUTE_PROVIDER_API_KEY`, `API_AUTH_TOKEN` from your secret manager, not `.env` |
| Database | Managed PostgreSQL 16 with backups and PITR; `DATABASE_URL=postgresql+psycopg://…` |
| Storage | `STORAGE_BACKEND=s3` with `S3_BUCKET`, `S3_REGION` (an India region for data residency, D-19), server-side encryption is set on every upload |
| Auth | `API_AUTH_TOKEN` at minimum. For per-user identity and the preparer/reviewer/approver roles, put an OIDC proxy in front and map claims to the `users` table (D-15 — still open) |
| TLS | Terminate at the load balancer; the API sets `--proxy-headers` |
| Providers | Self-host OSRM/Nominatim/Overpass, or use Google/Mapbox with a key. Public OSM endpoints are rate-limited and unsuitable for bulk |
| Logging | Ship stdout JSON to your log platform. Secrets are redacted by a filter, but keep log retention policies in mind: journeys contain employee travel plans |
| Monitoring | `/api/v1/health/ready` for probes; alert on failed jobs, queue depth, `GET /api/v1/usage/summary` cost drift, and cache hit ratio dropping (a sign the prefix changed) |

## Scaling notes

* Throughput is bounded by the routing provider and the Anthropic rate limit, not CPU. Measured on one
  laptop with 4 pipeline workers and mock providers: **120 journeys in 48 s**.
* For large batches use `BULK_LLM_MODE=batch` (50% cheaper) when the results can wait.
* `route_cache` de-duplicates provider calls for 30 days (`ROUTE_CACHE_TTL_DAYS`), which matters when many
  journeys share legs.
* PDFs are written once and served from storage; the API streams them with an auth check (no public URLs).

## Data retention (D-19)

Default: keep documents two years, then purge the files while keeping audit metadata.

```bash
python -m app.cli.maintenance purge --retention-days 730 --dry-run
python -m app.cli.maintenance purge --retention-days 730
```

Run it on a schedule (cron/Kubernetes CronJob). It also deletes validated-but-unused CSV uploads older
than 24 h. Soft-deleted documents (`DELETE /documents/{id}`) have their files removed on the next purge;
the row, its hashes, versions and usage stay for audit.

## Backups and recovery

* PostgreSQL holds all state; object storage holds PDFs/HTML. Back up both.
* A document can be re-rendered from `jmp_documents.report_json` with `POST /documents/{id}/rerender` —
  no Claude or provider calls, so a lost PDF file is recoverable as long as the row survives.
* A whole journey can be re-run from `journeys.input` if you want fresh route data.

## Upgrades

1. Apply Alembic migrations (`alembic upgrade head`) — the image entrypoint does this.
2. Version bumps are deliberate: template, prompt, schema, scoring, rules and hazard library each have
   their own version, and every document records all six. Bump them when you change the corresponding
   artefact so existing documents stay explainable.
3. Rolling restarts are safe: tasks use `acks_late`, stages are idempotent, and the beat reaper
   re-dispatches anything left mid-flight.
