// Browser speech: the agent talks with speechSynthesis and listens with SpeechRecognition.
// Interim speech from the claimant interrupts the agent (barge-in), so the conversation
// feels two-way: listen -> send the sentence to the backend -> speak its reply.

type Recognition = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((event: { resultIndex: number; results: ArrayLike<{ isFinal: boolean; 0: { transcript: string } }> }) => void) | null;
  onend: (() => void) | null;
  onerror: ((event: { error?: string }) => void) | null;
  start: () => void;
  stop: () => void;
};

declare global {
  interface Window {
    SpeechRecognition?: new () => Recognition;
    webkitSpeechRecognition?: new () => Recognition;
  }
}

let speaking = false;

export function speak(text: string): void {
  if (!("speechSynthesis" in window) || !text) return;
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.onstart = () => (speaking = true);
  utterance.onend = utterance.onerror = () => (speaking = false);
  window.speechSynthesis.speak(utterance);
}

export const stopSpeaking = () => window.speechSynthesis?.cancel();
export const isSpeaking = () => speaking;

/** Start continuous listening. Returns a stop function, or null when unsupported. */
export function listen(onFinal: (text: string) => void, onInterim: () => void): (() => void) | null {
  const Speech = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Speech) return null;
  const recognition = new Speech();
  recognition.lang = "en-US";
  recognition.continuous = true;
  recognition.interimResults = true;
  let active = true;
  recognition.onresult = (event) => {
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const result = event.results[i];
      const text = result[0].transcript.trim();
      if (!text) continue;
      if (result.isFinal) onFinal(text);
      else onInterim(); // the claimant started talking: stop the agent's voice
    }
  };
  // Chrome ends recognition after silence; restart while the sweep is running.
  recognition.onend = () => active && recognition.start();
  recognition.onerror = (event) => {
    if (event.error === "not-allowed") active = false;
  };
  recognition.start();
  return () => {
    active = false;
    recognition.stop();
  };
}
