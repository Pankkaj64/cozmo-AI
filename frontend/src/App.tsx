import { useEffect, useRef, useState } from "react";
import { API, fileUrl, get, post } from "./api";
import { Conversation } from "./components/Conversation";
import { Inventory } from "./components/Inventory";
import type { Locale, Packet } from "./types";
import { isSpeaking, listen, speak, stopSpeaking } from "./voice";

const FRAME_INTERVAL_MS = 2000; // one sampled frame every few seconds while the user walks

export default function App() {
  // setup 
  const [locales, setLocales] = useState<Locale[]>([]);
  const [saved, setSaved] = useState<{ id: string; captured_at: string; country: string }[]>([]);
  const [locale, setLocale] = useState<Locale>({ country_code: "AE", country: "United Arab Emirates", currency: "AED" });
  const [threshold, setThreshold] = useState("2000");
  // sweep state
  const [sweepId, setSweepId] = useState("");
  const [packet, setPacket] = useState<Packet | null>(null);
  const [running, setRunning] = useState(false);
  const [shelf, setShelf] = useState("Shelf 1");
  const [selected, setSelected] = useState("");
  const [message, setMessage] = useState("Choose the country, then start the sweep.");
  const [listening, setListening] = useState(false);
  // Two pages: the sweep (camera + live chat) and the inventory (lines + claim packet).
  const [view, setView] = useState<"sweep" | "inventory">(window.location.hash === "#inventory" ? "inventory" : "sweep");
  // What the sweep is doing right now, shown as a status strip under the camera.
  const [inFlight, setInFlight] = useState(false); // a frame is being analysed by the backend
  const [finishing, setFinishing] = useState(false); // Stop pressed; waiting, then building the packet
  const [frameStartedAt, setFrameStartedAt] = useState(0);
  const [, setTick] = useState(0); // re-render twice a second while something is running
  const videoRef = useRef<HTMLVideoElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const busyRef = useRef(false);
  const shelfRef = useRef(shelf);
  const selectedRef = useRef(selected);
  const lastGuidanceRef = useRef("");
  const stopListeningRef = useRef<(() => void) | null>(null);
  shelfRef.current = shelf;
  selectedRef.current = selected;

  useEffect(() => {
    void get<{ locales: Locale[] }>("/api/sweeps/locales").then((r) => setLocales(r.locales)).catch(() => undefined);
    void get<typeof saved>("/api/sweeps").then(setSaved).catch(() => undefined);
    // After a reload, reopen the sweep that was on screen (its id is kept for this browser tab).
    let remembered = "";
    try { remembered = sessionStorage.getItem("sweepId") ?? ""; } catch { /* storage blocked */ }
    if (remembered) void get<Packet>(`/api/sweeps/${remembered}`).then((p) => { setSweepId(remembered); setPacket(p); }).catch(() => undefined);
    return () => {
void stop()}}, []);

  // Live updates: the backend streams the packet while frames and background agents run.
  useEffect(() => {
    if (!sweepId) return;
    const events = new EventSource(`${API}/api/sweeps/${sweepId}/events`);
    events.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.packet) setPacket(data.packet);
      // The agent directs the sweep: speak new guidance (blur, glare, move closer, next shelf...).
      const text: string | undefined = data.guidance?.text;
      if (running && text && text !== lastGuidanceRef.current && !isSpeaking()) {
        lastGuidanceRef.current = text;
        setMessage(text);
        speak(text);
      }
    };
    return () => events.close();
  }, [sweepId, running]);

  useEffect(() => {
    try { if (sweepId) sessionStorage.setItem("sweepId", sweepId); } catch { /* storage blocked */ }
  }, [sweepId]);

  useEffect(() => {
    const onHash = () => setView(window.location.hash === "#inventory" ? "inventory" : "sweep");
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  const openInventory = () => { window.location.hash = "inventory"; };
  const backToSweep = () => { window.location.hash = ""; };

  useEffect(() => {
    if (!inFlight && !finishing) return;
    const timer = window.setInterval(() => setTick((t) => t + 1), 500);
    return () => window.clearInterval(timer);
  }, [inFlight, finishing]);

  // Capture loop: snapshot the camera and send one JPEG at a time.
  useEffect(() => {
    if (!running || !sweepId) return;
    const timer = window.setInterval(() => void captureFrame(), FRAME_INTERVAL_MS);
    void captureFrame();
    return () => window.clearInterval(timer);
  }, [running, sweepId]);

  async function captureFrame() {
    const video = videoRef.current;
    if (!video || busyRef.current || !video.videoWidth) return;
    busyRef.current = true;
    setInFlight(true);
    setFrameStartedAt(Date.now());
    try {
      const canvas = document.createElement("canvas");
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      canvas.getContext("2d")?.drawImage(video, 0, 0);
      const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.92));
      if (!blob) return;
      const body = new FormData();
      body.append("image", blob, "frame.jpg");
      body.append("shelf", shelfRef.current);
      const result = await post<{ packet: Packet }>(`/api/sweeps/${sweepId}/frames`, body);
      setPacket(result.packet);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Frame upload failed");
    } finally {
      busyRef.current = false;
      setInFlight(false);
    }
  }

  const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

  // Conversation: every sentence the claimant says goes to the backend, which either runs an
  // explicit tool ("skip this shelf") or answers with the dialogue model; the reply is spoken.
  async function sendTurn(text: string) {
    if (!sweepId) return;
    stopSpeaking();
    try {
      const result = await post<{ reply: string; action: string; packet: Packet }>(`/api/sweeps/${sweepId}/turns`, {
        text,
        shelf: shelfRef.current,
        ref_id: selectedRef.current,
      });
      setPacket(result.packet);
      setMessage(result.reply);
      speak(result.reply);
      if (result.action === "next_shelf") setShelf((s) => s.replace(/\d+$/, (n) => String(Number(n) + 1)));
      if (result.action === "capture") void captureFrame();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not send that");
    }
  }

  async function start() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { 
        facingMode: "environment", width: { ideal: 1920 }, height: { ideal: 1080 } }, 
        audio: false 
      });
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play();
      }
      const result = await post<{ sweep_id: string; packet: Packet }>("/api/sweeps", {
        country: locale.country, currency: locale.currency, country_code: locale.country_code,
        appraisal_threshold: Number(threshold) || 2000, device: "browser camera",
      });
      setSweepId(result.sweep_id);
      setPacket(result.packet);
      setSelected("");
      lastGuidanceRef.current = "";
      setRunning(true);
      const greeting = `Hello. We are documenting a contents claim in ${locale.country}, in ${locale.currency}. Walk the room once and pan slowly across every shelf, wall and floor area. I will tell you what I am logging and ask you to slow down or move closer when spines are unreadable. Say "next shelf" when you move on.`;
      setMessage(greeting);
      speak(greeting);
      stopListeningRef.current = listen((text) => void sendTurn(text), stopSpeaking);
      setListening(!!stopListeningRef.current);
    } catch (error) {
      setMessage(error instanceof Error ? `${error.message}. Camera needs localhost or HTTPS.` : "Could not start");
    }
  }

  async function stop() {
    if (finishing) return;
    setRunning(false);
    stopListeningRef.current?.();
    setListening(false);
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    if (!sweepId) return;
    setFinishing(true);
    try {
      // The last frame may still be in the backend; finishing before it returns would be refused.
      if (busyRef.current) setMessage("Capture stopped. Waiting for the last frame to finish…");
      while (busyRef.current) await sleep(300);
      await post(`/api/sweeps/${sweepId}/stop-capture`).catch(() => undefined);
      setMessage("Capture stopped. Finishing identification, pricing and the claim packet…");
      // Background stages (research, FX, valuation, validation) run inside /finish.
      let result: { packet: Packet } | null = null;
      for (let attempt = 0; result === null; attempt++) {
        try {
          result = await post<{ packet: Packet }>(`/api/sweeps/${sweepId}/finish`);
        } catch (error) {
          const text = error instanceof Error ? error.message : "";
          // 409 while a frame is still being analysed: wait and try again, up to five minutes.
          if (!/frame processing/i.test(text) || attempt >= 150) throw error;
          setMessage("A frame is still being analysed; the packet is built as soon as it is done…");
          await sleep(2000);
        }
      }
      setPacket(result.packet);
      const summary = result.packet.sweep.summary || "The claim packet is ready.";
      setMessage(summary);
      speak(summary);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not build the packet");
    } finally {
      setFinishing(false);
    }
  }

  // One line that says what is happening now; the spinner runs only while work is in progress.
  function status(): { kind: string; text: string } | null {
    const frames = packet?.frames?.length ?? 0;
    if (finishing) return { kind: "finishing", text: inFlight ? "Finishing: waiting for the last frame to be analysed…" : "Finishing: catalogue check, pricing and the claim packet…" };
    if (inFlight) {
      const seconds = Math.max(0, Math.round((Date.now() - frameStartedAt) / 1000));
      const detail = packet?.live_progress?.guidance || "detecting books, reading spines, checking the count";
      return { kind: "processing", text: `Processing frame ${frames + 1} · ${seconds}s · ${detail}` };
    }
    if (running) return { kind: "scanning", text: `Scanning · ${frames} frame${frames === 1 ? "" : "s"} analysed · next frame in 2 s · say "next shelf" when you move on` };
    const after = packet?.verification_progress;
    if (after?.status === "running") return { kind: "processing", text: `After-capture checks ${after.completed} of ${after.total}…` };
    return null;
  }
  const now = status();

  const finished = !!packet?.sweep.finished_at;
  const t = packet?.totals ?? {};
  const statusPill = (
    <span className={`pill ${now?.kind ?? (finished ? "done" : "")}`}>
      {now ? now.kind[0].toUpperCase() + now.kind.slice(1) : finished ? "Packet ready" : "Ready"}
    </span>
  );

  if (view === "inventory") {
    return (
      <main className="page-inventory">
        <header>
          <div className="row">
            <button className="secondary" onClick={backToSweep}>← Back to sweep</button>
            <div>
              <h1>Inventory</h1>
              <p className="muted">{packet ? `${packet.sweep.country} · ${packet.sweep.currency} · sweep ${packet.sweep.id.slice(0, 8)}` : "No sweep loaded."}</p>
            </div>
          </div>
          {statusPill}
        </header>
        {packet ? (
          <>
            {finished && (
              <section className="card">
                <h2>Claim packet</h2>
                <p className="message">{packet.sweep.summary || "The claim packet is ready."}</p>
                <div className="links" style={{ marginTop: 10 }}>
                  <a href={fileUrl(`data/claims/${packet.sweep.id}/claim_packet.json`)} target="_blank" rel="noreferrer">claim_packet.json</a>
                  <a href={`${API}/api/sweeps/${packet.sweep.id}/report`} target="_blank" rel="noreferrer">report.html</a>
                  <a href={`${API}/api/sweeps/${packet.sweep.id}/bundle`}>evidence bundle (zip)</a>
                  <a href={`${API}/api/sweeps/${packet.sweep.id}/prices`} target="_blank" rel="noreferrer">price details</a>
                </div>
                <p className="muted" style={{ marginTop: 10 }}>{packet.review_queue.length} review findings. Go back to the sweep and say "compare prices in the United Kingdom" to price ten books for a second country.</p>
              </section>
            )}
            <Inventory packet={packet} selected={selected} onSelect={setSelected} />
          </>
        ) : (
          <section className="card"><p className="muted">Start a sweep or review a saved one first.</p></section>
        )}
      </main>
    );
  }

  return (
    <main>
      <header>
        <div>
          <h1>Library Contents Claim Agent</h1>
          <p className="muted">One continuous sweep: talk to the agent while you pan across every shelf, wall and floor.</p>
        </div>
        <div className="row">
          {statusPill}
          <button onClick={openInventory} disabled={!finished} title={finished ? "Open the inventory and claim packet" : "Available once the sweep is finished"}>
            Inventory &amp; packet →
          </button>
        </div>
      </header>

      <div className="layout">
        <div className="column">
          <section className="card">
            <div className="fields">
              <label className="field">
                Country · currency
                <select disabled={running || finishing} value={locale.country_code} onChange={(e) => setLocale(locales.find((l) => l.country_code === e.target.value) ?? locale)}>
                  {(locales.length ? locales : [locale]).map((l) => (
                    <option key={l.country_code} value={l.country_code}>{l.country} · {l.currency}</option>
                  ))}
                </select>
              </label>
              <label className="field">
                Appraisal threshold
                <input disabled={running || finishing} value={threshold} onChange={(e) => setThreshold(e.target.value)} inputMode="numeric" title="items above this value need a professional appraisal" />
              </label>
              <label className="field">
                Current shelf
                <input value={shelf} onChange={(e) => setShelf(e.target.value)} title="label for the frames captured now" />
              </label>
            </div>
            <div className="toolbar">
              <div className="actions">
                {!running ? (
                  <button onClick={start} disabled={finishing}>{finishing ? "Finishing…" : finished ? "Start new sweep" : "Start sweep"}</button>
                ) : (
                  <button className="stop" onClick={stop} disabled={finishing}>Stop sweep · build packet</button>
                )}
                <button className="secondary" disabled={!running || inFlight} onClick={() => void captureFrame()}>{inFlight ? "Processing…" : "Capture now"}</button>
              </div>
              <label className="field">
                Review a saved sweep
                <select
                  disabled={running}
                  value=""
                  onChange={(e) => {
                    // Reopen a saved sweep for review (no camera needed).
                    const id = e.target.value;
                    if (id) void get<Packet>(`/api/sweeps/${id}`).then((p) => { setSweepId(id); setPacket(p); setMessage("Loaded saved sweep for review."); });
                  }}
                >
                  <option value="">Choose…</option>
                  {saved.map((s) => (
                    <option key={s.id} value={s.id}>{s.captured_at.slice(0, 16)} · {s.country} · {s.id.slice(0, 8)}</option>
                  ))}
                </select>
              </label>
            </div>
            <div className="camera">
              <video ref={videoRef} muted playsInline className={running ? "" : "hidden"} />
              {!running && (
                <div className="placeholder">
                  {finished ? "Sweep finished. Open the inventory, or start a new sweep." : "The camera preview appears here when you start the sweep."}
                </div>
              )}
              {running && <span className="shelf-tag">{shelf}</span>}
            </div>
            {now && (
              <div className={`strip ${now.kind}`} role="status" aria-live="polite">
                <span className="spinner" aria-hidden="true" />
                <span>{now.text}</span>
              </div>
            )}
            <p className="message">{message}</p>
            {packet && (
              <p className="live-counts">
                <span><b>{t.book_count ?? 0}</b> books</span>
                <span><b>{t.books_identified ?? 0}</b> identified</span>
                <span><b>{packet.items.length}</b> items</span>
                <span><b>{packet.review_queue.length}</b> to review</span>
                {finished && <button className="link" onClick={openInventory}>open inventory →</button>}
              </p>
            )}
          </section>
        </div>

        <div className="column">
          <Conversation packet={packet} listening={listening} onSend={sendTurn} selected={selected} onClearSelection={() => setSelected("")} />
        </div>
      </div>
    </main>
  );
}
