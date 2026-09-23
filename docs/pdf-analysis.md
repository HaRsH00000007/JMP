# PDF Analysis — Master Visual Reference

Source: `Danone_JMP_Pyraganda_Circular Finalized_removed.pdf`

Tags used throughout these docs:

- **[CONFIRMED]** — read directly from the source files
- **[ASSUMPTION]** — my inference; needs your confirmation before it becomes a rule
- **[DECISION D-xx]** — a business decision you need to make (register: `docs/decisions.md`)

---

## 1. Physical properties

| Property | Value | Tag |
|---|---|---|
| Page count | **7** | [CONFIRMED] |
| Footer page numbering | "Page N **of 8**" on pages 2–7 | [CONFIRMED] |
| Page size | 596 × 842 pt = **A4 portrait** (page 1 596.04 pt, others 595.56 pt) | [CONFIRMED] |
| Producer | iLovePDF (post-processed). Filename suffix `_removed` | [CONFIRMED] |
| Page 1 | Built separately (different width, no footer, 52 embedded images, Arial type) | [CONFIRMED] |
| Pages 2–7 | Built from a Word-style document (Calibri type, table grid) | [CONFIRMED] |

**Page 8 was removed.** The footers say "of 8" but only 7 pages exist, and the filename ends in `_removed`. We don't know what page 8 contained. The new Hazard Pointer page could take that slot and bring the total back to 8. → **[DECISION D-10]**

## 2. Typography

| Use | Source font | Production substitute | Tag |
|---|---|---|---|
| Page 1 headings/labels | Arial Bold 6–20 pt | Arimo / Liberation Sans (Arial-metric) | [CONFIRMED] font · [ASSUMPTION] substitute |
| Pages 2–7 body/tables | Calibri 6–12 pt, Calibri Bold for headers | **Carlito** (metric-compatible with Calibri, open licence) | [CONFIRMED] font · [ASSUMPTION] substitute |
| Icons | Emoji/symbol glyphs (MS Gothic, Segoe UI Symbol, MingLiU) | **Inline SVG icon set** | [CONFIRMED] |

Many icons in the master PDF render as empty boxes (☐ / ⍰) because a font was missing, for example in "☐ DISTANCE" and "☐ ACCIDENT". This is a defect in the source. We will not copy it. The template uses inline SVG icons, so glyphs cannot go missing.

Calibri is a Microsoft font and can't be redistributed in a Linux container. Carlito keeps the same text metrics, so page layout does not shift.

## 3. Colour palette (extracted from the PDF's drawing and text operators)

| Token | Hex | Where used | Tag |
|---|---|---|---|
| `--navy` | `#1A396A` | Section header bars, table header rows | [CONFIRMED] |
| `--navy-deep` | `#092445` / `#0A2545` | Page-1 title, journey-score card, emergency cards | [CONFIRMED] |
| `--ink` | `#1E2836` | Body text | [CONFIRMED] |
| `--muted` | `#6A717F` / `#63738A` | Captions, "Assessment basis" subtitles | [CONFIRMED] |
| `--blue` | `#1A5E9D` | Accent labels ("DANONE INDIA", card titles) | [CONFIRMED] |
| `--panel` | `#E9F0F9` / `#F4F8FC` | Dashboard tiles, recommendation box | [CONFIRMED] |
| `--row-alt` | `#F0F1F4` | Zebra table rows | [CONFIRMED] |
| `--rule` | `#C7D4E7` | Table/card borders (most frequent fill) | [CONFIRMED] |
| HIGH | text `#BF382A` / `#D03237`, bg `#F9DFDB` | Risk cells, HIGH pills | [CONFIRMED] |
| MEDIUM | text `#896C00`, bg `#FCF1D7`, pill `#E7A61F` | Risk cells, decision panel | [CONFIRMED] |
| LOW | text `#2D7C31`, bg `#E1F0E1` | Risk cells, LOW pills | [CONFIRMED] |
| Risk bar | `#D5E2BB` / `#E7A61F` / `#F0B08F` | Page-1 LOW/MEDIUM/HIGH band | [CONFIRMED] |

## 4. Page-by-page structure

Field source key: **D** = deterministic (application logic / provider data), **L** = LLM narrative, **S** = static template text, **P** = provided by user/client.

### Page 1 — Management Journey Snapshot (one-page executive dashboard)

| Block | Content | Source |
|---|---|---|
| Header left | "DANONE INDIA" / "JOURNEY MANAGEMENT PLAN" / "Management Journey Snapshot" / route name + region | S + D (route name, region) |
| Header right | Journey ID, Version + date, Region, "MANAGEMENT SUMMARY" pill | D |
| Route map card | "ROUTE MAP — 13-POINT CIRCULAR LOOP", schematic numbered route, start (green), waypoints (blue), rail-risk (red), legend, "Not to Scale" | D (SVG from geometry and hazard positions) |
| KPI tiles (2×2 + wide) | Distance (range), Journey time (range), Number of stops, Complexity, Journey Score bar (67/100) | D |
| Status line | "OVERALL JOURNEY RISK · FATIGUE: … · DECISION: …" plus LOW/MEDIUM/HIGH band | D |
| Route Exposures | **5 cards**: title, risk pill, location sub-title, 1–2-line description | D (hazard, band) + L (location phrase, description) |
| Risk → Required Control | 5-row table: key risk (colour-coded) → condensed control | D (hazard) + control text (see [DECISION D-07]) |
| Driver Readiness | 4 boxes: Before departure / During journey / Rest-at stops / Return | L (bounded bullets) + S (standard items) |
| Emergency Response | 3 dark cards: Accident / Breakdown / Medical sequences, plus 112 and 108 pills | S (fixed sequences) + [CONFIRMED] numbers |
| Final Journey Decision | Amber panel: decision text, donut gauge (score), "Risk: X \| Fatigue: Y", justification paragraph | D (decision, score) + L (paragraph) |

### Page 2 — Report header, Executive Summary, Dashboard, Timeline

| Block | Content | Source |
|---|---|---|
| Banner | Navy block: "DANONE INDIA / JOURNEY MANAGEMENT PLAN / Journey Risk Assessment & Journey Intelligence — {route}" | S + D |
| Identity table | Journey ID · Route · Region · Journey Type · Status · Version | D (journey type partly L) |
| EXECUTIVE SUMMARY | One paragraph, about 90 words | L |
| JOURNEY DASHBOARD | 8 tiles: Distance, Driving Time, Stops, Risk Level, Journey Score, Complexity, Fatigue Risk, Status, each with a sub-caption ("ASSESSMENT", "Subject to validation") | D |
| JOURNEY TIMELINE | Horizontal numbered chips 01…N. Start/end tinted green, institutional stop tinted red | D |
| Classification note | "Data classification used throughout this report: VERIFIED/PROVIDED · ASSESSMENT · REQUIRES OPERATIONAL VERIFICATION" | S |

### Page 3 — Route Intelligence & Primary Route Analysis

| Block | Content | Source |
|---|---|---|
| Route map | Large schematic with numbered nodes, legend, north arrow, road-name labels, rail-crossing markers, "Schematic … GPS coordinates not fabricated" | D (SVG) |
| Metrics table 1 | Total distance · Est. driving time · Road types (% split) · Route complexity | D |
| Metrics table 2 | Urban exposure % · Highway/expressway exposure % · Traffic exposure · HCV exposure | D (percentages) + L (short qualifiers) |
| ROUTE INTELLIGENCE HIGHLIGHTS | 6 tiles: Traffic, HCV Exposure, Railway, Pedestrian, Urban Density, Environmental | D (level) + L (≤4-word captions) |
| PRIMARY ROUTE BREAKDOWN | Segment · Road character · Key exposure · Risk (colour cell). 6 rows in the example | D (segments, road character, risk) + L (key exposure) |

### Page 4 — Alternative Route Analysis & Route Decision

| Block | Content | Source |
|---|---|---|
| Alternative A / B | Mini diagram each (primary solid, alternative dashed) plus Route / Distance-Time / Advantage / Limitation / Use-when | D (from provider alternatives) + L (advantage/limitation/use-when) |
| ROUTE COMPARISON | Parameter × {Primary, Alt A, Alt B}: Distance, Time, Traffic, Safety, Complexity, Recommendation | D + L |
| ROUTE RECOMMENDATION | Blue left-bordered box plus the fixed line "Alternative route selection requires live navigation verification — do not divert on the basis of this document alone." | L + S |

We need a rule for the case where the routing provider returns no alternatives. → **[DECISION D-11]**

### Page 5 — Route Risk Analysis & Assessment

| Block | Content | Source |
|---|---|---|
| Intro note | Says hazards come from the 25-Hazard library, values are reproduced exactly, and the vehicle type assumption | S + D (vehicle type) |
| Hazard table | Danone Hazard · Route Context · Sev · Prob · RPN · Risk* · Key Control (4W). **9 rows** in the example | D (hazard, S, P, RPN, band, control) + L (route context ≤ 15 words) |
| Footnote | "*Risk band is an indicative categorisation derived from Danone's supplied Severity value … (5=HIGH, 4=MEDIUM, ≤3=LOW) … official RPN calculation methodology was not supplied" | S — **conflicts with the Excel; see §6** |
| TOP ROUTE-SPECIFIC HAZARDS | Rank · Hazard (with route qualifier) · Risk. **7 rows** | D (rank) + L (qualifier) |

### Page 6 — Journey Assessment & Safety Readiness

| Block | Content | Source |
|---|---|---|
| Readiness score header | "Journey Readiness Score — Subject to Danone EHS Validation", score, fatigue trend | D |
| Scoring table | 6 dimensions, each with weight, score, contribution and remarks (see §5) | D (weights, scores, contribution) + L (remarks) |
| FATIGUE ASSESSMENT | Journey segment · Fatigue level · Basis, plus a "Fatigue management" sentence | D (segments, level) + L (basis) |
| Complexity tile | "JOURNEY COMPLEXITY MODERATE — 13-pt loop, 3 road types" | D |
| JOURNEY ASSESSMENT | One paragraph, about 110 words | L |
| PRIORITY RECOMMENDATIONS | 4 quadrants (Before departure / During / At stops / Return) with 3 bullets each | L (bounded) |

### Page 7 — Emergency Preparedness & Journey Decision

| Block | Content | Source |
|---|---|---|
| 4 protocol bars | Accident (red), Breakdown (amber), Medical (red), Severe weather/flooding (blue), each an arrow sequence | S (fixed protocols) |
| EMERGENCY DIRECTORY | Type · Name/Details · Location · **Status (VERIFIED / PROVIDED / REQUIRES VERIFICATION)** | D + P. **Never L** |
| Directory note | "112 and 108 are verified, pan-India operational numbers. Entries marked 'REQUIRES VERIFICATION' must be confirmed locally…" | S |
| KEY JOURNEY HAZARDS | Hazard · Risk · One-line mitigation (5 rows) | D + control text |
| Final decision panel | Amber box: "FINAL JOURNEY DECISION: …" plus a justification paragraph | D (decision) + L (paragraph) |
| ~~SIGN-OFF~~ | Prepared by / Reviewed by / Approved by in the master — **removed from our template in v1.1 at Danone's request** | — |
| Disclaimer | "This document is an AI-assisted Journey Management Plan prepared to support — not replace — formal route surveys…" | S |

### Headers and footers

- **Pages 3–7:** a full-width navy section bar with the page title on the left and italic "Page N" on the right. [CONFIRMED]
- **Pages 2–7:** a centred footer, "Danone India – Employee Health & Safety · Journey Management Plan · Confidential | Page **N** of 8", with N set larger and bold. [CONFIRMED]
- **Page 1:** no footer. [CONFIRMED]

## 5. Numbers in the example and how to reproduce them

| Item | Example | Derivation | Tag |
|---|---|---|---|
| Journey score | 67/100 | Σ(weight × score): 12.0 + 13.0 + 11.0 + 10.5 + 6.5 + 14.25 = 67.25 → 67 | [CONFIRMED] arithmetic |
| Weights | Route complexity 20%, Traffic & congestion 20%, HCV/Pedestrian/Rail exposure 20%, Driver fatigue 15%, Environmental 10%, Vehicle & emergency preparedness 15% | Printed in the PDF. Marked "Subject to Danone EHS Validation" | [CONFIRMED] in the reference report. **Not confirmed as Danone policy** |
| Dimension scores | 60, 65, 55, 70, 65, 95 | **The source contains no rubric** | **[DECISION D-03]** |
| Risk band per hazard | Sev 5 → HIGH, 4 → MEDIUM, ≤3 → LOW | PDF footnote | [CONFIRMED] in PDF, but **conflicts with the Excel matrix** → **[DECISION D-01]** |
| Distance/time as ranges | 83–93 km, 2h55m–3h45m | The rule for building a range is not stated | **[DECISION D-16]** |
| Decision | APPROVED WITH CONTROLS | No thresholds are stated | **[DECISION D-04]** |
| Fatigue | LOW–MODERATE outbound, MODERATE return | No rules are stated | **[DECISION D-05]** |

## 6. Inconsistencies inside the master PDF (do not replicate)

1. **Risk-band method vs Danone matrix.** The PDF bands risk by severity only and says that "RPN methodology was not supplied". The Excel *does* supply Danone's 5×5 likelihood × consequence matrix. Under that matrix, D4 (for example Waterlogging) is **red**, but the PDF shows it as MEDIUM. → [DECISION D-01]
2. **Stop count.** Page 1 shows "13 / 12 stops + return". Page 2 shows "12 + return / 13 waypoints". We need one counting convention. [ASSUMPTION]: waypoints = start + stops + end, and intermediate stops = `len(stops)`.
3. **Pedestrian.** The exposure level is **HIGH** (page 3 highlight), while the Pedestrian-Prone *hazard band* is **LOW** (sev 3). These are two different concepts, and the data model keeps them apart: `exposure_level` vs `hazard_risk_band`.
4. **"HCV / Expressway"** has no hazard of that name in the library. The PDF maps it to *National Highway* as the "closest library match". The hazard engine needs an explicit mapping table for this kind of analog, and the PDF should label it honestly (it already does: "National Highway analog").
5. **Control text is paraphrased.** "≤20 km/h" in the PDF vs "10 to 20 Km/h" in the library. → [DECISION D-07]
6. **The page-1 driver-readiness box lists 2-wheeler riding gear**, but the report assumes 4-wheeler travel. Controls must follow the vehicle type. → [DECISION D-08]
7. **Page-1 and page-3 maps differ** in layout and node placement. Both are schematic. The generated version will draw both from the same geometry.
8. **Missing icon glyphs.** See §2.

## 7. Content-length envelope (for the template contract)

The layout is fixed, but the content varies by route (2 stops vs 13, 3 hazards vs 9). The template must hold within these limits [ASSUMPTION — defaults, configurable]:

| Element | Min | Max (single page) |
|---|---|---|
| Waypoints (timeline chips / map nodes) | 2 | 15 (beyond that, the timeline wraps to 2 rows) |
| Route segments table | 1 | 8 |
| Hazard table rows (page 5) | 3 | 10 |
| Top hazards (page 5) | 3 | 7 |
| Route-exposure cards (page 1) | 3 | 5 |
| Hazard pointers (new page) | 3 | 5 (default 5, configurable) |
| Executive summary | — | 110 words |
| Journey assessment | — | 130 words |

What happens when content exceeds these limits (continuation page or cap) → **[DECISION D-18]**. Either way, render tests will measure every `.page` element and fail the build if content overflows.

## 8. New page — Hazard Pointer page (requirement §8)

Proposed as **page 6 of 8**, after Route Risk Analysis and before Journey Assessment. This fills the removed eighth slot. → [DECISION D-10]

Layout, in the same visual language:

- The navy section bar reads "ROUTE HAZARD POINTERS".
- A vertical route spine runs from START (green node) to END (green node), with intermediate stops as small blue nodes placed by cumulative distance along the route.
- 3–5 hazard callouts are drawn at the km position where each hazard occurs along the route. Each callout shows:
  - a risk pill (HIGH/MEDIUM/LOW colours from §3)
  - the library hazard name
  - the route context ("Naihati–Kakinara, km 38–44")
  - the single most important control
- Hazards spread along a stretch (for example a whole township belt) are drawn as a bracket spanning a km range. Point hazards (for example a level crossing) are drawn as a marker.
- A footer note reads: "Pointers show the N highest-ranked route-specific hazards from Danone's 25-Hazard library; full list on page 5."
- Positions come from real feature locations (see `hazard-library.md` §4). Where a hazard has no location (for example seasonal waterlogging), it is shown as a **route-wide** band with no km position. The page never invents a location.

---

## 9. As-built notes

The template is `backend/templates/jmp/v1/` — `report.html.j2` plus one partial per page, `styles.css`
holding the tokens from §3, and Carlito/Arimo embedded from `fonts/`.

* **Eight pages**, in the order analysed here, with the **hazard pointer page as page 6**; the
  "requires operational verification" checklist shares that page. Footers read `Page N of 8` on pages 2–8,
  and page 1 has no footer, as in the master.
* **Fonts**: Carlito (Calibri-metric, OFL) and Arimo (Arial-metric, Apache 2.0) are embedded, so output
  does not depend on host fonts. Licences in `templates/jmp/v1/fonts/LICENSES.txt`.
* **Icons** are the inline SVG set in `app/rendering/svg/icons.py`; the master's missing-glyph boxes are
  not reproduced.
* **Figures are drawn from real route geometry** (`app/rendering/svg/maps.py`): the page-1 and page-3 route
  maps, the alternative-route minis, the score gauge and the pointer-page spine. Hazard markers appear only
  where a detector measured a position; hazards without a location are listed in a separate "route-wide"
  band rather than placed at an invented point. If geometry is missing the render fails with
  `ROUTE_GEOMETRY_UNAVAILABLE`.
* **Overflow** is enforced, not hoped for: before the PDF is produced, every `.page` and its main blocks
  are measured in the browser, and any overflow fails the job with `RENDER_OVERFLOW`. The page-3 map height
  shrinks automatically as the segment table grows (added after a 13-stop journey overflowed by 7 mm).
* **Demo watermark**: any document generated with mock providers or the mock narrative writer carries a
  ribbon and diagonal *DEMO — NOT FOR OPERATIONAL USE* mark. It can be suppressed with
  `REPORT_DEMO_WATERMARK=false` (default on). Suppressing it only hides the banner — `demo_data` and
  `demo_narrative` stay in the stored report JSON and in the Reports listing, so a demo document is still
  identifiable. The banner stops appearing on its own once real providers and a Claude key are configured.
* **Version stamps** are printed at the foot of page 8 and written into the PDF metadata.
* **No sign-off block.** The master PDF ended with Prepared/Reviewed/Approved By signature boxes; Danone
  asked for these to be dropped, so template **v1.1** ends with the final-decision panel, the disclaimer and
  the version line. Page 8 is otherwise unchanged, and the report still states that EHS validation applies.
* Page 2 additionally carries a short **data-classification table** explaining VERIFIED/PROVIDED,
  ASSESSMENT and REQUIRES OPERATIONAL VERIFICATION, and page 5 adds a **Matrix** column showing the Danone
  matrix zone alongside the displayed band (see D-01).
