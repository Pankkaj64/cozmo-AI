import { useEffect, useRef, useState } from "react";
import { API, fileUrl, get, post } from "./api";
import { Conversation } from "./components/Conversation";
import { Inventory } from "./components/Inventory";
import type { Locale, Packet } from "./types";
import { isSpeaking, listen, speak, stopSpeaking } from "./voice";

const FRAME_INTERVAL_MS = 2000; // one sampled frame every few seconds while the user walks

export default function App() {
  // --- setup ---------------------------------------------------------------------------
  const [locales, setLocales] = useState<Locale[]>([]);
  const [saved, setSaved] = useState<{ id: string; captured_at: string; country: string }[]>([]);
  const [locale, setLocale] = useState<Locale>({ country_code: "AE", country: "United Arab Emirates", currency: "AED" });
  const [threshold, setThreshold] = useState("2000");
  // --- sweep state -------------------------------------------------------------------
  const [sweepId, setSweepId] = useState("");
  const [packet, setPacket] = useState<Packet | null>(null);
  const [running, setRunning] = useState(false);
  const [shelf, setShelf] = useState("Shelf 1");
  const [selected, setSelected] = useState("");
  const [message, setMessage] = useState("Choose the country, then start the sweep.");
  const [listening, setListening] = useState(false);
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
    return () => {
      void stop();
    };
  }, []);

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
    }
  }

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
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment", width: { ideal: 1920 }, height: { ideal: 1080 } }, audio: false });
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
    setRunning(false);
    stopListeningRef.current?.();
    setListening(false);
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    if (sweepId) {
      await post(`/api/sweeps/${sweepId}/stop-capture`).catch(() => undefined);
      setMessage("Capture stopped. Finishing identification, pricing and the claim packet…");
      try {
        // Background stages (research, FX, valuation, validation) run inside /finish.
        const result = await post<{ packet: Packet }>(`/api/sweeps/${sweepId}/finish`);
        setPacket(result.packet);
        const summary = result.packet.sweep.summary || "The claim packet is ready.";
        setMessage(summary);
        speak(summary);
      } catch (error) {
        setMessage(error instanceof Error ? error.message : "Could not build the packet");
      }
    }
  }

  return (
    <main>
      <header>
        <h1>Library Contents Claim Agent</h1>
        <p className="muted">One continuous sweep: talk to the agent while you pan across every shelf, wall and floor.</p>
      </header>

      <section className="card">
        <div className="row">
          <select disabled={!!sweepId} value={locale.country_code} onChange={(e) => setLocale(locales.find((l) => l.country_code === e.target.value) ?? locale)}>
            {(locales.length ? locales : [locale]).map((l) => (
              <option key={l.country_code} value={l.country_code}>{l.country} · {l.currency}</option>
            ))}
          </select>
          <input disabled={!!sweepId} value={threshold} onChange={(e) => setThreshold(e.target.value)} title="appraisal threshold in claim currency" />
          <input value={shelf} onChange={(e) => setShelf(e.target.value)} title="current shelf label" />
          {!running ? (
            <button onClick={start} disabled={!!sweepId && !!packet?.sweep.finished_at}>Start sweep</button>
          ) : (
            <button onClick={stop}>Stop sweep → build packet</button>
          )}
          <button disabled={!running} onClick={() => void captureFrame()}>Capture now</button>
          <select
            disabled={running}
            value=""
            onChange={(e) => {
              // Reopen a saved sweep for review (no camera needed).
              const id = e.target.value;
              if (id) void get<Packet>(`/api/sweeps/${id}`).then((p) => { setSweepId(id); setPacket(p); setMessage("Loaded saved sweep for review."); });
            }}
          >
            <option value="">Review saved sweep…</option>
            {saved.map((s) => (
              <option key={s.id} value={s.id}>{s.captured_at.slice(0, 16)} · {s.country} · {s.id.slice(0, 8)}</option>
            ))}
          </select>
        </div>
        <video ref={videoRef} muted playsInline className={running ? "" : "hidden"} />
        <p className="message">{message}</p>
      </section>

      {packet && (
        <>
          <Inventory packet={packet} selected={selected} onSelect={setSelected} />
          <Conversation packet={packet} listening={listening} onSend={sendTurn} />
          {packet.sweep.finished_at && (
            <section className="card">
              <h2>Claim packet</h2>
              <p className="row">
                <a href={fileUrl(`data/claims/${packet.sweep.id}/claim_packet.json`)} target="_blank" rel="noreferrer">claim_packet.json</a>
                <a href={`${API}/api/sweeps/${packet.sweep.id}/report`} target="_blank" rel="noreferrer">report.html</a>
                <a href={`${API}/api/sweeps/${packet.sweep.id}/bundle`}>evidence bundle (zip)</a>
                <a href={`${API}/api/sweeps/${packet.sweep.id}/prices`} target="_blank" rel="noreferrer">price details</a>
              </p>
              <p className="muted">{packet.review_queue.length} review findings. Say "compare prices in the United Kingdom" to price ten books for a second country.</p>
            </section>
          )}
        </>
      )}
    </main>
  );
}
