/**
 * The agents behind the rival, under the pitch: what each one controls, what it runs on, and what it last
 * did. A card pulses while its agent is working. The model labels say what runs today; where an agent is
 * still rules rather than a model, the label says so.
 */

export interface AgentReport { text: string; working: boolean; at: number } // at: epoch seconds

interface AgentInfo { id: string; name: string; role: string; model: string; idle: string }

export const AGENTS: AgentInfo[] = [
  { id: "host", name: "Lobby host", role: "Asks who you support and matches you with a rival",
    model: "Azure AI Speech", idle: "Waiting in the lobby" },
  { id: "news", name: "News agent", role: "Writes the fortnight's club news before kick-off",
    model: "gpt-5.4-mini · Agent Framework", idle: "Waiting for kick-off day" },
  { id: "match", name: "Match agent", role: "Follows the match, picks the big moments, sets the pace",
    model: "Match reader (rules)", idle: "Watching the match" },
  { id: "ammo", name: "Ammo agent", role: "Lines up the next dig: news, the table, last time",
    model: "Rules for now", idle: "Keeping digs in reserve" },
  { id: "rival", name: "The rival", role: "Watches with you and winds you up, full duplex",
    model: "GPT-Live (gpt-live-1)", idle: "Listening" },
  { id: "referee", name: "Referee", role: "Keeps the banter good-natured",
    model: "Azure AI Content Safety", idle: "All good-natured so far" },
];

const PULSE_S = 3; // a report keeps its card lit this long

export function AgentsStrip({ reports, live, now }: {
  reports: Record<string, AgentReport | undefined>;
  live: Record<string, { working: boolean; text: string } | undefined>; // agents the browser sees directly
  now: number;
}) {
  return (
    <section className="agents" aria-label="The agents at work">
      {AGENTS.map((a) => {
        const direct = live[a.id];
        const report = reports[a.id];
        const working = direct?.working ?? (report ? report.working && now - report.at < PULSE_S : false);
        const text = direct?.text ?? report?.text ?? a.idle;
        return (
          <article key={a.id} className={`agent-card agent-${a.id} ${working ? "is-working" : ""}`}>
            <header>
              <i className="agent-pulse" aria-hidden />
              <h3>{a.name}</h3>
            </header>
            <p className="agent-role">{a.role}</p>
            <p className="agent-model">{a.model}</p>
            <p className="agent-now" title={text}>{text}</p>
          </article>
        );
      })}
    </section>
  );
}
