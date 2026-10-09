import { useMemo, useState, type ReactNode } from "react";
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
  // Each stat as a split bar in the two clubs' colours, the way a broadcast shows them.
  const row = (label: string, h: number, a: number, show: (s: TeamSnapshot) => ReactNode = (s) => (s === home ? h : a)) => (
    <div className="stat" key={label}>
      <span className="stat-name">{label}</span>
      <span className="stat-v">{show(home)}</span>
      <span className="split" aria-hidden>
        <i style={{ flexGrow: h || (a ? 0 : 1), background: dir.colour(home.id) }} />
        <i style={{ flexGrow: a || (h ? 0 : 1), background: dir.colour(away.id) }} />
      </span>
      <span className="stat-v">{show(away)}</span>
    </div>
  );
  const cards = (s: TeamSnapshot) => (
    <>{s.yellow_cards}<span className="card-icon yellow" /> {s.red_cards > 0 && <>{s.red_cards}<span className="card-icon red" /></>}</>
  );
  return (
    <section className="panel stats" aria-label={t(lang, "stats")}>
      <h2>{t(lang, "stats")}</h2>
      <div className="stat-rows">
        {row(t(lang, "goals"), home.score, away.score)}
        {row(t(lang, "possession"), home.possession_pct, away.possession_pct, (s) => `${Math.round(s.possession_pct)}%`)}
        {row(t(lang, "shots"), home.shots, away.shots, (s) => `${s.shots} (${s.shots_on_target})`)}
        {row(t(lang, "corners"), home.corners, away.corners)}
        {row(t(lang, "fouls"), home.fouls, away.fouls)}
        {row(t(lang, "cards"), home.yellow_cards + home.red_cards * 2, away.yellow_cards + away.red_cards * 2, cards)}
        {home.players_on_pitch.length !== away.players_on_pitch.length &&
          row(t(lang, "players"), home.players_on_pitch.length, away.players_on_pitch.length)}
      </div>
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
