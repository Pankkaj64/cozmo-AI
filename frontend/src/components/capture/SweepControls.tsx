type SavedSweep = { id: string; captured_at: string; country: string };

type Props = {
  country: string;
  countryCode: string;
  currency: string;
  shelf: string;
  running: boolean;
  starting: boolean;
  finishing: boolean;
  queuedCount: number;
  recordVideo: boolean;
  savedSweeps: SavedSweep[];
  onCountryChange: (value: string) => void;
  onCountryCodeChange: (value: string) => void;
  onCurrencyChange: (value: string) => void;
  onShelfChange: (value: string) => void;
  onStart: () => void;
  onFinish: () => void;
  onReviewSaved: () => void;
  onOpenSweep: (id: string) => void;
  onRecordVideoChange: (value: boolean) => void;
};

export function SweepControls(props: Props) {
  const {
    country,
    countryCode,
    currency,
    shelf,
    running,
    starting,
    finishing,
    queuedCount,
    recordVideo,
    savedSweeps,
  } = props;
  return (
    <>
      <section className="controls">
        <label>
          Country
          <input
            value={country}
            onChange={(e) => props.onCountryChange(e.target.value)}
            disabled={running}
          />
        </label>
        <label>
          Country code
          <input
            value={countryCode}
            maxLength={2}
            onChange={(e) =>
              props.onCountryCodeChange(e.target.value.toUpperCase())
            }
            disabled={running}
            aria-label="Two-letter delivery country"
          />
        </label>
        <label>
          Currency
          <input
            value={currency}
            onChange={(e) =>
              props.onCurrencyChange(e.target.value.toUpperCase())
            }
            disabled={running}
          />
        </label>
        <label>
          Current shelf
          <input
            value={shelf}
            onChange={(e) => props.onShelfChange(e.target.value)}
            disabled={!running}
          />
        </label>
        {!running ? (
          <button
            className="primary"
            onClick={props.onStart}
            disabled={starting || finishing}
          >
            {starting
              ? "Opening camera…"
              : finishing
                ? `Saving sweep… (${queuedCount} views waiting)`
                : "Start camera sweep"}
          </button>
        ) : (
          <button className="stop" onClick={props.onFinish}>
            Finish sweep
          </button>
        )}
      </section>
      {!running && !starting && !finishing && (
        <div className="saved-sweeps">
          <button className="quiet" onClick={props.onReviewSaved}>
            Review saved sweeps
          </button>
          {savedSweeps.length > 0 && (
            <select
              aria-label="Open saved sweep"
              defaultValue=""
              onChange={(e) =>
                e.target.value && props.onOpenSweep(e.target.value)
              }
            >
              <option value="">Choose a saved sweep</option>
              {savedSweeps.map((sweep) => (
                <option key={sweep.id} value={sweep.id}>
                  {sweep.captured_at} · {sweep.country}
                </option>
              ))}
            </select>
          )}
        </div>
      )}
      <label className="record-option">
        <input
          type="checkbox"
          checked={recordVideo}
          disabled={running || starting}
          onChange={(e) => props.onRecordVideoChange(e.target.checked)}
        />{" "}
        Save continuous camera video as evidence (video only). Detection uses
        sampled images.
      </label>
    </>
  );
}
