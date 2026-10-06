import { useEffect, useRef, useState } from 'react';

/** A single uninterrupted screen+microphone take; no editing or stitching. */
export function DemoRecorder() {
  const [active, setActive] = useState(false);
  const [starting, setStarting] = useState(false);
  const startGuard = useRef(false);
  const [message, setMessage] = useState('Share this browser tab with tab audio to record the agent and camera.');
  const [download, setDownload] = useState('');
  const recorder = useRef<MediaRecorder | null>(null);
  const cleanup = useRef<() => void>(() => {});
  const url = useRef('');
  useEffect(() => () => { if (recorder.current?.state === "recording") recorder.current.stop(); cleanup.current(); if (url.current) URL.revokeObjectURL(url.current); }, []);

  async function start() {
    if (startGuard.current) return;
    startGuard.current = true; setStarting(true);
    let screen: MediaStream | undefined;
    let microphone: MediaStream | undefined;
    let audio: AudioContext | undefined;
    try {
      if (!navigator.mediaDevices?.getDisplayMedia || typeof MediaRecorder === 'undefined') throw new Error('Screen recording is unavailable in this browser. Use a screen recorder with microphone and system audio.');
      screen = await navigator.mediaDevices.getDisplayMedia({video: true, audio: true});
      if (!screen.getAudioTracks().length) throw new Error('No tab audio was shared. Select the application tab and enable Share tab audio to include the agent voice.');
      microphone = await navigator.mediaDevices.getUserMedia({audio: true});
      audio = new AudioContext();
      await audio.resume();
      const destination = audio.createMediaStreamDestination();
      audio.createMediaStreamSource(screen).connect(destination);
      audio.createMediaStreamSource(microphone).connect(destination);
      const combined = new MediaStream([...screen.getVideoTracks(), ...destination.stream.getAudioTracks()]);
      const mimeType = ['video/webm;codecs=vp9,opus', 'video/webm;codecs=vp8,opus', 'video/webm'].find(type => MediaRecorder.isTypeSupported(type));
      const capture = new MediaRecorder(combined, mimeType ? {mimeType} : undefined);
      recorder.current = capture;
      const chunks: BlobPart[] = [];
      capture.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
      const release = () => { screen?.getTracks().forEach(track => track.stop()); microphone?.getTracks().forEach(track => track.stop()); if (audio && audio.state !== "closed") void audio.close(); };
      const timer = window.setTimeout(() => { if (capture.state !== 'inactive') capture.stop(); }, 355_000);
      cleanup.current = () => { window.clearTimeout(timer); release(); };
      capture.onstop = () => {
        cleanup.current();
        if (url.current) URL.revokeObjectURL(url.current);
        url.current = URL.createObjectURL(new Blob(chunks, {type: capture.mimeType}));
        setDownload(url.current); setActive(false);
        setMessage('One continuous take saved locally. Verify agent audio, microphone audio and the full workflow before submitting.');
      };
      capture.onerror = () => setMessage('Recording failed. Record a new continuous take.');
      screen.getVideoTracks()[0].onended = () => { if (capture.state !== 'inactive') capture.stop(); };
      capture.start(1000); setActive(true); setDownload('');
      setMessage('Recording one continuous take. Stop after showing exports and review. Automatic stop at 5 minutes 55 seconds.');
    } catch (error) {
      screen?.getTracks().forEach(track => track.stop()); microphone?.getTracks().forEach(track => track.stop()); if (audio && audio.state !== "closed") void audio.close();
      setMessage(error instanceof Error ? error.message : 'Could not start recording.');
    } finally {startGuard.current = false; setStarting(false);}
  }
  return <details><summary>Record the unedited assignment demo</summary><p>{message}</p>
    <button disabled={starting} onClick={() => active ? recorder.current?.stop() : void start()}>{active ? 'Stop demo recording' : 'Record screen + agent audio + microphone'}</button>
    {download && <p><a href={download} download="library-claim-demo.webm">Download continuous demo video</a></p>}
  </details>;
}
