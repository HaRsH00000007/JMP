# Output

Return one JSON object that conforms to the supplied schema, with `"schema_version": "1.0"`.

Coverage rules the application checks automatically — an answer that breaks any of them is rejected and you will be asked again:

1. `hazard_notes` has exactly one entry per code in `candidate_hazards`, and no other codes.
2. `segment_notes` has exactly one entry per id in `segments`.
3. `alternative_notes` has exactly one entry per id in `alternatives`.
4. `dimension_remarks` has exactly one entry per id in `scores.dimensions`.
5. `fatigue_notes` has exactly one entry per id in `fatigue.stints`.
6. `priority_recommendations` has exactly three items in each of the four phases; `driver_readiness` has one to three.
7. Every number anywhere in your text appears in the facts object, the hazard library, or the standard guidance list.
8. No phone numbers, emergency numbers, URLs or e-mail addresses anywhere.
9. No library hazard names other than the candidates.

# Illustrative example (shape and tone only — a different route; do not reuse its facts)

For a candidate `HZ-08 Railway Crossing (Unmanned)` DETECTED at two crossings "near Kalyani", a good hazard note is:

```json
{"hazard_code": "HZ-08",
 "route_context": "Two level crossings near Kalyani on the township approach; barrier status to be confirmed",
 "card_location": "Kalyani township approach",
 "card_description": "Level crossings on the township approach; barrier status unconfirmed in route data.",
 "qualifier": "– Kalyani approach"}
```

A good dimension remark for `driver_fatigue` with score 70 and inputs `driving_hours_upper: 3.75, intermediate_stops: 11` is: "About 3.75 hours of driving across 11 stops; manageable with planned breaks."

A good `decision_rationale` names the HIGH-band hazards, states that they are managed by the listed controls rather than avoidance, and ends with the need for EHS validation of scoring and open verification items.
