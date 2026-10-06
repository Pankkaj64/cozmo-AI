import { DemoRecorder } from "./DemoRecorder";
import { useEffect, useRef, useState } from "react";
import { recordCamera } from "./recording";
import { ReviewTools } from "./ReviewTools";
import { errorDetails, loggedFetch, logStep } from "./logger";

type SpeechResult = { isFinal?: boolean; [index: number]: { transcript: string }; length: number };
type SpeechEvent = { resultIndex: number; results: SpeechResult[] };
type SpeechRecognizer = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((event: SpeechEvent) => void) | null;
  onerror: ((event?: { error?: string }) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
};
declare global {
  interface Window {
    SpeechRecognition?: new () => SpeechRecognizer;
    webkitSpeechRecognition?: new () => SpeechRecognizer;
  }
}

type CropVerification = {status: string; agreed: boolean; category?: string; reason?: string};
export type Book = {
  id: string;
  title: string;
  author: string;
  publisher?: string;
  edition?: string;
  proposed_publisher?: string;
  status: string;
  shelf: string;
  frame_ref: string;
  crop_verification?: CropVerification;
  isbn?: string;
  proposed_title?: string;
  proposed_author?: string;
  object_bbox?: number[];
  ocr_lines?: {text: string}[];
  partial?: boolean;
  description?: string;
  spine_height_cm?: number;
  spine_thickness_cm?: number;
  replacement_cost?: { amount?: number };
  used_value?: { amount?: number };
};
export type Packet = {
  verification_progress?: {status: string; completed: number; total: number};
  capabilities?: Record<string, {status: string; note: string}>;
  frames?: { frame_ref: string; shelf?: string; vision_status: string; count_status?: string; notes: string[]; ocr_text?: string[]; primary_count?: number; candidate_count?: number; validation?: { count: number | null } }[];
  guidance?: {code: string; text: string; action: string};
  transcript?: {id: string; role: string; text: string}[];
  workflow?: Record<string, {name: string; status: string; elapsed_s?: number}>;
  research?: {ref_id: string; identification?: {status: string; url?: string}; pricing?: {status: string; reason?: string; offers: {url: string; listing_title: string; amount: number; currency: string; condition_assumed: string}[]}; error?: string}[];
  room?: Record<string, unknown>;
  audit_trail?: {id: string; time: string; step: string}[];
  videos?: {ref: string}[];
  sweep: { country: string; currency: string; duration_s: number };
  books: Book[];
  items: {
    id: string;
    category: string;
    object_bbox?: number[];
    material?: string;
    proposed_material?: string;
    proposed_brand_model?: string;
    reader_category?: string;
    dismissed_by_reader?: boolean;
    brand_model?: string;
    category_verified?: boolean;
    crop_verification?: CropVerification;
    is_print?: boolean;
    dimensions_cm?: {w: number | null; h: number | null; d: number | null};
    replacement_cost?: {low: number | null; high: number | null};
    description: string;
    status: string;
    frame_ref: string;
  }[];
  totals: Record<string, number>;
  review_queue: { ref_id: string; reason: string }[];
};
const API = import.meta.env.VITE_API_URL || "";

async function responseError(response: Response, fallback: string): Promise<string> {
  try {
    const body = await response.json();
    const detail = body.detail || body.message;
    return detail ? `${fallback}: ${detail}` : `${fallback} (HTTP ${response.status})`;
  } catch {
    return `${fallback} (HTTP ${response.status})`;
  }
}

export default function App() {
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const busyRef = useRef(false);
  const capturePromiseRef = useRef<Promise<void> | null>(null);
  const snapshotPromiseRef = useRef<Promise<void> | null>(null);
  const queuedFrameRef = useRef(new Map<number, { blob: Blob; shelf: string; sweepId: string }>());
  const snapshotSequenceRef = useRef(0);
  const [queuedCount, setQueuedCount] = useState(0);
  const startingRef = useRef(false);
  const recordingRef = useRef<ReturnType<typeof recordCamera>>(null);
  const voiceWantedRef = useRef(false);
  const mutedRef = useRef(false);
  const speakingRef = useRef(false);
  const spokenTextRef = useRef("");
  const dialogueVersionRef = useRef(0);
  const dialogueBusyRef = useRef(false);
  const userSpeakingRef = useRef(false);
  const captureHandlerRef = useRef<() => Promise<void>>(() => Promise.resolve());
  const lastGuidanceRef = useRef("");
  const contextRef = useRef({ shelf: "Shelf 1", selected: "" });
  const [savedSweeps, setSavedSweeps] = useState<{id: string; captured_at: string; country: string}[]>([]);
  const [selected, setSelected] = useState("");
  const [turnText, setTurnText] = useState("");
  const [stageStatus, setStageStatus] = useState<Record<string, {name: string; status: string; elapsed_s?: number}>>({});
  const [recordVideo, setRecordVideo] = useState(true);
  const [recording, setRecording] = useState(false);
  const [mediaWarning, setMediaWarning] = useState("");

  const speechRef = useRef<SpeechRecognizer | null>(null);
  const [country, setCountry] = useState("United Arab Emirates");
  const [currency, setCurrency] = useState("AED");
  const [countryCode, setCountryCode] = useState("AE");
  const [shelf, setShelf] = useState("Shelf 1");
  const [sweepId, setSweepId] = useState("");
  const [packet, setPacket] = useState<Packet | null>(null);
  const [running, setRunning] = useState(false);
  const [starting, setStarting] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [finishing, setFinishing] = useState(false);
  const [voiceEnabled, setVoiceEnabled] = useState(false);
  const [previewFrame, setPreviewFrame] = useState("");
  const [message, setMessage] = useState(
    "Choose a country and currency, then start the sweep.",
  );
  const [files, setFiles] = useState<{
    json_file: string;
    report_file: string;
  } | null>(null);
  const latestFrame = packet?.frames?.at(-1);
  const evidenceLines = [...(packet?.books || []).map(book => ({...book, label: book.title || book.proposed_title || "Unidentified book", kind: "book"})), ...(packet?.items || []).map(item => ({...item, label: item.category, kind: "item"}))];
  const evidenceRef = evidenceLines.find(line => line.id === selected)?.frame_ref || latestFrame?.frame_ref;
  const evidenceObjects = evidenceLines.filter(line => line.frame_ref === evidenceRef && line.object_bbox);
  contextRef.current = { shelf, selected };
  captureHandlerRef.current = captureFrame;
  const countUncertain = latestFrame && (latestFrame.vision_status !== "ok" || latestFrame.count_status === "needs_review");

  useEffect(() => {
    logStep("app.mounted");
    return () => {
    logStep("app.cleanup");
    voiceWantedRef.current = false;
    recordingRef.current?.discard();
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    speechRef.current?.stop();
    speechRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (running && sweepId) void captureFrame();
  }, [running, sweepId]);

  useEffect(() => {
    if (!running) return;
    logStep("capture.timer.started", { sweepId, intervalMs: 4500 });
    const timer = window.setInterval(() => {
      void captureFrame();
    }, 4500);
    return () => { window.clearInterval(timer); logStep("capture.timer.stopped", { sweepId }); };
  }, [running, shelf, sweepId]);

  useEffect(() => {
    if (!sweepId || typeof EventSource === "undefined") return;
    const events = new EventSource(`${API}/api/sweeps/${sweepId}/events`);
    events.onmessage = event => {
      const data = JSON.parse(event.data);
      setStageStatus(data.workflow || {});
      if (data.packet) setPacket(data.packet);
      if (!dialogueBusyRef.current && !speakingRef.current && data.progress?.stage === "perception" && data.progress.guidance) {
        setMessage(data.progress.guidance);
      }
    };
    events.onerror = () => logStep("workflow.events.reconnecting", { sweepId }, "warn");
    return () => events.close();
  }, [sweepId]);

  async function sendTurn(text: string, id = sweepId) {
    if (!id || !text.trim()) return;
    const version = ++dialogueVersionRef.current;
    dialogueBusyRef.current = true;
    mutedRef.current = false;
    window.speechSynthesis?.cancel(); // Barge-in: the claimant can interrupt the agent.
    try {
    const response = await loggedFetch(`${API}/api/sweeps/${id}/turns`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, shelf: contextRef.current.shelf, ref_id: contextRef.current.selected }),
    });
    if (!response.ok) throw new Error(await responseError(response, "Could not process your correction"));
    const data = await response.json();
    if (version !== dialogueVersionRef.current) return;
    setPacket(data.packet);
    setMessage(data.reply);
    if (data.action === "next_shelf") setShelf(current => {
      const match = current.match(/(\d+)$/);
      return match ? current.replace(/\d+$/, String(Number(match[1]) + 1)) : `${current} next`;
    });
    if (data.action === "capture") void captureHandlerRef.current();
    if (data.action === "mute") mutedRef.current = true;
    else void speak(data.reply);
    setTurnText("");
    } finally { if (version === dialogueVersionRef.current) dialogueBusyRef.current = false; }
  }

  function guide(packet: Packet) {
    if (dialogueBusyRef.current || speakingRef.current || userSpeakingRef.current) return;
    const guidance = packet.guidance;
    const analyzedShelf = packet.frames?.at(-1)?.shelf;
    if (analyzedShelf && analyzedShelf !== contextRef.current.shelf) return;
    if (guidance && lastGuidanceRef.current !== guidance.text) {
      lastGuidanceRef.current = guidance.text;
      setMessage(guidance.text);
      if (streamRef.current) void speak(guidance.text);
    }
  }

  async function speak(text: string) {
    if ("speechSynthesis" in window && !mutedRef.current) {
      logStep("voice.prompt", { characters: text.length });
      window.speechSynthesis.cancel();
      spokenTextRef.current = text.toLowerCase();
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.onstart = () => { speakingRef.current = true; };
      utterance.onend = utterance.onerror = () => { speakingRef.current = false; };
      window.speechSynthesis.speak(utterance);
    }
  }
  async function start() {
    if (startingRef.current || streamRef.current) { logStep("sweep.start.skipped", { reason: "Camera already opening or active" }); return; }
    logStep("sweep.start.clicked");
    startingRef.current = true;
    setStarting(true);
    setMessage("Opening camera. Allow camera access when your browser asks.");
    try {
      if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
        throw new Error("Camera access requires HTTPS or localhost in a browser that supports cameras.");
      }
      logStep("camera.permission.requested", { idealWidth: 1920, idealHeight: 1080 });
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "environment", width: { ideal: 1920 }, height: { ideal: 1080 } },
        audio: false,
      });
      streamRef.current = stream;
      logStep("camera.stream.opened", { tracks: stream.getTracks().length });
      const video = videoRef.current;
      if (!video) throw new Error("Camera preview is unavailable on this page.");
      video.srcObject = stream;
      await video.play();
      logStep("camera.preview.playing", { width: video.videoWidth, height: video.videoHeight });
      const response = await loggedFetch(`${API}/api/sweeps`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ country, country_code: countryCode, currency, device: "browser camera" }),
      });
      if (!response.ok) throw new Error(await responseError(response, "Could not start sweep"));
      const data = await response.json();
      logStep("sweep.created", { sweepId: data.sweep_id });
      setSweepId(data.sweep_id);
      setPacket(data.packet || null);
      mutedRef.current = false;
      lastGuidanceRef.current = "";
      queuedFrameRef.current.clear();
      setQueuedCount(0);
      setMediaWarning("");
      if (recordVideo) {
        try { recordingRef.current = recordCamera(stream, API, data.sweep_id); setRecording(!!recordingRef.current); if (!recordingRef.current) setMediaWarning("This browser cannot save camera video; sampled images will still be saved."); }
        catch (error) { logStep("video.unavailable", errorDetails(error), "warn"); setMediaWarning("Video recording could not start; sampled images will still be saved."); }
      }
      setFiles(null);
      setRunning(true);
      setMessage(
        "Slowly pan across every shelf, wall and visible floor area. Keep text sharp and avoid glare.",
      );
      startVoiceInput(data.sweep_id);
      void speak(data.packet?.guidance?.text || "Let’s start. Slowly pan across every shelf and wall. I will ask you to pause if a frame is unclear.");
    } catch (error) {
      logStep("sweep.start.failed", errorDetails(error), "error");
      streamRef.current?.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
      if (videoRef.current) videoRef.current.srcObject = null;
      setRunning(false);
      setMessage(
        error instanceof Error
          ? `${error.message} Check camera permission for this localhost site and make sure the camera is not in use by another app.`
          : "Camera or backend access failed.",
      );
    } finally {
      startingRef.current = false;
      setStarting(false);
    }
  }
  function startVoiceInput(id: string) {
    const Speech = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Speech) {
      logStep("voice.input.unavailable", { reason: "Browser has no speech recognition API" }, "warn");
      setMessage(
        "Camera is running. Voice input is not available in this browser; you can still use the screen.",
      );
      return;
    }
    try {
      logStep("voice.input.starting", { sweepId: id });
      const recognition = new Speech();
      recognition.lang = "en-US";
      recognition.continuous = true;
      recognition.interimResults = true;
      recognition.onresult = (event) => {
        for (let index = event.resultIndex; index < event.results.length; index++) {
          const result = event.results[index];
          const text = result?.[0]?.transcript?.trim();
          if (!text) continue;
          if (speakingRef.current && spokenTextRef.current.includes(text.toLowerCase().replace(/[.!?]$/, ""))) continue;
          if (result.isFinal === false) {
            userSpeakingRef.current = true;
            if (dialogueBusyRef.current) { ++dialogueVersionRef.current; dialogueBusyRef.current = false; }
            if (speakingRef.current) { window.speechSynthesis?.cancel(); speakingRef.current = false; }
            continue;
          }
          userSpeakingRef.current = false;
          logStep("voice.correction.received", { sweepId: id, characters: text.length });
          void sendTurn(text, id).catch(error => {
            logStep("voice.turn.failed", errorDetails(error), "error");
            setMessage(error instanceof Error ? error.message : "Voice turn failed.");
          });
        }
      };
      recognition.onerror = (event) => {
        if (event?.error === "not-allowed" || event?.error === "service-not-allowed") voiceWantedRef.current = false;
        setVoiceEnabled(false);
        logStep("voice.input.error", { sweepId: id, reason: event?.error }, "warn");
      };
      recognition.onend = () => {
        userSpeakingRef.current = false;
        setVoiceEnabled(false);
        if (voiceWantedRef.current && streamRef.current) {
          window.setTimeout(() => {
            if (!voiceWantedRef.current) return;
            try { recognition.start(); setVoiceEnabled(true); } catch { /* Already restarting. */ }
          }, 800);
        }
      };
      voiceWantedRef.current = true;
      recognition.start();
      logStep("voice.input.started", { sweepId: id });
      speechRef.current = recognition;
      setVoiceEnabled(true);
    } catch (error) {
      logStep("voice.input.failed", errorDetails(error), "warn");
      setVoiceEnabled(false);
      setMessage("Camera is running. Voice input could not start; use the camera controls to continue.");
    }
  }
  function captureFrame(): Promise<void> {
    if (busyRef.current) { logStep("capture.skipped", { sweepId, reason: "Snapshot already encoding" }); return snapshotPromiseRef.current || Promise.resolve(); }
    const pending = captureAndQueue();
    snapshotPromiseRef.current = pending;
    return pending;
  }
  async function captureAndQueue() {
    if (busyRef.current || !videoRef.current || !canvasRef.current || !sweepId) { logStep("capture.skipped", { sweepId, reason: "Capture is not ready" }); return; }
    if (queuedFrameRef.current.size >= 120) {
      logStep("capture.backlog.full", {sweepId, queued: queuedFrameRef.current.size}, "warn");
      setMediaWarning("Image analysis is behind: pause your pan while queued views finish. Automatic snapshots are paused at 120 pending images; continuous video is still recording if enabled.");
      return;
    }
    const video = videoRef.current, canvas = canvasRef.current;
    if (video.readyState < 2 || !video.videoWidth || !video.videoHeight) { logStep("capture.skipped", { sweepId, reason: "Video has no decoded frame", readyState: video.readyState }); return; }
    logStep("capture.started", { sweepId, shelf, width: video.videoWidth, height: video.videoHeight });
    busyRef.current = true;
    try {
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      canvas.getContext("2d")?.drawImage(video, 0, 0);
      setPreviewFrame(canvas.toDataURL("image/jpeg", 0.94));
      const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.94));
      if (!blob) { logStep("capture.failed", { sweepId, reason: "JPEG encoding returned no image" }, "error"); return; }
      logStep("capture.encoded", { sweepId, bytes: blob.size });
      const sequence = ++snapshotSequenceRef.current;
      // Keep every sampled view. Different books can appear under the same shelf label.
      queuedFrameRef.current.set(sequence, { blob, shelf, sweepId });
      setQueuedCount(queuedFrameRef.current.size);
      logStep("capture.queued", { sweepId, shelf, sequence, pending: queuedFrameRef.current.size });
    } catch (error) {
      logStep("capture.failed", { sweepId, ...errorDetails(error) }, "error");
      setMessage(error instanceof Error ? error.message : "Could not capture frame.");
      return;
    } finally {
      busyRef.current = false;
    }
    if (!capturePromiseRef.current) {
      const pending = uploadQueuedFrames();
      capturePromiseRef.current = pending;
      void pending.finally(() => {
        if (capturePromiseRef.current === pending) capturePromiseRef.current = null;
      });
    }
    await capturePromiseRef.current;
  }
  async function uploadQueuedFrames() {
    setProcessing(true);
    try {
      while (queuedFrameRef.current.size) {
        const [sequence, frame] = queuedFrameRef.current.entries().next().value!;
        queuedFrameRef.current.delete(sequence);
        setQueuedCount(queuedFrameRef.current.size);
        logStep("frame.upload.started", { sweepId: frame.sweepId, shelf: frame.shelf, bytes: frame.blob.size });
        setMessage(`Analyzing saved view ${sequence}. ${queuedFrameRef.current.size} views waiting. Pan slowly and pause for readable spines.`);
        try {
          const form = new FormData();
          form.append("image", frame.blob, "frame.jpg");
          form.append("shelf", frame.shelf);
          const response = await loggedFetch(`${API}/api/sweeps/${frame.sweepId}/frames`, { method: "POST", body: form });
          if (!response.ok) throw new Error(await responseError(response, "Frame upload failed"));
          const data = await response.json();
          logStep("frame.processed", { sweepId: frame.sweepId, frameRef: data.frame_ref, status: data.candidate?.vision_status, books: data.packet.books?.length, primaryCount: data.candidate?.primary_count, validatorCount: data.candidate?.validation?.count, notes: data.candidate?.notes }, data.candidate?.vision_status === "ok" ? "info" : "warn");
          setPacket(data.packet);
          setMessage(data.candidate?.notes?.[0] || "Frame analyzed. Inventory keeps books and room items seen throughout the sweep.");
          guide(data.packet);
        } catch (error) {
          logStep("frame.upload.failed", { sweepId: frame.sweepId, ...errorDetails(error) }, "error");
          setMessage(error instanceof Error ? error.message : "Could not process this frame.");
        }
      }
    } finally {
      setProcessing(false);
      logStep("capture.queue.drained");
    }
  }
  async function finish() {
    logStep("sweep.finish.clicked", { sweepId });
    const lastSnapshot = busyRef.current ? snapshotPromiseRef.current : captureFrame();
    setFinishing(true);
    setRunning(false);
    voiceWantedRef.current = false;
    speechRef.current?.stop();
    speechRef.current = null;
    setVoiceEnabled(false);
    const videoSave = recordingRef.current?.save().catch(error => {
      logStep("video.save.failed", errorDetails(error), "error");
      return { error: error instanceof Error ? error.message : "Video save failed" };
    });
    recordingRef.current = null;
    setRecording(false);
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
    logStep("camera.stopped", { sweepId });
    logStep("voice.input.stopped", { sweepId });
    if (!sweepId) { logStep("sweep.finish.skipped", { reason: "No active sweep" }, "warn"); setFinishing(false); return; }
    try {
      const stopped = await loggedFetch(`${API}/api/sweeps/${sweepId}/stop-capture`, { method: "POST" });
      if (!stopped.ok) throw new Error(await responseError(stopped, "Could not mark capture as stopped"));
      setMessage("Finishing sweep. Waiting for the current frame to finish processing…");
      logStep("sweep.finish.waiting_for_frames", { sweepId });
      await lastSnapshot;
      await capturePromiseRef.current;
      const videoResult = await videoSave;
      if (videoResult?.error) setMediaWarning(videoResult.error);
      const response = await loggedFetch(`${API}/api/sweeps/${sweepId}/finish`, {
        method: "POST",
      });
      if (!response.ok) throw new Error(await responseError(response, "Could not save the claim packet"));
      const data = await response.json();
      setPacket(data.packet);
      setFiles({ json_file: data.json_file, report_file: data.report_file });
      logStep("sweep.saved", { sweepId, jsonFile: data.json_file, reportFile: data.report_file, books: data.packet.totals.book_count, reviewFlags: data.packet.review_queue.length });
      const failed = data.packet.frames?.filter((frame: { vision_status: string }) => frame.vision_status !== "ok").length || 0;
      const verifying = data.packet.verification_progress?.status === "running";
      setMessage(verifying
        ? "Sweep saved. Remaining crop checks are running; reports include verified names only and will update when checks finish."
        : failed
        ? `Sweep saved; ${failed} frame(s) need review or another capture. Check the count comparison and review reasons below.`
        : "Sweep saved. Review every flagged line and complete room measurements and sourced prices before submitting a claim.");
      void speak(verifying
        ? "The sweep is saved. I’m checking the remaining crops, and uncertain names will stay out of the report."
        : failed
        ? "The sweep is saved, but the book count needs review. Please check the reasons on screen."
        : `The sweep is complete. I logged ${data.packet.totals.book_count} candidate books and ${data.packet.review_queue.length} lines for review.`);
    } catch (error) {
      logStep("sweep.finish.failed", { sweepId, ...errorDetails(error) }, "error");
      setMessage(
        error instanceof Error ? error.message : "Could not finish sweep.",
      );
    } finally {
      setFinishing(false);
    }
  }
  return (
    <main>
      <header>
        <div className="brand">LC</div>
        <div>
          <p className="eyebrow">CONTENTS CLAIM · FIELD CAPTURE</p>
          <h1>Library sweep</h1>
        </div>
        <span className={`status ${running ? "live" : ""}`}>
          {running ? "● LIVE SWEEP" : "READY"}
        </span>
      </header>
      <section className="intro">
        <h2>Document the room in one continuous pass.</h2>
        <p>
          Pan slowly along the shelves, then include furniture, lamps, art, rugs and electronics.
          Keep books on the shelves. Earlier detections stay in the inventory as you move.
        </p>
      </section>
      <section className="controls">
        <label>
          Country
          <input
            value={country}
            onChange={(e) => setCountry(e.target.value)}
            disabled={running}
          />
        </label>
        <label>
          Country code
          <input value={countryCode} maxLength={2} onChange={e => setCountryCode(e.target.value.toUpperCase())} disabled={running} aria-label="Two-letter delivery country" />
        </label>
        <label>
          Currency
          <input
            value={currency}
            onChange={(e) => setCurrency(e.target.value.toUpperCase())}
            disabled={running}
          />
        </label>
        <label>
          Current shelf
          <input
            value={shelf}
            onChange={(e) => setShelf(e.target.value)}
            disabled={!running}
          />
        </label>
        {!running ? (
          <button className="primary" onClick={start} disabled={starting || finishing}>
            {starting ? "Opening camera…" : finishing ? `Saving sweep… (${queuedCount} views waiting)` : "Start camera sweep"}
          </button>
        ) : (
          <button className="stop" onClick={finish}>
            Finish sweep
          </button>
        )}
      </section>
      {!running && !starting && !finishing && <div className="saved-sweeps">
        <button className="quiet" onClick={async () => {
          try { const response = await loggedFetch(`${API}/api/sweeps`); if (!response.ok) throw new Error("Could not load saved sweeps"); setSavedSweeps(await response.json()); }
          catch (error) { setMessage(error instanceof Error ? error.message : "Could not load saved sweeps"); }
        }}>Review saved sweeps</button>
        {savedSweeps.length > 0 && <select aria-label="Open saved sweep" defaultValue="" onChange={async e => {
          if (!e.target.value) return;
          try { const id = e.target.value; const response = await loggedFetch(`${API}/api/sweeps/${id}`); if (!response.ok) throw new Error("Could not open sweep"); const saved = await response.json();
            setSweepId(id); setPacket(saved); setFiles({json_file:`data/claims/${id}/claim_packet.json`,report_file:`data/claims/${id}/report.html`});
            setCountry(saved.sweep.country); setCurrency(saved.sweep.currency); setPreviewFrame(saved.frames?.at(-1) ? `${API}/${saved.frames.at(-1).frame_ref}` : ""); setMessage("Saved sweep opened for review. Update exports after making corrections.");
          } catch (error) { setMessage(error instanceof Error ? error.message : "Could not open sweep"); }
        }}><option value="">Choose a saved sweep</option>{savedSweeps.map(sweep => <option key={sweep.id} value={sweep.id}>{sweep.captured_at} · {sweep.country}</option>)}</select>}
      </div>}
      <label className="record-option"><input type="checkbox" checked={recordVideo} disabled={running || starting} onChange={e => setRecordVideo(e.target.checked)} /> Save continuous camera video as evidence (video only). Detection uses sampled images.</label>
      {mediaWarning && <p role="alert" className="message">{mediaWarning}</p>}
      <section className="workspace">
        <div className="camera card">
          <div className="section-title">
            <h3>Camera</h3>
            <span>{recording ? "● Recording video + sampled images" : "Sampled image analysis"}</span>
          </div>
          <div className="video-wrap">
            <video
              ref={videoRef}
              autoPlay
              muted
              playsInline
              className={running || starting ? "camera-video" : "camera-video-hidden"}
            />
            {!running && !starting && previewFrame ? (
              <img className="camera-still" src={previewFrame} alt="Last captured camera frame" />
            ) : !running && !starting ? (
              <div className="camera-empty">Camera preview appears here</div>
            ) : null}
            <canvas ref={canvasRef} hidden />
          </div>
          <p className="message" role="status">{message}</p>
          {(processing || queuedCount > 0) && <p className="message">{processing ? "Analyzing one image" : "Waiting"} · {queuedCount} saved views queued. Finish waits for queued images.</p>}
          <div className="camera-actions">
            <button
              className="quiet"
              disabled={!running}
              onClick={() => void captureFrame()}
            >
              {processing ? "Queue this view next" : "Capture this shelf now"}
            </button>
            <span>
              {voiceEnabled ? "Voice input on" : "Voice input unavailable"}
            </span>
          </div>
        </div>
        <div className="inventory card">
          <div className="section-title">
            <h3>Live inventory</h3>
            <span>{countUncertain ? "Latest count needs review" : `${packet?.books.length || 0} candidate books`}</span>
          </div>
          <div className="stats inventory-stats">
            <div>
              <b>{countUncertain && !packet?.books.length ? "?" : packet?.totals?.book_count || 0}</b>
              <small>Books across this sweep</small>
            </div>
            <div>
              <b>{packet?.totals?.books_identified || 0}</b>
              <small>Titles reviewed</small>
            </div>
            <div><b>{packet?.items.length || 0}</b><small>Room-item candidates</small></div>
            <div>
              <b>{packet?.review_queue.length || 0}</b>
              <small>Review flags</small>
            </div>
          </div>
          {countUncertain && latestFrame?.primary_count !== undefined && (
            <p className="message">Located {latestFrame.candidate_count ?? latestFrame.primary_count} candidates; primary detector: {latestFrame.primary_count}; second detector: {latestFrame.validation?.count ?? "unavailable"}. Edge crops or count disagreement need review. Keep the complete books in view.</p>
          )}
          {!!latestFrame?.ocr_text?.length && <p className="message">Text read in this frame: {latestFrame.ocr_text.join(" · ")}. A text reading alone does not identify its book.</p>}
          {evidenceRef && evidenceObjects.length > 0 && <figure className="detection-evidence">
            <div className="detection-image">
              <img src={`${API}/${evidenceRef}`} alt="Saved evidence with book and room-item candidate boxes" />
              {evidenceObjects.map((line, index) => {
                const [left, top, right, bottom] = line.object_bbox!;
                return <button key={line.id} className={`detection-box ${line.kind === "item" ? "item-box" : ""} ${selected === line.id ? "selected" : ""}`} style={{left: `${left*100}%`, top: `${top*100}%`, width: `${(right-left)*100}%`, height: `${(bottom-top)*100}%`}} onClick={() => setSelected(line.id)} aria-label={`Review ${line.kind}: ${line.label}`}><span>{index+1} · {line.kind === "item" ? line.label : "Book"}</span></button>;
              })}
            </div>
            <figcaption>{selected ? "Evidence for selected inventory line" : "Latest analyzed view"} · Yellow: books. Blue: room items. Select any saved line below to revisit its evidence.</figcaption>
          </figure>}
          <div className="book-list">
            {packet?.books.length ? (
              packet.books
                .slice()
                .reverse()
                .map((book) => (
                  <article key={book.id}>
                    <div>
                      <b>{book.title || (book.proposed_title ? `Possible: ${book.proposed_title}` : "Unidentified book")}</b>
                      <small>{book.description || book.author || book.shelf}</small>
                      {book.crop_verification && <small>Crop check: {book.crop_verification.status}{book.crop_verification.reason ? ` · ${book.crop_verification.reason}` : ""}</small>}
                      {!!book.ocr_lines?.length && <small>Crop text: {book.ocr_lines.map(line => line.text).join(" · ")}</small>}
                      <small>Author: {book.author || (book.proposed_author ? `possible ${book.proposed_author}` : "unknown")} · Publisher: {book.publisher || (book.proposed_publisher ? `possible ${book.proposed_publisher}` : "unknown")}</small>
                      <small>Edition: {book.edition || "unknown"} · ISBN: {book.isbn || "not visible"} · Spine: {book.spine_height_cm ?? "?"} × {book.spine_thickness_cm ?? "?"} cm</small>
                      <small>Replacement: {book.replacement_cost?.amount ?? "unsourced"} · Used: {book.used_value?.amount ?? "unsourced"} {currency}</small>
                    </div>
                    <button className="tag" onClick={() => setSelected(book.id)}>{selected === book.id ? "Selected" : "Review"}</button>
                  </article>
                ))
            ) : (
              <div className="empty">
                {processing ? "Detecting books in the captured frame…"
                  : countUncertain
                    ? "Book count is unverified. See the count comparison and review reasons."
                    : packet ? "No book candidates returned. Aim closer at the books and capture again."
                    : "No frames yet. Inventory appears as the camera moves."}
              </div>
            )}
          </div>
          <h3 className="item-heading">Room contents · {packet?.items.length || 0} candidates</h3>
          {packet?.verification_progress && <p className="message">Remaining crop checks: {packet.verification_progress.completed}/{packet.verification_progress.total} · {packet.verification_progress.status}. Reports include verified names only.</p>}
          <div className="book-list item-list">
            {packet?.items.map(item => <article key={item.id}>
              <div><b>{item.category} {!item.category_verified && "· needs review"}</b>
                <small>{item.description}</small>
                {item.crop_verification && <small>Crop check: {item.crop_verification.status}{item.crop_verification.status === "conflict" ? ` · verifier sees ${item.crop_verification.category}` : ""}{item.crop_verification.reason ? ` · ${item.crop_verification.reason}` : ""}</small>}
                {item.dismissed_by_reader && <small>Possible false detection: reader sees {item.reader_category}. Review or exclude.</small>}
                <small>Material: {item.material || (item.proposed_material ? `possible ${item.proposed_material}` : "unknown")} · Brand/model: {item.brand_model || item.proposed_brand_model || "unknown"}</small>
                <small>W × H × D: {item.dimensions_cm?.w ?? "?"} × {item.dimensions_cm?.h ?? "?"} × {item.dimensions_cm?.d ?? "?"} cm</small>
                <small>Replacement: {item.replacement_cost?.low ?? "unsourced"}–{item.replacement_cost?.high ?? "unsourced"} {currency}</small>
              </div><button className="tag" onClick={() => setSelected(item.id)}>{selected === item.id ? "Selected" : "Review"}</button>
            </article>)}
            {!packet?.items.length && <p className="message">No room-item candidates yet. Include shelving, furniture, coffee machines, lamps, framed art, rugs, electronics and decor in this same sweep.</p>}
          </div>
        </div>
      </section>
      {packet?.capabilities && <section className="card lower">
        <h3>Assignment features and outstanding evidence</h3>
        <div className="feature-grid">{Object.entries(packet.capabilities).map(([name, feature]) => <article key={name}><b>{name.replaceAll("_", " ")} · {feature.status.replaceAll("_", " ")}</b><p>{feature.note}</p></article>)}</div>
        <p className="message">Measured shelf run: {packet.totals.shelf_run_m ?? 0} m. Floor: {String(packet.room?.floor_area_m2 ?? "unknown")} m² / {String(packet.room?.floor_area_ft2 ?? "unknown")} ft². Walls: {String(packet.room?.wall_area_m2 ?? "unknown")} m² / {String(packet.room?.wall_area_ft2 ?? "unknown")} ft². Shelved wall: {String(packet.room?.shelved_wall_area_m2 ?? "unknown")} m².</p>
      </section>}
      {sweepId && <section className="card lower">
        <div className="section-title"><h3>Live agent</h3><button className="quiet" onClick={() => { window.speechSynthesis?.cancel(); speakingRef.current = false; mutedRef.current = true; }}>Stop talking</button></div>
        <div className="stage-list">{Object.values(stageStatus).map(step => <span className="tag" key={step.name}>{step.name}: {step.status} {step.elapsed_s !== undefined ? `(${step.elapsed_s}s)` : ""}</span>)}</div>
        <div className="transcript" aria-live="polite">{packet?.transcript?.slice(-12).map(turn => <p key={turn.id}><b>{turn.role === "agent" ? "Agent" : "You"}:</b> {turn.text}</p>)}</div>
        <form className="turn-form" onSubmit={event => { event.preventDefault(); void sendTurn(turnText).catch(error => setMessage(error.message)); }}>
          <input aria-label="Talk to the agent" placeholder="Ask a question, discuss a result, or tell me what to do next…" value={turnText} onChange={event => setTurnText(event.target.value)} />
          <button type="submit" className="primary" disabled={!turnText.trim()}>Send</button>
        </form>
        <p className="message">Talk naturally and interrupt me whenever you need. Select an item when giving a specific correction. Browser voice recognition may use your browser's speech service. Typed corrections work in every browser.</p>
      </section>}
      {packet && (
        <section className="card lower">
          <div className="section-title">
            <h3>Review before submission</h3>
            <span>{packet.items.length} non-book candidates</span>
          </div>
          <ReviewTools packet={packet} api={API} sweepId={sweepId} selected={selected} onSelect={setSelected} onUpdate={setPacket} />
          {!!packet.videos?.length && <p>{packet.videos.map(video => <a key={video.ref} href={`${API}/${video.ref}`} target="_blank" rel="noreferrer">Open continuous camera recording</a>)}</p>}
          {packet.review_queue.length > 0 && (
            <details className="review-findings"><summary>{packet.review_queue.length} outstanding review findings</summary><ul>
              {packet.review_queue.map((entry, index) => (
                <li key={`${entry.ref_id}-${index}`}>{entry.reason}</li>
              ))}
            </ul></details>
          )}
          {files && <button className="quiet" onClick={async () => {
            try { const response = await loggedFetch(`${API}/api/sweeps/${sweepId}/finish`, { method: "POST" });
              if (!response.ok) throw new Error(await responseError(response, "Export failed"));
              const data = await response.json(); setPacket(data.packet); setFiles(data); setMessage("Updated packet and report saved.");
            } catch (error) { setMessage(error instanceof Error ? error.message : "Export failed"); }
          }}>Update exports after review</button>}
          {files && (
            <div className="downloads">
              <a href={`${API}/${files.json_file}`} target="_blank">
                Open claim_packet.json
              </a>
              <a href={`${API}/${files.report_file}`} target="_blank">
                Open readable report
              </a>
              <a href={`${API}/api/sweeps/${sweepId}/bundle`}>Download packet + evidence ZIP</a>
            </div>
          )}
        </section>
      )}
      <DemoRecorder />
      {sweepId && !running && !finishing && !files && <button onClick={() => void finish()}>Retry saving this sweep</button>}
      <footer>
        Capture only a space you have permission to document. Avoid people and
        personal papers.
      </footer>
    </main>
  );
}
