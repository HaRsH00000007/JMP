# Hazard Library Analysis

Source: `JMP Template- 25 Hazards.xlsx`. Tags follow `pdf-analysis.md`.

## 1. Workbook structure [CONFIRMED]

| Sheet | Content |
|---|---|
| `Header` | A 6-row "Particulars" form with an empty **Details** column: Name of the manager · Route Details · Tentative date of travel · Emergency Contact Details · Nearest Hospital · Nearest Police Station |
| `JMP Template` | "Road Risk Assessment": 25 hazards in rows 3–27, columns A–H, plus a hospital-network link in row 28 |
| `Risk Matrix` | A single embedded **image** (`xl/media/image1.png`): Danone's likelihood × consequence matrix, "Risk Management Core Principles", slide 2 |

### `JMP Template` columns

| Col | Header (verbatim) | Content |
|---|---|---|
| A | Sr No | 1–25 |
| B | Type of Hazard on the road | Hazard name |
| C | Hazard Available Yes/No | **Empty.** It has a data-validation dropdown (`$XFD$3:$XFD$4` = "Yes"/"No"). **The template expects each journey to mark every hazard applicable or not.** This is exactly what our hazard engine produces |
| D | Severity | Integer 2–5 |
| E | Probability | Letter A–E (only **D** and **E** are used) |
| F | RPN (SXP) | Formula `=CONCATENATE(E,D)`, so the value is a **code** such as `D5`. It is **not** a numeric product, despite the "S×P" header |
| G | Control Maesure (2 Wheeeler) | Bullet text separated by `\n•` |
| H | Control Measure (4 Wheerler) | Bullet text |

Row 28 reads: "Link to view member hospital list- https://www.paramounttpa.com/Home/ProviderNetwork.aspx" (insurer TPA provider network). [CONFIRMED]

## 2. The 25 hazards [CONFIRMED values, verbatim]

| # | Hazard | Sev | Prob | RPN | Matrix cell colour* |
|---|---|---|---|---|---|
| 1 | Sharp Turn / Hairpin Bend | 5 | D | D5 | Red |
| 2 | Steep Uphill Slope | 4 | D | D4 | Red |
| 3 | Downhill Slope | 5 | D | D5 | Red |
| 4 | Narrow Road | 3 | D | D3 | Yellow |
| 5 | Potholes / Broken Surface | 5 | D | D5 | Red |
| 6 | Unmarked Speed Breaker | 3 | D | D3 | Yellow |
| 7 | Bridge / Culvert | 5 | D | D5 | Red |
| 8 | Railway Crossing (Unmanned) | 5 | D | D5 | Red |
| 9 | Blind Curve | 4 | D | D4 | Red |
| 10 | Poor Road Markings/ No speed limit signage on road | 4 | D | D4 | Red |
| 11 | Roadside Construction / Diversion | 4 | D | D4 | Red |
| 12 | Waterlogging / Flooded Patch | 4 | D | D4 | Red |
| 13 | Landslide / Rockfall Zone | 5 | D | D5 | Red |
| 14 | Fog-Prone Stretch | 4 | D | D4 | Red |
| 15 | Busy Intersection | 4 | D | D4 | Red |
| 16 | Village Road Crossing | 3 | D | D3 | Yellow |
| 17 | Overhead Obstacles (low bridge, wires) | 3 | D | D3 | Yellow |
| 18 | Slippery Road (oil, wet)/ Muddy Patch | 4 | D | D4 | Red |
| 19 | Animal Crossing / Forest roads | 4 | D | D4 | Red |
| 20 | Pedestrian-Prone Area | 3 | D | D3 | Yellow |
| 21 | Ghats / Bridges | 5 | D | D5 | Red |
| 22 | Mobile Network Dead Zone | 2 | D | D2 | Yellow |
| 23 | Poorly Lit Road (Night Travel) | 4 | D | D4 | Red |
| 24 | National Highway | 5 | **E** | E5 | Red |
| 25 | Dangerous Dip | 3 | **E** | E3 | Red |

\* I read the colours from the matrix image (§3). The image is the only source, so the colours should be confirmed once by Danone EHS before the system relies on them.

## 3. Danone risk matrix (from the `Risk Matrix` image) [CONFIRMED]

The image is titled "2X2 RISK MATRIX", but it is actually a 5 × 5 grid.

**Consequence (1–5):**

1. Negligible (no injury)
2. Minor (first aid case)
3. Important (LTA/NLTA)
4. Severe (permanent injury / disability)
5. Major (1 or more fatalities)

Each level also has an Assets / Environment / Reputation descriptor.

**Likelihood (A–E):**

- A — Almost impossible (>20 yrs)
- B — Unlikely (once in 10 yrs)
- C — Unusual (once in 1–3 yrs)
- D — Possible (once in 6 months)
- E — Predictable / Expected (once in last week)

**Cell colours:**

| Sev \ Prob | A | B | C | D | E |
|---|---|---|---|---|---|
| 1 | G | G | G | G | G |
| 2 | G | G | G | Y | Y |
| 3 | G | G | Y | Y | R |
| 4 | G | Y | Y | R | R |
| 5 | Y | Y | R | R | R |

### Consequence for the product — conflict with the PDF → [DECISION D-01]

| Method | Source | Result on the 25 hazards |
|---|---|---|
| **Severity-only band** (5 = HIGH, 4 = MEDIUM, ≤3 = LOW) | PDF page-5 footnote, which also says "methodology not supplied" | 8 HIGH / 11 MEDIUM / 6 LOW |
| **Danone matrix cell** | Excel `Risk Matrix` sheet | **19 Red / 6 Yellow / 0 Green** |

With the matrix, most hazards are red. That is authoritative, but on its own it can't tell hazards apart for ranking. My recommendation:

- Store **both** in the library, as `severity_band` and `matrix_zone`.
- Display whichever one Danone EHS picks.
- Always **rank** with a deterministic tie-breaker (§5).

I also need the zone labels confirmed. Does Red/Yellow/Green map to HIGH/MEDIUM/LOW? Does "CRITICAL" (mentioned in the PDF) exist? → [DECISION D-02]

## 4. Data quality issues in the library [CONFIRMED]

The source text must be stored **verbatim**. Whether to show a corrected display version → [DECISION D-06].

- **Typos:** "Maesure", "Wheeeler", "Wheerler", "noyt", "tarffic", "continuosly", "breaking distance" (braking), "upto 10 ti 20 Km/h".
- **Duplicate bullets:** #17 4W and #19 2W repeat "Mandatory use of…".
- **Missing seat-belt bullet:** #24 and #25 in the 4W column.
- **Empty bullet:** #14 2W has "• •Mandatory use of Safety Gear".
- **Stray dropdown values** in cells `XFD3:XFD4`. These are the source of the Yes/No list, not data.

## 5. Normalized internal representation

Ingestion is a one-off CLI (`python -m app.cli.ingest_hazards <xlsx> --version 1.0`). It is idempotent, and it checksums the source file.

```text
hazard_library_versions
  id, version ("1.0"), source_filename, source_sha256, ingested_at, is_active, notes

hazards                         (one row per hazard per library version; immutable once active)
  id, library_version_id, code ("HZ-08"), sr_no (8),
  name ("Railway Crossing (Unmanned)")            -- verbatim
  severity (5), probability ("D"), rpn_code ("D5") -- verbatim; RPN re-derived and asserted equal
  severity_band  (HIGH/MEDIUM/LOW)                -- derived by the configured rule (D-01)
  matrix_zone    (RED/YELLOW/GREEN)               -- looked up from risk_matrix_cells
  control_2w_raw, control_4w_raw                  -- verbatim cell text
  control_2w_items[], control_4w_items[]          -- parsed bullets (split on \n / •, trimmed, de-duplicated)
  control_short_2w, control_short_4w              -- curated one-liner, human-approved (D-07); NULL until approved
  detection_profile (JSONB)                       -- how the engine detects it (§6)

risk_matrix_cells
  library_version_id, probability (A–E), severity (1–5), zone (RED/YELLOW/GREEN)

likelihood_levels / consequence_levels            -- A–E and 1–5 descriptors from the image, verbatim
```

Ingestion runs these checks:

- there are exactly 25 rows
- severity ∈ 1..5 and probability ∈ A..E
- the RPN in the file equals probability + severity
- names are unique

If any check fails, ingestion fails. **The application never edits S/P/RPN.** A change arrives only as a new library version, from a new source file.

## 6. Hazard detectability (hazard-engine design input)

This is the most important engineering finding. **Routing APIs return distance, time and geometry. They do not return most of these 25 hazards.** Each hazard therefore needs a detection method and an evidence level:

- **DETECTED** — a located feature from data.
- **INFERRED** — a deterministic rule on context (season, region, time, road class).
- **VERIFY** — cannot be established from data. It appears only in the "Requires operational verification" list and **never** as a route fact.

| # | Hazard | Detection method | Data source | Evidence |
|---|---|---|---|---|
| 1 | Sharp Turn / Hairpin | Bearing change > threshold within a short distance on the route polyline | Route geometry | DETECTED |
| 2 | Steep Uphill | Gradient > threshold over a window | Elevation profile | DETECTED |
| 3 | Downhill | Same, negative gradient | Elevation profile | DETECTED |
| 4 | Narrow Road | `lanes=1`, `width<…`, `highway=residential/unclassified/track` | OSM road attributes | DETECTED (partial coverage) |
| 5 | Potholes / Broken Surface | Road class (township/unclassified) plus `surface`/`smoothness` tags | OSM | INFERRED |
| 6 | Unmarked Speed Breaker | `traffic_calming=*` exists only for *marked* ones, so the rule falls back to road class plus settlement | OSM | INFERRED |
| 7 | Bridge / Culvert | `bridge=yes` intersecting the route | OSM | DETECTED |
| 8 | Railway Crossing (Unmanned) | `railway=level_crossing` on the route. *Unmanned* status comes from `crossing:barrier=no` where tagged; otherwise the manned/unmanned status is marked VERIFY | OSM | DETECTED (the crossing) · VERIFY (whether it is manned) |
| 9 | Blind Curve | Curvature plus built-up or hilly context | Geometry + OSM landuse | INFERRED |
| 10 | Poor markings / no speed signage | Missing `maxspeed` on long stretches (weak signal) | OSM | INFERRED (low) |
| 11 | Roadside Construction / Diversion | Live incidents/closures at generation time | Traffic-incident API (optional) | DETECTED (day-of) · otherwise VERIFY |
| 12 | Waterlogging / Flooded Patch | Travel month ∈ region's monsoon window, plus optional flood-prone-location list | Seasonal config table | INFERRED |
| 13 | Landslide / Rockfall | Terrain relief plus hilly-region config | Elevation + region config | INFERRED |
| 14 | Fog-Prone Stretch | Travel month ∈ region's fog window (e.g. Indo-Gangetic plain Dec–Jan) plus early/late hours | Seasonal config | INFERRED |
| 15 | Busy Intersection | Signalised or major-road junctions on the route (`highway=traffic_signals`, primary × primary) inside built-up areas | OSM | DETECTED |
| 16 | Village Road Crossing | Junctions with `unclassified/track` in rural landuse | OSM | DETECTED (partial) |
| 17 | Overhead Obstacles | `maxheight` tags, low bridges | OSM | DETECTED (partial) |
| 18 | Slippery / Muddy | Unpaved `surface` plus wet season | OSM + seasonal config | INFERRED |
| 19 | Animal Crossing / Forest | Route passes `landuse=forest`, `natural=wood` or protected areas | OSM | DETECTED |
| 20 | Pedestrian-Prone Area | Share of route through built-up/commercial landuse, plus POI density (markets, schools, stations) | OSM | DETECTED |
| 21 | Ghats / Bridges | High gradient plus curvature cluster, or major bridges | Elevation + geometry + OSM | DETECTED |
| 22 | Mobile Network Dead Zone | Coverage data if licensed, otherwise remote-area heuristic | Coverage dataset (optional) | INFERRED / VERIFY |
| 23 | Poorly Lit Road (Night) | Planned travel window overlaps sunset → sunrise, plus `lit=no` | Travel time + solar calc + OSM | INFERRED (requires travel time) |
| 24 | National Highway | `ref` starts with "NH", or `highway=motorway/trunk`. Also the explicit **analog** for expressway/HCV exposure (as in the PDF) | OSM / provider step names | DETECTED |
| 25 | Dangerous Dip | Local minima in the elevation profile over a short distance | Elevation profile | INFERRED |

Consequences:

- **Travel date/time and vehicle type are required inputs** for #12, #14, #18 and #23, and for choosing 2W vs 4W controls. → [DECISION D-08]
- The seasonal/regional windows (monsoon, fog, hilly regions) are business configuration that someone at Danone must own. → [DECISION D-21]
- All thresholds (turn angle, gradient %, built-up share) live in a versioned `hazard_rules.yaml`. They are **[ASSUMPTION]** until calibrated against real JMPs.

## 7. Ranking (deterministic) [ASSUMPTION — for approval]

This is the sort key for applicable hazards, in priority order:

1. Display band (per D-01): HIGH > MEDIUM > LOW
2. Matrix zone: RED > YELLOW > GREEN
3. Probability letter: E > D > …
4. Severity: 5 > 4 > …
5. Evidence: DETECTED > INFERRED
6. Exposure extent (km affected, or occurrence count), descending
7. Library `sr_no` — a stable final tie-breaker

Outputs:

- **Page 5 hazard table:** all applicable hazards (cap 10).
- **Top hazards:** first 7.
- **Page-1 exposure cards / key-hazards table:** first 5.
- **Hazard pointer page:** first N (default 5, configurable).

The LLM never picks hazards. It only writes the route-context phrase for hazard codes that the engine has already selected.

---

## 8. As-built notes

* **Ingestion** (`app/services/hazard_library.py`, CLI `python -m app.cli.ingest_hazards`) stores all 25
  hazards verbatim — names, severity, probability, RPN code and both control sets, typos included — and
  refuses the workbook if any RPN is not probability+severity, if a severity is out of range, if there are
  not exactly 25 rows, or if the risk-matrix image's SHA-256 differs from the transcription in
  `config/risk_matrix.yaml`. Re-ingesting the same file under the same version is a no-op; a different file
  under an existing version is rejected.
* **Both bands are stored.** `severity_band` (master-PDF method) and `matrix_zone` (Danone matrix). The
  displayed one is `display_band_method` in `config/risk_matrix.yaml` — currently `severity_band`, with the
  matrix zone shown as an extra column on page 5, pending D-01.
* **Detection** is implemented for all 25 hazards as described in §6, driven by `config/hazard_rules.yaml`.
  A test asserts every hazard resolves to a registered detector kind. Hazards that cannot be established
  from data (construction; seasonal or night hazards when no travel date/time was supplied) produce
  verification items instead of being asserted as facts.
* **Short controls (D-07)** are not yet EHS-approved, so the report prints the first two **verbatim**
  library bullets (excluding the generic seat-belt/safety-gear bullet). Approved one-liners are proposed in
  `config/hazard_display.yaml`; they are used only once that file's `approved: true` is set.
* **Typo corrections (D-06)** are proposed in the same file and applied only when approved *and*
  `HAZARD_DISPLAY_TEXT=approved_corrections`. The default remains verbatim.
* **Vehicle type** selects the 2W or 4W control set throughout; if it was not supplied, the report states
  the 4W assumption and adds a verification item.
