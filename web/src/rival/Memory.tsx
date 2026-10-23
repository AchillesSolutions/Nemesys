/**
 * What the rival remembers about you, for the demo view: last time's result and what you said then, bets made
 * along the way, your own words worth throwing back. The Ammo agent writes to it (a dot flies in) and recalls
 * from it (the memory lights up and a dot flies out, through the Ammo agent, to the rival). Saved for next time.
 */

export interface MemoryItem { id: number; kind: "last_time" | "bet" | "prediction" | "you_said"; text: string }

const KIND: Record<MemoryItem["kind"], string> = { last_time: "Last time", bet: "Bet", prediction: "Prediction", you_said: "You said" };
const SHOWN = 4;

const stroke = { fill: "none", stroke: "currentColor", strokeWidth: 1.7, strokeLinecap: "round", strokeLinejoin: "round" } as const;

/** A brain, in two halves. */
export function BrainIcon() {
  return (
    <svg viewBox="0 0 24 24" {...stroke} aria-hidden>
      <path d="M11 4.5a3 3 0 0 0-5.2 1.4A3 3 0 0 0 4 10.6a3 3 0 0 0 .9 4.9A3.2 3.2 0 0 0 8 19.5a2.8 2.8 0 0 0 3-1.3z" />
      <path d="M13 4.5a3 3 0 0 1 5.2 1.4 3 3 0 0 1 1.8 4.7 3 3 0 0 1-.9 4.9 3.2 3.2 0 0 1-3.1 4 2.8 2.8 0 0 1-3-1.3z" />
      <path d="M11 4.5v13.7M13 4.5v13.7M7.5 9.5c1 .2 1.8.8 2.2 1.7M16.5 9.5c-1 .2-1.8.8-2.2 1.7M8 14.5c.9-.4 1.9-.3 2.6.3M16 14.5c-.9-.4-1.9-.3-2.6.3" />
    </svg>
  );
}

export function MemoryBox({ items, recalling }: { items: MemoryItem[]; recalling: number | null }) {
  // The newest first; a memory being recalled stays in view even if it's older.
  const newest = [...items].reverse();
  const shown = newest.slice(0, SHOWN);
  const recalled = recalling != null ? items.find((m) => m.id === recalling) : undefined;
  if (recalled && !shown.includes(recalled)) shown[shown.length - 1] = recalled;
  return (
    <section className={`memory-box ${recalling != null ? "is-recalling" : ""}`} data-node="memory" aria-label="The rival's memory">
      <p className="memory-title"><span className="memory-icon"><BrainIcon /></span>Memory</p>
      {shown.length ? (
        <ul className="memory-list">
          {shown.map((m) => (
            <li key={m.id} className={`memory-item memory-${m.kind} ${m.id === recalling ? "is-recalled" : ""}`}>
              <span className="memory-kind">{KIND[m.kind]}</span>
              <span className="memory-text">{m.kind === "bet" || m.kind === "prediction" ? m.text : `“${m.text.replace(/^["“]|["”]$/g, "")}”`}</span>
            </li>
          ))}
        </ul>
      ) : <p className="empty">Bets, and anything you say worth remembering, are kept here for next time.</p>}
    </section>
  );
}
