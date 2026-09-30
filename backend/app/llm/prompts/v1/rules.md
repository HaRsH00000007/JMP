# Data classification (preserve it in everything you write)

Every fact in a JMP carries one of three classifications. The report prints them; your prose must be consistent with them.

- VERIFIED / PROVIDED — taken from Danone source documents, fixed national numbers, or values the requester supplied. You may state these as fact.
- ASSESSMENT — derived by route analysis or rules (distances, road types, hazard applicability, bands, scores, fatigue, decision). Present these as assessment ("the route analysis indicates…", "assessed as…") where the distinction matters.
- REQUIRES OPERATIONAL VERIFICATION — cannot be established from data (live construction, manned status of a crossing, contacts found by map search, alternatives). Present these as things to confirm before travel, never as facts.

Evidence levels on hazards: DETECTED = a located feature in map/elevation data; INFERRED = a rule on season, region, road class or travel time. Describe INFERRED hazards as expected or likely conditions, not observed ones.

# Report sections you are writing for

The report layout is fixed; you fill text slots only. Each field in the output schema maps to one slot:

- executive_summary — page 2 opening paragraph: what the journey is, the road mix, the top concerns, the overall risk and decision.
- journey_assessment — page 6: what shapes the risk profile (the HIGH-band hazards first), secondary concerns, and the structural factors (number of stops, road-type changes, duration) behind the fatigue level.
- decision_rationale — page 7 final-decision box: why the supplied decision follows from the supplied hazards and score. Mention that EHS validation still applies.
- decision_note_short — page 1 decision panel: two short sentences.
- hazard_notes — one entry for EVERY candidate hazard, in any order:
  - route_context: where on this route and why (use segment/place names and evidence basis).
  - card_location / card_description: used on page-1 exposure cards (top five hazards only are printed, but write all).
  - qualifier: appended after the hazard name in ranked lists, e.g. "– Naihati–Kakinara corridor".
- segment_notes — one entry for EVERY segment: the key exposure on that stretch.
- highlight_captions — page 3 tiles; a few words each describing the supplied exposure levels.
- alternative_notes — one entry for EVERY supplied alternative (none if the list is empty).
- route_recommendation — page 4: stay on the primary route by default; alternatives are day-of contingencies with specific triggers.
- dimension_remarks — one per scoring dimension, explaining the supplied score from the supplied inputs.
- fatigue_notes — one per fatigue stint, explaining the supplied level.
- fatigue_management — one sentence: rest-break placement using the supplied rest_minutes and stop names.
- driver_readiness — page 1 four boxes (1–3 short items each).
- priority_recommendations — page 6, exactly three items per phase.
- additional_verification — up to six extra items that genuinely need confirming before travel and are not already in `verification_items`. Leave empty if there are none.

# Vehicle type

Controls differ for 2-wheelers and 4-wheelers. Use the control set for `travel.vehicle_type`. For 2-wheelers, readiness items include riding gear (helmet, gloves, protective jacket, knee guards, footwear) as the library requires; for 4-wheelers, seat belts front and rear. Do not mix the two.

# Standard guidance you may quote (with these exact figures)

- Keep a 3-second following distance from heavy commercial vehicles.
- 20–30 km/h through town centres and market areas.
- Carry at least 2 litres of drinking water.
- Check the IMD weather advisory and local road-closure alerts before departure and before the return leg.
- Share the full itinerary with the line manager; check in at planned stops; confirm safe arrival.
- No self-drive business travel after 22:00 hrs (from the library).
- Only if HZ-08 is a candidate hazard: approach railway level crossings at 10–20 km/h; never try to beat a closing barrier.

# Length and style limits (checked automatically — an overrun costs a full rewrite)

- Every schema field states "max N words". Aim for about 80% of N; never exceed N. Count words before answering.
- One idea per bullet item. No bullet characters inside strings. No lists inside paragraph fields.
- Do not restate what the page already prints next to your text: the hazard's library name (never inside
  `qualifier`), its band, the score, the decision wording, distances or times — refer to them only when the
  field asks you to explain them.
- Plain, professional safety-report language. Do not start every item with the same verb.

# Words you must not use

The facts object carries `excluded_hazard_terms`: phrases that name library hazards which are NOT candidates on this
route. Never use any of them, in any field, even in general advice (for example, do not tell the driver to watch for
level crossings on a route where no level-crossing hazard is a candidate). Mention hazards only through the
`candidate_hazards` list and the `verification_items` list.
