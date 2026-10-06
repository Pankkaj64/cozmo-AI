# Historical review (superseded model configuration)

For the current detector, measured timings and remaining limitations, see [model-validation.md](model-validation.md). This earlier review is retained as a failure record.

# Latest saved record review

Record: `594aaf58-1ff2-4fa2-8ae2-b594f4bc103a` (6 October 2026).

The original record contains eight blank book entries with zero identification confidence. Those entries were created from repeated generic model output, not eight distinct supported observations. Only one 640×480 frame was processed during the 90-second sweep.

The saved frame is blurred and the stack is cut off by the bottom edge. Reprocessing with Qwen2.5-VL 3B gives a count of three; the independent Moondream check gives two. Apple Vision OCR runs successfully but reads no text. The correct result for this evidence is **count unverified; another capture required**. Neither count should be treated as ground truth.

The original packet remains unchanged. The replay result is saved in `data/diagnostics/latest-detection.json`.

## Changes

- Removed the conversion of generic book-group entries into individual books.
- Split counting from title reading. Counts must agree across the two local models before creating book candidates. Generated titles require matching local OCR evidence.
- Added local Apple Vision OCR for macOS and retained PaddleOCR support elsewhere.
- Requested up to 1920×1080 camera input with higher JPEG quality; actual resolution depends on the camera.
- Kept the newest waiting frame during slow inference and captured the final view before finishing.
- Updated each shelf's latest inventory instead of summing repeated views of the same books. Use a different shelf label for a new section.
- Exposed count disagreement and an unverified count in the UI/report, rather than a misleading zero.
- Preserved household-item capture and saved model responses for diagnosis.

## Verification and limits

Thirteen backend regression tests, frontend capture/queue checks, and the production build pass. Real replay of the latest frame correctly requests review because the two models disagree. A replay of the earlier frame without visible books also produced disagreeing model counts and was withheld from the inventory. These local models still make visual mistakes; model agreement alone is not a measured accuracy guarantee.

Restart the backend and reload the frontend. For the next capture, point directly at the books, include the whole stack, move closer, and hold still. A new clear frame is needed to validate detection accuracy and readable titles; the current saved image cannot establish either.
