# Cost model

Claude is the only per-document cost. Everything else — routing, hazard matching, scoring, rendering — is
deterministic code. This document records the **measured** token profile of the implementation and what it
implies per JMP, per 100 and per 1,000.

## 1. Measured token profile

Measured from the running system (`backend/app/llm/static_prefix.py`, `facts.py`); token counts are
estimates from character counts, since exact counts need a live `count_tokens` call with an API key.

| Part of the request | Size | Cached? |
|---|---|---|
| Block 1 — role & safety constraints | 2,462 chars | ✅ |
| Block 2 — classification rules, sections, style | 4,255 chars | ✅ |
| Block 3 — **25-hazard library + risk matrix** (verbatim) | 12,391 chars | ✅ |
| Block 4 — field guidance + output JSON schema | 7,988 chars | ✅ |
| **Static prefix total** | **27,096 chars ≈ 7,500 tokens** | ✅ one `cache_control` breakpoint |
| Variable facts (4-point journey) | 5,518 chars ≈ 1,700 tokens | ❌ |
| Variable facts (13-point journey) | 7,490 chars ≈ 2,300 tokens | ❌ |
| Output narrative | ≈ 2,000 tokens of JSON, plus thinking tokens | ❌ |

The library never travels as uncached input: it is block 3 of the cached prefix, and journeys reference
hazards by code (`HZ-08`). Route geometry, provider payloads and the full hazard controls are never sent.

Planning figure used below: **7,500 cached + 2,500 uncached input + 4,500 output** (output includes
adaptive thinking).

## 2. Cost per JMP

Anthropic list prices (USD per million tokens) as of the pricing table in `app/llm/pricing.py`
(`PRICING_VERSION = 2026-09`); cache writes cost 1.25× input (5-minute TTL) or 2× (1-hour), cache reads
0.1×, and Message Batches halves everything. `USD_TO_INR=88.0` is configurable.

| Model | Tier | Per JMP | Per 100 | Per 1,000 | Per 1,000 (INR) |
|---|---|---|---|---|---|
| **claude-opus-5** (default) | standard | **$0.129** | $12.88 | $128.75 | ₹11,330 |
| claude-opus-5 | batch | $0.064 | $6.44 | $64.38 | ₹5,665 |
| claude-sonnet-5 | standard | $0.052 | $5.15 | $51.50 | ₹4,532 |
| claude-sonnet-5 | batch | $0.026 | $2.58 | $25.75 | ₹2,266 |
| claude-haiku-4-5 | standard | $0.026 | $2.58 | $25.75 | ₹2,266 |
| claude-haiku-4-5 | batch | $0.013 | $1.29 | $12.88 | ₹1,133 |

A cold cache costs $0.172 per JMP on Opus 5 versus $0.129 warm — the prefix cache saves about 25% per
document, and more on shorter journeys where the prefix dominates.

Live figures for both tables are available at `GET /api/v1/usage/cost-model` (projection) and
`GET /api/v1/usage/summary` (actual, from recorded usage), and on the Settings and Dashboard pages.

## 3. Where the cost is, and what was done about it

**Output tokens dominate** (~87% of the cost on Opus 5). The levers applied:

1. **Prompt caching** — the ~7,500-token prefix is byte-stable and marked with a single `cache_control`
   breakpoint. A test asserts two different journeys produce an identical `system` block, and
   `cache_read_input_tokens` is recorded per call so the hit rate is measurable, not assumed.
2. **Prose-only schema with hard word limits** — Claude writes no numbers, tables or layout. Every field
   has a word cap enforced client-side (the API strips `maxLength`).
3. **Nothing deterministic is asked of Claude** — distances, durations, stop counts, road-type splits,
   hazard applicability, bands, ranks, all six dimension scores, the total, risk level, decision and
   fatigue levels are computed in code.
4. **Minimal variable payload** — no geometry, no provider responses, no library text; hazards by code.
5. **Batch tier for bulk** (`BULK_LLM_MODE=batch`) — 50% off, with a per-row realtime fallback if a batch
   result is invalid or errored.
6. **Cache warm-up in bulk** — the first row of a realtime batch runs alone to write the cache before the
   remaining rows are released, so they all read it instead of racing to write it.
7. **Retry only when necessary** — retries reuse the cached prefix and append only the validation errors.

## 4. Choosing a model and TTL

* **Model.** `claude-opus-5` is the default. Sonnet 5 is ~60% cheaper per token; whether its narrative
  quality is acceptable for a safety document is a judgement for EHS (D-13). Both are configured by
  `ANTHROPIC_MODEL`, and the deterministic half of the report is identical either way.
* **Cache TTL.** 5-minute (default) suits continuous traffic — a bulk run keeps the cache warm by itself.
  The 1-hour TTL costs 2× on writes and only pays off at roughly three or more requests per hour, so it
  suits sporadic individual use. Batch submissions always use the 1-hour TTL, since queued requests can be
  processed minutes apart.

## 5. Non-Claude costs

* **Routing/geocoding.** Free with the OSM profile (self-host for production volume). Google/Mapbox bill
  per request: roughly (stops + 1) geocodes and 1–3 route requests per journey; `route_cache` de-duplicates
  identical requests for 30 days.
* **Compute.** PDF rendering is ~1–5 s of CPU per document; the 120-journey demo batch completed in 48 s
  wall-clock on one laptop with four pipeline workers.
* **Storage.** ~350 KB per PDF (plus the HTML snapshot): about 35 MB per 100 documents.
