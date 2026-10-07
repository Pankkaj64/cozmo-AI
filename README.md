# Library Contents Claim Agent

A live voice-and-vision insurance agent that inventories and values a home library in one
continuous camera sweep. The policyholder walks the room once while talking to the agent;
the agent directs the capture, the inventory fills in live, and background stages finish
identification, measurement and sourced pricing. Each sweep ends with `claim_packet.json`,
a readable `report.html` and an evidence bundle.

Everything runs locally: FastAPI + Python stages, local YOLO detectors, PaddleOCR for spine
text, small Ollama vision/chat models (Gemma 3 4B, Qwen3-VL 2B, Qwen2.5 3B) and a very plain
React page for the camera and voice. Prices come only from retrievable sources (eBay Browse,
Google Books, ECB/ER-API exchange rates) and totals are computed in code.

Two guides explain the system: [`docs/how-it-works.md`](docs/how-it-works.md) in plain
language, step by step, and [`docs/technical-guide.md`](docs/technical-guide.md) with the
files, functions, objects and libraries at every step.

## Install and run

Requirements: Python 3.11–3.13, Node 20+, [Ollama](https://ollama.com), a webcam or phone
camera and a browser with speech support (Chrome or Edge). macOS, Linux and Windows.

```bash
# 1. local language/vision models (about 7 GB in total)
ollama pull gemma3:latest     # reads the visible title of each book crop (4B)
ollama pull qwen3-vl:2b       # blind check of each crop's category (2B)
ollama pull qwen2.5:3b        # spoken conversation with the claimant

# 2. backend: install into the same Python you will start uvicorn with
cd backend
python3 -m venv venv && source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt                          # torch, ultralytics, PaddleOCR: large downloads
python tools/setup_detector.py                           # downloads YOLO weights, builds the room detector
cp .env.example .env                                     # optional: eBay / Google Books credentials
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

# 3. frontend (second terminal)
cd frontend && npm install && npm run dev
```

Open http://localhost:5173, pick the country (currency follows), press **Start sweep** and
allow the camera and microphone. `GET http://127.0.0.1:8000/api/health` lists the models the
backend will use. If a frame's guidance says "I could not analyse that view", a package or
model is missing in the interpreter that runs uvicorn.

Formatting and lint: `cd backend && ruff check app tools && ruff format app tools`.

## Configuration

All settings are read from `backend/.env` first and fall back to the defaults in
`backend/app/config.py`. `backend/model_choices.env` holds the model profile and is read
after `.env`. Every variable is documented in `backend/.env.example`: model names, Ollama
URL, OCR engine (`paddle` or `easyocr`), price-source credentials, endpoint overrides,
pricing rules and CORS.

## How a sweep works

1. **Greeting and locale.** The agent greets, confirms country and currency (a runtime
   setting from `/api/sweeps/locales`) and explains the sweep in two sentences.
2. **One continuous pass.** The browser samples a JPEG every 2 s (one upload at a time) and
   posts it to `/api/sweeps/{id}/frames`. Each frame is saved as evidence, then:
   detection → crop OCR → title proposal → blind crop check → second-detector count check.
3. **The agent talks back.** Guidance (blur, glare, low light, unreadable spines, "move to
   the next shelf", "is that portrait an original or a print?") is spoken as it changes.
   Explicit commands ("next shelf", "skip this shelf, those are not mine", "that is a first
   edition", "the shelf is 90 centimetres wide", "the room is 4.2 by 3.1 metres", "compare
   prices in the United Kingdom") run deterministic tools; open questions go to the local
   chat model, which sees the live inventory but cannot change facts.
4. **Live inventory.** The packet streams over Server-Sent Events: books detected,
   identified, unreadable; items; review count.
5. **Stop sweep → build packet.** Deferred crop checks, catalogue lookup, price research for
   every identified line, exchange-rate evidence, deterministic valuation and validation.
   `data/claims/<id>/claim_packet.json` and `report.html` are written, the time to packet is
   recorded, and the agent reads back a summary assembled in code from the totals.
   `/bundle` zips the packet, report, referenced frames and a SHA-256 manifest.

## Repository layout

```
backend/app
  config.py              paths, model names, room classes, API endpoints, settings (.env first)
  main.py                FastAPI app, middleware, health, startup warm-up, static evidence
  routes.py              every HTTP route (transport only)
  logic/
    state.py             sweep store, checkpoints, finish/export, background research
    perception.py        capture quality, YOLO detection, crop OCR, title proposals, count check
    ocr.py               PaddleOCR / EasyOCR behind one function
    crop_verifier.py     blind crop category check (Qwen3-VL via Ollama)
    tracking.py          appearance + scene alignment across frames
    identification.py    rules for accepting a title; Open Library work/ISBN lookup
    measurement.py       reference-scale spines, shelf-width scale, room areas
    pricing.py           quote models, matching, locale table, appraisal rules, valuation
    providers.py         eBay, Google Books, FX evidence, research, second-country comparison
    claim.py             schemas, totals in code, validation, output contract
    report.py            HTML report and evidence bundle
    workflow.py          merge frames into the inventory; measurement → pricing → validation
    conversation.py      spoken tools + chat model
    evaluation.py        scoring against a ground-truth file
    utils.py             logging, stage timing, performance summary
backend/tools            setup_detector.py, evaluate.py, reprocess_sweep.py
backend/.runtime         downloaded model weights (created by setup_detector.py, git-ignored)
frontend/src             App.tsx, api.ts, voice.ts, types.ts, components/{Inventory,Conversation}.tsx
docs/                    how-it-works.md, technical-guide.md
data/                    frames/, packets/ (internal state), claims/<id>/ (deliverables); git-ignored
```

## Which model does what

| Stage | Component | Role | Can it write a fact? |
| --- | --- | --- | --- |
| Capture quality | Pillow heuristics | blur, glare, darkness → spoken guidance | no |
| Book / object boxes | YOLO26s + YOLO11s (COCO) and YOLOE (room classes in `config.py`) | boxes; every detector that drew a box is recorded | boxes only |
| Spine text | PaddleOCR | text lines with confidence, best of up to three rotations | OCR evidence |
| Title proposal | Gemma 3 4B via Ollama | reads the visible title; author/publisher only from OCR lines | proposal only |
| Blind crop check | Qwen3-VL 2B via Ollama | names the object without the detector label | agree / conflict |
| Identity | `logic/identification.py` (rules) | accepts a title only when OCR, reader, crop check and a second detector agree | **yes** |
| Conversation | Qwen2.5 3B via Ollama | answers questions from the live state; commands bypass it | no |
| Prices | eBay Browse, Google Books, Frankfurter/ER-API | dated quotes with URLs; FX evidence | quotes |
| Valuation, totals | `logic/pricing.py`, `logic/claim.py` (code) | rules, rejections with reasons, sums | **yes** |

These were chosen by measurement on an 8 GB Apple-silicon laptop: Qwen2.5-VL 3B cannot load
there (Metal needs a 6.6 GB buffer); Gemma 3 read 5/5 test titles; Qwen3-VL 2B agreed on 5/6
real spine crops where Gemma called them "table"; PaddleOCR read real spine fragments where
EasyOCR read nothing; the YOLOE book boxes found 18–51 spines per shelf wall where the COCO
models found 1–17. Qwen3-VL only answers when the prompt ends with `/no_think`.

## Measurement and metric scale

Metric scale never comes from a model. "The shelf is 90 centimetres wide" sets the span of
the detected shelf in the latest frame to 90 cm, and the front-on book boxes become height ×
thickness in cm. "The room is 4.2 by 3.1 metres, ceiling 2.6" records floor, gross wall and
shelved-wall areas against the current frame (an unstated ceiling is assumed at 2.4 m and
flagged). The same values can be posted to `/calibration`, `/spine-bounds`, `/room` and
`/item-measurement` with explicit pixel points. Unknown measurements stay `null`.

## Pricing

Providers return quotes with source, URL, retrieval date, condition, currency and a
title/author match score. Valuation is deterministic: operator-checked offers outrank
provider quotes, local quotes outrank converted ones, weak matches and ebooks are rejected
with a reason, signed/rare/first editions and original art are never priced automatically,
and anything at or above the appraisal threshold goes to `needs_appraisal`. Every decision
is kept in `price_details` (`GET /api/sweeps/{id}/prices`). Lines without a retrievable
quote stay blank, are excluded from totals and appear in the review queue. Without eBay
credentials most books will have no price; Google Books without an API key shares a daily
quota and may answer HTTP 429.

## Output contract

`claim_packet.json` has the required keys first and in order: `sweep`, `room`, `books`,
`items`, `totals`, `review_queue`; extra fields (`price_details`, `locale_comparison`,
`evidence_frames`, `performance`, `cost`, `methodology`) follow. Unknown numbers are `null`,
unknown text is `""`. Every `frame_ref` is a saved JPEG under `data/frames/`.

## Evaluation

`POST /api/sweeps/{id}/evaluate` and `python tools/evaluate.py <packet> <truth.json>` score a
packet against a ground-truth JSON file: a list of `{"title", "author", "shelf",
"spine_height_cm", "spine_thickness_cm", "replacement_cost"}` objects, one per book, plus
optional items. The score reports identification precision/recall, measurement error and
price error per line.

## Known limits

- An 8 GB laptop needs about 3 minutes for a frame with ten readable books (two model reads
  per book); empty views take about 3 seconds. Time to packet is recorded per sweep.
- Only what is in a sampled frame is seen; the agent asks the claimant to pause at each
  shelf. Dense shelves of very small spines are detected but rarely identified.
- The 60-book ground-truth sweep, tape measurements, hand-checked prices and the demo video
  still have to be recorded in a real room.
