import { useMemo, useState } from "react";
import { projectTable, type Directory } from "../api";
import { AGENT_NAMES, t } from "../i18n";
import type { Commentary, Language, MatchInfo, Snapshot, TeamSnapshot } from "../types";

export function Learned({ learned, lang }: { learned: string[]; lang: Language }) {
  return (
    <section className="panel learned" aria-label={t(lang, "learned")}>
      <h2>{t(lang, "learned")} <span className="count">{learned.length}</span></h2>
      {learned.length === 0 ? <p className="empty">{t(lang, "learnedEmpty")}</p> : (
        <ul>{learned.map((l) => <li key={l}><span className="tick" aria-hidden>✓</span>{l}</li>)}</ul>
      )}
    </section>
  );
}

export function Stats({ dir, snapshot, lang }: { dir: Directory; snapshot: Snapshot | null; lang: Language }) {
  if (!snapshot) return null;
  const { home, away } = snapshot;
  const row = (label: string, f: (s: TeamSnapshot) => string | number) => (
    <tr><td>{f(home)}</td><th scope="row">{label}</th><td>{f(away)}</td></tr>
  );
  return (
    <section className="panel stats" aria-label={t(lang, "stats")}>
      <h2>{t(lang, "stats")}</h2>
      <div className="possession" aria-label={`${t(lang, "possession")} ${home.possession_pct}% – ${away.possession_pct}%`}>
        <div style={{ width: `${home.possession_pct}%`, background: dir.colour(home.id) }}>{Math.round(home.possession_pct)}%</div>
        <div style={{ width: `${away.possession_pct}%`, background: dir.colour(away.id) }}>{Math.round(away.possession_pct)}%</div>
      </div>
      <p className="possession-label">{t(lang, "possession")}</p>
      <table>
        <tbody>
          {row(t(lang, "shots"), (s) => `${s.shots} (${s.shots_on_target})`)}
          {row(t(lang, "corners"), (s) => s.corners)}
          {row(t(lang, "fouls"), (s) => s.fouls)}
          {row(t(lang, "cards"), (s) => `${s.yellow_cards}🟨 ${s.red_cards}🟥`)}
          {row(t(lang, "players"), (s) => s.players_on_pitch.length)}
        </tbody>
      </table>
    </section>
  );
}

export function LeagueTable({ dir, match, snapshot, lang }: {
  dir: Directory; match: MatchInfo; snapshot: Snapshot | null; lang: Language;
}) {
  const home = dir.club(match.home)!, away = dir.club(match.away)!;
  const hs = snapshot?.home.score ?? 0, as = snapshot?.away.score ?? 0;
  const projected = useMemo(() => projectTable(dir.world.table, [
    { name: home.name, gf: hs, ga: as }, { name: away.name, gf: as, ga: hs },
  ]), [dir, home.name, away.name, hs, as]);
  const before = new Map(dir.world.table.map((r, i) => [r.club, i + 1]));
  // Live: the table as it would be if the match ended now (a draw counts too), and at full time the
  // final standings. The two teams playing are marked in their colours with how far they've moved.
  const colour = new Map([[home.name, dir.colour(home.id)], [away.name, dir.colour(away.id)]]);
  const status = !snapshot ? "beforeKickoff" : snapshot.status === "full_time" ? "finalStandings" : "ifEndedNow";
  const shown = projected.map((r, i) => ({ ...r, pos: i + 1 })).filter((r) => r.pos <= 8 || colour.has(r.club));

  return (
    <section className="panel table" aria-label={t(lang, "table")}>
      <h2>{t(lang, "table")} <span className="sub">{t(lang, status)}</span></h2>
      <table>
        <tbody>
          {shown.map((r) => {
            const c = colour.get(r.club);
            const move = c ? (before.get(r.club) ?? r.pos) - r.pos : 0;
            return (
              <tr key={r.club} className={`${c ? "ours" : ""} ${r.pos === 4 ? "cutoff" : ""}`}
                  style={c ? { ["--club" as string]: c } : undefined}>
                <td className="pos">{r.pos}</td>
                <td className="club">{r.club}</td>
                <td className="move">{move > 0 ? `▲${move}` : move < 0 ? `▼${-move}` : c ? "–" : ""}</td>
                <td className="pts">{r.points}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}

/** What the agents proposed and why the Producer chose what it did. */
export function UnderTheHood({ feed, lang }: { feed: Commentary[]; lang: Language }) {
  const [open, setOpen] = useState(false);
  const latest = feed.find((c) => c.proposals.length > 0);
  return (
    <section className="panel hood">
      <h2>
        <button className="link" onClick={() => setOpen(!open)} aria-expanded={open}>
          {open ? "▾" : "▸"} {t(lang, "howItWorks")}
        </button>
      </h2>
      {open && latest && (
        <>
          <p className="hood-minute">{latest.minute}' · {t(lang, "producerSaid")}</p>
          <p className="hood-reason">{latest.reasoning}</p>
          <p className="hood-sub">{t(lang, "proposals")}</p>
          <ul>
            {latest.proposals.map((p) => (
              <li key={p.id} className={p.used ? "used" : "held"}>
                <span className={`chip chip-${p.agent}`}>{AGENT_NAMES[p.agent]?.[lang] ?? p.agent}</span>
                <span className="prio">P{p.priority}</span>
                <span className="state">{p.used ? t(lang, "used") : t(lang, "held")}</span>
                <span className="text">{p.text}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
