# Ground truth and whole-sweep evaluation

Collect truth before tuning: >=60 books across >=2 shelving units, >=8 non-book items, one enclosed room. Keep every object, including missed/unreadable ones. `system_id` maps a truth row to its packet line; use null when the system missed it. Never map multiple physical objects to the same system ID.

Save a JSON file with this structure (example values describe the schema, not measured results):

```json
{
  "books": [
    {"system_id": null, "shelving_unit": "Unit A", "legible": true, "title": "Hand-read title",
     "spine_height_cm": null, "spine_thickness_cm": null,
     "replacement_cost": null, "used_value": null}
  ],
  "items": [{"system_id": null, "category": "chair"}],
  "room": {"floor_area_m2": null, "wall_area_m2": null}
}
```

For each of at least 15 hand-checked book prices, replace the corresponding null with `{"amount": 25, "url": "https://retailer.example/listing", "currency": "AED"}` using the actual observed amount and source. Take 20 paired spine height/thickness measurements. Ground-truth room areas come from tape measurement; system areas come from the recorded capture source. Do not copy system estimates into the ground truth.

```bash
backend/.venv/bin/python backend/tools/evaluate.py data/packets/SWEEP.json ground_truth.json --output results.json
```

The evaluator reports count error <=5%; correct titles >=70% of legible spines and confidently wrong <=3%; 20 spines each within 15%; 15 prices each within 25% using the same URL/currency; item recall >=80% with correct category; floor error <=10%; wall <=15%; packet latency <300 seconds. The strict per-sample interpretation is explicit. Missing predictions count as misses. Missing truth yields a failed/not-proven result, never a fabricated pass.

Also demonstrate the same ten books in a second country using checked quotes and the comparison panel. Record the <=6-minute demo in one unedited take with audible agent dialogue. The app's camera evidence recording has no audio and is not, by itself, that demo.
