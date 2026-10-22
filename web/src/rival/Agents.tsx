import { type CSSProperties, type ReactNode, useLayoutEffect, useRef, useState } from "react";

/**
 * The agents behind the rival, full width along the bottom of the screen, in the order work flows through
 * them: before kick-off the lobby host and the News agent; during the match the Match agent reads the game,
 * the Ammo agent picks the next dig, the Referee checks it, and the rival says it. Each card shows what it
 * runs on, the part it plays and, large, a two-word status (the detail is on hover); it lights up while it
 * works. When one agent hands its work to another, a dot travels from card to card. All of this is for the
 * demo: the listener hears only the rival.
 */

export interface AgentReport { text: string; working: boolean; at: number; status?: string } // at: epoch seconds
export interface Handoff { from: string; to: string; key: number }

const stroke = { fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round", strokeLinejoin: "round" } as const;

/** Small line icons, one per agent. */
const ICONS: Record<string, ReactNode> = {
  host: <svg viewBox="0 0 24 24" {...stroke}><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" /></svg>,
  news: <svg viewBox="0 0 24 24" {...stroke}><path d="M4 5h13v14H6a2 2 0 0 1-2-2zM17 9h3v8a2 2 0 0 1-2 2" /><path d="M7 9h7M7 12.5h7M7 16h4" /></svg>,
  match: <svg viewBox="0 0 24 24" {...stroke}><circle cx="12" cy="12" r="8.5" /><path d="m12 7.5 4 2.9-1.5 4.7h-5L8 10.4z" /></svg>,
  ammo: <svg viewBox="0 0 24 24" {...stroke}><circle cx="12" cy="12" r="8.5" /><circle cx="12" cy="12" r="4.5" /><circle cx="12" cy="12" r="1" /></svg>,
  referee: <svg viewBox="0 0 24 24" {...stroke}><rect x="6" y="4" width="9" height="13" rx="1.5" transform="rotate(-10 10 10)" /><path d="M14 19.5 20 18" /></svg>,
  rival: <svg viewBox="0 0 24 24" {...stroke}><path d="M4 6h11v7H8l-4 3z" /><path d="M15 9h5v7l-3-2.5h-6v-.5" /></svg>,
};

interface AgentInfo { id: string; name: string; role: string; model: string; idle: string; resting: string }

export const BEFORE: AgentInfo[] = [
  { id: "host", name: "Lobby Host", role: "Asks who you support, finds you a rival", model: "Azure AI Speech", idle: "Waiting in the lobby", resting: "In the lobby" },
  { id: "news", name: "News Agent", role: "Writes the fortnight's club news", model: "gpt-5.4-mini", idle: "Waiting for match day", resting: "Waiting" },
];
export const DURING: AgentInfo[] = [
  { id: "match", name: "Match Agent", role: "Reads the game, spots the storylines", model: "gpt-5.4-mini", idle: "Watching the match", resting: "Watching" },
  { id: "ammo", name: "Ammo Agent", role: "Picks the next dig, remembers bets", model: "gpt-5.4-mini", idle: "Digs in reserve", resting: "Standing by" },
  { id: "referee", name: "Referee", role: "Checks digs, keeps it good-natured", model: "gpt-5.4-mini + Azure AI Content Safety", idle: "All good-natured so far", resting: "All good" },
  { id: "rival", name: "The Rival", role: "Watches with you, talks back", model: "GPT-Live", idle: "Listening", resting: "Listening" },
];

const LIT_S = 2.5; // a report keeps its card lit this long

const HANDOFF_MS = 900; // how long the dot takes from one card to the next

interface Dot { key: number; x0: number; y0: number; x1: number; y1: number }

export function AgentsFlow({ reports, active, now, beforeDone = false, handoffs = [] }: {
  reports: Record<string, AgentReport | undefined>;
  beforeDone?: boolean; // once the match is on, the before-kick-off agents have done their part
  active: Record<string, { working: boolean; text?: string; status?: string } | undefined>; // seen in the browser
  now: number;
  handoffs?: Handoff[];
}) {
  const flow = useRef<HTMLElement>(null);
  const cards = useRef<Record<string, HTMLElement | null>>({});
  const seen = useRef(new Set<number>());
  const [dots, setDots] = useState<Dot[]>([]);
  const [receiving, setReceiving] = useState<Record<string, number>>({});

  // Each new hand-off: measure the two cards and send a dot from one to the other; the target lights on arrival.
  useLayoutEffect(() => {
    const box = flow.current?.getBoundingClientRect();
    if (!box) return;
    for (const h of handoffs) {
      if (seen.current.has(h.key)) continue;
      seen.current.add(h.key);
      const a = cards.current[h.from]?.getBoundingClientRect(), b = cards.current[h.to]?.getBoundingClientRect();
      if (!a || !b) continue;
      setDots((d) => [...d, { key: h.key, x0: a.left + a.width / 2 - box.left, y0: a.top + a.height / 2 - box.top,
                              x1: b.left + b.width / 2 - box.left, y1: b.top + b.height / 2 - box.top }]);
      setTimeout(() => {
        setDots((d) => d.filter((x) => x.key !== h.key));
        setReceiving((r) => ({ ...r, [h.to]: h.key }));
        setTimeout(() => setReceiving((r) => (r[h.to] === h.key ? { ...r, [h.to]: 0 } : r)), 900);
      }, HANDOFF_MS);
    }
  }, [handoffs]);

  const card = (a: AgentInfo) => {
    const report = reports[a.id], direct = active[a.id];
    const working = direct?.working ?? (report ? report.working && now - report.at < LIT_S : false);
    const latest = direct?.text ?? report?.text ?? a.idle;
    const status = direct?.status ?? report?.status ?? a.resting;
    const lit = working || Boolean(receiving[a.id]);
    return (
      <article key={a.id} ref={(el) => { cards.current[a.id] = el; }}
               className={`agent-card ${lit ? "is-working" : "is-idle"}${receiving[a.id] ? " is-receiving" : ""}`}
               aria-label={`${a.name}: ${a.role}. ${status}`}>
        <h3>{a.name}</h3>
        <div className="agent-tech">
          <span className="agent-icon" aria-hidden>{ICONS[a.id]}</span>
          <span className="agent-model">{a.model}</span>
        </div>
        <p className="agent-role">{a.role}</p>
        <p className="agent-status" title={latest}>{status}</p>
      </article>
    );
  };
  // Five equal columns: the two before-kick-off agents share the first, one above the other (their work is done
  // once the match starts); the four match agents follow, with an arrow in each gap. Only agents at work are lit.
  return (
    <section className="agents-flow" aria-label="The agents" ref={flow}>
      <p className="agents-label before">Before kick-off</p>
      <p className="agents-label during">During the match</p>
      <div className={`agents-cell stack ${beforeDone ? "done" : ""}`}>{BEFORE.map(card)}</div>
      {DURING.map((a, i) => (
        <div key={a.id} className={`agents-cell ${i > 0 ? "flows" : ""}`}>{card(a)}</div>
      ))}
      {dots.map((d) => (
        <span key={d.key} className="handoff-dot" aria-hidden style={{
          "--x0": `${d.x0}px`, "--y0": `${d.y0}px`, "--x1": `${d.x1}px`, "--y1": `${d.y1}px`, "--ms": `${HANDOFF_MS}ms`,
        } as CSSProperties} />
      ))}
    </section>
  );
}
