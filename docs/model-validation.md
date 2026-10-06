# Current profile update

The historical measurements below describe the earlier text-role pipeline. The active profile is now YOLO26s + YOLO11s book boxes, YOLOE26s room candidates, native crop OCR and Qwen2.5-VL 3B cropped title proposals. The separate text-role model and experimental room VLM reader are disabled on this 8 GB machine.

A complete replay of all 29 frames from the latest recording took **88.87 seconds**, retaining **three proposed titles** through the final no-book view: Extreme Ownership, 8 Rules of Love, and रावण. All were lost by the original latest-view replacement logic. A follow-up exact-OCR-choice pass recovered author proposals (including incomplete English names and अमीश); publisher remains unknown. A bestseller slogan proposed as publisher was rejected. The 88.87 s excludes this follow-up role pass. Room-category accuracy and duplicate association still require review; room candidates are not a verified item count. See [assignment coverage](assignment-coverage.md) and the current commented model profile.

---

# Selected local model profile

6 October 2026 — Apple M1, 8 GB RAM, Ollama 0.23.2. Active defaults and all considered alternatives with reasons are in `backend/model_choices.env`. `.env` and shell variables override those defaults.

The selected pipeline uses **YOLO26s** for object localization, native **Apple Vision** for crop OCR, **Qwen2.5 3B** for title/author line proposals and **YOLO11s** for a second box/count comparison. Images stay local. Ultralytics sync is disabled. A matching count is insufficient: each book must also match a distinct secondary box with intersection-over-union of at least 0.5. The models share COCO categories and can share mistakes.

## Recorded checks

| Capture | Time including OCR and checks | Primary / second detector | Result |
| --- | ---: | ---: | --- |
| Held Hindi book and foreground books (`c728…jpg`) | 4.597 s, warm detectors | 3 / 3, all boxes matched | Proposed रावण / अमीश from that crop; two books touch the frame edge and remain review candidates |
| Foreground book on sofa (`f58e…jpg`) | 2.826 s, warm detectors | 1 / 1 | Book localized; distorted OCR did not establish its title |
| Room view without a visible book (`1e7d…jpg`) | 2.946 s, including cold detector imports | 0 / 0 | No book candidates |
| Synthetic blank white negative control | 0.256 s, warm detectors | 0 / 0 | No invented book |

These are single-run timings on restored evidence, not universal performance or accuracy claims. The earlier generative replay took 430.235 s before its later token-limit adjustments; this is a historical baseline, not a controlled comparison against the final legacy configuration. Detailed outputs: `data/diagnostics/selected-model-benchmark.json` and `*.selected.json`.

YOLO26m had lower confidence on the held book and classified the foreground book as a laptop. YOLO11s produced overlapping book/keyboard labels, so it is used for secondary comparison instead of the primary inventory. Gemma 3 1B produced invalid OCR indices. Qwen2.5 1.5B abstained on readable text in the real batch; 3B correctly proposed the Hindi title/author and the English test title, but omitted one coauthor in the English text test. Both require review. Qwen3.5 2B could not be downloaded with the installed Ollama runtime (HTTP 412); it was not benchmarked.

Moondream's count check returned one book for the blank white image and disagreed on the saved frames. It is no longer the default validator. Its legacy implementation remains available for reproducing the earlier tests. Native OCR enlargement/deskew trials did not reliably recover the slanted English cover; those transforms were not added to the default path.

## Behavior and limits

- Detection failure reports a failure, never a reliable zero. OCR/role selection failure retains the object boxes.
- Count disagreement and edge crops retain candidates with review flags. Candidate counts are not confirmed book identities.
- Title/author proposals contain only text selected from that object's crop. Invalid, repeated, out-of-range and overlapping line indices are rejected. Text errors and missing coauthors remain possible.
- The last analyzed image shows the corresponding boxes; boxes are not painted over a different live camera frame.
- Whole-book boxes cannot supply spine thickness or height. Measurements require operator-marked spine bounds and a same-frame calibration.
- COCO object detection does not cover all artwork or household objects. Dense shelves can be missed or grouped. Keep a complete shelf section visible, move closer, and change the shelf label for each new section.
- Forty backend regression tests cover the workflow and the new retention, role-validation, overlapping-detection and measurement safeguards. Frontend capture/recording tests and the production build pass. These tests do not establish a room-level accuracy score.

The 60-book ground-truth evaluation, physical measurements, sourced valuations and one-take demo still require real collection. No synthetic test is presented as that evidence.

Official model references: [YOLO26](https://docs.ultralytics.com/models/yolo26/), [Qwen2.5 3B](https://ollama.com/library/qwen2.5:3b). Distribution must respect the model/software licenses: Ultralytics AGPL-3.0/Enterprise and Qwen 3B's Qwen license.


### 2026-10-06: blind crop-verification diagnostic

The existing detector remains the fast localization stage. Six identical saved crops from sweep `b1ddd62a-e508-4485-871f-62c91cdafe1c` were replayed through four installed vision models with the same prompt, structured schema and image hashes. No detector label is supplied to the verifier.

| Model | Valid replies | Correct names accepted by final gate | Wrong names accepted by final gate | Category matches to diagnostic labels |
| --- | ---: | ---: | ---: | ---: |
| Qwen2.5-VL 3B | 6 | 0 | 0 | 3 |
| Qwen3-VL 2B | 4 | 0 | 0 | 2 |
| Qwen3-VL 8B | 0 | 0 | 0 | 0 |
| Gemma 3 4B | 6 | 1 | 0 | 1 |

The 8B requests each timed out after 60 seconds. The 2B model truncated two structured answers. Gemma 4B accepted the TV, but proposed wrong categories elsewhere. Its proposed plant-pot label described only a circular shape/dark colour although no pot was visible. A distinguishing-parts gate now rejects such generic explanations; it was developed after inspecting these outputs and applied uniformly to all saved replies. The larger model is enabled as a separate conservative verifier, not presented as generally more accurate. OCR/reader agreement remains necessary for book identity.

This is a small targeted development diagnostic, not an independent holdout test or qualifying whole-room benchmark. Model agreement can still be wrong. Saved raw replies, crop hashes, labels, errors and limits are in `data/diagnostics/crop-verification/comparison.json`; completed latency excludes the initial runner's timed-out calls. Identified reports omit detector-only, uncertain and conflicting names.
