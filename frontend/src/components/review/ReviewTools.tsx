import { useState } from "react";
import type { Packet } from "../../types/packet";
import { loggedFetch } from "../../lib/logger";

type Props = {
  packet: Packet;
  api: string;
  sweepId: string;
  selected: string;
  onSelect: (id: string) => void;
  onUpdate: (packet: Packet) => void;
};
type Point = { x: number; y: number };
export function ReviewTools({
  packet,
  api,
  sweepId,
  selected,
  onSelect,
  onUpdate,
}: Props) {
  const [message, setMessage] = useState("");
  const [pending, setPending] = useState(false);
  const [points, setPoints] = useState<Point[]>([]);
  const [frameRef, setFrameRef] = useState("");
  const [mode, setMode] = useState("reference");
  const [comparison, setComparison] = useState<{
    country: string;
    currency: string;
    priced: number;
    lines: unknown[];
  } | null>(null);
  const lines = [
    ...packet.books.map((book) => ({
      ...book,
      label: `${book.shelf} · ${book.title || book.proposed_title || book.description || "Unidentified book"}`,
    })),
    ...packet.items.map((item) => ({
      ...item,
      label: `${item.category} · ${item.description}`,
    })),
  ];
  const line = lines.find((entry) => entry.id === selected);
  const frame =
    frameRef || line?.frame_ref || packet.frames?.at(-1)?.frame_ref || "";
  async function post(path: string, value: unknown) {
    setPending(true);
    setMessage("");
    try {
      const response = await loggedFetch(
        `${api}/api/sweeps/${sweepId}/${path}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(value),
        },
      );
      const data = await response.json();
      if (!response.ok)
        throw new Error(
          typeof data.detail === "string"
            ? data.detail
            : JSON.stringify(data.detail),
        );
      if (data.packet) onUpdate(data.packet);
      setMessage("Saved with its evidence source.");
      return data;
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not save");
    } finally {
      setPending(false);
    }
  }
  const values = (form: HTMLFormElement) =>
    Object.fromEntries(new FormData(form).entries()) as Record<string, string>;
  return (
    <div className="review-tools">
      <label>
        Selected inventory line
        <select
          value={selected}
          onChange={(event) => {
            onSelect(event.target.value);
            setFrameRef("");
            setPoints([]);
          }}
        >
          <option value="">
            Select an item for correction, measurement or pricing
          </option>
          {lines.map((entry) => (
            <option key={entry.id} value={entry.id}>
              {entry.label}
            </option>
          ))}
        </select>
      </label>
      <p className="message" role="status">
        {message}
      </p>
      {line && (
        <details open key={selected}>
          <summary>Verify identity and visible details</summary>
          <p>
            Compare this line with its saved evidence. Model text and categories
            remain proposals until reviewed. Leave unreadable fields blank.
          </p>
          <a href={`${api}/${line.frame_ref}`} target="_blank" rel="noreferrer">
            Open this line’s evidence
          </a>
          <form
            className="review-form"
            onSubmit={(event) => {
              event.preventDefault();
              const v = values(event.currentTarget);
              void post("inventory-review", {
                ref_id: selected,
                ...v,
                is_print:
                  v.is_print === "unknown" ? null : v.is_print === "print",
              });
            }}
          >
            {packet.books.some((book) => book.id === selected) ? (
              <>
                <label>
                  Title
                  <input
                    name="title"
                    defaultValue={
                      packet.books.find((b) => b.id === selected)?.title ||
                      packet.books.find((b) => b.id === selected)
                        ?.proposed_title ||
                      ""
                    }
                  />
                </label>
                <label>
                  Author
                  <input
                    name="author"
                    defaultValue={
                      packet.books.find((b) => b.id === selected)?.author ||
                      packet.books.find((b) => b.id === selected)
                        ?.proposed_author ||
                      ""
                    }
                  />
                </label>
                <label>
                  Publisher
                  <input
                    name="publisher"
                    defaultValue={
                      packet.books.find((b) => b.id === selected)?.publisher ||
                      packet.books.find((b) => b.id === selected)
                        ?.proposed_publisher ||
                      ""
                    }
                  />
                </label>
                <label>
                  Edition (only if supported)
                  <input
                    name="edition"
                    defaultValue={
                      packet.books.find((b) => b.id === selected)?.edition || ""
                    }
                  />
                </label>
              </>
            ) : (
              <>
                <label>
                  Category
                  <input
                    name="category"
                    required
                    defaultValue={
                      packet.items.find((i) => i.id === selected)?.category ||
                      ""
                    }
                  />
                </label>
                <label>
                  Material
                  <input
                    name="material"
                    defaultValue={
                      packet.items.find((i) => i.id === selected)?.material ||
                      packet.items.find((i) => i.id === selected)
                        ?.proposed_material ||
                      ""
                    }
                  />
                </label>
                <label>
                  Brand / model
                  <input
                    name="brand_model"
                    defaultValue={
                      packet.items.find((i) => i.id === selected)
                        ?.brand_model ||
                      packet.items.find((i) => i.id === selected)
                        ?.proposed_brand_model ||
                      ""
                    }
                  />
                </label>
                <label>
                  Artwork
                  <select
                    name="is_print"
                    defaultValue={
                      packet.items.find((i) => i.id === selected)?.is_print ===
                      true
                        ? "print"
                        : packet.items.find((i) => i.id === selected)
                              ?.is_print === false
                          ? "original"
                          : "unknown"
                    }
                  >
                    <option value="unknown">Unknown / not art</option>
                    <option value="print">Print</option>
                    <option value="original">
                      Original — appraisal needed
                    </option>
                  </select>
                </label>
              </>
            )}
            <label>
              Evidence for these details
              <input
                name="source"
                required
                minLength={3}
                placeholder="Readable text in saved frame / claimant correction"
              />
            </label>
            <label className="record-option">
              <input type="checkbox" required /> I checked these fields against
              the evidence.
            </label>
            <button disabled={pending}>Save reviewed details</button>
          </form>
          <form
            className="turn-form"
            onSubmit={(event) => {
              event.preventDefault();
              const v = values(event.currentTarget);
              void post("inventory-review", {
                ref_id: selected,
                source: v.reason,
                exclude: true,
              }).then((result) => {
                if (result) onSelect("");
              });
            }}
          >
            <input
              name="reason"
              required
              minLength={3}
              aria-label="Exclusion reason"
              placeholder="Why this is a duplicate or false detection"
            />
            <button className="quiet" disabled={pending}>
              Exclude from inventory
            </button>
          </form>
          <p>
            Exclusions retain the original observation and reason in the packet.
          </p>
        </details>
      )}
      <details>
        <summary>Measure spines from a known reference</summary>
        <p>
          Choose a saved view. Click the two ends of a known shelf width or
          reference object. It must be in the same plane as the spines, with the
          camera square to that plane. Scale applies only to this frame.
        </p>
        <label>
          Evidence frame
          <select
            value={frame}
            onChange={(e) => {
              setFrameRef(e.target.value);
              setPoints([]);
            }}
          >
            {packet.frames?.map((f) => (
              <option key={f.frame_ref} value={f.frame_ref}>
                {f.frame_ref.split("/").at(-1)}
              </option>
            ))}
          </select>
        </label>
        <label>
          Marking mode
          <select
            value={mode}
            onChange={(e) => {
              setMode(e.target.value);
              setPoints([]);
            }}
          >
            <option value="reference">Two ends of known reference</option>
            <option value="spine">
              Top-left and bottom-right of selected spine
            </option>
          </select>
        </label>
        {frame && (
          <div
            className="measurement-image"
            onClick={(event) => {
              const bounds = event.currentTarget.getBoundingClientRect();
              const point = {
                x: (event.clientX - bounds.left) / bounds.width,
                y: (event.clientY - bounds.top) / bounds.height,
              };
              setPoints((old) =>
                old.length === 2 ? [point] : [...old, point],
              );
            }}
          >
            <img
              src={`${api}/${frame}`}
              alt="Saved evidence. Click two reference endpoints or spine corners."
            />
            {points.map((p, i) => (
              <i
                key={i}
                style={{ left: `${p.x * 100}%`, top: `${p.y * 100}%` }}
              >
                {i + 1}
              </i>
            ))}
          </div>
        )}
        <p>
          {points.length} of 2 points selected. Horizontal stacks use the long
          edge as spine height.
        </p>
        {mode === "reference" ? (
          <form
            className="review-form"
            onSubmit={(e) => {
              e.preventDefault();
              const v = values(e.currentTarget);
              void post("calibration", {
                frame_ref: frame,
                start: points[0],
                end: points[1],
                length_cm: Number(v.length),
                reference: v.reference,
                same_plane: true,
                front_on: true,
              });
            }}
          >
            <label>
              Reference length (cm)
              <input
                name="length"
                type="number"
                step="0.01"
                min="0.01"
                required
              />
            </label>
            <label>
              What establishes this length?
              <input
                name="reference"
                placeholder="Manufacturer shelf width / ruler visible in view"
                minLength={3}
                required
              />
            </label>
            <label className="record-option">
              <input type="checkbox" required /> I confirm the reference and
              spines share a plane and this is a front-on view.
            </label>
            <button disabled={pending || points.length !== 2}>
              Save frame scale
            </button>
          </form>
        ) : (
          <button
            disabled={
              pending ||
              points.length !== 2 ||
              !packet.books.some(
                (b) => b.id === selected && b.frame_ref === frame,
              )
            }
            onClick={() =>
              void post("spine-bounds", {
                ref_id: selected,
                bbox: [
                  Math.min(points[0].x, points[1].x),
                  Math.min(points[0].y, points[1].y),
                  Math.max(points[0].x, points[1].x),
                  Math.max(points[0].y, points[1].y),
                ],
              })
            }
          >
            Measure selected spine
          </button>
        )}
      </details>
      <details>
        <summary>Room geometry and shelving coverage</summary>
        <p>
          Record dimensions already known or obtained during this sweep. A
          normal camera stream alone does not establish metres. For
          non-rectangular rooms, enter floor-plan vertices in metres.
        </p>
        <form
          className="review-form"
          onSubmit={(e) => {
            e.preventDefault();
            const v = values(e.currentTarget);
            try {
              void post("room", {
                frame_ref: frame,
                length_m: v.length ? Number(v.length) : null,
                width_m: v.width ? Number(v.width) : null,
                height_m: Number(v.height),
                polygon_m: JSON.parse(v.polygon || "[]"),
                shelving: JSON.parse(v.shelving || "[]"),
                source: v.source,
                method: v.method,
              });
            } catch {
              setMessage(
                "Polygon and shelving must be valid JSON arrays of pairs.",
              );
            }
          }}
        >
          <label>
            Length (m)
            <input name="length" type="number" min="0.01" step="0.01" />
          </label>
          <label>
            Width (m)
            <input name="width" type="number" min="0.01" step="0.01" />
          </label>
          <label>
            Ceiling height (m)
            <input
              name="height"
              type="number"
              min="0.01"
              step="0.01"
              required
            />
          </label>
          <label>
            Scale method
            <select name="method">
              <option value="known_dimensions">Known dimensions</option>
              <option value="lidar">LiDAR measurement</option>
              <option value="reference_geometry">
                Calibrated reference geometry
              </option>
            </select>
          </label>
          <label>
            Source and assumptions
            <input
              name="source"
              required
              minLength={3}
              placeholder="Where these measurements came from"
            />
          </label>
          <label>
            Polygon [x,y] vertices (optional)
            <input name="polygon" placeholder="[[0,0],[4,0],[4,3],[0,3]]" />
          </label>
          <label>
            Shelving [width,height] metres
            <input name="shelving" placeholder="[[1.2,2],[1.5,2]]" />
          </label>
          <button disabled={pending || !frame}>Calculate room areas</button>
        </form>
        {packet.room && (
          <p>
            Floor: {String(packet.room.floor_area_m2 ?? "unknown")} m² /{" "}
            {String(packet.room.floor_area_ft2 ?? "unknown")} ft². Walls:{" "}
            {String(packet.room.wall_area_m2 ?? "unknown")} m² /{" "}
            {String(packet.room.wall_area_ft2 ?? "unknown")} ft².
          </p>
        )}
      </details>
      <details>
        <summary>Non-book dimensions from recorded evidence</summary>
        <form
          className="review-form"
          onSubmit={(e) => {
            e.preventDefault();
            const v = values(e.currentTarget);
            void post("item-measurement", {
              ref_id: selected,
              frame_ref: line?.frame_ref,
              w: Number(v.w),
              h: Number(v.h),
              d: Number(v.d),
              source: v.source,
            });
          }}
        >
          <label>
            Width (cm)
            <input name="w" type="number" min="0.01" step="0.01" required />
          </label>
          <label>
            Height (cm)
            <input name="h" type="number" min="0.01" step="0.01" required />
          </label>
          <label>
            Depth (cm)
            <input name="d" type="number" min="0.01" step="0.01" required />
          </label>
          <label>
            Metric evidence / source
            <input
              name="source"
              minLength={3}
              required
              placeholder="Known furniture specification / calibrated geometry"
            />
          </label>
          <button
            disabled={
              pending || !packet.items.some((item) => item.id === selected)
            }
          >
            Save non-book dimensions
          </button>
        </form>
      </details>
      <details>
        <summary>Catalogue and market research</summary>
        <p>
          When configured, background lookup searches Open Library and eBay for
          the captured inventory. Review the physical edition, delivery,
          condition, taxes and currency before adding a price.
        </p>
        <button disabled={pending} onClick={() => void post("research", {})}>
          Look up inventory sources
        </button>
        {packet.research?.map((result) => (
          <article key={result.ref_id}>
            <p>
              <b>{lines.find((line) => line.id === result.ref_id)?.label}</b>:{" "}
              {result.error ||
                result.pricing?.reason ||
                result.pricing?.status ||
                result.identification?.status}
            </p>
            {result.identification?.url && (
              <a
                href={result.identification.url}
                target="_blank"
                rel="noreferrer"
              >
                Catalogue record — verify physical edition
              </a>
            )}
            <ul>
              {result.pricing?.offers.map((offer) => (
                <li key={offer.url}>
                  <a href={offer.url} target="_blank" rel="noreferrer">
                    {offer.listing_title}
                  </a>{" "}
                  — {offer.amount} {offer.currency}, {offer.condition_assumed}.
                  Asking price; shipping/tax excluded.
                </li>
              ))}
            </ul>
          </article>
        ))}
      </details>
      <details>
        <summary>Add a verified market price</summary>
        <p>
          Check a current physical-book or household-item listing. Record its
          URL, date and condition. The app never creates a price from model
          memory. Art originals, special editions and values at or above 2,000
          in the selected currency go to appraisal.
        </p>
        <form
          className="review-form"
          onSubmit={(e) => {
            e.preventDefault();
            const v = values(e.currentTarget);
            void post("offers", {
              ref_id: selected,
              kind: v.kind,
              country: v.country,
              currency: v.currency.toUpperCase(),
              amount: Number(v.amount),
              high: v.high ? Number(v.high) : null,
              source: v.source,
              url: v.url,
              retrieved_at: v.date,
              condition_assumed: v.condition,
              match_basis: v.match,
              verified: true,
              ...(v.target
                ? {
                    target_currency: v.target.toUpperCase(),
                    fx_rate: Number(v.rate),
                    fx_url: v.fxurl,
                    fx_date: v.fxdate,
                  }
                : {}),
            });
          }}
        >
          <label>
            Price type
            <select name="kind">
              <option value="replacement">
                Book replacement (new/equivalent)
              </option>
              <option value="used">Book used market value</option>
              <option value="item">Non-book replacement / range</option>
            </select>
          </label>
          <label>
            Source country
            <input
              name="country"
              defaultValue={packet.sweep.country}
              required
            />
          </label>
          <label>
            Source currency
            <input
              name="currency"
              defaultValue={packet.sweep.currency}
              pattern="[A-Za-z]{3}"
              required
            />
          </label>
          <label>
            Amount / range low
            <input name="amount" type="number" min="0" step="0.01" required />
          </label>
          <label>
            Range high (items only)
            <input name="high" type="number" min="0" step="0.01" />
          </label>
          <label>
            Retailer / marketplace
            <input name="source" required minLength={2} />
          </label>
          <label>
            Listing URL
            <input name="url" type="url" required />
          </label>
          <label>
            Retrieval date
            <input
              name="date"
              type="date"
              required
              defaultValue={new Date().toISOString().slice(0, 10)}
            />
          </label>
          <label>
            Condition assumed
            <input
              name="condition"
              required
              placeholder="New paperback / used good condition"
            />
          </label>
          <label>
            Why this matches the captured item
            <input name="match" required minLength={3} />
          </label>
          <label>
            Convert to currency (optional)
            <input name="target" placeholder="AED" pattern="[A-Za-z]{3}" />
          </label>
          <label>
            FX target units per source unit
            <input name="rate" type="number" step="any" min="0" />
          </label>
          <label>
            FX source URL
            <input name="fxurl" type="url" />
          </label>
          <label>
            FX date
            <input name="fxdate" type="date" />
          </label>
          <label className="record-option">
            <input type="checkbox" required /> I checked the listing, price,
            currency, physical format, and match.
          </label>
          <button disabled={pending || !selected}>Record sourced offer</button>
        </form>
      </details>
      <details>
        <summary>Compare the same inventory in another country</summary>
        <p>
          Add checked offers for the second country, then compare. Missing
          quotes stay blank. This does not change the primary claim locale.
        </p>
        <form
          className="review-form"
          onSubmit={(e) => {
            e.preventDefault();
            const v = values(e.currentTarget);
            void post("compare-locale", {
              country: v.country,
              currency: v.currency.toUpperCase(),
            }).then((data) => data && setComparison(data));
          }}
        >
          <label>
            Second country
            <input name="country" required />
          </label>
          <label>
            Currency
            <input name="currency" pattern="[A-Za-z]{3}" required />
          </label>
          <button disabled={pending}>Compare</button>
        </form>
        {comparison && (
          <div>
            <p>
              {comparison.country} ({comparison.currency}): {comparison.priced}{" "}
              of {comparison.lines.length} inventory lines have sourced prices.
            </p>
            <pre>{JSON.stringify(comparison.lines, null, 2)}</pre>
          </div>
        )}
      </details>
      <details>
        <summary>Ground-truth evaluation</summary>
        <p>
          Paste independently collected ground truth in the documented JSON
          format. Missing samples fail the pass bar; this form does not create
          physical evidence.
        </p>
        <form
          className="review-form"
          onSubmit={(e) => {
            e.preventDefault();
            const v = values(e.currentTarget);
            try {
              void post("evaluate", { ground_truth: JSON.parse(v.truth) }).then(
                (data) =>
                  data && setMessage(JSON.stringify(data.evaluation, null, 2)),
              );
            } catch {
              setMessage("Ground truth must be valid JSON.");
            }
          }}
        >
          <label>
            Manual ground truth
            <textarea
              name="truth"
              rows={10}
              required
              placeholder={'{"books":[],"items":[],"room":{}}'}
            />
          </label>
          <button disabled={pending}>Evaluate saved sweep</button>
        </form>
      </details>
      <details>
        <summary>Appraisal policy</summary>
        <form
          className="review-form"
          onSubmit={(e) => {
            e.preventDefault();
            const v = values(e.currentTarget);
            void post("settings", { appraisal_threshold: Number(v.threshold) });
          }}
        >
          <label>
            High-value threshold in claim currency
            <input
              name="threshold"
              type="number"
              min="0.01"
              step="0.01"
              defaultValue={2000}
              required
            />
          </label>
          <p>
            Rare, signed, antiquarian books and original art require appraisal
            regardless of this threshold.
          </p>
          <button disabled={pending}>Apply appraisal threshold</button>
        </form>
      </details>
      <details>
        <summary>
          Workflow audit ({packet.audit_trail?.length || 0} events)
        </summary>
        <ol>
          {packet.audit_trail?.slice(-50).map((entry) => (
            <li key={entry.id}>
              {entry.time} — {entry.step}
            </li>
          ))}
        </ol>
      </details>
    </div>
  );
}
