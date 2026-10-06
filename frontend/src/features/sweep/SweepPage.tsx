import { useEffect, useRef, useState } from "react";
import { DemoRecorder } from "../../components/review/DemoRecorder";
import { ReviewPanel } from "../../components/review/ReviewPanel";
import { CameraPanel } from "../../components/capture/CameraPanel";
import { InventoryPanel } from "../../components/inventory/InventoryPanel";
import { ConversationPanel } from "../../components/conversation/ConversationPanel";
import { SweepControls } from "../../components/capture/SweepControls";
import { recordCamera } from "../../lib/recording";
import { errorDetails, loggedFetch, logStep } from "../../lib/logger";
import { API, responseError } from "../../lib/api";
import type { Packet } from "../../types/packet";
import type { SpeechRecognizer } from "../../lib/speech";

export function SweepPage() {
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const busyRef = useRef(false);
  const capturePromiseRef = useRef<Promise<void> | null>(null);
  const snapshotPromiseRef = useRef<Promise<void> | null>(null);
  const queuedFrameRef = useRef(
    new Map<number, { blob: Blob; shelf: string; sweepId: string }>(),
  );
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
  const captureHandlerRef = useRef<() => Promise<void>>(() =>
    Promise.resolve(),
  );
  const lastGuidanceRef = useRef("");
  const contextRef = useRef({ shelf: "Shelf 1", selected: "" });
  const [savedSweeps, setSavedSweeps] = useState<
    { id: string; captured_at: string; country: string }[]
  >([]);
  const [selected, setSelected] = useState("");
  const [turnText, setTurnText] = useState("");
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
  const [files, setFiles] = useState(false);
  contextRef.current = { shelf, selected };
  captureHandlerRef.current = captureFrame;

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
    return () => {
      window.clearInterval(timer);
      logStep("capture.timer.stopped", { sweepId });
    };
  }, [running, shelf, sweepId]);

  useEffect(() => {
    if (!sweepId || typeof EventSource === "undefined") return;
    const events = new EventSource(`${API}/api/sweeps/${sweepId}/events`);
    events.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.packet) setPacket(data.packet);
      if (
        !dialogueBusyRef.current &&
        !speakingRef.current &&
        data.progress?.stage === "perception" &&
        data.progress.guidance
      ) {
        setMessage(data.progress.guidance);
      }
    };
    events.onerror = () =>
      logStep("workflow.events.reconnecting", { sweepId }, "warn");
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
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text,
          shelf: contextRef.current.shelf,
          ref_id: contextRef.current.selected,
        }),
      });
      if (!response.ok)
        throw new Error(
          await responseError(response, "Could not process your correction"),
        );
      const data = await response.json();
      if (version !== dialogueVersionRef.current) return;
      setPacket(data.packet);
      setMessage(data.reply);
      if (data.action === "next_shelf")
        setShelf((current) => {
          const match = current.match(/(\d+)$/);
          return match
            ? current.replace(/\d+$/, String(Number(match[1]) + 1))
            : `${current} next`;
        });
      if (data.action === "capture") void captureHandlerRef.current();
      if (data.action === "show_report")
        window.setTimeout(
          () =>
            document
              .getElementById("readable-report")
              ?.scrollIntoView({ behavior: "smooth", block: "center" }),
          0,
        );
      if (data.action === "mute") mutedRef.current = true;
      else void speak(data.reply);
      setTurnText("");
    } finally {
      if (version === dialogueVersionRef.current)
        dialogueBusyRef.current = false;
    }
  }

  function guide(packet: Packet) {
    if (
      dialogueBusyRef.current ||
      speakingRef.current ||
      userSpeakingRef.current
    )
      return;
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
      utterance.onstart = () => {
        speakingRef.current = true;
      };
      utterance.onend = utterance.onerror = () => {
        speakingRef.current = false;
      };
      window.speechSynthesis.speak(utterance);
    }
  }

  async function start() {
    if (startingRef.current || streamRef.current) {
      logStep("sweep.start.skipped", {
        reason: "Camera already opening or active",
      });
      return;
    }
    logStep("sweep.start.clicked");
    startingRef.current = true;
    setStarting(true);
    setMessage("Opening camera. Allow camera access when your browser asks.");
    try {
      if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
        throw new Error(
          "Camera access requires HTTPS or localhost in a browser that supports cameras.",
        );
      }
      logStep("camera.permission.requested", {
        idealWidth: 1920,
        idealHeight: 1080,
      });
      const stream = await navigator.mediaDevices.getUserMedia({
        video: {
          facingMode: "environment",
          width: { ideal: 1920 },
          height: { ideal: 1080 },
        },
        audio: false,
      });
      streamRef.current = stream;
      logStep("camera.stream.opened", { 
        tracks: stream.getTracks().length 
      });

      const video = videoRef.current;
      if (!video)
        throw new Error("Camera preview is unavailable on this page.");
      // 
      video.srcObject = stream;
      await video.play();
      logStep("camera.preview.playing", {
        width: video.videoWidth,
        height: video.videoHeight,
      });

      const response = await loggedFetch(`${API}/api/sweeps`, {
        method: "POST",
        headers: { 
          "Content-Type": "application/json" 
        },
        body: JSON.stringify({
          country,
          country_code: countryCode,
          currency,
          device: "browser camera",
        }),
      });
      if (!response.ok)
        throw new Error(await responseError(response, "Could not start sweep"));

      const data = await response.json();
      logStep("sweep.created", { 
        sweepId: data.sweep_id 
      });
      
      setSweepId(data.sweep_id);
      setPacket(data.packet || null);
      mutedRef.current = false;
      lastGuidanceRef.current = "";
      queuedFrameRef.current.clear();
      setQueuedCount(0);
      setMediaWarning("");
      if (recordVideo) {
        try {
          recordingRef.current = recordCamera(stream, API, data.sweep_id);
          setRecording(!!recordingRef.current);
          if (!recordingRef.current)
            setMediaWarning(
              "This browser cannot save camera video; sampled images will still be saved.",
            );
        } catch (error) {
          logStep("video.unavailable", errorDetails(error), "warn");
          setMediaWarning(
            "Video recording could not start; sampled images will still be saved.",
          );
        }
      }
      setFiles(false);
      setRunning(true);
      setMessage(
        "Slowly pan across every shelf, wall and visible floor area. Keep text sharp and avoid glare.",
      );
      startVoiceInput(data.sweep_id);
      void speak(
        data.packet?.guidance?.text ||
          "Let’s start. Slowly pan across every shelf and wall. I will ask you to pause if a frame is unclear.",
      );
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
      logStep(
        "voice.input.unavailable",
        { reason: "Browser has no speech recognition API" },
        "warn",
      );
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
        for (
          let index = event.resultIndex;
          index < event.results.length;
          index++
        ) {
          const result = event.results[index];
          const text = result?.[0]?.transcript?.trim();
          if (!text) continue;
          if (
            speakingRef.current &&
            spokenTextRef.current.includes(
              text.toLowerCase().replace(/[.!?]$/, ""),
            )
          )
            continue;
          if (result.isFinal === false) {
            userSpeakingRef.current = true;
            if (dialogueBusyRef.current) {
              ++dialogueVersionRef.current;
              dialogueBusyRef.current = false;
            }
            if (speakingRef.current) {
              window.speechSynthesis?.cancel();
              speakingRef.current = false;
            }
            continue;
          }
          userSpeakingRef.current = false;
          logStep("voice.correction.received", {
            sweepId: id,
            characters: text.length,
          });
          void sendTurn(text, id).catch((error) => {
            logStep("voice.turn.failed", errorDetails(error), "error");
            setMessage(
              error instanceof Error ? error.message : "Voice turn failed.",
            );
          });
        }
      };
      recognition.onerror = (event) => {
        if (
          event?.error === "not-allowed" ||
          event?.error === "service-not-allowed"
        )
          voiceWantedRef.current = false;
        setVoiceEnabled(false);
        logStep(
          "voice.input.error",
          { sweepId: id, reason: event?.error },
          "warn",
        );
      };
      recognition.onend = () => {
        userSpeakingRef.current = false;
        setVoiceEnabled(false);
        if (voiceWantedRef.current && streamRef.current) {
          window.setTimeout(() => {
            if (!voiceWantedRef.current) return;
            try {
              recognition.start();
              setVoiceEnabled(true);
            } catch {
              /* Already restarting. */
            }
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
      setMessage(
        "Camera is running. Voice input could not start; use the camera controls to continue.",
      );
    }
  }
  function captureFrame(): Promise<void> {
    if (busyRef.current) {
      logStep("capture.skipped", {
        sweepId,
        reason: "Snapshot already encoding",
      });
      return snapshotPromiseRef.current || Promise.resolve();
    }
    const pending = captureAndQueue();
    snapshotPromiseRef.current = pending;
    return pending;
  }
  async function captureAndQueue() {
    if (
      busyRef.current ||
      !videoRef.current ||
      !canvasRef.current ||
      !sweepId
    ) {
      logStep("capture.skipped", { sweepId, reason: "Capture is not ready" });
      return;
    }
    if (queuedFrameRef.current.size >= 120) {
      logStep(
        "capture.backlog.full",
        { sweepId, queued: queuedFrameRef.current.size },
        "warn",
      );
      setMediaWarning(
        "Image analysis is behind: pause your pan while queued views finish. Automatic snapshots are paused at 120 pending images; continuous video is still recording if enabled.",
      );
      return;
    }
    const video = videoRef.current,
      canvas = canvasRef.current;
    if (video.readyState < 2 || !video.videoWidth || !video.videoHeight) {
      logStep("capture.skipped", {
        sweepId,
        reason: "Video has no decoded frame",
        readyState: video.readyState,
      });
      return;
    }
    logStep("capture.started", {
      sweepId,
      shelf,
      width: video.videoWidth,
      height: video.videoHeight,
    });
    busyRef.current = true;
    try {
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      canvas.getContext("2d")?.drawImage(video, 0, 0);
      setPreviewFrame(canvas.toDataURL("image/jpeg", 0.94));
      const blob = await new Promise<Blob | null>((resolve) =>
        canvas.toBlob(resolve, "image/jpeg", 0.94),
      );
      if (!blob) {
        logStep(
          "capture.failed",
          { sweepId, reason: "JPEG encoding returned no image" },
          "error",
        );
        return;
      }
      logStep("capture.encoded", { sweepId, bytes: blob.size });
      const sequence = ++snapshotSequenceRef.current;
      // Keep every sampled view. Different books can appear under the same shelf label.
      queuedFrameRef.current.set(sequence, { blob, shelf, sweepId });
      setQueuedCount(queuedFrameRef.current.size);
      logStep("capture.queued", {
        sweepId,
        shelf,
        sequence,
        pending: queuedFrameRef.current.size,
      });
    } catch (error) {
      logStep("capture.failed", { sweepId, ...errorDetails(error) }, "error");
      setMessage(
        error instanceof Error ? error.message : "Could not capture frame.",
      );
      return;
    } finally {
      busyRef.current = false;
    }
    if (!capturePromiseRef.current) {
      const pending = uploadQueuedFrames();
      capturePromiseRef.current = pending;
      void pending.finally(() => {
        if (capturePromiseRef.current === pending)
          capturePromiseRef.current = null;
      });
    }
    await capturePromiseRef.current;
  }
  async function uploadQueuedFrames() {
    setProcessing(true);
    try {
      while (queuedFrameRef.current.size) {
        const [sequence, frame] = queuedFrameRef.current
          .entries()
          .next().value!;
        queuedFrameRef.current.delete(sequence);
        setQueuedCount(queuedFrameRef.current.size);
        logStep("frame.upload.started", {
          sweepId: frame.sweepId,
          shelf: frame.shelf,
          bytes: frame.blob.size,
        });
        setMessage(
          `Analyzing saved view ${sequence}. ${queuedFrameRef.current.size} views waiting. Pan slowly and pause for readable spines.`,
        );
        try {
          const form = new FormData();
          form.append("image", frame.blob, "frame.jpg");
          form.append("shelf", frame.shelf);
          const response = await loggedFetch(
            `${API}/api/sweeps/${frame.sweepId}/frames`,
            { method: "POST", body: form },
          );
          if (!response.ok)
            throw new Error(
              await responseError(response, "Frame upload failed"),
            );
          const data = await response.json();
          logStep(
            "frame.processed",
            {
              sweepId: frame.sweepId,
              frameRef: data.frame_ref,
              status: data.candidate?.vision_status,
              books: data.packet.books?.length,
              primaryCount: data.candidate?.primary_count,
              validatorCount: data.candidate?.validation?.count,
              notes: data.candidate?.notes,
            },
            data.candidate?.vision_status === "ok" ? "info" : "warn",
          );
          setPacket(data.packet);
          setMessage(
            data.candidate?.notes?.[0] ||
              "Frame analyzed. Inventory keeps books and room items seen throughout the sweep.",
          );
          guide(data.packet);
        } catch (error) {
          logStep(
            "frame.upload.failed",
            { sweepId: frame.sweepId, ...errorDetails(error) },
            "error",
          );
          setMessage(
            error instanceof Error
              ? error.message
              : "Could not process this frame.",
          );
        }
      }
    } finally {
      setProcessing(false);
      logStep("capture.queue.drained");
    }
  }
  async function finish() {
    logStep("sweep.finish.clicked", { sweepId });
    const lastSnapshot = busyRef.current
      ? snapshotPromiseRef.current
      : captureFrame();
    setFinishing(true);
    setRunning(false);
    voiceWantedRef.current = false;
    speechRef.current?.stop();
    speechRef.current = null;
    setVoiceEnabled(false);
    const videoSave = recordingRef.current?.save().catch((error) => {
      logStep("video.save.failed", errorDetails(error), "error");
      return {
        error: error instanceof Error ? error.message : "Video save failed",
      };
    });
    recordingRef.current = null;
    setRecording(false);
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
    logStep("camera.stopped", { sweepId });
    logStep("voice.input.stopped", { sweepId });
    if (!sweepId) {
      logStep("sweep.finish.skipped", { reason: "No active sweep" }, "warn");
      setFinishing(false);
      return;
    }
    try {
      const stopped = await loggedFetch(
        `${API}/api/sweeps/${sweepId}/stop-capture`,
        { method: "POST" },
      );
      if (!stopped.ok)
        throw new Error(
          await responseError(stopped, "Could not mark capture as stopped"),
        );
      setMessage(
        "Finishing sweep. Waiting for the current frame to finish processing…",
      );
      logStep("sweep.finish.waiting_for_frames", { sweepId });
      await lastSnapshot;
      await capturePromiseRef.current;
      const videoResult = await videoSave;
      if (videoResult?.error) setMediaWarning(videoResult.error);
      const response = await loggedFetch(
        `${API}/api/sweeps/${sweepId}/finish`,
        {
          method: "POST",
        },
      );
      if (!response.ok)
        throw new Error(
          await responseError(response, "Could not save the claim packet"),
        );
      const data = await response.json();
      setPacket(data.packet);
      setFiles(true);
      logStep("sweep.saved", {
        sweepId,
        jsonFile: data.json_file,
        reportFile: data.report_file,
        books: data.packet.totals.book_count,
        reviewFlags: data.packet.review_queue.length,
      });
      const failed =
        data.packet.frames?.filter(
          (frame: { vision_status: string }) => frame.vision_status !== "ok",
        ).length || 0;
      const verifying = data.packet.verification_progress?.status === "running";
      setMessage(
        verifying
          ? "Sweep saved. Remaining crop checks are running; the report shows uncertain candidates separately and will refresh when checks finish."
          : failed
            ? `Sweep saved; ${failed} frame(s) need review or another capture. Check the count comparison and review reasons below.`
            : "Sweep saved. Review every flagged line and complete room measurements and sourced prices before submitting a claim.",
      );
      void speak(
        verifying
          ? "The sweep is saved. I’m checking the remaining crops, and uncertain names will stay out of the report."
          : failed
            ? "The sweep is saved, but the book count needs review. Please check the reasons on screen."
            : `The sweep is complete. I logged ${data.packet.totals.book_count} candidate books and ${data.packet.review_queue.length} lines for review.`,
      );
    } catch (error) {
      logStep(
        "sweep.finish.failed",
        { sweepId, ...errorDetails(error) },
        "error",
      );
      setMessage(
        error instanceof Error ? error.message : "Could not finish sweep.",
      );
    } finally {
      setFinishing(false);
    }
  }

  async function refreshExports() {
    try {
      const response = await loggedFetch(
        `${API}/api/sweeps/${sweepId}/finish`,
        { method: "POST" },
      );
      if (!response.ok)
        throw new Error(await responseError(response, "Export failed"));
      const data = await response.json();
      setPacket(data.packet);
      setFiles(true);
      setMessage("Updated packet and report saved.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Export failed");
    }
  }

  async function loadSavedSweeps() {
    try {
      const response = await loggedFetch(`${API}/api/sweeps`);
      if (!response.ok) throw new Error("Could not load saved sweeps");
      setSavedSweeps(await response.json());
    } catch (error) {
      setMessage(
        error instanceof Error ? error.message : "Could not load saved sweeps",
      );
    }
  }

  async function openSavedSweep(id: string) {
    try {
      const response = await loggedFetch(`${API}/api/sweeps/${id}`);
      if (!response.ok) throw new Error("Could not open sweep");
      const saved: Packet = await response.json();
      setSweepId(id);
      setPacket(saved);
      setFiles(true);
      setCountry(saved.sweep.country);
      setCurrency(saved.sweep.currency);
      setPreviewFrame(
        saved.frames?.at(-1) ? `${API}/${saved.frames.at(-1)!.frame_ref}` : "",
      );
      setMessage(
        "Saved sweep opened for review. Update exports after making corrections.",
      );
    } catch (error) {
      setMessage(
        error instanceof Error ? error.message : "Could not open sweep",
      );
    }
  }

  return (
    <main>
      <header>
        <div className="brand">LC</div>
        <div>
          <h1>Library sweep</h1>
        </div>
        <span className={`status ${running ? "live" : ""}`}>
          {running ? "● LIVE SWEEP" : "READY"}
        </span>
      </header>
      <section className="intro">
        <h2>Document the room in one continuous pass.</h2>
      </section>
      <SweepControls
        country={country}
        countryCode={countryCode}
        currency={currency}
        shelf={shelf}
        running={running}
        starting={starting}
        finishing={finishing}
        queuedCount={queuedCount}
        recordVideo={recordVideo}
        savedSweeps={savedSweeps}
        onCountryChange={setCountry}
        onCountryCodeChange={setCountryCode}
        onCurrencyChange={setCurrency}
        onShelfChange={setShelf}
        onStart={() => void start()}
        onFinish={() => void finish()}
        onReviewSaved={() => void loadSavedSweeps()}
        onOpenSweep={openSavedSweep}
        onRecordVideoChange={setRecordVideo}
      />
      {mediaWarning && (
        <p role="alert" className="message">
          {mediaWarning}
        </p>
      )}
      <section className="workspace">
        <CameraPanel
          videoRef={videoRef}
          canvasRef={canvasRef}
          recording={recording}
          running={running}
          starting={starting}
          previewFrame={previewFrame}
          message={message}
          processing={processing}
          queuedCount={queuedCount}
          voiceEnabled={voiceEnabled}
          onCapture={() => void captureFrame()}
        />
        <InventoryPanel
          packet={packet}
          currency={currency}
          selected={selected}
          onSelect={setSelected}
          processing={processing}
        />
      </section>
      {sweepId && (
        <ConversationPanel
          packet={packet}
          turnText={turnText}
          onTurnTextChange={setTurnText}
          onSend={() =>
            void sendTurn(turnText).catch((error) => setMessage(error.message))
          }
          onStopTalking={() => {
            window.speechSynthesis?.cancel();
            speakingRef.current = false;
            mutedRef.current = true;
          }}
        />
      )}
      {packet && (
        <ReviewPanel
          packet={packet}
          api={API}
          sweepId={sweepId}
          selected={selected}
          hasExports={files}
          onSelect={setSelected}
          onUpdate={setPacket}
          onRefreshExports={() => void refreshExports()}
        />
      )}
      <DemoRecorder />
      {sweepId && !running && !finishing && !files && (
        <button onClick={() => void finish()}>Retry saving this sweep</button>
      )}
      <footer>
        Capture only a space you have permission to document. Avoid people and
        personal papers.
      </footer>
    </main>
  );
}
