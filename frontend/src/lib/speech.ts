export type SpeechResult = {
  isFinal?: boolean;
  [index: number]: { transcript: string };
  length: number;
};
export type SpeechEvent = { resultIndex: number; results: SpeechResult[] };
export type SpeechRecognizer = {
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
