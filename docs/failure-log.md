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

## 7 October 2026: pricing, export contract and portability fixes

4. **The exported `claim_packet.json` dropped required fields.** The download only
   contained a `materials` array of verified names. Root cause: the export filter written
   to keep unverified guesses out of the report was also used for the deliverable. Impact:
   an adjuster could not trace prices, totals or the review queue. Fix: `claim/contract.py`
   emits the brief's structure (required keys first; unknowns `null`/`""`; totals recomputed
   in code from the exported lines) and the report is rendered from it. Verified against
   the brief's key order, blanks, totals and sources.

5. **Prices existed only when an operator typed them.** Research returned eBay candidates
   but nothing applied them, and there was no FX path, so foreign quotes were dropped
   silently. Fix: provider quotes carry a title/author match score; valuation applies them
   deterministically (operator > local provider > converted provider), records every
   rejection reason in `price_details`, converts with dated ECB/ER-API evidence, and
   re-prices ten books for a second country. Verified with mocked providers;
   real quotes still need eBay credentials.

6. **OCR only worked on macOS.** Spine text used an Apple Vision helper compiled with
   `swiftc`; on any other machine it fell back to an untested path. Fix: `perception/ocr.py`
   with PaddleOCR (default) or EasyOCR behind one function, selectable with `OCR_ENGINE`.
   Not yet measured: OCR recall on the 60-book shelf with the new engine.

## 7 October 2026: first real run on the development laptop

7. **Nothing was detected in a live sweep (a held water bottle, three frames).** Every
   frame carried `Detection failed: ModuleNotFoundError: No module named 'ultralytics'`:
   the Python that served the API had none of the perception packages, no YOLO weights had
   been downloaded and Ollama was not running. The guidance line did say "I could not
   analyse that view", but nothing else in the UI made the failure obvious. Fix: install
   `requirements.txt` into the interpreter that runs uvicorn, run `tools/setup_detector.py`,
   start Ollama; `GET /api/health` now reports the configured models. With the stack in
   place the same kind of frame yields television, chairs, vases, lamp, rug and framed
   painting on a living-room test image in under three seconds.

8. **PaddleOCR could not be imported on a machine that also had TensorFlow + Keras 3.**
   `paddleocr` imports `transformers`, which tried to initialise its TensorFlow backend and
   raised. Fix: `logic/ocr.py` sets `USE_TF=0` before the import. Measured afterwards:
   PaddleOCR read 5/6 synthetic spines (the miss was text cut off at the strip edge) and real
   spine fragments ("CATALONIA", "COL-LECCIONS") where EasyOCR read nothing and misread
   "Dune" as "auna". PaddleOCR stays the default; 0.3-0.4 s per spine once loaded.

9. **Qwen2.5-VL 3B cannot load on an 8 GB Mac.** Ollama's Metal backend failed to allocate
   a 6.6 GB buffer; with a 1 k context it loaded but took 57-160 s per crop, far beyond the
   30 s reader timeout, so no title was ever proposed. Fix: Gemma 3 4B is the reader (5/5
   synthetic titles exact once warm, 8-10 s per crop), the reader timeout allows a cold load,
   models are kept resident for 30 minutes and warmed at startup. Moondream was tried as a
   lighter reader: it loaded slowly (four timeouts) and then read 2/2 correctly in 4-6 s.

10. **No book could ever be identified on a full shelf.** The identity gate required the
    whole frame's book count from YOLO26s and YOLO11s to be equal; on real shelves the two
    COCO models gave 48 vs 3 and 9 vs 17 boxes. Fix: the check is per book (a second
    detector drew the same box, IoU >= 0.5); the frame-wide count remains a review note.
    Also, the YOLOE room detector's "book" boxes were being discarded: on a shop wall it
    found 18 spines where the COCO detectors found 1-2, and 51 vs 9-17 on a library shelf.
    They now join the candidates with the detector name recorded as a witness.

11. **The blind crop check rejected every real book.** Gemma 3 4B, asked for the category of a
    single spine in a stack, answered "table" or marked the crop as not a clear single object,
    so no book could pass the identity gate. Qwen3-VL 2B agreed on 5/6 real crops with the
    cover, spine and title text named, but only after `/no_think` was appended to the prompt:
    its Ollama `think` flag is ignored and the JSON never arrived. Fix: Qwen3-VL 2B is the
    verifier, Gemma 3 4B the reader, so the two opinions still come from different models.

12. **Google Books returned nothing even with a working key.** The provider searched with
    `intitle:"…" inauthor:"…"`; on 8 October 2026 those operators (and `isbn:`) return zero
    volumes for well-known titles, while a plain-text query returns hundreds. Fix: plain query,
    20 results, the existing title/author match score does the filtering. Measured after: 6
    candidates for "Zero to One" in GB, 3 in AE. Remaining limit: every priced volume Google
    returned was a Google Play ebook, which the valuer rejects as a physical replacement cost
    unless `PRICING_ALLOW_EBOOK_PROXY=true`; physical prices need the eBay source.

13. **A fixed OCR budget skipped most spines on a dense shelf.** With a 10 s per-frame OCR
    budget, 40–52 of 53 worn spines were never read ("OCR frame budget reached"). Fix: the
    budget scales with the detected spine count (10 s floor, 1.5 s per spine, 60 s cap).
    Measured after, same photo: 48 of 53 spines read (was 13), 3 identified (was 1). Cost: the
    title reader then runs on 48 crops at 8–12 s each, so the frame took 13 minutes; the
    reader needs the same budget-and-defer treatment as the crop check.

14. **An author's name was accepted as a title.** On the worn shelf, OCR read `Northanger`,
    `JANE AUSTEN`, `Abbey`; the reader proposed "JANE AUSTEN" with an empty author, and every
    gate rule passed (exact OCR text, confidence 0.985, two detectors). The gate could not tell
    a writer's credit line from a title. Fix, three layers: the reader prompt now says a
    person's name belongs in `author`, and a title equal to the chosen author is dropped; the
    live gate refuses a title that equals the author line; and at finish the Open Library
    authors search is consulted first, so a "title" that is an author with 20+ works is demoted
    to `author` with a review finding. Measured after, same packet: "JANE AUSTEN" demoted
    (2,225 works), identified count 3 -> 2, the name kept as the line's author. Remaining
    limit: the live gate alone still passes this case when the reader leaves `author` empty;
    the catalogue check needs the network at finish time.

15. **Stop sweep left the sweep hanging with "Wait for frame processing before finishing".**
    Frames go out every 2 s and take 20-60 s each, so Stop almost always lands while one is in
    flight. The frontend turned the camera off and called finish at once; the backend's 409
    (correct: merging a frame during finish would corrupt the packet) was shown and never
    retried, so the sweep stayed active for ever. Fix: Stop waits for the in-flight frame,
    retries finish on that 409, and a status strip shows scanning / processing / finishing.
    Verified in the browser with a synthetic camera feed: Stop pressed at 30 s into a 50 s
    frame, finish returned 200 after the frame, no 409 in the network log.

16. **A tissue box counted as a book and the blind check crashed on it.** The detector called
    the box a book at 41 %, the second detector agreed on the box, OCR read the printed
    marketing text, and Gemma proposed the manufacturer as the author. The Qwen blind check,
    the one model that should have said "not a book", answered a category outside its list
    and the code raised a ValueError, recorded as "unavailable". Fix: an unlisted answer such as
    "tissue box" is kept as `raw_category` and becomes a `conflict` with the reason "Blind
    check saw a tissue box, not a book". The gate had already kept the line unidentified; the
    box still counts as one detected book, which is a known limit of the COCO "book" class.

