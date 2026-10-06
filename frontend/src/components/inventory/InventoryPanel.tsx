import type { Packet } from "../../types/packet";
import { API } from "../../lib/api";

type EvidenceObject = {
  id: string;
  frame_ref: string;
  object_bbox?: number[];
  label: string;
  kind: "book" | "item";
};

type Props = {
  packet: Packet | null;
  currency: string;
  selected: string;
  onSelect: (id: string) => void;
  processing: boolean;
};

export function InventoryPanel({
  packet,
  currency,
  selected,
  onSelect,
  processing,
}: Props) {
  const latestFrame = packet?.frames?.at(-1);
  const evidenceLines: EvidenceObject[] = [
    ...(packet?.books || []).map((book) => ({
      id: book.id,
      frame_ref: book.frame_ref,
      object_bbox: book.object_bbox,
      label: book.title || book.proposed_title || "Unidentified book",
      kind: "book" as const,
    })),
    ...(packet?.items || []).map((item) => ({
      id: item.id,
      frame_ref: item.frame_ref,
      object_bbox: item.object_bbox,
      label: item.category,
      kind: "item" as const,
    })),
  ];
  const evidenceRef =
    evidenceLines.find((line) => line.id === selected)?.frame_ref ||
    latestFrame?.frame_ref;
  const evidenceObjects = evidenceLines.filter(
    (line) => line.frame_ref === evidenceRef && line.object_bbox,
  );
  const countUncertain = Boolean(
    latestFrame &&
    (latestFrame.vision_status !== "ok" ||
      latestFrame.count_status === "needs_review"),
  );

  return (
    <div className="inventory card">
      <div className="section-title">
        <h3>Live inventory</h3>
        <span>
          {countUncertain
            ? "Latest count needs review"
            : `${packet?.books.length || 0} candidate books`}
        </span>
      </div>
      <div className="stats inventory-stats">
        <div>
          <b>
            {countUncertain && !packet?.books.length
              ? "?"
              : packet?.totals?.book_count || 0}
          </b>
          <small>Books across this sweep</small>
        </div>
        <div>
          <b>{packet?.totals?.books_identified || 0}</b>
          <small>Titles reviewed</small>
        </div>
        <div>
          <b>{packet?.items.length || 0}</b>
          <small>Room-item candidates</small>
        </div>
        <div>
          <b>{packet?.review_queue.length || 0}</b>
          <small>Review flags</small>
        </div>
      </div>
      {countUncertain && latestFrame?.primary_count !== undefined && (
        <p className="message">
          Located {latestFrame.candidate_count ?? latestFrame.primary_count}{" "}
          candidates; primary detector: {latestFrame.primary_count}; second
          detector: {latestFrame.validation?.count ?? "unavailable"}. Edge crops
          or count disagreement need review. Keep the complete books in view.
        </p>
      )}
      {!!latestFrame?.ocr_text?.length && (
        <p className="message">
          Text read in this frame: {latestFrame.ocr_text.join(" · ")}. A text
          reading alone does not identify its book.
        </p>
      )}
      {evidenceRef && evidenceObjects.length > 0 && (
        <figure className="detection-evidence">
          <div className="detection-image">
            <img
              src={`${API}/${evidenceRef}`}
              alt="Saved evidence with book and room-item candidate boxes"
            />
            {evidenceObjects.map((line, index) => {
              const [left, top, right, bottom] = line.object_bbox!;
              return (
                <button
                  key={line.id}
                  className={`detection-box ${line.kind === "item" ? "item-box" : ""} ${selected === line.id ? "selected" : ""}`}
                  style={{
                    left: `${left * 100}%`,
                    top: `${top * 100}%`,
                    width: `${(right - left) * 100}%`,
                    height: `${(bottom - top) * 100}%`,
                  }}
                  onClick={() => onSelect(line.id)}
                  aria-label={`Review ${line.kind}: ${line.label}`}
                >
                  <span>
                    {index + 1} · {line.kind === "item" ? line.label : "Book"}
                  </span>
                </button>
              );
            })}
          </div>
          <figcaption>
            {selected
              ? "Evidence for selected inventory line"
              : "Latest analyzed view"}{" "}
            · Yellow: books. Blue: room items. Select any saved line below to
            revisit its evidence.
          </figcaption>
        </figure>
      )}
      <div className="book-list">
        {packet?.books.length ? (
          packet.books
            .slice()
            .reverse()
            .map((book) => (
              <article key={book.id}>
                <div>
                  <b>
                    {book.title ||
                      (book.proposed_title
                        ? `Possible: ${book.proposed_title}`
                        : "Unidentified book")}
                  </b>
                  <small>{book.description || book.author || book.shelf}</small>
                  {book.crop_verification && (
                    <small>
                      Crop check: {book.crop_verification.status}
                      {book.crop_verification.reason
                        ? ` · ${book.crop_verification.reason}`
                        : ""}
                    </small>
                  )}
                  {!!book.ocr_lines?.length && (
                    <small>
                      Crop text:{" "}
                      {book.ocr_lines.map((line) => line.text).join(" · ")}
                    </small>
                  )}
                  <small>
                    Author:{" "}
                    {book.author ||
                      (book.proposed_author
                        ? `possible ${book.proposed_author}`
                        : "unknown")}{" "}
                    · Publisher:{" "}
                    {book.publisher ||
                      (book.proposed_publisher
                        ? `possible ${book.proposed_publisher}`
                        : "unknown")}
                  </small>
                  <small>
                    Edition: {book.edition || "unknown"} · ISBN:{" "}
                    {book.isbn || "not visible"} · Spine:{" "}
                    {book.spine_height_cm ?? "?"} ×{" "}
                    {book.spine_thickness_cm ?? "?"} cm
                  </small>
                  <small>
                    Replacement: {book.replacement_cost?.amount ?? "unsourced"}{" "}
                    · Used: {book.used_value?.amount ?? "unsourced"} {currency}
                  </small>
                </div>
                <button className="tag" onClick={() => onSelect(book.id)}>
                  {selected === book.id ? "Selected" : "Review"}
                </button>
              </article>
            ))
        ) : (
          <div className="empty">
            {processing
              ? "Detecting books in the captured frame…"
              : countUncertain
                ? "Book count is unverified. See the count comparison and review reasons."
                : packet
                  ? "No book candidates returned. Aim closer at the books and capture again."
                  : "No frames yet. Inventory appears as the camera moves."}
          </div>
        )}
      </div>
      <h3 className="item-heading">
        Room contents · {packet?.items.length || 0} candidates
      </h3>
      {packet?.verification_progress && (
        <p className="message">
          Remaining crop checks: {packet.verification_progress.completed}/
          {packet.verification_progress.total} ·{" "}
          {packet.verification_progress.status}. Unverified candidates appear
          separately in the report.
        </p>
      )}
      <div className="book-list item-list">
        {packet?.items.map((item) => (
          <article key={item.id}>
            <div>
              <b>
                {item.category} {!item.category_verified && "· needs review"}
              </b>
              <small>{item.description}</small>
              {item.crop_verification && (
                <small>
                  Crop check: {item.crop_verification.status}
                  {item.crop_verification.status === "conflict"
                    ? ` · verifier sees ${item.crop_verification.category}`
                    : ""}
                  {item.crop_verification.reason
                    ? ` · ${item.crop_verification.reason}`
                    : ""}
                </small>
              )}
              {item.dismissed_by_reader && (
                <small>
                  Possible false detection: reader sees {item.reader_category}.
                  Review or exclude.
                </small>
              )}
              <small>
                Material:{" "}
                {item.material ||
                  (item.proposed_material
                    ? `possible ${item.proposed_material}`
                    : "unknown")}{" "}
                · Brand/model:{" "}
                {item.brand_model || item.proposed_brand_model || "unknown"}
              </small>
              <small>
                W × H × D: {item.dimensions_cm?.w ?? "?"} ×{" "}
                {item.dimensions_cm?.h ?? "?"} × {item.dimensions_cm?.d ?? "?"}{" "}
                cm
              </small>
              <small>
                Replacement: {item.replacement_cost?.low ?? "unsourced"}–
                {item.replacement_cost?.high ?? "unsourced"} {currency}
              </small>
            </div>
            <button className="tag" onClick={() => onSelect(item.id)}>
              {selected === item.id ? "Selected" : "Review"}
            </button>
          </article>
        ))}
        {!packet?.items.length && (
          <p className="message">
            No room-item candidates yet. Include shelving, furniture, coffee
            machines, lamps, framed art, rugs, electronics and decor in this
            same sweep.
          </p>
        )}
      </div>
    </div>
  );
}
