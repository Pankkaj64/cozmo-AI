import { useState } from "react";
import type { Packet } from "../types";

type Props = { 
  packet: Packet | null; 
  listening: boolean; 
  onSend: (text: string) => Promise<void> 
};

// Transcript of the spoken conversation plus a text box for browsers without speech input.
export function Conversation({ packet, listening, onSend }: Props) {
  const [text, setText] = useState("");
  const turns = packet?.transcript?.slice(-12) ?? [];
  return (
    <section className="card">
      <h2>Agent {listening ? "· listening" : ""}</h2>
      <ul className="transcript">
        {turns.map((t) => (
          <li key={t.id} className={t.role}>
            <b>{t.role === "agent" ? "Agent" : "You"}:</b> {t.text}
          </li>
        ))}
      </ul>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (text.trim()) void onSend(text).then(() => setText(""));
        }}
      >
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Say or type: next shelf · skip this shelf · that is a first edition · how many books?" />
        <button type="submit">Send</button>
      </form>
    </section>
  );
}
