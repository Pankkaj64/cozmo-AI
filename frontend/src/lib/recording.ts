import { loggedFetch, logStep } from "./logger";

// Preserve the continuous camera evidence while inference samples JPEG frames.
// Microphone/audio is intentionally separate from the camera track.
export function recordCamera(
  stream: MediaStream,
  api: string,
  sweepId: string,
) {
  if (typeof MediaRecorder === "undefined") return null;
  const mimeType = ["video/webm;codecs=vp8", "video/webm", "video/mp4"].find(
    (type) => MediaRecorder.isTypeSupported(type),
  );
  if (!mimeType) return null;
  const recorder = new MediaRecorder(stream, {
    mimeType,
    videoBitsPerSecond: 1_500_000,
  });
  const chunks: Blob[] = [];
  let size = 0;
  let recordingError: Error | null = null;
  let ended: () => void;
  const stopped = new Promise<void>((resolve) => {
    ended = resolve;
  });
  recorder.ondataavailable = (event) => {
    if (event.data.size) {
      chunks.push(event.data);
      size += event.data.size;
    }
    if (size > 200_000_000 && recorder.state !== "inactive") recorder.stop();
  };
  recorder.onerror = () => {
    recordingError = new Error(
      "Camera recording failed. Still frames remain saved.",
    );
    ended();
  };
  recorder.onstop = () => ended();
  recorder.start(1000);
  logStep("video.recording.started", { sweepId, mimeType, audio: false });
  return {
    discard() {
      if (recorder.state !== "inactive") recorder.stop();
      chunks.length = 0;
    },
    async save() {
      if (recorder.state !== "inactive") recorder.stop();
      let timer: ReturnType<typeof setTimeout> | undefined;
      try {
        await Promise.race([
          stopped,
          new Promise<never>((_, reject) => {
            timer = setTimeout(
              () =>
                reject(
                  new Error(
                    "Camera recorder did not finish. Still frames remain saved.",
                  ),
                ),
              8000,
            );
          }),
        ]);
      } finally {
        if (timer !== undefined) clearTimeout(timer);
      }
      if (recordingError) throw recordingError;
      const body = new FormData();
      body.append(
        "video",
        new Blob(chunks, { type: mimeType }),
        mimeType.includes("mp4") ? "sweep.mp4" : "sweep.webm",
      );
      const response = await loggedFetch(`${api}/api/sweeps/${sweepId}/video`, {
        method: "POST",
        body,
      });
      if (!response.ok)
        throw new Error(
          "Video evidence could not be saved. Still frames remain available.",
        );
      chunks.length = 0;
      logStep("video.recording.saved", { sweepId, bytes: size });
      return response.json();
    },
  };
}
