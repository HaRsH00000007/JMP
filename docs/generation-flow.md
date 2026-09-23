# Generation Flow

Covers the per-journey pipeline, the Claude integration (prompt caching, structured output, cost), PDF generation, and bulk processing.

## 1. Per-journey pipeline

Each stage is a Celery task. It reads its inputs from Postgres, writes its output back to Postgres, and advances `generation_jobs.stage`. A failure stops the chain and records `error_code`. `POST /jobs/{id}/retry` resumes from the failed stage.

```text
 1 validate      pure checks, normalize strings, input_hash                      (api, sync)
 2 geocode       Geocoder per stop → lat/lng, admin area, place types, confidence  (D-17 on ambiguity)
 3 route         RouteProvider(waypoints in order, depart_at, alternatives=true)   → legs, polyline, steps
 4 features      RoadFeatureProvider + ElevationProvider along the polyline        → located features
 5 analyse       segments, road-type split, urban/highway/HCV exposure %, complexity, distance/time ranges
 6 hazards       HazardEngine: 25 detectors → applicable set + evidence + locations → ranking
 7 scoring       ScoringEngine: 6 dimensions × weights → score; fatigue; decision   (D-03/04/05)
 8 emergency     static verified numbers + PROVIDED inputs + PlacesProvider (REQUIRES VERIFICATION)
 9 narrative     Claude → NarrativeV1 (validated)                                  (§2)
10 assemble      ReportAssembler: deterministic facts + narrative → ReportModel (full "JMP JSON")
11 render        Jinja2 → HTML (+ SVG) → overflow check                            (§3)
12 pdf           Playwright → PDF → metadata → storage → jmp_documents row
```

### Data classification

Classification is attached at assembly (step 10). **Every field in `ReportModel` carries a classification tag.**

| Tag | Examples | Who produces it |
|---|---|---|
| `VERIFIED` / `PROVIDED` | 112, 108; library S/P/RPN/controls; user-supplied manager, hospital, contacts; stop names as entered | Source files, config, user input |
| `ASSESSMENT` | Distance/time, road split, exposures, hazard applicability, bands, ranks, scores, decision; all narrative prose | Providers, rule engine, LLM (prose only) |
| `REQUIRES_OPERATIONAL_VERIFICATION` | Places-API hospitals/police/fuel; unmanned status of a crossing; construction; alternative routes; any `VERIFY`-evidence hazard | Rule engine flags. The LLM may *add* items to this list but can never remove any |

The final JMP JSON has the shape in the requirements (`journey_id`, `route_name`, `journey_score`, `risk_level`, `relevant_hazards`, `route_segments`, …). **But only the prose fields inside it come from Claude.** `journey_score`, `risk_level`, `fatigue_risk`, `route_complexity`, hazards, segments and verification items are computed in steps 5–8. The model never sees them as fields it can set.

## 2. Claude integration

### 2.1 Model and request shape

| Setting | Value |
|---|---|
| SDK | `anthropic` (Python), `client.messages.parse(...)` with a Pydantic `NarrativeV1` → `output_config.format` JSON-schema structured output |
| Model | `claude-opus-5` by default, via `ANTHROPIC_MODEL`. → [DECISION D-13] cost vs quality: `claude-sonnet-5` is ~60% cheaper per token; choose only after comparing both on the reference fixtures |
| Thinking | Adaptive (the default on Opus 5). `output_config.effort` is configurable, default `high`; `medium` is a measured cost lever |
| Refusals | Server-side `fallbacks: "default"` (beta `server-side-fallback-2026-07-01`). A remaining `stop_reason == "refusal"` → `LLM_REFUSAL` |
| `max_tokens` | 16,000 (non-streaming) |
| Client | `max_retries=2` in the SDK for 429/5xx/connection errors. On top of that, Celery retries with exponential backoff and jitter for `LLM_UNAVAILABLE` |

### 2.2 Prompt layout for caching

Caching is a **byte-exact prefix match**. The static prefix must be identical for every journey. It must contain no timestamps, no IDs, and no unsorted dicts.

```text
system: [
  block 1  STATIC — role & safety constraints (never fabricate numbers/contacts/S/P/RPN; prose only;
           use only supplied facts; UK/Indian English; terminology list)
  block 2  STATIC — data-classification rules, report section definitions, word limits, tone
  block 3  STATIC — 25-hazard library (code, name, S, P, RPN, band, 2W/4W control bullets), serialized
           deterministically (sorted, fixed JSON separators) + risk-matrix + likelihood/consequence text
  block 4  STATIC — field-by-field guidance for NarrativeV1 + 1 compact worked example (Pyraganda-shaped,
           but not the reference text verbatim)                  ← cache_control {"type":"ephemeral", ttl}
]
output_config.format: NarrativeV1 JSON schema (fixed per schema_version)
messages: [
  user  VARIABLE — "facts" JSON only (see 2.3)
]
```

- The prefix is estimated at **6–9 K tokens**. That is well above the 512-token minimum cacheable prefix for Opus 5 (1,024 for Sonnet 5), so it will cache.
- **Cache pricing:** writes cost 1.25× base (5-min TTL) or 2× (1-h TTL); reads cost 0.1×.
- **TTL is configurable** (`ANTHROPIC_CACHE_TTL=5m|1h`):
  - Bulk realtime runs keep a 5-min cache warm on their own.
  - Sporadic individual traffic only benefits from `1h` when there are ≥3 requests per hour. This is decided from measured hit rates, not guessed.
- **Guard:** a unit test builds the static prefix twice, in separate processes, and asserts the bytes are identical. A second test asserts that `prompt_version` changes whenever the prefix hash changes.

### 2.3 Variable content (the only per-journey input)

This is compact, deterministic JSON:

- stops in order (display name, admin area, place type)
- totals and ranges
- segments (id, from→to, road class, km)
- road-type split and exposure levels
- candidate hazards: **codes only**, plus evidence and location labels
- alternatives (id, delta km/min, via-roads)
- scores and decision (so the prose can explain them, not change them)
- vehicle type
- travel month/time window

The full library and the route geometry are **never** sent. The typical size is 1.5–3 K tokens.

### 2.4 Output schema `NarrativeV1`

Prose only. Every item is keyed to IDs the application supplied.

```text
schema_version            "1.0"
route_name                ≤ 60 chars          (fallback: deterministic "Start → End")
journey_type_label        ≤ 60 chars
executive_summary         ≤ 110 words
journey_assessment        ≤ 130 words
decision_rationale        ≤ 70 words ; decision_note_short ≤ 45 words (page 1)
hazard_notes[]            { hazard_code ∈ candidates, route_context ≤ 15 words,
                            card_location ≤ 6 words, card_description ≤ 18 words, qualifier ≤ 8 words }
segment_notes[]           { segment_id ∈ segments, key_exposure ≤ 10 words }
highlight_captions        { traffic, hcv, railway, pedestrian, urban_density, environmental } ≤ 5 words each
alternative_notes[]       { alt_id ∈ alternatives, advantage, limitation, use_when } ≤ 14 words each
route_recommendation      ≤ 60 words
dimension_remarks[]       { dimension ∈ 6 fixed ids, remark ≤ 14 words }
fatigue_notes[]           { fatigue_segment_id, basis ≤ 14 words } ; fatigue_management ≤ 40 words
driver_readiness          { before_departure, during_journey, at_stops, return } each 1–3 items ≤ 14 words
priority_recommendations  same four keys, exactly 3 items each ≤ 16 words
additional_verification[] ≤ 6 × { item ≤ 14 words, reason ≤ 14 words }
```

### 2.5 Validation and retry

The API strips unsupported constraints such as `maxLength`. The Python SDK re-validates them client-side, and our Pydantic validators enforce the rest.

1. **Schema:** `messages.parse` raises or returns an invalid result → `invalid_json` / `schema_error`.
2. **Referential checks:**
   - every candidate hazard, segment, alternative and dimension is covered exactly once
   - no unknown IDs appear
3. **Anti-fabrication checks:**
   - A **phone/URL regex** rejects any contact-like string.
   - **Numeric grounding:** every number in the prose must appear in the facts payload (with tolerant formatting: "83–93", "~3 hrs"). Otherwise the output is rejected.
   - A library hazard name that is *not* among the candidates, appearing in hazard-context fields, is rejected.
4. **Retry path:** on a validation failure, re-ask **once or twice** (`LLM_MAX_ATTEMPTS=3`). The previous output and the specific validation errors are appended as a new user turn after the unchanged prefix, so the cache still hits. After the final attempt the job fails with `LLM_INVALID_OUTPUT`. Nothing partially valid is ever rendered.
5. **`stop_reason` handling:** checked before reading content (`max_tokens` → retry with a higher cap once; `refusal` → fail).

### 2.6 Usage and cost logging

One `generation_usage` row is written per API call (including retries). The same fields are emitted as a structured log line:

- `model`, `request_id`, `attempt`, `duration_ms`, `stop_reason`, `outcome`
- `input_tokens` (uncached), `cache_creation_input_tokens`, `cache_read_input_tokens`, `output_tokens`
- `usage.cache_creation.ephemeral_5m_input_tokens` / `ephemeral_1h_input_tokens` (split by TTL)
- `estimated_cost_usd` = Σ tokens × rate from `app/llm/pricing.py`. The rates are versioned, and the Batch tier gets its 50% factor
- `cache_hit_ratio` = cache_read / (cache_read + cache_creation + input)

The API key never enters logs. The redaction filter also covers exception traces from the SDK.

**Cost estimate per JMP** [ESTIMATE, to be measured in Phase 4], on Opus 5 at $5 in / $25 out per MTok:

- ~8 K cached prefix read ≈ $0.004
- ~2.5 K uncached input ≈ $0.013
- ~4–5 K output (including thinking) ≈ $0.10–0.13

That gives about **$0.12–0.15 per JMP in realtime, or about $0.06–0.08 through the Batch API.** Output dominates the cost, which is why the schema is prose-only with hard word limits, and why numbers are never generated.

## 3. PDF generation

```text
ReportModel ──▶ Jinja2 (templates/jmp/v1/report.html.j2, StrictUndefined)
            ──▶ SVG builders (route map, hazard pointer spine, alt-route minis, score gauge) in Python
            ──▶ HTML string (fonts + CSS inlined; no network)
            ──▶ Playwright Chromium: page.set_content → emulate_media("print")
                → overflow check (JS: every .page scrollHeight ≤ clientHeight)
                → page.pdf(format="A4", print_background=True, prefer_css_page_size=True)
            ──▶ pypdf: set Title / Subject / Keywords (all version stamps) ──▶ storage
```

- **Single-document template.** It holds 8 `<section class="page">` blocks, each exactly A4 (`@page { size: A4; margin: 0 }`, `.page { width: 210mm; height: 297mm; overflow: hidden; break-after: page }`). Headers, footers and "Page N of 8" are HTML inside each page, not Chromium header templates, so the styling matches the master exactly.
- **Fixed structure.** Section order, titles and static text are template constants. Only `ReportModel` values vary. `StrictUndefined` turns any missing field into a render error.
- **SVG maps** are drawn from real geometry:
  - Web-Mercator projection, then Douglas–Peucker simplification, then fit to the viewBox
  - numbered nodes, and hazard markers at their real positions
  - the "Not to scale / GPS coordinates not fabricated" wording kept
  - an OSM attribution line
  - a *schematic* mode (evenly spaced nodes) for routes whose geometry reads poorly at A4 size
- **Chromium lifecycle.** The browser is long-lived, one per render-worker process, with a fresh context per job. Render-queue concurrency is 2–4 per container. The browser restarts after N jobs or on crash.
- **Determinism.** The same `ReportModel` + `template_version` produces an identical HTML snapshot. The HTML is stored next to the PDF for audit and diffing.
- **Visual regression.** The Pyraganda reference fixture is rendered, rasterised, and compared page by page with a stored baseline (pixel-diff threshold). Human review against the master PDF happens in Phase 5.

## 4. Bulk processing

```text
upload → /bulk-jobs/validate (sync parse: header mapping, per-row checks, preview)
       → /bulk-jobs  → bulk_jobs row + bulk_job_items (valid → queued, invalid → invalid)
       → bulk.fanout task
            realtime mode:  run item #1 alone (warms the prompt cache), then enqueue the rest as
                            independent pipelines; LLM calls pass a Redis token-bucket limiter
                            sized to the account's RPM / input-TPM / output-TPM
            batch mode:     run stages 1–8 for all items in parallel → one Message Batches submission
                            (custom_id = item id; same cached static prefix, ttl 1h) → poll every 60 s →
                            validate each result (invalid → realtime retry) → stages 10–12
       → each item ends in succeeded | failed  (atomic counters on bulk_jobs; row-level error_code)
       → when processed == valid_rows: bulk.finalize (single run, guarded by a row lock + status transition)
            → JMP_Batch_<job_id>.zip streamed to storage (ZIP_STORED; PDFs are already compressed)
                 /pdfs/<route_id>_<document_code>.pdf
                 manifest.csv · manifest.xlsx (formula-injection safe) · summary.json
            → status completed | completed_with_errors
```

**Isolation.** Each item is its own task chain with its own retries. An exception in one row never touches its siblings: no shared transaction, no `group` that fails as a whole.

**Resilience:**

- Celery `acks_late` with a visibility timeout, so a crashed worker's item is redelivered.
- Tasks are idempotent by item id. A completed stage is skipped on redelivery.
- A reaper task requeues items stuck in `processing` past a timeout.
- `retry-failed` re-runs only the failed items.

**Throughput.** The routing provider's QPS and Anthropic rate limits cap throughput, not CPU. At a conservative 10 concurrent LLM calls and roughly 25–40 s per journey end-to-end, 250 routes take about 12–18 min in realtime mode. [ESTIMATE, to be measured in Phase 8]

**Dedup.** Rows with identical normalized inputs reuse `route_cache`. They still get separate documents, because the IDs differ.

**Frontend.** The browser only polls `GET /bulk-jobs/{id}` (every 3 s while the page is open). Closing it has no effect on processing, and results appear on the Jobs and Reports pages.

## 5. Testing map (Phase 9, built incrementally)

| Area | Tests |
|---|---|
| CSV validation | header aliases, gaps in stop columns, empty rows, BOM, oversize, formula-like cells |
| Journey validation | limits, empty/duplicate stops, round trip, **stop order preserved end-to-end** |
| Hazard matching | one fixture per detector (feature present/absent), evidence levels, VERIFY never shown as fact |
| Risk calculation | band per D-01 for all 25 hazards; ranking tie-breaks; score arithmetic (reference = 67.25 → 67) |
| Library ingestion | all 25 S/P/RPN values asserted against the XLSX; RPN mismatch fails ingestion |
| Claude schema | valid/invalid recorded responses; unknown hazard code; phone number; ungrounded number; missing coverage |
| Retry handling | 429 → backoff; invalid → re-ask with errors; exhausted → `LLM_INVALID_OUTPUT`; refusal path |
| Caching | static prefix byte-stable; prompt_version bump enforced; live smoke test `cache_read_input_tokens > 0` |
| PDF | page count 8, A4 size, fonts embedded, no overflow on 2-stop and 15-stop fixtures, visual baseline |
| Bulk | 250-row synthetic CSV with seeded geocode/LLM/render failures → counters, manifest, ZIP contents |
| Failed-route isolation | one poisoned row among good rows → siblings succeed |
| ZIP | entries match succeeded items; manifest row count = total rows |

The **reference case** is the Pyraganda loop, stored as recorded provider responses plus the expected deterministic outputs. It is test data only. No production code path branches on it.

---

## 6. As-built notes

**Stages.** The twelve logical steps run as **three persisted Celery stages** — `analyse` (2–8),
`narrate` (9) and `render` (10–12) — one per queue, each writing its output to the database before the
next begins. `generation_jobs.stage` still records the fine-grained step for the UI. A retry therefore
resumes at the failed stage: a narrative failure never re-runs routing, and a render failure never
re-calls Claude.

**NarrativeV1 (schema v1.0)** is slightly smaller than sketched above: `route_name` and
`journey_type_label` were removed because both are derivable from the geocoded stops, so they are computed
deterministically. Everything else is as designed.

**Validation in practice.** During the first Docker acceptance run the validator rejected a narrative that
said *"Scored 100 from the supplied route inputs"* for a dimension genuinely scored 100, because 100 is
also a police helpline number. The rule was refined rather than relaxed: an emergency number is rejected
when the digits are **not** a supplied fact, and always when they appear in a calling context ("dial 100").
Both cases are covered by tests.

**Measured prompt sizes** (see [cost-model.md](cost-model.md)): static prefix ≈ 7,500 tokens
(27,096 chars, of which the hazard library is 12,391), variable facts 1,700–2,300 tokens. A test asserts
the prefix is byte-identical across different journeys and carries exactly one `cache_control` breakpoint.

**Rendering.** All PDF rendering in a process goes through a single dedicated renderer thread owning one
Chromium instance. This is required because Playwright's sync API cannot run on a thread with a running
asyncio loop, and it avoids one browser per request thread. Concurrency comes from more worker processes.

**Bulk.** Implemented as designed, including the realtime cache warm-up (first row alone, then fan-out),
guarded counters (`counted` flag + conditional status transition so redelivery cannot double-count), and
the batch path with per-row realtime fallback. Measured: **120 journeys → 48 s**, 113 PDFs, 2 isolated
failures, ZIP 39.9 MB with CSV/XLSX/JSON manifests.

**Retention.** `python -m app.cli.maintenance purge` implements D-19: files of soft-deleted or expired
documents are removed while the audit row (versions, hashes, token usage) is kept; unused CSV uploads are
removed after 24 h.
