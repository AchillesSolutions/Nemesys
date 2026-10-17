import type { Directory } from "../api";

/**
 * The match at a glance, under the fixture: a 0-90 bar that fills as the clock runs, with a marker dropped on
 * it for each moment (goals in the scoring side's colour, cards, VAR). The real match is on the viewer's TV;
 * this only says where it's got to and what has happened.
 */

export interface TimelineMoment { minute: string; kind: string; team: string | null; elapsed: number; text: string }

const MARKS: Record<string, string> = {
  goal: "goal", penalty: "goal", goal_disallowed: "ruled-out", red_card: "red", yellow_card: "yellow",
  var_check: "var", half_time: "half",
};

export function Timeline({ dir, moments, elapsed, duration }: {
  dir: Directory; moments: TimelineMoment[]; elapsed: number; duration: number;
}) {
  const at = (s: number) => `${Math.min(100, (s / duration) * 100)}%`;
  return (
    <div className="timeline" aria-label="Match timeline">
      <span className="timeline-end">0'</span>
      <div className="timeline-track">
        <div className="timeline-fill" style={{ width: at(elapsed) }} />
        <i className="timeline-half" style={{ left: at(duration / 2) }} aria-hidden />
        {moments.filter((m) => MARKS[m.kind] && m.kind !== "half_time").map((m) => (
          <span key={`${m.kind}-${m.elapsed}`} className={`timeline-mark mark-${MARKS[m.kind]}`}
                style={{ left: at(m.elapsed), ["--team" as string]: dir.colour(m.team) }}
                title={`${m.minute}' ${m.text}`} />
        ))}
      </div>
      <span className="timeline-end">90'</span>
    </div>
  );
}
