import { useEffect, useRef, useState } from "react";
import type { Directory } from "../api";
import { t } from "../i18n";
import type { Language, MatchEvent, MatchInfo, Snapshot } from "../types";

export interface ClockInput {
  event: MatchEvent | null;
  arrivedAt: number;
  nextGap: number | null;
  speed: number;
  paused: boolean;
}

export function Scoreboard({ dir, match, snapshot, lang, clock: clockInput }: {
  dir: Directory; match: MatchInfo; snapshot: Snapshot | null; lang: Language; clock: ClockInput;
}) {
  const home = dir.club(match.home)!, away = dir.club(match.away)!;
  const status = snapshot?.status;
  const running = useRunningClock(clockInput, status === "live");
  const clock = status === "half_time" ? t(lang, "halfTime")
    : status === "full_time" ? t(lang, "fullTime")
    : running;

  return (
    <div className="scoreboard" aria-label="Score">
      <Side name={home.short_name} colours={home.colours} reds={snapshot?.home.red_cards ?? 0} />
      <div className="score">
        <span>{snapshot?.home.score ?? 0}</span>
        <span className="score-sep">–</span>
        <span>{snapshot?.away.score ?? 0}</span>
      </div>
      <Side name={away.short_name} colours={away.colours} reds={snapshot?.away.red_cards ?? 0} right />
      <div className={`clock ${status === "live" ? "clock-live" : ""}`}>{clock}</div>
    </div>
  );
}

function Side({ name, colours, reds, right }: { name: string; colours: [string, string]; reds: number; right?: boolean }) {
  return (
    <div className={`side ${right ? "side-right" : ""}`}>
      <span className="crest" style={{ background: colours[0], borderColor: colours[1] }} />
      <span className="side-name">{name}</span>
      {Array.from({ length: reds }, (_, i) => <span key={i} className="red-card" title="Red card" />)}
    </div>
  );
}

/**
 * The match clock keeps running between events, like a broadcast clock: it advances in real time
 * (times the playback speed) from the last event, but never past the next one, and freezes on pause.
 */
function useRunningClock({ event, arrivedAt, nextGap, speed, paused }: ClockInput, live: boolean): string {
  const [now, setNow] = useState(() => performance.now());
  const frozen = useRef<number | null>(null);
  useEffect(() => {
    if (!live || paused) return;
    const id = setInterval(() => setNow(performance.now()), 250);
    return () => clearInterval(id);
  }, [live, paused]);

  if (!event) return "00:00";
  let seconds = event.clock;
  if (live) {
    const advance = ((now - arrivedAt) / 1000) * speed;
    seconds += Math.max(0, Math.min(advance, nextGap ?? 0));
  }
  if (paused && frozen.current != null) seconds = frozen.current;
  frozen.current = paused ? (frozen.current ?? seconds) : null;
  return formatClock(seconds, event.period);
}

function formatClock(seconds: number, period: number): string {
  const mmss = (s: number) => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
  const end = 45 * 60 * period;
  return seconds >= end ? `${mmss(end)} +${mmss(seconds - end)}` : mmss(seconds);
}
