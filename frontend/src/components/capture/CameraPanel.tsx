import type { RefObject } from "react";

type Props = {
  videoRef: RefObject<HTMLVideoElement | null>;
  canvasRef: RefObject<HTMLCanvasElement | null>;
  recording: boolean;
  running: boolean;
  starting: boolean;
  previewFrame: string;
  message: string;
  processing: boolean;
  queuedCount: number;
  voiceEnabled: boolean;
  onCapture: () => void;
};

export function CameraPanel({
  videoRef,
  canvasRef,
  recording,
  running,
  starting,
  previewFrame,
  message,
  processing,
  queuedCount,
  voiceEnabled,
  onCapture,
}: Props) {
  return (
    <div className="camera card">
      <div className="section-title">
        <h3>Camera</h3>
        <span>
          {recording
            ? "● Recording video + sampled images"
            : "Sampled image analysis"}
        </span>
      </div>
      <div className="video-wrap">
        <video
          ref={videoRef}
          autoPlay
          muted
          playsInline
          className={
            running || starting ? "camera-video" : "camera-video-hidden"
          }
        />
        {!running && !starting && previewFrame ? (
          <img
            className="camera-still"
            src={previewFrame}
            alt="Last captured camera frame"
          />
        ) : !running && !starting ? (
          <div className="camera-empty">Camera preview appears here</div>
        ) : null}
        <canvas ref={canvasRef} hidden />
      </div>
      <p className="message" role="status">
        {message}
      </p>
      {(processing || queuedCount > 0) && (
        <p className="message">
          {processing ? "Analyzing one image" : "Waiting"} · {queuedCount} saved
          views queued. Finish waits for queued images.
        </p>
      )}
      <div className="camera-actions">
        <button className="quiet" disabled={!running} onClick={onCapture}>
          {processing ? "Queue this view next" : "Capture this shelf now"}
        </button>
        <span>
          {voiceEnabled ? "Voice input on" : "Voice input unavailable"}
        </span>
      </div>
    </div>
  );
}
