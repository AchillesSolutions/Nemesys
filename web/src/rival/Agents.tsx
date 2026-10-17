import type { ReactNode } from "react";

/**
 * The agents behind the rival, full width along the bottom of the screen, in the order work flows through
 * them: before kick-off the lobby host and the News agent; during the match the Match agent reads the game,
 * the Ammo agent picks the next dig, the Referee checks it, and the rival says it. Each card shows what it
 * runs on, the part it plays and the last thing it did, and lights up while it works.
 */

export interface AgentReport { text: string; working: boolean; at: number } // at: epoch seconds

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

interface AgentInfo { id: string; name: string; role: string; model: string; idle: string }

export const BEFORE: AgentInfo[] = [
  { id: "host", name: "Lobby Host", role: "Asks who you support, finds you a rival", model: "Azure AI Speech", idle: "Waiting in the lobby" },
  { id: "news", name: "News Agent", role: "Writes the fortnight's club news", model: "gpt-5.4-mini", idle: "Waiting for match day" },
];
export const DURING: AgentInfo[] = [
  { id: "match", name: "Match Agent", role: "Reads the game, spots the storylines", model: "gpt-5.4-mini", idle: "Watching the match" },
  { id: "ammo", name: "Ammo Agent", role: "Picks the next dig", model: "gpt-5.4-mini", idle: "Digs in reserve" },
  { id: "referee", name: "Referee", role: "Checks digs, keeps it good-natured", model: "gpt-5.4-mini + Azure AI Content Safety", idle: "All good-natured so far" },
  { id: "rival", name: "The Rival", role: "Watches with you, talks back", model: "GPT-Live", idle: "Listening" },
];

const LIT_S = 2.5; // a report keeps its card lit this long

export function AgentsFlow({ reports, active, now }: {
  reports: Record<string, AgentReport | undefined>;
  active: Record<string, { working: boolean; text?: string } | undefined>; // what the browser sees directly
  now: number;
}) {
  const card = (a: AgentInfo) => {
    const report = reports[a.id], direct = active[a.id];
    const working = direct?.working ?? (report ? report.working && now - report.at < LIT_S : false);
    const latest = direct?.text ?? report?.text ?? a.idle;
    return (
      <article key={a.id} className={`agent-card ${working ? "is-working" : ""}`} aria-label={`${a.name}: ${a.role}`}>
        <h3>{a.name}</h3>
        <div className="agent-tech">
          <span className="agent-icon" aria-hidden>{ICONS[a.id]}</span>
          <span className="agent-model">{a.model}</span>
        </div>
        <p className="agent-role">{a.role}</p>
        <p className="agent-latest" title={latest}>{latest}</p>
      </article>
    );
  };
  // One grid of six equal columns: the labels above their groups, an arrow in each gap during the match.
  return (
    <section className="agents-flow" aria-label="The agents">
      <p className="agents-label before">Before kick-off</p>
      <p className="agents-label during">During the match</p>
      {BEFORE.map((a) => <div key={a.id} className="agents-cell">{card(a)}</div>)}
      {DURING.map((a, i) => (
        <div key={a.id} className={`agents-cell ${i > 0 ? "flows" : ""}`}>{card(a)}</div>
      ))}
    </section>
  );
}
