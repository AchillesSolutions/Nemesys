import { useEffect, useRef, useState, type ReactNode } from "react";
import type { Directory } from "../api";
import { t } from "../i18n";
import type { Club, Language, MatchEvent, MatchInfo, Snapshot, TeamSnapshot } from "../types";
import { Crest } from "./Crest";

export interface ClockInput {
  event: MatchEvent | null;
  arrivedAt: number;
  nextGap: number | null;
  speed: number;
  paused: boolean;
}

/**
 * The fixture across the top of the screen, like a matchday graphic: each club's name and crest,
 * with its scorers underneath, either side of what is in the middle (the score and clock in a
 * match, the kick-off time before it).
 */
export function Fixture({ dir, match, home: homeSnap, away: awaySnap, lang, children }: {
  dir: Directory; match: MatchInfo; home?: TeamSnapshot; away?: TeamSnapshot; lang: Language; children: ReactNode;
}) {
  const home = dir.club(match.home)!, away = dir.club(match.away)!;
  return (
    <header className="fixture">
      <Side dir={dir} club={home} snap={homeSnap} lang={lang} />
      <div className="fixture-centre">{children}</div>
      <Side dir={dir} club={away} snap={awaySnap} lang={lang} away />
    </header>
  );
}

function Side({ dir, club, snap, lang, away = false }: {
  dir: Directory; club: Club; snap?: TeamSnapshot; lang: Language; away?: boolean;
}) {
  return (
    <div className={`fixture-side ${away ? "away" : "home"}`}>
      <div className="fixture-team">
        <h1>{club.name}</h1>
        <p className="scorers">
          {scorers(dir, snap, lang).map((s) => <span key={s}>{s}</span>)}
          {Array.from({ length: snap?.red_cards ?? 0 }, (_, i) => <span key={`r${i}`} className="red-card" title="Red card" />)}
        </p>
      </div>
      <Crest club={club} className="crest-big" />
    </div>
  );
}

/** "Whitcombe 56' (pen), 83'": one entry per scorer, in the order they first scored. */
function scorers(dir: Directory, snap: TeamSnapshot | undefined, lang: Language): string[] {
  const by = new Map<string, string[]>();
  for (const g of snap?.goals ?? []) {
    const minute = `${g.minute}'${g.penalty ? ` (${t(lang, "pen")})` : ""}`;
    by.set(g.player, [...(by.get(g.player) ?? []), minute]);
  }
  return [...by].map(([id, minutes]) => `${dir.player(id)?.name.split(" ").slice(-1)[0] ?? ""} ${minutes.join(", ")}`);
}

/** In the middle during a match: the score, and the running clock with the period. */
export function Score({ snapshot, lang, clock: clockInput }: { snapshot: Snapshot | null; lang: Language; clock: ClockInput }) {
  const status = snapshot?.status;
  const running = useRunningClock(clockInput, status === "live");
  const period = clockInput.event?.period === 2 ? t(lang, "secondHalf") : t(lang, "firstHalf");
  return (
    <>
      <div className="score" aria-label="Score">
        <span>{snapshot?.home.score ?? 0}</span><span className="score-dot" aria-hidden>•</span><span>{snapshot?.away.score ?? 0}</span>
      </div>
      <p className="match-clock">
        {status === "half_time" ? <b>{t(lang, "halfTime")}</b>
          : status === "full_time" ? <b>{t(lang, "fullTime")}</b>
          : <><span className="live-dot" aria-hidden /><b>{running}</b>{period}</>}
      </p>
    </>
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
