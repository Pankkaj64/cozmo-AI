import type { Packet } from "../../types/packet";
import { ReviewTools } from "./ReviewTools";

type Props = {
  packet: Packet;
  api: string;
  sweepId: string;
  selected: string;
  hasExports: boolean;
  onSelect: (id: string) => void;
  onUpdate: (packet: Packet) => void;
  onRefreshExports: () => void;
};

export function ReviewPanel({
  packet,
  api,
  sweepId,
  selected,
  hasExports,
  onSelect,
  onUpdate,
  onRefreshExports,
}: Props) {
  return (
    <section className="card lower">
      <div className="section-title">
        <h3>Review before submission</h3>
        <span>{packet.items.length} non-book candidates</span>
      </div>
      <ReviewTools
        packet={packet}
        api={api}
        sweepId={sweepId}
        selected={selected}
        onSelect={onSelect}
        onUpdate={onUpdate}
      />
      {!!packet.videos?.length && (
        <p>
          {packet.videos.map((video) => (
            <a
              key={video.ref}
              href={`${api}/${video.ref}`}
              target="_blank"
              rel="noreferrer"
            >
              Open continuous camera recording
            </a>
          ))}
        </p>
      )}
      {packet.review_queue.length > 0 && (
        <details className="review-findings">
          <summary>
            {packet.review_queue.length} outstanding review findings
          </summary>
          <ul>
            {packet.review_queue.map((entry, index) => (
              <li key={`${entry.ref_id}-${index}`}>{entry.reason}</li>
            ))}
          </ul>
        </details>
      )}
      {hasExports && (
        <button className="quiet" onClick={onRefreshExports}>
          Update exports after review
        </button>
      )}
      {hasExports && (
        <div className="downloads">
          <a
            href={`${api}/data/claims/${sweepId}/claim_packet.json`}
            target="_blank"
            rel="noreferrer"
          >
            Open claim_packet.json
          </a>
          <a
            id="readable-report"
            href={`${api}/api/sweeps/${sweepId}/report`}
            target="_blank"
            rel="noreferrer"
          >
            Open readable report
          </a>
          <a href={`${api}/api/sweeps/${sweepId}/bundle`}>
            Download packet + evidence ZIP
          </a>
        </div>
      )}
    </section>
  );
}
