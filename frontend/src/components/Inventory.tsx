import type { Book, Item, Packet } from "../types";

type Props = { packet: Packet; selected: string; onSelect: (id: string) => void };

const money = (p?: { amount?: number | null; low?: number | null; high?: number | null; currency?: string }) => {
  if (!p) return "";
  if (p.amount != null) return `${p.amount} ${p.currency ?? ""}`;
  if (p.low != null) return `${p.low}–${p.high} ${p.currency ?? ""}`;
  return "";
};

const pct = (value?: number) => (value == null ? "" : `${Math.round(value * 100)}%`);

// Live inventory: counts, every detected line with status/confidence/price, review count.
export function Inventory({ packet, selected, onSelect }: Props) {
  const t = packet.totals;
  const row = (id: string, name: string, meta: string, status: string, price: string) => (
    <li key={id} className={id === selected ? "selected" : ""} onClick={() => onSelect(id)}>
      <span className="name">{name}</span>
      <span className="meta">{meta}</span>
      <span className={`status ${status}`}>{status.replace("_", " ")}</span>
      <span className="price">{price}</span>
    </li>
  );
  return (
    <section className="card">
      <h2>Inventory</h2>
      <div className="counters">
        <div><b>{t.book_count ?? 0}</b> books detected</div>
        <div><b>{t.books_identified ?? 0}</b> identified</div>
        <div><b>{t.books_unidentified ?? 0}</b> unreadable</div>
        <div><b>{packet.items.length}</b> items</div>
        <div><b>{packet.review_queue.length}</b> need review</div>
      </div>
      <h3>Books</h3>
      <ul className="lines">
        {packet.books.map((b: Book) =>
          row(
            b.id,
            b.title || (b.proposed_title ? `(unverified) ${b.proposed_title}` : "Unidentified book"),
            [b.shelf, b.author, pct(b.id_confidence)].filter(Boolean).join(" · "),
            b.status,
            [money(b.replacement_cost), money(b.used_value) && `used ${money(b.used_value)}`].filter(Boolean).join(" / "),
          ),
        )}
      </ul>
      <h3>Other items</h3>
      <ul className="lines">
        {packet.items.map((i: Item) =>
          row(i.id, i.category, [i.description, pct(i.confidence)].filter(Boolean).join(" · "), i.status, money(i.replacement_cost)),
        )}
      </ul>
    </section>
  );
}
