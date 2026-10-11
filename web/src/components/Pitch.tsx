import { useMemo, useRef } from "react";
import type { Directory } from "../api";
import { EVENT_LABELS, t } from "../i18n";
import type { Language, MatchEvent, MatchInfo, Snapshot } from "../types";

// Real pitch proportions in metres; server coordinates are 0-100 on both axes.
const W = 105;
const H = 68;
const sx = (x: number) => (x / 100) * W;
const sy = (y: number) => (y / 100) * H;

// Events worth a pop-up marker on the pitch.
const MARKED = new Set([
  "goal", "goal_disallowed", "offside", "yellow_card", "red_card", "corner", "penalty", "var_check",
  "var_decision", "free_kick", "substitution", "save",
]);
// After these, play is stopped until the next event: say so, so the pitch never looks frozen.
const STOPPAGES = new Set([
  "foul", "offside", "goal", "goal_disallowed", "yellow_card", "red_card", "substitution", "var_check",
  "var_decision", "penalty", "corner", "half_time", "full_time",
]);

interface Props {
  dir: Directory;
  match: MatchInfo;
  snapshot: Snapshot | null;
  recent: MatchEvent[];
  nextGap: number | null;
  speed: number;
  lang: Language;
  lineup?: Record<string, number[]> | null; // before kick-off: where the players line up
}

export function Pitch({ dir, match, snapshot, recent, nextGap, speed, lang, lineup }: Props) {
  const latest = recent.at(-1) ?? null;
  const marker = latest && MARKED.has(latest.type) && latest.x != null ? latest : null;
  const home = dir.club(match.home)!, away = dir.club(match.away)!;

  // Everything glides to its next position over the real time until the next event, so the ball and
  // players are always moving during play. Long gaps (stoppages) settle quickly instead.
  const gapReal = nextGap == null ? 0.6 : nextGap / speed;
  const glide = Math.min(Math.max(gapReal, 0.35), 2.2);
  const motion = { transitionDuration: `${glide}s` };

  // Player positions come from the match data (every event carries all 22), so passes really go
  // from player to player. Each dot glides at no more than sprinting speed, so long runs look like runs.
  const previous = useRef<Record<string, number[]>>({});
  const dots = useMemo(() => {
    const frame = latest?.frame ?? lineup ?? {};
    const prev = previous.current;
    const out = Object.entries(frame).map(([id, [x, y]]) => {
      const from = prev[id] ?? [x, y];
      const metres = Math.hypot((x - from[0]) * 1.05, (y - from[1]) * 0.68);
      const duration = Math.max(glide, metres / 9 / speed);
      return { id, team: id.split("-")[0], x, y, duration, onBall: id === holderOf(latest) };
    });
    previous.current = frame;
    return out;
  }, [latest, glide, speed, lineup]);
  const ball = ballTarget(latest, snapshot);
  const stoppage = stoppageLabel(latest, nextGap, lang);
  const offside = offsidePicture(latest);

  return (
    <div className="pitch-wrap">
      <svg className="pitch" viewBox={`-3 -3 ${W + 6} ${H + 6}`} role="img"
           aria-label={`${home.short_name} attacking right, ${away.short_name} attacking left`}>
        <Markings />
        {dots.map((d) => {
          const club = dir.club(d.team)!;
          return (
            <g key={d.id} className={`player ${d.onBall ? "on-ball" : ""} ${offside?.player === d.id ? "offside-player" : ""}`}
               style={{ transitionDuration: `${d.duration}s`, transform: `translate(${sx(d.x)}px, ${sy(d.y)}px)` }}>
              <circle r={1.55} fill={club.colours[0]} stroke={club.colours[1]} strokeWidth={0.35} />
              <text y={0.62} textAnchor="middle" fill={club.colours[1]}>{dir.player(d.id)?.number}</text>
            </g>
          );
        })}
        {offside && (
          <g className="offside-line">
            <line x1={sx(offside.x)} y1={-1} x2={sx(offside.x)} y2={H + 1} />
            <text x={sx(offside.x)} y={H - 2} textAnchor="middle">{t(lang, "offsideLine")}</text>
            {offside.ghost && (
              <circle className="offside-ghost" cx={sx(offside.ghost[0])} cy={sy(offside.ghost[1])} r={2.2} />
            )}
          </g>
        )}
        {ball && (
          <g className="ball" style={{ ...motion, transform: `translate(${sx(ball[0])}px, ${sy(ball[1])}px)` }}>
            <circle r={1.4} className="ball-halo" />
            <circle r={0.8} className="ball-core" />
          </g>
        )}
        {marker && (
          <g key={marker.id} className={`marker marker-${marker.type}`}
             style={{ transform: `translate(${sx(clampLabel(marker.x!))}px, ${sy(Math.max(marker.y!, 18))}px)` }}>
            <rect x={-12} y={-9.5} width={24} height={6} rx={1.5} />
            <text y={-5.4} textAnchor="middle">{EVENT_LABELS[marker.type]?.[lang] ?? marker.type}</text>
          </g>
        )}
      </svg>
      {stoppage && <div className="stoppage" role="status">{stoppage}</div>}
    </div>
  );
}

/** Who has the ball after this event: the receiver of a completed pass, otherwise whoever acted. */
function holderOf(ev: MatchEvent | null): string | undefined {
  if (!ev) return undefined;
  return ev.type === "pass" && ev.outcome === "complete" ? ev.other_player : ev.player;
}

/**
 * Offside, drawn: the line of the second-last defender when the pass was played, and (for a VAR
 * review) where the attacker was at that moment.
 */
function offsidePicture(ev: MatchEvent | null): { x: number; player?: string; ghost?: number[] } | null {
  const d = ev?.detail as { offside_line_x?: number; offside_player?: string; offside_player_at?: number[] } | undefined;
  if (!ev || d?.offside_line_x == null) return null;
  if (ev.type === "pass") return null; // the flag goes up on the next event
  return { x: d.offside_line_x, player: d.offside_player ?? (ev.type === "offside" ? ev.player : undefined),
           ghost: d.offside_player_at };
}

/** Keep pop-up labels inside the pitch. */
const clampLabel = (x: number) => Math.max(12, Math.min(88, x));

function ballTarget(ev: MatchEvent | null, snapshot: Snapshot | null): [number, number] | null {
  if (ev?.end_x != null && ev.end_y != null) return [ev.end_x, ev.end_y];
  if (ev?.x != null && ev.y != null) return [ev.x, ev.y];
  return snapshot?.ball ?? null;
}

function stoppageLabel(ev: MatchEvent | null, nextGap: number | null, lang: Language): string | null {
  if (!ev) return null;
  const long = nextGap == null || nextGap >= 8;
  if (ev.type === "pass" && ev.outcome === "out" && long) return t(lang, "outOfPlay");
  if (!STOPPAGES.has(ev.type) || !long) return null;
  if (ev.type === "var_check") return t(lang, "varInProgress");
  return EVENT_LABELS[ev.type]?.[lang] ?? ev.type;
}

function Markings() {
  const box = 40.32, six = 18.32;
  return (
    <g className="markings">
      <rect x={0} y={0} width={W} height={H} className="line" />
      <line x1={W / 2} y1={0} x2={W / 2} y2={H} className="line" />
      <circle cx={W / 2} cy={H / 2} r={9.15} className="line" />
      <circle cx={W / 2} cy={H / 2} r={0.4} className="spot" />
      {[0, W].map((x0) => {
        const dir = x0 === 0 ? 1 : -1;
        return (
          <g key={x0}>
            <rect x={x0 === 0 ? 0 : W - 16.5} y={(H - box) / 2} width={16.5} height={box} className="line" />
            <rect x={x0 === 0 ? 0 : W - 5.5} y={(H - six) / 2} width={5.5} height={six} className="line" />
            <circle cx={x0 + dir * 11} cy={H / 2} r={0.4} className="spot" />
            <rect x={x0 === 0 ? -2 : W} y={H / 2 - 3.66} width={2} height={7.32} className="goal" />
          </g>
        );
      })}
    </g>
  );
}

/** One-line description of the latest event, under the pitch. */
export function Ticker({ dir, event, lang }: { dir: Directory; event: MatchEvent | null; lang: Language }) {
  if (!event) return <div className="ticker">&nbsp;</div>;
  const who = dir.player(event.player)?.name;
  const club = dir.club(event.team);
  return (
    <div className="ticker" aria-live="off">
      <span className="ticker-minute">{event.minute}'</span>
      {club && <span className="ticker-dot" style={{ background: club.colours[0] }} />}
      <span className="ticker-type">{EVENT_LABELS[event.type]?.[lang] ?? event.type}</span>
      {who && <span className="ticker-who">{who}</span>}
    </div>
  );
}
