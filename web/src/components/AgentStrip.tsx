import { useEffect, useState } from "react";
import { AGENT_NAMES } from "../i18n";
import type { MatchState } from "../useMatch";
import type { Language } from "../types";
import { voice } from "../voice";

const SPECIALISTS = ["teacher", "storyteller", "league_guide"];

/**
 * Demo only: the agents at work, across the top of the stage. When a moment wakes them, the three
 * specialists light up together (they run in parallel), each shows what it drafted, then the Producer
 * shows what it chose and why, and Achilles says it. The Host lights up when the viewer asks something.
 */
export function AgentStrip({ agents, asking, lang }: { agents: MatchState["agents"]; asking: number; lang: Language }) {
  const [speaking, setSpeaking] = useState(false);
  useEffect(() => voice.onLine((line) => setSpeaking(line !== null)) as () => void, []);
  const nodes = agents?.nodes ?? {};
  const producer = nodes.producer;

  return (
    <div className="agent-strip" aria-label="Agents at work">
      <div className="agent-group">
        {SPECIALISTS.map((id) => {
          const n = nodes[id];
          const text = n?.state === "done" ? (n.texts[0] ?? "Nothing to add") : n?.state === "thinking" ? "Writing…"
            : n?.state === "idle" ? "Nothing to add" : "";
          return <Node key={id} name={AGENT_NAMES[id][lang]} state={n?.state} text={text} kind={id} />;
        })}
      </div>
      <span className="agent-arrow" aria-hidden>→</span>
      <Node name={AGENT_NAMES.producer[lang]} kind="producer" state={producer?.state}
            text={producer?.state === "done" ? producer.texts[0] : producer?.state === "thinking" ? "Choosing what to say…" : ""} />
      <span className="agent-arrow" aria-hidden>→</span>
      <Node name="Achilles" kind="voice" state={speaking ? "thinking" : undefined} text={speaking ? "Speaking" : ""} />
      <Node name={AGENT_NAMES.host[lang]} kind="host" state={asking > 0 ? "thinking" : undefined}
            text={asking > 0 ? "Answering the viewer…" : "Waits for questions"} />
    </div>
  );
}

function Node({ name, state, text, kind }: { name: string; state?: string; text: string; kind: string }) {
  return (
    <div className={`agent-node agent-${kind} ${state ? `is-${state}` : ""}`}>
      <span className="agent-name"><i className="agent-dot" aria-hidden />{name}</span>
      <span className="agent-text" title={text}>{text}</span>
    </div>
  );
}
