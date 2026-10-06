# Library Contents Claim Agent

Python/FastAPI + React/TypeScript implementation of the Library Contents Claim Agent brief. A live camera sweep builds a persistent inventory, dialogue, evidence history and review packet. Vision/OCR run locally. Measurements and monetary totals come from code and recorded sources.

**Current limits:** this is an implemented workflow, not an accuracy-certified submission. The local detector provides book boxes quickly; crop OCR and independent checks can still miss books or fail to read text. No 60-book ground-truth evaluation or unedited room demo has been collected. Calibrated spine measurement needs a visible known reference and valid spine bounds. Room dimensions need known dimensions/LiDAR/reference geometry. Market matches need verification; missing values remain unknown. These requirements are not represented as passed.

## Run

Python 3.11–3.13, Node 20+, Ollama; macOS OCR also requires Apple's Swift command-line tools. The first model downloads total several GB. Active defaults and commented model alternatives are in `backend/model_choices.env`.

```bash
ollama pull qwen2.5vl:3b
ollama pull qwen2.5:3b
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python tools/setup_detector.py
# First setup only: do not overwrite an existing .env.
cp -n .env.example .env
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 --timeout-graceful-shutdown 5
```

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. HTTPS or localhost is required for the camera. A phone accessing the Mac over plain HTTP will not have camera access; use a trusted HTTPS development setup for phone capture. Backend inference has no hosted model key. PaddleOCR is optional on macOS; on other platforms also install a compatible `paddlepaddle` runtime. The backend reads `.env`, then `model_choices.env` defaults; shell variables take precedence. Restart the backend after changing model configuration.

## Capture and review

1. Confirm country, two-letter delivery country and currency. Start the camera. The greeting explains the sweep. Keep the complete shelf section in view; do not remove individual books.
2. The camera preview is live video. If recording is supported and checked, a continuous **video-only** recording is saved. Detection analyzes sampled JPEG images, not video clips. Audio is not embedded in this evidence video; use a separate screen recorder for the required one-take demo with agent audio.
3. Say **next shelf** or change the label for each non-overlapping section. Each sampled view is queued in order, including multiple views under the same label. One inference runs at a time; the UI shows the backlog. A 120-image cap pauses sampling with a warning instead of silently overwriting waiting views. Appearance/text matching associates repeated objects; this is not SLAM or a guarantee of counting identical copies. Books under different shelf labels can still be duplicates; review overlaps.
4. The agent requests retakes for uncertain counts/unreadable titles and asks whether artwork is a print. Stage activity streams while inference runs. The quality checks are conservative image heuristics, not proof of occlusion/glare detection or full coverage.
5. Say **skip this shelf**, **capture now**, **next shelf**, **coverage complete** or **status**. Select a line before saying **that is a first edition**, **that is a print**, **that is an original** or **title is …**. Ambiguous statements stay in the review queue. Typed input offers the same tools. Browser speech recognition may use the browser vendor's speech service and is not available in every browser. Stop talking interrupts audio.
6. Finish waits for pending images and video upload. Optional source research follows. JSON and HTML are generated from the same packet. Use **Review saved sweeps** to reopen a record without starting the camera.
7. In review, click two endpoints of a known reference in a saved image, enter its centimetre length and confirm a front-on, same-plane view. Mark the selected spine's two corners. Detector boxes enclose whole books and are deliberately excluded from automatic spine measurements. This is assisted annotation on an existing sweep image, not automatic calibrated reconstruction.
8. Enter room dimensions from an established source, or a non-self-intersecting polygon in metres. The app computes floor and gross wall areas in m²/ft², plus shelving coverage. Gross wall area includes doors/windows. Non-book dimensions accept a recorded metric source. Never enter model guesses as scale evidence.
9. Record checked local replacement/used prices with URL, date, condition and match basis. A foreign quote needs an explicit dated FX rate/source. Values at or above 2,000 in the selected claim currency, special editions, signed/rare books and original artwork go to appraisal. The threshold is a prototype policy, not an exchange-equivalent universal threshold.
10. Verify the proposed titles/authors/publishers and room categories/materials against their numbered boxes. Exclude false detections or duplicates with a reason. After review changes, click **Update exports after review** to regenerate the JSON and HTML. Existing exports otherwise remain the previous saved version.

## Source research

Optional configuration in `backend/.env`:

```dotenv
ENABLE_CATALOGUE_LOOKUP=false
EBAY_ACCESS_TOKEN=
EBAY_MARKETPLACE_ID=EBAY_US
```

Open Library can resolve exact title/author matches to a **work**; it does not establish a physical edition or ISBN. Enable it with `ENABLE_CATALOGUE_LOOKUP=true`. eBay Browse lookup needs your own application access token and a supported marketplace. Search uses the sweep's delivery country; a foreign seller or currency is not relabeled as a verified local quote. Results are asking-price comparables, exclude shipping/tax, and require physical-format/edition/condition review. No source API key is bundled. With no configured provider, enter source-checked offers through the review form. The app does not auto-accept retailer search matches. Set `ENABLE_LIVE_RESEARCH=true` to retrieve candidate sources during capture; new and used searches run separately. Source review remains required before totals.

Only inventory text is sent to these optional lookup services. Camera images remain on the local backend/Ollama. Market research is bounded to 180 seconds and preserves partial results. Compare the same inventory in a second country after adding its offers; missing quotes remain blank. A real 10-book two-country demonstration still needs those sources.

References: [Open Library Search API](https://openlibrary.org/dev/docs/api/search), [eBay Browse API](https://developer.ebay.com/api-docs/buy/api-browse.html), [eBay filters](https://developer.ebay.com/api-docs/buy/static/ref-buy-browse-filters.html).

## What was kept and changed from the reference

Read the [Insurance Claim Live Agent Team](https://github.com/Shubhamsaboo/awesome-llm-apps/tree/main/voice_ai_agents/insurance_claim_live_agent_team) and ran its unmodified FastAPI app in an isolated temporary environment on 2026-10-06: UI, health and session creation returned HTTP 200. Health reported no API key. **The actual Gemini Live conversation was not run**, because no Gemini key was configured. A startup/session check is not a completed voice demo.

Kept: server-owned state, role-labelled dialogue, sequential perception/domain-rule stages, live workflow updates, explicit next action, evidence links and audit history. Changed: local YOLO26s/YOLO11s book detection + YOLOE room categories + Apple Vision crop OCR + Qwen2.5-VL 3B crop reading; a library inventory instead of damage intake; deterministic geometry, price evidence and totals; optional continuous camera recording. Dropped: policy lookup, coverage decisions, avatar generation and insurer submission. This is independent domain code, not the reference with a changed prompt; it does not require ADK or Gemini.

## Pipeline / files

- `backend/app/main.py`: transport, streaming progress, persistence, evidence and exports.
- `backend/app/agent.py`: perception-to-inventory merge, stage records, measurement/pricing/review orchestration and next guidance.
- `backend/app/local_detector.py`: cached book and room detectors, best-orientation crop OCR, local title proposals and independent box checks.
- `backend/app/tracking.py`, `item_reader.py`: one-to-one association and reviewable room-category/material/brand proposals.
- `backend/app/vision.py`: native OCR at 0/90/270 degrees, legacy model experiments and second detector box checks.
- `backend/model_choices.env`: active model defaults and commented alternatives with observed rejection reasons.
- `backend/app/conversation.py`: explicit correction tools and source-traced claimant turns. Explicit commands use deterministic tools; open questions use a separate context-aware Ollama dialogue model with recent conversation and inventory state.
- `backend/app/identification.py`: independently testable OCR identity acceptance gate.
- `backend/app/validation.py`: deterministic review stage, independent of models and pricing.
- `backend/app/measurement.py`: reference scale, spine geometry, rectangular/polygon room geometry.
- `backend/app/pricing.py`: offer validation, currency conversion, appraisal gates and local-quote preference.
- `backend/app/research.py`: optional work lookup and replaceable `PricingService` / `EbayPricingService` market evidence retrieval.
- `backend/app/packet.py`: decimal monetary subtotals and full HTML report.
- `frontend/src/App.tsx`, `ReviewTools.tsx`, `recording.ts`: live capture, agent/review UI and camera recording.
- `backend/tools/evaluate.py`: complete ground-truth scoring; `docs/` contains architecture, limitations and collection instructions.

Each frame keeps book **and room-item** boxes, per-crop OCR, count comparison and its saved file. Select any inventory line to revisit its historical evidence. A later empty view does not erase earlier observations. OCR uses the best coherent orientation rather than mixing conflicting readings. Local image reading can propose a title when a stylized cover defeats OCR; authors/publishers are constrained to exact OCR text choices. Automatic identity requires exact agreement between crop OCR (confidence ≥0.9), the image title reader and the independent count check, with no partial/fallback box. Other proposals stay unconfirmed. ISBNs must be visibly labelled and checksum-valid; optional Open Library ISBN lookup supplies source-backed metadata. Whole-book boxes are not spine measurements.

The room inventory covers shelving, furniture, coffee machines, lamps, framed art, portraits, rugs, electronics and decor using explicit YOLOE prompts. The experimental room image reader is disabled after unreliable batch results. Material and brand stay blank until reviewed from visible evidence or claimant information. Structural negatives (people/doors/windows/walls/floor) are excluded by the detector. Disagreement between the reader and detector stays visible for exclusion rather than silently disappearing. Original art is routed to appraisal unless established as a print.

**Verify identity and visible details** records title, author, publisher, edition, category, material, brand and artwork type with a source. False positives and duplicates can be excluded with a reason; their evidence remains in JSON. Reviewed identity, measurements and offers survive later matching observations. Association uses crop hashes, color histograms, geometric feature matches and title overlap; it is not an independent physical count. Unreadable visually ambiguous copies may need manual duplicate review.

The feature-status panel and HTML show all six assignment areas: capture, books, room contents, measurements, valuation and exports. An implemented form is not evidence that the associated requirement or accuracy threshold has been satisfied. Unknown scale, titles and source prices remain flagged.

Assignment mapping and outstanding acceptance evidence: [docs/assignment-coverage.md](docs/assignment-coverage.md).

Measured model trials and remaining detection limits: [docs/model-validation.md](docs/model-validation.md).

## Logs and checks

`[frontend]` events appear in the Vite terminal (development only) and browser console. `[backend]` events appear in Uvicorn, with UTC timestamps, request/sweep/frame IDs, elapsed times and ten-second waiting messages. Logs omit images, raw model replies and transcript text. The packet retains the actual evidence and stage history. Local model API cost is recorded as zero, excluding hardware/electricity and browser speech service costs.

```bash
cd backend
.venv/bin/python -m unittest discover -s tests -v
cd ../frontend
npm test
npm run test:logging
npm run build
```

Replay a saved frame without overwriting its packet:

```bash
backend/.venv/bin/python backend/tools/recheck_frame.py data/frames/FRAME.jpg data/diagnostics/recheck.json
```

## Evidence still required for submission

Collect an enclosed room with at least 60 books, two shelving units and eight non-book items. Record every human-legible title, 20 hand-measured spines, 15 checked prices, the item list and taped room dimensions **before tuning**. Use `docs/ground-truth-format.md` and run the evaluator across the entire sweep. The evaluator cannot collect physical ground truth, prove a demo is unedited, or establish accuracy from unit tests.

The private shared repository, unedited <=6-minute demo including audio, qualifying real packet/evidence bundle, ground-truth results and measured failure report are not yet produced. Do not submit the old captures or synthetic test results as proof that the assignment pass bars are met.

Replay every saved image into a **new** packet (the original is preserved):

```bash
backend/.venv/bin/python backend/tools/reprocess_sweep.py data/packets/SWEEP_ID.json
```

Replay latency is reported separately from the original capture duration. On an 8 GB Mac the local vision reader can take tens of seconds for a new view; cached repeated objects are faster. A full-room five-minute completion target is not yet demonstrated.


## Submission tools

- **Download packet + evidence ZIP** packages exactly one named `claim_packet.json`, readable HTML, original frames, SHA-256 evidence manifest, and saved ground truth/results when available. Final canonical files also live in `data/claims/<sweep-id>/`.
- **Ground-truth evaluation** accepts manually collected JSON using `docs/ground-truth-format.md`, saves it and reports all pass bars. Missing objects/samples fail rather than disappear.
- **Appraisal policy** sets the threshold in the claim currency. Rare/signed/antiquarian books and originals always require appraisal. Unknown object models need a sourced range (or multiple checked comparables).
- **Record the unedited assignment demo** captures one continuous browser-tab video with shared tab audio and microphone. Enable Share tab audio. It stops at 5:55, preserves the final recording chunk, and downloads locally; it does not edit/stitch a demo. Verify sound and workflow yourself before submission. Browser support varies.
- `GET /api/sweeps/<id>/performance` reports actual per-stage durations and documented cost scope. Provider charges are unknown rather than reported as zero when eBay is configured.

Development uses Vite's same-origin `/api` and `/data` proxies. After `npm run build`, FastAPI serves the built UI at `http://localhost:8000`. For a phone, serve this same origin through trusted HTTPS; localhost refers to the phone itself, so a separate API needs an explicitly configured reachable `VITE_API_URL` and `CORS_ORIGINS`. No insecure camera bypass is used.

No GitHub CLI/account connection is available in this workspace, so a private remote repository has not been created. Secrets, local models and claim evidence are ignored; publish source code to your private repository and share the evidence ZIP separately.


## Conversational voice

Speak normally to ask questions or follow up: “How many books have you seen?”, “Why is that still unreadable?”, “Would moving closer help?” The dialogue model receives the last twelve turns, selected item, current shelf and observed inventory. It cannot write claim facts or prices. Clear scan commands bypass model latency. Interim speech can interrupt spoken output, and scan guidance waits while a conversational reply is active. Unsupported browsers retain the same conversation through typed input. Set `CONVERSATION_MODEL` to an installed Ollama chat model; default `qwen2.5:3b`. If the model is unavailable, the app says so and keeps the sweep working.


## Concise contents exports

The readable report lists books and other named objects. The downloadable `data/claims/<sweep-id>/claim_packet.json` contains only a `materials` array: named books and named room objects, available metadata, status, evidence and measured dimensions. Unknown/unreadable names, uncertain crop checks and conflicting labels are omitted. Proposed readings stay in the internal review inventory. Book titles require OCR/reader agreement and verified book crops; other names require detector/verifier agreement or explicit claimant review. Full inventory, unknown candidates, price sources, review findings and audit data remain in internal `data/packets/<sweep-id>.json` for recovery and evaluation. The evidence ZIP uses the same concise materials JSON.


## Crop verification

Fast YOLO localization is retained. A separate blind Gemma 3 4B vision check receives each usable object crop without its detector label. Automatic names require a clear crop, category agreement and distinguishing visible parts. Small crops, invalid responses, timeouts and conflicts remain unverified. Per-frame work is bounded to 90 seconds; remaining candidates are marked deferred and omitted from identified exports. After capture stops, deferred candidates are checked in the background and exports are refreshed when checks finish. The live inventory shows remaining-check progress. Explicit saved-sweep verification also checks every retained candidate without this aggregate deadline.

Four installed models were compared on six identical saved crops before enabling the 4B verifier. See `data/diagnostics/crop-verification/comparison.json`. The 8B model timed out on all six; the 4B model still made wrong guesses. The added parts-support gate and category agreement rejected those diagnostic false names. These are targeted development regressions, not held-out accuracy results.

```bash
backend/.venv/bin/python backend/tools/compare_crop_models.py --cases data/diagnostics/crop-verification/cases.json --output /tmp/crop-comparison.json --models qwen2.5vl:3b gemma3:latest --timeout 60
backend/.venv/bin/python backend/tools/verify_saved_sweep.py SWEEP_ID --model gemma3:latest
```

Verification preserves reviewed facts and saves the original internal packet under `data/diagnostics/crop-verification/` before updating report and materials exports. Restart the backend after changing `backend/model_choices.env`.
