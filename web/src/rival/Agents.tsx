import type { ReactNode } from "react";

/**
 * The agents behind the rival, in a compact row under the pitch: who they are, the part they play and what
 * they run on. A card lights up while its agent is at work. The model labels say what runs today; where an
 * agent is still rules rather than a model, the label says so.
 */

export interface AgentReport { text: string; working: boolean; at: number } // at: epoch seconds

const stroke = { fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round", strokeLinejoin: "round" } as const;

/** Small line icons, one per agent. */
const ICONS: Record<string, ReactNode> = {
  host: <svg viewBox="0 0 24 24" {...stroke}><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" /></svg>,
  news: <svg viewBox="0 0 24 24" {...stroke}><path d="M4 5h13v14H6a2 2 0 0 1-2-2zM17 9h3v8a2 2 0 0 1-2 2" /><path d="M7 9h7M7 12.5h7M7 16h4" /></svg>,
  match: <svg viewBox="0 0 24 24" {...stroke}><circle cx="12" cy="12" r="8.5" /><path d="m12 7.5 4 2.9-1.5 4.7h-5L8 10.4z" /></svg>,
  ammo: <svg viewBox="0 0 24 24" {...stroke}><circle cx="12" cy="12" r="8.5" /><circle cx="12" cy="12" r="4.5" /><circle cx="12" cy="12" r="1" /></svg>,
  rival: <svg viewBox="0 0 24 24" {...stroke}><path d="M4 6h11v7H8l-4 3z" /><path d="M15 9h5v7l-3-2.5h-6v-.5" /></svg>,
  referee: <svg viewBox="0 0 24 24" {...stroke}><rect x="6" y="4" width="9" height="13" rx="1.5" transform="rotate(-10 10 10)" /><path d="M14 19.5 20 18" /></svg>,
};

export const AGENTS = [
  { id: "host", name: "Lobby host", role: "Asks who you support, finds you a rival", model: "Azure AI Speech" },
  { id: "news", name: "News agent", role: "Writes the club news before kick-off", model: "gpt-5.4-mini" },
  { id: "match", name: "Match agent", role: "Spots the big moments, sets the pace", model: "Rules for now" },
  { id: "ammo", name: "Ammo agent", role: "Lines up the next dig", model: "Rules for now" },
  { id: "rival", name: "The rival", role: "Watches with you, talks back", model: "GPT-Live" },
  { id: "referee", name: "Referee", role: "Keeps it good-natured", model: "Azure AI Content Safety" },
];

const LIT_S = 2.5; // a report keeps its card lit this long

export function AgentsStrip({ reports, active, now }: {
  reports: Record<string, AgentReport | undefined>;
  active: Record<string, boolean>; // agents the browser sees at work directly (the host, the rival talking)
  now: number;
}) {
  return (
    <section className="agents" aria-label="The agents">
      {AGENTS.map((a) => {
        const report = reports[a.id];
        const working = active[a.id] ?? (report ? report.working && now - report.at < LIT_S : false);
        return (
          <article key={a.id} className={`agent-card agent-${a.id} ${working ? "is-working" : ""}`}
                   aria-label={`${a.name}: ${a.role}${working ? " (working)" : ""}`}>
            <header>
              <span className="agent-icon" aria-hidden>{ICONS[a.id]}</span>
              <h3>{a.name}</h3>
            </header>
            <p className="agent-role">{a.role}</p>
            <p className="agent-model">{a.model}</p>
          </article>
        );
      })}
    </section>
  );
}
