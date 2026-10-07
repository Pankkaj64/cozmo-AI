# Library Contents Claim Agent

A live voice-and-vision insurance agent that inventories and values a home library in one
continuous camera sweep. The policyholder walks the room once while talking to the agent; the
agent directs the capture, the inventory fills in live, and background stages finish
identification, measurement and sourced pricing. Each sweep ends with `claim_packet.json`
(the brief's exact output contract) and a readable HTML report built from it.

Everything runs locally on a laptop: FastAPI + Python stages, local YOLO detectors,
PaddleOCR (or EasyOCR) for spine text, small Ollama vision/chat models (Gemma 3 4B, Qwen3-VL 2B,
Qwen2.5 3B), and a very plain
React page for the camera, voice and review. Prices come only from retrievable sources
(eBay Browse, Google Books, ECB/ER-API FX) and totals are computed in code.

## Run it (about 10 minutes, first model downloads excluded)

Requirements: Python 3.11–3.13, Node 20+, [Ollama](https://ollama.com), a webcam/phone
camera and a browser with speech support (Chrome or Edge). Works on macOS, Linux and
Windows: no Apple-only components.

```bash
# 1. local language/vision models (~5 GB total)
ollama pull gemma3:latest     # reads the visible title of each book crop (4B)
ollama pull qwen3-vl:2b       # blind check of each crop's category (2B)
ollama pull qwen2.5:3b        # spoken conversation with the claimant

# 2. backend (use the same Python you will start uvicorn with)
cd backend
python3 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt                          # torch/ultralytics/paddle are large
python tools/setup_detector.py                           # downloads YOLO weights, builds the room detector
cp .env.example .env                                     # optional: add eBay / Google Books credentials
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

# 3. frontend (second terminal)
cd frontend && npm install && npm run dev
```

Open http://localhost:5173, pick the country (currency follows), press **Start sweep**,
allow camera and microphone. `GET http://127.0.0.1:8000/api/health` lists the models the
backend will use; a frame whose guidance says "I could not analyse that view" means a model
or package is missing in the interpreter that runs uvicorn. If PaddlePaddle refuses to install on your platform, run
`pip install easyocr` and set `OCR_ENGINE=easyocr` in `backend/.env`.

Tests and lint (no models or network needed):

```bash
cd backend && pip install -r requirements-dev.txt && pytest -q && ruff check app tests tools
```

## How a sweep works

1. **Greeting and locale.** The agent greets, confirms country and currency (a runtime
   setting, see `/api/sweeps/locales`) and explains the sweep in two sentences.
2. **One continuous pass.** The browser samples a JPEG every 4.5 s and posts it to
   `/api/sweeps/{id}/frames`. Each frame is saved as evidence (`data/frames/…`), then:
   detection → crop OCR → title proposal → blind crop check → independent count check.
3. **The agent talks back.** Guidance (blur, glare, low light, unreadable spines, "move to the
   next shelf", "is that portrait an original or a print?") is spoken as it changes. The
   claimant can interrupt: speech is transcribed and sent to `/turns`. Explicit commands
   ("next shelf", "skip this shelf, those are not mine", "that is a first edition",
   "title is …") run deterministic tools and are stored as evidence; open questions go to
   the local conversation model, which sees the live inventory but cannot change facts.
4. **Live inventory.** The packet streams over Server-Sent Events: books detected,
   identified, unreadable; items; review count; price when available.
5. **Background agents.** After capture stops: deferred crop checks, catalogue lookup,
   price research for every identified line, FX evidence, deterministic valuation and
   validation.
6. **Packet.** `Stop sweep → build packet` runs the background stages, writes
   `data/claims/<id>/claim_packet.json` and `report.html`, records time-to-packet, and the
   agent reads back a summary assembled in code from the totals. `/bundle` zips the packet,
   report, referenced frames and a SHA-256 manifest.

## Repository layout

```
backend/app
  config.py              paths, model names, room classes, API endpoints, env settings (.env first, defaults here)
  main.py                FastAPI app, middleware, health, static evidence
  routes.py              every HTTP route (transport only)
  logic/
    state.py             sweep store, checkpoints, finish/export, background research and deferred checks
    perception.py        capture quality, YOLO detection, crop OCR, title proposals, independent count
    ocr.py               PaddleOCR / EasyOCR behind one function
    crop_verifier.py     blind crop category check (Gemma 3 via Ollama)
    tracking.py          appearance + scene alignment across frames
    identification.py    evidence rules for accepting a title; Open Library work/ISBN lookup
    measurement.py       reference-scale spines, shelf-width scale, room areas
    pricing.py           quote models, matching, locale table, appraisal rules, deterministic valuation
    providers.py         eBay, Google Books, FX evidence, research, second-country comparison
    claim.py             schemas, totals in code, validation, output contract
    report.py            HTML report and evidence bundle
    workflow.py          merge frames into the inventory; measurement → pricing → validation; guidance
    conversation.py      spoken tools (next shelf, skip, first edition, room size, shelf width, compare) + chat model
    evaluation.py        pass-bar scoring against ground truth
    utils.py             logging, stage timing, performance summary
backend/tools            setup_detector.py, evaluate.py, reprocess_sweep.py
frontend/src             App.tsx (camera, voice, live inventory, finish → report), components/{Inventory,Conversation}.tsx
docs/                    architecture.md, failure-log.md, assignment-coverage.md, ground-truth-format.md
data/                    frames/, packets/ (internal state), claims/<id>/ (deliverables) — git-ignored
```

## Measurement and metric scale

Metric scale never comes from a model. During the sweep you tell the agent a known
dimension and it becomes the scale for the saved frame it refers to:

* **"The shelf is 90 centimetres wide."** The span of the detected shelf (or the detected
  book row) in the latest frame of the current shelf is set to 90 cm; the books standing on
  it, viewed front-on, show their spine face, so their boxes become height × thickness in
  cm (`measurement.calibrate_from_shelf`, method `shelf_width`, stored on each book).
* **"The room is 4.2 by 3.1 metres, ceiling 2.6."** Floor, gross wall and shelved-wall
  areas in m² and ft² are computed from those figures against the current frame
  (`scale_method: known_dimensions`, source recorded). An unstated ceiling is assumed at
  2.4 m and flagged.

The same measurements can be posted to `/calibration`, `/spine-bounds`, `/room` and
`/item-measurement` with explicit pixel points (used by the tests). Shelf run is the sum of
measured thicknesses. Unknown measurements stay `null` and are queued for review.

## Pricing (the part that must not be invented)

* `pricing/providers/` retrieve candidate **quotes** with source, URL, retrieval date,
  condition, currency and a title/author match score:
  **eBay Browse** (new listings → replacement, used listings → used value; median of the
  matching listings, every comparable kept; tokens fetched from client credentials) and
  **Google Books** (country-specific list prices; ebook prices are recorded but rejected as
  physical replacement unless `PRICING_ALLOW_EBOOK_PROXY=true`).
* `pricing/fx.py` records dated **FX evidence** (Frankfurter/ECB, then ExchangeRate-API) so a
  quote from another market can be converted and labelled `converted: true` with the rate,
  URL and date.
* `pricing/valuation.py` is **deterministic**: operator-checked offers outrank provider
  quotes, local quotes outrank converted ones, weak matches and ebooks are rejected with a
  reason, signed/rare/first editions and original art are never priced, and anything at or
  above the configurable appraisal threshold goes to `needs_appraisal`. Every decision is
  kept in `price_details` (chosen quote, candidates, rejections) and exposed at
  `GET /api/sweeps/{id}/prices`.
* Saying **"compare prices in the United Kingdom"** (or `POST /api/sweeps/{id}/compare-locale`)
  re-prices the same ten identified books for a second country to show locale is a
  setting; the table is in the report and `locale_comparison`.
* Lines without a retrievable quote stay blank, are excluded from totals and appear in the
  review queue with the reason. Totals are summed in `claim/totals.py`.

Without eBay credentials most books will have no price: that is reported honestly rather
than filled in.

## Output contract

`claim/contract.py` emits the brief's structure with the required keys first and in order:
`sweep`, `room`, `books`, `items`, `totals`, `review_queue`; extra fields (publisher,
ft² areas, `price_details`, `locale_comparison`, `evidence_frames`, `performance`, `cost`,
`methodology`) follow. Unknown numbers are `null`, unknown text is `""`. Every `frame_ref`
is a saved JPEG under `data/frames/`. Validation (`claim/validation.py`) is rule-based:
confidence thresholds, price source/URL/date present, currency matches, converted prices
carry FX evidence, implausible dimensions, duplicates, missing fields.

## What was kept, changed and discarded from the reference app

The [Insurance Claim Live Agent Team](https://github.com/Shubhamsaboo/awesome-llm-apps/tree/main/voice_ai_agents/insurance_claim_live_agent_team)
was read and its FastAPI app run locally (UI, health and session endpoints answered; the
Gemini Live conversation itself needs a Gemini key and was not exercised).

* **Kept:** the interaction pattern — one live session where the claimant talks, the camera
  is on and a claim file builds itself in real time; server-owned state; background agents
  that finish the packet; role-labelled transcript; evidence links.
* **Changed:** Gemini Live replaced by local models and browser speech (any provider would
  do); narrative damage descriptions replaced by measured, counted and priced inventory;
  one long prompt replaced by separate testable stages (detection, OCR, identification,
  measurement, pricing, validation, packet); prices from retrievable sources instead of a
  model; totals in code.
* **Discarded:** policy lookup, coverage decisions, avatar generation and insurer submission.

## Honest status

The pipeline, contract, pricing and evaluation are implemented and unit-tested (78 tests).
The 60-book ground-truth sweep, tape measurements, 15 hand-checked prices, the unedited demo
video and the measured results sheet still have to be collected in a real room; see
`docs/assignment-coverage.md` and `docs/failure-log.md` for what has and has not been
demonstrated. Local inference on an 8 GB laptop is slow (tens of seconds per dense frame);
the five-minute time-to-packet is recorded per sweep, not assumed.
