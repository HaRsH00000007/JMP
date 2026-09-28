# Integrating JMP with the Drive4Sure mobile backend

For engineers wiring JMP into an existing mobile app backend. The client-facing version of this analysis is
`Drive4Sure_System_Architecture_and_JMP_Integration_v2.pdf` in the repository root; this file is the developer's
cut of the same material.

## The shape of the problem

JMP is a **Python / FastAPI / PostgreSQL** service that turns journey stops into an assessed PDF. Drive4Sure is a
**NestJS / MongoDB** backend for a driver app. They share nothing but HTTP and S3.

JMP is *not* a feature to be built inside the mobile backend. It already exists, with 28 endpoints, 18 tables and
143 tests, and it is the system of record for the risk assessment and the document. The integration is a service
boundary.

| Owned by Drive4Sure | Owned by JMP |
|---|---|
| Driver identity, login, profile, working hours | Geocoding, routing, road attributes, elevation |
| Vehicle registry, documents, access rules | Hazard identification, risk bands, scoring, decision |
| GPS, trip detection, the actual journey | Narrative prose (Claude, validated) |
| Showing the plan, notifications, authorisation | The PDF and its audit trail |

## Why JMP is not ported to TypeScript

Because the expensive part is not the code, it is the confidence in it. The hazard engine decides which hazards a
driver is warned about; the scoring rules are versioned and stamped on every document so old plans stay
reproducible. A second implementation that differs subtly is worse than none. Chromium is needed for the PDF either
way, so porting does not even remove a dependency — it just moves it to Puppeteer.

## Contract

Server-to-server only. The phone never holds a JMP credential and never calls JMP directly: JMP has no per-user
authentication, so exposing it to devices would expose every driver's documents. Drive4Sure holds one service token
(`API_AUTH_TOKEN`) and does all per-driver authorisation itself.

```
POST /api/v1/journeys/generate          Idempotency-Key: <uuid>
{ "start_location": "...", "stops": ["..."], "end_location": "...",
  "vehicle_type": "4W", "travel_date": "2026-10-01", "depart_time": "09:30",
  "manager_name": "...", "emergency_contact": "...",
  "nearest_hospital": "...", "nearest_police": "...", "city": "Agra" }

→ 202 { "job_id": "...", "journey_id": "...", "status": "queued" }

GET  /api/v1/jobs/{job_id}              poll with backoff
GET  /api/v1/documents/{id}/download    the PDF
```

`Idempotency-Key` is honoured by JMP, so a retry after a timeout cannot produce a second plan or a second Claude
charge. Generate it once per plan request, store it, and reuse it on every retry.

### Field mapping notes

* `vehicle_type` must reduce to exactly `"2W"` or `"4W"` — it selects the control set printed in the document.
* `city` materially improves address disambiguation. Send it whenever known.
* `nearest_hospital`, `nearest_police`, `emergency_contact`: **send only real values.** They are printed as PROVIDED
  on a page a driver may rely on in an emergency. Omitted values become an explicit instruction to verify; guessed
  values become a wrong instruction.
* Drive4Sure's health check and vehicle inspection are *not* JMP inputs — JMP has no field for them. Use them as a
  pre-flight gate on the Drive4Sure side.

## Asynchrony is mandatory

With free public map providers a plan takes **5–7 minutes**, almost all of it waiting on geocoding, routing and
elevation. Even self-hosted it is seconds, not milliseconds. A synchronous call would hold a mobile request open and
block a Node worker.

Drive4Sure has **no queues, workers or scheduled jobs today**. That is the main structural gap: it must gain a queue
(BullMQ on Redis) before the JMP module is written. JMP already needs Redis for Celery, so one instance with separate
database indexes serves both.

## Error handling is a product surface, not a toast

On real field-visit data, roughly **one itinerary in three cannot be fully located** by free OpenStreetMap geocoding
— private clinics, shop names and landmark phrasing are simply absent from OSM. JMP refuses rather than guessing,
which is correct, but it means the app needs a real screen for it.

| JMP error | Retry? | App behaviour |
|---|---|---|
| `GEOCODE_NOT_FOUND` | No | Show **which** address failed; let the driver correct it |
| `GEOCODE_AMBIGUOUS` | No | Offer the candidates from the error details |
| `ROUTE_NOT_FOUND` | No | Usually a duplicated stop; ask the driver to check |
| `PROVIDER_UNAVAILABLE` | Yes | Transient; retry with backoff in the worker |
| `LLM_UNAVAILABLE`, `LLM_INVALID_OUTPUT` | Yes | Retry; JMP resumes from the narrative stage and does not repeat the map cost |
| `LLM_NOT_CONFIGURED` | No | Deployment fault — alert ops, do not surface |
| `RENDER_OVERFLOW`, `PDF_RENDER_FAILED` | No | JMP defect — alert ops, keep the plan for diagnosis |
| `VALIDATION_ERROR` | No | Bad payload from Drive4Sure — a mapping bug; catch it in contract tests |

## What Drive4Sure has to add

| Addition | Why |
|---|---|
| BullMQ + Redis | Somewhere to wait for an asynchronous result and to retry it |
| HTTP client with timeouts, retry and a circuit breaker | First internal service dependency; a JMP outage must not exhaust the event loop |
| `journey_plans` collection | References only: `jmpJobId`, `jmpDocumentId`, `pdfStorageKey`, status, the six version stamps, error code |
| Shared private S3 bucket | JMP writes the PDF; Drive4Sure returns a signed link through its existing document path |
| API versioning on new routes | Drive4Sure has none; JMP is already `/api/v1` |
| PostgreSQL for JMP | JMP is relational with Alembic migrations. Two datastores is the normal cost of a service boundary |
| Docker for JMP | JMP needs Python, Playwright and Chromium — far easier as an image |

**Never cache JMP's risk fields as authoritative.** Store `riskLevel`, `journeyScore` and `decision` for list
screens only, always alongside the document version, and treat JMP as the system of record. A drifted copy of a risk
assessment is dangerous.

## Prerequisites on the Drive4Sure side

1. Remove and rotate the hard-coded credentials before adding the JMP service token to the same file.
2. Secure file upload and make document links private — a JMP PDF names the driver, the manager and the full route.
3. Decide which timezone defines "today", so `travel_date` and the daily checks agree (the backend currently uses
   UTC, so the day changes at 05:30 IST).
4. Switch on trip detection — only needed for a later "planned versus actual" comparison, not for generating plans.

## Testing the integration

Run contract tests against a real JMP instance in **mock-provider mode**: no network, no API keys, no cost, and it
exercises the true request and response shapes. Keep one end-to-end test against real providers in staging, not CI.

Cover: field mapping (including the 2W/4W reduction and omitting unknown emergency contacts), all 17 error codes,
idempotent retry producing exactly one document, worker backoff and redelivery, cross-driver authorisation, and the
timezone boundary.

## Before you build against this

At the time of writing, the JMP working tree carried uncommitted changes (a text-only plan path, a cost-reporting
CLI and a narrative-resume tool). **Commit and tag first, then pin that tag.** Also note that 12 of the 22 business
decisions in [decisions.md](decisions.md) still need Danone EHS sign-off — the version stamps keep old documents
interpretable when they change, but wide rollout should wait for them.
