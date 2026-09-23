# Decision Register

These are open business or technical decisions. Where a decision isn't made before its phase starts, the **proposed default** is implemented behind configuration, so it can be changed without code changes. The Priority column says which phase is blocked.

| ID | Decision | Why it's open | Proposed default | Blocks |
|---|---|---|---|---|
| **D-01** | Risk band method: **severity-only** (PDF: 5 = HIGH, 4 = MED, ≤3 = LOW) **or** the **Danone 5×5 matrix** (Excel) | They disagree. Under the matrix, 19/25 hazards are red, and D4 hazards (e.g. Waterlogging) are red but MEDIUM in the PDF | Store both. Display severity-only (matches the master PDF), with matrix zone available. **Ask Danone EHS** | Phase 3 |
| **D-02** | Labels for matrix zones (Red/Yellow/Green → HIGH/MEDIUM/LOW?). Does a **CRITICAL** level exist? | The PDF says "No CRITICAL hazard identified", but no source defines CRITICAL | R/Y/G = HIGH/MED/LOW; no CRITICAL | Phase 3 |
| **D-03** | Scoring rubric for the 6 journey-score dimensions | The weights are in the PDF; how each 0–100 score is derived is not | Deterministic rubric in `scoring.yaml`, drafted from route metrics, calibrated on the reference case, **approved by EHS** | Phase 3 |
| **D-04** | Journey-decision rules (APPROVED / APPROVED WITH CONTROLS / REQUIRES REVIEW / NOT APPROVED) and thresholds | Only one example outcome exists | Deterministic: any unmitigable HIGH or score < 50 → REQUIRES REVIEW; any HIGH → WITH CONTROLS; else APPROVED. **The AI never gives final approval; sign-off stays human** | Phase 3 |
| **D-05** | Fatigue rules (continuous driving, total duration, night, stop count) | Not defined in the sources | e.g. continuous < 2 h LOW, 2–4 h MODERATE, > 4 h or any night driving HIGH; split at longest dwell/rest point | Phase 3 |
| **D-06** | Hazard library typos/duplicates: print verbatim or show a corrected display text? | "Must not modify supplied values" vs a professional-looking report | Store verbatim. Add an EHS-approved `display` text version; S/P/RPN are never touched | Phase 2 |
| **D-07** | Condensed control text (page 1, page 5, page 7) | The PDF paraphrases the library ("≤20 km/h" vs "10 to 20 Km/h") | One **curated, EHS-approved short control per hazard per vehicle type**, stored in the library; not LLM-written per report | Phase 5 |
| **D-08** | Additional inputs: vehicle type (2W/4W), travel date, departure time, and the Excel `Header` fields (manager, emergency contact, nearest hospital, nearest police) | They drive controls, seasonal/night hazards, and the PROVIDED rows of the emergency directory | Vehicle type is required (default 4W); the others are optional; CSV has optional columns | Phase 2 |
| **D-09** | Routing / feature / elevation / places providers, plus the licensing of a derived SVG route map | Google ToS limits caching and non-Google map display | Mapbox (or Google, if the ToS review passes) for routing, OSM Overpass for road features; Mock until keys exist | Phase 3 |
| **D-10** | Hazard-pointer page: position, number of hazards, total page count | The source has a removed page 8 ("of 8" footers) | New page 6 → 8 pages total; default 5 pointers | Phase 5 |
| **D-11** | Alternatives page when the provider returns 0 or 1 alternatives | Page 4 assumes two | Show what exists; empty slots render a fixed "No viable alternative returned — verify locally" | Phase 5 |
| **D-12** | Journey ID format | Example: `DAN-JMP-WB-014` | `DAN-JMP-{state code}-{zero-padded per-state sequence}` | Phase 2 |
| **D-13** | Claude model and bulk LLM mode | Cost vs quality; batch is 50% cheaper but results can take hours | `claude-opus-5`; compare with `claude-sonnet-5` on fixtures before go-live. Bulk default `realtime`, with `batch` selectable | Phase 4 |
| **D-14** | Single client (Danone branding hard-wired) or multi-client branding | Affects the template and data model | Danone-only, with brand strings in a config file | Phase 5 |
| **D-15** | Authentication and roles; is sign-off digital or wet-ink? | Not specified | Email/password or company SSO (OIDC); roles preparer/reviewer/approver/admin; sign-off block left blank in v1 | Phase 7 |
| **D-16** | How to present distance/time as ranges | The PDF shows ranges; providers give one value | distance: provider km → [×1.0, ×1.1]; time: [provider duration, ×1.3 + dwell allowance]. Parameters in config | Phase 3 |
| **D-17** | Ambiguous or low-confidence geocodes | "Chandigarh City Center Zirakpur" may match several places | Fail the row with `GEOCODE_AMBIGUOUS` in bulk; in individual mode, return candidates so the user can pick | Phase 3 |
| **D-18** | Content longer than a page | Fixed structure vs variable routes | Hard caps in the schema and ranking (see pdf-analysis §7); `RENDER_OVERFLOW` fails the job, no silent truncation | Phase 5 |
| **D-19** | Storage location, retention period, data residency | Contains employee journey data | S3-compatible storage in an India region; keep 2 years; soft delete + purge job | Phase 6 |
| **D-20** | Emergency directory sources | Only 112 and 108 are verified | 112/108 static (VERIFIED); user inputs (PROVIDED); nearest hospital/police from Places API marked **REQUIRES VERIFICATION**; include the TPA hospital-network link from the Excel? | Phase 3 |
| **D-21** | Seasonal/regional windows (monsoon, fog, hilly/landslide regions) — who owns them? | Needed for hazards #12, #13, #14, #18 | `seasons.yaml` per state, drafted from IMD norms, **owned by EHS** | Phase 3 |
| **D-22** | CSV format limits | Not specified | See api-design §3: `route_id,start_location,stop_1..stop_13,end_location` + optional columns; 2,000 rows / 5 MB | Phase 8 |

---

## Implementation status

Every decision above is implemented using its proposed default, behind configuration. This table says
where each one lives, so changing it later is a config edit (and a version bump), not a code change.

| ID | Implemented as | Where to change it | Still needs EHS sign-off |
|---|---|---|---|
| D-01 | Both bands stored; **severity band displayed**, matrix zone shown as an extra column on page 5 | `config/risk_matrix.yaml` → `display_band_method` | **Yes** — which method is authoritative |
| D-02 | Red/Yellow/Green → HIGH/MEDIUM/LOW; no CRITICAL level | `config/risk_matrix.yaml` → `zone_labels` | **Yes** — confirm labels; confirm CRITICAL does not exist |
| D-03 | Six dimensions, weights from the master PDF, linear scoring formulas | `config/scoring.yaml` → `dimensions` | **Yes** — the rubric (weights are from the reference report, not policy) |
| D-04 | `REQUIRES REVIEW` if score < 50; `APPROVED WITH CONTROLS` if any HIGH hazard or MODERATE+ risk; else `APPROVED`. The AI never approves — sign-off block stays blank | `config/scoring.yaml` → `decision`, `risk_level` | **Yes** — thresholds |
| D-05 | Stints split at the farthest point (round trip) or mid-duration; levels by continuous driving hours; later stint bumped over 2.5 h; night → HIGH | `config/scoring.yaml` → `fatigue` | **Yes** — thresholds |
| D-06 | Library text printed **verbatim** (typos included). Corrections proposed but unapplied | `config/hazard_display.yaml` (`approved: true`) + `HAZARD_DISPLAY_TEXT` | **Yes** — approve corrected wording |
| D-07 | Report prints the **first two verbatim bullets**; proposed one-line controls sit unapproved | `config/hazard_display.yaml` → `short_controls`, `approved` | **Yes** — approve the short controls |
| D-08 | Vehicle type (default 4W, stated in the report when assumed), travel date, departure time, manager, emergency contact, nearest hospital/police — all optional API and CSV fields | API/CSV schema | No |
| D-09 | Mock (default), free OSM profile (verified live), Google/Mapbox (written, untested — no keys) | `.env` provider variables | **Yes** — provider choice and the Google licensing question |
| D-10 | Hazard pointer page is **page 6 of 8**; 5 pointers by default (3–8) | `HAZARD_POINTER_COUNT`, `config/hazard_rules.yaml` → `report_caps` | No |
| D-11 | Alternatives come from the provider (whole route for 2 points, longest legs otherwise); missing slots print "No viable alternative was returned … verify locally" | `services/route_service.py` | No |
| D-12 | `DAN-JMP-{STATE}-{NNN}`, per-state sequence in `journey_code_sequences` | `config/seasons.yaml` state codes; `emergency_static.yaml` → `brand.id_prefix` | No |
| D-13 | `claude-opus-5`, effort `high`, 5-minute cache TTL, refusal fallbacks on; bulk default realtime with batch available | `.env` `ANTHROPIC_*`, `BULK_LLM_MODE` | **Yes** — Opus 5 vs Sonnet 5 (see cost-model.md) |
| D-14 | Danone-only; brand strings in config | `config/emergency_static.yaml` → `brand` | No |
| D-15 | Static bearer token (`API_AUTH_TOKEN`); `users` table exists but unused; **sign-off block removed from the report in template v1.1 at Danone's request** | `.env`, `app/main.py` middleware | **Yes** — SSO, roles, digital sign-off |
| D-16 | Distance ×1.0–1.1, duration ×1.0–1.3, dwell excluded | `config/hazard_rules.yaml` → `ranges` | Optional |
| D-17 | Low-confidence or far-apart candidates → `GEOCODE_AMBIGUOUS` with candidates; bulk rows fail individually | `services/route_service.py`, provider confidence | No |
| D-18 | Hard caps in ranking + schema, and a browser-measured overflow check that **fails** the job (`RENDER_OVERFLOW`) rather than clipping | `config/hazard_rules.yaml` → `report_caps` | No |
| D-19 | Soft delete + `python -m app.cli.maintenance purge` (default 730 days); audit row kept, files removed; S3 backend with SSE for residency | `.env` storage vars, purge schedule | **Yes** — retention period and hosting region |
| D-20 | 112/108 VERIFIED; supplied values PROVIDED; Places results REQUIRES VERIFICATION (names only, never numbers); TPA hospital-network link printed from the library | `config/emergency_static.yaml` | Optional — whether to print the TPA link |
| D-21 | Per-state monsoon/fog windows and hilly flags, drafted from general climatology | `config/seasons.yaml` | **Yes** — EHS owns these windows |
| D-22 | `route_id,start_location,stop_1..13,end_location` + optional columns, header aliases, 2,000 rows / 5 MB | `.env` `BULK_MAX_ROWS`, `services/bulk_csv.py` aliases | No |

**Summary: 12 of the 22 need a Danone EHS decision before operational use** — D-01, D-02, D-03, D-04,
D-05, D-06, D-07, D-09, D-13, D-15, D-19 and D-21 (the risk method, scoring/decision/fatigue rules,
hazard wording, provider and model choice, authentication, retention and the seasonal windows). Until
then the system runs on the documented defaults and states its assumptions inside every report.
