import { useEffect, useRef, useState } from "react";
import type { Packet } from "../types";

type Props = {
  packet: Packet | null;
  listening: boolean;
  onSend: (text: string) => Promise<void>;
  selected?: string;
  onClearSelection?: () => void;
};

// Live chat with the agent: the spoken transcript plus a text box for browsers without speech input.
export function Conversation({ packet, listening, onSend, selected, onClearSelection }: Props) {
  const [text, setText] = useState("");
  const turns = packet?.transcript?.slice(-40) ?? [];
  const endRef = useRef<HTMLLIElement>(null);
  useEffect(() => { endRef.current?.scrollIntoView({ block: "end" }); }, [turns.length]);
  const selectedLine = selected
    ? packet?.books.find((b) => b.id === selected)?.title || packet?.books.find((b) => b.id === selected)?.proposed_title || packet?.items.find((i) => i.id === selected)?.category
    : "";
  return (
    <section className="card chat">
      <h2>Agent {listening && <span className="mic">listening</span>}</h2>
      {!packet && <p className="muted">Start the sweep and the agent talks you through it here: what it logs, when to slow down, when to move closer.</p>}
      <ul className="transcript">
        {packet && turns.length === 0 && <li className="agent"><b>Agent</b>Say “next shelf” when you move on, or tell me about a selected line.</li>}
        {turns.map((t) => (
          <li key={t.id} className={t.role}>
            <b>{t.role === "agent" ? "Agent" : "You"}</b>
            {t.text}
          </li>
        ))}
        <li ref={endRef} className="end" aria-hidden="true" />
      </ul>
      {selectedLine && (
        <p className="selection">Talking about <b>{selectedLine}</b> <button type="button" className="link" onClick={onClearSelection}>clear</button></p>
      )}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (text.trim()) void onSend(text).then(() => setText(""));
        }}
      >
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Type or say something…" disabled={!packet} />
        <button type="submit" disabled={!packet || !text.trim()}>Send</button>
      </form>
      <p className="hint">Try: next shelf · skip this shelf · the shelf is 80 centimetres wide · that is a first edition · how many books?</p>
    </section>
  );
}
