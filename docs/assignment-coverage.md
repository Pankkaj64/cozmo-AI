# Assignment coverage

Source: Take-Home Assignment: Library Contents Claim Agent (received 6 October 2026).
This maps the brief to the implementation and states what still needs real-room evidence.

| Requirement | Implemented | Still needed |
| --- | --- | --- |
| Live sweep agent: voice in/out, camera, directs capture, live inventory, voice corrections | Browser camera + Web Speech; guidance spoken as it changes (blur, glare, dark, unreadable, next shelf, art question); SSE live inventory; explicit tools + contextual chat model | One-take demo video with audible agent |
| Books from the spine: detect, count, read, resolve, measure, unidentified when unreadable | YOLO26s + YOLO11s boxes, PaddleOCR/EasyOCR crops, Qwen-VL title proposal, Gemma crop check, exact-agreement identity gate, Open Library work/ISBN; reference-scale spine cm and shelf run; blanks stay blank | 60-book ground truth: count ±5 %, ≥70 % legible titles, ≤3 % confidently wrong, 20 spines ±15 % |
| Book valuation: replacement + used, sourced/dated/conditioned, converted labelled, none → excluded, rare → appraisal, 10 books in a second country | `pricing/` providers (eBay, Google Books), FX evidence, deterministic valuation with `price_details`, appraisal rules, `/compare-locale` | eBay credentials for real quotes; 15 hand-checked prices ±25 % |
| Non-book contents: detect/classify, dimensions, material/brand, sourced price or range, art → appraisal unless print | YOLOE room categories, crop verification, operator dimensions with source, eBay item range (≥2 comparables when brand unknown), art question + `is_print` | ≥80 % of 8+ items with correct category |
| Room surface area: floor, walls, shelved wall, m² and ft², non-rectangular shape, same sweep | `measurement/geometry.py` rectangle/polygon, scale method + confidence stored | Tape-measured room: floor ±10 %, walls ±15 % |
| Claim packet: contract JSON + readable report, review queue, totals from lines, traceable | `claim/contract.py`, `claim/report.py`, `claim/totals.py`, `/bundle` with SHA-256 manifest | Packet from the demo sweep |
| Ground truth and evaluation | `docs/ground-truth-format.md`, `tools/evaluate.py`, `/evaluate` endpoint scoring every pass bar | Collected sheet and results |
| Cost and latency | Per-stage timings, `time_to_packet_s`, provider call counts | Numbers from the demo sweep |
| README, `.env.example`, no secrets | Yes | Private repo shared |

Nothing in this table claims a pass bar has been met. Unit tests exercise the rules with
synthetic frames and mocked providers; accuracy can only come from the collected ground truth.
