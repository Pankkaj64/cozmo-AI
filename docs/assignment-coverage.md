# Assignment coverage and remaining acceptance evidence

Source: Take-Home Assignment: Library Contents Claim Agent, supplied 6 October 2026.
This maps implementation to the brief; it is not a pass certificate.

| Requirement | Implemented surface | Evidence still needed |
| --- | --- | --- |
| Continuous camera, voice, live inventory, corrections | Camera preview; video-only evidence; queued JPEG analysis; browser speech/typed corrections; stage/terminal logs | One-take demo including agent audio; speech support depends on browser; no camera-only metric inference |
| Every book including horizontal stacks; title/author/publisher/edition | Two book detectors, crop OCR, local title reader, persistent tracks, historical boxes, proposed/confirmed fields, correction/exclusion form | 60-book ground truth; count ±5%; ≥70% human-legible title recall and ≤3% confidently wrong; publisher/edition unknown unless visible/sourced |
| Spine dimensions and shelf run | Saved-frame reference calibration + marked spine bounds; centimetres and summed metres | 20 tape-measured spines and ±15% comparison; whole-cover boxes cannot supply spine thickness |
| New/equivalent and used local prices | Source/date/condition/match forms; optional Open Library/eBay lookup; explicit FX; appraisal rules | Provider configuration or checked source URLs; 15 benchmark prices; ten books in a second country; no fabricated values |
| Room contents | YOLOE prompts for shelving/furniture/coffee machines/lamps/art/rugs/electronics/decor; room candidate list/boxes; material/brand/dimension fields; sourced ranges | Category and duplicate review; ≥80% correct against eight-item ground truth. This local model still confuses TV/mirror and partial objects |
| Floor, walls and shelving areas | Known dimensions or measured polygon, ceiling height, shelving rectangles; m² and ft² | Same-sweep metric source and tape comparison (floor ±10%, walls ±15%); no guessed scale |
| Reviewable JSON + HTML/PDF | Deterministic subtotals; unknowns excluded; frame/source links; proposals and review queue; HTML print-to-PDF | Review all lines; qualifying final packet and evidence bundle; PDF is browser print output |
| Submission/evaluation | Evaluator, ground-truth format, failure log, architecture, per-stage latency/cost | Private shared repository, qualifying room recording, independent physical measurements and sources |

The supplied 29-frame recording demonstrates three different held books rather
than the required 60-book continuous shelf sweep. It can verify the regression
(retaining three titles after an empty frame), but not the assignment's pass bars.
The repaired app keeps books on shelves during the intended capture workflow;
there is no instruction to remove each book or scan barcodes.

Room detection outputs are candidates, not a verified physical-item count.
Repeated appearances are matched using crop features and scene alignment; heavily
occluded or low-texture views may create duplicates. Verify boxes and use the
exclusion control. Category errors remain a measured limitation, not a completed
accuracy fix. The experimental VLM room-detail reader is disabled because its
batch classifications and latency were worse than retaining detector proposals.


## Implementation additions (6 October 2026)

Delivered: separately testable detection/OCR/identity/measurement/pricing/validation stages; exact strong crop-OCR/image agreement identification; checksum-valid visible ISBN extraction and optional source-backed ISBN metadata; separate new/used market attempts; optional live research and SSE inventory updates; sourced ranges for unknown object models; configurable appraisal threshold; traceability/duplicate/currency/FX validation; canonical per-sweep claim files; portable evidence ZIP with hashes; ground-truth/evaluation API and UI; stage performance endpoint; same-origin UI/API serving; unedited screen/tab-audio/microphone recorder capped at 5:55.

Verification is automated regression coverage, not physical acceptance evidence. The recorder requires shared tab audio and has not been used to produce a qualifying real-room demo. Metric geometry remains an assisted reference workflow, not autonomous monocular room reconstruction. External source candidates need credentials and physical-match review. No 60-book ground truth, accuracy pass, ten-book second-market demonstration or private GitHub remote has been fabricated.
