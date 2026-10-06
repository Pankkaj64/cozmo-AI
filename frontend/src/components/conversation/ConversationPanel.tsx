import type { FormEvent } from "react";
import type { Packet } from "../../types/packet";

type Props = {
  packet: Packet | null;
  turnText: string;
  onTurnTextChange: (value: string) => void;
  onSend: () => void;
  onStopTalking: () => void;
};

export function ConversationPanel({
  packet,
  turnText,
  onTurnTextChange,
  onSend,
  onStopTalking,
}: Props) {
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onSend();
  }

  return (
    <section className="card lower">
      <div className="section-title">
        <h3>Conversation</h3>
        <button className="quiet" onClick={onStopTalking}>
          Stop talking
        </button>
      </div>
      <div className="transcript" aria-live="polite">
        {packet?.transcript?.slice(-12).map((turn) => (
          <p key={turn.id}>
            <b>{turn.role === "agent" ? "Agent" : "You"}:</b> {turn.text}
          </p>
        ))}
      </div>
      <form className="turn-form" onSubmit={submit}>
        <input
          aria-label="Talk to the agent"
          placeholder="Ask a question, discuss a result, or tell me what to do next…"
          value={turnText}
          onChange={(event) => onTurnTextChange(event.target.value)}
        />
        <button type="submit" className="primary" disabled={!turnText.trim()}>
          Send
        </button>
      </form>
      <p className="message">
        Talk naturally and interrupt me whenever you need. Select an item when
        giving a specific correction. Browser voice recognition may use your
        browser&apos;s speech service. Typed corrections work in every browser.
      </p>
    </section>
  );
}
