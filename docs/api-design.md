# API Design (v1)

Base path: `/api/v1`. All requests and responses are JSON unless noted. Errors use one envelope:

```json
{ "error": { "code": "VALIDATION_ERROR", "message": "…", "details": [ { "field": "stops[1]", "issue": "empty" } ] } }
```

Auth: bearer token (JWT or SSO-issued), role-checked per route. → [DECISION D-15]

## 1. Individual generation

### `POST /journeys/generate` → `202 Accepted`

```json
{
  "start_location": "Paras Downtown Zirakpur, Punjab",
  "stops": ["Chandigarh City Center Zirakpur, Punjab", "SBP Housing Park Society Derabassi, Punjab"],
  "end_location": "Danone Nutricia India Plant, Lalru, Punjab",

  "vehicle_type": "4W",                       // "2W" | "4W"            (D-08)
  "travel_date": "2026-10-05",                // optional → drives seasonal hazards
  "depart_time": "09:30",                     // optional → night/fatigue rules
  "manager_name": "…",                        // optional, from Excel Header sheet → PROVIDED
  "emergency_contact": "…",                   // optional → PROVIDED
  "nearest_hospital": "…", "nearest_police": "…",   // optional → PROVIDED (else REQUIRES VERIFICATION)
  "idempotency_key": "…"                      // optional; same key within 24h returns the same job
}
```

Validation (synchronous, pure):

- start and end are non-empty after trimming, 3–300 chars
- 0 ≤ stops ≤ 13 (configurable) [ASSUMPTION]
- no empty stop strings
- consecutive duplicate locations are rejected
- `start == end` is allowed and sets `is_round_trip`
- stop order is preserved exactly as submitted

Response:

```json
{ "job_id": "7c1…", "journey_id": "a90…", "status": "queued" }
```

### `GET /jobs/{job_id}`

```json
{
  "job_id": "7c1…", "kind": "individual", "status": "processing",
  "stage": "narrative", "attempt": 1,
  "queued_at": "…", "started_at": "…", "finished_at": null,
  "document_id": null,
  "error": null                                    // {code, message} when failed
}
```

`status` is one of `queued | processing | completed | failed | cancelled`. `stage` gives finer progress for the UI.

### `POST /jobs/{job_id}/retry`

Allowed only when the job has `failed`. It resumes from the failed stage, reusing the stored upstream outputs.

### `POST /jobs/{job_id}/cancel`

## 2. Documents

| Method | Path | Purpose |
|---|---|---|
| GET | `/documents?q=&status=&region=&risk=&from=&to=&bulk_job_id=&page=&size=` | Search/filter the reports list |
| GET | `/documents/{id}` | Metadata, versions, usage summary, `report_json` |
| GET | `/documents/{id}/download` | `application/pdf`, `Content-Disposition: attachment; filename="DAN-JMP-PB-001.pdf"` |
| GET | `/documents/{id}/preview` | Inline PDF, for the viewer |
| POST | `/documents/{id}/rerender` | Re-render from the stored `report_json` with the current template. No LLM or provider calls. Creates a new document version |
| DELETE | `/documents/{id}` | Soft delete (admin/preparer); the file is purged by the retention job (D-19) |

## 3. Bulk CSV

### CSV contract [ASSUMPTION → D-22]

```text
route_id,start_location,stop_1,stop_2,…,stop_13,end_location[,vehicle_type,travel_date,depart_time,manager_name]
```

- `route_id` is optional. The row number is used if it is absent.
- Stop columns are optional and read left to right. A gap (for example `stop_2` filled with `stop_1` empty) is a **row error**, not silently compacted.
- Column headers match case-insensitively, with aliases (`Starting Location`, `Stop 1`, `End Location`).
- UTF-8 (a BOM is tolerated). Max 5 MB / 2,000 rows [ASSUMPTION].

### `POST /bulk-jobs/validate` (multipart `file`) → `200`

This is a dry run: nothing is persisted except the uploaded file, which is kept for 24 h.

```json
{
  "upload_id": "u_…", "total_rows": 250, "valid_rows": 244, "invalid_rows": 6,
  "columns_detected": ["route_id","start_location","stop_1","stop_2","end_location"],
  "errors": [ { "row": 17, "route_id": "R-017", "field": "end_location", "issue": "missing" } ],
  "preview": [ { "row": 1, "route_id": "R-001", "start": "…", "stops": ["…"], "end": "…", "valid": true } ]
}
```

### `POST /bulk-jobs` → `202`

Body: `{ "upload_id": "u_…", "llm_mode": "realtime" | "batch" }`. Uploading the file directly as multipart `file` is also accepted, and runs the same validation.

```json
{ "job_id": "b_…", "total_routes": 250, "valid_routes": 244, "invalid_routes": 6, "status": "queued" }
```

Invalid rows are recorded as `bulk_job_items` with `status=invalid` and appear in the manifest. They are never processed.

### `GET /bulk-jobs/{id}`

```json
{
  "job_id": "b_…", "status": "processing",
  "total": 250, "processed": 180, "successful": 174, "failed": 6, "invalid": 6, "percentage": 72,
  "started_at": "…", "elapsed_seconds": 812, "eta_seconds": 300,
  "downloads": { "zip": null, "manifest_csv": null, "manifest_xlsx": null },
  "usage": { "input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 0, "estimated_cost_usd": 0.0 }
}
```

`percentage` = processed / valid × 100. Invalid rows are excluded from the denominator and reported separately.

### Other bulk endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/bulk-jobs/{id}/items?status=failed&page=` | Per-row status, error code/message, document id |
| POST | `/bulk-jobs/{id}/retry-failed` | Re-enqueue failed items only |
| POST | `/bulk-jobs/{id}/cancel` | Stop queued items; in-flight items finish |
| GET | `/bulk-jobs/{id}/download` | `JMP_Batch_<job_id>.zip` (successful PDFs + `manifest.csv` + `manifest.xlsx` + `summary.json`) |
| GET | `/bulk-jobs/{id}/manifest?format=csv\|xlsx` | Manifest only |

**Manifest columns:**

- row_number
- route_id
- status (`SUCCESS` / `FAILED` / `INVALID`)
- document_code
- pdf_filename
- error_code
- error_message
- generated_at
- risk_level
- journey_score

**`summary.json` fields:**

- total / successful / failed / invalid rows
- failure reasons grouped by `error_code`
- generated document IDs
- processing time
- token and cost totals

## 4. Reference and admin

| Method | Path | Purpose |
|---|---|---|
| GET | `/hazards?version=` | Active hazard library (read-only) |
| GET | `/hazard-library/versions` | Library versions |
| GET | `/settings` | Effective non-secret config: provider, model, top-N, versions |
| GET | `/usage/summary?from=&to=` | Tokens, cache-hit ratio, cost per document, by day and model |
| GET | `/health` · `/ready` | Liveness / readiness (DB, Redis, storage, Chromium) |

## 5. Error codes (stable, used in the manifest)

| Code | Meaning | Retried automatically? |
|---|---|---|
| `VALIDATION_ERROR` | Bad input | No |
| `GEOCODE_NOT_FOUND` | Location returned no result | No |
| `GEOCODE_AMBIGUOUS` | Low-confidence or multiple candidates (D-17) | No |
| `ROUTE_NOT_FOUND` | Provider could not route between points | No |
| `PROVIDER_UNAVAILABLE` | Routing/feature provider 5xx, timeout or rate limit | Yes, with backoff |
| `LLM_UNAVAILABLE` | Anthropic 429/5xx/overloaded | Yes, with backoff |
| `LLM_INVALID_OUTPUT` | Schema or content validation failed after N attempts | Up to N, then fail |
| `LLM_REFUSAL` | `stop_reason = refusal` after fallback | No |
| `RENDER_OVERFLOW` | Content exceeds the page envelope (D-18) | No |
| `PDF_RENDER_FAILED` | Chromium failure | Yes |
| `INTERNAL_ERROR` | Anything else | Yes (once) |

---

## 6. As-built notes

Implemented as designed, with these additions and clarifications:

* **Readiness** is `GET /health/ready` (under the same `/api/v1` prefix); it checks database, storage,
  Redis (when `JOB_EXECUTION=celery`) and that an active hazard library exists. `/health` is the liveness
  probe and is the only route exempt from auth.
* **Auth** (D-15, interim): when `API_AUTH_TOKEN` is set, every `/api/v1` route except `/health` requires
  `Authorization: Bearer <token>`, compared in constant time.
* **Added endpoints**
  * `GET /bulk-jobs/sample.csv` — the CSV template the UI offers for download.
  * `GET /bulk-jobs/{id}/manifest?format=csv|xlsx|json` — manifests individually (also inside the ZIP).
  * `GET /usage/summary` — actual recorded tokens, cache-hit ratio and USD/INR cost, with cost per
    JMP / 100 / 1,000.
  * `GET /usage/cost-model` — projected cost for Opus 5 / Sonnet 5 / Haiku 4.5 at standard and batch tiers.
  * `GET /settings` — effective non-secret configuration and all six version stamps.
  * `GET /hazards`, `GET /hazard-library/versions` — the library as ingested, with each hazard's detection
    method.
* **Bulk creation** accepts either `upload_id` (from `/bulk-jobs/validate`) or a direct multipart `file`,
  plus an optional `llm_mode` of `realtime` or `batch`.
* **Idempotency** is honoured on both `POST /journeys/generate` (24-hour window) and `POST /bulk-jobs`
  (same key + same CSV returns the original job; same key + different CSV is a `409`).
* **Progress** (`GET /bulk-jobs/{id}`) additionally returns `elapsed_seconds`, `eta_seconds`, per-mode
  `llm_mode`, download links and the job's token/cost totals. `percentage` is computed over *valid* rows;
  invalid rows are reported separately and still appear in the manifest.
* **Manifest columns** are as designed plus `decision`, and `summary.json` adds token/cost totals and the
  processing time.
