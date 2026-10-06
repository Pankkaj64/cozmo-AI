# Failure log — current evidence, 2026-10-06

This is a development failure log, not the required full-room ground-truth evaluation.

1. **Counts existed but titles appeared blank.** In saved sweep `c7288b12-6942-43fe-9dff-44d909cde52c`, both models counted two books. The detailed output supplied subject summaries instead of visible locations and invented an author for *Extreme Ownership*. Those observations were rejected; the count survived as two anonymous candidates. Fixes: independent count/title status, OCR checks for authors as well as titles, explicit review reasons and rotated OCR. On the saved frame, the revised OCR found two lines (`रावण`, `अमीश`) in 1.102 s, versus one stored OCR line in the old record. That is a measured OCR-evidence improvement on one frame, not a demonstrated title-identification pass.

2. **Single-frame inference far exceeded a usable live cadence.** Local replay of `c7288b12-6942-43fe-9dff-44d909cde52c-1791272824025.jpg` took 430.235 s: primary count 85.519 s, independent count 5.676 s, lengthy detail generation, and a 180-second non-book timeout. The models agreed on two books; no title was confirmed and no reliable item list survived. The replay began before the final image/response-budget reductions. Counting now uses an at-most-800-pixel model image and 64-token output budget; original-resolution OCR/evidence is retained. Item prompts prohibit repetition and exact repeats are deduplicated. These latter performance changes have not been benchmarked on the full room. With two more weeks: profile Metal/offload, use an actual spine detector plus crop OCR, cache visual embeddings, and benchmark appropriate hardware/providers rather than promise a five-minute packet on this Mac.

3. **Moving shelves could erase pending evidence.** A single-slot queue replaced an earlier shelf with the newest frame while inference was busy. It now retains the newest pending view for each shelf, and optional continuous camera video preserves the pass. A regression test confirms Shelf 1's pending frame survives moving to Shelf 2; another verifies the final recorder chunk is included before upload. This fixes a reproducible data-flow loss, but does not provide automatic cross-view tracking. With two more weeks: implement geometric overlap/track association, spatial coverage and scale-aware spine segmentation.

**Cost:** local model API spend is zero; hardware/electricity and browser speech service costs are not measured. Optional market API access depends on the user's provider account. No paid source calls were made during these checks. New packets record stage duration, capture duration and time after capture stop. `data/diagnostics/workflow-replay.json` preserves the replay output without overwriting the original claim.

**Submission gaps:** no qualifying 60-book/two-unit/eight-item dataset, independent tape measurements, 15 checked prices, ten-book second-country demonstration or unedited audio demo. Their pass bars remain unmeasured. Some older frame files referenced by the existing packet are no longer present on disk; the app did not recreate or invent them.

## Latest recording: inventory erasure and missed categories

Sweep `d2ad4b74-5627-45a7-9afa-5cec67c74d2e` had 29 frames but exported zero books
and zero items. Each successful frame replaced all entries under `Shelf 1`; the
last view contained no held book. Earlier frames and the video survived.

Repairs: accumulate one-to-one observations; preserve reviewed facts, offers and
measurement evidence; keep historical boxes; retain every queued sample; include
room detections in frame history and the UI. YOLO26s misclassified Extreme
Ownership as a phone. A constrained crop reader recovered that title, 8 Rules of
Love, and रावण. One full model replay took 88.87 s and retained all three through
the final empty view. Titles are proposals, not automatically confirmed identities.

Rotated OCR results previously mixed conflicting readings, and one invalid role
selection discarded a whole batch. The pipeline now selects one coherent OCR
orientation and handles books separately. Author/publisher text may still be
unreadable; leaving it blank is intentional.

The added YOLOE room detector covers more required categories but produces TV/
mirror mistakes, false positives and duplicate partial-object candidates. Scene
alignment reduces duplicate tracks; it does not establish a physical count. The
experimental contact-sheet room reader timed out or shifted categories (cup to
chair), so it is disabled and documented in model_choices.env. No ≥80% room-item
accuracy claim is made. More suitable local hardware/models or a trained room/
spine dataset, plus independent ground truth, are still needed for acceptance.
